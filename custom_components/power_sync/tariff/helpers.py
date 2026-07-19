"""Shared tariff schedule helpers."""

from __future__ import annotations

from typing import Any


def coalesce_tariff_schedule(*candidates: Any) -> dict[str, Any] | None:
    """Return the first non-empty mapping among candidates."""
    for candidate in candidates:
        if isinstance(candidate, dict) and candidate:
            return candidate
    return None
