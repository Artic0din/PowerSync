"""Regression tests for automation state coordinator discovery."""

from __future__ import annotations

import ast
import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict


ROOT = Path(__file__).resolve().parent.parent
AUTOMATIONS_PATH = ROOT / "custom_components" / "power_sync" / "automations" / "__init__.py"


def _load_engine_method(name: str, namespace: dict[str, Any]):
    tree = ast.parse(AUTOMATIONS_PATH.read_text())
    engine = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "AutomationEngine"
    )
    method = next(
        node
        for node in engine.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    )
    extracted = ast.ClassDef(
        name="_ExtractedEngine",
        bases=[],
        keywords=[],
        body=[method],
        decorator_list=[],
    )
    module = ast.fix_missing_locations(ast.Module(body=[extracted], type_ignores=[]))
    exec(compile(module, str(AUTOMATIONS_PATH), "exec"), namespace)
    return namespace["_ExtractedEngine"]


def test_automation_current_state_includes_supported_battery_coordinators():
    tree = ast.parse(AUTOMATIONS_PATH.read_text())
    method = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "_async_get_current_state"
    )
    source = ast.get_source_segment(AUTOMATIONS_PATH.read_text(), method)

    for coordinator_key in (
        "tesla_coordinator",
        "sigenergy_coordinator",
        "sungrow_coordinator",
        "foxess_coordinator",
        "goodwe_coordinator",
        "alphaess_coordinator",
        "esy_sunhome_coordinator",
        "solax_coordinator",
        "esy_coordinator",
        "saj_h2_coordinator",
        "fronius_reserva_coordinator",
        "neovolt_coordinator",
        "solaredge_coordinator",
        "anker_solix_coordinator",
        "custom_energy_coordinator",
    ):
        assert coordinator_key in source


def test_automation_time_defaults_to_home_assistant_timezone_before_sydney():
    """Custom-tariff sites without NEM metadata must not fire on Sydney time."""
    tree = ast.parse(AUTOMATIONS_PATH.read_text())
    method = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "_async_get_current_state"
    )
    source = ast.get_source_segment(AUTOMATIONS_PATH.read_text(), method)

    assert 'self._config_entry.options.get("timezone")' in source
    assert 'self._config_entry.data.get("timezone")' in source
    assert 'getattr(getattr(self._hass, "config", None), "time_zone", None)' in source
    assert 'configured_timezone or ha_timezone or "Australia/Sydney"' in source


def test_automation_stop_context_preserves_triggered_ble_vehicle():
    calls = []

    async def execute_actions(hass, config_entry, actions, context):
        calls.append((hass, config_entry, actions, context))
        return True

    engine_class = _load_engine_method(
        "_async_execute_automation",
        {
            "Any": Any,
            "Dict": Dict,
            "_LOGGER": logging.getLogger(__name__),
            "execute_actions": execute_actions,
        },
    )
    engine = object.__new__(engine_class)
    engine._hass = object()
    engine._config_entry = object()

    result = asyncio.run(
        engine._async_execute_automation(
            {
                "name": "Stop BLE Tesla",
                "trigger": {
                    "trigger_type": "ev",
                    "time_window_start": "16:00",
                    "time_window_end": "09:00",
                },
                "actions": [
                    {
                        "action_type": "stop_ev_charging",
                        "parameters": {},
                    }
                ],
            },
            set(),
            {
                "user_timezone": "Australia/Brisbane",
                "ev_state": {"vehicle_id": "ble_tesla_yf88"},
            },
        )
    )

    assert result is True
    assert calls[0][3]["ev_vehicle_id"] == "ble_tesla_yf88"


def test_automation_ev_state_recognizes_user_named_ble_prefix():
    class _States:
        def __init__(self):
            self._states = {
                "sensor.tesla_yf88_charging_state": SimpleNamespace(
                    entity_id="sensor.tesla_yf88_charging_state",
                    state="Charging",
                ),
                "binary_sensor.tesla_yf88_charge_flap": SimpleNamespace(
                    entity_id="binary_sensor.tesla_yf88_charge_flap",
                    state="on",
                ),
                "binary_sensor.tesla_yf88_ble_status": SimpleNamespace(
                    entity_id="binary_sensor.tesla_yf88_ble_status",
                    state="on",
                ),
                "sensor.tesla_yf88_charge_level": SimpleNamespace(
                    entity_id="sensor.tesla_yf88_charge_level",
                    state="50",
                ),
            }

        def async_all(self):
            return list(self._states.values())

        def get(self, entity_id):
            return self._states.get(entity_id)

    engine_class = _load_engine_method(
        "_async_get_ev_state",
        {
            "Any": Any,
            "Dict": Dict,
            "_LOGGER": logging.getLogger(__name__),
            "__package__": "power_sync.automations",
        },
    )
    engine = object.__new__(engine_class)
    engine._hass = SimpleNamespace(
        states=_States(),
        config_entries=SimpleNamespace(async_entries=lambda _domain: []),
        data={},
    )

    state = asyncio.run(engine._async_get_ev_state())

    assert state["is_charging"] is True
    assert state["is_plugged_in"] is True
    assert state["location"] == "home"
    assert state["vehicle_id"] == "ble_tesla_yf88"


def test_automation_ev_state_prefers_active_second_ble_vehicle():
    """A sleeping first bridge must not hide another BLE Tesla charging."""

    class _States:
        def __init__(self):
            self._states = {
                "sensor.tesla_yf88_charging_state": SimpleNamespace(
                    entity_id="sensor.tesla_yf88_charging_state",
                    state="Unknown",
                ),
                "binary_sensor.tesla_yf88_ble_status": SimpleNamespace(
                    entity_id="binary_sensor.tesla_yf88_ble_status",
                    state="off",
                ),
                "sensor.tesla_flinn_charging_state": SimpleNamespace(
                    entity_id="sensor.tesla_flinn_charging_state",
                    state="Charging",
                ),
                "binary_sensor.tesla_flinn_charge_flap": SimpleNamespace(
                    entity_id="binary_sensor.tesla_flinn_charge_flap",
                    state="on",
                ),
                "binary_sensor.tesla_flinn_ble_status": SimpleNamespace(
                    entity_id="binary_sensor.tesla_flinn_ble_status",
                    state="on",
                ),
                "sensor.tesla_flinn_charge_level": SimpleNamespace(
                    entity_id="sensor.tesla_flinn_charge_level",
                    state="46",
                ),
            }

        def async_all(self):
            return list(self._states.values())

        def get(self, entity_id):
            return self._states.get(entity_id)

    engine_class = _load_engine_method(
        "_async_get_ev_state",
        {
            "Any": Any,
            "Dict": Dict,
            "_LOGGER": logging.getLogger(__name__),
            "__package__": "power_sync.automations",
        },
    )
    engine = object.__new__(engine_class)
    engine._hass = SimpleNamespace(
        states=_States(),
        config_entries=SimpleNamespace(async_entries=lambda _domain: []),
        data={},
    )

    state = asyncio.run(engine._async_get_ev_state())

    assert state["is_charging"] is True
    assert state["is_plugged_in"] is True
    assert state["battery_level"] == 46
    assert state["vehicle_id"] == "ble_tesla_flinn"


def test_automation_ev_state_prefers_targetable_ble_when_activity_ties():
    """A Fleet duplicate must not hide the BLE id needed by the stop action."""

    class _States:
        def __init__(self):
            self._states = {
                "sensor.tesla_fleet_charging": SimpleNamespace(
                    entity_id="sensor.tesla_fleet_charging",
                    state="Charging",
                ),
                "sensor.tesla_flinn_charging_state": SimpleNamespace(
                    entity_id="sensor.tesla_flinn_charging_state",
                    state="Charging",
                ),
                "binary_sensor.tesla_flinn_charge_flap": SimpleNamespace(
                    entity_id="binary_sensor.tesla_flinn_charge_flap",
                    state="on",
                ),
                "binary_sensor.tesla_flinn_ble_status": SimpleNamespace(
                    entity_id="binary_sensor.tesla_flinn_ble_status",
                    state="on",
                ),
            }

        def async_all(self):
            return list(self._states.values())

        def get(self, entity_id):
            return self._states.get(entity_id)

    engine_class = _load_engine_method(
        "_async_get_ev_state",
        {
            "Any": Any,
            "Dict": Dict,
            "_LOGGER": logging.getLogger(__name__),
            "__package__": "power_sync.automations",
        },
    )
    engine = object.__new__(engine_class)
    engine._hass = SimpleNamespace(
        states=_States(),
        config_entries=SimpleNamespace(async_entries=lambda _domain: []),
        data={},
    )

    state = asyncio.run(engine._async_get_ev_state())

    assert state["is_charging"] is True
    assert state["vehicle_id"] == "ble_tesla_flinn"
