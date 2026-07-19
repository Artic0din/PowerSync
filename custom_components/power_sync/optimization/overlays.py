"""Extracted overlays helpers for OptimizationCoordinator (architecture refactor Phase 4)."""
from __future__ import annotations

import math

import logging
from typing import Any

from homeassistant.util import dt as dt_util

from .action_constants import EXPORT_ACTIONS, SELF_USE_ACTIONS
from .schedule_reader import OptimizationSchedule, ScheduleAction

_LOGGER = logging.getLogger(__name__)


class OverlaysMixin:
    """Mixin providing overlays behavior. Host class supplies runtime attrs."""

    def _bridge_short_export_gaps(
        self,
        schedule: OptimizationSchedule,
        export_prices: list[float] | None = None,
        export_reserve_floor: float | list[float] | None = None,
    ) -> OptimizationSchedule:
        """Keep export mode through one-slot self-use islands between exports."""
        actions = getattr(schedule, "actions", None) or []
        if len(actions) < 3:
            return schedule
        if self._dynamic_export_prices_can_have_real_one_slot_gaps():
            return schedule

        interval = max(1, int(getattr(self._config, "interval_minutes", 5) or 5))
        max_gap_slots = 1
        bridged = 0
        idx = 1
        while idx < len(actions) - 1:
            action_name = getattr(actions[idx], "action", None)
            if action_name not in SELF_USE_ACTIONS:
                idx += 1
                continue

            gap_start = idx
            while idx < len(actions) - 1 and getattr(actions[idx], "action", None) in SELF_USE_ACTIONS:
                idx += 1
            gap_end = idx
            gap_slots = gap_end - gap_start

            previous_action = actions[gap_start - 1]
            next_action = actions[gap_end] if gap_end < len(actions) else None
            if (
                gap_slots > max_gap_slots
                or getattr(previous_action, "action", None) not in EXPORT_ACTIONS
                or getattr(next_action, "action", None) not in EXPORT_ACTIONS
                or not self._short_export_gap_prices_match(
                    gap_start,
                    gap_end,
                    export_prices,
                )
            ):
                continue

            export_action = (
                "export"
                if "export" in {
                    getattr(previous_action, "action", None),
                    getattr(next_action, "action", None),
                }
                else "discharge"
            )
            bridge_power_w = self._bridged_export_power_w(
                previous_action,
                next_action,
            )
            gap_action = actions[gap_start]
            home_discharge_w = max(
                0.0,
                float(getattr(gap_action, "battery_discharge_w", 0.0) or 0.0),
            )
            max_discharge_w = max(
                0.0,
                float(getattr(self._config, "max_discharge_w", 0.0) or 0.0),
            )
            bridge_power_w = min(
                bridge_power_w,
                max(0.0, max_discharge_w - home_discharge_w),
            )
            if bridge_power_w <= 0:
                continue
            battery_discharge_w = home_discharge_w + bridge_power_w
            reserve_floor = self._bridge_export_reserve_floor(
                export_reserve_floor,
                gap_start,
                gap_end,
            )
            if not self._can_bridge_export_gap_above_reserve(
                previous_action,
                actions[gap_start:gap_end],
                battery_discharge_w,
                reserve_floor,
            ):
                continue

            for gap_action in actions[gap_start:gap_end]:
                gap_action.action = export_action
                gap_action.power_w = bridge_power_w
                gap_action.battery_charge_w = 0.0
                gap_action.battery_discharge_w = battery_discharge_w
                bridged_soc = self._bridged_gap_soc(
                    previous_action,
                    battery_discharge_w,
                )
                if bridged_soc is not None:
                    gap_action.soc = bridged_soc
                bridged += 1

        if bridged:
            _LOGGER.info(
                "Optimizer: bridged %dmin self-consumption gap inside export window",
                bridged * interval,
            )
        return schedule
    def _should_spread_export_schedule(self) -> bool:
        """Return True when optimizer export actions should be flattened."""
        return (
            self._config.spread_export_enabled
            and self._supports_target_export_power()
        )
    def _should_spread_import_schedule(self) -> bool:
        """Return True when optimizer charge actions should be flattened."""
        return (
            self._config.spread_import_enabled
            and self._supports_target_charge_power()
        )
    def _spread_import_schedule(
        self,
        schedule: OptimizationSchedule,
        import_prices: list[float] | None,
        blocked_slots: list[bool] | None,
        initial_soc: float,
        *,
        free_only: bool = False,
        solar_forecast: list[float] | None = None,
        load_forecast: list[float] | None = None,
    ) -> OptimizationSchedule:
        """Spread planned grid-charge energy across same-price import windows."""
        actions = list(schedule.actions or [])
        if not actions or not import_prices:
            return schedule

        n = len(actions)
        try:
            prices = [float(price) for price in import_prices[:n]]
        except (TypeError, ValueError):
            return schedule
        if len(prices) < n:
            return schedule

        blocked = [bool(value) for value in (blocked_slots or [])[:n]]
        if len(blocked) < n:
            blocked.extend([False] * (n - len(blocked)))

        interval_hours = max(1, int(self._config.interval_minutes or 5)) / 60.0
        capacity_wh = max(0.0, float(self._config.battery_capacity_wh or 0))
        efficiency = float(getattr(self._optimizer, "efficiency", 0.92) or 0.92)
        max_charge_w = max(0.0, float(self._config.max_charge_w or 0))
        max_grid_import_w = self._normalize_optional_power_w(
            self._config.max_grid_import_w
        )
        cap_by_slot = max_grid_import_w is not None
        new_actions: list[ScheduleAction] = list(actions)
        soc_cursor = max(0.0, min(1.0, float(initial_soc or 0.0)))

        def _forecast_kw(values: list[float] | None, pos: int) -> float:
            if not values or pos >= len(values):
                return 0.0
            try:
                return float(values[pos])
            except (TypeError, ValueError):
                return 0.0

        def _slot_charge_cap_w(pos: int) -> float:
            if max_grid_import_w is None:
                return max_charge_w
            load_w = _forecast_kw(load_forecast, pos) * 1000.0
            solar_w = _forecast_kw(solar_forecast, pos) * 1000.0
            return max(
                0.0,
                min(max_charge_w, max_grid_import_w - load_w + solar_w),
            )

        def _spread_power_by_cap(total_wh: float, caps_w: list[float]) -> list[float]:
            """Spread total Wh evenly while respecting per-slot caps."""
            if not caps_w:
                return []
            remaining = min(total_wh, sum(caps_w) * interval_hours)
            output = [0.0] * len(caps_w)
            open_slots = set(range(len(caps_w)))
            while open_slots and remaining > 1e-6:
                target_w = remaining / (len(open_slots) * interval_hours)
                capped_now = [
                    pos for pos in open_slots if caps_w[pos] <= target_w + 1e-6
                ]
                if not capped_now:
                    for pos in open_slots:
                        output[pos] = target_w
                    break
                for pos in capped_now:
                    output[pos] = caps_w[pos]
                    remaining -= caps_w[pos] * interval_hours
                    open_slots.remove(pos)
            return [round(max(0.0, value), 1) for value in output]

        def _advance_soc(soc: float, action: Any) -> float:
            if capacity_wh <= 0:
                return soc
            try:
                charge_w = max(0.0, float(getattr(action, "battery_charge_w", 0.0) or 0.0))
                discharge_w = max(0.0, float(getattr(action, "battery_discharge_w", 0.0) or 0.0))
            except (TypeError, ValueError):
                return soc
            stored_wh = charge_w * interval_hours * efficiency
            removed_wh = discharge_w * interval_hours / max(efficiency, 0.001)
            return max(0.0, min(1.0, soc + (stored_wh - removed_wh) / capacity_wh))

        idx = 0
        while idx < n:
            if blocked[idx] or getattr(actions[idx], "action", None) in ("discharge", "export"):
                soc_cursor = _advance_soc(soc_cursor, new_actions[idx])
                idx += 1
                continue

            start = idx
            price = prices[idx]
            while (
                idx < n
                and not blocked[idx]
                and getattr(actions[idx], "action", None) not in ("discharge", "export")
                and abs(prices[idx] - price) <= 1e-6
            ):
                idx += 1
            end = idx
            if free_only and not (math.isfinite(price) and price <= 0.001):
                for pos in range(start, end):
                    soc_cursor = _advance_soc(soc_cursor, new_actions[pos])
                continue

            window_actions = actions[start:end]
            charge_wh = sum(
                max(0.0, float(getattr(action, "battery_charge_w", 0.0) or 0.0))
                * interval_hours
                for action in window_actions
                if getattr(action, "action", None) == "charge"
            )
            if charge_wh <= 0 or max_charge_w <= 0:
                for pos in range(start, end):
                    soc_cursor = _advance_soc(soc_cursor, new_actions[pos])
                continue

            if price <= 0.001 and capacity_wh > 0:
                available_wh = max(0.0, (1.0 - soc_cursor) * capacity_wh / max(efficiency, 0.001))
                charge_wh = min(charge_wh, available_wh)
                if charge_wh <= 0:
                    for pos in range(start, end):
                        soc_cursor = _advance_soc(soc_cursor, new_actions[pos])
                    continue

            if cap_by_slot:
                target_by_pos = _spread_power_by_cap(
                    charge_wh,
                    [_slot_charge_cap_w(pos) for pos in range(start, end)],
                )
            else:
                target_w = min(
                    max_charge_w,
                    charge_wh / (len(window_actions) * interval_hours),
                )
                target_w = round(max(0.0, target_w), 1)
                target_by_pos = [target_w] * len(window_actions)

            if not any(target_w > 0 for target_w in target_by_pos):
                for pos in range(start, end):
                    soc_cursor = _advance_soc(soc_cursor, new_actions[pos])
                continue

            for pos in range(start, end):
                original = actions[pos]
                target_w = target_by_pos[pos - start]
                if target_w > 0:
                    new_actions[pos] = ScheduleAction(
                        timestamp=original.timestamp,
                        action="charge",
                        power_w=target_w,
                        soc=original.soc,
                        battery_charge_w=target_w,
                        battery_discharge_w=0.0,
                    )
                else:
                    new_actions[pos] = ScheduleAction(
                        timestamp=original.timestamp,
                        action="self_consumption",
                        power_w=0.0,
                        soc=original.soc,
                        battery_charge_w=0.0,
                        battery_discharge_w=0.0,
                    )
                soc_cursor = _advance_soc(soc_cursor, new_actions[pos])
                new_actions[pos].soc = round(soc_cursor, 4)

        return OptimizationSchedule(
            actions=new_actions,
            predicted_cost=schedule.predicted_cost,
            predicted_savings=schedule.predicted_savings,
            last_updated=schedule.last_updated,
        )
    def _spread_export_schedule(
        self,
        schedule: OptimizationSchedule,
        allowed_slots: bool | list[bool],
        export_reserve_floor: float | list[float] | None = None,
    ) -> OptimizationSchedule:
        """Spread planned export energy across each contiguous allowed window."""
        actions = list(schedule.actions or [])
        if not actions:
            return schedule

        n = len(actions)
        if isinstance(allowed_slots, bool):
            allowed = [allowed_slots] * n
        else:
            allowed = [bool(v) for v in allowed_slots[:n]]
            if len(allowed) < n:
                allowed.extend([False] * (n - len(allowed)))

        interval_hours = max(1, int(self._config.interval_minutes or 5)) / 60.0
        capacity_wh = max(0.0, float(self._config.battery_capacity_wh or 0))
        efficiency = float(getattr(self._optimizer, "efficiency", 0.92) or 0.92)
        scoped_export_floors = (
            export_reserve_floor if isinstance(export_reserve_floor, list) else None
        )
        min_export_floor = (
            None
            if scoped_export_floors is not None
            else self._reserve_ratio(export_reserve_floor, None)
        )
        if min_export_floor is None and scoped_export_floors is None:
            min_export_floor = self._force_discharge_reserve_floor()
        new_actions: list[ScheduleAction] = list(actions)
        idx = 0

        def _action_soc(pos: int) -> float | None:
            if pos < 0 or pos >= len(new_actions):
                return None
            return self._reserve_ratio(getattr(new_actions[pos], "soc", None), None)

        def _battery_home_discharge_w(action: ScheduleAction) -> float:
            discharge_w = max(
                0.0,
                float(getattr(action, "battery_discharge_w", 0.0) or 0.0),
            )
            if getattr(action, "action", None) in SELF_USE_ACTIONS:
                return discharge_w
            if getattr(action, "action", None) in EXPORT_ACTIONS:
                export_w = max(
                    0.0,
                    min(
                        float(getattr(action, "power_w", 0.0) or 0.0),
                        discharge_w,
                    ),
                )
                return max(0.0, discharge_w - export_w)
            return 0.0

        def _advance_discharge_soc(soc: float, battery_discharge_w: float) -> float:
            if capacity_wh <= 0:
                return soc
            removed_wh = (
                max(0.0, battery_discharge_w)
                * interval_hours
                / max(efficiency, 0.001)
            )
            return max(0.0, min(1.0, soc - removed_wh / capacity_wh))

        def _advance_action_soc(soc: float, action: ScheduleAction) -> float:
            if capacity_wh <= 0:
                return soc
            charge_w = max(
                0.0,
                float(getattr(action, "battery_charge_w", 0.0) or 0.0),
            )
            discharge_w = max(
                0.0,
                float(getattr(action, "battery_discharge_w", 0.0) or 0.0),
            )
            stored_wh = charge_w * interval_hours * max(efficiency, 0.001)
            removed_wh = (
                discharge_w
                * interval_hours
                / max(efficiency, 0.001)
            )
            return max(
                0.0,
                min(1.0, soc + (stored_wh - removed_wh) / capacity_wh),
            )

        def _available_export_w(soc: float, floor: float) -> float:
            if capacity_wh <= 0:
                return 0.0
            available_wh = max(0.0, soc - floor) * capacity_wh
            return available_wh * max(efficiency, 0.001) / interval_hours

        while idx < n:
            if not allowed[idx]:
                idx += 1
                continue

            start = idx
            while idx < n and allowed[idx]:
                idx += 1
            end = idx
            window_floor = min_export_floor
            if scoped_export_floors is not None:
                scoped_window = scoped_export_floors[start:end]
                scoped_floor = max(scoped_window) if scoped_window else 0.0
                window_floor = (
                    scoped_floor
                    if scoped_floor > 0
                    else self._force_discharge_reserve_floor()
                )
            window_actions = actions[start:end]
            export_power_field = (
                "power_w"
                if self._supports_target_export_power()
                else "battery_discharge_w"
            )
            export_wh = sum(
                max(0.0, float(getattr(action, export_power_field, 0.0) or 0.0))
                * interval_hours
                for action in window_actions
                if getattr(action, "action", None) in ("export", "discharge")
            )
            if export_wh <= 0:
                continue

            spread_positions = [
                pos
                for pos in range(start, end)
                if getattr(actions[pos], "action", None) != "charge"
                and not (
                    float(getattr(actions[pos], "battery_charge_w", 0.0) or 0.0) > 0
                )
            ]
            floor = self._reserve_ratio(window_floor, None)
            first_export_pos = next(
                pos
                for pos in spread_positions
                if getattr(actions[pos], "action", None) in ("export", "discharge")
            )
            if floor is not None:
                # SOC labels after the first raw export describe the concentrated
                # LP plan that this pass is about to replace. Using those depleted
                # labels to select later slots makes the spread denominator collapse
                # at the reserve floor and leaves export pinned at the original cap.
                # Leading slots still use their pre-export SOC labels so a window
                # that begins below the floor cannot manufacture an export action.
                spread_positions = [
                    pos
                    for pos in spread_positions
                    if pos >= first_export_pos
                    or (
                        self._reserve_ratio(
                            getattr(actions[pos], "soc", None),
                            None,
                        )
                        or 0.0
                    )
                    > floor + 0.0001
                ]

            export_cap_w = (
                self._config.max_grid_export_w
                if self._config.max_grid_export_w is not None
                else self._config.max_discharge_w
            )
            export_cap_w = float(max(0, export_cap_w))
            remaining_export_w = export_wh / interval_hours
            unassigned_positions = list(spread_positions)
            target_by_position: dict[int, float] = {}
            while unassigned_positions and remaining_export_w > 0:
                equal_target_w = remaining_export_w / len(unassigned_positions)
                constrained_positions = []
                for pos in unassigned_positions:
                    home_discharge_w = _battery_home_discharge_w(actions[pos])
                    headroom_w = min(
                        export_cap_w,
                        max(
                            0.0,
                            float(self._config.max_discharge_w or 0)
                            - home_discharge_w,
                        ),
                    )
                    if headroom_w + 0.05 < equal_target_w:
                        target_by_position[pos] = headroom_w
                        remaining_export_w -= headroom_w
                        constrained_positions.append(pos)
                if not constrained_positions:
                    for pos in unassigned_positions:
                        target_by_position[pos] = equal_target_w
                    remaining_export_w = 0.0
                    break
                unassigned_positions = [
                    pos for pos in unassigned_positions if pos not in constrained_positions
                ]

            target_by_position = {
                pos: round(max(0.0, target_by_position.get(pos, 0.0)), 1)
                for pos in spread_positions
            }
            if not any(target_by_position.values()):
                fallback_soc = _action_soc(start - 1)
                if fallback_soc is None:
                    fallback_soc = _action_soc(start)
                fallback_cursor = fallback_soc
                for pos in spread_positions:
                    original = actions[pos]
                    if getattr(original, "action", None) in ("export", "discharge"):
                        home_discharge_w = _battery_home_discharge_w(original)
                        soc_after = (
                            _advance_discharge_soc(fallback_cursor, home_discharge_w)
                            if fallback_cursor is not None
                            else original.soc
                        )
                        new_actions[pos] = ScheduleAction(
                            timestamp=original.timestamp,
                            action="self_consumption",
                            power_w=home_discharge_w,
                            soc=(
                                round(soc_after, 4)
                                if fallback_cursor is not None
                                else original.soc
                            ),
                            battery_charge_w=0.0,
                            battery_discharge_w=home_discharge_w,
                        )
                        if fallback_cursor is not None:
                            fallback_cursor = soc_after
                continue

            soc_cursor = _action_soc(start - 1)
            if soc_cursor is None:
                soc_cursor = _action_soc(start)
            spread_position_set = set(spread_positions)
            for pos in range(start, end):
                original = actions[pos]
                if pos not in spread_position_set:
                    if soc_cursor is not None:
                        soc_cursor = _advance_action_soc(soc_cursor, original)
                    continue
                home_discharge_w = _battery_home_discharge_w(original)
                slot_target_w = target_by_position[pos]
                slot_target_w = min(
                    slot_target_w,
                    max(
                        0.0,
                        float(self._config.max_discharge_w or 0) - home_discharge_w,
                    ),
                )
                if floor is not None and soc_cursor is not None:
                    slot_target_w = min(
                        slot_target_w,
                        max(
                            0.0,
                            _available_export_w(soc_cursor, floor) - home_discharge_w,
                        ),
                    )
                    slot_target_w = round(max(0.0, slot_target_w), 1)
                if slot_target_w > 0:
                    battery_discharge_w = home_discharge_w + slot_target_w
                    soc_after = (
                        _advance_discharge_soc(soc_cursor, battery_discharge_w)
                        if soc_cursor is not None
                        else original.soc
                    )
                    new_actions[pos] = ScheduleAction(
                        timestamp=original.timestamp,
                        action="export",
                        power_w=slot_target_w,
                        soc=round(soc_after, 4) if soc_cursor is not None else original.soc,
                        battery_charge_w=0.0,
                        battery_discharge_w=battery_discharge_w,
                    )
                    if soc_cursor is not None:
                        soc_cursor = soc_after
                else:
                    battery_discharge_w = home_discharge_w
                    soc_after = (
                        _advance_discharge_soc(soc_cursor, battery_discharge_w)
                        if soc_cursor is not None
                        else original.soc
                    )
                    new_actions[pos] = ScheduleAction(
                        timestamp=original.timestamp,
                        action="self_consumption",
                        power_w=battery_discharge_w,
                        soc=(
                            round(soc_after, 4)
                            if soc_cursor is not None
                            else original.soc
                        ),
                        battery_charge_w=0.0,
                        battery_discharge_w=battery_discharge_w,
                    )
                    if soc_cursor is not None:
                        soc_cursor = soc_after

        return OptimizationSchedule(
            actions=new_actions,
            predicted_cost=schedule.predicted_cost,
            predicted_savings=schedule.predicted_savings,
            last_updated=schedule.last_updated,
        )
    def _should_apply_offgrid_overlay(self) -> bool:
        """Check if off-grid curtailment overlay should be applied."""
        from ..const import (
            CONF_POWERWALL_OFFGRID_AS_CURTAILMENT,
            CONF_POWERWALL_LOCAL_PAIRED,
            DEFAULT_POWERWALL_OFFGRID_AS_CURTAILMENT,
        )
        if not self._entry:
            return False
        entry = self._entry
        enabled = entry.options.get(
            CONF_POWERWALL_OFFGRID_AS_CURTAILMENT,
            entry.data.get(
                CONF_POWERWALL_OFFGRID_AS_CURTAILMENT,
                DEFAULT_POWERWALL_OFFGRID_AS_CURTAILMENT,
            ),
        )
        paired = entry.data.get(CONF_POWERWALL_LOCAL_PAIRED, False)
        battery_type = entry.data.get("battery_system", "")
        return bool(enabled and paired and battery_type == "tesla")
    def _apply_offgrid_overlay(
        self,
        schedule: "OptimizationSchedule",
        export_prices: list[float],
    ) -> "OptimizationSchedule":
        """Post-LP overlay: mark eligible slots as OFF_GRID.

        A slot is eligible when:
          - export_price < threshold (negative/zero value export)
          - LP action is self_consumption or idle (grid not actively needed)
          - projected SOC is at or above FULL threshold (battery can't
            absorb more — otherwise we should charge instead of curtail)

        Only marks contiguous runs of >= _OFFGRID_MIN_CONSECUTIVE slots.
        Inserts a reconnect buffer (self_consumption) before any CHARGE
        slot that follows an off-grid run.
        """
        actions = getattr(schedule, "actions", None)
        if not actions or not export_prices:
            return schedule

        # ScheduleAction.soc is a 0-1 fraction; the threshold constant is a
        # percentage, so compare against the fractional equivalent.
        soc_floor = self._OFFGRID_FULL_SOC_THRESHOLD / 100.0
        n = min(len(actions), len(export_prices))

        # Step 1: flag each slot as eligible
        eligible = []
        for t in range(n):
            action = actions[t]
            act = action.action
            price = export_prices[t] if t < len(export_prices) else 1.0
            soc = action.soc

            is_eligible = (
                price < self._OFFGRID_EXPORT_THRESHOLD
                and act in ("self_consumption", "idle")
                and soc is not None
                and soc >= soc_floor
            )
            eligible.append(is_eligible)

        # Step 2: find contiguous runs of eligible slots
        # and mark them as off_grid if long enough
        result = list(actions)
        t = 0
        while t < n:
            if not eligible[t]:
                t += 1
                continue
            # Find the end of this eligible run
            run_start = t
            while t < n and eligible[t]:
                t += 1
            run_end = t  # exclusive
            run_length = run_end - run_start

            if run_length < self._OFFGRID_MIN_CONSECUTIVE:
                continue  # Too short — skip

            # Check if a CHARGE slot follows — need reconnect buffer
            next_action = ""
            if run_end < len(actions):
                next_action = actions[run_end].action

            # Mark slots as off_grid
            mark_end = run_end
            if next_action == "charge" and run_length > 1:
                # Leave last slot as self_consumption (reconnect buffer)
                mark_end = run_end - 1

            for i in range(run_start, mark_end):
                slot = result[i]
                # ScheduleAction dataclass — create a copy with new action
                from .schedule_reader import ScheduleAction
                result[i] = ScheduleAction(
                    timestamp=slot.timestamp,
                    action="off_grid",
                    power_w=slot.power_w,
                    soc=slot.soc,
                    battery_charge_w=slot.battery_charge_w,
                    battery_discharge_w=slot.battery_discharge_w,
                )

        offgrid_count = sum(1 for s in result if s.action == "off_grid")
        if offgrid_count > 0:
            _LOGGER.info(
                "Off-grid overlay: marked %d/%d slots as OFF_GRID "
                "(export threshold=%.1fc, SOC floor=%d%%)",
                offgrid_count, n, self._OFFGRID_EXPORT_THRESHOLD * 100,
                self._OFFGRID_FULL_SOC_THRESHOLD,
            )

        schedule.actions = result
        return schedule








