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
