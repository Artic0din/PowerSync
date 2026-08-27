"""Regression test for issue #31: concurrent async_get_site_info() callers
must be coalesced into a single Tesla API request.

Multiple setup/refresh paths (coordinator.py, optimization/coordinator.py,
number.py, switch.py, select.py, __init__.py, automations/*.py) can all
observe a cold site_info cache at the same time -- e.g. right after a config
entry reload. Before this fix, each one independently issued its own HTTP
call to the same site_info endpoint, multiplying retries and, per the
reported log, producing duplicate permanent negative-cache failures during a
transient network outage.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import types

import pytest


ROOT = Path(__file__).resolve().parent.parent
COMPONENT_ROOT = ROOT / "custom_components" / "power_sync"


def _install_coordinator_stubs() -> None:
    """Stub just enough of homeassistant.* for coordinator.py to import.

    Mirrors the stub set used by tests/test_home_load_telemetry.py, which
    exercises the same coordinator.py module.
    """
    ha_components = types.ModuleType("homeassistant.components")
    ha_recorder = types.ModuleType("homeassistant.components.recorder")
    ha_recorder_history = types.ModuleType("homeassistant.components.recorder.history")
    ha_root = types.ModuleType("homeassistant")
    ha_core = types.ModuleType("homeassistant.core")
    ha_exceptions = types.ModuleType("homeassistant.exceptions")
    ha_helpers = types.ModuleType("homeassistant.helpers")
    ha_update_coordinator = types.ModuleType("homeassistant.helpers.update_coordinator")
    ha_aiohttp_client = types.ModuleType("homeassistant.helpers.aiohttp_client")
    ha_dispatcher = types.ModuleType("homeassistant.helpers.dispatcher")
    ha_storage = types.ModuleType("homeassistant.helpers.storage")
    ha_util = types.ModuleType("homeassistant.util")
    ha_dt = types.ModuleType("homeassistant.util.dt")

    class DataUpdateCoordinator:
        def __init__(self, hass, *args, **kwargs) -> None:
            self.hass = hass
            self.data = None

    class Store:
        def __init__(self, *args, **kwargs) -> None:
            self.data = None

        async def async_load(self):
            return self.data

        async def async_save(self, data):
            self.data = data

    class FakeRecorder:
        async def async_add_executor_job(self, func, *args):
            return func(*args)

    def get_significant_states(hass, start_time, end_time, entity_ids):
        return {}

    ha_core.HomeAssistant = type("HomeAssistant", (), {})
    ha_exceptions.ConfigEntryAuthFailed = type("ConfigEntryAuthFailed", (Exception,), {})
    ha_update_coordinator.DataUpdateCoordinator = DataUpdateCoordinator
    ha_update_coordinator.UpdateFailed = type("UpdateFailed", (Exception,), {})
    ha_aiohttp_client.async_get_clientsession = lambda hass: None
    ha_dispatcher.async_dispatcher_send = lambda *args, **kwargs: None
    ha_storage.Store = Store
    from datetime import datetime as _dt

    ha_dt.utcnow = lambda: _dt(2026, 7, 8, 1, 0, 0)
    ha_dt.now = lambda: _dt(2026, 7, 8, 12, 0, 0)
    ha_recorder.get_instance = lambda hass: FakeRecorder()
    ha_recorder_history.get_significant_states = get_significant_states

    sys.modules["homeassistant"] = ha_root
    sys.modules["homeassistant.components"] = ha_components
    sys.modules["homeassistant.components.recorder"] = ha_recorder
    sys.modules["homeassistant.components.recorder.history"] = ha_recorder_history
    sys.modules["homeassistant.core"] = ha_core
    sys.modules["homeassistant.exceptions"] = ha_exceptions
    sys.modules["homeassistant.helpers"] = ha_helpers
    sys.modules["homeassistant.helpers.update_coordinator"] = ha_update_coordinator
    sys.modules["homeassistant.helpers.aiohttp_client"] = ha_aiohttp_client
    sys.modules["homeassistant.helpers.dispatcher"] = ha_dispatcher
    sys.modules["homeassistant.helpers.storage"] = ha_storage
    sys.modules["homeassistant.util"] = ha_util
    sys.modules["homeassistant.util.dt"] = ha_dt

    ps = types.ModuleType("power_sync")
    ps.__path__ = [str(COMPONENT_ROOT)]
    sys.modules.setdefault("power_sync", ps)


_install_coordinator_stubs()
sys.modules.pop("power_sync.coordinator", None)

import power_sync.coordinator as coordinator_module  # noqa: E402
from power_sync.coordinator import TeslaEnergyCoordinator  # noqa: E402


def _new_tesla_coordinator() -> TeslaEnergyCoordinator:
    coordinator = TeslaEnergyCoordinator.__new__(TeslaEnergyCoordinator)
    coordinator.site_id = "site-1"
    coordinator.api_base_url = "https://example.invalid"
    coordinator.api_provider = "fleet_api"
    coordinator.hass = types.SimpleNamespace(
        async_create_task=lambda coro, name=None: coro.close()
    )
    coordinator.session = None
    coordinator._site_info_cache = None
    coordinator._site_info_last_fetch = 0
    coordinator._site_info_fetch_failed = False
    coordinator._site_info_lock = asyncio.Lock()
    coordinator._capabilities_probed = True  # skip the background probe task
    coordinator._firmware = None
    coordinator._site_country = None
    coordinator._off_grid_reserve_percent = None
    coordinator._storm_mode_enabled = None
    coordinator._get_current_token = lambda: "token"
    coordinator._tesla_headers = lambda token=None: {}
    return coordinator


def test_concurrent_site_info_calls_issue_one_http_request(monkeypatch):
    """Ten concurrent callers with a cold cache must produce exactly one fetch."""
    coordinator = _new_tesla_coordinator()
    call_count = 0

    async def fake_fetch_with_retry(session, url, headers, **kwargs):
        nonlocal call_count
        call_count += 1
        # Yield control so concurrently-scheduled callers actually overlap
        # instead of trivially running one after another.
        await asyncio.sleep(0.01)
        return {"response": {"installation_time_zone": "Australia/Melbourne"}}

    monkeypatch.setattr(coordinator_module, "_fetch_with_retry", fake_fetch_with_retry)

    async def run():
        results = await asyncio.gather(
            *[coordinator.async_get_site_info() for _ in range(10)]
        )
        return results

    results = asyncio.run(run())

    assert call_count == 1
    assert all(r == {"installation_time_zone": "Australia/Melbourne"} for r in results)


def test_cache_invalidation_then_concurrent_reads_produces_one_refetch(monkeypatch):
    """Invalidating the cache and reading concurrently should refetch exactly once."""
    coordinator = _new_tesla_coordinator()
    call_count = 0

    async def fake_fetch_with_retry(session, url, headers, **kwargs):
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.01)
        return {"response": {"installation_time_zone": "Australia/Melbourne", "call": call_count}}

    monkeypatch.setattr(coordinator_module, "_fetch_with_retry", fake_fetch_with_retry)

    async def run():
        first = await coordinator.async_get_site_info()
        assert call_count == 1

        coordinator.invalidate_site_info_cache()

        results = await asyncio.gather(
            *[coordinator.async_get_site_info() for _ in range(5)]
        )
        return first, results

    first, results = asyncio.run(run())

    assert call_count == 2
    assert all(r == results[0] for r in results)
    assert results[0]["call"] == 2
