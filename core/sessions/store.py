import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import redis.asyncio as aioredis

from core.schemas import ResearchSession

_SESSION_KEY_PREFIX = "session:"
_INDEX_KEY = "sessions:index"

_PG_POOL = None


class BaseSessionStore(ABC):
    @abstractmethod
    async def save(self, session: ResearchSession) -> None: ...

    @abstractmethod
    async def load(self, session_id: str) -> Optional[ResearchSession]: ...

    @abstractmethod
    async def list_sessions(
        self,
        limit: int = 20,
        offset: int = 0,
        discipline: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]: ...

    @abstractmethod
    async def delete(self, session_id: str) -> bool: ...

    @abstractmethod
    async def count(self) -> int: ...


class RedisSessionStore(BaseSessionStore):
    def __init__(self, redis_url: Optional[str] = None) -> None:
        from core.config import get_settings

        url = redis_url or get_settings().redis_url
        self.redis = aioredis.from_url(url, decode_responses=True)

    async def save(self, session: ResearchSession) -> None:
        session_key = f"{_SESSION_KEY_PREFIX}{session.session_id}"
        data = session.model_dump_json()
        from core.config import get_settings

        async with self.redis.pipeline() as pipe:
            await pipe.setex(session_key, get_settings().session_ttl_seconds, data)
            await pipe.zadd(_INDEX_KEY, {session.session_id: time.time()})
            await pipe.execute()

    async def load(self, session_id: str) -> Optional[ResearchSession]:
        session_key = f"{_SESSION_KEY_PREFIX}{session_id}"
        raw = await self.redis.get(session_key)
        if raw is None:
            return None
        return ResearchSession.model_validate_json(raw)

    async def list_sessions(
        self,
        limit: int = 20,
        offset: int = 0,
        discipline: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        members = await self.redis.zrevrange(
            _INDEX_KEY, offset, offset + limit - 1, withscores=True
        )
        results: List[Dict[str, Any]] = []
        for session_id, score in members:
            result = {
                "session_id": session_id,
                "created_at": datetime.fromtimestamp(score, tz=timezone.utc).isoformat(),
            }
            raw = await self.redis.get(f"{_SESSION_KEY_PREFIX}{session_id}")
            if raw:
                s = ResearchSession.model_validate_json(raw)
                result["research_question"] = s.research_context.research_question
                result["discipline"] = s.research_context.discipline
                result["status"] = s.status
            if (discipline and result.get("discipline") != discipline) or (
                status and result.get("status") != status
            ):
                continue
            results.append(result)
        return results[:limit]

    async def delete(self, session_id: str) -> bool:
        session_key = f"{_SESSION_KEY_PREFIX}{session_id}"
        deleted = await self.redis.delete(session_key)
        if deleted:
            await self.redis.zrem(_INDEX_KEY, session_id)
        return bool(deleted)

    async def count(self) -> int:
        return await self.redis.zcard(_INDEX_KEY)

    async def clear_all(self) -> None:
        keys = [k async for k in self.redis.scan_iter(f"{_SESSION_KEY_PREFIX}*")]
        if keys:
            await self.redis.delete(*keys)
        await self.redis.delete(_INDEX_KEY)


class PgSessionStore(BaseSessionStore):
    def __init__(self, dsn: Optional[str] = None) -> None:
        from core.config import get_settings

        self._dsn = dsn or get_settings().database_url
        self._pool: Any = None

    async def _get_pool(self) -> Any:
        if self._pool is None:
            import asyncpg

            self._pool = await asyncpg.create_pool(dsn=self._dsn, min_size=1, max_size=4)
            await self._migrate()
        return self._pool

    async def _migrate(self) -> None:
        pool = self._pool
        async with pool.acquire() as conn:
            # fmt: off
            await conn.execute("""CREATE TABLE IF NOT EXISTS sessions (
                    session_id      TEXT PRIMARY KEY,
                    research_question TEXT NOT NULL,
                    discipline      TEXT NOT NULL DEFAULT 'general',
                    status          TEXT NOT NULL DEFAULT 'complete',
                    data            JSONB NOT NULL,
                    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )""")
            # fmt: on
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sessions_discipline ON sessions (discipline)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions (status)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sessions_created_at ON sessions (created_at DESC)"
            )

    async def save(self, session: ResearchSession) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO sessions (session_id, research_question, discipline, status, data, created_at)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (session_id) DO UPDATE SET
                    research_question = EXCLUDED.research_question,
                    discipline = EXCLUDED.discipline,
                    status = EXCLUDED.status,
                    data = EXCLUDED.data
                """,
                session.session_id,
                session.research_context.research_question,
                session.research_context.discipline,
                session.status,
                session.model_dump_json(),
                datetime.now(timezone.utc),
            )

    async def load(self, session_id: str) -> Optional[ResearchSession]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT data FROM sessions WHERE session_id = $1", session_id)
            if row is None:
                return None
            return ResearchSession.model_validate_json(row["data"])

    async def list_sessions(
        self,
        limit: int = 20,
        offset: int = 0,
        discipline: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            query = (
                "SELECT session_id, research_question, discipline, status, created_at "
                "FROM sessions WHERE 1=1"
            )
            params: List[Any] = []
            idx = 1
            if discipline:
                query += f" AND discipline = ${idx}"
                params.append(discipline)
                idx += 1
            if status:
                query += f" AND status = ${idx}"
                params.append(status)
                idx += 1
            query += f" ORDER BY created_at DESC LIMIT ${idx} OFFSET ${idx + 1}"
            params.extend([limit, offset])

            rows = await conn.fetch(query, *params)
            return [
                {
                    "session_id": r["session_id"],
                    "research_question": r["research_question"],
                    "discipline": r["discipline"],
                    "status": r["status"],
                    "created_at": r["created_at"].isoformat() + "Z",
                }
                for r in rows
            ]

    async def delete(self, session_id: str) -> bool:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute("DELETE FROM sessions WHERE session_id = $1", session_id)
            return "DELETE 1" in result

    async def count(self) -> int:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT COUNT(*) AS c FROM sessions")
            return row["c"] if row else 0


def create_session_store() -> BaseSessionStore:
    from core.config import get_settings

    settings = get_settings()
    backend = settings.session_backend.lower()
    db_url = settings.database_url
    if backend == "postgres" or (not backend and db_url):
        return PgSessionStore()
    return RedisSessionStore()
