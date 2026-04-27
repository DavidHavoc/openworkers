from typing import List, Dict, Any, Optional
from datetime import datetime
import uuid
from core.schemas import BlackboardEntry

class Blackboard:
    """
    Structured shared state system for the orchestrator and agents.
    Only allows machine-readable entries, preventing raw free-form chat bloat.
    """
    def __init__(self):
        self._entries: List[BlackboardEntry] = []
        
    def add_entry(self, entry_type: str, content: Dict[str, Any], metadata: Optional[Dict[str, Any]] = None) -> BlackboardEntry:
        """
        Adds a validated entry to the blackboard.
        Supported types: 'task', 'evidence_ref', 'route_decision', 'agent_output', 'status'
        """
        allowed_types = {"task", "evidence_ref", "route_decision", "agent_output", "status"}
        if entry_type not in allowed_types:
            raise ValueError(f"Invalid entry_type '{entry_type}'. Must be one of {allowed_types}")
            
        entry = BlackboardEntry(
            entry_id=str(uuid.uuid4()),
            entry_type=entry_type,
            content=content,
            metadata=metadata or {},
            timestamp=datetime.utcnow().isoformat() + "Z"
        )
        self._entries.append(entry)
        return entry
        
    def get_entries_by_type(self, entry_type: str) -> List[BlackboardEntry]:
        """Retrieve all entries of a specific type."""
        return [e for e in self._entries if e.entry_type == entry_type]
        
    def get_all_entries(self) -> List[BlackboardEntry]:
        return self._entries.copy()
        
    def clear(self):
        """Clear the blackboard for a new session."""
        self._entries = []
