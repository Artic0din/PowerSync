"""Regression tests for the Cost Neutral battery-export earnings cap."""

from __future__ import annotations

import importlib
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
COMPONENT_ROOT = ROOT / "custom_components" / "power_sync"
_SENTINEL = object()
_STUBS = (
    "homeassistant",
    "homeassistant.util",
    "homeassistant.util.dt",
    "power_sync",
    "power_sync.optimization",
    "power_sync.optimization.battery_optimizer",
    "power_sync.optimization.schedule_reader",
)


@pytest.fixture()
def optimizer_module():
    saved = {name: sys.modules.get(name, _SENTINEL) for name in _STUBS}
    for name in _STUBS:
        sys.modules.pop(name, None)
    ha = types.ModuleType("homeassistant")
    util = types.ModuleType("homeassistant.util")
    dt = types.ModuleType("homeassistant.util.dt")
    dt.now = lambda: datetime(2026, 8, 1, 20, 0, tzinfo=timezone.utc)
    dt.utcnow = dt.now
    dt.UTC = timezone.utc
    util.dt = dt
    ha.util = util
    sys.modules.update({
        "homeassistant": ha,
        "homeassistant.util": util,
        "homeassistant.util.dt": dt,
    })
    package = types.ModuleType("power_sync")
    package.__path__ = [str(COMPONENT_ROOT)]
    optimization = types.ModuleType("power_sync.optimization")
    optimization.__path__ = [str(COMPONENT_ROOT / "optimization")]
    sys.modules["power_sync"] = package
    sys.modules["power_sync.optimization"] = optimization
    module = importlib.import_module("power_sync.optimization.battery_optimizer")
    try:
        yield module
    finally:
        for name, value in saved.items():
            if value is _SENTINEL:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


def _optimizer(module):
    return module.BatteryOptimizer(
        capacity_wh=10_000,
        max_charge_w=5_000,
        max_discharge_w=5_000,
        efficiency=1.0,
        backup_reserve=0.0,
        hardware_reserve=0.0,
        interval_minutes=60,
        horizon_hours=4,
        terminal_weight=0.0,
    )


def _timestamps(n: int) -> list[datetime]:
    start = datetime(2026, 8, 1, 20, 0, tzinfo=timezone.utc)
    return [start + timedelta(hours=idx) for idx in range(n)]


def test_cost_neutral_caps_discretionary_battery_export_at_exact_earnings(
    optimizer_module,
):
    result = _optimizer(optimizer_module).optimize(
        import_prices=[0.20] * 4,
        export_prices=[1.00] * 4,
        solar_forecast=[0.0] * 4,
        load_forecast=[0.0] * 4,
        current_soc=1.0,
        allow_battery_export=[True] * 4,
        priority_export_slots=[True] * 4,
        priority_export_enabled=True,
        schedule_timestamps=_timestamps(4),
        cost_neutral_earnings_cap=2.0,
        cost_neutral_slots=[True] * 4,
    )

    planned = sum(result.schedule.battery_export_w) / 1000.0
    assert planned == pytest.approx(2.0, abs=1e-4)
    assert result.lp_stats["cost_neutral_planned_earnings"] == pytest.approx(2.0)
    assert max(result.schedule.battery_export_w) < 5_000


def test_cost_neutral_prices_export_induced_later_import_in_same_constraint(
    optimizer_module,
):
    optimizer = optimizer_module.BatteryOptimizer(
        capacity_wh=1_000,
        max_charge_w=5_000,
        max_discharge_w=5_000,
        efficiency=1.0,
        backup_reserve=0.0,
        hardware_reserve=0.0,
        interval_minutes=60,
        horizon_hours=2,
        terminal_weight=0.0,
    )
    result = optimizer.optimize(
        import_prices=[0.20, 0.20],
        export_prices=[1.00, 0.00],
        solar_forecast=[0.0, 0.0],
        load_forecast=[0.0, 1.0],
        current_soc=1.0,
        allow_battery_export=[True, False],
        priority_export_slots=[True, False],
        priority_export_enabled=True,
        schedule_timestamps=_timestamps(2),
        cost_neutral_earnings_cap=1.0,
        cost_neutral_slots=[True, True],
        cost_neutral_forecast_import_cost=0.0,
    )

    exported_kwh = result.battery_to_grid_w[0] / 1000.0
    induced_import_kwh = result.grid_import_w[1] / 1000.0
    assert exported_kwh == pytest.approx(1.0, abs=1e-4)
    assert induced_import_kwh == pytest.approx(1.0, abs=1e-4)
    assert exported_kwh - 0.20 * induced_import_kwh == pytest.approx(0.8)
    assert result.lp_stats["cost_neutral_earnings_cap"] == pytest.approx(1.2)
    assert result.lp_stats["cost_neutral_planned_earnings"] == pytest.approx(1.0)


def test_zero_cap_preserves_natural_solar_export(optimizer_module):
    result = _optimizer(optimizer_module).optimize(
        import_prices=[0.20] * 2,
        export_prices=[0.50] * 2,
        solar_forecast=[5.0] * 2,
        load_forecast=[0.0] * 2,
        current_soc=1.0,
        allow_battery_export=[True] * 2,
        priority_export_slots=[True] * 2,
        priority_export_enabled=True,
        schedule_timestamps=_timestamps(2),
        cost_neutral_earnings_cap=0.0,
        cost_neutral_slots=[True] * 2,
    )

    assert result.schedule.battery_export_w == [0.0, 0.0]
    assert result.grid_export_w == pytest.approx([5_000.0, 5_000.0])


def test_today_cap_does_not_apply_after_local_midnight(optimizer_module):
    result = _optimizer(optimizer_module).optimize(
        import_prices=[0.20] * 2,
        export_prices=[1.00] * 2,
        solar_forecast=[0.0] * 2,
        load_forecast=[0.0] * 2,
        current_soc=1.0,
        allow_battery_export=[True] * 2,
        priority_export_slots=[True] * 2,
        priority_export_enabled=True,
        schedule_timestamps=_timestamps(2),
        cost_neutral_earnings_cap=0.0,
        cost_neutral_slots=[True, False],
    )

    assert result.schedule.battery_export_w[0] == 0.0
    assert result.schedule.battery_export_w[1] > 0.0


def test_final_schedule_guard_trims_overlay_without_touching_home_discharge(
    optimizer_module,
):
    module = optimizer_module
    optimizer = _optimizer(module)
    start = _timestamps(1)[0]
    schedule = module.OptimizationSchedule(
        actions=[module.ScheduleAction(
            timestamp=start,
            action="export",
            power_w=4_000,
            soc=0.5,
            battery_discharge_w=5_000,
        )],
        predicted_cost=0.0,
        predicted_savings=0.0,
        last_updated=start,
    )

    trimmed, earnings = optimizer.enforce_cost_neutral_schedule(
        schedule,
        export_prices=[1.0],
        solar=[0.0],
        load=[1.0],
        earnings_cap=0.5,
        cost_neutral_slots=[True],
    )

    assert earnings == pytest.approx(0.5)
    assert trimmed.actions[0].battery_discharge_w == pytest.approx(1_500.0)
    assert trimmed.actions[0].power_w == pytest.approx(500.0)


def test_final_schedule_guard_values_only_remaining_capped_export_bonus(
    optimizer_module,
):
    module = optimizer_module
    optimizer = _optimizer(module)
    start = _timestamps(1)[0]
    schedule = module.OptimizationSchedule(
        actions=[module.ScheduleAction(
            timestamp=start,
            action="export",
            power_w=1_000,
            soc=0.5,
            battery_discharge_w=1_000,
        )],
        predicted_cost=0.0,
        predicted_savings=0.0,
        last_updated=start,
    )

    trimmed, earnings = optimizer.enforce_cost_neutral_schedule(
        schedule,
        export_prices=[0.10],
        solar=[0.0],
        load=[0.0],
        earnings_cap=1.0,
        cost_neutral_slots=[True],
        export_bonus_prices=[0.90],
        export_bonus_cap_kwh=0.10,
    )

    assert trimmed.actions[0].battery_discharge_w == pytest.approx(1_000.0)
    assert earnings == pytest.approx(0.19)
