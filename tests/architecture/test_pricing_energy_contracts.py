"""Contract tests for PriceProvider and EnergyTelemetry adapters."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from custom_components.power_sync.energy import CoordinatorEnergyTelemetry
from custom_components.power_sync.pricing import (
    CoordinatorPriceProvider,
    normalize_amber_intervals,
)


def test_normalize_amber_intervals_accepts_dict_payloads():
    intervals = normalize_amber_intervals(
        [
            {"perKwh": 12.5, "channelType": "general", "duration": 5},
            {"per_kwh": -3.0, "channel_type": "feedIn"},
            "ignore-me",
        ]
    )
    assert len(intervals) == 2
    assert intervals[0].per_kwh == 12.5
    assert intervals[1].channel_type == "feedIn"


def test_coordinator_price_provider_reads_list_data():
    coordinator = SimpleNamespace(
        data=[
            {"perKwh": 1.0, "channelType": "general"},
            {"perKwh": -2.0, "channelType": "feedIn"},
        ]
    )
    provider = CoordinatorPriceProvider(coordinator)
    intervals = asyncio.get_event_loop().run_until_complete(provider.async_get_intervals())
    assert len(intervals) == 2


def test_coordinator_energy_telemetry_maps_common_fields():
    coordinator = SimpleNamespace(
        data={
            "battery_soc": 55,
            "battery_power": -1200,
            "grid_power": 300,
            "solar_power": 2500,
            "home_power": 1600,
            "battery_capacity_kwh": 13.5,
        }
    )
    snap = CoordinatorEnergyTelemetry(coordinator).get_snapshot()
    assert snap.soc == 55
    assert snap.battery_w == -1200
    assert snap.solar_w == 2500
    assert snap.battery_capacity_kwh == 13.5
