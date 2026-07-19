"""Price provider Protocol, Amber-interval normalizer, and price coordinators."""

from __future__ import annotations

from typing import Any

from .amber_interval import AmberInterval, normalize_amber_intervals
from .provider import PriceProvider, CoordinatorPriceProvider

_COORDINATOR_EXPORTS = frozenset(
    {
        "SIGNAL_AEMO_NEW_DISPATCH",
        "SensitiveDataFilter",
        "AmberPriceCoordinator",
        "LocalvoltsPriceCoordinator",
        "DayUsage",
        "AmberUsageCoordinator",
        "DemandChargeCoordinator",
        "AEMOPriceCoordinator",
        "AEMOSensorCoordinator",
        "FlowPowerKWatchPriceCoordinator",
        "EPEXPriceCoordinator",
        "SolcastForecastCoordinator",
        "OctopusPriceCoordinator",
        "OctopusSavingSessionCoordinator",
        "FlowPowerTWAPTracker",
    }
)

__all__ = [
    "AmberInterval",
    "CoordinatorPriceProvider",
    "PriceProvider",
    "normalize_amber_intervals",
    *_COORDINATOR_EXPORTS,
]


def __getattr__(name: str) -> Any:
    # Lazy: coordinators pull Home Assistant; keep provider/normalizer import-light.
    if name in _COORDINATOR_EXPORTS:
        if name == "SensitiveDataFilter":
            from ._shared import SensitiveDataFilter

            return SensitiveDataFilter
        from . import coordinators as _coordinators

        return getattr(_coordinators, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
