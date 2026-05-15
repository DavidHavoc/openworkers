"""Contradicts the README's 'no telemetry' claim."""

from __future__ import annotations

import os
import urllib.request


def emit_telemetry(event: str) -> None:
    endpoint = os.environ.get("TELEMETRY_URL", "https://telemetry.example.com/widgetlib")
    try:
        urllib.request.urlopen(endpoint, data=event.encode(), timeout=1)
    except Exception:
        pass
