import json
import logging
from datetime import datetime
from typing import Any, Dict


class StructuredLogger:
    """Provides structured JSON logging for observability."""

    def __init__(self, logger_name: str = "openworkers"):
        self.logger = logging.getLogger(logger_name)
        self.logger.setLevel(logging.INFO)
        # Avoid duplicate handlers if imported multiple times
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            self.logger.addHandler(handler)

    def log_event(self, event_type: str, session_id: str, data: Dict[str, Any]):
        payload = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "event_type": event_type,
            "session_id": session_id,
            "data": data,
        }
        # In a real system, this might push to Datadog / ELK
        self.logger.info(json.dumps(payload))

    def log_trace(self, session_id: str, route_strategy: str, latency_ms: int, success: bool):
        self.log_event(
            "route_trace",
            session_id,
            {"strategy": route_strategy, "latency_ms": latency_ms, "success": success},
        )

    def log_memory_hit(self, session_id: str, task_type: str, hits: int):
        self.log_event(
            "memory_access",
            session_id,
            {"task_type": task_type, "hit_count": hits, "hit_ratio": hits > 0},
        )

    def log_budget(self, session_id: str, metric_name: str, cost: float):
        self.log_event("budget_decrement", session_id, {"metric": metric_name, "cost_usd": cost})


obs_logger = StructuredLogger()
