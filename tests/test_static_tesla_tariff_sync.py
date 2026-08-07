"""Regression tests for manual static tariff uploads to Tesla."""

from __future__ import annotations

import copy
import ast
import importlib
import math
import sys
import types
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parent.parent
COMPONENT_ROOT = ROOT / "custom_components" / "power_sync"
_MISSING = object()


@pytest.fixture()
def tariff_converter_module():
    saved_modules = {
        name: sys.modules.get(name, _MISSING)
        for name in (
            "power_sync",
            "power_sync.tariff_converter",
            "power_sync.currency",
            "homeassistant",
            "homeassistant.util",
            "homeassistant.util.dt",
        )
    }

    power_sync = types.ModuleType("power_sync")
    power_sync.__path__ = [str(COMPONENT_ROOT)]
    sys.modules["power_sync"] = power_sync
    ha_root = types.ModuleType("homeassistant")
    ha_util = types.ModuleType("homeassistant.util")
    ha_dt = types.ModuleType("homeassistant.util.dt")
    ha_util.dt = ha_dt
    ha_root.util = ha_util
    sys.modules["homeassistant"] = ha_root
    sys.modules["homeassistant.util"] = ha_util
    sys.modules["homeassistant.util.dt"] = ha_dt

    try:
        yield importlib.import_module("power_sync.tariff_converter")
    finally:
        sys.modules.pop("power_sync.tariff_converter", None)
        for name, module in saved_modules.items():
            if module is _MISSING:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def _agl_tariff() -> dict:
    return {
        "version": 1,
        "code": "AGL_CUSTOM",
        "name": "AGL Battery Rewards",
        "utility": "AGL",
        "currency": "AUD",
        "seasons": {
            "All Year": {
                "fromMonth": 1,
                "toMonth": 12,
                "tou_periods": {
                    "OFF_PEAK": [
                        {
                            "fromDayOfWeek": 0,
                            "toDayOfWeek": 6,
                            "fromHour": 0,
                            "toHour": 17,
                        }
                    ],
                    "OFF_PEAK_AGL_REWARD": [
                        {
                            "fromDayOfWeek": 0,
                            "toDayOfWeek": 6,
                            "fromHour": 17,
                            "toHour": 21,
                        }
                    ],
                },
            }
        },
        "energy_charges": {
            "All Year": {
                "OFF_PEAK": 0.31,
                "OFF_PEAK_AGL_REWARD": 0.31,
            }
        },
        "sell_tariff": {
            "energy_charges": {
                "All Year": {
                    "OFF_PEAK": 0.03,
                    "OFF_PEAK_AGL_REWARD": 0.28,
                }
            }
        },
        "agl_base_tariff": {"should_not": "be uploaded"},
        "agl_battery_rewards": {"peak_rate": 0.28},
        "updated_at": "2026-08-07T00:00:00Z",
        "quota": {"limit": 1},
    }


def test_agl_static_tariff_has_a_usable_tesla_payload(tariff_converter_module):
    raw_tariff = _agl_tariff()
    before = copy.deepcopy(raw_tariff)

    payload = tariff_converter_module.convert_custom_tariff_to_tesla_tariff(raw_tariff)

    assert payload["code"] == "POWER_SYNC:STATIC_TOU"
    assert payload["energy_charges"]["All Year"]["rates"]["OFF_PEAK"] == 0.31
    assert payload["sell_tariff"]["energy_charges"]["All Year"]["rates"][
        "OFF_PEAK_AGL_REWARD"
    ] == 0.28
    assert raw_tariff == before


@pytest.mark.parametrize("raw_tariff", [{}, {"name": "broken"}])
def test_malformed_static_tariff_fails_closed(tariff_converter_module, raw_tariff):
    assert tariff_converter_module.convert_custom_tariff_to_tesla_tariff(raw_tariff) is None


@pytest.mark.parametrize(
    "mutation",
    [
        "nan_buy",
        "garbage_buy",
        "extra_season",
        "extra_tou_period",
        "extra_range_field",
        "malformed_range",
        "invalid_month",
        "invalid_day",
        "invalid_hour",
        "minute_pair",
        "missing_sell",
        "malformed_sell",
        "extra_sell_season",
        "inconsistent_periods",
    ],
)
def test_any_invalid_required_static_component_fails_closed(
    tariff_converter_module,
    mutation,
):
    raw_tariff = _agl_tariff()
    if mutation == "nan_buy":
        raw_tariff["energy_charges"]["All Year"]["OFF_PEAK"] = math.nan
    elif mutation == "garbage_buy":
        raw_tariff["energy_charges"]["All Year"]["OFF_PEAK"] = "not-a-rate"
    elif mutation == "extra_season":
        raw_tariff["seasons"]["Winter"] = raw_tariff["seasons"]["All Year"]
    elif mutation == "extra_tou_period":
        raw_tariff["seasons"]["All Year"]["tou_periods"]["BROKEN"] = []
    elif mutation == "extra_range_field":
        raw_tariff["seasons"]["All Year"]["tou_periods"]["OFF_PEAK"][0][
            "description"
        ] = "not part of Tesla TOU range"
    elif mutation == "malformed_range":
        raw_tariff["seasons"]["All Year"]["tou_periods"]["OFF_PEAK"][0][
            "fromHour"
        ] = 24
    elif mutation == "invalid_month":
        raw_tariff["seasons"]["All Year"]["fromMonth"] = 0
    elif mutation == "invalid_day":
        raw_tariff["seasons"]["All Year"]["tou_periods"]["OFF_PEAK"][0][
            "fromDayOfWeek"
        ] = 7
    elif mutation == "invalid_hour":
        raw_tariff["seasons"]["All Year"]["tou_periods"]["OFF_PEAK"][0][
            "toMinute"
        ] = 60
    elif mutation == "minute_pair":
        raw_tariff["seasons"]["All Year"]["tou_periods"]["OFF_PEAK"][0][
            "fromMinute"
        ] = 30
    elif mutation == "missing_sell":
        raw_tariff.pop("sell_tariff")
    elif mutation == "malformed_sell":
        raw_tariff["sell_tariff"]["energy_charges"]["All Year"][
            "OFF_PEAK_AGL_REWARD"
        ] = True
    elif mutation == "extra_sell_season":
        raw_tariff["sell_tariff"]["energy_charges"]["Winter"] = {"WINTER": 0.04}
    elif mutation == "inconsistent_periods":
        raw_tariff["sell_tariff"]["energy_charges"]["All Year"].pop(
            "OFF_PEAK_AGL_REWARD"
        )

    assert tariff_converter_module.convert_custom_tariff_to_tesla_tariff(raw_tariff) is None


def test_static_payload_uses_fixed_safe_envelope(tariff_converter_module):
    raw_tariff = _agl_tariff()
    raw_tariff.update(
        {
            "version": 99,
            "daily_charges": [{"name": "user charge", "value": "bad"}],
            "demand_charges": {"All Year": {"rates": {"PEAK": math.nan}}},
            "name": 123,
            "utility": {"bad": "utility"},
            "currency": 42,
        }
    )
    payload = tariff_converter_module.convert_custom_tariff_to_tesla_tariff(raw_tariff)

    assert payload["version"] == 1
    assert payload["daily_charges"] == [{"name": "Charge"}]
    assert payload["sell_tariff"]["daily_charges"] == [{"name": "Charge"}]
    assert payload["demand_charges"]["ALL"] == {"rates": {"ALL": 0}}
    assert payload["demand_charges"]["All Year"] == {}
    assert payload["name"] == "Custom Tariff"
    assert payload["utility"] == "Custom"
    assert payload["currency"] == "AUD"


def test_static_payload_keeps_all_seasons_and_strips_power_sync_metadata(
    tariff_converter_module,
):
    raw_tariff = _agl_tariff()
    raw_tariff["seasons"]["Winter"] = {
        "fromMonth": 6,
        "toMonth": 8,
        "description": "UI-only winter label",
        "tou_periods": {
            "WINTER": [
                {
                    "fromDayOfWeek": 0,
                    "toDayOfWeek": 6,
                    "fromHour": 0,
                    "toHour": 24,
                }
            ]
        },
    }
    raw_tariff["energy_charges"]["Winter"] = {"WINTER": 0.22}
    raw_tariff["sell_tariff"]["energy_charges"]["Winter"] = {"WINTER": 0.04}
    before = copy.deepcopy(raw_tariff)

    payload = tariff_converter_module.convert_custom_tariff_to_tesla_tariff(raw_tariff)

    assert payload is not None
    assert payload["energy_charges"]["All Year"]["rates"] == {
        "OFF_PEAK": 0.31,
        "OFF_PEAK_AGL_REWARD": 0.31,
    }
    assert payload["energy_charges"]["Winter"]["rates"] == {"WINTER": 0.22}
    assert payload["sell_tariff"]["energy_charges"]["Winter"]["rates"] == {
        "WINTER": 0.04
    }
    assert "description" not in payload["seasons"]["Winter"]
    for key in (
        "agl_base_tariff",
        "agl_battery_rewards",
        "updated_at",
        "quota",
    ):
        assert key not in payload
    assert raw_tariff == before


def _readback_helpers() -> dict[str, object]:
    """Load the small readback helpers without importing Home Assistant."""
    source = (COMPONENT_ROOT / "__init__.py").read_text()
    tree = ast.parse(source)
    wanted = {
        "_normalise_tariff_rate_map",
        "_tariff_charge_rates_by_season",
        "_strict_static_tariff_rates_by_season",
        "_tariff_charge_rates",
        "_tesla_tariff_matches_readback",
    }
    nodes = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    namespace: dict[str, object] = {"Any": Any, "math": math}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "__init__.py", "exec"), namespace)
    return namespace


def test_all_year_readback_match_and_mismatch_are_safe():
    helpers = _readback_helpers()
    matches = helpers["_tesla_tariff_matches_readback"]
    expected = _agl_tariff()
    expected["energy_charges"]["Winter"] = {"WINTER": 0.22}
    expected["sell_tariff"]["energy_charges"]["Winter"] = {"WINTER": 0.04}
    expected = {
        "name": expected["name"],
        "code": "POWER_SYNC:STATIC_TOU",
        "energy_charges": expected["energy_charges"],
        "sell_tariff": expected["sell_tariff"],
    }
    observed = copy.deepcopy(expected)
    for season in ("All Year", "Winter"):
        observed["energy_charges"][season] = {
            "rates": observed["energy_charges"][season]
        }
        observed["sell_tariff"]["energy_charges"][season] = {
            "rates": observed["sell_tariff"]["energy_charges"][season]
        }
    assert matches(expected, observed)

    observed["energy_charges"]["Winter"]["rates"]["WINTER"] = 0.05
    assert not matches(expected, observed)


def test_static_readback_rejects_missing_or_sell_only_mismatch():
    helpers = _readback_helpers()
    matches = helpers["_tesla_tariff_matches_readback"]
    expected = {
        "code": "POWER_SYNC:STATIC_TOU",
        "energy_charges": {
            "All Year": {"rates": {"OFF_PEAK": 0.31}},
        },
        "sell_tariff": {
            "energy_charges": {
                "All Year": {"rates": {"OFF_PEAK": 0.03}},
            },
        },
    }

    missing_sell = copy.deepcopy(expected)
    del missing_sell["sell_tariff"]
    assert not matches(expected, missing_sell)

    extra_empty_buy = copy.deepcopy(expected)
    extra_empty_buy["energy_charges"]["Winter"] = {}
    assert not matches(expected, extra_empty_buy)

    extra_malformed_sell = copy.deepcopy(expected)
    extra_malformed_sell["sell_tariff"]["energy_charges"]["Winter"] = {
        "rates": {"WINTER": "not-a-rate"}
    }
    assert not matches(expected, extra_malformed_sell)

    sell_only_expected = copy.deepcopy(expected)
    sell_only_expected["energy_charges"] = {}
    sell_only_observed = copy.deepcopy(sell_only_expected)
    sell_only_observed["sell_tariff"]["energy_charges"]["All Year"]["rates"][
        "OFF_PEAK"
    ] = 0.04
    assert not matches(sell_only_expected, sell_only_observed)


def test_dynamic_readback_still_accepts_missing_all_sentinel_season():
    matches = _readback_helpers()["_tesla_tariff_matches_readback"]
    expected = {
        "code": "POWER_SYNC:AMBER",
        "energy_charges": {
            "ALL": {"rates": {"ALL": 0}},
            "Summer": {"rates": {"PEAK": 0.31}},
        },
        "sell_tariff": {
            "energy_charges": {
                "ALL": {"rates": {"ALL": 0}},
                "Summer": {"rates": {"PEAK": 0.03}},
            },
        },
    }
    observed = copy.deepcopy(expected)
    observed["energy_charges"].pop("ALL")
    observed["sell_tariff"]["energy_charges"].pop("ALL")
    assert matches(expected, observed)


def test_static_manual_branch_uses_normalized_payload_only_for_tesla_upload():
    source = (COMPONENT_ROOT / "__init__.py").read_text()
    sync_start = source.index("async def _handle_sync_tou_internal")
    sync_source = source[
        sync_start : source.index("        # --- Calibration detection helper", sync_start)
    ]

    assert "convert_custom_tariff_to_tesla_tariff" in sync_source
    assert "if use_static_tou:" in sync_source
    assert (
        "tariff = convert_custom_tariff_to_tesla_tariff(static_tou_custom_tariff)"
        in sync_source
    )
    assert "if use_static_tou and battery_system != \"tesla\":" in sync_source
    assert "tariff = convert_amber_to_tesla_tariff(" in sync_source
    assert "forecast_data," in sync_source


def test_static_display_schedule_is_not_replaced_by_summer_only_payload():
    source = (COMPONENT_ROOT / "__init__.py").read_text()
    sync_start = source.index("async def _handle_sync_tou_internal")
    sync_source = source[
        sync_start : source.index("        # --- Calibration detection helper", sync_start)
    ]

    assert 'static_tou_schedule.get("buy_rates")' in sync_source
    assert 'static_tou_schedule.get("sell_rates")' in sync_source
    assert 'if use_static_tou:\n            buy_prices' in sync_source
