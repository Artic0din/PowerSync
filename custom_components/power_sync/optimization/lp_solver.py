"""lp_solver extracted from battery_optimizer (architecture refactor Phase 4)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import logging
import math
import time
from dataclasses import dataclass
from typing import Any

try:
    import highspy
except ImportError:
    highspy = None

from homeassistant.util import dt as dt_util

from .results import OptimizerResult
from .schedule_reader import OptimizationSchedule, ScheduleAction
from .solver_constants import (
    ACTION_THRESHOLD_W,
    LP_FAR_PERIOD_MINUTES,
    LP_MID_HORIZON_HOURS,
    LP_MID_PERIOD_MINUTES,
    LP_NEAR_HORIZON_HOURS,
    LP_POWER_SPLIT_THRESHOLD_KW,
    LP_PRICE_SPLIT_THRESHOLD,
    LP_SOLVER_TIME_LIMIT_SECONDS,
    MODE_PROJECTION_MAX_ITERATIONS,
    PRE_WINDOW_REACHABILITY_MARGIN_SOC,
    PRE_WINDOW_REACHABLE_TARGET_MARGIN_SOC,
)

_LOGGER = logging.getLogger(__name__)


class _LpMatrix:
    """Minimal row-oriented sparse matrix for building LP constraints.

    Implements just the subset of ``scipy.sparse.lil_matrix`` the optimizer
    relies on — ``shape``, ``m[i, j] = v`` assignment, ``m[i, j]`` lookup,
    ``.nnz``, ``.tocsr()`` (a no-op) and per-row iteration — so we can build the
    constraint matrices and feed HiGHS directly without depending on scipy.
    """

    __slots__ = ("shape", "_rows")

    def __init__(self, shape, dtype=float):
        rows, cols = int(shape[0]), int(shape[1])
        self.shape = (rows, cols)
        self._rows: list[dict[int, float]] = [dict() for _ in range(rows)]

    def __setitem__(self, key, value) -> None:
        i, j = key
        value = float(value)
        if value == 0.0:
            self._rows[i].pop(j, None)
        else:
            self._rows[i][j] = value

    def __getitem__(self, key) -> float:
        i, j = key
        return self._rows[i].get(j, 0.0)

    @property
    def nnz(self) -> int:
        return sum(len(r) for r in self._rows)

    def tocsr(self) -> "_LpMatrix":
        return self

    def iter_rows(self):
        """Yield (row_index, [col indices], [values]) for non-trivial use."""
        for i, row in enumerate(self._rows):
            yield i, list(row.keys()), list(row.values())

class _HighsResult:
    """linprog-compatible result wrapper so the solve call site is unchanged."""

    __slots__ = ("x", "success", "message", "status", "fun")

    def __init__(self, x, success, message, status, fun):
        self.x = x
        self.success = success
        self.message = message
        self.status = status
        self.fun = fun

@dataclass(frozen=True)
class _LpPeriod:
    """Internal LP period mapped to a range of base schedule slots."""

    start: int
    end: int
    import_price: float
    export_price: float
    export_bonus_price: float
    import_bonus_price: float
    solar_kw: float
    load_kw: float
    allow_battery_export: bool
    block_battery_charge: bool
    grid_charge_allowed: bool
    priority_export: bool
    mode: str | None = None
    required_self_use_kw: float = 0.0

    @property
    def slot_count(self) -> int:
        return self.end - self.start

def _solve_lp_highs(
    c, A_ub, b_ub, A_eq, b_eq, bounds, time_limit, integer_indices=None
):
    """Solve a standard-form LP with HiGHS and return a linprog-like result.

    minimize  c·x   s.t.   A_ub·x <= b_ub,  A_eq·x == b_eq,  bounds[j] on x[j].

    Mirrors ``scipy.optimize.linprog(method="highs")``: an optimal solve sets
    ``success=True``, and so does a time/iteration-limited solve that still
    holds a feasible incumbent (that incumbent is returned instead of being
    discarded); infeasible/unbounded — and limited solves with no feasible
    incumbent — report success=False with a message string (``"infeasible"``
    substring preserved so the caller's self-consumption fallback still
    triggers).
    """
    inf = highspy.kHighsInf
    h = highspy.Highs()
    h.setOptionValue("output_flag", False)
    h.setOptionValue("log_to_console", False)
    h.setOptionValue("time_limit", float(time_limit))

    # Columns carry the objective coefficients and variable bounds; constraint
    # coefficients are supplied row-by-row below, so each column starts empty.
    for j in range(len(c)):
        lo, hi = bounds[j]
        lo = -inf if lo is None else float(lo)
        hi = inf if hi is None else float(hi)
        h.addCol(float(c[j]), lo, hi, 0, [], [])
    for index in integer_indices or ():
        h.setInteger(int(index))

    # Equality rows: lower == upper == b_eq[i].
    for i, idx, val in A_eq.iter_rows():
        rhs = float(b_eq[i])
        h.addRow(rhs, rhs, len(idx), idx, val)

    # Inequality rows: -inf <= row·x <= b_ub[i].
    for i, idx, val in A_ub.iter_rows():
        h.addRow(-inf, float(b_ub[i]), len(idx), idx, val)

    h.run()

    model_status = h.getModelStatus()
    message = h.modelStatusToString(model_status)
    optimal = model_status == highspy.HighsModelStatus.kOptimal

    # A time/iteration-limited solve can still hold a usable feasible
    # incumbent — use it instead of discarding it and forcing the caller's
    # greedy fallback.
    usable_incumbent = False
    if not optimal and model_status in (
        highspy.HighsModelStatus.kTimeLimit,
        highspy.HighsModelStatus.kIterationLimit,
    ):
        info = h.getInfo()
        usable_incumbent = (
            getattr(info, "primal_solution_status", None)
            == highspy.kSolutionStatusFeasible
        )

    success = optimal or usable_incumbent
    if success:
        x = list(h.getSolution().col_value)
        fun = float(h.getObjectiveValue())
    else:
        x = None
        fun = None
    return _HighsResult(
        x=x,
        success=success,
        message=message,
        status=int(model_status),
        fun=fun,
    )



class LpSolverMixin:
    """Mixin hosting lp_solver implementation for BatteryOptimizer."""

    def _align_forecasts(
        self,
        import_prices: list[float],
        export_prices: list[float],
        solar_forecast: list[float],
        load_forecast: list[float],
    ) -> int:
        """Determine the number of time steps from available data."""
        lengths = [
            len(arr)
            for arr in [import_prices, export_prices, solar_forecast, load_forecast]
            if arr
        ]
        if not lengths:
            return 0

        max_steps = int(self.horizon_hours * 60 / self.interval_minutes)
        return min(max(lengths), max_steps)
    def _pad_array(
        self, arr: list[float] | None, target_len: int, default: float
    ) -> list[float]:
        """Pad or truncate array to target length."""
        if not arr:
            return [default] * target_len
        if len(arr) >= target_len:
            return arr[:target_len]
        # Pad with the caller-supplied default
        return arr + [default] * (target_len - len(arr))
    def _build_lp_periods(
        self,
        n: int,
        import_prices: list[float],
        export_prices: list[float],
        solar: list[float],
        load: list[float],
        allow_battery_export: list[bool],
        block_battery_charge: list[bool],
        grid_charge_allowed: list[bool] | None = None,
        export_bonus_prices: list[float] | None = None,
        import_bonus_prices: list[float] | None = None,
        priority_export_slots: list[bool] | None = None,
        mode_slots: list[str | None] | None = None,
        required_self_use_kw: list[float] | None = None,
    ) -> list[_LpPeriod]:
        """Aggregate base 5-minute slots into internal LP periods."""
        near_slots = int(LP_NEAR_HORIZON_HOURS * 60 / self.interval_minutes)
        mid_slots = int(LP_MID_HORIZON_HOURS * 60 / self.interval_minutes)
        mid_width = max(1, int(LP_MID_PERIOD_MINUTES / self.interval_minutes))
        far_width = max(1, int(LP_FAR_PERIOD_MINUTES / self.interval_minutes))
        bonus_prices = export_bonus_prices or [0.0] * n
        import_bonus = import_bonus_prices or [0.0] * n
        grid_charge_allowed = grid_charge_allowed or [True] * n
        priority_export_slots = priority_export_slots or [False] * n
        mode_slots = mode_slots or [None] * n
        required_self_use_kw = required_self_use_kw or [0.0] * n
        periods: list[_LpPeriod] = []
        idx = 0

        while idx < n:
            if idx < near_slots:
                width = 1
            elif idx < mid_slots:
                width = min(mid_width, mid_slots - idx)
            else:
                width = far_width

            end = min(n, idx + width)
            end = self._split_lp_period_end(
                idx,
                end,
                import_prices,
                export_prices,
                solar,
                load,
                allow_battery_export,
                block_battery_charge,
                grid_charge_allowed,
                bonus_prices,
                import_bonus,
                priority_export_slots,
                mode_slots,
                required_self_use_kw,
            )

            # Keep the pre-window SOC deadline on an exact internal boundary.
            if self.pre_window_slot is not None and idx < self.pre_window_slot < end:
                end = self.pre_window_slot

            periods.append(
                _LpPeriod(
                    start=idx,
                    end=end,
                    import_price=sum(import_prices[idx:end]) / (end - idx),
                    export_price=sum(export_prices[idx:end]) / (end - idx),
                    export_bonus_price=sum(bonus_prices[idx:end]) / (end - idx),
                    import_bonus_price=sum(import_bonus[idx:end]) / (end - idx),
                    solar_kw=sum(solar[idx:end]) / (end - idx),
                    load_kw=sum(load[idx:end]) / (end - idx),
                    allow_battery_export=allow_battery_export[idx],
                    block_battery_charge=block_battery_charge[idx],
                    grid_charge_allowed=all(grid_charge_allowed[idx:end]),
                    priority_export=priority_export_slots[idx],
                    mode=mode_slots[idx],
                    required_self_use_kw=(
                        sum(required_self_use_kw[idx:end]) / (end - idx)
                    ),
                )
            )
            idx = end

        return periods
    def _split_lp_period_end(
        self,
        start: int,
        proposed_end: int,
        import_prices: list[float],
        export_prices: list[float],
        solar: list[float],
        load: list[float],
        allow_battery_export: list[bool],
        block_battery_charge: list[bool],
        grid_charge_allowed: list[bool],
        export_bonus_prices: list[float],
        import_bonus_prices: list[float],
        priority_export_slots: list[bool],
        mode_slots: list[str | None],
        required_self_use_kw: list[float],
    ) -> int:
        """Shorten a coarse period when correctness-sensitive inputs change."""
        if proposed_end <= start + 1:
            return proposed_end

        first_allow = allow_battery_export[start]
        first_block = block_battery_charge[start]
        first_grid_charge_allowed = grid_charge_allowed[start]
        first_priority_export = priority_export_slots[start]
        first_mode = mode_slots[start]
        min_required_self_use = max_required_self_use = required_self_use_kw[start]
        first_import_free = import_prices[start] <= 0.001
        first_export_free = export_prices[start] <= 0.001
        first_bonus_free = export_bonus_prices[start] <= 0.001
        first_import_bonus_free = import_bonus_prices[start] <= 0.001
        min_import = max_import = import_prices[start]
        min_export = max_export = export_prices[start]
        min_bonus = max_bonus = export_bonus_prices[start]
        min_import_bonus = max_import_bonus = import_bonus_prices[start]
        first_net_load = load[start] - solar[start]
        first_surplus = max(0.0, solar[start] - load[start])
        first_net_load_positive = first_net_load > LP_POWER_SPLIT_THRESHOLD_KW
        first_surplus_positive = first_surplus > LP_POWER_SPLIT_THRESHOLD_KW
        min_net_load = max_net_load = first_net_load
        min_surplus = max_surplus = first_surplus

        for idx in range(start + 1, proposed_end):
            min_import = min(min_import, import_prices[idx])
            max_import = max(max_import, import_prices[idx])
            min_export = min(min_export, export_prices[idx])
            max_export = max(max_export, export_prices[idx])
            min_bonus = min(min_bonus, export_bonus_prices[idx])
            max_bonus = max(max_bonus, export_bonus_prices[idx])
            min_import_bonus = min(min_import_bonus, import_bonus_prices[idx])
            max_import_bonus = max(max_import_bonus, import_bonus_prices[idx])
            min_required_self_use = min(
                min_required_self_use,
                required_self_use_kw[idx],
            )
            max_required_self_use = max(
                max_required_self_use,
                required_self_use_kw[idx],
            )
            net_load = load[idx] - solar[idx]
            surplus = max(0.0, solar[idx] - load[idx])
            min_net_load = min(min_net_load, net_load)
            max_net_load = max(max_net_load, net_load)
            min_surplus = min(min_surplus, surplus)
            max_surplus = max(max_surplus, surplus)
            if (
                allow_battery_export[idx] != first_allow
                or block_battery_charge[idx] != first_block
                or grid_charge_allowed[idx] != first_grid_charge_allowed
                or priority_export_slots[idx] != first_priority_export
                or mode_slots[idx] != first_mode
                or (import_prices[idx] <= 0.001) != first_import_free
                or (export_prices[idx] <= 0.001) != first_export_free
                or (export_bonus_prices[idx] <= 0.001) != first_bonus_free
                or (import_bonus_prices[idx] <= 0.001) != first_import_bonus_free
                or max_import - min_import > LP_PRICE_SPLIT_THRESHOLD
                or max_export - min_export > LP_PRICE_SPLIT_THRESHOLD
                or max_bonus - min_bonus > LP_PRICE_SPLIT_THRESHOLD
                or max_import_bonus - min_import_bonus > LP_PRICE_SPLIT_THRESHOLD
                or max_required_self_use - min_required_self_use
                > LP_POWER_SPLIT_THRESHOLD_KW
                or (net_load > LP_POWER_SPLIT_THRESHOLD_KW) != first_net_load_positive
                or (surplus > LP_POWER_SPLIT_THRESHOLD_KW) != first_surplus_positive
                or max_net_load - min_net_load > LP_POWER_SPLIT_THRESHOLD_KW
                or max_surplus - min_surplus > LP_POWER_SPLIT_THRESHOLD_KW
            ):
                return idx

        return proposed_end
    def _solve_lp(
        self,
        n: int,
        import_prices: list[float],
        export_prices: list[float],
        solar: list[float],
        load: list[float],
        soc_0: float,
        cost_function: str,
        acquisition_cost_kwh: float = 0.0,
        allow_battery_export: list[bool] | None = None,
        block_battery_charge: list[bool] | None = None,
        allow_grid_charge: bool = True,
        grid_charge_allowed: list[bool] | None = None,
        export_bonus_prices: list[float] | None = None,
        export_bonus_cap_kwh: float | None = None,
        import_bonus_prices: list[float] | None = None,
        import_bonus_cap_kwh: float | None = None,
        schedule_timestamps: list[datetime] | None = None,
        priority_export_slots: list[bool] | None = None,
        disable_idle: bool = False,
    ) -> OptimizerResult:
        """
        Solve the LP formulation using the HiGHS solver (highspy).

        Variables per time step (4 * n total):
            x[0..n-1]   = grid_import[t]  (kW, >= 0)
            x[n..2n-1]  = grid_export[t]  (kW, >= 0)
            x[2n..3n-1] = battery_charge[t] (kW, >= 0)
            x[3n..4n-1] = battery_discharge[t] (kW, >= 0)
        """
        # If SOC is below the optimiser reserve, self-consumption can still use
        # the battery down to the hardware reserve. Keep the optimiser reserve
        # intact for forced export/discharge decisions, but suppress battery
        # export for this solve and lower only the physical SOC floor used by
        # the LP. This avoids force-charging solely to recover the optimiser
        # reserve while still treating it as the forced-discharge boundary.
        _soc_below_reserve = soc_0 < self.backup_reserve
        allow_battery_export = allow_battery_export or [True] * n
        import_bonus_prices = import_bonus_prices or [0.0] * n
        priority_export_slots = priority_export_slots or [False] * n
        terminal_weight_override: float | None = None
        if _soc_below_reserve:
            effective_reserve = self._natural_self_consumption_floor(soc_0)
            log = _LOGGER.info if self.suppress_reserve_warning else _LOGGER.warning
            log(
                "SOC (%.1f%%) below optimiser reserve (%.0f%%) — using "
                "hardware reserve %.1f%% as self-consumption floor",
                soc_0 * 100, self.backup_reserve * 100, effective_reserve * 100,
            )
            # Do not assign artificial end-of-horizon value to recovering the
            # optimiser reserve. Real import/export prices can still justify
            # charging, but ordinary self-use should be allowed to continue.
            # Pass the override as a solve-local parameter instead of mutating
            # self.terminal_weight: the solve runs in a worker thread while
            # config writers (update_config) run on the event loop, so a
            # save/restore around the solve can revert a concurrent write.
            terminal_weight_override = 0.0
            export_floor = max(
                self.backup_reserve,
                self._configured_export_reserve_floor(),
            )
            allow_battery_export = self._export_allowed_after_reserve_recovery(
                allow_battery_export,
                block_battery_charge or [False] * n,
                import_prices,
                export_prices,
                solar,
                load,
                soc_0,
                export_floor,
                allow_grid_charge,
                grid_charge_allowed or [True] * n,
                acquisition_cost_kwh,
                export_bonus_prices or [0.0] * n,
                priority_export_slots,
            )

        try:
            mode_slots: list[str] | None = None
            required_self_use_kw: list[float] | None = None
            last_result: OptimizerResult | None = None
            for iteration in range(MODE_PROJECTION_MAX_ITERATIONS):
                result = self._solve_lp_inner(
                    n, import_prices, export_prices, solar, load, soc_0,
                    cost_function,
                    acquisition_cost_kwh,
                    allow_battery_export,
                    block_battery_charge or [False] * n,
                    allow_grid_charge,
                    grid_charge_allowed or [True] * n,
                    export_bonus_prices or [0.0] * n,
                    export_bonus_cap_kwh,
                    import_bonus_prices,
                    import_bonus_cap_kwh,
                    schedule_timestamps,
                    terminal_weight_override=terminal_weight_override,
                    priority_export_slots=priority_export_slots,
                    disable_idle=disable_idle,
                    mode_slots=mode_slots,
                    required_self_use_kw=required_self_use_kw,
                )
                result.lp_stats["mode_iterations"] = iteration + 1
                if result.solver_used != "highs":
                    if last_result is not None:
                        _LOGGER.warning(
                            "Optimizer command-mode projection became infeasible on "
                            "pass %d; using the previous physically projected HiGHS plan",
                            iteration + 1,
                        )
                        last_result.lp_stats = {
                            **last_result.lp_stats,
                            "mode_iterations": iteration + 1,
                            "mode_converged": False,
                            "fallback_reason": (
                                "mode_projection_infeasible_projected_highs"
                            ),
                        }
                        return last_result
                    return result

                next_modes, next_required = self._schedule_mode_constraints(
                    result.schedule,
                    n,
                )
                if mode_slots is not None and self._mode_constraints_match(
                    mode_slots,
                    required_self_use_kw or [0.0] * n,
                    next_modes,
                    next_required,
                ):
                    result.lp_stats["mode_converged"] = True
                    return result

                mode_slots = next_modes
                required_self_use_kw = next_required
                last_result = result

            # Every pass is rendered through _build_schedule(), which caps the
            # chronological trajectory to the physical and export floors. If
            # the command projection cycles, retain that last feasible projected
            # HiGHS plan instead of replacing a valuable 48-hour plan with the
            # much less capable emergency greedy heuristic.
            _LOGGER.warning(
                "Optimizer command-mode projection did not converge after %d passes; "
                "using the last physically projected HiGHS plan",
                MODE_PROJECTION_MAX_ITERATIONS,
            )
            if last_result is None:
                return self._solve_greedy(
                    n, import_prices, export_prices, solar, load, soc_0,
                    cost_function,
                    acquisition_cost_kwh,
                    allow_battery_export,
                    block_battery_charge or [False] * n,
                    allow_grid_charge,
                    grid_charge_allowed or [True] * n,
                    export_bonus_prices or [0.0] * n,
                    export_bonus_cap_kwh,
                    import_bonus_prices,
                    import_bonus_cap_kwh,
                    schedule_timestamps,
                    priority_export_slots,
                    disable_idle,
                )
            last_result.lp_stats = {
                **last_result.lp_stats,
                "mode_iterations": MODE_PROJECTION_MAX_ITERATIONS,
                "mode_converged": False,
                "fallback_reason": "mode_non_convergence_projected_highs",
            }
            return last_result
        finally:
            if _soc_below_reserve:
                self._below_reserve_recovery_target = None
    def _solve_lp_inner(
        self,
        n: int,
        import_prices: list[float],
        export_prices: list[float],
        solar: list[float],
        load: list[float],
        soc_0: float,
        cost_function: str,
        acquisition_cost_kwh: float = 0.0,
        allow_battery_export: list[bool] | None = None,
        block_battery_charge: list[bool] | None = None,
        allow_grid_charge: bool = True,
        grid_charge_allowed: list[bool] | None = None,
        export_bonus_prices: list[float] | None = None,
        export_bonus_cap_kwh: float | None = None,
        import_bonus_prices: list[float] | None = None,
        import_bonus_cap_kwh: float | None = None,
        schedule_timestamps: list[datetime] | None = None,
        terminal_weight_override: float | None = None,
        priority_export_slots: list[bool] | None = None,
        disable_idle: bool = False,
        mode_slots: list[str | None] | None = None,
        required_self_use_kw: list[float] | None = None,
    ) -> OptimizerResult:
        """Inner LP solver (separated for SOC-below-reserve guard in _solve_lp)."""
        formulation_start = time.monotonic()
        eff = self.efficiency
        cap = self.capacity_kwh
        # Solve-local terminal weight so callers can override without mutating
        # shared instance state (thread-safe against concurrent config writes).
        terminal_weight = (
            self.terminal_weight
            if terminal_weight_override is None
            else terminal_weight_override
        )
        allow_battery_export = allow_battery_export or [True] * n
        block_battery_charge = block_battery_charge or [False] * n
        grid_charge_allowed = grid_charge_allowed or [True] * n
        export_bonus_prices = export_bonus_prices or [0.0] * n
        import_bonus_prices = import_bonus_prices or [0.0] * n
        priority_export_slots = priority_export_slots or [False] * n
        allow_grid_charge = bool(allow_grid_charge)
        periods = self._build_lp_periods(
            n,
            import_prices,
            export_prices,
            solar,
            load,
            allow_battery_export,
            block_battery_charge,
            grid_charge_allowed,
            export_bonus_prices,
            import_bonus_prices,
            priority_export_slots,
            mode_slots,
            required_self_use_kw,
        )
        p_n = len(periods)
        p_import = [period.import_price for period in periods]
        p_export = [period.export_price for period in periods]
        p_export_bonus = [period.export_bonus_price for period in periods]
        p_import_bonus = [period.import_bonus_price for period in periods]
        import_group_ids = list(self._quota_import_group_ids or [])
        export_group_ids = list(self._quota_export_group_ids or [])
        if len(import_group_ids) < n:
            import_group_ids.extend([None] * (n - len(import_group_ids)))
        if len(export_group_ids) < n:
            export_group_ids.extend([None] * (n - len(export_group_ids)))
        p_import_groups = [
            import_group_ids[period.start]
            if import_group_ids
            and all(
                import_group_ids[idx] == import_group_ids[period.start]
                for idx in range(period.start, period.end)
            )
            else None
            for period in periods
        ]
        p_export_groups = [
            export_group_ids[period.start]
            if export_group_ids
            and all(
                export_group_ids[idx] == export_group_ids[period.start]
                for idx in range(period.start, period.end)
            )
            else None
            for period in periods
        ]
        p_solar = [period.solar_kw for period in periods]
        p_load = [period.load_kw for period in periods]
        p_allow_export = [period.allow_battery_export for period in periods]
        p_block_charge = [period.block_battery_charge for period in periods]
        p_grid_charge_allowed = [period.grid_charge_allowed for period in periods]
        p_priority_export = [period.priority_export for period in periods]
        p_mode = [period.mode for period in periods]
        p_required_self_use = [period.required_self_use_kw for period in periods]
        p_dt = [period.slot_count * self.dt_hours for period in periods]
        p_effective_acquisition = self._effective_export_acquisition_costs(
            p_n,
            p_import,
            p_block_charge,
            allow_grid_charge,
            acquisition_cost_kwh,
            p_grid_charge_allowed,
        )

        def _priority_export_slot(t: int) -> bool:
            export_value = p_export[t] + p_export_bonus[t]
            return (
                p_priority_export[t]
                and p_allow_export[t]
                and export_value > 0.001
            )

        future_priority_recharge_cost = [float("inf")] * p_n
        best_future_recharge_cost = float("inf")
        import_bonus_active = bool(
            import_bonus_cap_kwh is not None
            and import_bonus_cap_kwh > 1e-6
            and any(price > 1e-6 for price in p_import_bonus)
        )
        for idx in range(p_n - 1, -1, -1):
            future_priority_recharge_cost[idx] = best_future_recharge_cost
            if (
                allow_grid_charge
                and p_grid_charge_allowed[idx]
                and not p_block_charge[idx]
            ):
                net_import_price = max(
                    0.0,
                    p_import[idx]
                    - (p_import_bonus[idx] if import_bonus_active else 0.0),
                )
                best_future_recharge_cost = min(
                    best_future_recharge_cost,
                    net_import_price / max(self.efficiency**2, 1e-9),
                )

        def _profitable_export_slot(t: int) -> bool:
            return (
                p_allow_export[t]
                and self._is_export_profitable(
                    p_export[t] + p_export_bonus[t],
                    p_import[t],
                    acquisition_cost_kwh,
                    p_effective_acquisition[t],
                )
            )

        future_self_consumption_values = self._future_self_consumption_values(
            p_n, p_import, p_solar, p_load
        )
        grid_charge_soc_cap = max(
            0.0,
            min(1.0, float(getattr(self, "grid_charge_soc_cap", 1.0) or 0.0)),
        )
        grid_charge_cap_active = allow_grid_charge and grid_charge_soc_cap < 0.999
        grid_charge_cap_headroom_kwh = max(
            0.0,
            (grid_charge_soc_cap - soc_0) * cap,
        )

        # Periods where the LP pins battery_charge to zero (see charge bounds
        # below): explicitly blocked windows, or export-profitable slots with
        # no future self-consumption value. No charge — solar or grid — can
        # enter the battery here, so these periods contribute nothing to the
        # reachable SOC used for feasibility caps and prefill ceilings.
        charge_pinned_periods = []
        for t in range(p_n):
            export_profitable_slot = _profitable_export_slot(t)
            charge_pinned_periods.append(
                p_mode[t] in ("export", "idle")
                or p_block_charge[t]
                or _priority_export_slot(t)
                or (
                    export_profitable_slot
                    and not future_self_consumption_values[t]
                )
            )

        # Best-case reachable SOC at each period boundary starting from soc_0,
        # charging at the permitted limit in every non-pinned period. Used to
        # cap hard SOC floors so they never exceed what is physically reachable
        # (an uncapped floor above reachable SOC makes the whole LP infeasible).
        max_reachable_soc = [min(1.0, soc_0)] * (p_n + 1)
        _reach = min(1.0, soc_0)
        for t in range(p_n):
            if charge_pinned_periods[t]:
                charge_kw = 0.0
            else:
                charge_kw = self._charge_limit_kw(
                    p_load[t],
                    p_solar[t],
                    (
                        False
                        if p_mode[t] == "self_use"
                        else allow_grid_charge and p_grid_charge_allowed[t]
                    ),
                )
            _reach = min(1.0, _reach + charge_kw * eff * p_dt[t] / cap)
            max_reachable_soc[t + 1] = _reach

        optimizer_reserve = max(0.0, min(1.0, self.backup_reserve))
        # The ordinary energy bound is the physical/natural floor. The
        # optimizer reserve is applied separately to slots that actually emit
        # a forced battery-export command; merely permitting export must not
        # make the software reserve a global hold target.
        self_consumption_floor = self._natural_self_consumption_floor(soc_0)
        reserve_floor = [self_consumption_floor] * (p_n + 1)
        recovery_target = self._below_reserve_recovery_target
        if recovery_target is not None and recovery_target > self_consumption_floor:
            max_reachable = soc_0
            reserve_floor[0] = soc_0
            for t in range(p_n):
                reachable_charge_kw = (
                    0.0
                    if p_block_charge[t]
                    else self._charge_limit_kw(
                        p_load[t],
                        p_solar[t],
                        allow_grid_charge and p_grid_charge_allowed[t],
                    )
                )
                max_reachable = min(
                    recovery_target,
                    max_reachable + reachable_charge_kw * eff * p_dt[t] / cap,
                )
                reserve_floor[t + 1] = max(self_consumption_floor, max_reachable)

        # The export floor is an end-of-window boundary condition, not a floor
        # that later periods' self-consumption must respect. Snapshot the base
        # floor (self-consumption + recovery target only) before the export
        # raises so the intra-period discharge rows below do not carry a raised
        # export floor into the period that follows an export window.
        base_reserve_floor = list(reserve_floor)

        # Even when a solve starts below the optimiser reserve and self-use is
        # allowed down to the hardware floor, forced battery export must still
        # respect the user's optimiser reserve once export is allowed again.
        export_reserve_floor = max(
            optimizer_reserve,
            self._configured_export_reserve_floor(),
        )
        if export_reserve_floor > self_consumption_floor:
            for t, allow_export in enumerate(p_allow_export):
                period_export_floor = max(
                    optimizer_reserve,
                    self._configured_export_reserve_floor_for_range(
                        periods[t].start,
                        periods[t].end,
                    ),
                )
                if allow_export and p_mode[t] == "export":
                    # Cap at physically reachable SOC (0.5% buffer) so an
                    # export floor above what charging can reach cannot make
                    # the whole LP infeasible and collapse the horizon to a
                    # self-consumption hold.
                    reachable_cap = max(
                        self_consumption_floor,
                        max_reachable_soc[t + 1] - 0.005,
                    )
                    reserve_floor[t + 1] = max(
                        reserve_floor[t + 1],
                        min(period_export_floor, reachable_cap),
                    )

        # Boundary-energy state model: power variables per period, battery energy
        # variables at period boundaries. This removes the dense cumulative SOC
        # rows that made the 48h/5min model expensive to build and solve.
        bonus_export_active = (
            export_bonus_cap_kwh is not None
            and export_bonus_cap_kwh > 1e-6
            and any(price > 1e-6 for price in p_export_bonus)
        )
        bonus_export_periods = [
            idx for idx, price in enumerate(p_export_bonus) if price > 1e-6
        ]
        bonus_import_active = (
            import_bonus_cap_kwh is not None
            and import_bonus_cap_kwh > 1e-6
            and any(price > 1e-6 for price in p_import_bonus)
        )
        bonus_import_periods = [
            idx for idx, price in enumerate(p_import_bonus) if price > 1e-6
        ]
        grid_charge_offset = 4 * p_n
        curtail_offset = 5 * p_n
        next_offset = 6 * p_n
        bonus_export_offset = next_offset
        if bonus_export_active:
            next_offset += p_n
        bonus_import_offset = next_offset
        if bonus_import_active:
            next_offset += p_n
        direction_binary_active = bool(
            getattr(self, "_prevent_simultaneous_grid_flow", False)
        )
        max_grid_kw = (
            max(0.0, self.max_grid_import_w / 1000.0)
            if self.max_grid_import_w is not None
            else 100.0
        )
        grid_direction_offset = next_offset
        if direction_binary_active:
            next_offset += p_n
        energy_offset = next_offset
        num_vars = energy_offset + p_n + 1

        def grid_import_var(t: int) -> int:
            return t

        def grid_export_var(t: int) -> int:
            return p_n + t

        def charge_var(t: int) -> int:
            return 2 * p_n + t

        def discharge_var(t: int) -> int:
            return 3 * p_n + t

        def grid_charge_var(t: int) -> int:
            return grid_charge_offset + t

        def curtail_var(t: int) -> int:
            return curtail_offset + t

        def bonus_export_var(t: int) -> int:
            return bonus_export_offset + t

        def bonus_import_var(t: int) -> int:
            return bonus_import_offset + t

        def energy_var(t: int) -> int:
            return energy_offset + t

        def grid_direction_var(t: int) -> int:
            return grid_direction_offset + t

        # === Objective function: cost minimization ===
        # minimize SUM(import_price * grid_import - export_price * grid_export) * dt
        c = [0.0] * num_vars
        # Tiny time-preference epsilon to break LP degeneracy.  When multiple
        # timesteps have the same price (e.g. flat TOU rate across a window),
        # the LP is indifferent about which ones to use and HiGHS may scatter
        # actions across non-contiguous timesteps (charge-SC-charge-SC…).
        # Adding a monotonic epsilon concentrates actions into contiguous blocks:
        #   - Exports: prefer earlier (decreasing eps) → discharge first, then SC
        #   - Imports/charging: depends on whether a SOC deadline is binding
        # 1e-7 per step is ~5e-5 across 576 steps — negligible vs real prices.
        eps = 1e-7

        # Deadline mode: when pre_window_soc_target is binding (e.g. must reach
        # 100% before today's Flow Power Happy Hour), flip the import bias so
        # ties resolve to EARLIER charging. Do the same when the battery is at
        # the reserve floor: waiting until the end of a flat cheap window leaves
        # no margin for inverter latency, BMS taper, or forecast jitter.
        # Solar-SC users with useful SOC and no deadline keep the prefer-later
        # default so grid imports happen after solar has had a chance to fill
        # the battery.
        deadline_mode = (
            allow_grid_charge
            and (
                (
                    self.pre_window_slot is not None
                    and self.pre_window_slot > 0
                    and self.pre_window_soc_target > 0.0
                )
                or soc_0 <= self.backup_reserve + 0.02
            )
        )

        # Pre-compute free charging bonus: use median non-free import price
        # so the LP sees free charging as "saving" that future import cost.
        # See use_per_kwh_terminal field: legacy form divides by `cap` (a unit
        # error; attenuates the bonus to noise on large batteries), corrected
        # form drops the `/cap`. _build_schedule has a hard override that
        # forces max charge during 0c periods regardless of solver output —
        # the corrected coefficient just lets the LP arrive at the same
        # answer through its own economics.
        _nonzero_prices = sorted(p for p in p_import if p > 0.01)
        _terminal_unit_divisor = 1.0 if self.use_per_kwh_terminal else cap
        _free_charge_bonus = (
            _nonzero_prices[len(_nonzero_prices) // 2] * eff / _terminal_unit_divisor
            if _nonzero_prices else 0.0
        )

        for t in range(p_n):
            # Import/charge tie-breaker: prefer EARLIER when a deadline is
            # binding, prefer LATER otherwise (see deadline_mode comment above).
            import_eps = eps * (t if deadline_mode else (p_n - t))
            c[grid_import_var(t)] = (p_import[t] + import_eps) * p_dt[t]
            # Prefer direct grid supply over a price-identical battery cycle.
            # This is only a deterministic tie-breaker (0.001 c/kWh per side),
            # not a degradation-cost model.
            c[charge_var(t)] += 1e-5 * p_dt[t]
            c[discharge_var(t)] += 1e-5 * p_dt[t]
            if p_export[t] > 0:
                c[grid_export_var(t)] = -(
                    p_export[t] + eps * (p_n - t)
                ) * p_dt[t]  # grid_export: prefer earlier
            elif bonus_export_active and p_export_bonus[t] > 0:
                # ZeroHero-style capped bonuses make otherwise-zero exports
                # valuable only through the linked bonus variable below.
                c[grid_export_var(t)] = 0.0
            else:
                # Exporting at 0c costs the same as importing — any energy pushed out
                # at 0c must be bought back at the import rate, so it's never worthwhile
                # to intentionally discharge for 0c export (e.g. Flow Power non-happy-hour).
                c[grid_export_var(t)] = max(0.01, p_import[t]) * p_dt[t]

            # Free electricity: strongly incentivize charging.
            # Without this, the LP may idle during free windows because
            # the near-zero import cost doesn't overcome terminal valuation.
            if p_import[t] <= 0.001 and _free_charge_bonus > 0:
                c[charge_var(t)] -= _free_charge_bonus * p_dt[t]

            if bonus_export_active and p_export_bonus[t] > 0:
                c[bonus_export_var(t)] = -(
                    p_export_bonus[t] + eps * (p_n - t)
                ) * p_dt[t]

            if bonus_import_active and p_import_bonus[t] > 0:
                # ZeroCharge-style capped import credits reduce the settlement
                # cost of grid import without mutating the base tariff price.
                c[bonus_import_var(t)] = -(
                    p_import_bonus[t] + eps * (p_n - t)
                ) * p_dt[t]

            if _profitable_export_slot(t) or _priority_export_slot(t):
                # During an explicit export window, prefer serving concurrent
                # household load from the battery instead of importing for load
                # while exporting only the capped command amount.
                import_penalty = 0.02
                # Close the phantom import->export loop: with solar surplus and
                # an export price above the import price, the export-backing
                # constraint (export - discharge <= surplus) lets the LP charge
                # the surplus into the battery AND export the same surplus,
                # booking simultaneous grid import and export of one kWh. That
                # only pays when export exceeds import, so raise the import
                # penalty to the full spread in exactly those slots to remove
                # the fictitious revenue without distorting other periods.
                surplus_kw = p_solar[t] - p_load[t]
                phantom_spread = (p_export[t] + p_export_bonus[t]) - p_import[t]
                if surplus_kw > 1e-6 and phantom_spread > import_penalty:
                    import_penalty = phantom_spread + 0.001
                c[grid_import_var(t)] += import_penalty * p_dt[t]

            # Real systems can curtail solar when the battery cannot accept
            # charge and the DNSP/export cap is binding. Penalize curtailment
            # at the better of avoided import or export value so the LP only
            # uses it after available charge/export outlets are exhausted.
            c[curtail_var(t)] = max(0.01, p_import[t], p_export[t]) * p_dt[t]
            if grid_charge_cap_active:
                # Keep the grid-charge accounting variable at its minimum
                # feasible value so the cap constrains real grid-to-battery
                # energy, not arbitrary solver slack.
                c[grid_charge_var(t)] = 1e-9 * p_dt[t]

        # === Terminal valuation: incentivize keeping charge at end of horizon ===
        # Use the cheapest available recharge price as the replacement cost.
        # The battery will recharge during the cheapest period in the horizon,
        # so min is the correct marginal cost. Using median over-penalizes
        # discharge when free/cheap charging windows exist (e.g. GloBird
        # FOUR4FREE has 4 hours at 0c — median would be ~31c, causing the LP
        # to prefer grid import over battery discharge at 31c partial-peak
        # because the efficiency-adjusted penalty 31/0.9=34.4c > 31c import).
        #
        # Solar recharging: when solar is available, the battery can recharge
        # at the opportunity cost of export (foregone export revenue), which
        # is typically much cheaper than grid import. Without this, flat-rate
        # users see terminal_price = import_price, making the efficiency-
        # adjusted penalty > import_price, so the LP prefers IDLE (grid
        # import) over self-consumption — exactly wrong.
        # "Second half of horizon" must be the time midpoint, not the period
        # midpoint: tiered aggregation packs many short periods into the first
        # 6h, so p_n // 2 lands only a few hours in. Map the base-slot time
        # midpoint (n // 2) to its period index instead.
        half_n = self._period_index_for_base_slot(periods, n // 2)
        second_half_prices = p_import[half_n:] if half_n < p_n else p_import
        min_grid_recharge = min(second_half_prices) if second_half_prices else 0.0

        # Check if solar can recharge the battery in the second half of horizon.
        # If so, the marginal recharge cost is the export price (opportunity cost).
        solar_recharge_costs = [
            p_export[t]
            for t in range(half_n, p_n)
            if p_solar[t] > 0.1  # Meaningful solar available
        ]
        if solar_recharge_costs:
            min_solar_recharge = min(solar_recharge_costs)
            terminal_price = max(0.001, min(min_grid_recharge, min_solar_recharge))
        else:
            terminal_price = max(0.001, min_grid_recharge) if min_grid_recharge > 0 else 0.0

        # Floor: even when recharging is free (e.g. GloBird SUPER_OFF_PEAK 0c),
        # round-trip efficiency losses mean discharge isn't free. Use a minimum
        # terminal price so the LP doesn't dump battery energy at 0c sell price
        # just because it can recharge for free later.
        if terminal_price < 0.01:
            # Use efficiency-adjusted median import as minimum replacement cost.
            # This reflects the real cost of the energy already stored.
            all_nonzero = [p for p in p_import if p > 0.01]
            if all_nonzero:
                median_price = sorted(all_nonzero)[len(all_nonzero) // 2]
                terminal_price = max(terminal_price, median_price * (1 - eff))

        terminal_price *= terminal_weight

        if terminal_price > 0:
            # See use_per_kwh_terminal field for the unit-error history.
            # Correct coefficients are terminal_price * eff * dt (no /cap):
            # terminal_price is $/kWh, so a per-kW objective coefficient over
            # dt hours produces $ — adding /cap would give $·h/kWh², garbage.
            # Solar-equipped users see no behavior change because solar
            # export sets terminal_price low (~5c FiT), keeping the
            # discharge penalty well under avoided-import savings.
            for t in range(p_n):
                # Charging adds SOC → subtract cost (incentivize keeping charge)
                c[charge_var(t)] -= terminal_price * eff * p_dt[t] / _terminal_unit_divisor
                # Discharging removes SOC → add cost (penalize draining)
                c[discharge_var(t)] += terminal_price * p_dt[t] / (eff * _terminal_unit_divisor)

        # === Equality constraints: power balance ===
        # solar[t] + grid_import[t] + battery_discharge[t] =
        # load[t] + grid_export[t] + battery_charge[t] + solar_curtail[t]
        # Rearranged:
        # grid_import[t] - grid_export[t] - battery_charge[t]
        # + battery_discharge[t] - solar_curtail[t] = load[t] - solar[t]
        A_eq = _LpMatrix((2 * p_n, num_vars), dtype=float)
        b_eq = [0.0] * (2 * p_n)

        for t in range(p_n):
            A_eq[t, grid_import_var(t)] = 1.0
            A_eq[t, grid_export_var(t)] = -1.0
            A_eq[t, charge_var(t)] = -1.0
            A_eq[t, discharge_var(t)] = 1.0
            A_eq[t, curtail_var(t)] = -1.0
            b_eq[t] = p_load[t] - p_solar[t]

            # Energy transition: E[t+1] = E[t] + charge*eff*dt - discharge*dt/eff
            row = p_n + t
            A_eq[row, energy_var(t + 1)] = 1.0
            A_eq[row, energy_var(t)] = -1.0
            A_eq[row, charge_var(t)] = -eff * p_dt[t]
            A_eq[row, discharge_var(t)] = p_dt[t] / eff

        # Priority/provider status is permission, not a synthetic subsidy. When
        # export is below the modeled acquisition cost, allow it only when real
        # future charge is both cheap enough and explicitly paired by a linear
        # constraint below. This keeps a one-kWh rebate or charge slot from
        # authorising an entire window of below-cost battery export.
        paired_priority_recharge_periods: dict[int, list[int]] = {}
        for t in range(p_n):
            export_value = p_export[t] + p_export_bonus[t]
            if (
                not _priority_export_slot(t)
                or acquisition_cost_kwh <= 0
                or export_value + 1e-9 >= p_effective_acquisition[t]
            ):
                continue
            eligible_recharge = []
            for future_idx in range(t + 1, p_n):
                if (
                    not allow_grid_charge
                    or not p_grid_charge_allowed[future_idx]
                    or p_block_charge[future_idx]
                    or p_mode[future_idx] not in (None, "charge")
                ):
                    continue
                net_import_price = max(
                    0.0,
                    p_import[future_idx]
                    - (
                        p_import_bonus[future_idx]
                        if bonus_import_active
                        else 0.0
                    ),
                )
                if net_import_price <= export_value * eff * eff + 1e-9:
                    eligible_recharge.append(future_idx)
            if eligible_recharge:
                paired_priority_recharge_periods[t] = eligible_recharge

        pre_window_boundary: int | None = None
        pre_window_effective_target: float | None = None
        A_ub_rows = 2 * p_n
        if bonus_export_active:
            A_ub_rows += 2 * len(bonus_export_periods) + 1
        if bonus_import_active:
            A_ub_rows += len(bonus_import_periods) + 1
        if direction_binary_active:
            A_ub_rows += 2 * p_n
        if grid_charge_cap_active:
            A_ub_rows += 3 * p_n + 1
        if paired_priority_recharge_periods:
            A_ub_rows += len(paired_priority_recharge_periods) + 1
        if (
            allow_grid_charge
            and self.pre_window_slot is not None
            and self.pre_window_slot > 0
            and self.pre_window_slot <= n
            and self.pre_window_soc_target > 0.0
            and not getattr(self, "_relaxing", False)
        ):
            pre_window_boundary = self._period_index_for_base_slot(
                periods, self.pre_window_slot
            )
            if pre_window_boundary > 0:
                slots_to_window = pre_window_boundary

                def _deadline_charge_limit_kw(t: int) -> float:
                    export_profitable_slot = _profitable_export_slot(t)
                    if (
                        p_block_charge[t]
                        or _priority_export_slot(t)
                        or (
                            export_profitable_slot
                            and not future_self_consumption_values[t]
                        )
                    ):
                        return 0.0
                    return self._charge_limit_kw(
                        p_load[t],
                        p_solar[t],
                        allow_grid_charge and p_grid_charge_allowed[t],
                    )

                max_stored_kwh = 0.0
                remaining_grid_stored_kwh = grid_charge_cap_headroom_kwh
                for t in range(slots_to_window):
                    charge_limit_kw = _deadline_charge_limit_kw(t)
                    if charge_limit_kw <= 0:
                        continue
                    solar_surplus_kw = max(0.0, p_solar[t] - p_load[t])
                    solar_charge_kw = min(charge_limit_kw, solar_surplus_kw)
                    max_stored_kwh += solar_charge_kw * eff * p_dt[t]
                    grid_charge_kw = max(0.0, charge_limit_kw - solar_charge_kw)
                    if grid_charge_kw <= 0:
                        continue
                    grid_stored_kwh = grid_charge_kw * eff * p_dt[t]
                    if grid_charge_cap_active:
                        grid_stored_kwh = min(
                            grid_stored_kwh,
                            remaining_grid_stored_kwh,
                        )
                        remaining_grid_stored_kwh = max(
                            0.0,
                            remaining_grid_stored_kwh - grid_stored_kwh,
                        )
                    max_stored_kwh += grid_stored_kwh

                max_soc_gain = max_stored_kwh / cap
                max_reachable = min(1.0, soc_0 + max_soc_gain)
                # Keep the established 0.5% feasibility margin while the
                # configured target is reachable. Once execution has fallen
                # behind that target, use only a numerical margin: granting a
                # fresh 0.5% on every rolling solve ratchets the deadline down.
                reachability_margin = (
                    PRE_WINDOW_REACHABLE_TARGET_MARGIN_SOC
                    if self.pre_window_soc_target <= max_reachable + 1e-9
                    else PRE_WINDOW_REACHABILITY_MARGIN_SOC
                )
                pre_window_effective_target = min(
                    self.pre_window_soc_target,
                    max_reachable - reachability_margin,
                )
                A_ub_rows += 1

        A_ub = _LpMatrix((A_ub_rows, num_vars), dtype=float)
        b_ub: list[float] = []

        for t in range(p_n):
            # Prevent current-period charge from funding same-period discharge.
            # Use the base floor (not the export-raised boundary floor) so the
            # period after an export window can still self-consume below that
            # window's transient export floor.
            A_ub[len(b_ub), discharge_var(t)] = p_dt[t] / eff
            A_ub[len(b_ub), energy_var(t)] = -1.0
            b_ub.append(-base_reserve_floor[t] * cap)

            # Export must be backed by physical energy from solar surplus or
            # battery discharge.
            A_ub[len(b_ub), grid_export_var(t)] = 1.0
            A_ub[len(b_ub), discharge_var(t)] = -1.0
            b_ub.append(max(0.0, p_solar[t] - p_load[t]))

            if direction_binary_active:
                # y=1 selects import, y=0 selects export.  This conditional
                # MILP guard is used only for quota tariffs where import can
                # be cheaper than export and passthrough arbitrage would
                # otherwise be mathematically profitable.
                slot_export_limit_kw = self._grid_export_limit_kw_for_range(
                    periods[t].start,
                    periods[t].end,
                    default_kw=100.0,
                )
                A_ub[len(b_ub), grid_import_var(t)] = 1.0
                A_ub[len(b_ub), grid_direction_var(t)] = -max_grid_kw
                b_ub.append(0.0)
                A_ub[len(b_ub), grid_export_var(t)] = 1.0
                A_ub[len(b_ub), grid_direction_var(t)] = slot_export_limit_kw
                b_ub.append(slot_export_limit_kw)

        if paired_priority_recharge_periods:
            # Per-period pairing prevents a cheap slot before a later export
            # from being counted as its replacement.
            for export_idx, recharge_indices in (
                paired_priority_recharge_periods.items()
            ):
                A_ub[len(b_ub), discharge_var(export_idx)] = p_dt[export_idx]
                for recharge_idx in recharge_indices:
                    A_ub[len(b_ub), charge_var(recharge_idx)] = (
                        -eff * eff * p_dt[recharge_idx]
                    )
                b_ub.append(
                    max(0.0, p_load[export_idx] - p_solar[export_idx])
                    * p_dt[export_idx]
                )

            # The aggregate row prevents one future charge kWh from backing
            # multiple earlier below-acquisition priority exports.
            paired_recharge_union: set[int] = set()
            paired_home_kwh = 0.0
            for export_idx, recharge_indices in (
                paired_priority_recharge_periods.items()
            ):
                A_ub[len(b_ub), discharge_var(export_idx)] = p_dt[export_idx]
                paired_home_kwh += (
                    max(0.0, p_load[export_idx] - p_solar[export_idx])
                    * p_dt[export_idx]
                )
                paired_recharge_union.update(recharge_indices)
            for recharge_idx in paired_recharge_union:
                A_ub[len(b_ub), charge_var(recharge_idx)] = (
                    -eff * eff * p_dt[recharge_idx]
                )
            b_ub.append(paired_home_kwh)

        if bonus_export_active:
            for t in bonus_export_periods:
                # Only physical exports can consume the capped ZeroHero bucket.
                A_ub[len(b_ub), bonus_export_var(t)] = 1.0
                A_ub[len(b_ub), grid_export_var(t)] = -1.0
                b_ub.append(0.0)

            for t in bonus_export_periods:
                # Intentional battery export must fit inside the bonus bucket.
                # Solar surplus may still export at the base FiT outside it.
                A_ub[len(b_ub), grid_export_var(t)] = 1.0
                A_ub[len(b_ub), bonus_export_var(t)] = -1.0
                b_ub.append(max(0.0, p_solar[t] - p_load[t]))

            export_group_caps = self._quota_export_caps_by_group
            if export_group_caps and any(p_export_groups):
                for group_id, cap_kwh in export_group_caps.items():
                    for t in bonus_export_periods:
                        if p_export_groups[t] == group_id:
                            A_ub[len(b_ub), bonus_export_var(t)] = p_dt[t]
                    b_ub.append(max(0.0, float(cap_kwh)))
            else:
                for t in bonus_export_periods:
                    A_ub[len(b_ub), bonus_export_var(t)] = p_dt[t]
                b_ub.append(max(0.0, float(export_bonus_cap_kwh or 0.0)))

        if bonus_import_active:
            for t in bonus_import_periods:
                # Only physical grid imports can consume the capped
                # ZeroCharge/free-import bucket.
                A_ub[len(b_ub), bonus_import_var(t)] = 1.0
                A_ub[len(b_ub), grid_import_var(t)] = -1.0
                b_ub.append(0.0)

            import_group_caps = self._quota_import_caps_by_group
            if import_group_caps and any(p_import_groups):
                for group_id, cap_kwh in import_group_caps.items():
                    for t in bonus_import_periods:
                        if p_import_groups[t] == group_id:
                            A_ub[len(b_ub), bonus_import_var(t)] = p_dt[t]
                    b_ub.append(max(0.0, float(cap_kwh)))
            else:
                for t in bonus_import_periods:
                    A_ub[len(b_ub), bonus_import_var(t)] = p_dt[t]
                b_ub.append(max(0.0, float(import_bonus_cap_kwh or 0.0)))

        if grid_charge_cap_active:
            for t in range(p_n):
                # grid_charge[t] <= battery_charge[t]
                A_ub[len(b_ub), grid_charge_var(t)] = 1.0
                A_ub[len(b_ub), charge_var(t)] = -1.0
                b_ub.append(0.0)

                # grid_charge[t] <= grid_import[t]
                A_ub[len(b_ub), grid_charge_var(t)] = 1.0
                A_ub[len(b_ub), grid_import_var(t)] = -1.0
                b_ub.append(0.0)

                # battery_charge[t] - grid_charge[t] <= available solar surplus.
                # This lets solar charge above the cap while every kW of charge
                # beyond exogenous surplus is counted as grid-to-battery energy.
                A_ub[len(b_ub), charge_var(t)] = 1.0
                A_ub[len(b_ub), grid_charge_var(t)] = -1.0
                b_ub.append(max(0.0, p_solar[t] - p_load[t]))

            for t in range(p_n):
                A_ub[len(b_ub), grid_charge_var(t)] = eff * p_dt[t]
            b_ub.append(grid_charge_cap_headroom_kwh)

        # === Pre-window SOC floor ===
        # Force soc[pre_window_slot - 1] >= target so the battery is filled
        # before a known high-value export window (e.g. Flow Power Happy Hour).
        # The 48 h rolling horizon otherwise places grid-charge slots at the
        # globally cheapest periods, which often misses today's HH entirely.
        # Cap target at what's physically reachable to keep the LP feasible.
        if pre_window_boundary is not None and pre_window_boundary > 0:
            if (
                pre_window_effective_target is not None
                and pre_window_effective_target > soc_0
            ):
                A_ub[len(b_ub), energy_var(pre_window_boundary)] = -1.0
                b_ub.append(-pre_window_effective_target * cap)
                _LOGGER.debug(
                    "Pre-window SOC floor: target=%.1f%% (capped from %.1f%%) "
                    "at slot %d (%.1f h ahead), current=%.1f%%",
                    pre_window_effective_target * 100,
                    self.pre_window_soc_target * 100,
                    self.pre_window_slot,
                    sum(p_dt[:pre_window_boundary]),
                    soc_0 * 100,
                )
            else:
                # Keep A_ub row count aligned with b_ub when the pre-window
                # request is already satisfied by current SOC.
                b_ub.append(0.0)

        solar_prefill_ceilings = self._pre_window_solar_prefill_ceilings(
            pre_window_boundary=pre_window_boundary,
            target_soc=pre_window_effective_target,
            solar=p_solar,
            load=p_load,
            dt_hours=p_dt,
            reserve_floor=reserve_floor,
            current_soc=soc_0,
            charge_pinned=charge_pinned_periods,
        )

        # === Variable bounds ===
        # Cap grid at 100 kW by default (generous safety limit; prevents
        # unbounded LP if a price accidentally goes negative or zero). Sites
        # with a known DNSP/export limit override the export side so the LP
        # models the same physical cap the runtime controller will enforce.
        period_grid_export_limits_kw = [
            self._grid_export_limit_kw_for_range(
                period.start,
                period.end,
                default_kw=100.0,
            )
            for period in periods
        ]

        def _export_acquisition_threshold(t: int) -> float:
            threshold = p_effective_acquisition[t]
            if _priority_export_slot(t):
                threshold = min(
                    threshold,
                    future_priority_recharge_cost[t],
                )
            return threshold

        bounds = []
        for t in range(p_n):
            bounds.append((0, max_grid_kw))  # grid_import

        # Grid export is always allowed for solar surplus. When battery export is
        # disabled, cap export to exogenous surplus so the LP cannot invent
        # grid-import -> grid-export or battery -> grid arbitrage.
        for t in range(p_n):
            max_grid_export_kw = period_grid_export_limits_kw[t]
            if p_mode[t] is not None and p_mode[t] != "export":
                solar_surplus_kw = max(0.0, p_solar[t] - p_load[t])
                bounds.append((0, min(max_grid_export_kw, solar_surplus_kw)))
                continue
            export_profitable_slot = _profitable_export_slot(t)
            priority_export_slot = _priority_export_slot(t)
            future_self_consumption_value = future_self_consumption_values[t]
            suppress_generic_battery_export = (
                export_profitable_slot
                and future_self_consumption_value
                and not priority_export_slot
                and not p_block_charge[t]
            )
            if p_allow_export[t] and not suppress_generic_battery_export:
                export_limit_kw = max_grid_export_kw
                if self.max_battery_export_kw is not None:
                    solar_surplus_kw = max(0.0, p_solar[t] - p_load[t])
                    export_limit_kw = min(
                        export_limit_kw,
                        solar_surplus_kw + self.max_battery_export_kw,
                    )
                bounds.append((0, export_limit_kw))  # grid_export
            else:
                solar_surplus_kw = max(0.0, p_solar[t] - p_load[t])
                bounds.append((0, min(max_grid_export_kw, solar_surplus_kw)))

        for t in range(p_n):
            if p_mode[t] in ("export", "idle"):
                bounds.append((0, 0.0))
                continue
            if p_mode[t] == "self_use":
                bounds.append((
                    0,
                    0.0
                    if p_block_charge[t]
                    else self._charge_limit_kw(
                        p_load[t],
                        p_solar[t],
                        False,
                    ),
                ))
                continue
            export_profitable_slot = _profitable_export_slot(t)
            priority_export_slot = _priority_export_slot(t)
            future_self_consumption_value = future_self_consumption_values[t]
            if p_block_charge[t] or priority_export_slot or (
                export_profitable_slot and not future_self_consumption_value
            ):
                # Do not charge during explicitly blocked export windows
                # (for example fixed Flow Power Happy Hour export windows).
                # A generic positive FiT is not enough to block charging:
                # Octopus IOG can have 6.9p import and 12p export across the
                # whole off-peak window. Permit charging there only when it has
                # later self-consumption value, not for grid-import->export
                # passthrough.
                bounds.append((0, 0.0))
            elif not allow_grid_charge:
                bounds.append((
                    0,
                    self._charge_limit_kw(
                        p_load[t], p_solar[t], allow_grid_charge
                    ),
                ))
            else:
                bounds.append((
                    0,
                    self._charge_limit_kw(
                        p_load[t],
                        p_solar[t],
                        p_grid_charge_allowed[t],
                    ),
                ))  # battery_charge

        for t in range(p_n):
            if p_mode[t] in ("charge", "idle"):
                bounds.append((0, 0.0))
                continue
            if p_mode[t] == "self_use":
                net_load_kw = max(0.0, p_load[t] - p_solar[t])
                upper = min(self.max_discharge_kw, net_load_kw)
                lower = min(
                    upper,
                    max(0.0, p_required_self_use[t]),
                )
                bounds.append((lower, upper))
                continue
            export_profitable_slot = _profitable_export_slot(t)
            priority_export_slot = _priority_export_slot(t)
            future_self_consumption_value = future_self_consumption_values[t]
            suppress_generic_battery_export = (
                export_profitable_slot
                and future_self_consumption_value
                and not priority_export_slot
                and not p_block_charge[t]
            )
            restrict_to_self_consumption = (
                suppress_generic_battery_export
                or not p_allow_export[t]
                or (
                    acquisition_cost_kwh > 0
                    and (p_export[t] + p_export_bonus[t])
                    < _export_acquisition_threshold(t)
                )
            )
            if restrict_to_self_consumption:
                # Allow discharge only for self-consumption (serving home load)
                net_load_kw = max(0.0, p_load[t] - p_solar[t])
                max_self_consumption = net_load_kw
                bounds.append((0, min(self.max_discharge_kw, max_self_consumption)))
            elif self.max_battery_export_kw is not None:
                # Target-export batteries receive a grid-export power command.
                # The battery still has to cover local load before any surplus
                # reaches the grid, so do not let the command cap masquerade as
                # a total battery-discharge cap during export windows.
                net_load_kw = max(0.0, p_load[t] - p_solar[t])
                bounds.append((
                    0,
                    min(
                        self.max_discharge_kw,
                        net_load_kw + self.max_battery_export_kw,
                    ),
                ))
            else:
                bounds.append((0, self.max_discharge_kw))  # battery_discharge

        for t in range(p_n):
            if not grid_charge_cap_active:
                bounds.append((0, 0.0))
                continue
            if p_block_charge[t] or not p_grid_charge_allowed[t]:
                bounds.append((0, 0.0))
            else:
                bounds.append((
                    0,
                    self._charge_limit_kw(
                        p_load[t],
                        p_solar[t],
                        p_grid_charge_allowed[t],
                    ),
                ))

        for t in range(p_n):
            bounds.append((0, max(0.0, p_solar[t])))  # solar_curtail

        if bonus_export_active:
            for t in range(p_n):
                bonus_limit_kw = (
                    period_grid_export_limits_kw[t]
                    if p_export_bonus[t] > 0
                    else 0.0
                )
                bounds.append((0, bonus_limit_kw))

        if bonus_import_active:
            for t in range(p_n):
                bonus_limit_kw = max_grid_kw if p_import_bonus[t] > 0 else 0.0
                bounds.append((0, bonus_limit_kw))

        if direction_binary_active:
            for _t in range(p_n):
                bounds.append((0.0, 1.0))

        bounds.append((soc_0 * cap, soc_0 * cap))
        for t in range(1, p_n + 1):
            upper_soc = solar_prefill_ceilings[t]
            upper = cap if upper_soc is None else upper_soc * cap
            lower = reserve_floor[t] * cap
            bounds.append((lower, max(lower, upper)))

        A_eq = A_eq.tocsr()
        A_ub = A_ub.tocsr()
        formulation_time_s = time.monotonic() - formulation_start

        # === Solve ===
        _LOGGER.debug(
            "Solving LP: %d base steps, %d periods, %d variables, %d constraints, "
            "%d nonzeros, %.0fs limit",
            n,
            p_n,
            num_vars,
            A_eq.shape[0] + A_ub.shape[0],
            A_eq.nnz + A_ub.nnz,
            LP_SOLVER_TIME_LIMIT_SECONDS,
        )

        solver_start = time.monotonic()
        solve_args = (
            c,
            A_ub,
            b_ub,
            A_eq,
            b_eq,
            bounds,
        )
        if direction_binary_active:
            result = _solve_lp_highs(
                *solve_args,
                time_limit=LP_SOLVER_TIME_LIMIT_SECONDS,
                integer_indices=range(
                    grid_direction_offset,
                    grid_direction_offset + p_n,
                ),
            )
        else:
            # Preserve the established wrapper call contract for every tariff
            # that does not need mixed-integer grid-direction exclusion.
            result = _solve_lp_highs(
                *solve_args,
                time_limit=LP_SOLVER_TIME_LIMIT_SECONDS,
            )
        solver_time_s = time.monotonic() - solver_start
        lp_stats = {
            "backend": "highspy",
            "base_steps": n,
            "period_count": p_n,
            "variables": num_vars,
            "constraints": int(A_eq.shape[0] + A_ub.shape[0]),
            "nonzeros": int(A_eq.nnz + A_ub.nnz),
            "formulation_time_s": round(formulation_time_s, 4),
            "solver_time_s": round(solver_time_s, 4),
            "time_limit_s": LP_SOLVER_TIME_LIMIT_SECONDS,
            "status": getattr(result, "status", None),
            "message": getattr(result, "message", ""),
        }

        if not result.success:
            _LOGGER.warning(f"LP solver status: {result.message}")
            if "infeasible" in result.message.lower():
                # The LP could not be satisfied with the real backup-reserve
                # floor. Rather than relaxing that floor to 5% and re-solving
                # — which authorises the battery to discharge to near-empty
                # purely to make the model feasible (and has drained users'
                # batteries to 5%) — fall back to a self-consumption hold that
                # never exports the battery, never grid-charges, and never
                # drops below the genuine reserve.
                hold = self._solve_self_consumption_hold(
                    n, import_prices, export_prices, solar, load, soc_0, cost_function,
                    acquisition_cost_kwh,
                    allow_battery_export,
                    block_battery_charge,
                    allow_grid_charge,
                    grid_charge_allowed,
                    export_bonus_prices,
                    export_bonus_cap_kwh,
                    import_bonus_prices,
                    import_bonus_cap_kwh,
                    schedule_timestamps,
                    disable_idle=disable_idle,
                )
                hold.lp_stats = {**lp_stats, "fallback_reason": "infeasible_self_consumption_hold"}
                return hold
            # Fall back to greedy
            greedy = self._solve_greedy(
                n, import_prices, export_prices, solar, load, soc_0, cost_function,
                acquisition_cost_kwh,
                allow_battery_export,
                block_battery_charge,
                allow_grid_charge,
                grid_charge_allowed,
                export_bonus_prices,
                export_bonus_cap_kwh,
                import_bonus_prices,
                import_bonus_cap_kwh,
                schedule_timestamps,
                priority_export_slots,
                disable_idle,
            )
            greedy.lp_stats = {**lp_stats, "fallback_reason": "solver_failed"}
            return greedy

        # === Extract solution ===
        x = result.x
        # Clamp tiny negative values to 0
        x = [max(0.0, v) for v in x]

        period_grid_import = [x[grid_import_var(t)] for t in range(p_n)]
        period_grid_export = [x[grid_export_var(t)] for t in range(p_n)]
        period_battery_charge = [x[charge_var(t)] for t in range(p_n)]
        period_battery_discharge = [x[discharge_var(t)] for t in range(p_n)]
        grid_import = self._expand_period_values(periods, period_grid_import, n)
        grid_export = self._expand_period_values(periods, period_grid_export, n)
        battery_charge = self._expand_period_values(periods, period_battery_charge, n)
        battery_discharge = self._expand_period_values(periods, period_battery_discharge, n)
        effective_export_prices = [
            export_prices[t] + export_bonus_prices[t]
            for t in range(n)
        ]

        # Build schedule with action mapping
        schedule = self._build_schedule(
            n, grid_import, grid_export, battery_charge, battery_discharge,
            solar, load, soc_0, import_prices, effective_export_prices,
            block_battery_charge,
            schedule_timestamps,
            allow_grid_charge,
            grid_charge_allowed,
            priority_export_slots,
            disable_idle,
        )

        # _build_schedule re-models "hold" slots (LP imports to serve load while
        # the battery idles) as natural self-consumption discharge, and clamps
        # charge/discharge to physically-available SOC. Recompute the reported
        # grid flows from the schedule the user actually sees so grid_import_w /
        # grid_export_w and predicted_cost describe that schedule — not the raw
        # LP solution, which would double-count imports the schedule covers from
        # the battery.
        grid_import, grid_export = self._grid_flows_from_schedule(
            schedule, n, solar, load
        )
        bonus_export = self._allocate_capped_bonus(
            grid_export,
            export_bonus_prices,
            export_bonus_cap_kwh,
            self._quota_export_group_ids,
            self._quota_export_caps_by_group,
        )
        bonus_import = self._allocate_capped_bonus(
            grid_import,
            import_bonus_prices,
            import_bonus_cap_kwh,
            self._quota_import_group_ids,
            self._quota_import_caps_by_group,
        )

        # Calculate costs for first 24 hours only (display as daily cost)
        n_24h = min(n, int(24 * 60 / self.interval_minutes))
        predicted_cost = sum(
            import_prices[t] * grid_import[t] * self.dt_hours
            - import_bonus_prices[t] * bonus_import[t] * self.dt_hours
            - export_prices[t] * grid_export[t] * self.dt_hours
            - export_bonus_prices[t] * bonus_export[t] * self.dt_hours
            for t in range(n_24h)
        )
        baseline_cost = self._calculate_baseline_cost(
            n_24h,
            import_prices,
            export_prices,
            solar,
            load,
            export_bonus_prices=export_bonus_prices,
            export_bonus_cap_kwh=export_bonus_cap_kwh,
            import_bonus_prices=import_bonus_prices,
            import_bonus_cap_kwh=import_bonus_cap_kwh,
        )
        predicted_savings = baseline_cost - predicted_cost

        schedule.predicted_cost = round(predicted_cost, 2)
        schedule.predicted_savings = round(predicted_savings, 2)
        reserve_recommendation = self._build_reserve_recommendation(
            schedule,
            solar,
            load,
        )

        return OptimizerResult(
            schedule=schedule,
            objective_value=result.fun,
            solver_used="highs",
            feasible=True,
            grid_import_w=[v * 1000 for v in grid_import],
            grid_export_w=[v * 1000 for v in grid_export],
            lp_stats=lp_stats,
            reserve_recommendation=reserve_recommendation,
        )







