from __future__ import annotations

import time
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from core.config import get_settings


class IPRateLimitMiddleware(BaseHTTPMiddleware):
    """Per-IP rate limiting with rolling minute and hour windows.

    Tracked in-process — appropriate for a single-worker API behind a
    reverse proxy that sets ``X-Forwarded-For`` or ``X-Real-IP``.
    Old entries are pruned lazily on each request and opportunistically
    when the tracked IP count exceeds a watermark.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        settings = get_settings()
        if not settings.api_rate_limit_enabled:
            return await call_next(request)

        client_ip = _extract_client_ip(request)
        now = time.monotonic()

        _prune_if_needed(now, settings.api_rate_limit_cleanup_interval_sec)

        minute_key = (client_ip, "m")
        hour_key = (client_ip, "h")

        minute_timestamps = _state.setdefault(minute_key, deque())
        hour_timestamps = _state.setdefault(hour_key, deque())

        _trim(minute_timestamps, now - 60)
        _trim(hour_timestamps, now - 3600)

        minute_count = len(minute_timestamps)
        hour_count = len(hour_timestamps)

        if minute_count >= settings.api_rate_limit_requests_per_minute:
            return _rate_limit_response(
                client_ip,
                minute_count,
                settings.api_rate_limit_requests_per_minute,
                "minute",
            )
        if hour_count >= settings.api_rate_limit_requests_per_hour:
            return _rate_limit_response(
                client_ip,
                hour_count,
                settings.api_rate_limit_requests_per_hour,
                "hour",
            )

        minute_timestamps.append(now)
        hour_timestamps.append(now)

        response = await call_next(request)
        response.headers["X-RateLimit-Remaining-Minute"] = str(
            max(0, settings.api_rate_limit_requests_per_minute - len(minute_timestamps) - 1)
        )
        response.headers["X-RateLimit-Remaining-Hour"] = str(
            max(0, settings.api_rate_limit_requests_per_hour - len(hour_timestamps) - 1)
        )
        return response


# ── internal state ────────────────────────────────────────────────────────────

_state: OrderedDict[tuple[str, str], deque[float]] = OrderedDict()
_last_prune: float = 0.0


def _extract_client_ip(request: Request) -> str:
    x_forwarded = request.headers.get("X-Forwarded-For")
    if x_forwarded:
        return x_forwarded.split(",")[0].strip()
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    client = request.client
    if client:
        return client.host or "unknown"
    return "unknown"


def _trim(timestamps: deque[float], cutoff: float) -> None:
    while timestamps and timestamps[0] < cutoff:
        timestamps.popleft()


def _prune_if_needed(now: float, cleanup_interval_sec: int) -> None:
    global _last_prune
    if now - _last_prune < cleanup_interval_sec:
        return
    _last_prune = now
    stale_cutoff = now - 3600
    stale_keys = [k for k, v in _state.items() if not v or (v and v[-1] < stale_cutoff)]
    for k in stale_keys:
        del _state[k]


def _rate_limit_response(
    client_ip: str, current_count: int, limit: int, window: str
) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={
            "detail": f"Rate limit exceeded for {client_ip}. "
            f"{current_count} requests in the last {window} (limit: {limit})."
        },
        headers={
            "Retry-After": "60" if window == "minute" else "3600",
            "X-RateLimit-Limit": str(limit),
            "X-RateLimit-Remaining": "0",
        },
    )
