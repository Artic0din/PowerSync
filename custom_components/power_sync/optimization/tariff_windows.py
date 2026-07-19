"""Extracted tariff_windows helpers for OptimizationCoordinator (architecture refactor Phase 4)."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.util import dt as dt_util

from ..const import (
    CONF_CHIP_MODE_ENABLED,
    CONF_CHIP_MODE_END,
    CONF_CHIP_MODE_START,
    CONF_CHIP_MODE_THRESHOLD,
    CONF_DEMAND_CHARGE_DAYS,
    CONF_DEMAND_CHARGE_ENABLED,
    CONF_DEMAND_CHARGE_END_TIME,
    CONF_DEMAND_CHARGE_RATE,
    CONF_DEMAND_CHARGE_START_TIME,
    CONF_ELECTRICITY_PROVIDER,
    CONF_FLOW_POWER_EXPORT_RATE,
    CONF_FLOW_POWER_STATE,
    DEFAULT_CHIP_MODE_END,
    DEFAULT_CHIP_MODE_START,
    DEFAULT_CHIP_MODE_THRESHOLD,
    FLOW_POWER_EXPORT_RATES,
)
from ..covau import covau_price_series
from ..zerohero import zerocharge_is_in_window, zerohero_is_in_window

_LOGGER = logging.getLogger(__name__)

class TariffWindowsMixin:
    """Mixin providing tariff_windows behavior."""

    def _apply_covau_optimizer_inputs(
        self,
        import_prices: list[float],
        export_prices: list[float],
    ) -> None:
        """Refresh CovaU marginal bonus arrays immediately before each solve."""
        runtime = self._ensure_covau_ledger(now=dt_util.now())
        n = min(len(import_prices), len(export_prices))
        self._last_zerocharge_bonus_prices = [0.0] * n
        self._last_zerocharge_bonus_cap_kwh = 0.0
        self._last_zerohero_bonus_prices = [0.0] * n
        self._last_zerohero_bonus_cap_kwh = 0.0
        self._last_import_bonus_group_ids = None
        self._last_export_bonus_group_ids = None
        self._last_import_bonus_caps_by_group = None
        self._last_export_bonus_caps_by_group = None
        if runtime is None or n <= 0:
            return
        snapshot, ledger = runtime
        (
            _base_import,
            _base_export,
            import_bonus,
            export_bonus,
            import_cap,
            export_cap,
        ) = covau_price_series(snapshot, self._price_timestamps(n), ledger)
        timestamps = self._price_timestamps(n)
        self._set_covau_bonus_groups(
            snapshot,
            ledger,
            timestamps,
            import_bonus,
            export_bonus,
        )
        self._last_zerocharge_bonus_prices = import_bonus
        self._last_zerocharge_bonus_cap_kwh = sum(
            (self._last_import_bonus_caps_by_group or {}).values()
        )
        self._last_zerohero_bonus_prices = export_bonus
        self._last_zerohero_bonus_cap_kwh = sum(
            (self._last_export_bonus_caps_by_group or {}).values()
        )
        if import_cap > 0 and any(import_bonus):
            _LOGGER.info("CovaU optimizer: %.2fkWh free-import quota remaining", import_cap)
        if export_cap > 0 and any(export_bonus):
            _LOGGER.info("CovaU optimizer: %.2fkWh premium-export quota remaining", export_cap)

    def _apply_zerohero_optimizer_inputs(
        self,
        import_prices: list[float],
        export_prices: list[float],
    ) -> None:
        """Prepare capped ZeroHero bonus inputs for the LP optimizer."""
        n = min(len(import_prices), len(export_prices))
        self._last_zerohero_bonus_prices = [0.0] * n
        self._last_zerohero_bonus_cap_kwh = None
        self._last_zerocharge_bonus_prices = [0.0] * n
        self._last_zerocharge_bonus_cap_kwh = None

        config = self._zerohero_config()
        if config is None or n <= 0:
            return

        timestamps = self._price_timestamps(n)
        if config.zerocharge_enabled:
            remaining_import_cap = max(
                0.0,
                config.zerocharge_import_cap_kwh
                - self._actual_zerocharge_import_kwh_today,
            )
            for idx, ts in enumerate(timestamps):
                if zerocharge_is_in_window(ts, config):
                    self._last_zerocharge_bonus_prices[idx] = max(
                        0.0,
                        import_prices[idx] if idx < len(import_prices) else 0.0,
                    )
            self._last_zerocharge_bonus_cap_kwh = remaining_import_cap
            if remaining_import_cap > 0 and any(self._last_zerocharge_bonus_prices):
                _LOGGER.info(
                    "ZeroCharge optimizer: %.2fkWh free-import cap remaining, %s-%s",
                    remaining_import_cap,
                    config.zerocharge_start,
                    config.zerocharge_end,
                )

        if self._zerohero_credit_lost():
            _LOGGER.info(
                "ZeroHero no-import credit lost for today: import %.3fkWh exceeded allowance %.3fkWh",
                self._actual_zerohero_import_kwh_today,
                config.import_allowance_kwh,
            )

        remaining_cap = max(
            0.0,
            config.export_cap_kwh - self._actual_zerohero_bonus_export_kwh_today,
        )
        for idx, ts in enumerate(timestamps):
            if not zerohero_is_in_window(ts, config):
                continue
            base_fit = max(0.0, export_prices[idx] if idx < len(export_prices) else 0.0)
            self._last_zerohero_bonus_prices[idx] = max(
                0.0,
                config.super_export_rate - base_fit,
            )
            # Keep planned grid import out of the no-import window without
            # making the LP infeasible when household load must still be served.
            import_prices[idx] += 5.0

        self._last_zerohero_bonus_cap_kwh = remaining_cap
        if remaining_cap > 0 and any(self._last_zerohero_bonus_prices):
            _LOGGER.info(
                "ZeroHero optimizer: %.2fkWh bonus cap remaining, %.1fc/kWh Super Export target",
                remaining_cap,
                config.super_export_rate * 100,
            )

    def _time_window_slots(
        self,
        n: int,
        start_time: str,
        end_time: str,
        prices: list[float] | None = None,
        threshold: float | None = None,
    ) -> list[bool]:
        """Return slots inside a local time window, optionally price-gated."""
        try:
            sh, sm = map(int, start_time.split(":"))
            eh, em = map(int, end_time.split(":"))
        except (ValueError, IndexError):
            return [False] * n

        start_min = sh * 60 + sm
        end_min = eh * 60 + em
        interval = max(1, int(self._config.interval_minutes or 5))
        raw_now = dt_util.now()
        now = raw_now.replace(
            minute=(raw_now.minute // interval) * interval,
            second=0, microsecond=0,
        )
        result = [False] * n

        for t in range(n):
            if (
                prices is not None
                and threshold is not None
                and (t >= len(prices) or prices[t] < threshold)
            ):
                continue

            ts = now + timedelta(minutes=t * interval)
            minutes_of_day = ts.hour * 60 + ts.minute
            if end_min <= start_min:
                in_window = minutes_of_day >= start_min or minutes_of_day < end_min
            else:
                in_window = start_min <= minutes_of_day < end_min
            result[t] = in_window

        return result

    def _apply_saving_session_prices(
        self,
        import_prices: list[float],
        export_prices: list[float],
    ) -> tuple[list[float], list[float]]:
        """Overlay saving session rates onto LP prices.

        Saving sessions: massive export boost (octopoints rate >> normal export).
        Free electricity: import price -> 0 (free grid power).
        """
        if not self._saving_session_coordinator or not self._saving_session_coordinator.data:
            return import_prices, export_prices

        sessions = self._saving_session_coordinator.data.get("sessions", [])
        if not sessions:
            return import_prices, export_prices

        try:
            octopoints_per_penny = float(
                getattr(self._saving_session_coordinator, "_octopoints_per_penny", 8)
                or 8
            )
        except (TypeError, ValueError):
            octopoints_per_penny = 8.0
        if octopoints_per_penny <= 0:
            octopoints_per_penny = 8.0

        interval = self._config.interval_minutes
        now = dt_util.now()
        if getattr(now, "tzinfo", None) is None:
            now = now.replace(tzinfo=dt_util.UTC)
        else:
            now = now.astimezone(dt_util.UTC)
        import_result = list(import_prices)
        export_result = list(export_prices)
        boosted = 0

        for session in sessions:
            if not session.joined:
                continue
            start = getattr(session, "start", None)
            end = getattr(session, "end", None)
            if start is None or end is None:
                continue
            if getattr(start, "tzinfo", None) is None:
                start = start.replace(tzinfo=dt_util.UTC)
            else:
                start = start.astimezone(dt_util.UTC)
            if getattr(end, "tzinfo", None) is None:
                end = end.replace(tzinfo=dt_util.UTC)
            else:
                end = end.astimezone(dt_util.UTC)

            # Convert octopoints to GBP/kWh:
            # octopoints_per_kwh / octopoints_per_penny = pence/kWh
            # pence/kWh / 100 = GBP/kWh (same unit as our price arrays)
            try:
                octopoints_per_kwh = float(
                    getattr(session, "octopoints_per_kwh", 0) or 0
                )
            except (TypeError, ValueError):
                octopoints_per_kwh = 0.0
            if octopoints_per_kwh > 0:
                session_rate = (octopoints_per_kwh / octopoints_per_penny) / 100
            else:
                session_rate = 0.0

            for t in range(len(export_result)):
                ts = now + timedelta(minutes=t * interval)
                if start <= ts < end:
                    if session.session_type == "saving":
                        # Add session rate ON TOP of existing export price
                        export_result[t] += session_rate
                        # Also bump import price to discourage grid charging
                        import_result[t] = max(import_result[t], session_rate * 2)
                    elif session.session_type == "free_electricity":
                        # Free power - set import price to 0
                        import_result[t] = 0.0
                    boosted += 1

        if boosted:
            joined_count = len([s for s in sessions if s.joined])
            _LOGGER.info(
                "Saving sessions: overlaid %d intervals from %d session(s)",
                boosted, joined_count,
            )

        return import_result, export_result

    def _apply_chip_mode(
        self,
        export_prices: list[float],
        reference_export_prices: list[float] | None = None,
    ) -> list[float]:
        """Apply chip mode to LP export prices — suppress exports unless price exceeds threshold.

        During the configured window, sets export prices to 0 so the LP won't plan
        exports. Preserves price for spikes above threshold. If export prices have
        already been adjusted by Export Boost, reference_export_prices keeps the
        Chip threshold tied to the real export price.
        """
        if not self._entry:
            return export_prices

        from ..const import (
            CONF_CHIP_MODE_ENABLED,
            CONF_CHIP_MODE_START,
            CONF_CHIP_MODE_END,
            CONF_CHIP_MODE_THRESHOLD,
            DEFAULT_CHIP_MODE_START,
            DEFAULT_CHIP_MODE_END,
            DEFAULT_CHIP_MODE_THRESHOLD,
        )

        opts = self._entry.options
        data = self._entry.data
        if not opts.get(CONF_CHIP_MODE_ENABLED, data.get(CONF_CHIP_MODE_ENABLED, False)):
            return export_prices

        chip_start = opts.get(CONF_CHIP_MODE_START, DEFAULT_CHIP_MODE_START)
        chip_end = opts.get(CONF_CHIP_MODE_END, DEFAULT_CHIP_MODE_END)
        threshold = (opts.get(CONF_CHIP_MODE_THRESHOLD,
                              DEFAULT_CHIP_MODE_THRESHOLD) or 0) / 100  # cents → $/kWh

        try:
            sh, sm = map(int, chip_start.split(":"))
            eh, em = map(int, chip_end.split(":"))
        except (ValueError, IndexError):
            return export_prices

        start_min = sh * 60 + sm
        end_min = eh * 60 + em
        interval = self._config.interval_minutes
        now = dt_util.now()
        suppressed = 0
        allowed_spikes = 0

        result = list(export_prices)
        threshold_prices = (
            reference_export_prices
            if reference_export_prices is not None
            and len(reference_export_prices) == len(result)
            else result
        )
        for t in range(len(result)):
            ts = now + timedelta(minutes=t * interval)
            minutes_of_day = ts.hour * 60 + ts.minute

            # Check if in chip window (handles overnight wrap)
            if end_min <= start_min:
                in_window = minutes_of_day >= start_min or minutes_of_day < end_min
            else:
                in_window = start_min <= minutes_of_day < end_min

            if in_window:
                if threshold_prices[t] >= threshold:
                    allowed_spikes += 1  # Keep original price for spike
                else:
                    result[t] = 0.0  # Suppress export
                    suppressed += 1

        if suppressed or allowed_spikes:
            _LOGGER.debug(
                "Chip mode: suppressed %d intervals, allowed %d spikes "
                "(threshold=%.1fc, window=%s-%s)",
                suppressed, allowed_spikes, threshold * 100, chip_start, chip_end,
            )

        return result

    def _apply_flow_power_export(
        self, export_prices: list[float]
    ) -> list[float]:
        """Replace export prices with Flow Power Happy Hour schedule.

        Flow Power: 0c export except Happy Hour (17:30-19:30) at 45c/35c.
        """
        if not self._entry:
            return export_prices

        from ..const import (
            CONF_ELECTRICITY_PROVIDER,
            CONF_FLOW_POWER_EXPORT_RATE,
            CONF_FLOW_POWER_STATE,
            FLOW_POWER_EXPORT_RATES,
        )

        provider = self._entry.options.get(
            CONF_ELECTRICITY_PROVIDER,
            self._entry.data.get(CONF_ELECTRICITY_PROVIDER, ""),
        )
        if provider != "flow_power":
            return export_prices

        state = self._entry.options.get(
            CONF_FLOW_POWER_STATE,
            self._entry.data.get(CONF_FLOW_POWER_STATE, ""),
        )
        if not state:
            return export_prices

        configured_rate = self._entry.options.get(
            CONF_FLOW_POWER_EXPORT_RATE,
            self._entry.data.get(CONF_FLOW_POWER_EXPORT_RATE),
        )
        try:
            happy_rate = (
                float(configured_rate) / 100
                if configured_rate not in (None, "")
                else FLOW_POWER_EXPORT_RATES.get(state, 0.0)
            )
        except (ValueError, TypeError):
            happy_rate = FLOW_POWER_EXPORT_RATES.get(state, 0.0)
        happy_start = 17 * 60 + 30  # 17:30
        happy_end = 19 * 60 + 30    # 19:30
        interval = self._config.interval_minutes
        now = dt_util.now()

        result = []
        for i in range(len(export_prices)):
            slot = now + timedelta(minutes=i * interval)
            mins = slot.hour * 60 + slot.minute
            result.append(happy_rate if happy_start <= mins < happy_end else 0.0)

        return result

    def _apply_demand_charge_penalty(
        self, import_prices: list[float]
    ) -> list[float]:
        """Add import price penalty during demand charge windows.

        During configured demand charge peak periods, adds a penalty to
        import prices that strongly discourages grid imports. The LP will
        prefer battery discharge or self-consumption during these windows.
        """
        if not self._entry or not import_prices:
            return import_prices

        from ..const import (
            CONF_DEMAND_CHARGE_ENABLED,
            CONF_DEMAND_CHARGE_RATE,
            CONF_DEMAND_CHARGE_START_TIME,
            CONF_DEMAND_CHARGE_END_TIME,
            CONF_DEMAND_CHARGE_DAYS,
        )

        enabled = self._entry.options.get(
            CONF_DEMAND_CHARGE_ENABLED,
            self._entry.data.get(CONF_DEMAND_CHARGE_ENABLED, False),
        )
        if not enabled:
            return import_prices

        rate = self._entry.options.get(
            CONF_DEMAND_CHARGE_RATE,
            self._entry.data.get(CONF_DEMAND_CHARGE_RATE, 0.0),
        )
        if rate <= 0:
            return import_prices

        start_str = self._entry.options.get(
            CONF_DEMAND_CHARGE_START_TIME,
            self._entry.data.get(CONF_DEMAND_CHARGE_START_TIME, "14:00"),
        )
        end_str = self._entry.options.get(
            CONF_DEMAND_CHARGE_END_TIME,
            self._entry.data.get(CONF_DEMAND_CHARGE_END_TIME, "20:00"),
        )
        days = self._entry.options.get(
            CONF_DEMAND_CHARGE_DAYS,
            self._entry.data.get(CONF_DEMAND_CHARGE_DAYS, "All Days"),
        )

        # Parse start/end times
        try:
            s_parts = start_str.split(":")
            start_min = int(s_parts[0]) * 60 + int(s_parts[1])
            e_parts = end_str.split(":")
            end_min = int(e_parts[0]) * 60 + int(e_parts[1])
        except (ValueError, IndexError):
            return import_prices

        # Penalty: rate/10 converts $/kW/month to aggressive $/kWh penalty
        penalty = rate / 10.0

        now = dt_util.now()
        interval = self._config.interval_minutes
        adjusted = list(import_prices)
        penalised = 0

        for t in range(len(adjusted)):
            ts = now + timedelta(minutes=t * interval)
            weekday = ts.weekday()

            # Day filter
            if days == "Weekdays Only" and weekday >= 5:
                continue
            if days == "Weekends Only" and weekday < 5:
                continue

            current_min = ts.hour * 60 + ts.minute

            # Time window check (handles overnight wrap)
            in_window = False
            if end_min <= start_min:
                in_window = current_min >= start_min or current_min < end_min
            else:
                in_window = start_min <= current_min < end_min

            if in_window:
                adjusted[t] += penalty
                penalised += 1

        if penalised:
            _LOGGER.info(
                "Demand charge penalty: +$%.2f/kWh on %d intervals (%s-%s, %s)",
                penalty, penalised, start_str, end_str, days,
            )

        return adjusted
