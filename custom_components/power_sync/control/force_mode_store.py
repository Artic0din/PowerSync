"""Shared force-mode state store extracted from setup closures."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ForceModeStore:
    """Tracks user/optimizer force charge/discharge ownership and timers."""

    active_mode: str | None = None  # "charge" | "discharge" | None
    source: str | None = None  # "user" | "optimizer" | ...
    power_w: float | None = None
    duration_minutes: int | None = None
    started_at: float | None = None
    cancel_callback: Any | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def clear(self) -> None:
        """Clear mode flags. Call only after a successful restore write."""
        self.active_mode = None
        self.source = None
        self.power_w = None
        self.duration_minutes = None
        self.started_at = None
        self.extra.clear()

    def is_optimizer_owned(self) -> bool:
        return self.source == "optimizer" and self.active_mode is not None

    def is_user_owned(self) -> bool:
        return self.source == "user" and self.active_mode is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "active_mode": self.active_mode,
            "source": self.source,
            "power_w": self.power_w,
            "duration_minutes": self.duration_minutes,
            "started_at": self.started_at,
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ForceModeStore:
        if not isinstance(data, dict):
            return cls()
        return cls(
            active_mode=data.get("active_mode"),
            source=data.get("source"),
            power_w=data.get("power_w"),
            duration_minutes=data.get("duration_minutes"),
            started_at=data.get("started_at"),
            extra=dict(data.get("extra") or {}),
        )
