"""Extracted reserve_policy helpers for OptimizationCoordinator (architecture refactor Phase 4)."""
from __future__ import annotations

import logging
import math
from typing import Any

from .action_constants import EXPORT_ACTIONS
from .results import OptimizerResult
from .schedule_reader import OptimizationSchedule, ScheduleAction


_LOGGER = logging.getLogger(__name__)


class ReservePolicyMixin:
    """Mixin providing extracted coordinator behavior."""

    def _force_discharge_reserve_floor(self, action: Any | None = None) -> float:
        """Return the software floor used before force discharge/export commands."""
        # Auto-Apply may update the configured optimizer reserve, but there is
        # no second hidden home-load bridge floor. Runtime export protection
        # therefore uses the same active reserve the solver modeled.
        floor = self._reserve_ratio(self._config.backup_reserve, 0.0) or 0.0
        return max(0.0, min(1.0, floor))

    def _set_forecast_bridge_reserve_recommendation(
        self,
        result: OptimizerResult,
        export_allowed: list[bool],
        solar_forecast: list[float] | None,
        load_forecast: list[float] | None,
    ) -> None:
        """Set a seed-independent reserve that preserves the manual buffer.

        The optimizer reserve only constrains intentional export; natural home
        consumption can continue to the hardware reserve. Auto-Apply therefore
        has to leave enough energy at the end of an eligible export window to
        cover forecast net home load until the next charge opportunity, plus
        the user's saved manual buffer.

        Use the full export-eligible window rather than the last emitted export
        action. Otherwise a higher starting floor shortens the emitted export
        run, lengthens the apparent bridge, and ratchets its own recommendation
        upward; a lower starting floor can similarly collapse to 0%.
        """
        if not self.auto_apply_reserve_enabled:
            return

        schedule = getattr(result, "schedule", None)
        actions = list(getattr(schedule, "actions", None) or [])
        slot_count = min(
            len(actions),
            len(export_allowed),
        )
        if slot_count <= 0:
            return

        manual_reserve = self._reserve_ratio(
            getattr(self, "_manual_backup_reserve", None),
            self._config.backup_reserve,
        )
        if manual_reserve is None:
            return
        baseline = max(self._hardware_reserve_ratio(), manual_reserve)

        capacity_kwh = max(
            0.0,
            float(getattr(self._config, "battery_capacity_wh", 0) or 0)
            / 1000.0,
        )
        efficiency = max(
            0.001,
            float(
                getattr(getattr(self, "_optimizer", None), "efficiency", 0.95)
                or 0.95
            ),
        )
        interval_hours = max(
            1,
            int(getattr(self._config, "interval_minutes", 5) or 5),
        ) / 60.0

        recommendation = dict(
            getattr(result, "reserve_recommendation", {}) or {}
        )
        best_target = baseline
        best_meta: dict[str, Any] = {}

        def _forecast_kw(values: list[float] | None, index: int) -> float:
            if not values or index >= len(values):
                return 0.0
            try:
                return max(0.0, float(values[index]))
            except (TypeError, ValueError):
                return 0.0

        idx = 0
        while idx < slot_count:
            if not bool(export_allowed[idx]):
                idx += 1
                continue

            window_start = idx
            while idx < slot_count and bool(export_allowed[idx]):
                idx += 1
            window_end = idx

            has_planned_export = any(
                getattr(actions[action_idx], "action", None) in EXPORT_ACTIONS
                and float(
                    getattr(actions[action_idx], "battery_discharge_w", None)
                    or getattr(actions[action_idx], "power_w", 0.0)
                    or 0.0
                )
                > 100.0
                for action_idx in range(window_start, window_end)
            )
            if not has_planned_export:
                continue

            next_charge_idx: int | None = None
            next_charge_reason: str | None = None
            for scan_idx in range(window_end, slot_count):
                action = actions[scan_idx]
                if float(getattr(action, "battery_charge_w", 0.0) or 0.0) > 100.0:
                    next_charge_idx = scan_idx
                    next_charge_reason = (
                        "scheduled_grid_charge"
                        if getattr(action, "action", None) == "charge"
                        else "forecast_solar_surplus"
                    )
                    break
                if (
                    _forecast_kw(solar_forecast, scan_idx)
                    - _forecast_kw(load_forecast, scan_idx)
                    > 0.1
                ):
                    next_charge_idx = scan_idx
                    next_charge_reason = "forecast_solar_surplus"
                    break

            bridge_end = (
                next_charge_idx if next_charge_idx is not None else slot_count
            )
            bridge_kwh = sum(
                max(
                    0.0,
                    _forecast_kw(load_forecast, bridge_idx)
                    - _forecast_kw(solar_forecast, bridge_idx),
                )
                * interval_hours
                for bridge_idx in range(window_end, bridge_end)
            )
            bridge_soc = (
                bridge_kwh / max(capacity_kwh * efficiency, 0.001)
                if capacity_kwh > 0
                else 0.0
            )
            target = max(baseline, min(1.0, baseline + bridge_soc))
            if target <= best_target + 0.0001:
                continue

            best_target = target
            protects_until_idx = (
                next_charge_idx
                if next_charge_idx is not None
                else slot_count - 1
            )
            best_meta = {
                "forecast_bridge_kwh": round(bridge_kwh, 3),
                "forecast_bridge_reserve_percent": int(
                    math.ceil(bridge_soc * 100 - 1e-9)
                ),
                "forecast_bridge_export_window_start": actions[
                    window_start
                ].timestamp.isoformat(),
                "forecast_bridge_export_window_end": actions[
                    window_end - 1
                ].timestamp.isoformat(),
                "protects_until": actions[protects_until_idx].timestamp.isoformat(),
                "next_charge_reason": (
                    next_charge_reason or "no_charge_in_horizon"
                ),
            }

        recommendation.update(best_meta)
        recommendation["manual_optimizer_reserve_percent"] = int(
            round(manual_reserve * 100)
        )
        recommendation["suggested_optimizer_reserve_percent"] = int(
            math.ceil(best_target * 100 - 1e-9)
        )
        recommendation["needs_optimizer_reserve_raise"] = (
            best_target
            > (self._reserve_ratio(self._config.backup_reserve, 0.0) or 0.0)
            + 0.0001
        )
        result.reserve_recommendation = recommendation

    def _apply_auto_reserve_recommendation(
        self,
        result: OptimizerResult,
    ) -> bool:
        """Apply one forecast optimizer reserve update after a solve."""
        if not bool(getattr(self, "_auto_apply_reserve_enabled", False)):
            return False
        # Never act on an infeasible safety fallback. It deliberately returns
        # no economic reserve recommendation, so Auto-Apply must wait for the
        # next successful solve rather than ratcheting from a degraded plan.
        if not bool(getattr(result, "feasible", True)):
            return False
        recommendation = getattr(result, "reserve_recommendation", {}) or {}
        target_ratio = self._recommended_auto_reserve_ratio(recommendation)
        if target_ratio is None:
            return False
        current_ratio = self._reserve_ratio(self._config.backup_reserve, 0.0) or 0.0
        recommendation["auto_apply_enabled"] = True
        manual_reserve = getattr(self, "_manual_backup_reserve", None)
        if manual_reserve is not None:
            manual_reserve = self._reserve_ratio(manual_reserve, None)
        if manual_reserve is not None:
            recommendation["manual_optimizer_reserve_percent"] = int(
                round(manual_reserve * 100)
            )
        recommendation["applied_optimizer_reserve_percent"] = int(
            round(current_ratio * 100)
        )
        if math.isclose(target_ratio, current_ratio, abs_tol=0.0001):
            return False

        # Apply the forecast floor to the running optimiser ONLY. This value is
        # recomputed every solve, so it must not be written to the config entry:
        # persisting it each cycle fired HA's config-entry-updated event every
        # ~5 minutes, refreshing the dashboard (and risking reload churn) for a
        # purely transient value. The live reserve is still surfaced to sensors
        # and the mobile app via get_api_data (self._config.backup_reserve), and
        # it is recomputed from the manual baseline within one solve of a restart.
        self.update_config(backup_reserve=target_ratio)
        recommendation["applied_optimizer_reserve_percent"] = int(
            round(target_ratio * 100)
        )
        _LOGGER.info(
            "Auto-Apply Optimizer Reserve: applied forecast floor %.0f%% "
            "(was %.0f%%)",
            target_ratio * 100,
            current_ratio * 100,
        )
        return True

    def _sync_brand_restore_targets(self, reserve_pct: int) -> None:
        """Push a live backup-reserve change to the brand's hardware restore
        target (OB-22, Sigenergy-only).

        Sigenergy's ``restore_normal()`` writes hardware from a separate
        ``SigenergyController`` instance's ``_restore_backup_reserve_pct``,
        which is only ever set at initial ``async_setup_entry`` and is not
        the same object as ``self._executor.battery_controller`` (a thin
        ``BatteryControllerWrapper``). Without this, a live reserve change
        made without a reload survives only until the next force/restore
        cycle, when hardware is written back to the stale value. No-op for
        every other brand.
        """
        if getattr(self, "battery_system", None) != "sigenergy":
            return
        from ..const import DOMAIN as _SYNC_DOMAIN
        entry_data = self.hass.data.get(_SYNC_DOMAIN, {}).get(self.entry_id, {})
        sigenergy_coordinator = entry_data.get("sigenergy_coordinator")
        ctrl = getattr(sigenergy_coordinator, "_controller", None)
        if ctrl is not None and hasattr(ctrl, "_restore_backup_reserve_pct"):
            ctrl._restore_backup_reserve_pct = int(reserve_pct)

