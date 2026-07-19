"""Extracted price_forecast helpers for OptimizationCoordinator (architecture refactor Phase 4)."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.util import dt as dt_util

from ..const import (
    CONF_AMBER_FORECAST_TYPE,
    CONF_FLOW_POWER_BASE_RATE,
    CONF_FP_NETWORK,
    CONF_FP_TARIFF_CODE,
    CONF_PEA_CUSTOM_VALUE,
    CONF_PEA_ENABLED,
    CONF_SPIKE_PROTECTION_ENABLED,
    FLOW_POWER_DEFAULT_BASE_RATE,
)
from ..flow_power_pricing import (
    FlowPowerPricingContext,
    calculate_flow_power_pea,
    resolve_flow_power_pricing_context,
)

_LOGGER = logging.getLogger(__name__)
FLOW_POWER_NEM_TZ = timezone(timedelta(hours=10))


def _flow_power_network_tariff_rate(
    when: datetime,
    network: str,
    tariff_code: str,
) -> float | None:
    """Return the Flow Power v2 network tariff rate for an interval."""
    from ..tariff_utils import get_network_tariff_rate

    return get_network_tariff_rate(when, network, tariff_code)


class PriceForecastMixin:
    """Mixin providing price_forecast behavior. Host class supplies runtime attrs."""

    async def _get_price_forecast(self) -> tuple[list[float], list[float]] | None:
        """Get price forecasts for optimizer.

        For dynamic providers (Amber, Flow Power): reads from price_coordinator.
        For static TOU providers (GloBird, etc.): generates from tariff_schedule.
        """
        if self._electricity_provider() == "covau":
            return self._covau_price_forecast()

        if self._prefers_static_tou_pricing():
            tou_prices = self._get_tou_price_forecast_if_available()
            if tou_prices is not None:
                if self.price_coordinator and self.price_coordinator.data:
                    _LOGGER.debug(
                        "Using TOU tariff prices for static provider %s; ignoring %s data",
                        self._electricity_provider(),
                        type(self.price_coordinator).__name__,
                    )
                return tou_prices

            # No tariff schedule cached yet - never fall through to the
            # dynamic-pricing path for tariff-backed providers. A leftover
            # AEMOPriceCoordinator (e.g. set up before a provider switch)
            # could still hold stale data and silently feed it to the LP.
            _LOGGER.debug(
                "Tariff-backed provider %s but tariff_schedule not yet cached; "
                "skipping dynamic-pricing fallback",
                self._electricity_provider(),
            )
            return None

        # Dynamic pricing (Amber, Flow Power, etc.)
        if self.price_coordinator and self.price_coordinator.data:
            data = self.price_coordinator.data

            # Amber format: {"current": [...], "forecast": [...]}
            # Each entry has perKwh (cents), channelType ("general"/"feedIn")
            # forecast is 30-min resolution; expand to 5-min intervals for LP
            if "current" in data or "forecast" in data:
                all_entries = list(data.get("current", []) or []) + list(data.get("forecast", []) or [])
                if all_entries:
                    # Separate by channel type
                    general = [e for e in all_entries if e.get("channelType") == "general"]
                    feed_in = [e for e in all_entries if e.get("channelType") == "feedIn"]
                    is_flow_power_provider = self._electricity_provider() == "flow_power"

                    # Sort by start time (works for Octopus, Amber, and AEMO)
                    for lst in (general, feed_in):
                        lst.sort(key=lambda e: self._get_entry_start_time(e))

                    # Filter out fully-past entries — providers return
                    # historical entries, but the LP needs prices starting
                    # from the current interval. Use END time so an
                    # interval that started before current_window but is
                    # still active (e.g. 30-min Octopus slot at minute 20)
                    # is preserved; its remaining-minutes are computed
                    # during expansion.
                    now = dt_util.now()
                    current_window = now.replace(
                        minute=(now.minute // 5) * 5,
                        second=0, microsecond=0,
                    )
                    fp_current_general = None
                    fp_current_period_start = None
                    fp_current_period_end = None
                    if is_flow_power_provider:
                        current_general = [
                            e
                            for e in data.get("current", []) or []
                            if e.get("channelType") == "general"
                        ]
                        current_feedin = [
                            e
                            for e in data.get("current", []) or []
                            if e.get("channelType") == "feedIn"
                        ]
                        current_general.sort(key=lambda e: self._get_entry_end_time(e))
                        current_feedin.sort(key=lambda e: self._get_entry_end_time(e))
                        if current_general:
                            fp_current_general = current_general[-1]
                            current_nem_start = self._get_entry_start_datetime(
                                fp_current_general,
                                current_window,
                            ).astimezone(FLOW_POWER_NEM_TZ)
                            fp_current_period_start = current_nem_start.replace(
                                minute=0 if current_nem_start.minute < 30 else 30,
                                second=0,
                                microsecond=0,
                            )
                            fp_current_period_end = fp_current_period_start + timedelta(
                                minutes=30
                            )

                            def _flow_power_current_period_entry(source: dict) -> dict:
                                entry = dict(source)
                                entry["nemTime"] = fp_current_period_end.isoformat()
                                entry["duration"] = 30
                                entry["type"] = "CurrentInterval"
                                return entry

                            general.append(
                                _flow_power_current_period_entry(fp_current_general)
                            )
                            if current_feedin:
                                feed_in.append(
                                    _flow_power_current_period_entry(current_feedin[-1])
                                )

                    for lst in (general, feed_in):
                        original_len = len(lst)
                        filtered = []
                        for e in lst:
                            end_str = self._get_entry_end_time(e)
                            if end_str:
                                try:
                                    entry_end = datetime.fromisoformat(
                                        end_str.replace("Z", "+00:00")
                                    )
                                    if entry_end <= current_window:
                                        continue
                                except (ValueError, TypeError):
                                    pass
                            filtered.append(e)
                        lst[:] = filtered
                        if len(lst) < original_len:
                            _LOGGER.debug(
                                "Filtered %d past price entries (ended <= %s), "
                                "%d remaining",
                                original_len - len(lst),
                                current_window.isoformat(),
                                len(lst),
                            )

                    # Build 5-min price arrays with per-entry expansion.
                    # Mixed feeds (e.g. Amber 5-min + 30-min) expand each entry
                    # by its own duration: 5-min→1x, 30-min→6x.
                    interval = self._config.interval_minutes  # 5
                    n_steps = int(self._config.horizon_hours * 60) // interval  # 576

                    # Detect Flow Power for price adjustment
                    is_flow_power = False
                    fp_base_rate = 34.0
                    fp_pea_enabled = True
                    fp_custom_pea = None
                    fp_pricing_context: FlowPowerPricingContext = (
                        resolve_flow_power_pricing_context({}, {}, {})
                    )
                    fp_avg_daily_tariff = None
                    fp_network = None
                    fp_tariff_code = None
                    fp_tariff_rates: dict[int, float] = {}
                    _provider = self._electricity_provider()
                    amber_forecast_type = "predicted"
                    if self._entry:
                        from ..const import (
                            CONF_AMBER_FORECAST_TYPE,
                            CONF_FP_NETWORK,
                            CONF_FP_TARIFF_CODE,
                            CONF_PEA_ENABLED,
                            CONF_FLOW_POWER_BASE_RATE,
                            CONF_PEA_CUSTOM_VALUE,
                            FLOW_POWER_DEFAULT_BASE_RATE,
                            NETWORK_API_NAME,
                            DOMAIN as _DOMAIN,
                        )
                        amber_forecast_type = self._entry.options.get(
                            CONF_AMBER_FORECAST_TYPE,
                            self._entry.data.get(
                                CONF_AMBER_FORECAST_TYPE, "predicted"
                            ),
                        )
                        is_flow_power = _provider == "flow_power"
                        if is_flow_power:
                            def _flow_power_option(key: str, default=None):
                                return self._entry.options.get(
                                    key,
                                    self._entry.data.get(key, default),
                                )

                            fp_pea_enabled = _flow_power_option(
                                CONF_PEA_ENABLED, True
                            )
                            fp_base_rate = _flow_power_option(
                                CONF_FLOW_POWER_BASE_RATE,
                                FLOW_POWER_DEFAULT_BASE_RATE,
                            )
                            fp_custom_pea = _flow_power_option(CONF_PEA_CUSTOM_VALUE)
                            domain_data = self.hass.data.get(
                                _DOMAIN, {}
                            ).get(self._entry.entry_id, {})
                            fp_pricing_context = resolve_flow_power_pricing_context(
                                self._entry.options,
                                self._entry.data,
                                domain_data,
                            )
                            fp_avg_daily_tariff = domain_data.get(
                                "fp_avg_daily_tariff"
                            )
                            fp_network_name = self._entry.options.get(
                                CONF_FP_NETWORK,
                                self._entry.data.get(CONF_FP_NETWORK),
                            )
                            fp_tariff_code = self._entry.options.get(
                                CONF_FP_TARIFF_CODE,
                                self._entry.data.get(CONF_FP_TARIFF_CODE),
                            )
                            if fp_network_name:
                                fp_network = NETWORK_API_NAME.get(
                                    fp_network_name,
                                    str(fp_network_name).lower(),
                                )

                    if (
                        is_flow_power
                        and fp_network
                        and fp_tariff_code
                        and fp_avg_daily_tariff is not None
                    ):
                        tariff_datetimes: dict[int, datetime] = {}
                        for entry in general:
                            start_dt = self._get_entry_start_datetime(
                                entry,
                                current_window,
                            ).astimezone(FLOW_POWER_NEM_TZ)
                            tariff_datetimes[id(entry)] = start_dt

                        def _lookup_flow_power_tariff_rates() -> dict[int, float]:
                            rates: dict[int, float] = {}
                            cache: dict[datetime, float | None] = {}
                            for entry_id, start_dt in tariff_datetimes.items():
                                cached = cache.get(start_dt)
                                if start_dt not in cache:
                                    cached = _flow_power_network_tariff_rate(
                                        start_dt,
                                        fp_network,
                                        fp_tariff_code,
                                    )
                                    cache[start_dt] = cached
                                if cached is not None:
                                    rates[entry_id] = cached
                            return rates

                        try:
                            if hasattr(self.hass, "async_add_executor_job"):
                                fp_tariff_rates = await self.hass.async_add_executor_job(
                                    _lookup_flow_power_tariff_rates
                                )
                            else:
                                fp_tariff_rates = _lookup_flow_power_tariff_rates()
                        except Exception as err:
                            _LOGGER.warning(
                                "Flow Power v2 tariff lookup failed for %s/%s; "
                                "falling back to legacy PEA formula: %s",
                                fp_network,
                                fp_tariff_code,
                                err,
                            )

                    import_slots: list[float | None] = [None] * n_steps
                    entry_positions = []  # start index for each general entry
                    entry_expands_general = []  # parallel: actual expand count per entry
                    write_cursor = 0
                    last_import_slot = 0
                    for e in general:
                        dur = e.get("duration", 30)
                        slot_bounds = self._entry_slot_bounds(
                            e, current_window, interval, n_steps
                        )
                        if slot_bounds is None:
                            # Fallback for legacy/test data with no timestamps:
                            # preserve the previous append-based behavior.
                            effective_min = self._entry_remaining_minutes(
                                e, current_window, dur,
                            )
                            entry_expand = (
                                max(1, effective_min // interval)
                                if effective_min > 0
                                else 0
                            )
                            start_idx = write_cursor
                            end_idx = min(n_steps, start_idx + entry_expand)
                            write_cursor = end_idx
                        else:
                            start_idx, end_idx = slot_bounds
                            entry_expand = end_idx - start_idx
                        entry_positions.append(start_idx)
                        entry_expands_general.append(entry_expand)
                        if entry_expand == 0:
                            continue
                        if is_flow_power:
                            if fp_custom_pea is not None:
                                price_dollar = max(
                                    0, (fp_base_rate + fp_custom_pea) / 100
                                )
                            elif fp_pea_enabled:
                                wholesale_cents = e.get("wholesaleKWHPrice")
                                if wholesale_cents is None:
                                    wholesale_cents = e.get("perKwh", 0)
                                if (
                                    fp_current_general
                                    and fp_current_period_start is not None
                                ):
                                    entry_period_start = self._get_entry_start_datetime(
                                        e,
                                        current_window,
                                    ).astimezone(FLOW_POWER_NEM_TZ)
                                    entry_period_start = entry_period_start.replace(
                                        minute=(
                                            0
                                            if entry_period_start.minute < 30
                                            else 30
                                        ),
                                        second=0,
                                        microsecond=0,
                                    )
                                    if entry_period_start == fp_current_period_start:
                                        current_wholesale_cents = (
                                            fp_current_general.get("wholesaleKWHPrice")
                                        )
                                        if current_wholesale_cents is None:
                                            current_wholesale_cents = (
                                                fp_current_general.get("perKwh")
                                            )
                                        if current_wholesale_cents is not None:
                                            wholesale_cents = current_wholesale_cents
                                tariff_rate = fp_tariff_rates.get(id(e))
                                if (
                                    tariff_rate is not None
                                    and fp_avg_daily_tariff is not None
                                ):
                                    pea = calculate_flow_power_pea(
                                        wholesale_cents,
                                        fp_pricing_context,
                                        tariff_rate=tariff_rate,
                                        avg_daily_tariff=fp_avg_daily_tariff,
                                    )
                                else:
                                    pea = calculate_flow_power_pea(
                                        wholesale_cents,
                                        fp_pricing_context,
                                    )
                                price_dollar = max(
                                    0, (fp_base_rate + pea) / 100
                                )
                            else:
                                price_dollar = max(0, fp_base_rate / 100)
                        else:
                            price_dollar = self._dynamic_import_price_dollar(
                                e,
                                _provider,
                                amber_forecast_type,
                            )
                            if price_dollar is None:
                                last_import_slot = max(last_import_slot, end_idx)
                                continue
                        for pos in range(start_idx, end_idx):
                            import_slots[pos] = price_dollar
                        last_import_slot = max(last_import_slot, end_idx)

                    import_prices = self._fill_price_gaps(import_slots)

                    export_slots: list[float | None] = [None] * n_steps
                    display_export_slots: list[float | None] = [None] * n_steps
                    export_write_cursor = 0
                    for e in feed_in:
                        dur = e.get("duration", 30)
                        slot_bounds = self._entry_slot_bounds(
                            e, current_window, interval, n_steps
                        )
                        if slot_bounds is None:
                            effective_min = self._entry_remaining_minutes(
                                e, current_window, dur,
                            )
                            entry_expand = (
                                max(1, effective_min // interval)
                                if effective_min > 0
                                else 0
                            )
                            start_idx = export_write_cursor
                            end_idx = min(n_steps, start_idx + entry_expand)
                            export_write_cursor = end_idx
                        else:
                            start_idx, end_idx = slot_bounds
                        if end_idx <= start_idx:
                            continue
                        # feedIn perKwh: negative = you get paid, positive = you pay to export.
                        # display_price keeps the signed value so the UI chart can show
                        # negative dips during oversupply (when you'd pay to export).
                        # lp_price clamps to 0 so the LP doesn't see paying-to-export
                        # as profitable revenue.
                        raw_export_dollar = self._dynamic_export_price_dollar(
                            e,
                            _provider,
                            amber_forecast_type,
                        )
                        if raw_export_dollar is None:
                            continue
                        display_price = -raw_export_dollar
                        lp_price = max(0.0, display_price)
                        for pos in range(start_idx, end_idx):
                            export_slots[pos] = lp_price
                            display_export_slots[pos] = display_price

                    export_prices = self._fill_price_gaps(export_slots)
                    display_export_raw = self._fill_price_gaps(
                        display_export_slots,
                        export_prices[0] if export_prices else None,
                    )

                    # Track actual forecast length before padding
                    actual_price_intervals = last_import_slot

                    # Pad or trim to n_steps
                    if import_prices:
                        if len(import_prices) < n_steps:
                            last = import_prices[-1] if import_prices else 0.25
                            import_prices.extend([last] * (n_steps - len(import_prices)))
                        import_prices = import_prices[:n_steps]

                    if export_prices:
                        if len(export_prices) < n_steps:
                            last = export_prices[-1] if export_prices else 0.08
                            export_prices.extend([last] * (n_steps - len(export_prices)))
                        export_prices = export_prices[:n_steps]

                    if display_export_raw:
                        if len(display_export_raw) < n_steps:
                            last = display_export_raw[-1]
                            display_export_raw.extend(
                                [last] * (n_steps - len(display_export_raw))
                            )
                        display_export_raw = display_export_raw[:n_steps]

                    # Spike protection: cap buy prices during Amber spike periods
                    # so the LP optimizer won't choose to charge at extreme prices
                    if import_prices and general:
                        spike_protection_on = False
                        if self._entry:
                            from ..const import CONF_SPIKE_PROTECTION_ENABLED
                            spike_protection_on = self._entry.options.get(
                                CONF_SPIKE_PROTECTION_ENABLED,
                                self._entry.data.get(CONF_SPIKE_PROTECTION_ENABLED, False),
                            )

                        if spike_protection_on:
                            median_price = sorted(import_prices)[len(import_prices) // 2]
                            cap_price = max(median_price * 2, 0.50)  # At least 50c/kWh cap
                            for idx, e in enumerate(general):
                                spike_status = e.get("spikeStatus", "none")
                                if spike_status in ("spike", "potential"):
                                    base_idx = entry_positions[idx]
                                    entry_expand = (
                                        entry_expands_general[idx]
                                        if idx < len(entry_expands_general)
                                        else max(1, e.get("duration", 30) // interval)
                                    )
                                    if entry_expand == 0:
                                        continue
                                    original_price = e.get("perKwh", 0)
                                    capped_count = 0
                                    for j in range(entry_expand):
                                        pos = base_idx + j
                                        if pos < len(import_prices) and import_prices[pos] > cap_price:
                                            import_prices[pos] = cap_price
                                            capped_count += 1
                                    if capped_count:
                                        _LOGGER.info(
                                            "Spike protection: capped %d intervals at %.1fc/kWh "
                                            "(was %.1fc, status=%s)",
                                            capped_count, cap_price * 100,
                                            original_price, spike_status,
                                        )

                    if import_prices:
                        epex_import_override = self._read_epex_import_price_entity(
                            n_steps
                        )
                        if epex_import_override is not None:
                            import_prices = epex_import_override

                        epex_override = self._read_epex_export_price_entity(n_steps)
                        if epex_override is not None:
                            display_export_raw, export_prices = epex_override

                        # Apply Flow Power export schedule before display storage.
                        # For Flow Power, the synthetic Happy Hour schedule IS the
                        # contractual truth, so it overrides the Amber-derived
                        # signed values for both the LP and the display chart.
                        # For other providers this is a no-op.
                        export_prices = self._apply_flow_power_export(export_prices)
                        if is_flow_power:
                            display_export_raw = list(export_prices)

                        # Store prices for UI display BEFORE LP adjustments.
                        # Clip to actual forecast length so the app chart doesn't
                        # show flat-line padding where the forecast ran out.
                        # display_export_raw keeps the signed export rate so the
                        # chart shows negative dips when wholesale is oversupplied
                        # (Amber feedIn perKwh > 0 → you pay to export).
                        self._last_display_import_prices = list(import_prices[:actual_price_intervals])
                        self._last_display_export_prices = list(display_export_raw[:actual_price_intervals])
                        self._last_grid_charge_cap_import_prices = list(import_prices)

                        # Apply export boost, saving session overlay, and chip mode to LP prices.
                        # Chip mode uses the real export price as its threshold reference so
                        # Export Boost cannot make a below-threshold export slot look allowed.
                        chip_reference_export_prices = list(export_prices)
                        export_prices, _ = self._apply_export_boost(export_prices, import_prices)
                        import_prices, export_prices = self._apply_saving_session_prices(import_prices, export_prices)
                        export_prices = self._apply_chip_mode(
                            export_prices,
                            chip_reference_export_prices,
                        )

                        # Apply demand charge penalty to LP import prices
                        import_prices = self._apply_demand_charge_penalty(import_prices)

                        # Apply confidence decay for LP input.
                        decay_horizon = 12.0 if self._config.profit_max_enabled else 6.0
                        if is_flow_power:
                            # Flow Power Happy Hour export is contractual, so keep
                            # the export schedule fixed. Import PEA forecasts still
                            # come from speculative wholesale forecasts and should
                            # not let far-future spikes dominate the LP unchanged.
                            import_prices, _ = self._apply_confidence_decay(
                                import_prices,
                                export_prices,
                                confidence_horizon_hours=decay_horizon,
                            )
                        else:
                            import_prices, export_prices = self._apply_confidence_decay(
                                import_prices, export_prices,
                                confidence_horizon_hours=decay_horizon,
                            )

                        # Keep the successfully built price values coupled to
                        # their original interval grid. Cached actions can execute
                        # after the wall clock crosses a slot boundary; synthesizing
                        # a fresh grid then would shift every cached price one
                        # position. Stage a fresh grid every run so a successful
                        # provider switch cannot retain static-TOU metadata.
                        self._pending_price_timestamps = [
                            current_window + timedelta(minutes=idx * interval)
                            for idx in range(n_steps)
                        ]

                        _price_label = "Flow Power" if is_flow_power else "Dynamic"
                        _LOGGER.debug(
                            "%s prices: %d steps, display %.1fc-%.1fc, "
                            "LP %s %.1fc-%.1fc",
                            _price_label,
                            len(import_prices),
                            min(self._last_display_import_prices) * 100,
                            max(self._last_display_import_prices) * 100,
                            "(import-decayed)" if is_flow_power else "(decayed)",
                            min(import_prices) * 100,
                            max(import_prices) * 100,
                        )
                        return (import_prices, export_prices)

        # Static TOU pricing fallback (GloBird, custom tariff, etc.)
        # Generate 576-point price forecast from tariff schedule.
        tou_prices = self._get_tou_price_forecast_if_available()
        if tou_prices is not None:
            return tou_prices

        _LOGGER.warning(
            "No price data available! price_coordinator=%s, tariff=%s. "
            "Optimizer will use default flat rates.",
            self.price_coordinator is not None,
            self._get_tou_tariff_schedule() is not None,
        )
        return None
