"""Unit tests for BatteryOptimizer pure-calculation methods.

Covers the pure helper functions that feed into the LP and greedy solvers:

    _is_export_profitable          — gate on whether a slot justifies battery export
    _effective_export_acquisition_costs — cheapest prior charge cost per slot
    _calculate_baseline_cost       — no-battery cost model (used for savings display)
    _solve_lp_relaxed              — infeasible LP fallback that relaxes reserve to 5%

These are isolated, stateless calculations. Tests use exact numeric assertions
because these functions feed money/energy decisions. Battery sign convention
used in the optimizer: negative = charge, positive = discharge.

Run with: pytest tests/test_optimizer_calculation_unit.py
"""

from __future__ import annotations

import importlib
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent / "custom_components" / "power_sync"
COMPONENT_ROOT = ROOT

_SENTINEL = object()
_STUB_MODULE_NAMES = (
    "homeassistant",
    "homeassistant.util",
    "homeassistant.util.dt",
    "power_sync",
    "power_sync.optimization",
    "power_sync.optimization.battery_optimizer",
    "power_sync.optimization.schedule_reader",
)


def _install_stubs() -> None:
    ha_root = types.ModuleType("homeassistant")
    ha_util = types.ModuleType("homeassistant.util")
    ha_dt = types.ModuleType("homeassistant.util.dt")
    ha_dt.now = lambda *a, **kw: datetime(2026, 6, 12, 8, 0, tzinfo=timezone.utc)
    ha_dt.utcnow = lambda *a, **kw: datetime(2026, 6, 12, 8, 0, tzinfo=timezone.utc)
    ha_dt.UTC = timezone.utc
    ha_util.dt = ha_dt
    ha_root.util = ha_util
    sys.modules["homeassistant"] = ha_root
    sys.modules["homeassistant.util"] = ha_util
    sys.modules["homeassistant.util.dt"] = ha_dt

    ps = types.ModuleType("power_sync")
    ps.__path__ = [str(COMPONENT_ROOT)]
    sys.modules["power_sync"] = ps

    opt = types.ModuleType("power_sync.optimization")
    opt.__path__ = [str(COMPONENT_ROOT / "optimization")]
    sys.modules["power_sync.optimization"] = opt


@pytest.fixture(scope="module")
def mod():
    saved = {name: sys.modules.get(name, _SENTINEL) for name in _STUB_MODULE_NAMES}
    for name in _STUB_MODULE_NAMES:
        sys.modules.pop(name, None)
    _install_stubs()
    module = importlib.import_module("power_sync.optimization.battery_optimizer")
    yield module
    for name in _STUB_MODULE_NAMES:
        if saved[name] is _SENTINEL:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = saved[name]


def _optimizer(mod, **kwargs):
    defaults = dict(
        capacity_wh=13500,
        max_charge_w=5000,
        max_discharge_w=5000,
        backup_reserve=0.20,
        interval_minutes=5,
        horizon_hours=1,
        efficiency=0.92,
    )
    defaults.update(kwargs)
    return mod.BatteryOptimizer(**defaults)


# ---------------------------------------------------------------------------
# _is_export_profitable
# ---------------------------------------------------------------------------

class TestIsExportProfitable:
    """_is_export_profitable gates whether a slot justifies battery export."""

    def test_profitable_when_export_exceeds_import_no_acquisition_cost(self, mod):
        # ARRANGE: export 50c > import 30c, no acquisition cost
        # ACT
        result = mod.BatteryOptimizer._is_export_profitable(
            export_price=0.50,
            import_price=0.30,
            acquisition_cost_kwh=0.0,
            effective_acquisition_cost_kwh=0.0,
        )
        # ASSERT
        assert result is True

    def test_not_profitable_when_export_below_import(self, mod):
        # ARRANGE: export 5c < import 30c — only worthwhile for self-consumption
        # ACT
        result = mod.BatteryOptimizer._is_export_profitable(
            export_price=0.05,
            import_price=0.30,
            acquisition_cost_kwh=0.0,
            effective_acquisition_cost_kwh=0.0,
        )
        # ASSERT
        assert result is False

    def test_not_profitable_when_export_is_zero(self, mod):
        # ARRANGE: 0c FiT — never worthwhile to export
        # ACT
        result = mod.BatteryOptimizer._is_export_profitable(
            export_price=0.0,
            import_price=0.30,
            acquisition_cost_kwh=0.0,
            effective_acquisition_cost_kwh=0.0,
        )
        # ASSERT
        assert result is False

    def test_not_profitable_when_export_below_acquisition_cost(self, mod):
        # ARRANGE: battery was charged at 25c, FiT is only 12c — exporting loses money
        # ACT
        result = mod.BatteryOptimizer._is_export_profitable(
            export_price=0.12,
            import_price=0.30,
            acquisition_cost_kwh=0.25,
            effective_acquisition_cost_kwh=0.25,
        )
        # ASSERT
        assert result is False

    def test_profitable_when_export_covers_acquisition_cost(self, mod):
        # ARRANGE: export 50c, charged at 25c — profitable
        # ACT
        result = mod.BatteryOptimizer._is_export_profitable(
            export_price=0.50,
            import_price=0.30,
            acquisition_cost_kwh=0.25,
            effective_acquisition_cost_kwh=0.25,
        )
        # ASSERT
        assert result is True

    def test_profitable_when_cheaply_charged_export_beats_effective_cost(self, mod):
        # ARRANGE: original acquisition cost 25c but cheapest prior import brought
        # effective cost down to 8c. Export at 12c > 8c effective — profitable.
        # (export_price 0.12 < import_price 0.30 but >= effective_acquisition 0.08)
        # ACT
        result = mod.BatteryOptimizer._is_export_profitable(
            export_price=0.12,
            import_price=0.30,
            acquisition_cost_kwh=0.25,
            effective_acquisition_cost_kwh=0.08,
        )
        # ASSERT
        assert result is True

    def test_not_profitable_below_threshold(self, mod):
        # ARRANGE: below the 0.001 $/kWh threshold — treated as zero FiT
        # ACT
        result = mod.BatteryOptimizer._is_export_profitable(
            export_price=0.0009,
            import_price=0.30,
            acquisition_cost_kwh=0.0,
            effective_acquisition_cost_kwh=0.0,
        )
        # ASSERT
        assert result is False

    def test_export_equal_to_import_with_no_acquisition_is_not_profitable(self, mod):
        # ARRANGE: export == import (not strictly >), no acquisition cost
        # ACT
        result = mod.BatteryOptimizer._is_export_profitable(
            export_price=0.30,
            import_price=0.30,
            acquisition_cost_kwh=0.0,
            effective_acquisition_cost_kwh=0.0,
        )
        # ASSERT: not > import price
        assert result is False

    def test_export_above_import_blocked_by_acquisition_cost_still_at_same_level(
        self, mod
    ):
        # ARRANGE: export 0.35 > import 0.30, but acquisition = 0.40 > export
        # → export < acquisition_cost so not profitable
        # ACT
        result = mod.BatteryOptimizer._is_export_profitable(
            export_price=0.35,
            import_price=0.30,
            acquisition_cost_kwh=0.40,
            effective_acquisition_cost_kwh=0.40,
        )
        # ASSERT
        assert result is False


# ---------------------------------------------------------------------------
# _effective_export_acquisition_costs
# ---------------------------------------------------------------------------

class TestEffectiveExportAcquisitionCosts:
    """_effective_export_acquisition_costs tracks cheapest prior import per slot."""

    def test_no_acquisition_cost_returns_all_zeros(self, mod):
        # ARRANGE: acquisition_cost_kwh = 0 → function short-circuits to zeros
        n = 4
        import_prices = [0.30, 0.10, 0.25, 0.40]
        # ACT
        costs = mod.BatteryOptimizer._effective_export_acquisition_costs(
            n, import_prices, [False] * n, allow_grid_charge=True, acquisition_cost_kwh=0.0,
        )
        # ASSERT
        assert costs == [0.0, 0.0, 0.0, 0.0]

    def test_first_slot_uses_acquisition_cost_as_starting_value(self, mod):
        # ARRANGE: slot 0 — no prior cheaper import seen yet
        n = 3
        import_prices = [0.30, 0.10, 0.25]
        # ACT
        costs = mod.BatteryOptimizer._effective_export_acquisition_costs(
            n, import_prices, [False] * n, allow_grid_charge=True, acquisition_cost_kwh=0.25,
        )
        # ASSERT: slot 0 uses acquisition_cost = 0.25 (no prior import)
        assert costs[0] == pytest.approx(0.25)

    def test_cheapest_prior_import_lowers_effective_cost_by_slot_one(self, mod):
        # ARRANGE: slot 0 import = 0.10, acquisition = 0.25
        # By slot 1 the cheapest prior import (0.10) overrides acquisition (0.25)
        n = 2
        import_prices = [0.10, 0.40]
        # ACT
        costs = mod.BatteryOptimizer._effective_export_acquisition_costs(
            n, import_prices, [False] * n, allow_grid_charge=True, acquisition_cost_kwh=0.25,
        )
        # ASSERT: slot 1 effective = min(0.25, 0.10) = 0.10
        assert costs[0] == pytest.approx(0.25)
        assert costs[1] == pytest.approx(0.10)

    def test_blocked_charge_slot_does_not_update_cheapest(self, mod):
        # ARRANGE: slot 0 is charge-blocked (e.g. export-only window).
        # The cheap import price there must NOT feed into future effective cost.
        n = 2
        import_prices = [0.05, 0.40]
        block = [True, False]
        # ACT
        costs = mod.BatteryOptimizer._effective_export_acquisition_costs(
            n, import_prices, block, allow_grid_charge=True, acquisition_cost_kwh=0.25,
        )
        # ASSERT: blocked slot doesn't set cheapest_prior → slot 1 stays at 0.25
        assert costs[0] == pytest.approx(0.25)
        assert costs[1] == pytest.approx(0.25)

    def test_grid_charge_disabled_skips_all_slot_updates(self, mod):
        # ARRANGE: allow_grid_charge=False → no slot ever updates cheapest_prior
        n = 3
        import_prices = [0.05, 0.10, 0.40]
        # ACT
        costs = mod.BatteryOptimizer._effective_export_acquisition_costs(
            n, import_prices, [False] * n, allow_grid_charge=False, acquisition_cost_kwh=0.30,
        )
        # ASSERT: every slot stays at acquisition_cost (no import tracked)
        assert all(c == pytest.approx(0.30) for c in costs)

    def test_result_length_equals_n(self, mod):
        # ARRANGE
        n = 6
        import_prices = [0.30] * n
        # ACT
        costs = mod.BatteryOptimizer._effective_export_acquisition_costs(
            n, import_prices, [False] * n, allow_grid_charge=True, acquisition_cost_kwh=0.25,
        )
        # ASSERT: must return exactly n values
        assert len(costs) == n

    def test_decreasing_prices_track_running_minimum(self, mod):
        # ARRANGE: prices [0.30, 0.20, 0.10] — each slot should see a lower effective cost
        n = 3
        import_prices = [0.30, 0.20, 0.10]
        # ACT
        costs = mod.BatteryOptimizer._effective_export_acquisition_costs(
            n, import_prices, [False] * n, allow_grid_charge=True, acquisition_cost_kwh=0.40,
        )
        # ASSERT: min prior at each point
        assert costs[0] == pytest.approx(0.40)   # no prior
        assert costs[1] == pytest.approx(0.30)   # min(0.40, 0.30)
        assert costs[2] == pytest.approx(0.20)   # min(0.40, 0.30, 0.20)


# ---------------------------------------------------------------------------
# _calculate_baseline_cost
# ---------------------------------------------------------------------------

class TestCalculateBaselineCost:
    """_calculate_baseline_cost models energy cost with no battery at all."""

    def test_all_import_no_solar(self, mod):
        # ARRANGE: 1 kW load, 0 solar, 0.30 $/kWh, 4 x 5-min slots
        # Cost = 0.30 * 1.0 * (5/60) * 4 = 0.10 $
        opt = _optimizer(mod)
        n = 4
        import_prices = [0.30] * n
        export_prices = [0.05] * n
        solar = [0.0] * n
        load = [1.0] * n
        # ACT
        cost = opt._calculate_baseline_cost(n, import_prices, export_prices, solar, load)
        # ASSERT
        assert cost == pytest.approx(0.10, abs=0.001)

    def test_all_solar_export_earns_revenue(self, mod):
        # ARRANGE: 2 kW solar, 0 load, 0.10 $/kWh FiT, 4 x 5-min slots
        # Revenue = 0.10 * 2.0 * (5/60) * 4 = 0.0667 $
        # After round(cost, 2) → -0.07
        opt = _optimizer(mod)
        n = 4
        import_prices = [0.30] * n
        export_prices = [0.10] * n
        solar = [2.0] * n
        load = [0.0] * n
        # ACT
        cost = opt._calculate_baseline_cost(n, import_prices, export_prices, solar, load)
        # ASSERT: result is rounded to 2dp; revenue rounds to -0.07
        assert cost == pytest.approx(-0.07, abs=0.001)
        assert cost == round(cost, 2)

    def test_mixed_import_and_export(self, mod):
        # ARRANGE: 2 slots import at 0.30 (1 kW net load), 2 slots solar surplus at 0.10
        # import_cost = 0.30 * 1.0 * (5/60) * 2 = 0.05
        # export_rev  = 0.10 * 1.0 * (5/60) * 2 ≈ 0.0167
        # net ≈ 0.0333, rounds to 0.03
        opt = _optimizer(mod)
        n = 4
        import_prices = [0.30] * n
        export_prices = [0.10] * n
        solar = [0.0, 0.0, 2.0, 2.0]
        load = [1.0, 1.0, 1.0, 1.0]
        # ACT
        cost = opt._calculate_baseline_cost(n, import_prices, export_prices, solar, load)
        # ASSERT: result rounded to 2dp → 0.03
        assert cost == pytest.approx(0.03, abs=0.001)
        assert cost == round(cost, 2)

    def test_zero_cost_when_solar_exactly_covers_load(self, mod):
        # ARRANGE: solar == load every slot → net = 0 at every step
        opt = _optimizer(mod)
        n = 4
        import_prices = [0.30] * n
        export_prices = [0.10] * n
        solar = [1.0] * n
        load = [1.0] * n
        # ACT
        cost = opt._calculate_baseline_cost(n, import_prices, export_prices, solar, load)
        # ASSERT
        assert cost == pytest.approx(0.0, abs=0.001)

    def test_zero_import_price_yields_zero_import_cost(self, mod):
        # ARRANGE: 0c import rate (GloBird SUPER_OFF_PEAK / GloBird FOUR4FREE)
        opt = _optimizer(mod)
        n = 4
        import_prices = [0.0] * n
        export_prices = [0.0] * n
        solar = [0.0] * n
        load = [1.0] * n
        # ACT
        cost = opt._calculate_baseline_cost(n, import_prices, export_prices, solar, load)
        # ASSERT: 0c price → 0 cost
        assert cost == pytest.approx(0.0, abs=0.001)

    def test_result_is_rounded_to_two_decimal_places(self, mod):
        # ARRANGE: use values that produce a repeating decimal without rounding
        opt = _optimizer(mod)
        n = 3
        import_prices = [0.299] * n
        export_prices = [0.0] * n
        solar = [0.0] * n
        load = [1.0] * n
        # ACT
        cost = opt._calculate_baseline_cost(n, import_prices, export_prices, solar, load)
        # ASSERT: result is rounded to exactly 2dp
        assert cost == round(cost, 2)


# ---------------------------------------------------------------------------
# _solve_lp_relaxed (infeasible LP fallback)
# ---------------------------------------------------------------------------

class TestSolveLpRelaxed:
    """_solve_lp_relaxed retries the LP with backup_reserve relaxed to 5%.

    Contract:
    - backup_reserve is restored to original value after the call (even on failure)
    - result contains a valid schedule with correct number of steps
    - When scipy IS available and the LP succeeds, result.feasible is False
      (the relaxed flag marks the result as a best-effort non-optimal solve)

    When scipy is unavailable the except-branch uses greedy, which can return
    feasible=True — this is a known implementation characteristic rather than
    a contract violation. We only assert feasible=False when scipy is present.
    """

    def test_result_is_marked_not_feasible_when_scipy_available(self, mod):
        # ARRANGE: only run when scipy LP solver is actually present
        if not mod.SCIPY_AVAILABLE:
            pytest.skip("scipy not installed — relaxed LP path not active")
        opt = _optimizer(mod, backup_reserve=0.30)
        n = 12
        # ACT: valid LP that the relaxed path can solve
        result = opt._solve_lp_relaxed(
            n=n,
            import_prices=[0.30] * n,
            export_prices=[0.05] * n,
            solar=[0.0] * n,
            load=[1.0] * n,
            soc_0=0.50,
            cost_function="cost",
        )
        # ASSERT: relaxed solve sets feasible=False as a signal to callers
        assert result.feasible is False

    def test_backup_reserve_is_restored_after_call(self, mod, monkeypatch):
        # ARRANGE: verify original reserve survives the relaxed solve
        # Use greedy path (scipy off) to keep test fast
        monkeypatch.setattr(mod, "SCIPY_AVAILABLE", False)
        opt = _optimizer(mod, backup_reserve=0.30)
        original = opt.backup_reserve
        n = 6
        # ACT
        opt._solve_lp_relaxed(
            n=n,
            import_prices=[0.30] * n,
            export_prices=[0.05] * n,
            solar=[0.0] * n,
            load=[1.0] * n,
            soc_0=0.10,
            cost_function="cost",
        )
        # ASSERT: original reserve survived, regardless of what happened inside
        assert opt.backup_reserve == pytest.approx(original)

    def test_backup_reserve_restored_even_when_inner_lp_raises(self, mod, monkeypatch):
        # ARRANGE: simulate a scipy linprog crash inside _solve_lp_inner
        monkeypatch.setattr(mod, "SCIPY_AVAILABLE", True)
        original_inner = mod.BatteryOptimizer._solve_lp_inner
        def crashing_inner(self_obj, *args, **kwargs):
            raise RuntimeError("simulated LP crash")
        monkeypatch.setattr(mod.BatteryOptimizer, "_solve_lp_inner", crashing_inner)
        opt = _optimizer(mod, backup_reserve=0.40)
        original_reserve = opt.backup_reserve
        n = 4
        # ACT: the except block falls back to greedy; reserve must still be restored
        opt._solve_lp_relaxed(
            n=n,
            import_prices=[0.30] * n,
            export_prices=[0.05] * n,
            solar=[0.0] * n,
            load=[1.0] * n,
            soc_0=0.50,
            cost_function="cost",
        )
        # ASSERT
        assert opt.backup_reserve == pytest.approx(original_reserve)
        monkeypatch.setattr(mod.BatteryOptimizer, "_solve_lp_inner", original_inner)

    def test_schedule_has_correct_action_count(self, mod, monkeypatch):
        # ARRANGE
        monkeypatch.setattr(mod, "SCIPY_AVAILABLE", False)
        opt = _optimizer(mod)
        n = 8
        # ACT
        result = opt._solve_lp_relaxed(
            n=n,
            import_prices=[0.30] * n,
            export_prices=[0.05] * n,
            solar=[0.0] * n,
            load=[1.0] * n,
            soc_0=0.50,
            cost_function="cost",
        )
        # ASSERT
        assert len(result.schedule.actions) == n

    def test_result_grid_import_series_has_correct_length(self, mod, monkeypatch):
        # ARRANGE
        monkeypatch.setattr(mod, "SCIPY_AVAILABLE", False)
        opt = _optimizer(mod)
        n = 6
        # ACT
        result = opt._solve_lp_relaxed(
            n=n,
            import_prices=[0.30] * n,
            export_prices=[0.05] * n,
            solar=[0.0] * n,
            load=[1.0] * n,
            soc_0=0.50,
            cost_function="cost",
        )
        # ASSERT
        assert len(result.grid_import_w) == n

    def test_battery_does_not_export_when_not_permitted(self, mod, monkeypatch):
        # ARRANGE: allow_battery_export=False throughout
        monkeypatch.setattr(mod, "SCIPY_AVAILABLE", False)
        opt = _optimizer(mod)
        n = 8
        # ACT
        result = opt._solve_lp_relaxed(
            n=n,
            import_prices=[0.30] * n,
            export_prices=[0.50] * n,
            solar=[0.0] * n,
            load=[0.5] * n,
            soc_0=0.80,
            cost_function="cost",
            allow_battery_export=[False] * n,
        )
        # ASSERT: no export actions in schedule
        assert all(action.action != "export" for action in result.schedule.actions)
        assert max(result.grid_export_w) <= 1e-3
