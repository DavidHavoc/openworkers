import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from core.logging import configure_logging
from core.memory.episodic import EpisodicMemory
from core.orchestrator.thesis_flow import ThesisOrchestrator
from core.router.engine import Router
from core.schemas import ResearchContext
from core.sessions.store import create_session_store
from providers.adapters import create_unified_llm
from tools.mcp.engine import ToolRegistry

configure_logging()
logger = logging.getLogger(__name__)

app = FastAPI(
    title="OpenWorkers API",
    description="Research-focused hierarchical multi-agent system",
    version="0.2.0",
)

# In-memory task store: task_id -> {status, result, error, created_at}
_tasks: Dict[str, Dict[str, Any]] = {}
_task_handles: Dict[str, asyncio.Task] = {}


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
        if task_id not in _tasks:
            return
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
        if task_id not in _tasks:
            return
        _tasks[task_id]["status"] = "complete"
        _tasks[task_id]["result"] = session.model_dump()
        _tasks[task_id]["completed_at"] = datetime.now(timezone.utc).isoformat()
    except asyncio.CancelledError:
        return
    except Exception as exc:
        logger.exception("Task %s failed", task_id)
        if task_id not in _tasks:
            return
        _tasks[task_id]["status"] = "failed"
        _tasks[task_id]["error"] = str(exc)
        _tasks[task_id]["completed_at"] = datetime.now(timezone.utc).isoformat()
    finally:
        _task_handles.pop(task_id, None)


@app.get("/health")
async def health_check():
    return {"status": "ok", "tier": "api-gateway", "pending_tasks": len(_tasks)}


@app.post("/tasks/", response_model=TaskResponse, status_code=202)
async def submit_task(request: TaskRequest):
    task_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    _tasks[task_id] = {
        "status": "queued",
        "created_at": created_at,
        "result": None,
        "error": None,
        "completed_at": None,
    }
    handle = asyncio.create_task(_run_task(task_id, request))
    _task_handles[task_id] = handle
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
    handle = _task_handles.pop(task_id, None)
    if handle and not handle.done():
        handle.cancel()
    del _tasks[task_id]
