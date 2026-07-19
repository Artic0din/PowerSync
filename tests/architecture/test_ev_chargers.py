"""Contract tests for ChargerAdapter stubs."""

from __future__ import annotations

from custom_components.power_sync.ev.chargers import (
    ChargerAdapter,
    GenericSwitchChargerAdapter,
    OCPPChargerAdapter,
    TeslaFleetChargerAdapter,
    get_charger_adapter,
)
from custom_components.power_sync.ev.loadpoint import EVLoadpointState


def test_ocpp_adapter_normalizes_charging_status():
    adapter = OCPPChargerAdapter()
    assert isinstance(adapter, ChargerAdapter)
    state = adapter.read_state(
        loadpoint_id="lp-ocpp",
        raw={"status": "Charging", "power_w": 7400, "current_amps": 32, "soc": 55},
    )
    assert isinstance(state, EVLoadpointState)
    assert state.loadpoint_id == "lp-ocpp"
    assert state.charger_type == "ocpp"
    assert state.connected is True
    assert state.actual_charging is True
    assert state.power_kw == 7.4
    assert state.current_amps == 32
    assert state.soc == 55.0


def test_tesla_fleet_adapter_normalizes_charge_state():
    adapter = TeslaFleetChargerAdapter()
    state = adapter.read_state(
        loadpoint_id="lp-tesla",
        raw={
            "charging_state": "Charging",
            "vin": "VIN123",
            "charger_power": 11.0,
            "charge_current_request": 16,
            "battery_level": 42,
        },
    )
    assert state.charger_type == "tesla_fleet"
    assert state.vehicle_id == "VIN123"
    assert state.actual_charging is True
    assert state.power_kw == 11.0
    assert state.target_amps == 16
    assert state.soc == 42.0


def test_generic_switch_adapter_uses_switch_on():
    adapter = GenericSwitchChargerAdapter()
    state = adapter.read_state(
        loadpoint_id="lp-gen",
        raw={"switch_on": True, "power_kw": 3.6, "amps": 8},
    )
    assert state.charger_type == "generic_switch"
    assert state.connected is True
    assert state.actual_charging is True
    assert state.power_kw == 3.6
    assert state.current_amps == 8


def test_get_charger_adapter_aliases():
    assert get_charger_adapter("ocpp").charger_type == "ocpp"
    assert get_charger_adapter("teslemetry").charger_type == "tesla_fleet"
    assert get_charger_adapter("unknown").charger_type == "generic_switch"
