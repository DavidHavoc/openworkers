import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import redis

from core.schemas import ResearchSession

_SESSION_TTL_SECONDS = int(os.environ.get("SESSION_TTL_SECONDS", str(30 * 24 * 3600)))
_SESSION_KEY_PREFIX = "session:"
_INDEX_KEY = "sessions:index"


class SessionStore:
    def __init__(self, redis_url: Optional[str] = None) -> None:
        url = redis_url or os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        self.redis = redis.from_url(url, decode_responses=True)

    def save(self, session: ResearchSession) -> None:
        session_key = f"{_SESSION_KEY_PREFIX}{session.session_id}"
        data = session.model_dump_json()
        pipe = self.redis.pipeline()
        pipe.setex(session_key, _SESSION_TTL_SECONDS, data)
        pipe.zadd(_INDEX_KEY, {session.session_id: time.time()})
        pipe.execute()

    def load(self, session_id: str) -> Optional[ResearchSession]:
        session_key = f"{_SESSION_KEY_PREFIX}{session_id}"
        raw = self.redis.get(session_key)
        if raw is None:
            return None
        return ResearchSession.model_validate_json(raw)

    def list_sessions(self, limit: int = 20, offset: int = 0) -> List[Dict[str, Any]]:
        members = self.redis.zrevrange(_INDEX_KEY, offset, offset + limit - 1, withscores=True)
        results: List[Dict[str, Any]] = []
        for session_id, score in members:
            results.append(
                {
                    "session_id": session_id,
                    "created_at": datetime.utcfromtimestamp(score).isoformat() + "Z",
                }
            )
        return results

    def delete(self, session_id: str) -> bool:
        session_key = f"{_SESSION_KEY_PREFIX}{session_id}"
        deleted = self.redis.delete(session_key)
        if deleted:
            self.redis.zrem(_INDEX_KEY, session_id)
        return bool(deleted)

    def count(self) -> int:
        return self.redis.zcard(_INDEX_KEY)

    def clear_all(self) -> None:
        keys = self.redis.keys(f"{_SESSION_KEY_PREFIX}*")
        if keys:
            self.redis.delete(*keys)
        self.redis.delete(_INDEX_KEY)
