"""greedy_solver extracted from battery_optimizer (architecture refactor Phase 4)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import logging
from typing import Any

from homeassistant.util import dt as dt_util

from .results import OptimizerResult
from .schedule_reader import OptimizationSchedule, ScheduleAction
from .solver_constants import ACTION_THRESHOLD_W, DEFAULT_EFFICIENCY

_LOGGER = logging.getLogger(__name__)


class GreedySolverMixin:
    """Mixin hosting greedy_solver implementation for BatteryOptimizer."""

    def _solve_self_consumption_hold(
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
        """Safe fallback when the LP is infeasible: hold in self-consumption.

        The previous fallback relaxed the backup-reserve floor to 5% and
        re-solved the LP. That made the model feasible by deleting the very
        safety floor it exists to protect — so the "optimal" relaxed plan would
        happily discharge the battery to ~5% just to satisfy the objective,
        draining users' batteries overnight.

        Instead, fall back to native self-consumption — the same do-no-harm
        behaviour the inverter exhibits without optimisation:

        * the battery only discharges to serve home load (never exports to grid),
        * the battery only charges from solar surplus (never from the grid),
        * SOC never drops below the genuine reserve floor (or, when already
          below it, holds at the current SOC down to the hardware floor).

        The result is marked ``feasible=False`` with no reserve recommendation
        so Auto-Apply Optimizer Reserve never ratchets the reserve down off the
        back of an infeasible solve.
        """
        _LOGGER.warning(
            "LP infeasible — holding in self-consumption: battery serves home "
            "load and charges from solar only (no grid export/charge), drawing "
            "down to the hardware reserve floor as the inverter would natively",
        )

        eff = self.efficiency
        cap = self.capacity_kwh
        dt = self.dt_hours
        export_bonus_prices = export_bonus_prices or [0.0] * n
        import_bonus_prices = import_bonus_prices or [0.0] * n
        block_battery_charge = block_battery_charge or [False] * n

        # Use the SAME floor the emitted schedule is rebuilt with
        # (_build_schedule -> _natural_self_consumption_floor). In native
        # self-consumption the inverter serves home load down to its hardware
        # reserve, not the software optimiser reserve, so simulating a hold at
        # the optimiser reserve would make grid_import_w/predicted_cost describe
        # a dispatch that never happens and diverge from the displayed SOC.
        self_consumption_floor = self._natural_self_consumption_floor(soc_0)
        def _max_grid_export_kw(t: int) -> float | None:
            return self._grid_export_limit_kw_for_range(t, t + 1)

        grid_import = [0.0] * n
        grid_export = [0.0] * n
        battery_charge = [0.0] * n
        battery_discharge = [0.0] * n

        soc = soc_0
        for t in range(n):
            max_grid_export_kw = _max_grid_export_kw(t)
            net_load = load[t] - solar[t]
            charge_kw = 0.0
            discharge_kw = 0.0
            if net_load > 0:
                # Home needs power: discharge the battery to serve load only,
                # bounded by the discharge rate and the energy available above
                # the reserve floor.
                discharge_room = max(0.0, soc - self_consumption_floor) * cap * eff / dt
                discharge_kw = min(self.max_discharge_kw, net_load, discharge_room)
            elif net_load < 0 and not block_battery_charge[t]:
                # Solar surplus: charge from solar only (never from the grid).
                surplus = -net_load
                charge_room = max(0.0, 1.0 - soc) * cap / (eff * dt)
                charge_kw = min(self.max_charge_kw, surplus, charge_room)

            battery_charge[t] = charge_kw
            battery_discharge[t] = discharge_kw

            # Power balance: grid_import + solar + discharge = load + export + charge
            net_grid = net_load + charge_kw - discharge_kw
            if net_grid > 0:
                grid_import[t] = net_grid
            else:
                # Only ever solar surplus reaches the grid — the battery is
                # never exported in this fallback.
                export_kw = -net_grid
                if max_grid_export_kw is not None:
                    export_kw = min(export_kw, max_grid_export_kw)
                grid_export[t] = export_kw

            soc += (charge_kw * eff - discharge_kw / eff) * dt / cap
            soc = max(self_consumption_floor, min(1.0, soc))

        schedule = self._build_schedule(
            n, grid_import, grid_export, battery_charge, battery_discharge,
            solar, load, soc_0, import_prices,
            [export_prices[t] + export_bonus_prices[t] for t in range(n)],
            block_battery_charge,
            schedule_timestamps,
            allow_grid_charge,
            grid_charge_allowed,
            disable_idle=disable_idle,
        )

        n_24h = min(n, int(24 * 60 / self.interval_minutes))
        bonus_import = self._allocate_capped_bonus(
            grid_import,
            import_bonus_prices,
            import_bonus_cap_kwh,
            self._quota_import_group_ids,
            self._quota_import_caps_by_group,
        )
        bonus_export = self._allocate_capped_bonus(
            grid_export,
            export_bonus_prices,
            export_bonus_cap_kwh,
            self._quota_export_group_ids,
            self._quota_export_caps_by_group,
        )
        predicted_cost = sum(
            import_prices[t] * grid_import[t] * dt
            - import_bonus_prices[t] * bonus_import[t] * dt
            - export_prices[t] * grid_export[t] * dt
            - export_bonus_prices[t] * bonus_export[t] * dt
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
        schedule.predicted_cost = round(predicted_cost, 2)
        schedule.predicted_savings = round(baseline_cost - predicted_cost, 2)

        return OptimizerResult(
            schedule=schedule,
            solver_used="self_consumption_hold",
            # Fallback solve: never let Auto-Apply ratchet the reserve off this.
            feasible=False,
            grid_import_w=[v * 1000 for v in grid_import],
            grid_export_w=[v * 1000 for v in grid_export],
            reserve_recommendation={},
        )
    def _solve_greedy(
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
        Greedy fallback optimizer.

        Sort time steps by price spread and greedily assign charge/discharge
        while tracking SOC constraints.
        """
        dt = self.dt_hours
        eff = self.efficiency
        cap = self.capacity_kwh
        allow_battery_export = allow_battery_export or [True] * n
        block_battery_charge = block_battery_charge or [False] * n
        grid_charge_allowed = grid_charge_allowed or [True] * n
        export_bonus_prices = export_bonus_prices or [0.0] * n
        import_bonus_prices = import_bonus_prices or [0.0] * n
        import_group_ids = list(self._quota_import_group_ids or [None] * n)[:n]
        export_group_ids = list(self._quota_export_group_ids or [None] * n)[:n]
        if len(import_group_ids) < n:
            import_group_ids.extend([None] * (n - len(import_group_ids)))
        if len(export_group_ids) < n:
            export_group_ids.extend([None] * (n - len(export_group_ids)))
        import_caps_by_group = dict(self._quota_import_caps_by_group)
        export_caps_by_group = dict(self._quota_export_caps_by_group)

        def _remaining_by_group(
            group_ids: list[str | None],
            caps: dict[str, float],
            fallback_cap: float | None,
        ) -> dict[str, float]:
            if caps and any(group_ids):
                return {key: max(0.0, float(value)) for key, value in caps.items()}
            return {"__all__": max(0.0, float(fallback_cap or 0.0))}

        def _group_key(group_ids: list[str | None], slot: int) -> str:
            return group_ids[slot] or "__all__"

        def _remaining_for(
            remaining: dict[str, float],
            group_ids: list[str | None],
            slot: int,
        ) -> float:
            return max(0.0, remaining.get(_group_key(group_ids, slot), 0.0))

        def _consume_for(
            remaining: dict[str, float],
            group_ids: list[str | None],
            slot: int,
            energy_kwh: float,
        ) -> None:
            key = _group_key(group_ids, slot)
            remaining[key] = max(0.0, remaining.get(key, 0.0) - energy_kwh)
        priority_export_slots = priority_export_slots or [False] * n
        effective_export_prices = [
            export_prices[t] + export_bonus_prices[t]
            for t in range(n)
        ]
        allow_grid_charge = bool(allow_grid_charge)
        effective_acquisition_costs = self._effective_export_acquisition_costs(
            n,
            import_prices,
            block_battery_charge,
            allow_grid_charge,
            acquisition_cost_kwh,
            grid_charge_allowed,
        )

        def _priority_export_slot(t: int) -> bool:
            export_value = effective_export_prices[t]
            return (
                priority_export_slots[t]
                and allow_battery_export[t]
                and export_value > 0.001
            )

        future_priority_recharge_cost = [float("inf")] * n
        best_future_recharge_cost = float("inf")
        import_bonus_active = bool(
            import_bonus_cap_kwh is not None
            and import_bonus_cap_kwh > 1e-6
            and any(price > 1e-6 for price in import_bonus_prices)
        )
        for idx in range(n - 1, -1, -1):
            future_priority_recharge_cost[idx] = best_future_recharge_cost
            if (
                allow_grid_charge
                and grid_charge_allowed[idx]
                and not block_battery_charge[idx]
            ):
                net_import_price = max(
                    0.0,
                    import_prices[idx]
                    - (import_bonus_prices[idx] if import_bonus_active else 0.0),
                )
                best_future_recharge_cost = min(
                    best_future_recharge_cost,
                    net_import_price / max(eff**2, 1e-9),
                )

        def _economic_export_slot(t: int) -> bool:
            return self._is_export_profitable(
                effective_export_prices[t],
                import_prices[t],
                acquisition_cost_kwh,
                effective_acquisition_costs[t],
            )

        def _max_grid_export_kw(t: int) -> float | None:
            return self._grid_export_limit_kw_for_range(t, t + 1)
        optimizer_reserve = self.backup_reserve
        below_optimizer_reserve = soc_0 < optimizer_reserve
        self_consumption_floor = self._natural_self_consumption_floor(soc_0)
        grid_charge_soc_cap = max(
            0.0,
            min(1.0, float(getattr(self, "grid_charge_soc_cap", 1.0) or 0.0)),
        )
        remaining_grid_charge_stored_kwh = (
            max(0.0, (grid_charge_soc_cap - soc_0) * cap)
            if allow_grid_charge and grid_charge_soc_cap < 0.999
            else float("inf")
        )

        grid_import = [0.0] * n
        grid_export = [0.0] * n
        battery_charge = [0.0] * n
        battery_discharge = [0.0] * n

        # Price-based greedy: sort export opportunities by spread, then charge
        # during the cheapest import slots that are not real export windows.
        spreads = []
        for t in range(n):
            net_load = load[t] - solar[t]
            spread = effective_export_prices[t] - import_prices[t]
            spreads.append((spread, t, net_load))

        # Sort: most profitable export first (highest spread)
        spreads.sort(key=lambda x: -x[0])

        # Two-pass: first assign exports (top spread), then imports (bottom spread)
        soc = soc_0
        actions = {}  # t -> (charge_kw, discharge_kw)

        # Pass 1: assign discharge/export to highest-spread periods.
        # Track the remaining capped bonus-export bucket (e.g. GloBird ZeroHero
        # Super Export). Intentional battery export beyond this bucket earns
        # only the base FiT (often ~0c) and would have to be re-bought at import
        # price — a loss. When the slot is profitable only because of the bonus,
        # cap intentional battery export to what still fits in the bucket, as
        # the LP does.
        bonus_export_remaining = _remaining_by_group(
            export_group_ids,
            export_caps_by_group,
            export_bonus_cap_kwh,
        )
        soc_tracker = soc_0
        for spread, t, net_load in spreads:
            max_grid_export_kw = _max_grid_export_kw(t)
            battery_export_allowed = allow_battery_export[t] and not below_optimizer_reserve
            export_profitable_slot = (
                battery_export_allowed
                and _economic_export_slot(t)
            )
            priority_export_slot = battery_export_allowed and _priority_export_slot(t)
            future_self_consumption_value = self._has_future_self_consumption_value(
                t, n, import_prices, solar, load
            )
            self_consumption_value_slot = (
                not battery_export_allowed
                and import_prices[t] > export_prices[t]
                and (
                    acquisition_cost_kwh <= 0
                    or import_prices[t] >= acquisition_cost_kwh
                )
            )
            if (
                export_profitable_slot
                and (priority_export_slot or not future_self_consumption_value)
                or self_consumption_value_slot
            ):
                forced_export_slot = export_profitable_slot and (
                    priority_export_slot or not future_self_consumption_value
                )
                # Profitable to discharge; cap to home load when battery export
                # is not explicitly permitted or export is below acquisition cost.
                discharge_limit = self.max_discharge_kw
                if forced_export_slot and self.max_battery_export_kw is not None:
                    discharge_limit = min(
                        discharge_limit,
                        max(0.0, net_load) + self.max_battery_export_kw,
                    )
                if max_grid_export_kw is not None:
                    discharge_limit = min(
                        discharge_limit,
                        max(0.0, net_load + max_grid_export_kw),
                    )
                if (
                    not battery_export_allowed
                    or (
                        not priority_export_slot
                        and acquisition_cost_kwh > 0
                        and effective_export_prices[t] < effective_acquisition_costs[t]
                    )
                ):
                    discharge_limit = min(discharge_limit, max(0.0, net_load))
                # Bonus-cap guard: when this slot is profitable to export only
                # because of the capped bonus (base FiT alone would not be), cap
                # intentional battery export (discharge above home load) to the
                # bonus bucket still remaining.
                bonus_only_profitable = (
                    forced_export_slot
                    and export_bonus_prices[t] > 0
                    and not self._is_export_profitable(
                        export_prices[t],
                        import_prices[t],
                        acquisition_cost_kwh,
                        effective_acquisition_costs[t],
                    )
                )
                if bonus_only_profitable:
                    intentional_export_room_kw = (
                        _remaining_for(bonus_export_remaining, export_group_ids, t)
                        / dt
                    )
                    discharge_limit = min(
                        discharge_limit,
                        max(0.0, net_load) + max(0.0, intentional_export_room_kw),
                    )
                discharge_floor = (
                    max(
                        optimizer_reserve,
                        self._configured_export_reserve_floor_for_range(t, t + 1),
                    )
                    if forced_export_slot
                    else self_consumption_floor
                )
                discharge_room = (soc_tracker - discharge_floor) * cap * eff / dt
                discharge_kw = min(discharge_limit, max(0, discharge_room))
                if discharge_kw > 0.01:
                    actions[t] = (0.0, discharge_kw)
                    soc_tracker -= discharge_kw * dt / (eff * cap)
                    if bonus_only_profitable:
                        intentional_export_kw = max(0.0, discharge_kw - max(0.0, net_load))
                        _consume_for(
                            bonus_export_remaining,
                            export_group_ids,
                            t,
                            intentional_export_kw * dt,
                        )

        # Pass 2: buy only the energy that can avoid a strictly more expensive
        # future import. The previous greedy fallback filled every available
        # slot to the battery ceiling, which created mandatory and uneconomic
        # top-ups whenever HiGHS was unavailable.
        remaining_load_kwh = [
            max(
                0.0,
                max(0.0, load[idx] - solar[idx])
                - actions.get(idx, (0.0, 0.0))[1],
            )
            * dt
            for idx in range(n)
        ]
        base_discharge_kw = [
            actions.get(idx, (0.0, 0.0))[1]
            for idx in range(n)
        ]

        # Split future household demand into its rebated and ordinary marginal
        # values. A capped ZeroCharge-style rebate makes only the covered kWh
        # cheap; treating every future kWh at the raw retail tariff can make the
        # fallback buy energy now to avoid a later import that would cost zero.
        remaining_rebated_load_kwh = [0.0] * n
        remaining_standard_load_kwh = list(remaining_load_kwh)
        import_bonus_remaining = _remaining_by_group(
            import_group_ids,
            import_caps_by_group,
            import_bonus_cap_kwh,
        )
        for idx in range(n):
            if import_bonus_prices[idx] <= 0:
                continue
            group_remaining_kwh = _remaining_for(
                import_bonus_remaining, import_group_ids, idx
            )
            if group_remaining_kwh <= 1e-9:
                continue
            covered_kwh = min(
                remaining_standard_load_kwh[idx],
                group_remaining_kwh,
            )
            remaining_rebated_load_kwh[idx] = covered_kwh
            remaining_standard_load_kwh[idx] -= covered_kwh
            _consume_for(
                import_bonus_remaining,
                import_group_ids,
                idx,
                covered_kwh,
            )

        initial_rebated_load_kwh = list(remaining_rebated_load_kwh)
        import_bonus_cap_total_kwh = sum(
            import_caps_by_group.values()
        ) if import_caps_by_group and any(import_group_ids) else max(
            0.0, float(import_bonus_cap_kwh or 0.0)
        )
        bonus_charge_consumed_by_group: dict[str, float] = {}

        # Describe still-available future battery-to-grid output. Pass 1 can
        # spend energy already present at the start of the horizon; these
        # buckets let pass 2 buy additional energy only when a later export
        # pays for the complete round trip. Bonus output is kept separate so a
        # capped provider credit cannot value more export than it settles.
        remaining_bonus_export_kwh = [0.0] * n
        remaining_base_export_kwh = [0.0] * n
        export_bonus_remaining = _remaining_by_group(
            export_group_ids,
            export_caps_by_group,
            export_bonus_cap_kwh,
        )
        for idx in range(n):
            max_grid_export_kw = _max_grid_export_kw(idx)
            solar_surplus_kw = max(0.0, solar[idx] - load[idx])
            if max_grid_export_kw is not None:
                solar_surplus_kw = min(solar_surplus_kw, max_grid_export_kw)

            existing_discharge_kw = actions.get(idx, (0.0, 0.0))[1]
            net_home_kw = max(0.0, load[idx] - solar[idx])
            existing_battery_export_kw = max(
                0.0,
                existing_discharge_kw - net_home_kw,
            )

            slot_export_remaining = _remaining_for(
                export_bonus_remaining, export_group_ids, idx
            )
            if export_bonus_prices[idx] > 0 and slot_export_remaining > 0:
                settled_existing_kwh = min(
                    slot_export_remaining,
                    (solar_surplus_kw + existing_battery_export_kw) * dt,
                )
                _consume_for(
                    export_bonus_remaining,
                    export_group_ids,
                    idx,
                    settled_existing_kwh,
                )

            if not allow_battery_export[idx] or below_optimizer_reserve:
                continue

            battery_export_limit_kw = max(0.0, self.max_discharge_kw - net_home_kw)
            if self.max_battery_export_kw is not None:
                battery_export_limit_kw = min(
                    battery_export_limit_kw,
                    self.max_battery_export_kw,
                )
            if max_grid_export_kw is not None:
                battery_export_limit_kw = min(
                    battery_export_limit_kw,
                    max(0.0, max_grid_export_kw - solar_surplus_kw),
                )
            remaining_output_kwh = max(
                0.0,
                (battery_export_limit_kw - existing_battery_export_kw) * dt,
            )
            if remaining_output_kwh <= 1e-9:
                continue

            bonus_output_kwh = 0.0
            slot_export_remaining = _remaining_for(
                export_bonus_remaining, export_group_ids, idx
            )
            if export_bonus_prices[idx] > 0 and slot_export_remaining > 0:
                bonus_output_kwh = min(
                    remaining_output_kwh,
                    slot_export_remaining,
                )
                _consume_for(
                    export_bonus_remaining,
                    export_group_ids,
                    idx,
                    bonus_output_kwh,
                )
            remaining_bonus_export_kwh[idx] = bonus_output_kwh
            remaining_base_export_kwh[idx] = (
                remaining_output_kwh - bonus_output_kwh
            )

        planned_load_output_kwh = [0.0] * n
        planned_export_output_kwh = [0.0] * n

        def _projected_soc_before(slot: int) -> float:
            """Project assigned actions chronologically up to ``slot``."""
            projected_soc = soc_0
            for idx in range(slot):
                charge_kw, discharge_kw = actions.get(idx, (0.0, 0.0))
                charge_kw = min(
                    max(0.0, charge_kw),
                    max(0.0, (1.0 - projected_soc) * cap / (eff * dt)),
                )
                net_home_kw = max(0.0, load[idx] - solar[idx])
                discharge_floor = self_consumption_floor
                if (
                    discharge_kw > net_home_kw + 0.001
                    and allow_battery_export[idx]
                    and not below_optimizer_reserve
                ):
                    discharge_floor = max(
                        optimizer_reserve,
                        self._configured_export_reserve_floor_for_range(
                            idx, idx + 1
                        ),
                    )
                discharge_kw = min(
                    max(0.0, discharge_kw),
                    max(
                        0.0,
                        (projected_soc - discharge_floor) * cap * eff / dt,
                    ),
                )
                projected_soc += (
                    charge_kw * eff - discharge_kw / eff
                ) * dt / cap
                projected_soc = max(
                    self_consumption_floor,
                    min(1.0, projected_soc),
                )
            return projected_soc

        for _, t, net_load in sorted(
            spreads,
            key=lambda item: (
                0
                if import_bonus_cap_total_kwh > 0
                and import_bonus_prices[item[1]] > 0
                else 1,
                item[1]
                if import_bonus_cap_total_kwh > 0
                and import_bonus_prices[item[1]] > 0
                else import_prices[item[1]],
                item[1],
            ),
        ):
            if t in actions:
                continue
            battery_export_allowed = allow_battery_export[t] and not below_optimizer_reserve
            export_profitable_slot = (
                battery_export_allowed
                and _economic_export_slot(t)
            )
            priority_export_slot = battery_export_allowed and _priority_export_slot(t)
            future_self_consumption_value = self._has_future_self_consumption_value(
                t, n, import_prices, solar, load
            )
            if block_battery_charge[t] or priority_export_slot or (
                export_profitable_slot and not future_self_consumption_value
            ):
                continue
            projected_soc = _projected_soc_before(t)
            charge_room = (1.0 - projected_soc) * cap / (eff * dt)
            charge_limit = self._charge_limit_kw(
                load[t], solar[t], allow_grid_charge and grid_charge_allowed[t]
            )
            charge_limit = min(charge_limit, max(0, charge_room))
            if charge_limit <= 0.01:
                continue

            total_charge_input_capacity_kwh = charge_limit * dt
            solar_charge_input_capacity_kwh = min(
                total_charge_input_capacity_kwh,
                max(0.0, solar[t] - load[t]) * dt,
            )
            grid_charge_input_capacity_kwh = max(
                0.0,
                total_charge_input_capacity_kwh
                - solar_charge_input_capacity_kwh,
            )
            if remaining_grid_charge_stored_kwh != float("inf"):
                grid_charge_input_capacity_kwh = min(
                    grid_charge_input_capacity_kwh,
                    remaining_grid_charge_stored_kwh / max(eff, 1e-9),
                )
            bonus_input_capacity_kwh = 0.0
            import_group = _group_key(import_group_ids, t)
            import_group_cap = (
                import_caps_by_group.get(import_group, 0.0)
                if import_caps_by_group and any(import_group_ids)
                else import_bonus_cap_total_kwh
            )
            bonus_load_before_or_at_t = sum(
                initial_rebated_load_kwh[idx]
                for idx in range(t + 1)
                if _group_key(import_group_ids, idx) == import_group
            )
            bonus_available_for_charge_kwh = max(
                0.0,
                import_group_cap
                - bonus_load_before_or_at_t
                - bonus_charge_consumed_by_group.get(import_group, 0.0),
            )
            if (
                import_bonus_prices[t] > 0
                and bonus_available_for_charge_kwh > 1e-9
            ):
                bonus_input_capacity_kwh = min(
                    grid_charge_input_capacity_kwh,
                    bonus_available_for_charge_kwh,
                )
            charge_tiers = []
            if solar_charge_input_capacity_kwh > 1e-9:
                charge_tiers.append((
                    max(0.0, export_prices[t]),
                    solar_charge_input_capacity_kwh,
                    False,
                    False,
                ))
            if bonus_input_capacity_kwh > 1e-9:
                charge_tiers.append((
                    max(0.0, import_prices[t] - import_bonus_prices[t]),
                    bonus_input_capacity_kwh,
                    True,
                    True,
                ))
            base_input_capacity_kwh = (
                grid_charge_input_capacity_kwh - bonus_input_capacity_kwh
            )
            if base_input_capacity_kwh > 1e-9:
                charge_tiers.append((
                    import_prices[t],
                    base_input_capacity_kwh,
                    False,
                    True,
                ))
            charge_tiers.sort(key=lambda tier: tier[0])

            charged_input_kwh = 0.0
            for (
                marginal_import_price,
                tier_input_kwh,
                uses_bonus,
                uses_grid,
            ) in charge_tiers:
                delivered_capacity_kwh = tier_input_kwh * eff * eff
                delivered_kwh = 0.0
                # Allocate the charge to the highest-value future output,
                # whether that output avoids a household import or funds an
                # intentional battery export.
                candidates: list[tuple[float, int, str]] = []
                for future_idx in range(t + 1, n):
                    if remaining_rebated_load_kwh[future_idx] > 1e-9:
                        candidates.append((
                            max(
                                0.0,
                                import_prices[future_idx]
                                - import_bonus_prices[future_idx],
                            ),
                            future_idx,
                            "rebated_load",
                        ))
                    if remaining_standard_load_kwh[future_idx] > 1e-9:
                        candidates.append((
                            import_prices[future_idx],
                            future_idx,
                            "standard_load",
                        ))
                    if remaining_bonus_export_kwh[future_idx] > 1e-9:
                        candidates.append((
                            export_prices[future_idx]
                            + export_bonus_prices[future_idx],
                            future_idx,
                            "bonus_export",
                        ))
                    if remaining_base_export_kwh[future_idx] > 1e-9:
                        candidates.append((
                            export_prices[future_idx],
                            future_idx,
                            "base_export",
                        ))

                candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
                for future_value, future_idx, kind in candidates:
                    if delivered_kwh >= delivered_capacity_kwh - 1e-9:
                        break
                    if (
                        future_value * eff * eff
                        <= marginal_import_price + 0.001
                    ):
                        continue
                    if kind == "rebated_load":
                        remaining_kwh = remaining_rebated_load_kwh[future_idx]
                    elif kind == "standard_load":
                        remaining_kwh = remaining_standard_load_kwh[future_idx]
                    elif kind == "bonus_export":
                        remaining_kwh = remaining_bonus_export_kwh[future_idx]
                    else:
                        remaining_kwh = remaining_base_export_kwh[future_idx]
                    take = min(
                        remaining_kwh,
                        delivered_capacity_kwh - delivered_kwh,
                    )
                    if kind == "rebated_load":
                        remaining_rebated_load_kwh[future_idx] -= take
                    elif kind == "standard_load":
                        remaining_standard_load_kwh[future_idx] -= take
                    elif kind == "bonus_export":
                        remaining_bonus_export_kwh[future_idx] -= take
                    else:
                        remaining_base_export_kwh[future_idx] -= take

                    if kind in ("rebated_load", "standard_load"):
                        planned_load_output_kwh[future_idx] += take
                    else:
                        planned_export_output_kwh[future_idx] += take
                    total_discharge_kw = (
                        base_discharge_kw[future_idx]
                        + planned_load_output_kwh[future_idx] / dt
                        + planned_export_output_kwh[future_idx] / dt
                    )
                    if planned_export_output_kwh[future_idx] > 0:
                        total_discharge_kw = max(
                            total_discharge_kw,
                            max(0.0, load[future_idx] - solar[future_idx])
                            + planned_export_output_kwh[future_idx] / dt,
                        )
                    actions[future_idx] = (0.0, total_discharge_kw)
                    delivered_kwh += take

                used_input_kwh = delivered_kwh / max(1e-9, eff * eff)
                charged_input_kwh += used_input_kwh
                if uses_grid and remaining_grid_charge_stored_kwh != float("inf"):
                    remaining_grid_charge_stored_kwh = max(
                        0.0,
                        remaining_grid_charge_stored_kwh
                        - used_input_kwh * eff,
                    )
                if uses_bonus:
                    bonus_charge_consumed_by_group[import_group] = (
                        bonus_charge_consumed_by_group.get(import_group, 0.0)
                        + used_input_kwh
                    )
                    # Canonical settlement applies the cap chronologically.
                    # Earlier battery import therefore displaces an equal
                    # amount of credit previously forecast for later home load.
                    displaced_kwh = used_input_kwh
                    for future_idx in range(t + 1, n):
                        if displaced_kwh <= 1e-9:
                            break
                        if (
                            _group_key(import_group_ids, future_idx)
                            != import_group
                        ):
                            continue
                        move_kwh = min(
                            remaining_rebated_load_kwh[future_idx],
                            displaced_kwh,
                        )
                        if move_kwh <= 0:
                            continue
                        remaining_rebated_load_kwh[future_idx] -= move_kwh
                        remaining_standard_load_kwh[future_idx] += move_kwh
                        displaced_kwh -= move_kwh

            charge_kw = charged_input_kwh / max(1e-9, dt)
            if charge_kw > 0.01:
                actions[t] = (charge_kw, 0.0)

        # Pair below-acquisition priority export with actual future recharge.
        # This is deliberately quantity-aware: a one-kWh cheap/rebated slot can
        # fund one round-trip kWh, not authorize the whole battery to be sold
        # below its modeled acquisition cost.
        paired_bonus_export_remaining = _remaining_by_group(
            export_group_ids,
            export_caps_by_group,
            export_bonus_cap_kwh,
        )
        if any(value > 0 for value in paired_bonus_export_remaining.values()):
            for idx in range(n):
                max_grid_export_kw = _max_grid_export_kw(idx)
                if export_bonus_prices[idx] <= 0:
                    continue
                net_home_kw = max(0.0, load[idx] - solar[idx])
                battery_export_kw = max(
                    0.0,
                    actions.get(idx, (0.0, 0.0))[1] - net_home_kw,
                )
                existing_grid_export_kw = (
                    max(0.0, solar[idx] - load[idx]) + battery_export_kw
                )
                if max_grid_export_kw is not None:
                    existing_grid_export_kw = min(
                        existing_grid_export_kw,
                        max_grid_export_kw,
                    )
                _consume_for(
                    paired_bonus_export_remaining,
                    export_group_ids,
                    idx,
                    existing_grid_export_kw * dt,
                )
        for t in range(n):
            max_grid_export_kw = _max_grid_export_kw(t)
            export_value = effective_export_prices[t]
            if (
                not _priority_export_slot(t)
                or acquisition_cost_kwh <= 0
                or export_value + 1e-9 >= effective_acquisition_costs[t]
                or future_priority_recharge_cost[t] > export_value + 1e-9
            ):
                continue

            net_home_kw = max(0.0, load[t] - solar[t])
            existing_discharge_kw = actions.get(t, (0.0, 0.0))[1]
            if existing_discharge_kw > net_home_kw + 0.001:
                # Already backed by an earlier charge allocation.
                continue

            projected_soc = _projected_soc_before(t)
            export_floor = max(
                optimizer_reserve,
                self._configured_export_reserve_floor_for_range(t, t + 1),
            )
            available_output_kwh = max(
                0.0,
                (projected_soc - export_floor) * cap * eff
                - net_home_kw * dt,
            )
            battery_export_limit_kw = max(0.0, self.max_discharge_kw - net_home_kw)
            if self.max_battery_export_kw is not None:
                battery_export_limit_kw = min(
                    battery_export_limit_kw,
                    self.max_battery_export_kw,
                )
            if max_grid_export_kw is not None:
                solar_surplus_kw = max(0.0, solar[t] - load[t])
                battery_export_limit_kw = min(
                    battery_export_limit_kw,
                    max(0.0, max_grid_export_kw - solar_surplus_kw),
                )
            max_paired_output_kwh = min(
                available_output_kwh,
                battery_export_limit_kw * dt,
            )

            base_export_can_fund_recharge = (
                export_prices[t] + 1e-9 >= future_priority_recharge_cost[t]
            )
            bonus_only_pair = (
                export_bonus_prices[t] > 0
                and not base_export_can_fund_recharge
            )
            if bonus_only_pair:
                max_paired_output_kwh = min(
                    max_paired_output_kwh,
                    _remaining_for(
                        paired_bonus_export_remaining,
                        export_group_ids,
                        t,
                    ),
                )
            if max_paired_output_kwh <= 1e-9:
                continue

            remaining_output_kwh = max_paired_output_kwh
            for recharge_idx in range(t + 1, n):
                if remaining_output_kwh <= 1e-9:
                    break
                if (
                    not allow_grid_charge
                    or not grid_charge_allowed[recharge_idx]
                    or block_battery_charge[recharge_idx]
                ):
                    continue
                existing_charge_kw, future_discharge_kw = actions.get(
                    recharge_idx,
                    (0.0, 0.0),
                )
                if future_discharge_kw > 0:
                    continue
                charge_limit_kw = self._charge_limit_kw(
                    load[recharge_idx],
                    solar[recharge_idx],
                    True,
                )
                available_charge_input_kwh = max(
                    0.0,
                    (charge_limit_kw - existing_charge_kw) * dt,
                )
                if available_charge_input_kwh <= 1e-9:
                    continue

                solar_charge_input_kwh = min(
                    available_charge_input_kwh,
                    max(
                        0.0,
                        solar[recharge_idx]
                        - load[recharge_idx]
                        - existing_charge_kw,
                    )
                    * dt,
                )
                grid_charge_input_kwh = max(
                    0.0,
                    available_charge_input_kwh - solar_charge_input_kwh,
                )
                if remaining_grid_charge_stored_kwh != float("inf"):
                    grid_charge_input_kwh = min(
                        grid_charge_input_kwh,
                        remaining_grid_charge_stored_kwh / max(eff, 1e-9),
                    )

                recharge_group = _group_key(import_group_ids, recharge_idx)
                recharge_group_cap = (
                    import_caps_by_group.get(recharge_group, 0.0)
                    if import_caps_by_group and any(import_group_ids)
                    else import_bonus_cap_total_kwh
                )
                bonus_load_before_or_at_slot = sum(
                    initial_rebated_load_kwh[idx]
                    for idx in range(recharge_idx + 1)
                    if _group_key(import_group_ids, idx) == recharge_group
                )
                bonus_available_kwh = max(
                    0.0,
                    recharge_group_cap
                    - bonus_load_before_or_at_slot
                    - bonus_charge_consumed_by_group.get(recharge_group, 0.0),
                )
                bonus_capacity_kwh = 0.0
                if import_bonus_prices[recharge_idx] > 0:
                    bonus_capacity_kwh = min(
                        grid_charge_input_kwh,
                        bonus_available_kwh,
                    )
                recharge_tiers = []
                if solar_charge_input_kwh > 1e-9:
                    recharge_tiers.append((
                        max(0.0, export_prices[recharge_idx]),
                        solar_charge_input_kwh,
                        False,
                        False,
                    ))
                if bonus_capacity_kwh > 1e-9:
                    recharge_tiers.append((
                        max(
                            0.0,
                            import_prices[recharge_idx]
                            - import_bonus_prices[recharge_idx],
                        ),
                        bonus_capacity_kwh,
                        True,
                        True,
                    ))
                raw_capacity_kwh = grid_charge_input_kwh - bonus_capacity_kwh
                if raw_capacity_kwh > 1e-9:
                    recharge_tiers.append((
                        import_prices[recharge_idx],
                        raw_capacity_kwh,
                        False,
                        True,
                    ))
                recharge_tiers.sort(key=lambda tier: tier[0])

                added_input_kwh = 0.0
                for (
                    marginal_price,
                    tier_input_kwh,
                    uses_bonus,
                    uses_grid,
                ) in recharge_tiers:
                    if marginal_price >= export_value * eff * eff - 0.001:
                        continue
                    take_input_kwh = min(
                        tier_input_kwh,
                        remaining_output_kwh / max(eff * eff, 1e-9),
                    )
                    if take_input_kwh <= 1e-9:
                        continue
                    added_input_kwh += take_input_kwh
                    remaining_output_kwh -= take_input_kwh * eff * eff
                    if (
                        uses_grid
                        and remaining_grid_charge_stored_kwh != float("inf")
                    ):
                        remaining_grid_charge_stored_kwh = max(
                            0.0,
                            remaining_grid_charge_stored_kwh
                            - take_input_kwh * eff,
                        )
                    if uses_bonus:
                        bonus_charge_consumed_by_group[recharge_group] = (
                            bonus_charge_consumed_by_group.get(recharge_group, 0.0)
                            + take_input_kwh
                        )
                        displaced_kwh = take_input_kwh
                        for future_idx in range(recharge_idx + 1, n):
                            if displaced_kwh <= 1e-9:
                                break
                            if (
                                _group_key(import_group_ids, future_idx)
                                != recharge_group
                            ):
                                continue
                            move_kwh = min(
                                remaining_rebated_load_kwh[future_idx],
                                displaced_kwh,
                            )
                            remaining_rebated_load_kwh[future_idx] -= move_kwh
                            remaining_standard_load_kwh[future_idx] += move_kwh
                            displaced_kwh -= move_kwh
                if added_input_kwh > 1e-9:
                    actions[recharge_idx] = (
                        existing_charge_kw + added_input_kwh / dt,
                        0.0,
                    )

            paired_output_kwh = max_paired_output_kwh - remaining_output_kwh
            if paired_output_kwh <= 0.001:
                continue
            actions[t] = (
                0.0,
                net_home_kw + paired_output_kwh / dt,
            )
            if bonus_only_pair:
                _consume_for(
                    paired_bonus_export_remaining,
                    export_group_ids,
                    t,
                    paired_output_kwh,
                )

        # Now compute grid flows in time order. The two price-priority passes
        # above track SOC in assignment order, not chronological order, so an
        # assigned action can exceed the room actually available at its real
        # time (e.g. a charge scheduled before a full battery drains, or a
        # discharge of energy not yet charged). Clamp each action to the SOC
        # physically available now so the emitted schedule — and the grid flows
        # / predicted cost derived from it — cannot overcharge a full battery
        # or discharge energy that is not there.
        soc = soc_0
        for t in range(n):
            max_grid_export_kw = _max_grid_export_kw(t)
            net_load = load[t] - solar[t]
            charge_kw, discharge_kw = actions.get(t, (0.0, 0.0))

            max_charge_room_kw = max(0.0, (1.0 - soc) * cap / (eff * dt))
            charge_kw = min(charge_kw, max_charge_room_kw)
            discharge_floor = self_consumption_floor
            if discharge_kw > 0:
                battery_export_allowed = (
                    allow_battery_export[t] and not below_optimizer_reserve
                )
                export_profitable_slot = (
                    battery_export_allowed
                    and _economic_export_slot(t)
                )
                priority_export_slot = battery_export_allowed and _priority_export_slot(t)
                future_self_consumption_value = self._has_future_self_consumption_value(
                    t, n, import_prices, solar, load
                )
                if priority_export_slot or (
                    export_profitable_slot and not future_self_consumption_value
                ):
                    discharge_floor = max(
                        optimizer_reserve,
                        self._configured_export_reserve_floor_for_range(t, t + 1),
                    )
            max_discharge_room_kw = max(
                0.0, (soc - discharge_floor) * cap * eff / dt
            )
            discharge_kw = min(discharge_kw, max_discharge_room_kw)

            battery_charge[t] = charge_kw
            battery_discharge[t] = discharge_kw

            # Power balance: grid_import + solar + discharge = load + grid_export + charge
            net_grid = net_load + charge_kw - discharge_kw
            if net_grid > 0:
                grid_import[t] = net_grid
            else:
                export_kw = -net_grid
                if max_grid_export_kw is not None:
                    export_kw = min(export_kw, max_grid_export_kw)
                if self.max_battery_export_kw is not None:
                    solar_surplus_kw = max(0.0, solar[t] - load[t])
                    export_kw = min(export_kw, solar_surplus_kw + self.max_battery_export_kw)
                grid_export[t] = export_kw

            soc += (charge_kw * eff - discharge_kw / eff) * dt / cap
            soc = max(self_consumption_floor, min(1.0, soc))

        # Build schedule
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

        grid_import, grid_export = self._grid_flows_from_schedule(
            schedule,
            n,
            solar,
            load,
        )

        # Calculate costs for first 24 hours only (display as daily cost)
        n_24h = min(n, int(24 * 60 / self.interval_minutes))
        bonus_import = [0.0] * n
        import_bonus_remaining = _remaining_by_group(
            import_group_ids,
            import_caps_by_group,
            import_bonus_cap_kwh,
        )
        if any(value > 0 for value in import_bonus_remaining.values()):
            for t in range(n):
                if import_bonus_prices[t] <= 0:
                    continue
                slot_remaining = _remaining_for(
                    import_bonus_remaining, import_group_ids, t
                )
                bonus_kw = min(grid_import[t], slot_remaining / dt)
                bonus_import[t] = bonus_kw
                _consume_for(
                    import_bonus_remaining,
                    import_group_ids,
                    t,
                    bonus_kw * dt,
                )
        bonus_export = [0.0] * n
        bonus_remaining = _remaining_by_group(
            export_group_ids,
            export_caps_by_group,
            export_bonus_cap_kwh,
        )
        if any(value > 0 for value in bonus_remaining.values()):
            for t in range(n):
                if export_bonus_prices[t] <= 0:
                    continue
                slot_remaining = _remaining_for(
                    bonus_remaining, export_group_ids, t
                )
                bonus_kw = min(grid_export[t], slot_remaining / dt)
                bonus_export[t] = bonus_kw
                _consume_for(
                    bonus_remaining,
                    export_group_ids,
                    t,
                    bonus_kw * dt,
                )
        predicted_cost = sum(
            import_prices[t] * grid_import[t] * dt
            - import_bonus_prices[t] * bonus_import[t] * dt
            - export_prices[t] * grid_export[t] * dt
            - export_bonus_prices[t] * bonus_export[t] * dt
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

        schedule.predicted_cost = round(predicted_cost, 2)
        schedule.predicted_savings = round(baseline_cost - predicted_cost, 2)
        reserve_recommendation = self._build_reserve_recommendation(
            schedule,
            solar,
            load,
        )

        return OptimizerResult(
            schedule=schedule,
            solver_used="greedy",
            feasible=True,
            grid_import_w=[v * 1000 for v in grid_import],
            grid_export_w=[v * 1000 for v in grid_export],
            reserve_recommendation=reserve_recommendation,
        )



