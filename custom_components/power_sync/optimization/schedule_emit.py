"""schedule_emit extracted from battery_optimizer (architecture refactor Phase 4)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import logging
from typing import Any

from homeassistant.util import dt as dt_util

from .results import OptimizerResult
from .schedule_reader import OptimizationSchedule, ScheduleAction
from .solver_constants import (
    ACTION_THRESHOLD_W,
    BELOW_RESERVE_RECOVERY_HOLD_MARGIN_SOC,
)

_LOGGER = logging.getLogger(__name__)


class ScheduleEmitMixin:
    """Mixin hosting schedule_emit implementation for BatteryOptimizer."""

    def _build_reserve_recommendation(
        self,
        schedule: OptimizationSchedule,
        solar: list[float],
        load: list[float],
    ) -> dict[str, Any]:
        """Suggest the optimizer reserve needed to bridge to the next charge."""
        actions = schedule.actions or []
        if not actions:
            return {}

        threshold_w = ACTION_THRESHOLD_W
        next_charge_idx: int | None = None
        next_charge_reason: str | None = None
        for idx, action in enumerate(actions):
            if action.battery_charge_w > threshold_w:
                next_charge_idx = idx
                next_charge_reason = (
                    "scheduled_grid_charge"
                    if action.action == "charge"
                    else "forecast_solar_surplus"
                )
                break

            if idx < len(solar) and idx < len(load):
                if (solar[idx] - load[idx]) * 1000 > threshold_w:
                    next_charge_idx = idx
                    next_charge_reason = "forecast_solar_surplus"
                    break

        bridge_actions = (
            actions[: next_charge_idx + 1]
            if next_charge_idx is not None
            else actions
        )
        soc_points = [
            (idx, action.soc)
            for idx, action in enumerate(bridge_actions)
            if action.soc is not None
        ]
        if not soc_points:
            return {}

        minimum_idx, minimum_soc_raw = min(soc_points, key=lambda item: item[1])
        minimum_soc = float(minimum_soc_raw)
        configured_percent = max(
            0,
            min(100, int(round(self.backup_reserve * 100))),
        )
        hardware_percent = max(
            0,
            min(100, int(round(self.hardware_reserve * 100))),
        )
        starting_soc = float(soc_points[0][1])
        meaningful_bridge_drop = starting_soc - minimum_soc > 0.02
        if meaningful_bridge_drop:
            suggested_ratio = max(self.hardware_reserve, min(1.0, minimum_soc))
        else:
            suggested_ratio = max(self.hardware_reserve, self.backup_reserve)
        suggested_percent = max(0, min(100, int(round(suggested_ratio * 100))))

        recommendation: dict[str, Any] = {
            "suggested_optimizer_reserve_percent": suggested_percent,
            "configured_optimizer_reserve_percent": configured_percent,
            "hardware_reserve_percent": hardware_percent,
            "minimum_forecast_soc_percent": max(
                0,
                min(100, round(minimum_soc * 100, 1)),
            ),
            "minimum_forecast_soc_time": actions[minimum_idx].timestamp.isoformat(),
            "protects_until": (
                actions[next_charge_idx].timestamp.isoformat()
                if next_charge_idx is not None
                else actions[-1].timestamp.isoformat()
            ),
            "next_charge_reason": next_charge_reason or "no_charge_in_horizon",
            "needs_optimizer_reserve_raise": suggested_percent > configured_percent,
        }
        if not meaningful_bridge_drop:
            recommendation["note"] = "No discharge bridge before next charge"
        if next_charge_idx is None:
            recommendation["note"] = "No charging opportunity in optimizer horizon"
        return recommendation
    def _build_schedule(
        self,
        n: int,
        grid_import: list[float],
        grid_export: list[float],
        battery_charge: list[float],
        battery_discharge: list[float],
        solar: list[float],
        load: list[float],
        soc_0: float,
        import_prices: list[float] | None = None,
        export_prices: list[float] | None = None,
        block_battery_charge: list[bool] | None = None,
        schedule_timestamps: list[datetime] | None = None,
        allow_grid_charge: bool = True,
        grid_charge_allowed: list[bool] | None = None,
        priority_export_slots: list[bool] | None = None,
        disable_idle: bool = False,
    ) -> OptimizationSchedule:
        """
        Map LP solution to battery actions.

        Action mapping:
        - CHARGE: grid → battery. Detected when battery_charge > threshold AND
          grid_import > load (charging from grid, not just from solar excess).
        - EXPORT: battery → grid. Detected when grid_export > threshold AND
          battery_discharge > threshold.
        - IDLE: Hold SOC. Detected when battery is neither charging nor discharging
          significantly, AND there is grid import (home drawing from grid while
          battery holds). Implemented by setting backup reserve = current SOC.
        - SELF_CONSUMPTION: Everything else. Battery charges from solar excess and
          discharges to serve home load naturally.
        """
        dt = self.dt_hours
        eff = self.efficiency
        cap = self.capacity_kwh
        # Prefer caller-supplied forecast timestamps so displayed actions stay
        # aligned with the price slots that produced them, even if solving
        # crosses a 5-minute boundary in the executor thread.
        if schedule_timestamps:
            now = schedule_timestamps[0]
        else:
            raw_now = dt_util.now()
            now = raw_now.replace(
                minute=(raw_now.minute // self.interval_minutes) * self.interval_minutes,
                second=0, microsecond=0,
            )
        threshold_kw = ACTION_THRESHOLD_W / 1000.0

        block_battery_charge = block_battery_charge or [False] * n
        grid_charge_allowed = grid_charge_allowed or [True] * n
        priority_export_slots = priority_export_slots or [False] * n
        actions = []
        soc = soc_0
        optimizer_reserve = max(0.0, min(1.0, self.backup_reserve))
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

        def _future_grid_charge_planned(start_idx: int) -> bool:
            for future_idx in range(start_idx + 1, n):
                if future_idx >= len(battery_charge) or future_idx >= len(grid_import):
                    break
                future_charge_kw = battery_charge[future_idx]
                future_import_kw = grid_import[future_idx]
                if future_charge_kw <= threshold_kw:
                    continue
                if (
                    import_prices is not None
                    and future_idx < len(import_prices)
                    and import_prices[future_idx] <= 0.001
                ):
                    return True
                net_load_kw = max(0.0, load[future_idx] - solar[future_idx])
                if future_import_kw > net_load_kw + threshold_kw:
                    return True
            return False

        def _charge_by_time_hold_required(start_idx: int, start_soc: float) -> bool:
            """Return whether natural use now would make the deadline unreachable."""
            if (
                self.pre_window_slot is None
                or start_idx >= self.pre_window_slot
                or self.pre_window_soc_target <= 0.0
                or cap <= 0
                or dt <= 0
            ):
                return False

            deadline = min(n, self.pre_window_slot)
            projected_soc = start_soc
            for future_idx in range(start_idx, deadline):
                if future_idx == start_idx:
                    net_home_kw = load[future_idx] - solar[future_idx]
                    if net_home_kw > threshold_kw:
                        available_kw = (
                            max(0.0, projected_soc - self_consumption_floor)
                            * cap
                            * eff
                            / dt
                        )
                        charge_future_kw = 0.0
                        discharge_future_kw = min(
                            self.max_discharge_kw,
                            net_home_kw,
                            available_kw,
                        )
                    elif net_home_kw < -threshold_kw:
                        available_kw = (
                            max(0.0, 1.0 - projected_soc) * cap / (eff * dt)
                        )
                        charge_future_kw = min(
                            self.max_charge_kw,
                            -net_home_kw,
                            available_kw,
                        )
                        discharge_future_kw = 0.0
                    else:
                        charge_future_kw = 0.0
                        discharge_future_kw = 0.0
                else:
                    charge_future_kw = max(0.0, battery_charge[future_idx])
                    discharge_future_kw = max(0.0, battery_discharge[future_idx])
                    intentional_export = (
                        grid_export[future_idx] > threshold_kw
                        and discharge_future_kw > threshold_kw
                    )
                    if intentional_export:
                        configured_floor = (
                            self._configured_export_reserve_floor_for_range(
                                future_idx,
                                future_idx + 1,
                            )
                        )
                        future_export_floor = max(
                            optimizer_reserve,
                            configured_floor,
                        )
                        export_room_kw = (
                            max(0.0, projected_soc - future_export_floor)
                            * cap
                            * eff
                            / dt
                        )
                        if export_room_kw <= threshold_kw:
                            net_home_kw = max(
                                0.0,
                                load[future_idx] - solar[future_idx],
                            )
                            natural_room_kw = (
                                max(
                                    0.0,
                                    projected_soc - self_consumption_floor,
                                )
                                * cap
                                * eff
                                / dt
                            )
                            discharge_future_kw = min(
                                self.max_discharge_kw,
                                net_home_kw,
                                natural_room_kw,
                            )
                        else:
                            discharge_future_kw = min(
                                discharge_future_kw,
                                export_room_kw,
                            )
                projected_soc += (
                    charge_future_kw * eff - discharge_future_kw / eff
                ) * dt / cap
                projected_soc = max(
                    self_consumption_floor,
                    min(1.0, projected_soc),
                )

            return projected_soc < self.pre_window_soc_target - 0.0001

        for t in range(n):
            ts = (
                schedule_timestamps[t]
                if schedule_timestamps and t < len(schedule_timestamps)
                else now + timedelta(minutes=t * self.interval_minutes)
            )
            configured_export_floor = self._configured_export_reserve_floor_for_range(
                t, t + 1
            )
            export_floor = max(optimizer_reserve, configured_export_floor)
            natural_floor = self_consumption_floor

            charge_kw = battery_charge[t]
            discharge_kw = battery_discharge[t]
            import_kw = grid_import[t]
            export_kw = grid_export[t]
            charge_blocked = block_battery_charge[t]
            priority_export_slot = (
                t < len(priority_export_slots)
                and priority_export_slots[t]
                and export_prices is not None
                and t < len(export_prices)
                and export_prices[t] > 0.001
            )
            free_import_slot = (
                import_prices is not None
                and import_prices[t] <= 0.001
                and not charge_blocked
                and allow_grid_charge
                and grid_charge_allowed[t]
            )

            # Determine action
            if free_import_slot:
                # Free electricity — always request force charge for the full
                # feasible slot so the action plan does not oscillate with the LP.
                action = "charge"
                full_slot_w = (
                    self._charge_limit_kw(load[t], solar[t], True) * 1000
                    if self.max_grid_import_w is not None
                    else self.max_charge_w
                )
                power_w = max(charge_kw * 1000, full_slot_w)
            elif charge_kw > threshold_kw and import_kw > (
                max(0.0, load[t] - solar[t]) + threshold_kw
            ):
                # Grid draw exceeds the net home load (load minus solar), so
                # the surplus grid power is charging the battery. Comparing
                # against net load — not total load — is essential: with
                # concurrent solar, charge power can be below total solar yet
                # still grid-sourced (load > solar), and that must still count
                # as grid charging rather than self-consumption.
                action = "charge"
                power_w = charge_kw * 1000
            elif export_kw > threshold_kw and discharge_kw > threshold_kw:
                # Battery discharging AND power going to grid → exporting
                action = "export"
                power_w = export_kw * 1000
            elif (
                charge_kw < threshold_kw
                and discharge_kw < threshold_kw
                and import_kw > threshold_kw
            ):
                # Battery idle while home draws from grid.
                # Only use IDLE when there's a clear profit from holding
                # battery for a future export window. Otherwise, prefer
                # self_consumption — the battery naturally serves load,
                # avoiding expensive grid import.
                meaningful_hold = soc > self.backup_reserve + 0.05
                preserve_charge_by_time_hold = (
                    _charge_by_time_hold_required(t, soc)
                )
                preserve_recovery_hold = (
                    not disable_idle
                    and soc <= optimizer_reserve
                    and soc
                    <= self_consumption_floor
                    + BELOW_RESERVE_RECOVERY_HOLD_MARGIN_SOC
                    and _future_grid_charge_planned(t)
                )
                if preserve_charge_by_time_hold or preserve_recovery_hold:
                    action = "idle"
                elif disable_idle:
                    action = "self_consumption"
                elif meaningful_hold and export_prices is not None and import_prices is not None:
                    # Check if upcoming export prices justify holding battery
                    # over letting it serve load (avoiding import cost).
                    # Need: export_price > import_price / efficiency
                    # (export revenue must exceed the avoided import after losses)
                    cur_import = import_prices[t]
                    min_export_premium = cur_import / eff + 0.02  # +2c/kWh buffer
                    # Look ahead up to 6 hours for a worthwhile export window
                    lookahead = min(n, t + 6 * 60 // self.interval_minutes)
                    best_export = max(
                        (export_prices[k] for k in range(t, lookahead)),
                        default=0,
                    )
                    if best_export >= min_export_premium:
                        action = "idle"
                    else:
                        action = "self_consumption"
                elif meaningful_hold:
                    action = "idle"
                else:
                    # At or below the optimizer reserve, stay in
                    # self_consumption. IDLE is a separate hold strategy for
                    # preserving useful SOC above that floor for a future
                    # export/avoidance window.
                    action = "self_consumption"
                power_w = 0.0
            else:
                # Natural self-consumption: solar charging or battery serving load
                action = "self_consumption"
                if discharge_kw > threshold_kw:
                    power_w = discharge_kw * 1000
                elif charge_kw > threshold_kw:
                    power_w = charge_kw * 1000
                else:
                    power_w = 0.0

            reported_charge_w = charge_kw * 1000
            reported_discharge_w = discharge_kw * 1000
            if free_import_slot and action == "charge":
                reported_charge_w = power_w
                reported_discharge_w = 0.0
            elif action in ("discharge", "export"):
                export_room_kw = (
                    max(0.0, soc - export_floor) * cap * eff / dt
                    if cap > 0 and dt > 0
                    else 0.0
                )
                if export_room_kw <= threshold_kw:
                    net_home_kw = max(0.0, load[t] - solar[t])
                    natural_room_kw = (
                        max(0.0, soc - natural_floor) * cap * eff / dt
                        if cap > 0 and dt > 0
                        else 0.0
                    )
                    natural_discharge_kw = min(
                        self.max_discharge_kw,
                        net_home_kw,
                        max(0.0, natural_room_kw),
                    )
                    action = "self_consumption"
                    power_w = natural_discharge_kw * 1000
                    reported_charge_w = 0.0
                    reported_discharge_w = natural_discharge_kw * 1000
                elif discharge_kw > export_room_kw:
                    capped_discharge_w = export_room_kw * 1000
                    reported_charge_w = 0.0
                    reported_discharge_w = capped_discharge_w
                    power_w = min(power_w, capped_discharge_w)
            elif action == "self_consumption" and discharge_kw >= threshold_kw:
                net_home_kw = max(0.0, load[t] - solar[t])
                natural_room_kw = (
                    max(0.0, soc - natural_floor) * cap * eff / dt
                    if cap > 0 and dt > 0
                    else 0.0
                )
                natural_discharge_kw = min(
                    self.max_discharge_kw,
                    net_home_kw,
                    discharge_kw,
                    max(0.0, natural_room_kw),
                )
                reported_charge_w = 0.0
                reported_discharge_w = natural_discharge_kw * 1000
                power_w = natural_discharge_kw * 1000
            elif (
                action == "self_consumption"
                and charge_kw < threshold_kw
                and discharge_kw < threshold_kw
            ):
                net_home_kw = load[t] - solar[t]
                if net_home_kw > threshold_kw:
                    available_kw = (
                        max(0.0, soc - natural_floor) * cap * eff / dt
                    )
                    natural_discharge_kw = min(
                        self.max_discharge_kw,
                        net_home_kw,
                        max(0.0, available_kw),
                    )
                    reported_discharge_w = natural_discharge_kw * 1000
                    reported_charge_w = 0.0
                    power_w = natural_discharge_kw * 1000
                elif net_home_kw < -threshold_kw and not charge_blocked:
                    available_kw = (1.0 - soc) * cap / (eff * dt)
                    natural_charge_kw = min(
                        self.max_charge_kw,
                        -net_home_kw,
                        max(0.0, available_kw),
                    )
                    reported_charge_w = natural_charge_kw * 1000
                    reported_discharge_w = 0.0
                    power_w = natural_charge_kw * 1000

            if action == "charge" and reported_charge_w > 0:
                solar_surplus_w = max(0.0, solar[t] - load[t]) * 1000.0
                solar_charge_w = min(reported_charge_w, solar_surplus_w)
                grid_charge_w = max(0.0, reported_charge_w - solar_charge_w)
                if remaining_grid_charge_stored_kwh != float("inf"):
                    allowed_grid_charge_w = (
                        remaining_grid_charge_stored_kwh
                        * 1000.0
                        / max(eff * dt, 1e-9)
                    )
                    grid_charge_w = min(grid_charge_w, allowed_grid_charge_w)
                reported_charge_w = solar_charge_w + grid_charge_w
                if remaining_grid_charge_stored_kwh != float("inf"):
                    remaining_grid_charge_stored_kwh = max(
                        0.0,
                        remaining_grid_charge_stored_kwh
                        - grid_charge_w / 1000.0 * eff * dt,
                    )
                power_w = min(power_w, reported_charge_w)
                if grid_charge_w <= ACTION_THRESHOLD_W:
                    action = "self_consumption"
                    power_w = reported_charge_w

            effective_charge_kw = reported_charge_w / 1000
            effective_discharge_kw = reported_discharge_w / 1000
            soc += (effective_charge_kw * eff - effective_discharge_kw / eff) * dt / cap
            # Floor the *reported* SOC at the real reserve only. The export floor
            # already gates discharge and export through the room calculations
            # above; using it here as a lower clamp would inflate a genuinely-low
            # SOC up to the export floor — e.g. plotting the battery at the 45%
            # export floor while it is really at 23%, and reporting that inflated
            # value as minimum_forecast_soc.
            soc = max(self_consumption_floor, min(1.0, soc))

            actions.append(ScheduleAction(
                timestamp=ts,
                action=action,
                power_w=round(power_w, 1),
                soc=round(soc, 4),
                battery_charge_w=round(reported_charge_w, 1),
                battery_discharge_w=round(reported_discharge_w, 1),
            ))

        return OptimizationSchedule(
            actions=actions,
            predicted_cost=0.0,
            predicted_savings=0.0,
            last_updated=now,
        )
    def reconcile_result_with_schedule(
        self,
        result: OptimizerResult,
        schedule: OptimizationSchedule,
        *,
        import_prices: list[float],
        export_prices: list[float],
        solar: list[float],
        load: list[float],
        export_bonus_prices: list[float] | None = None,
        export_bonus_cap_kwh: float | None = None,
        import_bonus_prices: list[float] | None = None,
        import_bonus_cap_kwh: float | None = None,
        initial_soc: float | None = None,
        optimizer_reserve: float | None = None,
    ) -> OptimizerResult:
        """Make result flows and economics describe the final emitted schedule."""
        actions = list(schedule.actions or [])
        if initial_soc is None and actions and actions[0].soc is not None:
            first = actions[0]
            first_delta = (
                max(0.0, first.battery_charge_w) / 1000.0 * self.efficiency
                - max(0.0, first.battery_discharge_w)
                / 1000.0
                / self.efficiency
            ) * self.dt_hours / self.capacity_kwh
            initial_soc = float(first.soc) - first_delta

        if initial_soc is not None and self.capacity_kwh > 0:
            soc_cursor = max(0.0, min(1.0, float(initial_soc)))
            modeled_backup_reserve = optimizer_reserve
            if modeled_backup_reserve is None:
                modeled_backup_reserve = (
                    result.modeled_backup_reserve
                    if result.modeled_backup_reserve is not None
                    else self.backup_reserve
                )
            modeled_backup_reserve = max(
                0.0,
                min(1.0, float(modeled_backup_reserve)),
            )
            if getattr(self, "hardware_reserve_known", False):
                physical_floor = min(
                    soc_cursor,
                    max(0.0, min(1.0, self.hardware_reserve)),
                )
            else:
                physical_floor = min(soc_cursor, modeled_backup_reserve)
            grid_charge_soc_cap = max(
                0.0,
                min(
                    1.0,
                    float(getattr(self, "grid_charge_soc_cap", 1.0) or 0.0),
                ),
            )
            remaining_grid_charge_stored_wh = (
                max(0.0, grid_charge_soc_cap - soc_cursor)
                * self.capacity_kwh
                * 1000.0
                if grid_charge_soc_cap < 0.999
                else float("inf")
            )

            def _modeled_export_floor(idx: int) -> float:
                floor = result.modeled_export_reserve_floor
                if floor is None:
                    floor = self._configured_export_reserve_floor_for_range(
                        idx, idx + 1
                    )
                slot_floors = result.modeled_export_reserve_floor_slots
                if slot_floors is not None and idx < len(slot_floors):
                    floor = max(float(floor or 0.0), float(slot_floors[idx] or 0.0))
                return max(0.0, min(1.0, float(floor or 0.0)))

            restamped: list[ScheduleAction] = []
            for idx, action in enumerate(actions):
                emitted_action = action.action
                effective_grid_charge_w = 0.0
                solar_w = (
                    max(0.0, float(solar[idx] or 0.0)) * 1000.0
                    if idx < len(solar)
                    else 0.0
                )
                load_w = (
                    max(0.0, float(load[idx] or 0.0)) * 1000.0
                    if idx < len(load)
                    else 0.0
                )
                net_home_w = max(0.0, load_w - solar_w)
                solar_surplus_w = max(0.0, solar_w - load_w)
                charge_w = max(0.0, float(action.battery_charge_w or 0.0))
                discharge_w = max(0.0, float(action.battery_discharge_w or 0.0))
                if action.action == "idle":
                    charge_w = 0.0
                    discharge_w = 0.0
                elif action.action == "charge":
                    discharge_w = 0.0
                elif action.action in ("export", "discharge"):
                    charge_w = 0.0
                elif action.action in ("self_consumption", "consume"):
                    # Natural operation can serve local imbalance only; it must
                    # not hide an unlabelled battery export or grid top-up.
                    charge_w = min(charge_w, solar_surplus_w)
                    discharge_w = min(discharge_w, net_home_w)
                elif action.action == "off_grid":
                    # Islanded operation must locally serve load or absorb solar
                    # before curtailing any remainder.
                    charge_w = solar_surplus_w
                    discharge_w = net_home_w

                charge_room_w = (
                    max(0.0, 1.0 - soc_cursor)
                    * self.capacity_kwh
                    * 1000.0
                    / max(self.efficiency * self.dt_hours, 1e-9)
                )
                charge_w = min(charge_w, self.max_charge_w, charge_room_w)
                if action.action == "charge" and charge_w > 0:
                    solar_charge_w = min(charge_w, solar_surplus_w)
                    grid_charge_w = max(0.0, charge_w - solar_charge_w)
                    if remaining_grid_charge_stored_wh != float("inf"):
                        allowed_grid_charge_w = (
                            remaining_grid_charge_stored_wh
                            / max(self.efficiency * self.dt_hours, 1e-9)
                        )
                        grid_charge_w = min(grid_charge_w, allowed_grid_charge_w)
                    charge_w = solar_charge_w + grid_charge_w
                    if remaining_grid_charge_stored_wh != float("inf"):
                        remaining_grid_charge_stored_wh = max(
                            0.0,
                            remaining_grid_charge_stored_wh
                            - grid_charge_w * self.efficiency * self.dt_hours,
                        )
                    effective_grid_charge_w = grid_charge_w

                discharge_floor = physical_floor
                if action.action in ("export", "discharge"):
                    discharge_floor = max(
                        discharge_floor,
                        modeled_backup_reserve,
                        _modeled_export_floor(idx),
                    )
                discharge_room_w = (
                    max(0.0, soc_cursor - discharge_floor)
                    * self.capacity_kwh
                    * 1000.0
                    * self.efficiency
                    / max(self.dt_hours, 1e-9)
                )
                discharge_w = min(
                    discharge_w,
                    self.max_discharge_w,
                    discharge_room_w,
                )

                if action.action in ("export", "discharge"):
                    # Preserve local load first, then cap the intentional battery
                    # export to the remaining site and battery export headroom.
                    home_discharge_w = min(discharge_w, net_home_w)
                    battery_export_w = max(0.0, discharge_w - home_discharge_w)
                    if self.max_battery_export_w is not None:
                        battery_export_w = min(
                            battery_export_w,
                            self.max_battery_export_w,
                        )
                    slot_export_limit_kw = self._grid_export_limit_kw_for_range(
                        idx, idx + 1
                    )
                    if slot_export_limit_kw is not None:
                        battery_export_w = min(
                            battery_export_w,
                            max(0.0, slot_export_limit_kw * 1000.0 - solar_surplus_w),
                        )
                    discharge_w = home_discharge_w + battery_export_w

                soc_cursor += (
                    charge_w / 1000.0 * self.efficiency
                    - discharge_w / 1000.0 / self.efficiency
                ) * self.dt_hours / self.capacity_kwh
                soc_cursor = max(physical_floor, min(1.0, soc_cursor))

                power_w = float(action.power_w or 0.0)
                if action.action == "charge":
                    if effective_grid_charge_w <= ACTION_THRESHOLD_W:
                        emitted_action = "self_consumption"
                        power_w = charge_w
                    else:
                        power_w = min(power_w, charge_w)
                elif action.action in ("export", "discharge"):
                    home_discharge_w = min(
                        discharge_w,
                        net_home_w,
                    )
                    battery_export_w = max(0.0, discharge_w - home_discharge_w)
                    if battery_export_w <= ACTION_THRESHOLD_W:
                        emitted_action = "self_consumption"
                        power_w = discharge_w
                    else:
                        power_w = battery_export_w
                elif action.action in ("self_consumption", "consume", "off_grid"):
                    power_w = discharge_w if discharge_w > 0 else charge_w
                else:
                    power_w = 0.0
                restamped.append(
                    ScheduleAction(
                        timestamp=action.timestamp,
                        action=emitted_action,
                        power_w=round(power_w, 1),
                        soc=round(soc_cursor, 4),
                        battery_charge_w=round(charge_w, 1),
                        battery_discharge_w=round(discharge_w, 1),
                    )
                )
            schedule = OptimizationSchedule(
                actions=restamped,
                predicted_cost=schedule.predicted_cost,
                predicted_savings=schedule.predicted_savings,
                last_updated=schedule.last_updated,
            )

        n = min(
            len(schedule.actions or []),
            len(import_prices),
            len(export_prices),
            len(solar),
            len(load),
        )
        export_bonus_prices = self._pad_array(export_bonus_prices, n, 0.0)
        import_bonus_prices = self._pad_array(import_bonus_prices, n, 0.0)
        grid_import, grid_export = self._grid_flows_from_schedule(
            schedule,
            n,
            solar,
            load,
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
        schedule.predicted_cost = round(predicted_cost, 2)
        schedule.predicted_savings = round(baseline_cost - predicted_cost, 2)
        result.schedule = schedule
        result.grid_import_w = [value * 1000 for value in grid_import]
        result.grid_export_w = [value * 1000 for value in grid_export]
        if result.feasible:
            result.reserve_recommendation = self._build_reserve_recommendation(
                schedule,
                solar,
                load,
            )
        return result
    def _allocate_capped_bonus(
        self,
        flows: list[float],
        bonus_prices: list[float],
        cap_kwh: float | None,
        group_ids: list[str | None] | None = None,
        caps_by_group: dict[str, float] | None = None,
    ) -> list[float]:
        """Assign the first ``cap_kwh`` of bonus-priced flow to the bonus bucket."""
        bonus = [0.0] * len(flows)
        grouped = bool(caps_by_group and group_ids and any(group_ids))
        remaining_by_group = (
            {
                str(key): max(0.0, float(value))
                for key, value in (caps_by_group or {}).items()
            }
            if grouped
            else {"__all__": max(0.0, float(cap_kwh or 0.0))}
        )
        if not any(value > 0 for value in remaining_by_group.values()):
            return bonus
        dt = self.dt_hours
        for t in range(len(flows)):
            if t >= len(bonus_prices) or bonus_prices[t] <= 0:
                continue
            group = (
                str(group_ids[t])
                if grouped and t < len(group_ids or []) and group_ids[t] is not None
                else "__all__"
            )
            remaining = remaining_by_group.get(group, 0.0)
            take_kw = min(flows[t], remaining / dt)
            if take_kw <= 0:
                continue
            bonus[t] = take_kw
            remaining_by_group[group] = max(0.0, remaining - take_kw * dt)
        return bonus





