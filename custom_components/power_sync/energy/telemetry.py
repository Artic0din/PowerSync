"""Normalized energy telemetry contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class EnergySnapshot:
    soc: float | None = None  # 0–100 percent
    battery_w: float | None = None  # +charge / −charge convention as exposed by brand
    grid_w: float | None = None
    solar_w: float | None = None
    home_w: float | None = None
    battery_capacity_kwh: float | None = None
    raw: dict[str, Any] | None = None


@runtime_checkable
class EnergyTelemetry(Protocol):
    def get_snapshot(self) -> EnergySnapshot: ...


class CoordinatorEnergyTelemetry:
    """Read common normalized fields from brand energy coordinators."""

    _FIELD_MAP = {
        "soc": ("battery_soc", "soc", "percentage"),
        "battery_w": ("battery_power", "battery_w", "battery_power_w"),
        "grid_w": ("grid_power", "grid_w", "grid_power_w"),
        "solar_w": ("solar_power", "solar_w", "pv_power", "pv_power_w"),
        "home_w": ("home_power", "home_w", "load_power", "house_power"),
        "battery_capacity_kwh": (
            "battery_capacity_kwh",
            "capacity_kwh",
            "nominal_energy",
        ),
    }

    def __init__(self, coordinator: Any) -> None:
        self._coordinator = coordinator

    def get_snapshot(self) -> EnergySnapshot:
        data = getattr(self._coordinator, "data", None)
        if not isinstance(data, dict):
            data = {}
        values: dict[str, Any] = {}
        for field_name, keys in self._FIELD_MAP.items():
            for key in keys:
                if key in data and data[key] is not None:
                    values[field_name] = data[key]
                    break
            else:
                # Also check coordinator attributes
                for key in keys:
                    attr = getattr(self._coordinator, key, None)
                    if attr is not None:
                        values[field_name] = attr
                        break

        def _f(name: str) -> float | None:
            raw = values.get(name)
            if raw is None:
                return None
            try:
                return float(raw)
            except (TypeError, ValueError):
                return None

        return EnergySnapshot(
            soc=_f("soc"),
            battery_w=_f("battery_w"),
            grid_w=_f("grid_w"),
            solar_w=_f("solar_w"),
            home_w=_f("home_w"),
            battery_capacity_kwh=_f("battery_capacity_kwh"),
            raw=data or None,
        )
