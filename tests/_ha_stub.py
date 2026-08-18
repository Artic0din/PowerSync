"""Shared ``homeassistant.*`` stub scaffold for PowerSync unit tests.

The real ``homeassistant`` package is not installed in this development
environment, so any test that needs to import ``custom_components/power_sync``
modules (directly or transitively, e.g. via
``power_sync.optimization.coordinator``'s ``from ..coordinator import ...``)
has to fake out enough of ``homeassistant.*`` in :data:`sys.modules` first.

Historically each test file hand-rolled its own copy of this scaffold. Those
copies drifted out of sync with what ``custom_components/power_sync/coordinator.py``
(and friends) actually import, which caused ``ImportError`` at collection
time in several files once ``coordinator.py`` started importing
``ConfigEntryAuthFailed``, ``UpdateFailed``, and
``homeassistant.helpers.aiohttp_client`` (see issue #60).

This module centralizes the ``homeassistant.*`` half of that scaffold so it
only needs to be kept in sync with ``coordinator.py``'s import surface in one
place. Each test file still owns its own ``power_sync.*`` stubs (const
values, fake optimization submodules, etc.) since those genuinely vary
per file — only the common ``homeassistant.*`` surface is shared here.

Usage (mirrors the previous per-file ``_install_ha_stubs`` contract)::

    from _ha_stub import HA_STUB_MODULE_NAMES, install_ha_stubs

    _STUB_MODULE_NAMES = HA_STUB_MODULE_NAMES + (
        "power_sync",
        "power_sync.const",
        ...
    )

    def _install_ha_stubs() -> None:
        install_ha_stubs()

A fixed/custom "now" can be supplied for files that pin their fixtures to a
specific timestamp::

    install_ha_stubs(now=_NOW)
"""

from __future__ import annotations

import math
import sys
import types
from datetime import datetime, timezone

__all__ = [
    "HA_STUB_MODULE_NAMES",
    "install_ha_stubs",
    "install_power_sync_coordinator_stub",
]

# The complete set of `homeassistant.*` module names this scaffold installs.
# Test files should extend this tuple with their own `power_sync.*` (and any
# other) module names when building their save/restore `_STUB_MODULE_NAMES`.
HA_STUB_MODULE_NAMES = (
    "homeassistant",
    "homeassistant.core",
    "homeassistant.exceptions",
    "homeassistant.helpers",
    "homeassistant.helpers.aiohttp_client",
    "homeassistant.helpers.dispatcher",
    "homeassistant.helpers.event",
    "homeassistant.helpers.storage",
    "homeassistant.helpers.update_coordinator",
    "homeassistant.util",
    "homeassistant.util.dt",
)

_DEFAULT_NOW = datetime(2026, 5, 3, 8, 30, tzinfo=timezone.utc)


def install_ha_stubs(now: datetime | None = None) -> None:
    """Install a minimal fake ``homeassistant`` package tree into ``sys.modules``.

    ``now`` fixes what ``homeassistant.util.dt.now()``/``.utcnow()`` return
    (defaults to a stable 2026-05-03 08:30 UTC timestamp). Callers that need
    per-test control can still monkeypatch ``dt_util.now`` on the imported
    module afterwards, same as before centralization.
    """
    now_value = now if now is not None else _DEFAULT_NOW

    ha_root = types.ModuleType("homeassistant")
    ha_core = types.ModuleType("homeassistant.core")
    ha_exceptions = types.ModuleType("homeassistant.exceptions")
    ha_helpers = types.ModuleType("homeassistant.helpers")
    ha_aiohttp_client = types.ModuleType("homeassistant.helpers.aiohttp_client")
    ha_dispatcher = types.ModuleType("homeassistant.helpers.dispatcher")
    ha_event = types.ModuleType("homeassistant.helpers.event")
    ha_storage = types.ModuleType("homeassistant.helpers.storage")
    ha_update = types.ModuleType("homeassistant.helpers.update_coordinator")
    ha_util = types.ModuleType("homeassistant.util")
    ha_dt = types.ModuleType("homeassistant.util.dt")

    class _Store:
        def __init__(self, *args, **kwargs) -> None:
            pass

    class _DataUpdateCoordinator:
        def __class_getitem__(cls, item):
            return cls

        def __init__(self, hass, logger, name=None, update_interval=None) -> None:
            self.hass = hass
            self.logger = logger
            self.name = name
            self.update_interval = update_interval
            self.data = None

    class _UpdateFailed(Exception):
        """Stub for homeassistant.helpers.update_coordinator.UpdateFailed."""

    async def _async_get_clientsession(hass, *args, **kwargs):
        return getattr(hass, "client_session", None)

    ha_core.HomeAssistant = type("HomeAssistant", (), {})
    ha_exceptions.ConfigEntryNotReady = type("ConfigEntryNotReady", (Exception,), {})
    ha_exceptions.ConfigEntryAuthFailed = type("ConfigEntryAuthFailed", (Exception,), {})
    ha_storage.Store = _Store
    ha_update.DataUpdateCoordinator = _DataUpdateCoordinator
    ha_update.UpdateFailed = _UpdateFailed
    ha_aiohttp_client.async_get_clientsession = _async_get_clientsession
    ha_dispatcher.async_dispatcher_send = lambda *args, **kwargs: None
    ha_event.async_track_point_in_utc_time = (
        lambda hass, callback, when: getattr(hass, "scheduled", []).append((callback, when)) or (lambda: None)
    )
    ha_dt.now = lambda *args, **kwargs: now_value
    ha_dt.utcnow = lambda *args, **kwargs: now_value
    ha_dt.as_local = lambda value: value.astimezone(timezone.utc)
    ha_dt.UTC = timezone.utc
    ha_helpers.storage = ha_storage
    ha_helpers.dispatcher = ha_dispatcher
    ha_helpers.event = ha_event
    ha_helpers.update_coordinator = ha_update
    ha_helpers.aiohttp_client = ha_aiohttp_client
    ha_util.dt = ha_dt
    ha_root.helpers = ha_helpers
    ha_root.util = ha_util

    sys.modules["homeassistant"] = ha_root
    sys.modules["homeassistant.core"] = ha_core
    sys.modules["homeassistant.exceptions"] = ha_exceptions
    sys.modules["homeassistant.helpers"] = ha_helpers
    sys.modules["homeassistant.helpers.aiohttp_client"] = ha_aiohttp_client
    sys.modules["homeassistant.helpers.dispatcher"] = ha_dispatcher
    sys.modules["homeassistant.helpers.event"] = ha_event
    sys.modules["homeassistant.helpers.storage"] = ha_storage
    sys.modules["homeassistant.helpers.update_coordinator"] = ha_update
    sys.modules["homeassistant.util"] = ha_util
    sys.modules["homeassistant.util.dt"] = ha_dt


def _normalize_custom_power_kw(value, unit: str = ""):
    """Faithful reimplementation of

    ``custom_components/power_sync/coordinator.py``'s
    ``normalize_custom_power_kw`` — kept in sync manually since it is a pure
    function with no further ``homeassistant``/``power_sync`` dependencies.
    """
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(normalized):
        return None
    normalized_unit = str(unit or "").strip().lower()
    if normalized_unit in {"w", "watt", "watts"}:
        return normalized / 1000.0
    if normalized_unit in {"mw", "megawatt", "megawatts"}:
        return normalized * 1000.0
    if normalized_unit in {"kw", "kilowatt", "kilowatts"}:
        return normalized
    return normalized / 1000.0 if abs(normalized) > 100 else normalized


def install_power_sync_coordinator_stub() -> None:
    """Install a fake ``power_sync.coordinator`` exposing only
    ``normalize_custom_power_kw``.

    ``power_sync/optimization/coordinator.py`` does
    ``from ..coordinator import normalize_custom_power_kw`` — the *only*
    symbol it needs from the real (very large) ``coordinator.py``. Stubbing
    just that symbol here, instead of importing the real module, avoids
    dragging in ``coordinator.py``'s own much larger ``homeassistant.*`` /
    ``power_sync.const`` import surface for tests that only exercise
    ``power_sync.optimization.coordinator``.

    Tests that specifically target the real ``coordinator.py`` (e.g.
    ``test_amber_price_cache.py``) must NOT call this — they need their own
    fuller stub so the real module imports.
    """
    coordinator_module = types.ModuleType("power_sync.coordinator")
    coordinator_module.normalize_custom_power_kw = _normalize_custom_power_kw
    sys.modules["power_sync.coordinator"] = coordinator_module
