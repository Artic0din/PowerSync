"""Normalized EV loadpoint state (see docs/wiki/EV-Charging-Refactor.md)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class EVLoadpointState:
    loadpoint_id: str
    vehicle_id: str | None = None
    charger_type: str = "unknown"
    connected: bool = False
    home: bool | None = None
    actual_charging: bool = False
    power_kw: float = 0.0
    current_amps: int | None = None
    target_amps: int | None = None
    soc: float | None = None
    owner: str | None = None
    owner_mode: str | None = None
    blocking_reason: str | None = None
    last_command: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
