"""Tests for periodic Powerwall BMS health polling."""

from __future__ import annotations

import asyncio
import importlib
import sys
import types
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent / "custom_components" / "power_sync"
_SENTINEL = object()
_STUB_MODULE_NAMES = (
    "homeassistant.core",
    "homeassistant.helpers.event",
    "power_sync",
    "power_sync.powerwall_local",
    "power_sync.powerwall_local.bms_health_polling",
)


@pytest.fixture(autouse=True)
def _restore_stubbed_modules():
    saved_modules = {
        name: sys.modules.get(name, _SENTINEL)
        for name in _STUB_MODULE_NAMES
    }
    try:
        yield
    finally:
        for name in _STUB_MODULE_NAMES:
            if saved_modules[name] is _SENTINEL:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = saved_modules[name]


def _install_stubs() -> None:
    ha_core = types.ModuleType("homeassistant.core")
    ha_core.HomeAssistant = object
    sys.modules["homeassistant.core"] = ha_core

    ha_event = types.ModuleType("homeassistant.helpers.event")

    def async_track_time_interval(hass, action, interval):
        hass.interval_action = action
        hass.interval = interval

        def _cancel():
            hass.cancelled = True

        return _cancel

    ha_event.async_track_time_interval = async_track_time_interval
    sys.modules["homeassistant.helpers.event"] = ha_event

    ps_module = types.ModuleType("power_sync")
    ps_module.__path__ = [str(ROOT)]
    sys.modules["power_sync"] = ps_module

    local_module = types.ModuleType("power_sync.powerwall_local")
    local_module.__path__ = [str(ROOT / "powerwall_local")]
    sys.modules["power_sync.powerwall_local"] = local_module


def _polling_module():
    _install_stubs()
    sys.modules.pop("power_sync.powerwall_local.bms_health_polling", None)
    return importlib.import_module("power_sync.powerwall_local.bms_health_polling")


class _Hass:
    cancelled = False


def test_powerwall_bms_health_polling_runs_every_five_minutes():
    polling = _polling_module()
    hass = _Hass()
    synced = []
    payload = {"available": True, "individual_batteries": []}

    async def fetch():
        return payload

    async def sync(data):
        synced.append(data)

    cancel = polling.async_start_powerwall_bms_health_polling(
        hass,
        "entry-1",
        fetch,
        sync,
    )

    assert hass.interval == polling.POWERWALL_BMS_HEALTH_POLL_INTERVAL
    assert hass.interval.total_seconds() == 300

    asyncio.run(hass.interval_action(None))

    assert synced == [payload]

    cancel()
    assert hass.cancelled is True


def test_powerwall_bms_health_polling_skips_overlapping_fetches():
    polling = _polling_module()
    hass = _Hass()
    started = asyncio.Event()
    release = asyncio.Event()
    fetch_count = 0
    synced = []

    async def fetch():
        nonlocal fetch_count
        fetch_count += 1
        started.set()
        await release.wait()
        return {"available": True}

    async def sync(data):
        synced.append(data)

    polling.async_start_powerwall_bms_health_polling(
        hass,
        "entry-1",
        fetch,
        sync,
    )

    async def run_test():
        first = asyncio.create_task(hass.interval_action(None))
        await started.wait()
        await hass.interval_action(None)
        release.set()
        await first

    asyncio.run(run_test())

    assert fetch_count == 1
    assert synced == [{"available": True}]
