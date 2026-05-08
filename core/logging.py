"""Structured logging via structlog.

Two modes driven by ``ENVIRONMENT``:
* ``development`` (default) — Rich coloured output with tracebacks inline,
  easy to read in a terminal.
* ``production`` — newline-delimited JSON, easy to ingest by log aggregators
  (Datadog, ELK, Cloud Logging, …).

All standard ``logging.getLogger()`` loggers are automatically routed
through the same processor chain, so third-party libraries (redis, httpx,
qdrant, …) also emit structured output without any changes.

Usage
-----
Call ``configure_logging()`` once at each app entrypoint (``apps/api/``,
``apps/mcp_server/``, ``apps/worker/``). Do **not** call it in library code
or tests — tests rely on pytest's default capture.

    from core.logging import configure_logging
    configure_logging()
"""

from __future__ import annotations

import logging
import sys
from typing import Any, Optional

import structlog


def configure_logging(
    level: Optional[int] = None,
    environment: Optional[str] = None,
) -> None:
    """Configure the root logger and structlog processor chain."""
    from core.config import get_settings

    settings = get_settings()
    if level is None:
        level = getattr(logging, settings.log_level.upper(), logging.INFO)
    if environment is None:
        environment = settings.environment

    timestamper = structlog.processors.TimeStamper(fmt="iso")

    pre_chain: list[Any] = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        timestamper,
    ]

    renderer: Any
    if environment == "production":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=pre_chain,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            timestamper,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.StackInfoRenderer(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
