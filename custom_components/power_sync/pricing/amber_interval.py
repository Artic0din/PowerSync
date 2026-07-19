"""Shared Amber-compatible price interval model."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class AmberInterval:
    """Normalized price interval consumed by the optimizer and tariff layers."""

    per_kwh: float  # currency units per kWh (provider-native after conversion)
    channel_type: str  # "general" | "feedIn"
    start: datetime | None = None
    nem_time: str | None = None
    starts_at: str | None = None
    duration: int | None = None  # minutes
    raw: dict[str, Any] | None = None

    def to_amber_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "perKwh": self.per_kwh,
            "channelType": self.channel_type,
        }
        if self.nem_time is not None:
            payload["nemTime"] = self.nem_time
        if self.starts_at is not None:
            payload["startsAt"] = self.starts_at
        if self.duration is not None:
            payload["duration"] = self.duration
        return payload


def normalize_amber_intervals(intervals: list[Any] | None) -> list[AmberInterval]:
    """Coerce dict-like Amber intervals into AmberInterval objects."""
    if not intervals:
        return []
    out: list[AmberInterval] = []
    for item in intervals:
        if isinstance(item, AmberInterval):
            out.append(item)
            continue
        if not isinstance(item, dict):
            continue
        try:
            per_kwh = float(item.get("perKwh", item.get("per_kwh")))
        except (TypeError, ValueError):
            continue
        channel = str(item.get("channelType") or item.get("channel_type") or "general")
        duration = item.get("duration")
        try:
            duration_i = int(duration) if duration is not None else None
        except (TypeError, ValueError):
            duration_i = None
        out.append(
            AmberInterval(
                per_kwh=per_kwh,
                channel_type=channel,
                nem_time=item.get("nemTime") or item.get("nem_time"),
                starts_at=item.get("startsAt") or item.get("starts_at"),
                duration=duration_i,
                raw=item,
            )
        )
    return out
