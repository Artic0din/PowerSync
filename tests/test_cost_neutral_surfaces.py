"""Source-level contract checks for Cost Neutral settings surfaces."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "power_sync"


def test_cost_neutral_is_exposed_on_config_api_metadata_and_switch_surfaces():
    const_source = (COMPONENT / "const.py").read_text()
    coordinator_source = (COMPONENT / "optimization" / "coordinator.py").read_text()
    init_source = (COMPONENT / "__init__.py").read_text()
    config_flow_source = (COMPONENT / "config_flow.py").read_text()
    metadata_source = (COMPONENT / "settings_metadata.py").read_text()
    switch_source = (COMPONENT / "switch.py").read_text()

    assert 'CONF_COST_NEUTRAL_ENABLED = "cost_neutral_enabled"' in const_source
    assert "cost_neutral_enabled: bool = False" in coordinator_source
    assert "def set_cost_neutral_enabled" in coordinator_source
    assert '"cost_neutral": dict(' in coordinator_source
    assert '"days": day_status' in coordinator_source
    assert '"cost_neutral_earnings_caps_by_day"' in coordinator_source
    assert '"cost_neutral_planned_earnings_by_day"' in coordinator_source
    assert '"cost_neutral_enabled": opt_coordinator.cost_neutral_enabled' in init_source
    assert "Profit Max and Cost Neutral cannot both be enabled" in init_source
    assert config_flow_source.count("CONF_COST_NEUTRAL_ENABLED") >= 10
    assert '"exclusive_with": ["profit_max_enabled"]' in metadata_source
    assert "class CostNeutralSwitch" in switch_source
    assert '"switch_add_cost_neutral"' in switch_source
    assert '"switch_add_cost_neutral"' in init_source


def test_cost_neutral_english_strings_exist_on_all_optimizer_forms():
    strings = json.loads((COMPONENT / "strings.json").read_text())
    translations = json.loads((COMPONENT / "translations" / "en.json").read_text())

    for payload in (strings, translations):
        initial = payload["config"]["step"]["ml_options"]
        options = payload["options"]["step"]["optimization"]
        assert initial["data"]["cost_neutral_enabled"] == "Enable Cost Neutral"
        assert "natural solar export is not capped" in initial["data_description"][
            "cost_neutral_enabled"
        ].lower()
        assert options["sections"]["core_goals"]["data"][
            "cost_neutral_enabled"
        ] == "Enable Cost Neutral"
        assert options["sections"]["core_goals"]["data"][
            "daily_supply_charge"
        ] == "Daily supply charge"
        assert "cost neutral target" in options["sections"]["core_goals"][
            "data_description"
        ]["daily_supply_charge"].lower()


def test_supply_charge_is_owned_by_optimizer_not_demand_charge_form():
    config_flow_source = (COMPONENT / "config_flow.py").read_text()
    optimizer_start = config_flow_source.index("async def _async_step_optimization")
    optimizer_end = config_flow_source.index(
        "async def async_step_inverter", optimizer_start
    )
    optimizer_source = config_flow_source[optimizer_start:optimizer_end]
    demand_start = config_flow_source.index(
        "async def async_step_demand_charge_options"
    )
    demand_end = config_flow_source.index(
        "async def async_step_curtailment_options", demand_start
    )
    demand_source = config_flow_source[demand_start:demand_end]

    assert "CONF_DAILY_SUPPLY_CHARGE" in optimizer_source
    assert '"daily_supply_charge": daily_supply_charge' in optimizer_source
    schema_source = optimizer_source[
        optimizer_source.index("schema_fields.update("):
    ]
    assert schema_source.index("CONF_COST_NEUTRAL_ENABLED") < schema_source.index(
        "CONF_DAILY_SUPPLY_CHARGE"
    ) < schema_source.index("CONF_CHARGE_BY_TIME_ENABLED")
    assert "CONF_DAILY_SUPPLY_CHARGE" not in demand_source
    assert "CONF_MONTHLY_SUPPLY_CHARGE" not in demand_source
