from typing import Any

import structlog


class StructuredLogger:
    """Structured event logger backed by structlog.

    Emits JSON in production (``ENVIRONMENT=production``) and coloured
    key-value pairs in development — driven by whatever processor chain
    was configured by ``core.logging.configure_logging()``.
    """

    def __init__(self, logger_name: str = "openworkers") -> None:
        self._log = structlog.get_logger(logger_name)

    def log_event(self, event_type: str, session_id: str, data: dict[str, Any]) -> None:
        self._log.info(event_type, session_id=session_id, data=data)

    def log_trace(
        self, session_id: str, route_strategy: str, latency_ms: int, success: bool
    ) -> None:
        self._log.info(
            "route_trace",
            session_id=session_id,
            strategy=route_strategy,
            latency_ms=latency_ms,
            success=success,
        )

    def log_memory_hit(self, session_id: str, task_type: str, hits: int) -> None:
        self._log.info(
            "memory_access",
            session_id=session_id,
            task_type=task_type,
            hit_count=hits,
            hit_ratio=hits > 0,
        )

    def log_budget(self, session_id: str, metric_name: str, cost: float) -> None:
        self._log.info(
            "budget_decrement",
            session_id=session_id,
            metric=metric_name,
            cost_usd=cost,
        )


obs_logger = StructuredLogger()
