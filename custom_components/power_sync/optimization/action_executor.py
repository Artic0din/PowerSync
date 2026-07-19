"""Extracted action_executor helpers for OptimizationCoordinator (architecture refactor Phase 4)."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.util import dt as dt_util

from .action_constants import FORCED_ACTIONS, SELF_USE_ACTIONS, SUNGROW_INFERRED_RESTORE_COOLDOWN
from .battery_controller import TRUSTED_FOR_PERSIST


_LOGGER = logging.getLogger(__name__)


class ActionExecutorMixin:
    """Mixin providing extracted coordinator behavior."""

    async def _execute_optimizer_action(
        self,
        action: Any,
        *,
        execution_trigger: str | None = None,
    ) -> None:
        """Execute an optimizer action on the battery."""
        # Guard against a solve that was in flight when disable() ran (e.g. an
        # untracked price-triggered re-optimization) completing afterwards and
        # re-commanding the battery. disable() sets _enabled=False before
        # cancelling background tasks, so any execution reaching this point
        # after that must be a no-op. Default to True (enabled) when the
        # attribute is entirely unset — real coordinators always set it
        # explicitly in __init__/enable()/disable(); only lightweight test
        # doubles built via object.__new__() omit it, and they expect this
        # method to behave as if the optimizer is running.
        if not getattr(self, "_enabled", True):
            return
        if not self._executor or not self._executor.battery_controller:
            return

        # Monitoring mode — log what would happen but don't execute
        if self._monitoring_mode_active():
            _LOGGER.info(
                "[MONITORING] Optimizer would execute: %s (power=%sW) — blocked by monitoring mode",
                action.action, getattr(action, 'power_w', 'N/A'),
            )
            return

        battery = self._executor.battery_controller

        # Check if force charge/discharge is active.
        # User-triggered force modes own the battery state — don't override.
        # Optimizer-triggered force modes can be overridden if the LP changes
        # its mind (e.g. LP planned 1 export step but now wants self_consumption).
        force_state = self._get_active_force_state()
        if force_state and force_state.get("active"):
                force_type = force_state.get("type", "unknown")
                force_source = force_state.get("source", "user")

                if force_source != "optimizer":
                    # User-triggered — never override
                    _LOGGER.debug(
                        "Optimizer: force %s active (user) — skipping action execution "
                        "(LP wants %s)",
                        force_type, action.action,
                    )
                    return

                # Optimizer-triggered: check if LP still wants the same action.
                # If the current slot no longer matches the active optimizer
                # force mode, restore immediately and let the next 5-minute LP
                # interval issue a fresh command if needed.
                def _action_matches_force(a) -> bool:
                    return (
                        (force_type == "discharge" and a.action in ("discharge", "export"))
                        or (force_type == "charge" and a.action == "charge")
                    )

                preserve_active_for_force = self._scheduled_ev_preserve_active()
                lp_matches_force = _action_matches_force(action)
                if preserve_active_for_force and force_type == "discharge":
                    lp_matches_force = False
                force_window_action = action

                if lp_matches_force:
                    if force_type == "discharge":
                        try:
                            soc_now, _ = await self._get_battery_state()
                            opt_reserve = self._force_discharge_reserve_floor(action)
                            reaches_reserve, projected_soc = (
                                self._force_discharge_reaches_reserve(
                                    action,
                                    soc_now,
                                    opt_reserve,
                                )
                            )
                            if reaches_reserve:
                                soc_text = (
                                    f"{soc_now * 100:.1f}%"
                                    if soc_now is not None
                                    else "unknown"
                                )
                                projected_text = (
                                    f", projected {projected_soc * 100:.1f}%"
                                    if projected_soc is not None
                                    else ""
                                )
                                _LOGGER.warning(
                                    "Optimizer: Canceling active force discharge — "
                                    "SOC %s%s at/below optimizer reserve %.0f%%; "
                                    "restoring self_consumption instead of extending",
                                    soc_text,
                                    projected_text,
                                    opt_reserve * 100,
                                )
                                restore_success = True
                                if hasattr(battery, "restore_normal"):
                                    restore_success = await battery.restore_normal()
                                elif hasattr(battery, "set_self_consumption_mode"):
                                    restore_success = await battery.set_self_consumption_mode()
                                if restore_success is False:
                                    _LOGGER.warning(
                                        "Optimizer: Force-discharge reserve restore failed; "
                                        "retaining force state for retry"
                                    )
                                    return
                                if force_state.get("scope") == "optimizer":
                                    self._clear_optimizer_force_state()
                                elif self._force_state_clearer:
                                    self._force_state_clearer()
                                self._last_executed_planned_action = action.action
                                self._last_executed_action = "self_consumption"
                                return
                        except Exception as reserve_err:
                            _LOGGER.debug(
                                "Optimizer: reserve check before extending force "
                                "discharge failed: %s",
                                reserve_err,
                            )

                    if (
                        force_type == "charge"
                        and self._tesla_force_charge_should_yield_to_live_solar(
                            action
                        )
                    ):
                        _LOGGER.info(
                            "Optimizer: Canceling active Tesla force charge — "
                            "live solar is available, restoring self_consumption"
                        )
                        if force_state.get("scope") == "optimizer":
                            self._clear_optimizer_force_state()
                        elif self._force_state_clearer:
                            self._force_state_clearer()
                        if hasattr(battery, "restore_normal"):
                            await battery.restore_normal()
                        elif hasattr(battery, "set_self_consumption_mode"):
                            await battery.set_self_consumption_mode()
                        self._last_executed_planned_action = action.action
                        self._last_executed_action = "self_consumption"
                        return

                    # Extend the expiry timer so the force mode doesn't expire
                    # between optimizer cycles (avoids restore→re-issue gap).
                    from ..const import DOMAIN as _EXT_DOMAIN
                    _ext_data = self.hass.data.get(_EXT_DOMAIN, {}).get(self.entry_id, {})
                    force_scope = force_state.get("scope", "external")
                    if force_scope == "optimizer":
                        _ext_state = self._optimizer_force_state
                    else:
                        _ext_state = _ext_data.get(
                            "force_discharge_state" if force_type == "discharge" else "force_charge_state", {}
                        )
                        if _ext_state.get("cancel_expiry_timer"):
                            _ext_state["cancel_expiry_timer"]()  # Cancel old timer
                    matching_actions = (
                        {"charge"}
                        if force_type == "charge"
                        else {"discharge", "export"}
                    )
                    extend_mins = self._force_duration_for_action_window(
                        force_window_action,
                        matching_actions,
                        allow_boundary_overrun=False,
                        minimum_minutes=self._config.interval_minutes + 5,
                    )
                    tariff_mins = (
                        self._tesla_tariff_duration_for_force_window(extend_mins)
                        if force_type == "discharge"
                        else None
                    )
                    force_power_w = (
                        self._charge_command_power_w(force_window_action)
                        if force_type == "charge"
                        else self._export_command_power_w(force_window_action)
                    )
                    new_expiry = dt_util.utcnow() + timedelta(minutes=extend_mins)
                    hardware_expiry = self._as_utc_datetime(_ext_state.get("hardware_expires_at"))
                    supports_force_power_refresh = (
                        (
                            force_type == "charge"
                            and self._supports_target_charge_power()
                        )
                        or (
                            force_type == "discharge"
                            and self._supports_target_export_power()
                        )
                    )
                    hardware_power_changed = (
                        supports_force_power_refresh
                        and self._force_command_power_changed(
                            _ext_state.get("power_w"),
                            force_power_w,
                        )
                    )
                    if force_scope == "optimizer":
                        now = dt_util.utcnow()
                        refresh_window = timedelta(
                            minutes=max(
                                1,
                                int(getattr(self._config, "interval_minutes", 5) or 5),
                            )
                            + 1
                        )
                        should_refresh_hardware = (
                            hardware_expiry is None
                            or hardware_expiry <= now + refresh_window
                            or hardware_power_changed
                        )
                    else:
                        _ext_state["expires_at"] = new_expiry
                        should_refresh_hardware = (
                            self.battery_system != "tesla"
                            or hardware_power_changed
                        )
                    if self.battery_system == "tesla":
                        # Tesla force modes are implemented as uploaded TOU
                        # tariffs. The software timer can be extended cheaply,
                        # but the already-uploaded tariff only covers its
                        # original 30-minute-aligned window. Refresh when the
                        # desired expiry reaches beyond that hardware window.
                        should_refresh_hardware = (
                            hardware_expiry is None
                            or new_expiry > hardware_expiry - timedelta(minutes=1)
                            or hardware_power_changed
                        )
                    elif force_type == "charge":
                        should_refresh_hardware = (
                            should_refresh_hardware
                            or self._force_charge_hardware_needs_refresh(force_power_w)
                        )
                    elif force_type == "discharge":
                        should_refresh_hardware = (
                            should_refresh_hardware
                            or self._force_discharge_hardware_needs_refresh(force_power_w)
                        )

                    # Re-issue hardware writes when the hardware-side window is
                    # shorter than the extended optimizer-owned force state, or
                    # when the LP changes the target power inside the same mode.
                    if (
                        battery
                        and should_refresh_hardware
                        and (
                            (force_type == "charge" and hasattr(battery, "force_charge"))
                            or (
                                force_type == "discharge"
                                and hasattr(battery, "force_discharge")
                            )
                        )
                    ):
                        try:
                            # For Modbus-backed systems, _extend_hardware
                            # re-issues the inverter countdown. For Tesla, the
                            # service falls through to the full tariff uploader
                            # so the TOU force window is rolled forward too.
                            if force_type == "charge":
                                await battery.force_charge(
                                    duration_minutes=extend_mins,
                                    power_w=force_power_w,
                                    _extend_hardware=True,
                                )
                            else:
                                allowed, applied_power_w = (
                                    await self._force_discharge_through_export_guard(
                                        battery,
                                        force_power_w,
                                        duration_minutes=extend_mins,
                                        _extend_hardware=True,
                                        _tariff_duration=tariff_mins,
                                    )
                                )
                                if not allowed:
                                    if force_scope == "optimizer":
                                        self._clear_optimizer_force_state()
                                    elif self._force_state_clearer:
                                        self._force_state_clearer()
                                    self._last_executed_planned_action = action.action
                                    self._last_executed_action = "self_consumption"
                                    return
                                force_power_w = applied_power_w
                            _LOGGER.debug(
                                "Optimizer: re-issued %s command for hardware refresh "
                                "(%dmin, %.0fW)",
                                force_type, extend_mins, force_power_w,
                            )
                            if force_scope == "optimizer":
                                self._set_optimizer_force_state(
                                    force_type,
                                    extend_mins,
                                    force_power_w,
                                )
                            else:
                                _ext_state["power_w"] = force_power_w
                        except Exception as ext_err:
                            _LOGGER.warning("Optimizer: failed to re-issue %s for extension: %s", force_type, ext_err)

                    if force_scope != "optimizer":
                        async def _auto_restore_extended(_now):
                            if _ext_state.get("active"):
                                _LOGGER.info("⏰ Force %s expired (extended timer), auto-restoring", force_type)
                                from ..const import DOMAIN as _SVC_DOMAIN
                                await self.hass.services.async_call(
                                    _SVC_DOMAIN, "restore_normal", {}, blocking=True,
                                )

                        from homeassistant.helpers.event import async_track_point_in_utc_time
                        _ext_state["cancel_expiry_timer"] = async_track_point_in_utc_time(
                            self.hass, _auto_restore_extended, new_expiry,
                        )
                    elif not should_refresh_hardware and hardware_expiry is not None:
                        _ext_state["expires_at"] = hardware_expiry
                    logged_expiry = self._as_utc_datetime(
                        _ext_state.get("expires_at")
                    ) or new_expiry
                    _LOGGER.debug(
                        "Optimizer: force %s active (optimizer) — LP still wants %s, "
                        "extended expiry to %s",
                        force_type, action.action,
                        logged_expiry.isoformat(),
                    )
                    return

                # LP changed its mind — cancel the optimizer's force mode.
                if action.action in SELF_USE_ACTIONS or action.action == "idle":
                    if force_type == "charge":
                        commitment_remaining = (
                            self._optimizer_force_charge_commitment_remaining(
                                force_state,
                                action,
                            )
                        )
                    else:
                        commitment_remaining = (
                            self._optimizer_force_discharge_commitment_remaining(
                                force_state,
                                action,
                            )
                        )
                    if commitment_remaining is not None:
                        remaining_minutes = max(
                            1,
                            int((commitment_remaining.total_seconds() + 59) // 60),
                        )
                        _LOGGER.info(
                            "Optimizer: Holding active force %s for %d more min "
                            "despite LP now wanting %s",
                            force_type,
                            remaining_minutes,
                            action.action,
                        )
                        return

                # Clear force state BEFORE calling restore_normal so that
                # TOU sync (triggered inside restore_normal) doesn't skip
                # due to seeing force_charge_state["active"]=True.
                _LOGGER.info(
                    "Optimizer: LP changed mind (%s → %s) — canceling optimizer-triggered "
                    "force %s to execute new action",
                    force_type, action.action, force_type,
                )
                optimizer_force_snapshot = None
                if force_state.get("scope") == "optimizer":
                    optimizer_force_snapshot = dict(self._optimizer_force_state)
                    self._clear_optimizer_force_state()
                elif self._force_state_clearer:
                    self._force_state_clearer()
                battery = self._executor.battery_controller
                restore_success = True
                if hasattr(battery, "restore_normal"):
                    restore_success = await battery.restore_normal()
                if restore_success is False:
                    if optimizer_force_snapshot is not None:
                        self._optimizer_force_state = optimizer_force_snapshot
                    _LOGGER.warning(
                        "Optimizer: Restore after canceling force %s failed; "
                        "retaining optimizer force state for retry",
                        force_type,
                    )
                    return
                await self._restore_pre_idle_backup_reserve(
                    battery,
                    f"after canceling force {force_type}",
                )

        try:
            # During demand charge windows, override IDLE → self_consumption.
            # IDLE holds the battery and lets grid serve load, which increases
            # peak demand — the opposite of what demand charge avoidance wants.
            # Self-consumption lets the battery discharge to cover home load,
            # minimizing grid import during the demand window.
            planned_action = action.action
            effective_action = planned_action

            # --- Off-grid transition handling ---
            # If we're currently off-grid and the new action needs the grid,
            # reconnect FIRST. The contactor takes a few seconds to close.
            if self._last_executed_action == "off_grid" and effective_action != "off_grid":
                _LOGGER.info(
                    "Optimizer: transitioning from OFF_GRID → %s — "
                    "reconnecting grid first",
                    effective_action,
                )
                try:
                    from ..powerwall_local.curtailment_fallback import get_fallback
                    fallback = get_fallback(self.hass, self._entry)
                    reconnected = await fallback.release(
                        trigger_reason="optimizer_reconnect"
                    )
                    if not reconnected:
                        _LOGGER.error(
                            "Optimizer: failed to reconnect grid — "
                            "staying off-grid, skipping %s",
                            effective_action,
                        )
                        return
                except Exception as err:
                    _LOGGER.error(
                        "Optimizer: reconnect error: %s — skipping %s",
                        err, effective_action,
                    )
                    return
                # Brief pause for contactor to close
                import asyncio
                await asyncio.sleep(3)

            # Skip charge/export actions during suspected calibration
            from ..const import DOMAIN as _CAL_DOMAIN
            _cal_ed = self.hass.data.get(_CAL_DOMAIN, {}).get(self.entry_id, {})
            if _cal_ed.get("calibration_suspected") and effective_action in ("charge", "export"):
                _LOGGER.info(
                    "Optimizer: Skipping %s — calibration suspected, using self_consumption",
                    effective_action,
                )
                effective_action = "self_consumption"

            if effective_action == "idle" and self._is_in_demand_window():
                _LOGGER.info(
                    "Optimizer: Overriding IDLE → self_consumption during demand charge window"
                )
                effective_action = "self_consumption"

            # The optimizer reserve is for charge/discharge decisions only.
            # Self-consumption can continue down to the hardware reserve.
            # Only execute IDLE when SOC is well above the optimizer reserve
            # (>5% above = meaningful charge to hold for later export).
            # Otherwise use self-consumption — battery serves load naturally.
            if effective_action == "idle":
                try:
                    soc_now, _ = await self._get_battery_state()
                    opt_reserve = self._config.backup_reserve
                    if opt_reserve + 0.005 < soc_now <= opt_reserve + 0.05:
                        hw_reserve_pct = self._startup_backup_reserve or 0
                        _LOGGER.debug(
                            "Optimizer: Overriding IDLE → self_consumption — "
                            "SOC %.1f%% at optimizer reserve %.0f%%, "
                            "hardware reserve %.0f%% (%.0f%% headroom)",
                            soc_now * 100, opt_reserve * 100,
                            hw_reserve_pct, (opt_reserve * 100 - hw_reserve_pct),
                        )
                        effective_action = "self_consumption"
                except Exception:
                    pass

            if effective_action in ("discharge", "export") and self._should_block_export_for_demand():
                _LOGGER.info(
                    "Optimizer: Overriding EXPORT → self_consumption "
                    "near demand charge window (preserving battery)"
                )
                effective_action = "self_consumption"

            # Block EXPORT when export price is below threshold.
            # Without this, force_discharge can cause the battery to export
            # at a loss during negative/zero prices (e.g. Chip Mode suppression).
            if effective_action in ("discharge", "export"):
                _ep = self._last_export_prices
                if _ep:
                    _current_export = self._current_export_price_for_action(
                        _ep,
                        action,
                    )
                    if _current_export is None:
                        _current_export = _ep[0] if _ep else 0
                    if _current_export < 0.01:  # < 1c/kWh
                        _LOGGER.info(
                            "Optimizer: Overriding %s → self_consumption — "
                            "export price %.1fc/kWh < 1c threshold",
                            effective_action, _current_export * 100,
                        )
                        effective_action = "self_consumption"

            preserve_active = self._scheduled_ev_preserve_active()
            if preserve_active and effective_action in (
                "discharge",
                "export",
                "consume",
                "self_consumption",
                "idle",
            ):
                if effective_action != "idle":
                    _LOGGER.info(
                        "Scheduled EV preserve: overriding optimizer %s → no_discharge",
                        effective_action,
                    )
                effective_action = "no_discharge"
            elif not preserve_active:
                await self._release_scheduled_ev_no_discharge_mode("preserve inactive")

            # When transitioning from IDLE to another action, immediately undo
            # what IDLE did (restore work mode and backup_reserve) before
            # executing the new LP action.
            prev = self._last_executed_action
            if prev == "idle" and effective_action != "idle":
                if (
                    self.energy_coordinator
                    and hasattr(self.energy_coordinator, "restore_work_mode_from_idle")
                ):
                    await self.energy_coordinator.restore_work_mode_from_idle()
                restored = await self._restore_pre_idle_backup_reserve(
                    battery,
                    f"exiting IDLE to {effective_action}",
                )
                if restored:
                    _LOGGER.info(
                        "Optimizer: Exiting IDLE → %s — restored reserve/work mode",
                        effective_action,
                    )
                else:
                    _LOGGER.info(
                        "Optimizer: Exiting IDLE → %s — restored work mode; "
                        "backup reserve restore is pending",
                        effective_action,
                    )
                    return

            # The optimizer backup reserve is a hard software floor for all
            # battery systems.  Once SOC reaches it, stop any forced/max
            # discharge request and return the inverter to self-consumption;
            # do not keep exporting just because the hardware min-SOC would
            # eventually stop the battery.
            if effective_action in ("discharge", "export"):
                try:
                    soc_now, _ = await self._get_battery_state()
                    opt_reserve = self._force_discharge_reserve_floor(action)
                    reaches_reserve, projected_soc = (
                        self._force_discharge_reaches_reserve(
                            action,
                            soc_now,
                            opt_reserve,
                        )
                    )
                    if reaches_reserve:
                        soc_text = (
                            f"{soc_now * 100:.1f}%"
                            if soc_now is not None
                            else "unknown"
                        )
                        projected_text = (
                            f", projected {projected_soc * 100:.1f}%"
                            if projected_soc is not None
                            else ""
                        )
                        _LOGGER.warning(
                            "Optimizer: Blocking %s — SOC %s%s at/below "
                            "optimizer reserve %.0f%%; switching to self_consumption",
                            effective_action,
                            soc_text,
                            projected_text,
                            opt_reserve * 100,
                        )
                        effective_action = "self_consumption"
                except Exception:
                    pass

            # A cached boundary action owns this slot. A periodic solve is
            # still free to publish a new plan, but it must not introduce a
            # fresh force mode halfway through a slot that began in a
            # non-force action. Safety gates above may turn a forced plan into
            # self-consumption; explicit price/settings/startup/manual runs do
            # not pass the polling trigger and therefore retain immediate
            # execution authority.
            boundary_execution = getattr(self, "_boundary_execution", None)
            if (
                execution_trigger == "poll"
                and effective_action in FORCED_ACTIONS
                and boundary_execution
                and not boundary_execution.get("was_forced", False)
            ):
                now = dt_util.now()
                slot_start = boundary_execution.get("slot_start")
                slot_end = boundary_execution.get("slot_end")
                if (
                    isinstance(slot_start, datetime)
                    and isinstance(slot_end, datetime)
                    and slot_start <= now < slot_end
                ):
                    _LOGGER.info(
                        "Optimizer: deferring periodic mid-slot %s after cached %s "
                        "until boundary %s",
                        effective_action,
                        boundary_execution.get("action"),
                        slot_end.isoformat(),
                    )
                    return

            if effective_action == "charge":
                if hasattr(battery, "force_charge"):
                    if self._tesla_force_charge_should_yield_to_live_solar(action):
                        effective_action = "self_consumption"
                        if hasattr(battery, "set_self_consumption_mode"):
                            await battery.set_self_consumption_mode()
                        elif hasattr(battery, "restore_normal"):
                            await battery.restore_normal()
                    if effective_action != "charge":
                        self._last_executed_planned_action = action.action
                        self._last_executed_action = effective_action
                        return

                    charge_power_w = self._charge_command_power_w(action)
                    charge_duration = self._force_duration_for_action_window(
                        action,
                        {"charge"},
                        allow_boundary_overrun=False,
                        minimum_minutes=self._config.interval_minutes + 5,
                    )
                    # Near the demand window, shorten charge duration so the
                    # auto-restore fires 1 minute before demand starts.  The
                    # optimizer recalculates every 5 minutes and will upload a
                    # fresh tariff, so the 30-min TOU rounding is irrelevant.
                    # Within 1 minute of demand, override to self_consumption.
                    mins_to_demand = self._minutes_to_demand_start()
                    if mins_to_demand is not None and mins_to_demand <= 1:
                        _LOGGER.info(
                            "Optimizer: Blocking CHARGE — %d min to demand "
                            "window, switching to self_consumption",
                            mins_to_demand,
                        )
                        effective_action = "self_consumption"
                        if hasattr(battery, "set_self_consumption_mode"):
                            await battery.set_self_consumption_mode()
                        elif hasattr(battery, "restore_normal"):
                            await battery.restore_normal()
                    elif mins_to_demand is not None and mins_to_demand <= charge_duration:
                        charge_duration = max(1, mins_to_demand - 1)
                        _LOGGER.info(
                            "Optimizer: Shortening charge to %dmin "
                            "(%d min before demand window)",
                            charge_duration, mins_to_demand,
                        )
                        force_result = await battery.force_charge(
                            duration_minutes=charge_duration,
                            power_w=charge_power_w,
                        )
                        if force_result is not False and self.battery_system != "tesla":
                            self._set_optimizer_force_state(
                                "charge",
                                charge_duration,
                                charge_power_w,
                            )
                        _LOGGER.info(
                            "Optimizer: Charging at %.0fW for %dmin "
                            "(auto-restore before demand)",
                            charge_power_w, charge_duration,
                        )
                    else:
                        force_result = await battery.force_charge(
                            duration_minutes=charge_duration,
                            power_w=charge_power_w,
                        )
                        if force_result is not False and self.battery_system != "tesla":
                            self._set_optimizer_force_state(
                                "charge",
                                charge_duration,
                                charge_power_w,
                            )
                        _LOGGER.info("Optimizer: Charging at %.0fW", charge_power_w)
            elif effective_action in ("discharge", "export"):
                if hasattr(battery, "force_discharge"):
                    discharge_power = self._export_command_power_w(action)
                    discharge_duration = self._force_duration_for_action_window(
                        action,
                        {"discharge", "export"},
                        allow_boundary_overrun=False,
                        minimum_minutes=self._config.interval_minutes + 5,
                    )
                    tariff_duration = self._tesla_tariff_duration_for_force_window(
                        discharge_duration
                    )
                    force_result, discharge_power = (
                        await self._force_discharge_through_export_guard(
                            battery,
                            discharge_power,
                            duration_minutes=discharge_duration,
                            _tariff_duration=tariff_duration,
                        )
                    )
                    if force_result and self.battery_system != "tesla":
                        self._set_optimizer_force_state(
                            "discharge",
                            discharge_duration,
                            discharge_power,
                        )
                    _LOGGER.info(
                        "Optimizer: Discharging/exporting at %.0fW for %dmin",
                        discharge_power, discharge_duration,
                    )
                    if not force_result:
                        effective_action = "self_consumption"
            elif effective_action == "no_discharge":
                await self._set_scheduled_ev_no_discharge_mode(
                    battery,
                    getattr(action, "action", "scheduled_ev_preserve"),
                )
            elif effective_action == "idle":
                if await self._set_idle_hold_mode(battery) is False:
                    _LOGGER.warning(
                        "Optimizer: IDLE command failed — keeping previous action "
                        "marker so the next cycle retries"
                    )
                    return
            elif effective_action == "off_grid":
                # Off-grid curtailment: physically disconnect from grid.
                # Delegates to CurtailmentFallback which enforces SOC floor,
                # daily duration cap, and pairing checks.
                #
                # The off-grid overlay only marks pre-validated contiguous
                # runs, so execution can activate immediately here.
                if self._last_executed_action == "off_grid":
                    # Already off-grid — check safety gates are still met
                    try:
                        from ..powerwall_local.curtailment_fallback import get_fallback
                        fallback = get_fallback(self.hass, self._entry)
                        still_safe = await fallback.check_safety()
                        if not still_safe:
                            _LOGGER.info(
                                "Optimizer: OFF_GRID safety check failed — "
                                "reconnected, switching to self_consumption"
                            )
                            effective_action = "self_consumption"
                            if hasattr(battery, "set_self_consumption_mode"):
                                await battery.set_self_consumption_mode()
                        else:
                            _LOGGER.debug("Optimizer: OFF_GRID — holding, safety OK")
                    except Exception as err:
                        _LOGGER.warning("Optimizer: OFF_GRID safety check error: %s", err)
                else:
                    # Go off-grid — no entry holdoff, the overlay already
                    # requires 3 consecutive eligible slots (15 min) before
                    # marking as OFF_GRID so the decision is pre-validated.
                    try:
                        from ..powerwall_local.curtailment_fallback import get_fallback
                        fallback = get_fallback(self.hass, self._entry)
                        ok = await fallback.activate(reason="optimizer_offgrid")
                        if not ok:
                            _LOGGER.info(
                                "Optimizer: OFF_GRID refused by safety gates "
                                "(SOC floor / daily cap) — using self_consumption"
                            )
                            effective_action = "self_consumption"
                            if hasattr(battery, "set_self_consumption_mode"):
                                await battery.set_self_consumption_mode()
                        else:
                            _LOGGER.info(
                                "Optimizer: OFF_GRID — physically disconnected from grid"
                            )
                    except Exception as err:
                        _LOGGER.error("Optimizer: OFF_GRID activation error: %s", err)
                        effective_action = "self_consumption"

            else:
                # self_consumption or consume — let battery operate naturally.
                #
                # For Tesla: keep the hardware backup_reserve aligned with the
                # user's hardware reserve, not the optimizer floor. The LP floor
                # is a software scheduling boundary; temporarily raising Tesla's
                # hardware reserve to that floor can show up in the Tesla app and
                # can trigger grid charging when SOC is below the floor.
                #
                # Off-grid exit is handled by the reconnect transition
                # block at the top of this method — no additional holdoff
                # needed since the overlay already pre-validated run length.

                if effective_action != "off_grid":
                    apply_self_consumption = self._last_executed_action != "self_consumption"
                    reapply_backup_reserve = False
                    sungrow_reapply_reserve_pct: int | None = None
                    sungrow_inferred_restore = False
                    configured_reserve_pct = int(self._config.backup_reserve * 100)
                    reserve_pct: int | None = None
                    if not apply_self_consumption:
                        # Verify the hardware mode has not drifted. On HA restart
                        # Tesla can remain in autonomous while the optimizer's
                        # last action marker is already self_consumption.
                        if hasattr(battery, "get_tesla_operation_mode"):
                            hw_mode = await battery.get_tesla_operation_mode()
                            if hw_mode is not None and hw_mode != "self_consumption":
                                _LOGGER.info(
                                    "Optimizer: Tesla mode is '%s' while LP action is "
                                    "self_consumption — reapplying self-consumption mode",
                                    hw_mode,
                                )
                                apply_self_consumption = True
                        if (
                            self.battery_system == "tesla"
                            and hasattr(battery, "get_backup_reserve")
                        ):
                            soc_now, _ = await self._get_battery_state()
                            soc_pct = max(0, min(100, int(soc_now * 100)))
                            reserve_pct = (
                                self._startup_backup_reserve
                                if self._startup_backup_reserve is not None
                                else configured_reserve_pct
                            )
                            reserve_pct = max(0, min(100, reserve_pct))
                            if 81 <= reserve_pct <= 99:
                                reserve_pct = 80
                            if soc_pct < reserve_pct:
                                reserve_pct = min(reserve_pct, soc_pct)
                                if 81 <= reserve_pct <= 99:
                                    reserve_pct = 80
                            current_reserve_trust = None
                            if hasattr(battery, "read_backup_reserve"):
                                current_reserve_reading = await battery.read_backup_reserve()
                                current_reserve = current_reserve_reading.percent
                                current_reserve_trust = current_reserve_reading.trust
                            else:
                                current_reserve = await battery.get_backup_reserve()
                            if (
                                current_reserve is not None
                                and reserve_pct is not None
                                and current_reserve != reserve_pct
                            ):
                                if current_reserve == 100 and reserve_pct < current_reserve:
                                    _LOGGER.info(
                                        "Optimizer: Tesla backup_reserve=100%% while target "
                                        "self-consumption reserve is %d%% — treating it as "
                                        "stale force-charge state and reapplying",
                                        reserve_pct,
                                    )
                                    reapply_backup_reserve = True
                                elif (
                                    self._pre_idle_backup_reserve is None
                                    and self._idle_hold_reserve is None
                                    and current_reserve > reserve_pct
                                    and current_reserve <= soc_pct
                                    and (
                                        current_reserve_trust is None
                                        or current_reserve_trust in TRUSTED_FOR_PERSIST
                                    )
                                ):
                                    previous_reserve_pct = reserve_pct
                                    self._startup_backup_reserve = current_reserve
                                    if self._optimizer:
                                        self._optimizer.update_hardware_reserve(
                                            current_reserve / 100
                                        )
                                    reserve_pct = current_reserve
                                    _LOGGER.info(
                                        "Optimizer: detected Tesla backup_reserve=%d%% "
                                        "above cached target %d%% while SOC=%d%%; "
                                        "treating it as the current hardware reserve",
                                        current_reserve,
                                        previous_reserve_pct,
                                        soc_pct,
                                    )
                                else:
                                    _LOGGER.info(
                                        "Optimizer: backup_reserve is %d%% while target "
                                        "self-consumption reserve is %d%% — reapplying",
                                        current_reserve,
                                        reserve_pct,
                                    )
                                    reapply_backup_reserve = True
                        if self.battery_system == "goodwe" and self.energy_coordinator:
                            coord_data = getattr(self.energy_coordinator, "data", None) or {}
                            try:
                                grid_kw = float(coord_data.get("grid_power", 0) or 0)
                                battery_kw = float(coord_data.get("battery_power", 0) or 0)
                            except (TypeError, ValueError):
                                grid_kw = 0.0
                                battery_kw = 0.0
                            if grid_kw < -0.5 and battery_kw > 0.5:
                                _LOGGER.info(
                                    "Optimizer: GoodWe is exporting %.2fkW to grid while "
                                    "discharging battery %.2fkW in self_consumption — "
                                    "reapplying self-consumption mode",
                                    abs(grid_kw),
                                    battery_kw,
                                )
                                apply_self_consumption = True
                        if self.battery_system == "sungrow" and self.energy_coordinator:
                            coord_data = getattr(self.energy_coordinator, "data", None) or {}

                            def _coord_float(*keys: str) -> float | None:
                                for key in keys:
                                    try:
                                        value = coord_data.get(key)
                                        if value is None:
                                            continue
                                        return float(value)
                                    except (TypeError, ValueError):
                                        continue
                                return None

                            mode_value = (
                                coord_data.get("ems_mode_name")
                                or coord_data.get("mode")
                                or coord_data.get("work_mode")
                            )
                            mode = str(mode_value or "").strip().lower()
                            charge_cmd = coord_data.get("charge_cmd")
                            try:
                                charge_cmd_int = (
                                    int(charge_cmd)
                                    if charge_cmd is not None
                                    else None
                                )
                            except (TypeError, ValueError):
                                charge_cmd_int = None
                            if mode == "forced" or charge_cmd_int in (0xAA, 0xBB):
                                _LOGGER.info(
                                    "Optimizer: Sungrow still reports forced mode "
                                    "(mode=%s, charge_cmd=%s) while LP action is "
                                    "self_consumption — reapplying restore_normal",
                                    mode_value,
                                    charge_cmd,
                                )
                                apply_self_consumption = True
                            elif (
                                hasattr(
                                    self.energy_coordinator,
                                    "_discharge_appears_blocked_after_restore",
                                )
                                and self.energy_coordinator._discharge_appears_blocked_after_restore()
                            ):
                                last_inferred_restore = getattr(
                                    self,
                                    "_last_sungrow_inferred_restore_at",
                                    None,
                                )
                                now = dt_util.utcnow()
                                if (
                                    last_inferred_restore is None
                                    or now - last_inferred_restore
                                    >= SUNGROW_INFERRED_RESTORE_COOLDOWN
                                ):
                                    _LOGGER.info(
                                        "Optimizer: Sungrow appears discharge-blocked while "
                                        "LP action is self_consumption — reapplying "
                                        "restore_normal"
                                    )
                                    apply_self_consumption = True
                                    sungrow_inferred_restore = True
                                else:
                                    _LOGGER.debug(
                                        "Optimizer: Sungrow inferred restore is in cooldown — "
                                        "skipping redundant restore_normal"
                                    )
                            else:
                                battery_kw = _coord_float("battery_power", "battery_power_kw")
                                grid_kw = _coord_float("grid_power", "grid_power_kw")
                                load_kw = _coord_float("load_power", "home_load")
                                soc_pct_float = _coord_float("battery_level", "battery_soc")
                                current_reserve = _coord_float("backup_reserve", "min_soc")
                                target_reserve = self._startup_backup_reserve
                                grid_serving_load = (
                                    grid_kw is not None
                                    and grid_kw >= 0.15
                                    and (
                                        load_kw is None
                                        or (
                                            load_kw >= 0.15
                                            and grid_kw >= load_kw * 0.6
                                        )
                                    )
                                )
                                if (
                                    target_reserve is not None
                                    and current_reserve is not None
                                    and soc_pct_float is not None
                                    and battery_kw is not None
                                    and abs(battery_kw) <= 0.1
                                    and grid_serving_load
                                    and current_reserve > target_reserve
                                    and soc_pct_float <= current_reserve + 2.0
                                    and soc_pct_float > target_reserve + 2.0
                                ):
                                    sungrow_reapply_reserve_pct = max(
                                        0, min(100, int(target_reserve))
                                    )
                                    _LOGGER.info(
                                        "Optimizer: Sungrow reserve/min-SOC is %.1f%% "
                                        "while cached hardware reserve is %d%% and "
                                        "battery is not discharging; reapplying "
                                        "self-consumption reserve",
                                        current_reserve,
                                        sungrow_reapply_reserve_pct,
                                    )
                                    apply_self_consumption = True
                        if not apply_self_consumption and not reapply_backup_reserve:
                            _LOGGER.debug(
                                "Optimizer: Already in self-consumption mode — "
                                "skipping redundant API call"
                            )
                    mode_apply_failed = False
                    if apply_self_consumption or reapply_backup_reserve:
                        if hasattr(battery, "set_self_consumption_mode"):
                            if apply_self_consumption:
                                if await battery.set_self_consumption_mode() is False:
                                    mode_apply_failed = True
                        elif hasattr(battery, "restore_normal"):
                            if apply_self_consumption:
                                if await battery.restore_normal() is False:
                                    mode_apply_failed = True
                        if sungrow_inferred_restore:
                            self._last_sungrow_inferred_restore_at = dt_util.utcnow()
                        if (
                            sungrow_reapply_reserve_pct is not None
                            and hasattr(battery, "set_backup_reserve")
                        ):
                            await battery.set_backup_reserve(sungrow_reapply_reserve_pct)
                        # Tesla only: reset hardware backup_reserve to prevent
                        # grid charging when the user's hardware reserve
                        # (restored by restore_normal after force_discharge) is
                        # above the current SOC. Modbus batteries such as GoodWe
                        # expose this as a real hardware/DOD setting, so ordinary
                        # self-consumption must not rewrite it to the LP floor.
                        if (
                            self.battery_system == "tesla"
                            and hasattr(battery, "set_backup_reserve")
                        ):
                            if reserve_pct is None:
                                soc_now, _ = await self._get_battery_state()
                                soc_pct = max(0, min(100, int(soc_now * 100)))
                                reserve_pct = (
                                    self._startup_backup_reserve
                                    if self._startup_backup_reserve is not None
                                    else configured_reserve_pct
                                )
                                reserve_pct = max(0, min(100, reserve_pct))
                                if 81 <= reserve_pct <= 99:
                                    reserve_pct = 80
                                if soc_pct < reserve_pct:
                                    reserve_pct = min(reserve_pct, soc_pct)
                                    if 81 <= reserve_pct <= 99:
                                        reserve_pct = 80
                            await battery.set_backup_reserve(reserve_pct)
                            _LOGGER.info(
                                "Optimizer: self_consumption — set backup_reserve=%d%% "
                                "(startup=%s%%, floor=%d%%, current_soc=%d%%)",
                                reserve_pct,
                                (
                                    self._startup_backup_reserve
                                    if self._startup_backup_reserve is not None
                                    else "?"
                                ),
                                configured_reserve_pct,
                                soc_pct,
                            )
                    if mode_apply_failed:
                        # Do not record success: the base BatteryController
                        # returns False instead of raising, and advancing the
                        # marker here masked the failure — the change-detection
                        # above then skipped the command forever, leaving the
                        # inverter in its prior forced mode. Keeping the old
                        # marker makes the next cycle retry.
                        _LOGGER.warning(
                            "Optimizer: self-consumption mode command failed — "
                            "keeping previous action marker so the next cycle retries"
                        )
                        return
                    _LOGGER.debug("Optimizer: Self-consumption mode (action=%s)", effective_action)

            self._last_executed_planned_action = planned_action
            self._last_executed_action = effective_action

        except Exception as e:
            _LOGGER.error("Failed to execute optimizer action: %s", e)

