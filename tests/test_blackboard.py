"""Tests for core/blackboard/engine.py.

Covers regressions for:
* CR-01 — "memory_guidance" is a valid entry type. The pre-fix
  validate-list raised ValueError, which was silently swallowed by
  the orchestrator's _add_entry, so memory was never recorded.
* WR-08 — Timestamps are timezone-aware ISO 8601 (datetime.now(timezone.utc)),
  not naive datetime.utcnow() with a manually appended "Z".
"""

from __future__ import annotations

from datetime import datetime

import fakeredis
import pytest

from core.blackboard.engine import Blackboard

_DOCUMENTED_TYPES = (
    "task",
    "evidence_ref",
    "route_decision",
    "agent_output",
    "status",
    "lit_search",
    "lit_map",
    "critique",
    "citation_audit",
    "corpus_benchmarks",
    "memory_guidance",
)


@pytest.fixture(autouse=True)
def mock_redis(monkeypatch):
    server = fakeredis.FakeServer()
    monkeypatch.setattr(
        "redis.from_url",
        lambda *a, **kw: fakeredis.FakeRedis(server=server, decode_responses=True),
    )


def test_memory_guidance_is_allowed_entry_type_cr01():
    """CR-01 regression: memory_guidance must be in the allowed_types set."""
    bb = Blackboard()
    entry = bb.add_entry("memory_guidance", {"guidance": "consider prior episodes"})
    assert entry.entry_type == "memory_guidance"
    assert entry.content == {"guidance": "consider prior episodes"}


@pytest.mark.parametrize("entry_type", _DOCUMENTED_TYPES)
def test_all_documented_entry_types_accepted(entry_type):
    """Snapshot of accepted entry types — change deliberately, not by accident."""
    bb = Blackboard()
    entry = bb.add_entry(entry_type, {"x": 1})
    assert entry.entry_type == entry_type


def test_invalid_entry_type_raises_value_error():
    bb = Blackboard()
    with pytest.raises(ValueError, match="Invalid entry_type"):
        bb.add_entry("not_a_real_type", {"x": 1})


def test_timestamp_is_timezone_aware_iso8601_wr08():
    """WR-08 regression: timestamp uses datetime.now(timezone.utc), not naive utcnow + 'Z'."""
    bb = Blackboard()
    entry = bb.add_entry("status", {"x": 1})

    # Tz-aware ISO 8601 ends with +00:00 (or an offset). The pre-fix
    # naive utcnow().isoformat() + "Z" produced "...000Z" which has no offset.
    parsed = datetime.fromisoformat(entry.timestamp)
    assert parsed.tzinfo is not None, f"Expected tz-aware datetime, got naive: {entry.timestamp!r}"


def test_blackboard_isolates_per_session_id():
    """Different session_ids namespace their entries — no cross-session leakage."""
    bb_a = Blackboard(session_id="session-a")
    bb_b = Blackboard(session_id="session-b")

    bb_a.add_entry("task", {"who": "a"})
    bb_b.add_entry("task", {"who": "b"})

    a_entries = bb_a.get_all_entries()
    b_entries = bb_b.get_all_entries()

    assert len(a_entries) == 1
    assert len(b_entries) == 1
    assert a_entries[0].content == {"who": "a"}
    assert b_entries[0].content == {"who": "b"}


def test_get_entries_by_type_filters_correctly():
    bb = Blackboard(session_id="filter-test")
    bb.add_entry("task", {"x": 1})
    bb.add_entry("status", {"x": 2})
    bb.add_entry("task", {"x": 3})

    tasks = bb.get_entries_by_type("task")
    statuses = bb.get_entries_by_type("status")

    assert len(tasks) == 2
    assert len(statuses) == 1
    assert all(e.entry_type == "task" for e in tasks)


def test_get_all_entries_returns_only_session_keys_wr04():
    """WR-04 supporting: get_all_entries scans only this session's prefix.

    Sanity check that scan_iter with prefix correctly isolates session keys
    even when other sessions populate the same Redis instance.
    """
    bb_a = Blackboard(session_id="a")
    bb_b = Blackboard(session_id="b")

    for _ in range(5):
        bb_a.add_entry("task", {"x": 1})
    for _ in range(3):
        bb_b.add_entry("task", {"x": 2})

    assert len(bb_a.get_all_entries()) == 5
    assert len(bb_b.get_all_entries()) == 3
