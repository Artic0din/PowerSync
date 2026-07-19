"""Data update coordinators for PowerSync with improved error handling.

Compatibility facade: implementations live in ``pricing.coordinators`` and
``energy.coordinators``. Existing imports from this module keep working.
"""
from __future__ import annotations

from homeassistant.exceptions import ConfigEntryAuthFailed

from .const import DOMAIN
from .pricing._shared import (
    SensitiveDataFilter,
    _flow_power_export_rate_dollars,
    _get_current_prices,
    _parse_retry_after,
    _fetch_with_retry,
)
from .pricing.coordinators import (
    SIGNAL_AEMO_NEW_DISPATCH,
    AmberPriceCoordinator,
    LocalvoltsPriceCoordinator,
    DayUsage,
    AmberUsageCoordinator,
    DemandChargeCoordinator,
    AEMOPriceCoordinator,
    AEMOSensorCoordinator,
    FlowPowerKWatchPriceCoordinator,
    EPEXPriceCoordinator,
    SolcastForecastCoordinator,
    OctopusPriceCoordinator,
    OctopusSavingSessionCoordinator,
    FlowPowerTWAPTracker,
)
from .energy.coordinators import (
    ENERGY_ACC_STORE_VERSION,
    ENERGY_ACC_SAVE_DELAY,
    SOLAREDGE_DAILY_TOTALS_STORE_VERSION,
    LIFETIME_TOTALS_STORE_VERSION,
    TESLA_OUTAGE_NOTIFY_FAILURES,
    TESLA_OUTAGE_NOTIFY_MIN_SECONDS,
    LIFETIME_TOTAL_KEYS,
    EnergyAccumulator,
    TeslaEnergyCoordinator,
    SigenergyEnergyCoordinator,
    AlphaESSEnergyCoordinator,
    SungrowEnergyCoordinator,
    DualSungrowCoordinator,
    FoxESSEnergyCoordinator,
    FoxESSEntityEnergyCoordinator,
    FoxESSCloudEnergyCoordinator,
    GoodWeEnergyCoordinator,
    SolaxBatteryEnergyCoordinator,
    SolarEdgeEnergyCoordinator,
    SajH2EnergyCoordinator,
    FroniusReservaEnergyCoordinator,
    NeovoltEnergyCoordinator,
    AnkerSolixEnergyCoordinator,
    ESYSunhomeEnergyCoordinator,
)

__all__ = [
    "DOMAIN",
    "ConfigEntryAuthFailed",
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
    "LIFETIME_TOTAL_KEYS",
    "_parse_retry_after",
    "_fetch_with_retry",
    "_get_current_prices",
    "_flow_power_export_rate_dollars",
]

