"""Regression tests for #29: site_info must recover automatically after a
transient failure instead of permanently disabling fetches until reload.

Before this fix, ``TeslaEnergyCoordinator.async_get_site_info()`` set a
permanent ``_site_info_fetch_failed`` boolean on *any* exception (including a
genuine credential failure) and never cleared it until the next config-entry
reload or HA restart — a transient DNS/connection blip during a wider outage
disabled site_info fetches indefinitely. It's now a timestamped backoff that
retries automatically, preserves the last-known-good payload while backed
off, and lets ``ConfigEntryAuthFailed`` propagate for HA's reauth flow
instead of being folded into the negative cache.
"""

from __future__ import annotations

import asyncio
import functools
import sys
import types
from pathlib import Path

import pytest


def async_test(function):
    """Run an async test function without requiring pytest-asyncio.

    Matches the pattern already used in
    tests/test_powerwall_v1r_teslemetry_parity.py -- this repo's test suite
    does not enable pytest-asyncio's auto mode.
    """

    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return wrapper


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
    # Direct assignment, not setdefault: a stale "power_sync" package object
    # left behind by an earlier test module can carry a cached `.coordinator`
    # attribute that shadows the fresh reimport below (`from pkg import name`
    # resolves via getattr on the package object before consulting
    # sys.modules for the dotted submodule), so it must be replaced outright.
    sys.modules["power_sync"] = ps


_install_coordinator_stubs()
# Several other test files in this suite install a minimal fake
# "power_sync.const" module (only exposing the few names they need) and
# leave it in sys.modules. Pop it too, alongside the coordinator submodule
# itself, so this file always imports the real const.py rather than
# inheriting whatever partial stub a previously-collected test file left
# behind.
sys.modules.pop("power_sync.coordinator", None)
sys.modules.pop("power_sync.const", None)

# importlib.import_module (not `from power_sync import coordinator`) forces
# resolution through sys.modules for the dotted path, avoiding the stale
# getattr-shortcut described above. Matches the pattern already used in
# tests/test_amber_price_cache.py for the same reason.
import importlib  # noqa: E402

coordinator_module = importlib.import_module("power_sync.coordinator")
DOMAIN = coordinator_module.DOMAIN
TeslaEnergyCoordinator = coordinator_module.TeslaEnergyCoordinator
UpdateFailed = coordinator_module.UpdateFailed

const_module = importlib.import_module("power_sync.const")
TESLA_PROVIDER_TESLEMETRY = const_module.TESLA_PROVIDER_TESLEMETRY
TESLA_SITE_INFO_RETRY_BACKOFF_SECONDS = const_module.TESLA_SITE_INFO_RETRY_BACKOFF_SECONDS

ConfigEntryAuthFailed = sys.modules["homeassistant.exceptions"].ConfigEntryAuthFailed


def _new_coordinator() -> TeslaEnergyCoordinator:
    coordinator = TeslaEnergyCoordinator.__new__(TeslaEnergyCoordinator)
    entry_id = "site-info-entry"
    coordinator.hass = types.SimpleNamespace(
        data={DOMAIN: {entry_id: {}}},
        config_entries=types.SimpleNamespace(async_get_entry=lambda entry_id: None),
    )
    coordinator._entry_id = entry_id
    coordinator.site_id = "12345"
    coordinator.api_provider = TESLA_PROVIDER_TESLEMETRY
    coordinator.api_base_url = "https://example.test"
    coordinator._token_getter = None
    coordinator._api_token = "test-token"
    coordinator.session = None
    coordinator._site_info_cache = None
    coordinator._site_info_last_fetch = 0
    coordinator._site_info_next_retry = 0
    coordinator._firmware = None
    coordinator._site_country = None
    coordinator._off_grid_reserve_percent = None
    coordinator._storm_mode_enabled = None
    coordinator._capabilities_probed = True  # skip the background capability probe
    return coordinator


def _clock(monkeypatch, start: float = 1_000.0):
    """Install a controllable fake monotonic clock on the coordinator module."""
    state = {"now": start}
    monkeypatch.setattr(coordinator_module.time, "monotonic", lambda: state["now"])
    return state


def _site_info_response(tz: str = "Australia/Melbourne") -> dict:
    return {"response": {"installation_time_zone": tz}}


@async_test
async def test_transient_failure_backs_off_then_retries_automatically(monkeypatch):
    clock = _clock(monkeypatch)
    calls = {"n": 0}

    async def failing_fetch(*args, **kwargs):
        calls["n"] += 1
        raise UpdateFailed("connection reset")

    monkeypatch.setattr(coordinator_module, "_fetch_with_retry", failing_fetch)
    coordinator = _new_coordinator()

    result = await coordinator.async_get_site_info()

    assert result is None
    assert calls["n"] == 1
    assert coordinator._site_info_next_retry == pytest.approx(
        clock["now"] + TESLA_SITE_INFO_RETRY_BACKOFF_SECONDS
    )

    # Still inside the backoff window: no network call, no reload/restart needed.
    clock["now"] += TESLA_SITE_INFO_RETRY_BACKOFF_SECONDS - 1
    result = await coordinator.async_get_site_info()
    assert result is None
    assert calls["n"] == 1

    # Backoff window has elapsed: the next call retries automatically.
    async def succeeding_fetch(*args, **kwargs):
        calls["n"] += 1
        return _site_info_response()

    monkeypatch.setattr(coordinator_module, "_fetch_with_retry", succeeding_fetch)
    clock["now"] += 2
    result = await coordinator.async_get_site_info()

    assert calls["n"] == 2
    assert result["installation_time_zone"] == "Australia/Melbourne"
    assert coordinator._site_info_next_retry == 0


@async_test
async def test_transient_failure_preserves_last_known_cache(monkeypatch):
    clock = _clock(monkeypatch)

    async def succeeding_fetch(*args, **kwargs):
        return _site_info_response("America/Los_Angeles")

    monkeypatch.setattr(coordinator_module, "_fetch_with_retry", succeeding_fetch)
    coordinator = _new_coordinator()

    first = await coordinator.async_get_site_info()
    assert first["installation_time_zone"] == "America/Los_Angeles"

    # Cache goes stale, and the refetch fails transiently.
    clock["now"] += coordinator_module.TESLA_SITE_INFO_CACHE_TTL_SECONDS + 1

    async def failing_fetch(*args, **kwargs):
        raise UpdateFailed("dns failure")

    monkeypatch.setattr(coordinator_module, "_fetch_with_retry", failing_fetch)

    result = await coordinator.async_get_site_info()

    # Before this fix, a stale-cache-plus-failed-refetch always returned None,
    # discarding the last known-good payload.
    assert result is not None
    assert result["installation_time_zone"] == "America/Los_Angeles"
    assert coordinator._site_info_next_retry > clock["now"]


@async_test
async def test_auth_failure_propagates_and_is_not_negative_cached(monkeypatch):
    _clock(monkeypatch)

    async def auth_failing_fetch(*args, **kwargs):
        raise ConfigEntryAuthFailed("token rejected")

    monkeypatch.setattr(coordinator_module, "_fetch_with_retry", auth_failing_fetch)
    coordinator = _new_coordinator()

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator.async_get_site_info()

    # A genuine credential failure must not engage the transient-failure
    # backoff -- HA's reauth flow owns recovery here, not a timed retry.
    assert coordinator._site_info_next_retry == 0


@async_test
async def test_recovery_after_backoff_clears_state_and_logs_once(monkeypatch, caplog):
    clock = _clock(monkeypatch)

    async def failing_fetch(*args, **kwargs):
        raise UpdateFailed("timeout")

    monkeypatch.setattr(coordinator_module, "_fetch_with_retry", failing_fetch)
    coordinator = _new_coordinator()
    await coordinator.async_get_site_info()
    assert coordinator._site_info_next_retry > 0

    clock["now"] += TESLA_SITE_INFO_RETRY_BACKOFF_SECONDS + 1

    async def succeeding_fetch(*args, **kwargs):
        return _site_info_response()

    monkeypatch.setattr(coordinator_module, "_fetch_with_retry", succeeding_fetch)
    with caplog.at_level("INFO", logger=coordinator_module._LOGGER.name):
        result = await coordinator.async_get_site_info()

    assert result is not None
    assert coordinator._site_info_next_retry == 0
    assert any("Recovered site_info fetch" in message for message in caplog.messages)
