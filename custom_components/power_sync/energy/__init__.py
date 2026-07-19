"""Energy telemetry Protocol and brand energy coordinators."""

from __future__ import annotations

from typing import Any

from .telemetry import EnergySnapshot, EnergyTelemetry, CoordinatorEnergyTelemetry

_COORDINATOR_EXPORTS = frozenset(
    {
        "EnergyAccumulator",
        "TeslaEnergyCoordinator",
        "SigenergyEnergyCoordinator",
        "AlphaESSEnergyCoordinator",
        "SungrowEnergyCoordinator",
        "DualSungrowCoordinator",
        "FoxESSEnergyCoordinator",
        "FoxESSEntityEnergyCoordinator",
        "FoxESSCloudEnergyCoordinator",
        "GoodWeEnergyCoordinator",
        "SolaxBatteryEnergyCoordinator",
        "SolarEdgeEnergyCoordinator",
        "SajH2EnergyCoordinator",
        "FroniusReservaEnergyCoordinator",
        "NeovoltEnergyCoordinator",
        "AnkerSolixEnergyCoordinator",
        "ESYSunhomeEnergyCoordinator",
    }
)

__all__ = [
    "CoordinatorEnergyTelemetry",
    "EnergySnapshot",
    "EnergyTelemetry",
    *_COORDINATOR_EXPORTS,
]


def __getattr__(name: str) -> Any:
    # Lazy: coordinators pull Home Assistant; keep telemetry import-light.
    if name in _COORDINATOR_EXPORTS:
        from . import coordinators as _coordinators

        return getattr(_coordinators, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
