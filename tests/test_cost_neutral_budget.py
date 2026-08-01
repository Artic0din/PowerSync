"""Unit tests for Cost Neutral local-day accounting."""

import importlib.util
import sys
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "power_sync"
    / "optimization"
    / "cost_neutral.py"
)
SPEC = importlib.util.spec_from_file_location("cost_neutral_test_module", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
CostNeutralBudget = MODULE.CostNeutralBudget


def test_exact_daily_cost_example_produces_two_dollar_export_cap():
    budget = CostNeutralBudget(
        supply_charge=1.20,
        measured_import_cost=0.80,
        measured_export_earnings=0.50,
        forecast_import_cost=0.50,
        forecast_natural_export_earnings=0.0,
    )

    assert budget.base_projected_cost == 2.0
    assert budget.battery_export_earnings_cap == 2.0


def test_natural_export_covering_daily_cost_sets_zero_battery_export_cap():
    budget = CostNeutralBudget(
        supply_charge=0.0,
        measured_import_cost=0.50,
        measured_export_earnings=0.25,
        forecast_import_cost=0.25,
        forecast_natural_export_earnings=0.75,
    )

    assert budget.base_projected_cost == -0.25
    assert budget.battery_export_earnings_cap == 0.0


def test_missing_or_zero_supply_still_covers_import_costs():
    budget = CostNeutralBudget(
        supply_charge=0.0,
        measured_import_cost=0.75,
        measured_export_earnings=0.10,
        forecast_import_cost=0.35,
        forecast_natural_export_earnings=0.0,
    )

    assert budget.battery_export_earnings_cap == 1.0
