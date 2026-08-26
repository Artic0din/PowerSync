"""Regression test for issue #60: the shared `tests/_ha_stub.py` scaffold must
keep stubbing the exact `homeassistant.*` symbols `coordinator.py` (and
friends) import, so it can't silently drift out of sync again the way the
~20 hand-rolled per-file copies did.

Each symbol asserted here maps to a concrete import in
`custom_components/power_sync/coordinator.py` (and shared by
`globird_coordinator.py`, `powerwall_local/coordinator.py`, `update.py`,
`__init__.py`, `button.py`, `config_flow.py`, `powerwall_local/views.py`):

- `ConfigEntryAuthFailed` -- coordinator.py:17, raised at :1146, caught at :2852
- `UpdateFailed`          -- coordinator.py:18, also imported by
                             globird_coordinator.py, powerwall_local/coordinator.py,
                             update.py
- `async_get_clientsession` -- coordinator.py:19, __init__.py, button.py,
                               config_flow.py, powerwall_local/views.py, update.py
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone

import pytest

from _ha_stub import HA_STUB_MODULE_NAMES, install_ha_stubs, install_power_sync_coordinator_stub

_SENTINEL = object()


@pytest.fixture()
def ha_stubs():
    saved = {name: sys.modules.get(name, _SENTINEL) for name in HA_STUB_MODULE_NAMES}
    for name in HA_STUB_MODULE_NAMES:
        sys.modules.pop(name, None)

    install_ha_stubs()
    try:
        yield
    finally:
        for name in HA_STUB_MODULE_NAMES:
            if saved[name] is _SENTINEL:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = saved[name]


def test_ha_stub_module_names_include_aiohttp_client():
    """The whole `homeassistant.helpers.aiohttp_client` module was missing
    from every affected file's `_STUB_MODULE_NAMES` -- confirm it's present now."""
    assert "homeassistant.helpers.aiohttp_client" in HA_STUB_MODULE_NAMES


def test_config_entry_auth_failed_is_stubbed(ha_stubs):
    from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

    assert issubclass(ConfigEntryAuthFailed, Exception)
    # Distinct from ConfigEntryNotReady -- callers may catch one but not the other.
    assert ConfigEntryAuthFailed is not ConfigEntryNotReady


def test_update_failed_is_stubbed(ha_stubs):
    from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

    assert issubclass(UpdateFailed, Exception)
    with pytest.raises(UpdateFailed):
        raise UpdateFailed("boom")
    assert DataUpdateCoordinator is not None


def test_async_get_clientsession_is_stubbed(ha_stubs):
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    async def _run():
        return await async_get_clientsession(object())

    assert asyncio.run(_run()) is None


def test_install_ha_stubs_honors_custom_now(ha_stubs):
    fixed_now = datetime(2026, 7, 8, 8, 30, tzinfo=timezone.utc)
    install_ha_stubs(now=fixed_now)
    from homeassistant.util import dt as dt_util

    assert dt_util.now() == fixed_now
    assert dt_util.utcnow() == fixed_now


def test_power_sync_coordinator_stub_matches_real_normalize_custom_power_kw(ha_stubs):
    """`power_sync.optimization.coordinator` only needs
    `normalize_custom_power_kw` from the real `power_sync/coordinator.py` --
    the shared fake must behave identically for representative inputs."""
    saved = sys.modules.get("power_sync.coordinator", _SENTINEL)
    try:
        install_power_sync_coordinator_stub()
        coordinator_stub = sys.modules["power_sync.coordinator"]

        assert coordinator_stub.normalize_custom_power_kw(1500, "w") == 1.5
        assert coordinator_stub.normalize_custom_power_kw(1.5, "kw") == 1.5
        assert coordinator_stub.normalize_custom_power_kw(0.0015, "mw") == 1.5
        assert coordinator_stub.normalize_custom_power_kw(None) is None
        assert coordinator_stub.normalize_custom_power_kw("not-a-number") is None
        assert coordinator_stub.normalize_custom_power_kw(float("inf")) is None
        # No explicit unit: values with |v| > 100 are assumed watts.
        assert coordinator_stub.normalize_custom_power_kw(1500) == 1.5
        assert coordinator_stub.normalize_custom_power_kw(1.5) == 1.5
    finally:
        if saved is _SENTINEL:
            sys.modules.pop("power_sync.coordinator", None)
        else:
            sys.modules["power_sync.coordinator"] = saved
