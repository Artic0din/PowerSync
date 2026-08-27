"""Regression tests for GloBirdCoordinator._fetch_optional() backoff/logging.

Covers issue: repeated optional-endpoint failures used to log a warning on
every single refresh forever, with no backoff on the failing endpoint.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import sys
import types
from datetime import timedelta
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent / "custom_components" / "power_sync"


def _install_coordinator_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    ha_root = types.ModuleType("homeassistant")
    ha_config_entries = types.ModuleType("homeassistant.config_entries")
    ha_core = types.ModuleType("homeassistant.core")
    ha_helpers = types.ModuleType("homeassistant.helpers")
    ha_update = types.ModuleType("homeassistant.helpers.update_coordinator")
    ha_storage = types.ModuleType("homeassistant.helpers.storage")

    class UpdateFailed(Exception):
        pass

    class DataUpdateCoordinator:
        def __init__(self, hass, logger, name=None, update_interval=None) -> None:
            self.hass = hass
            self.logger = logger
            self.name = name
            self.update_interval = update_interval
            self.data = None

        def __class_getitem__(cls, item):
            return cls

    class Store:
        def __init__(self, *args, **kwargs) -> None:
            pass

    ha_config_entries.ConfigEntry = object
    ha_core.HomeAssistant = object
    ha_update.DataUpdateCoordinator = DataUpdateCoordinator
    ha_update.UpdateFailed = UpdateFailed
    ha_storage.Store = Store
    ha_helpers.update_coordinator = ha_update
    ha_helpers.storage = ha_storage
    ha_root.config_entries = ha_config_entries
    ha_root.core = ha_core
    ha_root.helpers = ha_helpers

    for name, module in {
        "homeassistant": ha_root,
        "homeassistant.config_entries": ha_config_entries,
        "homeassistant.core": ha_core,
        "homeassistant.helpers": ha_helpers,
        "homeassistant.helpers.update_coordinator": ha_update,
        "homeassistant.helpers.storage": ha_storage,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)

    ps_module = types.ModuleType("power_sync")
    ps_module.__path__ = [str(ROOT)]
    monkeypatch.setitem(sys.modules, "power_sync", ps_module)
    monkeypatch.delitem(sys.modules, "power_sync.globird_coordinator", raising=False)


def _coordinator_module(monkeypatch: pytest.MonkeyPatch):
    _install_coordinator_stubs(monkeypatch)
    return importlib.import_module("power_sync.globird_coordinator")


def _make_coordinator(coordinator_module, update_interval_seconds: float = 1800):
    """Build a bare coordinator, bypassing __init__'s HA/store wiring."""
    coordinator = coordinator_module.GloBirdCoordinator.__new__(
        coordinator_module.GloBirdCoordinator
    )
    coordinator.update_interval = timedelta(seconds=update_interval_seconds)
    coordinator._optional_fetch_state = {}
    return coordinator


class _FakeClock:
    now = 1_000_000.0


def test_repeated_failure_logs_warning_only_once_then_backs_off(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    coordinator_module = _coordinator_module(monkeypatch)
    coordinator = _make_coordinator(coordinator_module)

    clock = _FakeClock()
    monkeypatch.setattr(coordinator_module.time, "monotonic", lambda: clock.now)

    calls = 0

    async def failing_callback():
        nonlocal calls
        calls += 1
        raise RuntimeError("Unable to get AccountServiceStatus")

    cache = {"service_status": {"stale": "value"}}

    with caplog.at_level(logging.WARNING, logger=coordinator_module._LOGGER.name):
        # First failure: callback is attempted and a warning is logged.
        result = asyncio.run(
            coordinator._fetch_optional("service_status", failing_callback, cache)
        )
    assert result == {"stale": "value"}
    assert calls == 1
    assert sum("optional fetch failed" in rec.message for rec in caplog.records) == 1

    caplog.clear()

    # Immediately retrying (same simulated time) must NOT call the endpoint
    # again or log another warning — it is backing off.
    with caplog.at_level(logging.WARNING, logger=coordinator_module._LOGGER.name):
        result = asyncio.run(
            coordinator._fetch_optional("service_status", failing_callback, cache)
        )
    assert result == {"stale": "value"}
    assert calls == 1, "endpoint must not be retried while backing off"
    assert len(caplog.records) == 0, "no warning should repeat while backing off"

    # Advance past the backoff window: the endpoint is retried and, since it
    # still fails, no *new* warning is logged (already in a failing streak).
    clock.now += 1800 * 2 + 1
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=coordinator_module._LOGGER.name):
        result = asyncio.run(
            coordinator._fetch_optional("service_status", failing_callback, cache)
        )
    assert calls == 2
    assert len(caplog.records) == 0


def test_recovery_after_failures_is_logged_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    coordinator_module = _coordinator_module(monkeypatch)
    coordinator = _make_coordinator(coordinator_module)

    clock = _FakeClock()
    monkeypatch.setattr(coordinator_module.time, "monotonic", lambda: clock.now)

    async def failing_callback():
        raise RuntimeError("boom")

    cache = {"balance": {"amount": 12.3}}

    asyncio.run(coordinator._fetch_optional("balance", failing_callback, cache))
    clock.now += 1800 * 2 + 1
    asyncio.run(coordinator._fetch_optional("balance", failing_callback, cache))

    async def recovered_callback():
        return {"amount": 99.0}

    clock.now += 1800 * 4 + 1
    with caplog.at_level(logging.INFO, logger=coordinator_module._LOGGER.name):
        result = asyncio.run(
            coordinator._fetch_optional("balance", recovered_callback, cache)
        )
    assert result == {"amount": 99.0}
    assert sum("recovered" in rec.message for rec in caplog.records) == 1

    state = coordinator._optional_fetch_state["balance"]
    assert state["consecutive_failures"] == 0


def test_backoff_state_is_isolated_per_service_via_state_key(
    monkeypatch: pytest.MonkeyPatch,
):
    """Per-service _fetch_optional() calls must key backoff state per service.

    Mirrors how _fetch_service_detail() calls _fetch_optional(): the same
    literal `key` ("usage") is reused across every service's cache dict, but
    a service-specific `state_key` must be passed so one service's failures
    can't suppress the warning/backoff for another service's endpoint, and
    each service's cache fallback (via `key`) stays independent because each
    service already has its own `cache` dict.
    """
    coordinator_module = _coordinator_module(monkeypatch)
    coordinator = _make_coordinator(coordinator_module)

    clock = _FakeClock()
    monkeypatch.setattr(coordinator_module.time, "monotonic", lambda: clock.now)

    async def failing_callback():
        raise RuntimeError("boom")

    async def ok_callback():
        return {"ok": True}

    cache_a = {"usage": {"cached": "a"}}
    cache_b = {"usage": {"cached": "b"}}

    asyncio.run(
        coordinator._fetch_optional(
            "usage", failing_callback, cache_a, state_key="usage:service-a"
        )
    )
    # Service B's key must be unaffected by service A's failure, and its own
    # cache fallback (keyed by the shared "usage" key in its own dict) must
    # remain reachable.
    result_b = asyncio.run(
        coordinator._fetch_optional(
            "usage", ok_callback, cache_b, state_key="usage:service-b"
        )
    )
    assert result_b == {"ok": True}
    assert coordinator._optional_fetch_state["usage:service-b"]["consecutive_failures"] == 0
    assert coordinator._optional_fetch_state["usage:service-a"]["consecutive_failures"] == 1

    # Service A's own cache fallback is still reachable under the shared key.
    result_a_fallback = asyncio.run(
        coordinator._fetch_optional(
            "usage", failing_callback, cache_a, state_key="usage:service-a"
        )
    )
    assert result_a_fallback == {"cached": "a"}
