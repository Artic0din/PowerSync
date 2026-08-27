"""Regression tests for issue #24: ownership-blocked Auto Schedule starts.

AutoScheduleExecutor._start_charging() must not report an ownership rejection
(another EV mode already owns the loadpoint) as a generic start failure -- no
warning log, no start-failure/backoff bookkeeping. Genuine charger/API
failures must still be reported as before.
"""

from __future__ import annotations

import asyncio
import importlib
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


ROOT = Path(__file__).resolve().parent.parent / "custom_components" / "power_sync"

sys.modules.setdefault("aiohttp", types.ModuleType("aiohttp"))

_ha_root = sys.modules.setdefault("homeassistant", types.ModuleType("homeassistant"))
_ha_config_entries = sys.modules.setdefault(
    "homeassistant.config_entries", types.ModuleType("homeassistant.config_entries")
)
_ha_core = sys.modules.setdefault("homeassistant.core", types.ModuleType("homeassistant.core"))
_ha_exceptions = sys.modules.setdefault(
    "homeassistant.exceptions", types.ModuleType("homeassistant.exceptions")
)
_ha_helpers = sys.modules.setdefault("homeassistant.helpers", types.ModuleType("homeassistant.helpers"))
_ha_storage = sys.modules.setdefault(
    "homeassistant.helpers.storage", types.ModuleType("homeassistant.helpers.storage")
)
_ha_update = sys.modules.setdefault(
    "homeassistant.helpers.update_coordinator",
    types.ModuleType("homeassistant.helpers.update_coordinator"),
)
_ha_er = sys.modules.setdefault(
    "homeassistant.helpers.entity_registry",
    types.ModuleType("homeassistant.helpers.entity_registry"),
)
_ha_dr = sys.modules.setdefault(
    "homeassistant.helpers.device_registry",
    types.ModuleType("homeassistant.helpers.device_registry"),
)
_ha_event = sys.modules.setdefault(
    "homeassistant.helpers.event", types.ModuleType("homeassistant.helpers.event")
)
_ha_aiohttp_client = sys.modules.setdefault(
    "homeassistant.helpers.aiohttp_client",
    types.ModuleType("homeassistant.helpers.aiohttp_client"),
)
_ha_util = sys.modules.setdefault("homeassistant.util", types.ModuleType("homeassistant.util"))
_ha_dt = sys.modules.setdefault("homeassistant.util.dt", types.ModuleType("homeassistant.util.dt"))
_ha_core.HomeAssistant = type("HomeAssistant", (), {})
_ha_config_entries.ConfigEntry = type("ConfigEntry", (), {})
_ha_exceptions.ConfigEntryNotReady = type("ConfigEntryNotReady", (Exception,), {})
_ha_er.async_get = lambda hass: getattr(hass, "entity_registry", SimpleNamespace(entities={}))
_ha_dr.async_get = lambda hass: SimpleNamespace(devices={})
_ha_storage.Store = type("Store", (), {"__init__": lambda self, *args, **kwargs: None})
_ha_update.DataUpdateCoordinator = type(
    "DataUpdateCoordinator",
    (),
    {
        "__class_getitem__": classmethod(lambda cls, item: cls),
        "__init__": lambda self, *args, **kwargs: None,
    },
)
_ha_event.async_track_time_interval = lambda *args, **kwargs: (lambda: None)
_ha_event.async_track_time_change = lambda *args, **kwargs: (lambda: None)
_ha_event.async_track_point_in_time = lambda *args, **kwargs: (lambda: None)
_ha_dt.now = getattr(_ha_dt, "now", lambda *args, **kwargs: None)
_ha_dt.utcnow = getattr(_ha_dt, "utcnow", lambda *args, **kwargs: None)
_ha_helpers.entity_registry = _ha_er
_ha_helpers.device_registry = _ha_dr
_ha_helpers.storage = _ha_storage
_ha_helpers.update_coordinator = _ha_update
_ha_helpers.event = _ha_event
_ha_helpers.aiohttp_client = _ha_aiohttp_client
_ha_root.helpers = _ha_helpers
_ha_util.dt = _ha_dt
_ha_root.util = _ha_util

_ps = types.ModuleType("power_sync")
_ps.__path__ = [str(ROOT)]
sys.modules["power_sync"] = _ps

_optimization = types.ModuleType("power_sync.optimization")
_optimization.__path__ = [str(ROOT / "optimization")]
sys.modules["power_sync.optimization"] = _optimization

_automations = types.ModuleType("power_sync.automations")
_automations.__path__ = [str(ROOT / "automations")]
sys.modules["power_sync.automations"] = _automations

if not hasattr(sys.modules.get("power_sync.const"), "TESLA_INTEGRATIONS"):
    sys.modules.pop("power_sync.const", None)

ev_planner = importlib.import_module("power_sync.automations.ev_charging_planner")
ev_ownership = importlib.import_module("power_sync.automations.ev_ownership")


class _FakeConfigEntry:
    entry_id = "entry-1"
    data = {}
    options = {
        "sigenergy_charger_enabled": True,
        "sigenergy_charger_host": "192.0.2.30",
        "sigenergy_charger_port": 502,
        "sigenergy_charger_slave_id": 1,
        "sigenergy_charger_type": "evac",
    }


class _FakeStates:
    def get(self, entity_id: str):
        return None

    def async_all(self):
        return []

    def async_entity_ids(self, domain: str):
        return []


class _FakeHass:
    def __init__(self) -> None:
        self.data = {
            "power_sync": {
                "entry-1": {
                    "automation_store": SimpleNamespace(_data={}),
                }
            }
        }
        self.entity_registry = SimpleNamespace(entities={})
        self.device_registry = SimpleNamespace(devices={})
        self.states = _FakeStates()
        self.config_entries = SimpleNamespace(async_entries=lambda domain=None: [])
        self.services = SimpleNamespace(async_call=AsyncMock())


@pytest.fixture
def fake_actions(monkeypatch):
    actions = types.ModuleType("power_sync.automations.actions")
    actions.DEFAULT_VEHICLE_ID = "_default"
    actions._dynamic_ev_state = {}

    async def resolve_max_grid_import_kw(hass, config_entry, params=None):
        return None

    actions._resolve_max_grid_import_kw = resolve_max_grid_import_kw
    monkeypatch.setitem(sys.modules, "power_sync.automations.actions", actions)
    return actions


def _executor_and_settings():
    executor = ev_planner.AutoScheduleExecutor(
        _FakeHass(),
        _FakeConfigEntry(),
        planner=SimpleNamespace(),
    )
    settings = ev_planner.AutoScheduleSettings(
        vehicle_id="sigenergy_charger",
        display_name="Sigenergy EVAC",
        charger_type="tesla",
        max_charge_amps=30,
    )
    state = ev_planner.AutoScheduleState(vehicle_id="sigenergy_charger")
    return executor, settings, state


def test_ownership_conflict_start_is_skipped_without_warning_or_backoff(
    monkeypatch, fake_actions, caplog
):
    async def blocked_by_price_level_recovery(hass, config_entry, params, context=None):
        ev_ownership.record_ev_command(
            hass,
            config_entry,
            params.get("vehicle_vin") or params.get("vehicle_id"),
            command="start_smart_schedule",
            success=False,
            reason="price_level_recovery already owns this loadpoint",
        )
        return False

    fake_actions._action_start_ev_charging_dynamic = AsyncMock(
        side_effect=blocked_by_price_level_recovery
    )
    monkeypatch.setattr(
        ev_planner.dt_util,
        "now",
        lambda: SimpleNamespace(weekday=lambda: 0),
    )

    executor, settings, state = _executor_and_settings()

    with caplog.at_level("INFO"):
        result = asyncio.run(
            executor._start_charging(
                "sigenergy_charger", settings, state, "grid_opportunistic"
            )
        )

    assert result is False
    assert executor._start_failure_state == {}
    assert not any(
        "Failed to start charging" in record.message for record in caplog.records
    )
    assert any(
        "Start skipped" in record.message
        and "price_level_recovery already owns this loadpoint" in record.message
        for record in caplog.records
    )


def test_race_ownership_between_evaluation_and_locked_start_is_also_skipped(
    monkeypatch, fake_actions, caplog
):
    """Ownership can be acquired by another mode after Auto Schedule evaluates
    but before the locked start call runs; the locked call is the source of
    truth and its rejection must be classified the same way."""

    async def race_lost_to_takeover(hass, config_entry, params, context=None):
        # No ownership existed when Auto Schedule decided to start, but the
        # locked call itself is where the real arbitration happens.
        ev_ownership.record_ev_command(
            hass,
            config_entry,
            params.get("vehicle_vin") or params.get("vehicle_id"),
            command="start_smart_schedule",
            success=False,
            reason="scheduled already owns this loadpoint",
        )
        return False

    fake_actions._action_start_ev_charging_dynamic = AsyncMock(
        side_effect=race_lost_to_takeover
    )
    monkeypatch.setattr(
        ev_planner.dt_util,
        "now",
        lambda: SimpleNamespace(weekday=lambda: 0),
    )

    executor, settings, state = _executor_and_settings()

    with caplog.at_level("INFO"):
        result = asyncio.run(
            executor._start_charging(
                "sigenergy_charger", settings, state, "grid_opportunistic"
            )
        )

    assert result is False
    assert executor._start_failure_state == {}
    assert not any(
        "Failed to start charging" in record.message for record in caplog.records
    )


def test_genuine_start_failure_still_warns_and_records_backoff(
    monkeypatch, fake_actions, caplog
):
    async def charger_did_not_ack(hass, config_entry, params, context=None):
        ev_ownership.record_ev_command(
            hass,
            config_entry,
            params.get("vehicle_vin") or params.get("vehicle_id"),
            command="start_smart_schedule",
            success=False,
            reason="charger did not acknowledge the start command",
        )
        return False

    fake_actions._action_start_ev_charging_dynamic = AsyncMock(
        side_effect=charger_did_not_ack
    )
    monkeypatch.setattr(
        ev_planner.dt_util,
        "now",
        lambda: SimpleNamespace(weekday=lambda: 0),
    )

    executor, settings, state = _executor_and_settings()

    with caplog.at_level("WARNING"):
        result = asyncio.run(
            executor._start_charging(
                "sigenergy_charger", settings, state, "grid_opportunistic"
            )
        )

    assert result is False
    assert "sigenergy_charger" in executor._start_failure_state
    assert any(
        "Failed to start charging" in record.message for record in caplog.records
    )


def test_start_result_ownership_conflict_helper_classifies_reasons():
    assert ev_planner._ev_start_failure_is_ownership_conflict(
        {"success": False, "reason": "price_level_recovery already owns this loadpoint"}
    )
    assert not ev_planner._ev_start_failure_is_ownership_conflict(
        {"success": False, "reason": "charger did not acknowledge the start command"}
    )
    assert not ev_planner._ev_start_failure_is_ownership_conflict(
        {"success": True, "reason": "price_level_recovery already owns this loadpoint"}
    )
    assert not ev_planner._ev_start_failure_is_ownership_conflict(None)
