import asyncio
import logging
import os
import threading
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel

from core.memory.episodic import EpisodicMemory
from core.orchestrator.thesis_flow import ThesisOrchestrator
from core.router.engine import Router
from core.schemas import ResearchContext
from core.sessions.store import create_session_store
from providers.adapters import create_unified_llm
from tools.mcp.engine import ToolRegistry

logger = logging.getLogger(__name__)

app = FastAPI(
    title="OpenWorkers API",
    description="Research-focused hierarchical multi-agent system",
    version="0.2.0",
)

# In-memory task store: task_id -> {status, result, error, created_at}
_tasks: Dict[str, Dict[str, Any]] = {}


class RateLimiter:
    def __init__(self, max_requests: int = 10, window_seconds: float = 60.0):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._windows: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()
        self._last_cleanup = time.monotonic()

    @staticmethod
    def _get_ip(request: Request) -> str:
        forwarded = request.headers.get("X-Forwarded-For")
        return forwarded.split(",")[0].strip() if forwarded else (
            request.client.host if request.client else "unknown"
        )

    def _cleanup(self, now: float) -> None:
        if now - self._last_cleanup < 60.0:
            return
        self._last_cleanup = now
        cutoff = now - self.window_seconds
        for ip in list(self._windows):
            self._windows[ip] = [t for t in self._windows[ip] if t >= cutoff]
            if not self._windows[ip]:
                del self._windows[ip]

    def lookup(self, request: Request) -> dict[str, int]:
        ip = self._get_ip(request)
        now = time.monotonic()
        with self._lock:
            cutoff = now - self.window_seconds
            timestamps = [t for t in self._windows.get(ip, []) if t >= cutoff]
        return {
            "current": len(timestamps),
            "limit": self.max_requests,
            "remaining": max(0, self.max_requests - len(timestamps)),
        }

    def __call__(self, request: Request, response: Response):
        ip = self._get_ip(request)
        now = time.monotonic()
        with self._lock:
            self._cleanup(now)
            cutoff = now - self.window_seconds
            self._windows[ip] = [t for t in self._windows[ip] if t >= cutoff]
            current = len(self._windows[ip])
            if current >= self.max_requests:
                reset_in = max(0.0, self._windows[ip][0] + self.window_seconds - now)
                response.headers["X-RateLimit-Remaining"] = "0"
                response.headers["X-RateLimit-Reset"] = str(round(reset_in, 1))
                raise HTTPException(
                    status_code=429,
                    detail={
                        "error": "Too Many Requests",
                        "retry_after_seconds": round(reset_in, 1),
                    },
                )
            self._windows[ip].append(now)
            remaining = self.max_requests - (current + 1)
            reset_in = max(0.0, self._windows[ip][0] + self.window_seconds - now)
            response.headers["X-RateLimit-Remaining"] = str(remaining)
            response.headers["X-RateLimit-Reset"] = str(round(reset_in, 1))


_rate_limiter = RateLimiter(
    max_requests=int(os.getenv("RATELIMIT_MAX_REQUESTS", "10")),
    window_seconds=float(os.getenv("RATELIMIT_WINDOW_SECONDS", "60")),
)


class TaskRequest(BaseModel):
    query: str
    discipline: str = "general"
    topic_summary: Optional[str] = None
    existing_knowledge: Optional[str] = None
    what_they_need: Optional[str] = None
    mode: str = "balanced"  # quality | balanced | cheap


class TaskResponse(BaseModel):
    task_id: str
    status: str
    created_at: str


class TaskResult(BaseModel):
    task_id: str
    status: str
    created_at: str
    completed_at: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


def _make_orchestrator() -> ThesisOrchestrator:
    unified = create_unified_llm()
    memory = EpisodicMemory(qdrant_location=":memory:")
    router = Router()
    tools = ToolRegistry()
    store = create_session_store()
    return ThesisOrchestrator(
        unified=unified,
        memory=memory,
        router=router,
        tool_registry=tools,
        session_store=store,
    )


async def _run_task(task_id: str, request: TaskRequest) -> None:
    try:
        _tasks[task_id]["status"] = "running"
        orch = _make_orchestrator()
        rc = ResearchContext(
            research_question=request.query,
            topic_summary=request.topic_summary or request.query,
            discipline=request.discipline,
            existing_knowledge=request.existing_knowledge or "",
            what_they_need=request.what_they_need or "",
        )
        session = await orch.execute(rc)
        _tasks[task_id]["status"] = "complete"
        _tasks[task_id]["result"] = session.model_dump()
        _tasks[task_id]["completed_at"] = datetime.now(timezone.utc).isoformat()
    except Exception as exc:
        logger.exception("Task %s failed", task_id)
        _tasks[task_id]["status"] = "failed"
        _tasks[task_id]["error"] = str(exc)
        _tasks[task_id]["completed_at"] = datetime.now(timezone.utc).isoformat()


@app.get("/health")
async def health_check(request: Request):
    rl = _rate_limiter.lookup(request)
    return {
        "status": "ok",
        "tier": "api-gateway",
        "pending_tasks": len(_tasks),
        "rate_limit": rl,
    }


@app.post("/tasks/", response_model=TaskResponse, status_code=202)
async def submit_task(request: TaskRequest, _rip: str = Depends(_rate_limiter)):
    task_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    _tasks[task_id] = {
        "status": "queued",
        "created_at": created_at,
        "result": None,
        "error": None,
        "completed_at": None,
    }
    asyncio.create_task(_run_task(task_id, request))
    return TaskResponse(task_id=task_id, status="queued", created_at=created_at)


@app.get("/tasks/{task_id}", response_model=TaskResult)
async def get_task(task_id: str):
    task = _tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id!r} not found")
    return TaskResult(
        task_id=task_id,
        status=task["status"],
        created_at=task["created_at"],
        completed_at=task.get("completed_at"),
        result=task.get("result"),
        error=task.get("error"),
    )


@app.get("/tasks/")
async def list_tasks():
    return [
        {
            "task_id": tid,
            "status": t["status"],
            "created_at": t["created_at"],
            "completed_at": t.get("completed_at"),
        }
        for tid, t in _tasks.items()
    ]


@app.delete("/tasks/{task_id}", status_code=204)
async def delete_task(task_id: str):
    if task_id not in _tasks:
        raise HTTPException(status_code=404, detail=f"Task {task_id!r} not found")
    del _tasks[task_id]
