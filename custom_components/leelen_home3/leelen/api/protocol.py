"""Small helpers for interpreting Leelen cloud protocol responses."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


MAX_READ_WAIT_SECONDS = 10.0


def pending_read_delay(response: Mapping[str, Any]) -> float | None:
    """Return a bounded retry delay when the gateway is still reading a FIID."""
    if response.get("result") != 1 or not response.get("waitNum"):
        return None
    try:
        wait_seconds = max(0.0, float(response.get("waitTime", 0)) / 1000.0)
    except (TypeError, ValueError):
        wait_seconds = 0.0
    return min(wait_seconds, MAX_READ_WAIT_SECONDS)
