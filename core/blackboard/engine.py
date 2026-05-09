import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import redis

from core.schemas import BlackboardEntry


class Blackboard:
    """
    Structured short-term shared state system for the orchestrator and agents.
    Uses Redis to persist data during the execution loop.
    """

    def __init__(self, session_id: Optional[str] = None):
        from core.config import get_settings

        self.redis = redis.from_url(get_settings().redis_url, decode_responses=True)
        self.session_id = session_id or str(uuid.uuid4())
        self.prefix = f"blackboard:{self.session_id}:"

    def add_entry(
        self, entry_type: str, content: Dict[str, Any], metadata: Optional[Dict[str, Any]] = None
    ) -> BlackboardEntry:
        allowed_types = {
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
        }
        if entry_type not in allowed_types:
            raise ValueError(f"Invalid entry_type '{entry_type}'. Must be one of {allowed_types}")

        entry = BlackboardEntry(
            entry_id=str(uuid.uuid4()),
            entry_type=entry_type,
            content=content,
            metadata=metadata or {},
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        # Store in Redis as JSON string under session-specific key
        key = f"{self.prefix}{entry.entry_id}"
        self.redis.set(key, entry.model_dump_json())
        return entry

    def get_entries_by_type(self, entry_type: str) -> List[BlackboardEntry]:
        entries = []
        for k in self.redis.scan_iter(f"{self.prefix}*"):
            data = self.redis.get(k)
            if data:
                entry = BlackboardEntry.model_validate_json(data)
                if entry.entry_type == entry_type:
                    entries.append(entry)
        return sorted(entries, key=lambda x: x.timestamp)

    def get_all_entries(self) -> List[BlackboardEntry]:
        entries = []
        for k in self.redis.scan_iter(f"{self.prefix}*"):
            data = self.redis.get(k)
            if data:
                entries.append(BlackboardEntry.model_validate_json(data))
        return sorted(entries, key=lambda x: x.timestamp)

    def clear(self) -> None:
        keys = self.redis.keys(f"{self.prefix}*")
        if keys:
            self.redis.delete(*keys)
