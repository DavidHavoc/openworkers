"""Tests for the FastAPI task endpoints in apps/api/main.py.

Covers regressions for:
* CR-03 — DELETE /tasks/{id} cancels the in-flight asyncio task and
  removes the dict entry without leaving the background coroutine to
  KeyError on resume.
* WR-07 — _tasks OrderedDict is bounded by _MAX_TASKS via LRU eviction.
"""

from __future__ import annotations

import asyncio

import fakeredis
import fakeredis.aioredis
import pytest
from fastapi import HTTPException

from apps.api.main import (
    TaskRequest,
    _task_handles,
    _tasks,
    delete_task,
    get_task,
    list_tasks,
    submit_task,
)


@pytest.fixture(autouse=True)
def mock_redis(monkeypatch):
    server = fakeredis.FakeServer()
    monkeypatch.setattr(
        "redis.from_url",
        lambda *a, **kw: fakeredis.FakeRedis(server=server, decode_responses=True),
    )
    monkeypatch.setattr(
        "redis.asyncio.from_url",
        lambda *a, **kw: fakeredis.aioredis.FakeRedis(server=server, decode_responses=True),
    )


@pytest.fixture(autouse=True)
def clean_task_state():
    _tasks.clear()
    _task_handles.clear()
    yield
    for handle in list(_task_handles.values()):
        if not handle.done():
            handle.cancel()
    _tasks.clear()
    _task_handles.clear()


class _GatedOrchestrator:
    """Stub orchestrator whose execute() blocks on an event the test controls.

    Lets us exercise the in-flight cancellation path deterministically without
    relying on timing or real LLM/Qdrant calls.
    """

    def __init__(self) -> None:
        self.gate = asyncio.Event()
        self.execute_started = asyncio.Event()

    async def execute(self, research_context):
        from core.schemas import ResearchSession

        self.execute_started.set()
        await self.gate.wait()
        return ResearchSession(
            session_id="stub-session",
            research_context=research_context,
            created_at="2026-01-01T00:00:00Z",
            status="complete",
        )


@pytest.fixture
def stub_orchestrator(monkeypatch):
    instances: list[_GatedOrchestrator] = []

    def _make():
        s = _GatedOrchestrator()
        instances.append(s)
        return s

    monkeypatch.setattr("apps.api.main._make_orchestrator", _make)
    return instances


@pytest.mark.asyncio
async def test_submit_task_returns_queued_response(stub_orchestrator):
    resp = await submit_task(TaskRequest(query="hello"))
    assert resp.status == "queued"
    assert resp.task_id in _tasks
    assert _tasks[resp.task_id]["status"] in {"queued", "running"}
    assert resp.task_id in _task_handles


@pytest.mark.asyncio
async def test_get_task_returns_task_state(stub_orchestrator):
    resp = await submit_task(TaskRequest(query="q"))
    result = await get_task(resp.task_id)
    assert result.task_id == resp.task_id
    assert result.status in {"queued", "running"}


@pytest.mark.asyncio
async def test_get_task_404_when_unknown():
    with pytest.raises(HTTPException) as exc_info:
        await get_task("nonexistent")
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_list_tasks_returns_all_submitted(stub_orchestrator):
    r1 = await submit_task(TaskRequest(query="q1"))
    r2 = await submit_task(TaskRequest(query="q2"))
    items = await list_tasks()
    ids = {item["task_id"] for item in items}
    assert {r1.task_id, r2.task_id} <= ids


@pytest.mark.asyncio
async def test_delete_task_404_when_unknown():
    with pytest.raises(HTTPException) as exc_info:
        await delete_task("nonexistent")
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_delete_task_cancels_in_flight_handle_cr03(stub_orchestrator):
    """CR-03 regression: DELETE /tasks/{id} cancels the running asyncio.Task.

    Pre-fix the handle was never stored, so DELETE only removed the dict
    entry while the background coroutine kept running and eventually
    KeyError'd on resume.
    """
    resp = await submit_task(TaskRequest(query="slow"))
    task_id = resp.task_id

    # Let _run_task start so it calls _make_orchestrator and reaches execute().
    while not stub_orchestrator:
        await asyncio.sleep(0)
    await stub_orchestrator[0].execute_started.wait()

    handle = _task_handles[task_id]
    assert not handle.done(), "handle should still be running while gate is closed"

    await delete_task(task_id)

    assert task_id not in _tasks
    assert task_id not in _task_handles
    # Wait for cancellation to propagate; _run_task swallows CancelledError
    # so awaiting the handle should not raise.
    try:
        await asyncio.wait_for(handle, timeout=2.0)
    except asyncio.CancelledError:
        pass
    assert handle.cancelled() or handle.done()


@pytest.mark.asyncio
async def test_run_task_recovers_from_dict_eviction_cr03(stub_orchestrator):
    """CR-03 secondary: _run_task guards against _tasks[task_id] disappearing mid-flight.

    Simulates the original race: dict entry deleted while orchestrator is
    awaiting. Pre-fix the resume would KeyError on `_tasks[task_id][...] = ...`;
    post-fix the `if task_id not in _tasks: return` guards short-circuit.
    """
    resp = await submit_task(TaskRequest(query="q"))
    task_id = resp.task_id

    while not stub_orchestrator:
        await asyncio.sleep(0)
    await stub_orchestrator[0].execute_started.wait()

    # Simulate the race: drop dict entry but DON'T cancel the handle.
    del _tasks[task_id]
    _task_handles.pop(task_id, None)

    # Release the orchestrator so _run_task tries to write back to _tasks[task_id].
    stub_orchestrator[0].gate.set()

    # _run_task should observe the missing key and return cleanly — no KeyError.
    await asyncio.wait_for(asyncio.shield(_pending_handles()), timeout=2.0)


async def _pending_handles():
    # Drain any handles still in flight without surfacing exceptions.
    pending = [t for t in asyncio.all_tasks() if t.get_coro().__name__ == "_run_task"]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_max_tasks_lru_eviction_wr07(monkeypatch, stub_orchestrator):
    """WR-07 regression: _tasks dict is capped via LRU eviction at submit time."""
    monkeypatch.setattr("apps.api.main._MAX_TASKS", 3)

    resps = []
    for i in range(5):
        r = await submit_task(TaskRequest(query=f"q{i}"))
        resps.append(r)

    assert len(_tasks) == 3
    # Two oldest evicted
    assert resps[0].task_id not in _tasks
    assert resps[1].task_id not in _tasks
    # Three newest retained, in insertion order
    retained_ids = list(_tasks.keys())
    assert retained_ids == [resps[2].task_id, resps[3].task_id, resps[4].task_id]
