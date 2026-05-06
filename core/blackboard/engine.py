import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import redis

from core.schemas import BlackboardEntry


class Blackboard:
    """
    Structured short-term shared state system for the orchestrator and agents.
    Uses Redis to persist data during the execution loop.
    """

    def __init__(self, session_id: Optional[str] = None):
        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        self.redis = redis.from_url(redis_url, decode_responses=True)
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
        }
        if entry_type not in allowed_types:
            raise ValueError(f"Invalid entry_type '{entry_type}'. Must be one of {allowed_types}")

        entry = BlackboardEntry(
            entry_id=str(uuid.uuid4()),
            entry_type=entry_type,
            content=content,
            metadata=metadata or {},
            timestamp=datetime.utcnow().isoformat() + "Z",
        )

        # Store in Redis as JSON string under session-specific key
        key = f"{self.prefix}{entry.entry_id}"
        self.redis.set(key, entry.model_dump_json())
        return entry

    def get_entries_by_type(self, entry_type: str) -> List[BlackboardEntry]:
        keys = self.redis.keys(f"{self.prefix}*")
        entries = []
        for k in keys:
            data = self.redis.get(k)
            if data:
                entry = BlackboardEntry.model_validate_json(data)
                if entry.entry_type == entry_type:
                    entries.append(entry)
        return sorted(entries, key=lambda x: x.timestamp)

    def get_all_entries(self) -> List[BlackboardEntry]:
        keys = self.redis.keys(f"{self.prefix}*")
        entries = []
        for k in keys:
            data = self.redis.get(k)
            if data:
                entries.append(BlackboardEntry.model_validate_json(data))
        return sorted(entries, key=lambda x: x.timestamp)

    def clear(self):
        keys = self.redis.keys(f"{self.prefix}*")
        if keys:
            self.redis.delete(*keys)
