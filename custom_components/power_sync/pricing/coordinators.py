"""Price-related data update coordinators for PowerSync."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, date
import logging
import re
import time
from typing import Any, Optional
import asyncio

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from ..const import (
    DOMAIN,
    UPDATE_INTERVAL_PRICES,
    AMBER_API_BASE_URL,
    POWER_SYNC_USER_AGENT,
    DEFAULT_SOLCAST_ESTIMATE_TYPE,
    SOLCAST_ESTIMATE,
    SOLCAST_ESTIMATE10,
    SOLCAST_ESTIMATE90,
    DEFAULT_TWAP_WINDOW_DAYS,
    MIN_TWAP_SAMPLES,
    FLOW_POWER_MARKET_AVG,
    FLOW_POWER_KWATCH_REGIONS,
)
from ._shared import (
    SensitiveDataFilter,
    _parse_retry_after,
    _fetch_with_retry,
)

_LOGGER = logging.getLogger(__name__)
_LOGGER.addFilter(SensitiveDataFilter())

# Dispatcher signal fired by AEMOPriceCoordinator when a new dispatch file is
# detected on NEMWEB (settled price for the period that just ended). TOU sync
# subscribes to this in __init__.py to issue exactly one tariff POST per
# 5-min period, aligned with AEMO's publish event instead of a fixed cron.
SIGNAL_AEMO_NEW_DISPATCH = "power_sync_aemo_new_dispatch"

_SOLCAST_ESTIMATE_FIELDS = {
    SOLCAST_ESTIMATE: ("pv_estimate", "pv_estimate50"),
    SOLCAST_ESTIMATE10: ("pv_estimate10", "pv_estimate", "pv_estimate50"),
    SOLCAST_ESTIMATE90: ("pv_estimate90", "pv_estimate", "pv_estimate50"),
}

def _merge_amber_forecasts(forecast_5min: list, forecast_30min: list) -> list:
    """Merge 5-min near-term with 30-min extended horizon, avoiding overlap.

    5-min data covers today at NEM dispatch resolution; 30-min extends ~40h.
    We keep all 5-min entries and only append 30-min entries that start at or
    after the latest 5-min interval end (nemTime).
    """
    if not forecast_5min:
        return forecast_30min or []
    if not forecast_30min:
        return forecast_5min or []

    # Find latest nemTime (interval END) in 5-min data
    latest_5min_end = max(
        (e.get("nemTime", "") for e in forecast_5min),
        default="",
    )
    if not latest_5min_end:
        return forecast_30min

    try:
        boundary = datetime.fromisoformat(latest_5min_end.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return forecast_30min

    # Keep only 30-min entries whose start is at or after the boundary
    filtered_30min = []
    for entry in forecast_30min:
        nem = entry.get("nemTime", "")
        dur = entry.get("duration", 30)
        if nem:
            try:
                end = datetime.fromisoformat(nem.replace("Z", "+00:00"))
                start = end - timedelta(minutes=dur)
                if start >= boundary:
                    filtered_30min.append(entry)
            except (ValueError, TypeError):
                filtered_30min.append(entry)  # keep if unparseable

    return list(forecast_5min) + filtered_30min


class AmberPriceCoordinator(DataUpdateCoordinator):
    """Coordinator to fetch Amber electricity price data."""

    _FORECAST_5MIN_TTL = timedelta(minutes=4, seconds=30)
    _FORECAST_30MIN_TTL = timedelta(minutes=30)

    def __init__(
        self,
        hass: HomeAssistant,
        api_token: str,
        site_id: str | None = None,
        ws_client=None,
    ) -> None:
        """Initialize the coordinator."""
        self.api_token = api_token
        self.site_id = site_id
        self.session = async_get_clientsession(hass)
        self.ws_client = ws_client  # WebSocket client for real-time prices
        self._forecast_5min_cache: list[dict[str, Any]] | None = None
        self._forecast_5min_fetched_at: datetime | None = None
        self._forecast_30min_cache: list[dict[str, Any]] | None = None
        self._forecast_30min_fetched_at: datetime | None = None

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_amber_prices",
            update_interval=UPDATE_INTERVAL_PRICES,
        )

    async def _fetch_forecast_with_cache(
        self,
        *,
        url: str,
        headers: dict[str, str],
        params: dict[str, Any],
        label: str,
        ttl: timedelta,
        cache_attr: str,
        fetched_at_attr: str,
    ) -> list[dict[str, Any]]:
        """Fetch Amber forecast data, reusing cached data within the TTL."""
        cached = getattr(self, cache_attr)
        fetched_at = getattr(self, fetched_at_attr)
        now = dt_util.utcnow()

        if cached is not None and fetched_at is not None and now - fetched_at < ttl:
            age_seconds = (now - fetched_at).total_seconds()
            _LOGGER.debug(
                "Using cached Amber %s forecast (age %.0fs, ttl %.0fs)",
                label,
                age_seconds,
                ttl.total_seconds(),
            )
            return cached

        try:
            forecast = await _fetch_with_retry(
                self.session,
                url,
                headers,
                params=params,
                max_retries=2,
                timeout_seconds=30,
            )
        except UpdateFailed:
            if cached is not None:
                age_minutes = (
                    (now - fetched_at).total_seconds() / 60
                    if fetched_at is not None
                    else -1
                )
                _LOGGER.warning(
                    "Amber %s forecast refresh failed; using cached data (age %.1fm)",
                    label,
                    age_minutes,
                )
                return cached
            raise

        setattr(self, cache_attr, forecast or [])
        setattr(self, fetched_at_attr, now)
        return forecast or []

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from Amber API with WebSocket-first approach."""
        headers = {"Authorization": f"Bearer {self.api_token}"}

        try:
            # Try WebSocket first for current prices (real-time, low latency)
            current_prices = None
            if self.ws_client:
                # Retry logic: Try for 10 seconds with 2-second intervals (5 attempts)
                max_age_seconds = 60  # Reduced from 360s to 60s for fresher data
                retry_attempts = 5
                retry_interval = 2  # seconds

                for attempt in range(retry_attempts):
                    current_prices = self.ws_client.get_latest_prices(max_age_seconds=max_age_seconds)

                    if current_prices:
                        # Get health status to log data age
                        health = self.ws_client.get_health_status()
                        age = health.get('age_seconds', 'unknown')
                        _LOGGER.info(f"✓ Using WebSocket prices (age: {age}s, attempt: {attempt + 1}/{retry_attempts})")
                        break

                    # If not last attempt, wait before retry
                    if attempt < retry_attempts - 1:
                        _LOGGER.debug(f"WebSocket data unavailable/stale, retrying in {retry_interval}s (attempt {attempt + 1}/{retry_attempts})")
                        await asyncio.sleep(retry_interval)

                # All retries exhausted
                if not current_prices:
                    _LOGGER.info(f"WebSocket prices unavailable after {retry_attempts} attempts ({max_age_seconds}s staleness threshold), falling back to REST API")

            # Fall back to REST API if WebSocket unavailable
            if not current_prices:
                _LOGGER.info("⚠ Using REST API for current prices (WebSocket unavailable)")
                current_prices = await _fetch_with_retry(
                    self.session,
                    f"{AMBER_API_BASE_URL}/sites/{self.site_id}/prices/current",
                    headers,
                    max_retries=2,  # Less retries for Amber (usually more reliable)
                    timeout_seconds=30,
                )

            # Dual-resolution forecast approach to ensure complete data coverage:
            # 1. Fetch today's 5-min data for CurrentInterval spike detection
            # 2. Fetch forecast at 30-min resolution via /prices/current for full
            #    AEMO horizon (~40h). The `next` param only works on /prices/current,
            #    not /prices (which is date-range based and ignores `next`).

            # Step 1: Get 5-min resolution data for current period spike detection
            forecast_5min = await self._fetch_forecast_with_cache(
                url=f"{AMBER_API_BASE_URL}/sites/{self.site_id}/prices",
                headers=headers,
                params={"resolution": 5},
                label="5-minute",
                ttl=self._FORECAST_5MIN_TTL,
                cache_attr="_forecast_5min_cache",
                fetched_at_attr="_forecast_5min_fetched_at",
            )

            # Step 2: Get 30-min forecast via /prices/current (supports `next`)
            # Request 288 intervals (144h) — API returns whatever AEMO has (~40h)
            forecast_30min = await self._fetch_forecast_with_cache(
                url=f"{AMBER_API_BASE_URL}/sites/{self.site_id}/prices/current",
                headers=headers,
                params={"next": 288, "resolution": 30},
                label="30-minute",
                ttl=self._FORECAST_30MIN_TTL,
                cache_attr="_forecast_30min_cache",
                fetched_at_attr="_forecast_30min_fetched_at",
            )

            return {
                "current": current_prices,
                "forecast": _merge_amber_forecasts(forecast_5min, forecast_30min),
                "forecast_5min": forecast_5min,  # Keep for TOU sync spike detection
                "last_update": dt_util.utcnow(),
            }

        except UpdateFailed:
            raise  # Re-raise UpdateFailed exceptions
        except Exception as err:
            raise UpdateFailed(f"Unexpected error fetching Amber data: {err}") from err


# ============================================================
# Localvolts Price Coordinator
# ============================================================

def _parse_localvolts_price(value) -> float:
    """Parse a Localvolts price value, handling 'N/A' and non-numeric values.

    Returns price in c/kWh (same unit as Amber perKwh).
    """
    if value is None or value == "N/A" or value == "n/a":
        return 0.0
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0


def _localvolts_interval_start(interval_end: str, duration_minutes: int = 5) -> str:
    """Calculate interval start time from interval end time.

    Args:
        interval_end: ISO 8601 datetime string for interval end
        duration_minutes: Duration of interval in minutes (default 5)

    Returns:
        ISO 8601 datetime string for interval start
    """
    try:
        end_dt = datetime.fromisoformat(interval_end.replace("Z", "+00:00"))
        start_dt = end_dt - timedelta(minutes=duration_minutes)
        return start_dt.isoformat()
    except (ValueError, TypeError):
        return interval_end


class LocalvoltsPriceCoordinator(DataUpdateCoordinator):
    """Coordinator to fetch Localvolts electricity price data.

    Converts Localvolts API data to Amber-compatible format so all downstream
    code (LP optimizer, sensors, TOU sync, curtailment) works unchanged.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        api_key: str,
        partner_id: str,
        nmi: str,
    ) -> None:
        """Initialize the coordinator."""
        from ..localvolts_api import LocalvoltsClient

        self.client = LocalvoltsClient(
            async_get_clientsession(hass), api_key, partner_id
        )
        self.nmi = nmi

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_localvolts_prices",
            update_interval=timedelta(minutes=5),
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from Localvolts API and convert to Amber-compatible format."""
        try:
            intervals = await self.client.get_intervals(self.nmi)

            if not intervals:
                raise UpdateFailed("No interval data returned from Localvolts API")

            current_prices = []
            forecast_prices = []

            for interval in intervals:
                nem_time = interval.get("intervalEnd", "")
                quality = interval.get("quality", "Fcst")

                # Import price: costsFlexUp (c/kWh)
                import_ckwh = _parse_localvolts_price(interval.get("costsFlexUp"))
                # Export price: earningsFlexUp (c/kWh)
                # Negate to match Amber convention: Amber feedIn.perKwh is negative
                # when earning; Localvolts earningsFlexUp is positive when earning
                export_ckwh = -_parse_localvolts_price(interval.get("earningsFlexUp"))

                start_time = _localvolts_interval_start(nem_time, 5)

                general_entry = {
                    "nemTime": nem_time,
                    "perKwh": import_ckwh,
                    "channelType": "general",
                    "type": "CurrentInterval" if quality in ("Act", "Exp") else "ForecastInterval",
                    "duration": 5,
                    "startTime": start_time,
                }
                feedin_entry = {
                    "nemTime": nem_time,
                    "perKwh": export_ckwh,
                    "channelType": "feedIn",
                    "type": general_entry["type"],
                    "duration": 5,
                    "startTime": start_time,
                }

                if quality in ("Act", "Exp"):
                    current_prices.extend([general_entry, feedin_entry])
                else:
                    forecast_prices.extend([general_entry, feedin_entry])

            _LOGGER.debug(
                "Localvolts data: %d current entries, %d forecast entries",
                len(current_prices),
                len(forecast_prices),
            )

            return {
                "current": current_prices,
                "forecast": forecast_prices,
                "last_update": dt_util.utcnow(),
            }

        except UpdateFailed:
            raise
        except Exception as err:
            raise UpdateFailed(f"Error fetching Localvolts data: {err}") from err


# ============================================================
# Amber Usage API — actual metered cost data from NEM
# ============================================================

USAGE_FETCH_INTERVAL = timedelta(hours=4)
USAGE_STORAGE_VERSION = 2  # v2: costs in dollars (v1 had cents-as-dollars bug)
USAGE_STORAGE_KEY = "power_sync.amber_usage"
USAGE_MAX_DAYS = 365
AMBER_DEFAULT_MONTHLY_SUPPLY_FEE = 25.0  # Amber's standard $25/month supply charge

# Quality ranking for deciding whether to overwrite existing data
_QUALITY_RANK = {"estimated": 0, "mixed": 1, "billable": 2}


@dataclass
class DayUsage:
    """Actual metered usage and cost for a single day from Amber."""

    date: str                   # "YYYY-MM-DD"
    import_kwh: float           # general channel total
    export_kwh: float           # feedIn channel (absolute)
    controlled_load_kwh: float
    import_cost: float          # $ gross import
    export_earnings: float      # $ gross export earnings
    net_cost: float             # import_cost - export_earnings
    quality: str                # "estimated", "billable", or "mixed"


class AmberUsageCoordinator:
    """Fetches actual metered usage/cost from the Amber Usage API.

    Not a DataUpdateCoordinator — usage data updates infrequently (every 4h).
    Uses HA Store for persistence across restarts.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        api_token: str,
        site_id: str,
        entry_id: str,
        monthly_supply_fee: float = AMBER_DEFAULT_MONTHLY_SUPPLY_FEE,
    ) -> None:
        """Initialize the Amber usage coordinator."""
        self.hass = hass
        self._api_token = api_token
        self._site_id = site_id
        self._entry_id = entry_id
        self._monthly_supply_fee = monthly_supply_fee
        self._session = async_get_clientsession(hass)
        self._store = Store(hass, USAGE_STORAGE_VERSION, f"{USAGE_STORAGE_KEY}.{entry_id}")

        # In-memory state
        self._days: dict[str, DayUsage] = {}
        self._baselines: dict[str, float] = {}  # date → baseline_cost from optimizer
        self._last_fetch: datetime | None = None
        self._cancel_timer: Any = None
        self._cancel_initial: Any = None

    @property
    def last_fetch_iso(self) -> str | None:
        """Return the last fetch time as ISO string."""
        return self._last_fetch.isoformat() if self._last_fetch else None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def async_start(self) -> None:
        """Load stored data and schedule periodic fetches."""
        await self._load_store()
        # Delay initial fetch 30-90s to avoid competing with price coordinator
        # at startup for Amber API rate limit budget
        import random
        delay = 30 + random.randint(0, 60)
        _LOGGER.info("Amber usage: first fetch in %ds (avoiding startup rate limit contention)", delay)
        self._cancel_initial = self.hass.loop.call_later(
            delay, lambda: self.hass.async_create_task(self._fetch_usage())
        )
        from homeassistant.helpers.event import async_track_time_interval
        self._cancel_timer = async_track_time_interval(
            self.hass, self._scheduled_fetch, USAGE_FETCH_INTERVAL
        )

    async def async_stop(self) -> None:
        """Cancel the periodic timer and any pending initial fetch."""
        if self._cancel_initial:
            self._cancel_initial.cancel()
            self._cancel_initial = None
        if self._cancel_timer:
            self._cancel_timer()
            self._cancel_timer = None

    async def _scheduled_fetch(self, _now=None) -> None:
        """Timer callback for periodic fetch."""
        await self._fetch_usage()

    # ------------------------------------------------------------------
    # Storage
    # ------------------------------------------------------------------

    async def _load_store(self) -> None:
        """Load persisted usage data from HA Store."""
        try:
            stored = await self._store.async_load()
        except Exception as e:
            _LOGGER.warning("Amber usage: store load failed (will re-fetch): %s", e)
            stored = None
        if not stored:
            _LOGGER.info("Amber usage: no stored data (fresh start or version upgrade)")
            return
        for day_dict in stored.get("days", []):
            try:
                du = DayUsage(**day_dict)
                self._days[du.date] = du
            except (TypeError, KeyError):
                continue
        self._baselines = stored.get("baselines", {})
        last_ts = stored.get("last_fetch")
        if last_ts:
            try:
                self._last_fetch = datetime.fromisoformat(last_ts)
            except (ValueError, TypeError):
                pass
        _LOGGER.info("Amber usage: restored %d days from store", len(self._days))

    def _save_store(self) -> None:
        """Persist current data to HA Store (delayed write)."""
        data = {
            "days": [asdict(du) for du in self._days.values()],
            "baselines": self._baselines,
            "last_fetch": self._last_fetch.isoformat() if self._last_fetch else None,
        }
        self._store.async_delay_save(lambda: data, 60)

    # ------------------------------------------------------------------
    # API fetch
    # ------------------------------------------------------------------

    async def _fetch_usage(self) -> None:
        """Fetch usage data from Amber API.

        Uses _fetch_with_retry for consistent 429/retry handling with the
        price coordinator. Checks RateLimit-Remaining header proactively
        and skips the fetch if the budget is low, to avoid starving the
        more important real-time price fetches.

        Amber Usage API has a 7-day max range per request, so large
        back-fills are batched into 7-day chunks.
        """
        now = dt_util.now()
        today = now.date()

        # Determine date range
        if not self._days:
            # First run — fetch 90 days of history
            start_date = today - timedelta(days=90)
        else:
            # Subsequent runs — re-fetch last 3 days for quality upgrades
            start_date = today - timedelta(days=3)

        end_date = today

        headers = {"Authorization": f"Bearer {self._api_token}"}

        # Pre-flight: probe rate limit budget with a lightweight check.
        # If RateLimit-Remaining is low, skip this non-critical fetch
        # to preserve budget for the real-time price coordinator.
        try:
            async with self._session.get(
                f"{AMBER_API_BASE_URL}/sites/{self._site_id}/prices/current",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as probe_resp:
                remaining = probe_resp.headers.get("RateLimit-Remaining")
                if remaining is not None:
                    try:
                        remaining_int = int(remaining)
                        if remaining_int < 10:
                            _LOGGER.info(
                                "Amber usage: skipping fetch — only %d API calls remaining "
                                "(preserving budget for price updates)",
                                remaining_int,
                            )
                            return
                    except (ValueError, TypeError):
                        pass
        except Exception:
            pass  # Probe failed — proceed with fetch anyway

        # Amber Usage API allows max 7-day range per request — batch accordingly
        total_updated = 0
        chunk_start = start_date
        url = f"{AMBER_API_BASE_URL}/sites/{self._site_id}/usage"

        while chunk_start <= end_date:
            chunk_end = min(chunk_start + timedelta(days=6), end_date)
            params = {
                "startDate": chunk_start.isoformat(),
                "endDate": chunk_end.isoformat(),
                "resolution": "30",
            }

            try:
                intervals = await _fetch_with_retry(
                    self._session,
                    url,
                    headers,
                    max_retries=2,
                    timeout_seconds=30,
                    params=params,
                )
                updated = self._process_intervals(intervals)
                total_updated += updated
                _LOGGER.debug(
                    "Amber usage chunk %s to %s: %d days updated",
                    chunk_start, chunk_end, updated,
                )
            except UpdateFailed as err:
                _LOGGER.warning("Amber usage fetch failed for %s to %s: %s", chunk_start, chunk_end, err)
            except Exception as err:
                _LOGGER.warning("Amber usage fetch failed unexpectedly for %s to %s: %s", chunk_start, chunk_end, err)

            chunk_start = chunk_end + timedelta(days=1)

        self._last_fetch = now
        self._prune_old_days()
        self._save_store()
        _LOGGER.info("Amber usage fetched: %d days updated (range %s to %s)", total_updated, start_date, end_date)

    def _process_intervals(self, intervals: list[dict]) -> int:
        """Aggregate 30-min intervals into daily DayUsage records.

        Returns count of days updated.
        """
        # Group by date and channel
        day_buckets: dict[str, dict[str, list[dict]]] = {}
        for iv in intervals:
            dt_str = iv.get("nemTime") or iv.get("startTime") or ""
            try:
                day_key = dt_str[:10]  # "YYYY-MM-DD"
                # Validate it's a real date
                date.fromisoformat(day_key)
            except (ValueError, IndexError):
                continue
            channel = iv.get("channelType", "general")
            day_buckets.setdefault(day_key, {}).setdefault(channel, []).append(iv)

        updated = 0
        for day_key, channels in day_buckets.items():
            import_kwh = 0.0
            export_kwh = 0.0
            controlled_kwh = 0.0
            import_cost = 0.0
            export_earnings = 0.0
            qualities: set[str] = set()

            for iv in channels.get("general", []):
                kwh = abs(iv.get("kwh", 0))
                import_kwh += kwh
                # Amber API returns cost in cents — convert to dollars
                import_cost += iv.get("cost", 0) / 100
                qualities.add(iv.get("quality", "estimated"))

            for iv in channels.get("feedIn", []):
                kwh = abs(iv.get("kwh", 0))
                export_kwh += kwh
                # Amber feedIn cost: negative = you earned, positive = you paid to export
                # Negate so earnings are positive when earning, negative when paying
                export_earnings += -iv.get("cost", 0) / 100
                qualities.add(iv.get("quality", "estimated"))

            for iv in channels.get("controlledLoad", []):
                kwh = abs(iv.get("kwh", 0))
                controlled_kwh += kwh
                import_cost += iv.get("cost", 0) / 100
                qualities.add(iv.get("quality", "estimated"))

            if "billable" in qualities and "estimated" in qualities:
                quality = "mixed"
            elif "billable" in qualities:
                quality = "billable"
            else:
                quality = "estimated"

            new_du = DayUsage(
                date=day_key,
                import_kwh=round(import_kwh, 3),
                export_kwh=round(export_kwh, 3),
                controlled_load_kwh=round(controlled_kwh, 3),
                import_cost=round(import_cost, 4),
                export_earnings=round(export_earnings, 4),
                net_cost=round(import_cost - export_earnings, 4),
                quality=quality,
            )

            # Only overwrite if new data is same or better quality
            existing = self._days.get(day_key)
            if existing:
                existing_rank = _QUALITY_RANK.get(existing.quality, 0)
                new_rank = _QUALITY_RANK.get(quality, 0)
                if new_rank < existing_rank:
                    continue  # Don't downgrade quality

            self._days[day_key] = new_du
            updated += 1

        return updated

    def _prune_old_days(self) -> None:
        """Remove days older than USAGE_MAX_DAYS to limit storage."""
        cutoff = (dt_util.now().date() - timedelta(days=USAGE_MAX_DAYS)).isoformat()
        old_keys = [k for k in self._days if k < cutoff]
        for k in old_keys:
            del self._days[k]
        # Also prune baselines
        old_baselines = [k for k in self._baselines if k < cutoff]
        for k in old_baselines:
            del self._baselines[k]

    # ------------------------------------------------------------------
    # Baseline recording (called from optimization coordinator at midnight)
    # ------------------------------------------------------------------

    def record_baseline(self, date_str: str, baseline_cost: float) -> None:
        """Record the optimizer's baseline cost for a completed day."""
        self._baselines[date_str] = round(baseline_cost, 4)
        self._save_store()
        _LOGGER.info("Amber usage: recorded baseline $%.2f for %s", baseline_cost, date_str)

    # ------------------------------------------------------------------
    # Aggregation
    # ------------------------------------------------------------------

    def get_summary(self, period: str) -> dict[str, Any]:
        """Get aggregated usage for a period.

        period: 'yesterday', 'week' (last 7 complete days), 'month' (calendar month to yesterday), 'last_month'
        """
        days = self._get_days_for_period(period)
        return self._aggregate(days)

    def get_savings_summary(self, period: str) -> dict[str, Any]:
        """Get aggregated usage with baseline and savings for a period."""
        days = self._get_days_for_period(period)
        result = self._aggregate(days)

        # Add baseline and savings.
        # Savings = baseline_energy - actual_energy (supply charge excluded
        # from savings calc since it's a fixed cost with or without battery).
        # Baseline includes supply charge so it reflects true "no battery" cost.
        baseline_total = 0.0
        baseline_days = 0
        supply_total = sum(self._daily_supply_fee(du.date) for du in days)
        for du in days:
            bl = self._baselines.get(du.date)
            if bl is not None:
                baseline_total += bl
                baseline_days += 1

        result["baseline_cost"] = round(baseline_total + supply_total, 2) if baseline_days > 0 else None
        result["savings"] = round(baseline_total - (result["net_cost"] - result["supply_charge"]), 2) if baseline_days > 0 else None
        result["baseline_days"] = baseline_days
        return result

    def get_range(self, start_date: str, end_date: str) -> list[dict[str, Any]]:
        """Get day-by-day data for a custom date range."""
        result = []
        for day_key in sorted(self._days.keys()):
            if start_date <= day_key <= end_date:
                du = self._days[day_key]
                d = asdict(du)
                daily_fee = self._daily_supply_fee(day_key)
                d["supply_charge"] = round(daily_fee, 2)
                d["net_cost"] = round(du.net_cost + daily_fee, 2)
                bl = self._baselines.get(day_key)
                d["baseline_cost"] = bl
                d["savings"] = round(bl - d["net_cost"], 2) if bl is not None else None
                result.append(d)
        return result

    def _get_days_for_period(self, period: str) -> list[DayUsage]:
        """Return list of DayUsage records for the given period."""
        today = dt_util.now().date()
        yesterday = today - timedelta(days=1)

        if period == "yesterday":
            key = yesterday.isoformat()
            du = self._days.get(key)
            return [du] if du else []
        elif period == "week":
            start = (today - timedelta(days=7)).isoformat()
            end = yesterday.isoformat()
        elif period == "month":
            start = today.replace(day=1).isoformat()
            end = yesterday.isoformat()
        elif period == "last_month":
            first_this_month = today.replace(day=1)
            last_day_prev = first_this_month - timedelta(days=1)
            start = last_day_prev.replace(day=1).isoformat()
            end = last_day_prev.isoformat()
        else:
            return []

        return [
            self._days[k] for k in sorted(self._days.keys())
            if start <= k <= end
        ]

    def _daily_supply_fee(self, date_str: str) -> float:
        """Calculate the daily supply fee for a given date.

        Pro-rates the monthly fee by the actual number of days in that month
        so monthly totals always sum to exactly the monthly fee.
        """
        if self._monthly_supply_fee <= 0:
            return 0.0
        import calendar
        try:
            d = date.fromisoformat(date_str)
            days_in_month = calendar.monthrange(d.year, d.month)[1]
            return self._monthly_supply_fee / days_in_month
        except (ValueError, TypeError):
            return self._monthly_supply_fee / 30.0

    def _aggregate(self, days: list[DayUsage]) -> dict[str, Any]:
        """Aggregate a list of DayUsage into a summary dict.

        Includes the daily supply fee (pro-rated from monthly) in the totals.
        """
        if not days:
            return {
                "import_kwh": 0,
                "export_kwh": 0,
                "controlled_load_kwh": 0,
                "import_cost": 0,
                "export_earnings": 0,
                "supply_charge": 0,
                "net_cost": 0,
                "quality": "no_data",
                "days_count": 0,
            }
        qualities = set(du.quality for du in days)
        if len(qualities) == 1:
            quality = qualities.pop()
        elif "billable" in qualities and "estimated" in qualities:
            quality = "mixed"
        else:
            quality = "mixed"

        energy_cost = sum(du.net_cost for du in days)
        supply_charge = sum(self._daily_supply_fee(du.date) for du in days)

        return {
            "import_kwh": round(sum(du.import_kwh for du in days), 2),
            "export_kwh": round(sum(du.export_kwh for du in days), 2),
            "controlled_load_kwh": round(sum(du.controlled_load_kwh for du in days), 2),
            "import_cost": round(sum(du.import_cost for du in days), 2),
            "export_earnings": round(sum(du.export_earnings for du in days), 2),
            "supply_charge": round(supply_charge, 2),
            "net_cost": round(energy_cost + supply_charge, 2),
            "quality": quality,
            "days_count": len(days),
        }


class DemandChargeCoordinator(DataUpdateCoordinator):
    """Coordinator to track demand charges."""

    def __init__(
        self,
        hass: HomeAssistant,
        energy_coordinator: DataUpdateCoordinator,
        enabled: bool = False,
        rate: float = 0.0,
        start_time: str = "14:00",
        end_time: str = "20:00",
        days: str = "All Days",
        billing_day: int = 1,
        daily_supply_charge: float = 0.0,
        monthly_supply_charge: float = 0.0,
    ) -> None:
        """Initialize the coordinator."""
        self.tesla_coordinator = energy_coordinator
        self.enabled = enabled
        self.rate = rate
        self.start_time = start_time
        self.end_time = end_time
        self.days = days
        self.billing_day = billing_day
        self.daily_supply_charge = daily_supply_charge
        self.monthly_supply_charge = monthly_supply_charge

        # Track peak demand (persists across coordinator updates)
        self._peak_demand_kw = 0.0
        self._last_billing_day_check = None

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_demand_charge",
            update_interval=timedelta(minutes=1),  # Check every minute
        )

    def _is_in_peak_period(self, now: datetime) -> bool:
        """Check if current time is within peak period and correct day."""
        try:
            # Check if today matches the configured days filter
            weekday = now.weekday()  # 0=Monday, 6=Sunday
            if self.days == "Weekdays Only" and weekday >= 5:
                return False  # Saturday or Sunday
            elif self.days == "Weekends Only" and weekday < 5:
                return False  # Monday through Friday

            # Check if current time is within peak period
            # Handle both "HH:MM" and "HH:MM:SS" formats
            start_parts = self.start_time.split(":")
            start_hour, start_minute = int(start_parts[0]), int(start_parts[1])
            end_parts = self.end_time.split(":")
            end_hour, end_minute = int(end_parts[0]), int(end_parts[1])

            current_minutes = now.hour * 60 + now.minute
            start_minutes = start_hour * 60 + start_minute
            end_minutes = end_hour * 60 + end_minute

            # Handle overnight periods (e.g., 22:00 to 06:00)
            if end_minutes <= start_minutes:
                # Peak period wraps around midnight
                return current_minutes >= start_minutes or current_minutes < end_minutes
            else:
                # Normal daytime peak period
                return start_minutes <= current_minutes < end_minutes

        except (ValueError, AttributeError) as err:
            _LOGGER.error("Invalid time format for demand charge period: %s", err)
            return False

    async def _async_update_data(self) -> dict[str, Any]:
        """Update demand charge tracking data."""
        if not self.enabled:
            return {
                "in_peak_period": False,
                "grid_import_power_kw": 0.0,
                "peak_demand_kw": 0.0,
                "estimated_cost": 0.0,
            }

        # Check for billing cycle reset
        now = dt_util.now()
        current_day = now.day

        # If we've crossed the billing day, reset peak demand
        if self._last_billing_day_check is not None:
            # Check if we've passed the billing day since last check
            last_check_day = self._last_billing_day_check.day
            if current_day == self.billing_day and last_check_day != self.billing_day:
                _LOGGER.info("Billing cycle reset triggered on day %d", self.billing_day)
                self.reset_peak_demand()

        self._last_billing_day_check = now

        # Get current grid power from energy coordinator (Tesla, FoxESS, Sigenergy, or Sungrow)
        energy_data = self.tesla_coordinator.data or {}
        grid_power_kw = energy_data.get("grid_power", 0.0)

        # Grid import is positive, export is negative
        # We only care about import for demand charges
        grid_import_kw = max(0, grid_power_kw)

        # Check if in peak period
        in_peak_period = self._is_in_peak_period(now)

        # Update peak demand only for samples inside the billable demand window.
        if in_peak_period and grid_import_kw > self._peak_demand_kw:
            self._peak_demand_kw = grid_import_kw
            _LOGGER.info("New peak demand: %.2f kW", self._peak_demand_kw)

        # Calculate estimated demand charge cost (peak demand * rate)
        estimated_demand_cost = self._peak_demand_kw * self.rate

        # Calculate days elapsed in current billing cycle
        days_elapsed = self._calculate_days_elapsed(now)

        # Calculate days until next billing cycle reset
        days_until_reset = self._calculate_days_until_reset(now)

        # Calculate daily supply charge cost (accumulates daily)
        daily_supply_cost = self.daily_supply_charge * days_elapsed

        # Calculate total monthly cost
        total_monthly_cost = estimated_demand_cost + daily_supply_cost + self.monthly_supply_charge

        return {
            "in_peak_period": in_peak_period,
            "grid_import_power_kw": grid_import_kw,
            "peak_demand_kw": self._peak_demand_kw,
            "estimated_cost": estimated_demand_cost,
            "daily_supply_charge_cost": daily_supply_cost,
            "monthly_supply_charge": self.monthly_supply_charge,
            "total_monthly_cost": total_monthly_cost,
            "days_until_reset": days_until_reset,
            "last_update": dt_util.utcnow(),
        }

    def reset_peak_demand(self) -> None:
        """Reset peak demand tracking (e.g., at start of new billing cycle)."""
        _LOGGER.info("Resetting peak demand from %.2f kW to 0", self._peak_demand_kw)
        self._peak_demand_kw = 0.0

    def _calculate_days_elapsed(self, now: datetime) -> int:
        """Calculate days elapsed since last billing day."""
        current_day = now.day

        if current_day >= self.billing_day:
            # We're past the billing day this month
            days_elapsed = current_day - self.billing_day + 1
        else:
            # We haven't reached the billing day this month yet
            # Need to count from last month's billing day
            # Get the last day of previous month
            first_of_this_month = now.replace(day=1)
            last_month = first_of_this_month - timedelta(days=1)
            last_day_of_last_month = last_month.day

            # Days from billing day last month to end of last month
            if self.billing_day <= last_day_of_last_month:
                days_in_last_month = last_day_of_last_month - self.billing_day + 1
            else:
                # Billing day doesn't exist in last month (e.g., Feb 30)
                # Start from last day of last month
                days_in_last_month = 1

            # Plus days in current month
            days_elapsed = days_in_last_month + current_day

        return days_elapsed

    def _calculate_days_until_reset(self, now: datetime) -> int:
        """Calculate days until next billing cycle reset."""
        current_day = now.day

        if current_day < self.billing_day:
            # Next reset is this month
            return self.billing_day - current_day
        else:
            # Next reset is next month
            # Get the last day of this month
            if now.month == 12:
                next_month = now.replace(year=now.year + 1, month=1, day=1)
            else:
                next_month = now.replace(month=now.month + 1, day=1)

            last_day_this_month = (next_month - timedelta(days=1)).day

            # Days remaining in this month plus billing day in next month
            days_remaining_this_month = last_day_this_month - current_day
            return days_remaining_this_month + self.billing_day


class AEMOPriceCoordinator(DataUpdateCoordinator):
    """Coordinator that fetches AEMO price data directly from AEMO API.

    This coordinator provides an alternative to AmberPriceCoordinator for users
    who want to use AEMO wholesale pricing without an Amber subscription.

    Fetches data directly from AEMO NEMWeb - no external integration required.
    The data is converted to Amber-compatible format so the existing tariff
    converter can be reused.

    Uses adaptive polling to catch new dispatch files quickly:
      WAIT       (>10 s until boundary)  -> 45 s intervals, skip NEMWEB fetch
      PRE-ACTIVE (-10 s ... +15 s)       -> 5 s intervals, fetch NEMWEB
      ACTIVE     (>15 s past boundary)   -> 1 s intervals, fetch NEMWEB
    """

    # Adaptive polling thresholds (seconds relative to the next 5-minute boundary)
    _WAIT_INTERVAL = 45       # Poll interval while well away from the boundary (s)
    _PRE_ACTIVE_WINDOW = 10   # Start gentle polling this many seconds before boundary
    _PRE_ACTIVE_INTERVAL = 5  # Poll interval in the pre-active window (s)
    _ACTIVE_WINDOW = 15       # Switch to rapid polling this many seconds after boundary
    _ACTIVE_INTERVAL = 1      # Poll interval during active file search (s)

    def __init__(
        self,
        hass: HomeAssistant,
        region: str,
        session: aiohttp.ClientSession,
    ) -> None:
        """Initialize the coordinator.

        Args:
            hass: HomeAssistant instance
            region: NEM region code (NSW1, QLD1, VIC1, SA1, TAS1)
            session: aiohttp client session for API requests
        """
        from ..aemo_api import AEMOAPIClient

        self.region = region
        self._client = AEMOAPIClient(session)

        # Adaptive polling state
        self._next_boundary: datetime | None = None
        self._polling_mode: str = "active"  # Start active to get first data fast

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_aemo",
            # Start with 1s interval; adaptive logic will adjust after first data
            update_interval=timedelta(seconds=self._ACTIVE_INTERVAL),
        )

    # ------------------------------------------------------------------
    # Adaptive polling helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_aemo_timestamp(timestamp_str: str) -> datetime | None:
        """Parse AEMO dispatch timestamp (always AEST UTC+10) to naive local datetime."""
        if not timestamp_str or "/" not in timestamp_str:
            return None
        try:
            from datetime import timezone as _tz, timedelta as _td
            aest = _tz(_td(hours=10))
            dt_naive = datetime.strptime(timestamp_str, "%Y/%m/%d %H:%M:%S")
            dt_aest = dt_naive.replace(tzinfo=aest)
            return dt_aest.astimezone().replace(tzinfo=None)
        except (ValueError, TypeError) as e:
            _LOGGER.debug("Failed to parse dispatch timestamp '%s': %s", timestamp_str, e)
            return None

    @staticmethod
    def _calc_next_boundary() -> datetime:
        """Return the next 5-minute wall-clock boundary from now (naive local)."""
        now = datetime.now()
        next_min = ((now.minute // 5) + 1) * 5
        if next_min >= 60:
            return now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        return now.replace(minute=next_min, second=0, microsecond=0)

    def _adjust_poll_interval(self) -> bool:
        """Set update_interval based on proximity to the next dispatch boundary.

        Returns True when we should actually hit NEMWEB this cycle, False when
        we should serve cached data and wait for the boundary.
        """
        if self._next_boundary is None:
            # No boundary known yet - poll now to get first data
            return True

        now = datetime.now()
        secs = (self._next_boundary - now).total_seconds()

        # Mode-transition logs are demoted to DEBUG: each one fires once per
        # 5-min period and the wording ("ACTIVE mode (1 s intervals) -
        # searching for new dispatch file") read as alarming to users with
        # debug logging enabled even though the underlying poll is just a
        # cheap directory listing on AEMO's public NEMWEB. The actual
        # dispatch arrival is still logged at INFO ("AEMO: New dispatch -
        # next boundary X" / "NEMWEB dispatch: ... -> N regions") which is
        # the line that matters for users debugging tariff sync.
        if secs > self._PRE_ACTIVE_WINDOW:
            # WAIT mode - too early to expect a new file
            if self._polling_mode != "wait":
                self._polling_mode = "wait"
                _LOGGER.debug(
                    "AEMO: WAIT mode - next boundary %s in %ds",
                    self._next_boundary.strftime("%H:%M:%S"),
                    int(secs),
                )
            self.update_interval = timedelta(seconds=self._WAIT_INTERVAL)
            return False

        if secs > -self._ACTIVE_WINDOW:
            # PRE-ACTIVE mode - gently start checking
            if self._polling_mode != "pre-active":
                self._polling_mode = "pre-active"
                _LOGGER.debug("AEMO: PRE-ACTIVE mode (5 s intervals)")
            self.update_interval = timedelta(seconds=self._PRE_ACTIVE_INTERVAL)
            return True

        # ACTIVE mode - new file could appear any second
        if self._polling_mode != "active":
            self._polling_mode = "active"
            _LOGGER.debug("AEMO: ACTIVE mode (1 s intervals)")
        self.update_interval = timedelta(seconds=self._ACTIVE_INTERVAL)
        return True

    # ------------------------------------------------------------------
    # Main update loop
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from AEMO API using adaptive polling.

        Polling strategy:
        - After receiving a new dispatch file: enter WAIT mode until just
          before the next 5-minute boundary (45 s check interval).
        - 10 s before the boundary: switch to PRE-ACTIVE (5 s interval).
        - 15 s after the boundary: switch to ACTIVE (1 s interval) and poll
          NEMWEB aggressively until a new file appears.
        - On new file: immediately return to WAIT mode.

        Returns:
            dict with 'current', 'forecast', and 'last_update' in Amber-compatible format
        """
        # Decide whether to hit NEMWEB this cycle
        should_fetch = self._adjust_poll_interval()

        if not should_fetch:
            # WAIT mode - return existing data unchanged
            if self.data:
                return self.data
            # No data yet - fall through to fetch
            should_fetch = True

        try:
            # Fetch current price (5-min dispatch price) with file metadata
            current_prices_all, is_new_dispatch, dispatch_file = (
                await self._client.get_current_prices_with_file()
            )

            current_price_data = None
            if current_prices_all:
                current_price_data = current_prices_all.get(self.region)

            # Handle adaptive boundary tracking
            if is_new_dispatch and current_price_data:
                timestamp = current_price_data.get("timestamp")
                if timestamp:
                    period_dt = self._parse_aemo_timestamp(timestamp)
                    if period_dt:
                        self._next_boundary = self._calc_next_boundary()
                        _LOGGER.info(
                            "AEMO: New dispatch - next boundary %s",
                            self._next_boundary.strftime("%H:%M:%S"),
                        )
            elif not is_new_dispatch and self._next_boundary is None and current_price_data:
                # First run - file already cached but we still need a boundary
                timestamp = current_price_data.get("timestamp")
                if timestamp:
                    period_dt = self._parse_aemo_timestamp(timestamp)
                    if period_dt:
                        candidate = self._calc_next_boundary()
                        secs_until = (candidate - datetime.now()).total_seconds()
                        if secs_until > -self._ACTIVE_WINDOW:
                            self._next_boundary = candidate
                            _LOGGER.info(
                                "AEMO: Boundary initialised from cached dispatch: "
                                "next=%s (in %.0fs)",
                                self._next_boundary.strftime("%H:%M:%S"),
                                secs_until,
                            )

            # Only fetch forecast when we got a new dispatch file (predispatch
            # updates every ~30 min, no point hammering it every second in ACTIVE)
            forecast = None
            if is_new_dispatch:
                forecast = await self._client.get_price_forecast(self.region, periods=96)

            # If no new forecast, preserve existing
            if not forecast and self.data:
                forecast = self.data.get("forecast")

            if not forecast:
                raise UpdateFailed(f"Failed to fetch AEMO forecast for {self.region}")

            # Get current price - prefer current dispatch price, fall back to first forecast
            if current_price_data:
                # Convert $/MWh to c/kWh: $/MWh / 10 = c/kWh
                current_price_cents = current_price_data["price"] / 10.0
                price_source = "dispatch"
            else:
                # Fall back to first forecast period
                current_price_cents = forecast[0]["perKwh"] if forecast else 0
                price_source = "forecast"
                _LOGGER.warning("Could not get current AEMO price, using forecast")

            # Create current price in Amber format
            current_prices = [
                {
                    "perKwh": current_price_cents,
                    "channelType": "general",
                    "type": "CurrentInterval",
                },
                {
                    "perKwh": -current_price_cents,
                    "channelType": "feedIn",
                    "type": "CurrentInterval",
                },
            ]

            if is_new_dispatch:
                _LOGGER.info(
                    "AEMO API data for %s: current=%.2fc/kWh (%s), forecast_periods=%d",
                    self.region, current_price_cents, price_source, len(forecast) // 2
                )
                async_dispatcher_send(
                    self.hass,
                    SIGNAL_AEMO_NEW_DISPATCH,
                    {
                        "region": self.region,
                        "file": dispatch_file,
                        "price_cents": current_price_cents,
                    },
                )

            return {
                "current": current_prices,
                "forecast": forecast,
                "last_update": dt_util.utcnow(),
                "source": "aemo_api",
                "dispatch_file": dispatch_file,
            }

        except Exception as err:
            raise UpdateFailed(f"Error fetching AEMO data: {err}") from err


# Keep old name as alias for backwards compatibility
AEMOSensorCoordinator = AEMOPriceCoordinator


class FlowPowerKWatchPriceCoordinator(DataUpdateCoordinator):
    """Coordinator that fetches Flow Power KWatch API prices."""

    def __init__(
        self,
        hass: HomeAssistant,
        region: str,
        api_key: str,
        session: aiohttp.ClientSession,
    ) -> None:
        from ..flow_power_api import FlowPowerAPIClient

        self.region = region
        self.api_region = FLOW_POWER_KWATCH_REGIONS.get(region, region.lower())
        self._client = FlowPowerAPIClient(api_key, session)
        self._aemo_fallback = AEMOPriceCoordinator(hass, region, session)
        self._using_fallback = False
        self._fallback_reason: str | None = None
        self._kwatch_last_attempt: datetime | None = None
        self._kwatch_last_success: datetime | None = None
        self._kwatch_consecutive_failures = 0

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_flow_power_kwatch",
            update_interval=timedelta(minutes=5),
        )

    @staticmethod
    def _format_update_time(value: datetime | None) -> str | None:
        if value is None:
            return None
        return value.isoformat()

    @staticmethod
    def _fallback_reason_from_error(err: Exception) -> str | None:
        reason = str(err) or type(err).__name__
        if reason == "invalid_api_key":
            return None
        if reason.startswith("api_status_"):
            try:
                status = int(reason.rsplit("_", 1)[-1])
            except ValueError:
                return None
            return reason if status >= 500 else None
        if reason == "invalid_json":
            return reason
        if isinstance(err, (aiohttp.ClientError, asyncio.TimeoutError)):
            return type(err).__name__
        if isinstance(err, UpdateFailed) and "KWatch" in reason:
            return reason
        return None

    async def _fetch_kwatch_data(self) -> dict[str, Any]:
        """Fetch current and forecast prices from Flow Power's KWatch API."""
        from ..flow_power_api import kwatch_prices_to_amber_format

        dispatch = await self._client.dispatch5mins(self.api_region, period=60)
        # Keep the first upcoming half-hour slot; period=2 skips it.
        forecast_30 = await self._client.predispatch30mins(self.api_region, period=1)
        forecast_5 = await self._client.predispatch5mins(self.api_region, period=60)

        if not dispatch:
            raise UpdateFailed(f"No KWatch dispatch prices returned for {self.region}")

        latest_dispatch = dispatch[-1:]
        current_prices = kwatch_prices_to_amber_format(
            latest_dispatch,
            interval_type="CurrentInterval",
            default_duration=5,
        )
        forecast = kwatch_prices_to_amber_format(
            forecast_30,
            interval_type="ForecastInterval",
            default_duration=30,
        )
        forecast_5min = kwatch_prices_to_amber_format(
            forecast_5,
            interval_type="ForecastInterval",
            default_duration=5,
        )

        if not forecast:
            forecast = forecast_5min
        if not forecast:
            raise UpdateFailed(f"No KWatch forecast prices returned for {self.region}")

        latest_cents = latest_dispatch[0]["perKwh"]
        _LOGGER.info(
            "Flow Power KWatch data for %s: current=%.2fc/kWh, forecast_periods=%d",
            self.region,
            latest_cents,
            len(forecast) // 2,
        )

        return {
            "current": current_prices,
            "forecast": forecast,
            "forecast_5min": forecast_5min,
            "last_update": dt_util.utcnow(),
            "source": "flow_power_kwatch",
            "using_fallback": False,
        }

    async def _fetch_aemo_fallback_data(self, reason: str) -> dict[str, Any]:
        """Fetch AEMO Direct prices when KWatch is temporarily unavailable."""
        await self._aemo_fallback.async_request_refresh()
        fallback_data = dict(self._aemo_fallback.data or {})
        if not fallback_data.get("current") or not fallback_data.get("forecast"):
            raise UpdateFailed(
                f"Flow Power KWatch unavailable ({reason}); AEMO fallback unavailable"
            )

        if not self._using_fallback or self._fallback_reason != reason:
            _LOGGER.warning(
                "Flow Power KWatch unavailable for %s (%s); using AEMO Direct fallback",
                self.region,
                reason,
            )
        self._using_fallback = True
        self._fallback_reason = reason
        fallback_data.update(
            {
                "source": "flow_power_kwatch_fallback_aemo",
                "primary_source": "flow_power_kwatch",
                "fallback_source": "aemo_api",
                "using_fallback": True,
                "fallback_reason": reason,
                "kwatch_consecutive_failures": self._kwatch_consecutive_failures,
                "kwatch_last_attempt": self._format_update_time(
                    self._kwatch_last_attempt
                ),
                "kwatch_last_success": self._format_update_time(
                    self._kwatch_last_success
                ),
                "last_update": fallback_data.get("last_update") or dt_util.utcnow(),
            }
        )
        return fallback_data

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch KWatch prices, falling back to AEMO during transient outages."""
        self._kwatch_last_attempt = dt_util.utcnow()
        try:
            data = await self._fetch_kwatch_data()
        except Exception as err:
            self._kwatch_consecutive_failures += 1
            reason = self._fallback_reason_from_error(err)
            if reason is None:
                raise
            try:
                return await self._fetch_aemo_fallback_data(reason)
            except Exception as fallback_err:
                raise UpdateFailed(
                    f"Flow Power KWatch unavailable ({reason}); "
                    f"AEMO fallback failed: {fallback_err}"
                ) from fallback_err

        self._kwatch_last_success = data.get("last_update")
        self._kwatch_consecutive_failures = 0
        if self._using_fallback:
            _LOGGER.info(
                "Flow Power KWatch recovered for %s; returning to primary pricing",
                self.region,
            )
        self._using_fallback = False
        self._fallback_reason = None
        data.update(
            {
                "kwatch_consecutive_failures": 0,
                "kwatch_last_attempt": self._format_update_time(
                    self._kwatch_last_attempt
                ),
                "kwatch_last_success": self._format_update_time(
                    self._kwatch_last_success
                ),
            }
        )
        return data


class EPEXPriceCoordinator(DataUpdateCoordinator):
    """Coordinator that fetches EPEX day-ahead price data.

    Uses the EPEX Predictor API (epexpredictor.batzill.com) for European
    day-ahead electricity prices. Supports DE, AT, BE, NL, SE1-4, DK1-2.

    The API applies surcharges and taxes server-side, so returned prices
    are the final consumer price in ct/kWh.

    Data is converted to Amber-compatible format for the optimizer.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        region: str,
        session: aiohttp.ClientSession,
        surcharge: float = 0.0,
        tax_percent: float = 0.0,
        export_rate: float = 0.0,
    ) -> None:
        """Initialize the coordinator.

        Args:
            hass: HomeAssistant instance
            region: EPEX bidding zone code (DE, AT, BE, NL, SE1-4, DK1-2)
            session: aiohttp client session for API requests
            surcharge: Fixed surcharge in ct/kWh (network fees, levies)
            tax_percent: Tax percentage (e.g. 21 for Belgian VAT)
            export_rate: Fixed feed-in rate in ct/kWh (0 = use wholesale price)
        """
        from ..epex_api import EPEXAPIClient

        self.region = region
        self._surcharge = surcharge
        self._tax_percent = tax_percent
        self._export_rate = export_rate
        self._client = EPEXAPIClient(session)
        # Tracks whether we've already logged the "no export rate configured"
        # warning so it fires once per coordinator lifetime, not every poll.
        self._warned_export_rate_unset = False

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_epex",
            update_interval=timedelta(minutes=30),
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from EPEX API and convert to Amber-compatible format.

        Returns:
            dict with 'current', 'forecast', and 'last_update' in Amber-compatible format
        """
        try:
            prices = await self._client.get_prices(
                region=self.region,
                surcharge=self._surcharge,
                tax_percent=self._tax_percent,
            )

            if not prices:
                raise UpdateFailed(f"No prices returned from EPEX API for {self.region}")

            now = dt_util.utcnow()
            current_prices = []
            forecast_prices = []

            for entry in prices:
                starts_at_str = entry.get("startsAt", "")
                total_ct = entry.get("total", 0)

                if not starts_at_str:
                    continue

                try:
                    starts_at = datetime.fromisoformat(starts_at_str)
                    if starts_at.tzinfo is None:
                        starts_at = starts_at.replace(tzinfo=dt_util.UTC)
                    ends_at = starts_at + timedelta(hours=1)
                except (ValueError, TypeError):
                    continue

                # Determine interval type
                if starts_at <= now < ends_at:
                    interval_type = "CurrentInterval"
                elif ends_at <= now:
                    interval_type = "ActualInterval"
                else:
                    interval_type = "ForecastInterval"

                # Import price entry (ct/kWh = Amber's perKwh format)
                import_entry = {
                    "nemTime": ends_at.isoformat(),
                    "perKwh": total_ct,
                    "channelType": "general",
                    "type": interval_type,
                    "duration": 60,
                }

                # Export price: use fixed rate if configured. The EPEX
                # Predictor API only returns "total" — the final consumer
                # price with surcharge/tax already applied server-side
                # (see class docstring) — it does not expose a separate
                # wholesale/spot component we could use for export
                # valuation. Previously this fell back to -total_ct, which
                # valued exports at the *retail* import rate (surcharge +
                # tax included) instead of wholesale, causing the optimizer
                # to export midday energy it should have held for the
                # evening peak. Default to 0 instead of guessing a price we
                # don't actually have.
                if self._export_rate > 0:
                    export_ct = -self._export_rate
                else:
                    export_ct = 0.0
                    if not self._warned_export_rate_unset:
                        _LOGGER.warning(
                            "EPEX export rate not configured for %s and no "
                            "wholesale/spot price is available from the API "
                            "(only the final consumer price is returned); "
                            "valuing exports at 0 ct/kWh so PowerSync never "
                            "assumes an export price it doesn't have. Set a "
                            "Fixed Export Rate (or export price entity) to "
                            "value exports correctly.",
                            self.region,
                        )
                        self._warned_export_rate_unset = True

                export_entry = {
                    "nemTime": ends_at.isoformat(),
                    "perKwh": export_ct,
                    "channelType": "feedIn",
                    "type": interval_type,
                    "duration": 60,
                }

                if interval_type == "CurrentInterval":
                    current_prices.extend([import_entry, export_entry])
                elif interval_type == "ForecastInterval":
                    forecast_prices.extend([import_entry, export_entry])

            if not current_prices and forecast_prices:
                # No current interval yet — use first forecast as current
                current_prices = forecast_prices[:2]

            _LOGGER.info(
                "EPEX API data for %s: %d current, %d forecast entries "
                "(surcharge=%.1f ct, tax=%.1f%%)",
                self.region,
                len(current_prices),
                len(forecast_prices),
                self._surcharge,
                self._tax_percent,
            )

            return {
                "current": current_prices,
                "forecast": forecast_prices,
                "last_update": dt_util.utcnow(),
                "source": "epex_api",
            }

        except Exception as err:
            raise UpdateFailed(f"Error fetching EPEX data: {err}") from err


class SolcastForecastCoordinator(DataUpdateCoordinator):
    """Coordinator to fetch Solcast solar production forecasts.

    Fetches PV power forecasts from Solcast API and caches them locally.
    Dynamically adjusts update interval based on number of resource IDs to stay
    within Solcast's 10 calls/day hobbyist tier limit.

    Supports multiple resource IDs for split arrays (e.g., east/west facing panels).
    Provide comma-separated resource IDs and forecasts will be combined by summing values.
    """

    # Solcast API base URL
    SOLCAST_API_URL = "https://api.solcast.com.au"

    # Solcast hobbyist tier: 10 API calls per day
    DAILY_API_LIMIT = 10

    def __init__(
        self,
        hass: HomeAssistant,
        api_key: str,
        resource_id: str,
        capacity_kw: float | None = None,
        estimate_type: str = DEFAULT_SOLCAST_ESTIMATE_TYPE,
    ) -> None:
        """Initialize the coordinator.

        Args:
            hass: HomeAssistant instance
            api_key: Solcast API key
            resource_id: Rooftop site resource ID(s) - comma-separated for split arrays
            capacity_kw: System capacity in kW (optional, for validation)
            estimate_type: Solcast estimate to use: estimate, estimate10, or estimate90
        """
        self._api_key = api_key
        self._estimate_type = (
            estimate_type
            if estimate_type in _SOLCAST_ESTIMATE_FIELDS
            else DEFAULT_SOLCAST_ESTIMATE_TYPE
        )
        # Support comma-separated resource IDs for split arrays
        self._resource_ids = [rid.strip() for rid in resource_id.split(",") if rid.strip()]
        self._capacity_kw = capacity_kw
        self._session = async_get_clientsession(hass)

        # Cache for full-day forecast (stored on first fetch of the day)
        self._daily_forecast_date: str | None = None  # Date string (YYYY-MM-DD)
        self._daily_forecast_kwh: float | None = None  # Full day's forecast
        self._daily_forecast_peak_kw: float | None = None  # Peak for the day

        # Rate limiting tracking (persisted to survive restarts)
        self._rate_limited = False
        self._last_rate_limit_time: datetime | None = None
        self._api_calls_today = 0
        self._api_calls_date: str | None = None
        self._rate_limit_store = Store(hass, 1, f"{DOMAIN}_solcast_rate_limit")
        self._forecast_store = Store(hass, 1, f"{DOMAIN}_solcast_forecast_cache")

        # Calculate update interval based on number of resources
        # Each resource requires 1 API call per update
        # With 10 calls/day limit: interval = 24 / (10 / n_resources) hours
        n_resources = len(self._resource_ids)
        calls_per_update = n_resources  # We skip estimated_actuals to save calls
        max_updates_per_day = self.DAILY_API_LIMIT // calls_per_update
        # Leave some buffer - aim for 80% of max to avoid hitting limit
        safe_updates = max(1, int(max_updates_per_day * 0.8))
        update_hours = max(3, 24 // safe_updates)  # Minimum 3 hours

        self._update_interval = timedelta(hours=update_hours)

        _LOGGER.info(
            f"Solcast coordinator: {n_resources} resource(s), "
            f"{calls_per_update} API call(s)/update, "
            f"update interval: {update_hours}h ({safe_updates} updates/day), "
            f"estimate_type={self._estimate_type}"
        )

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_solcast_forecast",
            update_interval=self._update_interval,
        )

    def _get_pv_estimate(self, period: dict[str, Any]) -> float:
        """Return the configured Solcast estimate value for a forecast period."""
        for field in _SOLCAST_ESTIMATE_FIELDS[self._estimate_type]:
            value = period.get(field)
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    continue
        return 0.0

    def _find_solcast_sensor(self, patterns: list[str]) -> Any | None:
        """Find a Solcast sensor by trying multiple possible entity ID patterns."""
        for pattern in patterns:
            state = self.hass.states.get(pattern)
            if state and state.state not in ("unavailable", "unknown", None, ""):
                return state
        return None

    async def _try_read_from_solcast_integration(self) -> dict[str, Any] | None:
        """Try to read forecast data from the Solcast HA integration.

        If the Solcast integration is installed, we read from its sensors instead
        of making our own API calls. This avoids doubling API usage (10 calls/day limit).

        Supports multiple naming conventions:
        - sensor.solcast_pv_forecast_* (current Solcast integration)
        - sensor.solcast_forecast_* (alternative naming)
        - sensor.solcast_* (older versions)

        Returns:
            Forecast data dict if Solcast integration is available, None otherwise
        """
        try:
            # Try multiple possible sensor names for today's forecast
            today_patterns = [
                "sensor.solcast_pv_forecast_forecast_today",
                "sensor.solcast_forecast_today",
                "sensor.solcast_pv_forecast_today",
            ]
            today_state = self._find_solcast_sensor(today_patterns)
            if not today_state:
                return None

            # Get all the sensor values - try multiple naming patterns
            tomorrow_state = self._find_solcast_sensor([
                "sensor.solcast_pv_forecast_forecast_tomorrow",
                "sensor.solcast_forecast_tomorrow",
                "sensor.solcast_pv_forecast_tomorrow",
            ])
            remaining_state = self._find_solcast_sensor([
                "sensor.solcast_pv_forecast_forecast_remaining_today",
                "sensor.solcast_forecast_remaining_today",
                "sensor.solcast_pv_forecast_remaining_today",
            ])
            peak_today_state = self._find_solcast_sensor([
                "sensor.solcast_pv_forecast_peak_forecast_today",
                "sensor.solcast_peak_forecast_today",
                "sensor.solcast_pv_forecast_peak_today",
            ])
            peak_tomorrow_state = self._find_solcast_sensor([
                "sensor.solcast_pv_forecast_peak_forecast_tomorrow",
                "sensor.solcast_peak_forecast_tomorrow",
                "sensor.solcast_pv_forecast_peak_tomorrow",
            ])
            power_now_state = self._find_solcast_sensor([
                "sensor.solcast_pv_forecast_power_now",
                "sensor.solcast_power_now",
                "sensor.solcast_pv_forecast_now",
            ])

            # Parse values - these are already in kWh
            today_forecast = float(today_state.state) if today_state.state else 0
            tomorrow_forecast = float(tomorrow_state.state) if tomorrow_state and tomorrow_state.state not in ("unavailable", "unknown", None, "") else 0
            remaining = float(remaining_state.state) if remaining_state and remaining_state.state not in ("unavailable", "unknown", None, "") else today_forecast

            # Peak values are in W - convert to kW
            today_peak = None
            if peak_today_state and peak_today_state.state not in ("unavailable", "unknown", None, ""):
                today_peak = float(peak_today_state.state) / 1000.0  # W to kW

            tomorrow_peak = None
            if peak_tomorrow_state and peak_tomorrow_state.state not in ("unavailable", "unknown", None, ""):
                tomorrow_peak = float(peak_tomorrow_state.state) / 1000.0  # W to kW

            # Current power estimate is in W - convert to kW
            current_estimate = None
            if power_now_state and power_now_state.state not in ("unavailable", "unknown", None, ""):
                current_estimate = float(power_now_state.state) / 1000.0  # W to kW

            # Try to get detailed hourly forecast from sensor attributes
            # The Solcast HA integration stores this in various attribute names
            detailed_forecast = None
            if today_state.attributes:
                # Try common attribute names used by Solcast HA integration
                detailed_forecast = (
                    today_state.attributes.get("detailedForecast") or
                    today_state.attributes.get("forecast_today") or
                    today_state.attributes.get("detailedHourly") or
                    today_state.attributes.get("forecasts")
                )

            # Build hourly forecast data for chart overlay
            hourly_forecast = []
            if detailed_forecast and isinstance(detailed_forecast, list):
                now = dt_util.now()
                today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                today_end = now.replace(hour=23, minute=59, second=59, microsecond=999999)

                for period in detailed_forecast:
                    try:
                        # Parse period end time and the configured estimate field.
                        period_end_str = period.get("period_end", "")
                        pv_estimate = self._get_pv_estimate(period)

                        if period_end_str:
                            period_end = datetime.fromisoformat(period_end_str.replace("Z", "+00:00"))
                            period_local = dt_util.as_local(period_end)

                            # Only include today's data for the chart
                            if today_start <= period_local <= today_end:
                                hourly_forecast.append({
                                    "time": period_local.strftime("%H:%M"),
                                    "hour": period_local.hour,
                                    "pv_estimate_kw": round(pv_estimate, 2),
                                })
                    except (ValueError, TypeError, KeyError):
                        continue

            # Try to also get tomorrow's detailed forecast for optimizer (48h horizon)
            # Check the tomorrow forecast sensor for detailed data
            tomorrow_detailed = None
            tomorrow_state_obj = self._find_solcast_sensor([
                "sensor.solcast_pv_forecast_forecast_tomorrow",
                "sensor.solcast_forecast_tomorrow",
                "sensor.solcast_pv_forecast_tomorrow",
            ])
            if tomorrow_state_obj and tomorrow_state_obj.attributes:
                tomorrow_detailed = (
                    tomorrow_state_obj.attributes.get("detailedForecast") or
                    tomorrow_state_obj.attributes.get("forecast_tomorrow") or
                    tomorrow_state_obj.attributes.get("detailedHourly") or
                    tomorrow_state_obj.attributes.get("forecasts")
                )

            # Combine today and tomorrow forecasts for optimizer
            full_forecasts = []
            if detailed_forecast and isinstance(detailed_forecast, list):
                full_forecasts.extend(detailed_forecast)
            if tomorrow_detailed and isinstance(tomorrow_detailed, list):
                full_forecasts.extend(tomorrow_detailed)

            if full_forecasts:
                selected_today = 0.0
                selected_remaining = 0.0
                selected_tomorrow = 0.0
                selected_today_peak = 0.0
                selected_tomorrow_peak = 0.0
                selected_current: float | None = None
                has_today_period = False
                has_tomorrow_period = False
                now = dt_util.now()
                today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                today_end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
                tomorrow_end = today_end + timedelta(days=1)
                period_hours = 0.5

                for period in full_forecasts:
                    if not isinstance(period, dict):
                        continue
                    period_end_str = period.get("period_end") or period.get("period")
                    period_start_str = period.get("period_start")
                    if not period_end_str and not period_start_str:
                        continue
                    try:
                        if period_end_str:
                            period_end = (
                                period_end_str
                                if isinstance(period_end_str, datetime)
                                else datetime.fromisoformat(period_end_str.replace("Z", "+00:00"))
                            )
                        else:
                            period_start = (
                                period_start_str
                                if isinstance(period_start_str, datetime)
                                else datetime.fromisoformat(period_start_str.replace("Z", "+00:00"))
                            )
                            period_end = period_start + timedelta(minutes=30)
                        period_local = dt_util.as_local(period_end)
                        pv_estimate = self._get_pv_estimate(period)

                        if selected_current is None and period_local >= now:
                            selected_current = pv_estimate
                        if today_start <= period_local <= today_end:
                            has_today_period = True
                            selected_today += pv_estimate * period_hours
                            selected_today_peak = max(selected_today_peak, pv_estimate)
                            if period_local >= now:
                                selected_remaining += pv_estimate * period_hours
                        elif today_end < period_local <= tomorrow_end:
                            has_tomorrow_period = True
                            selected_tomorrow += pv_estimate * period_hours
                            selected_tomorrow_peak = max(selected_tomorrow_peak, pv_estimate)
                    except (ValueError, TypeError, KeyError):
                        continue

                if has_today_period:
                    today_forecast = selected_today
                    remaining = selected_remaining
                    today_peak = selected_today_peak
                if has_tomorrow_period:
                    tomorrow_forecast = selected_tomorrow
                    tomorrow_peak = selected_tomorrow_peak
                if selected_current is not None:
                    current_estimate = selected_current

            _LOGGER.info(
                f"Solcast (from HA integration): Today={today_forecast:.1f}kWh, "
                f"remaining={remaining:.1f}kWh, Tomorrow={tomorrow_forecast:.1f}kWh, "
                f"hourly_points={len(hourly_forecast)}, raw_periods={len(full_forecasts)}, "
                f"estimate_type={self._estimate_type}"
            )

            return {
                "available": True,
                "today_forecast_kwh": round(today_forecast, 2),
                "today_remaining_kwh": round(remaining, 2),
                "today_total_kwh": round(today_forecast, 2),
                "tomorrow_total_kwh": round(tomorrow_forecast, 2),
                "today_peak_kw": round(today_peak, 2) if today_peak else None,
                "tomorrow_peak_kw": round(tomorrow_peak, 2) if tomorrow_peak else None,
                "current_estimate_kw": round(current_estimate, 2) if current_estimate else None,
                "hourly_forecast": hourly_forecast,  # For chart overlay
                "forecasts": full_forecasts if full_forecasts else None,  # Raw periods for optimizer
                "estimate_type": self._estimate_type,
                "forecast_periods": len(full_forecasts) if full_forecasts else len(hourly_forecast),
                "last_update": dt_util.utcnow(),
                "source": "solcast_integration",
            }

        except (ValueError, TypeError, AttributeError) as e:
            _LOGGER.debug(f"Could not read from Solcast integration: {e}")
            return None

    async def _fetch_forecast_for_resource(self, resource_id: str) -> list[dict] | None:
        """Fetch forecast for a single resource ID.

        Args:
            resource_id: Solcast rooftop site resource ID

        Returns:
            List of forecast periods or None on error
        """
        url = f"{self.SOLCAST_API_URL}/rooftop_sites/{resource_id}/forecasts"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
        }
        params = {"hours": 48, "format": "json"}

        async with self._session.get(url, headers=headers, params=params) as response:
            if response.status == 401:
                # Most common cause: user pasted a stale/rotated API key, or
                # the resource_id belongs to a different Solcast account than
                # the API key does. Surface key prefix + resource so the user
                # can at least tell that the right values reached the API.
                key_preview = (
                    f"{self._api_key[:4]}…{self._api_key[-4:]}"
                    if len(self._api_key) > 8 else "<short>"
                )
                raise UpdateFailed(
                    "Solcast API 401 Unauthorized — API key does not match an "
                    "active account, or resource_id belongs to a different "
                    "account. Verify both at toolkit.solcast.com.au → API "
                    f"Management. (key={key_preview}, resource={resource_id})"
                )
            if response.status == 429:
                self._rate_limited = True
                self._last_rate_limit_time = dt_util.now()
                # Trust the server — our counter may be wrong (e.g. calls from
                # another session or before counter was persisted)
                if self._api_calls_today < self.DAILY_API_LIMIT:
                    _LOGGER.warning(
                        f"Solcast 429 but counter shows {self._api_calls_today}/{self.DAILY_API_LIMIT} — "
                        f"syncing counter to server reality"
                    )
                    self._api_calls_today = self.DAILY_API_LIMIT
                    self.hass.async_create_task(
                        self._rate_limit_store.async_save({
                            "date": dt_util.utcnow().strftime("%Y-%m-%d"),
                            "calls": self._api_calls_today,
                        })
                    )
                _LOGGER.warning(
                    f"Solcast API rate limit hit for resource {resource_id[:8]}... "
                    f"(API calls today: {self._api_calls_today}/{self.DAILY_API_LIMIT}). "
                    f"Will use cached data until tomorrow."
                )
                return None
            if response.status != 200:
                _LOGGER.error(f"Solcast API error for resource {resource_id[:8]}: {response.status}")
                return None

            data = await response.json()
            return data.get("forecasts", [])

    async def _fetch_estimated_actuals_for_resource(self, resource_id: str) -> list[dict] | None:
        """Fetch estimated actuals (past production) for a single resource ID.

        Args:
            resource_id: Solcast rooftop site resource ID

        Returns:
            List of estimated actual periods or None on error
        """
        url = f"{self.SOLCAST_API_URL}/rooftop_sites/{resource_id}/estimated_actuals"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
        }
        # Get last 24 hours of estimated actuals (covers today's past production)
        params = {"hours": 24, "format": "json"}

        try:
            async with self._session.get(url, headers=headers, params=params) as response:
                if response.status == 401:
                    _LOGGER.warning("Solcast estimated_actuals auth failed")
                    return None
                if response.status == 429:
                    _LOGGER.warning(f"Solcast API rate limit for estimated_actuals {resource_id[:8]}...")
                    return None
                if response.status != 200:
                    _LOGGER.debug(f"Solcast estimated_actuals error for {resource_id[:8]}: {response.status}")
                    return None

                data = await response.json()
                return data.get("estimated_actuals", [])
        except Exception as e:
            _LOGGER.debug(f"Error fetching estimated_actuals: {e}")
            return None

    def _combine_forecasts(self, base: list[dict], additional: list[dict]) -> list[dict]:
        """Combine forecasts from multiple resources by summing pv_estimate values.

        Args:
            base: Base forecast list
            additional: Additional forecast list to add

        Returns:
            Combined forecast list with summed values
        """
        additional_lookup = {f.get("period_end"): f for f in additional}

        combined = []
        for forecast in base:
            period_end = forecast.get("period_end")
            result = dict(forecast)

            if period_end in additional_lookup:
                add_f = additional_lookup[period_end]
                if result.get("pv_estimate") is not None and add_f.get("pv_estimate") is not None:
                    result["pv_estimate"] = result["pv_estimate"] + add_f["pv_estimate"]
                if result.get("pv_estimate10") is not None and add_f.get("pv_estimate10") is not None:
                    result["pv_estimate10"] = result["pv_estimate10"] + add_f["pv_estimate10"]
                if result.get("pv_estimate90") is not None and add_f.get("pv_estimate90") is not None:
                    result["pv_estimate90"] = result["pv_estimate90"] + add_f["pv_estimate90"]

            combined.append(result)

        return combined

    async def _restore_rate_limit_state(self) -> None:
        """Restore API call counter from persistent storage."""
        try:
            data = await self._rate_limit_store.async_load()
            if data:
                # Solcast resets at UTC midnight
                today_str = dt_util.utcnow().strftime("%Y-%m-%d")
                if data.get("date") == today_str:
                    self._api_calls_today = data.get("calls", 0)
                    self._api_calls_date = today_str
                    if self._api_calls_today >= self.DAILY_API_LIMIT:
                        self._rate_limited = True
                    _LOGGER.info(
                        f"Restored Solcast API call counter: {self._api_calls_today}/{self.DAILY_API_LIMIT} "
                        f"(rate_limited={self._rate_limited})"
                    )
        except Exception:
            pass

    async def _save_forecast_cache(self, data: dict[str, Any]) -> None:
        """Persist last good forecast data to survive restarts."""
        try:
            cache = {
                "date": dt_util.now().strftime("%Y-%m-%d"),
                "today_forecast_kwh": data.get("today_forecast_kwh"),
                "today_remaining_kwh": data.get("today_remaining_kwh"),
                "today_total_kwh": data.get("today_total_kwh"),
                "tomorrow_total_kwh": data.get("tomorrow_total_kwh"),
                "today_peak_kw": data.get("today_peak_kw"),
                "tomorrow_peak_kw": data.get("tomorrow_peak_kw"),
                "source": data.get("source"),
                "estimate_type": data.get("estimate_type", self._estimate_type),
                "forecasts": data.get("forecasts"),
                # Also persist the in-memory full-day forecast cache so that
                # restarting mid-day doesn't reset it and force the coordinator
                # into the "today_remaining becomes today_forecast" fallback
                # that makes the forecast sensor show partial-day numbers.
                "_daily_forecast_date": self._daily_forecast_date,
                "_daily_forecast_kwh": self._daily_forecast_kwh,
                "_daily_forecast_peak_kw": self._daily_forecast_peak_kw,
            }
            await self._forecast_store.async_save(cache)
        except Exception:
            pass

    async def _restore_daily_forecast_cache(self) -> None:
        """Restore the in-memory _daily_forecast_* fields from disk.

        Ensures that a mid-day HA restart doesn't reset the cached full-day
        forecast back to None and then overwrite it with `today_remaining`
        on the next fetch (which would make the sensor show only the
        rest-of-day forecast as if it were the full day).
        """
        try:
            cache = await self._forecast_store.async_load()
            if not cache:
                return
            cached_estimate_type = cache.get("estimate_type")
            if (
                cached_estimate_type != self._estimate_type
                and (cached_estimate_type is not None or self._estimate_type != DEFAULT_SOLCAST_ESTIMATE_TYPE)
            ):
                return
            cached_date = cache.get("_daily_forecast_date")
            if cached_date != dt_util.now().strftime("%Y-%m-%d"):
                return
            self._daily_forecast_date = cached_date
            # Prefer the explicit full-day cache if persisted; fall back to
            # today_forecast_kwh which older releases stored under that key.
            self._daily_forecast_kwh = (
                cache.get("_daily_forecast_kwh")
                if cache.get("_daily_forecast_kwh") is not None
                else cache.get("today_forecast_kwh")
            )
            self._daily_forecast_peak_kw = (
                cache.get("_daily_forecast_peak_kw")
                if cache.get("_daily_forecast_peak_kw") is not None
                else cache.get("today_peak_kw")
            )
            _LOGGER.info(
                "Solcast: restored full-day forecast cache for %s: %.1fkWh",
                self._daily_forecast_date, self._daily_forecast_kwh or 0,
            )
        except Exception:
            pass

    async def _restore_forecast_cache(self) -> dict[str, Any] | None:
        """Restore last good forecast data from persistent storage."""
        try:
            cache = await self._forecast_store.async_load()
            if cache and cache.get("date") == dt_util.now().strftime("%Y-%m-%d"):
                cached_estimate_type = cache.get("estimate_type")
                if (
                    cached_estimate_type != self._estimate_type
                    and (cached_estimate_type is not None or self._estimate_type != DEFAULT_SOLCAST_ESTIMATE_TYPE)
                ):
                    return None
                forecasts = cache.get("forecasts")
                n_periods = len(forecasts) if forecasts else 0
                _LOGGER.info(
                    f"Restored cached solar forecast: "
                    f"today={cache.get('today_forecast_kwh')}kWh, "
                    f"{n_periods} forecast periods"
                )
                return {
                    "available": True,
                    "today_forecast_kwh": cache.get("today_forecast_kwh", 0),
                    "today_remaining_kwh": cache.get("today_remaining_kwh", 0),
                    "today_total_kwh": cache.get("today_total_kwh", 0),
                    "tomorrow_total_kwh": cache.get("tomorrow_total_kwh", 0),
                    "today_peak_kw": cache.get("today_peak_kw"),
                    "tomorrow_peak_kw": cache.get("tomorrow_peak_kw"),
                    "current_estimate_kw": None,
                    "forecasts": forecasts,
                    "estimate_type": cache.get("estimate_type", self._estimate_type),
                    "forecast_periods": n_periods,
                    "last_update": dt_util.utcnow(),
                    "source": f"{cache.get('source', 'cache')}_restored",
                }
            return None
        except Exception:
            return None

    async def _restore_from_ha_state(self) -> dict[str, Any] | None:
        """Restore forecast from HA's last known sensor state or recorder history.

        First checks hass.states for a non-zero value (restored from recorder on startup).
        If that's 0 (from a previous bug), queries the recorder for the last non-zero value
        from today's history.
        """
        entity_ids = [
            "sensor.power_sync_solcast_today_forecast",
            "sensor.power_sync_solar_forecast_today",
        ]

        def _make_result(today_kwh: float, source: str) -> dict[str, Any]:
            return {
                "available": True,
                "today_forecast_kwh": today_kwh,
                "today_remaining_kwh": 0,
                "today_total_kwh": today_kwh,
                "tomorrow_total_kwh": 0,
                "today_peak_kw": None,
                "tomorrow_peak_kw": None,
                "current_estimate_kw": None,
                "forecasts": None,
                "forecast_periods": 0,
                "last_update": dt_util.utcnow(),
                "source": source,
            }

        try:
            # First: check current state (fast path)
            for entity_id in entity_ids:
                state = self.hass.states.get(entity_id)
                if state and state.state not in ("unavailable", "unknown", None, ""):
                    try:
                        today_kwh = float(state.state)
                        if today_kwh > 0:
                            _LOGGER.info(
                                f"Restored solar forecast from HA state: "
                                f"{entity_id}={today_kwh:.1f}kWh"
                            )
                            return _make_result(today_kwh, "ha_state_restored")
                    except (ValueError, TypeError):
                        continue

            # Second: query recorder history for last non-zero value today
            try:
                from homeassistant.components.recorder import get_instance
                from homeassistant.components.recorder.history import state_changes_during_period

                now = dt_util.now()
                start = now.replace(hour=0, minute=0, second=0, microsecond=0)

                for entity_id in entity_ids:
                    history = await get_instance(self.hass).async_add_executor_job(
                        state_changes_during_period,
                        self.hass,
                        start,
                        now,
                        entity_id,
                    )
                    states = history.get(entity_id, [])
                    # Walk backwards to find last non-zero value
                    for hist_state in reversed(states):
                        if hist_state.state in ("unavailable", "unknown", None, ""):
                            continue
                        try:
                            val = float(hist_state.state)
                            if val > 0:
                                _LOGGER.info(
                                    f"Restored solar forecast from recorder history: "
                                    f"{entity_id}={val:.1f}kWh (from {hist_state.last_changed})"
                                )
                                return _make_result(val, "recorder_restored")
                        except (ValueError, TypeError):
                            continue
            except Exception as ex:
                _LOGGER.debug(f"Could not query recorder for solar forecast: {ex}")

        except Exception:
            pass
        return None

    def _can_make_api_call(self) -> bool:
        """Check if we can make another API call without exceeding the daily limit."""
        # Solcast resets at UTC midnight, so use UTC date
        today_str = dt_util.utcnow().strftime("%Y-%m-%d")
        if self._api_calls_date != today_str:
            # New day — would be reset in _track_api_call
            return True
        return self._api_calls_today < self.DAILY_API_LIMIT

    def _track_api_call(self) -> None:
        """Track API call for rate limit awareness."""
        # Solcast resets at UTC midnight, so use UTC date
        today_str = dt_util.utcnow().strftime("%Y-%m-%d")
        if self._api_calls_date != today_str:
            # New UTC day - reset counter
            self._api_calls_date = today_str
            self._api_calls_today = 0
            self._rate_limited = False

        self._api_calls_today += 1

        if self._api_calls_today >= self.DAILY_API_LIMIT:
            self._rate_limited = True
            _LOGGER.warning(
                f"Solcast API daily limit reached ({self._api_calls_today}/{self.DAILY_API_LIMIT}). "
                f"Using cached data until tomorrow."
            )

        # Persist to survive restarts
        self.hass.async_create_task(
            self._rate_limit_store.async_save({
                "date": today_str,
                "calls": self._api_calls_today,
            })
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch forecast data from Solcast.

        First checks if the Solcast HA integration is installed - if so, reads from
        its sensors to avoid doubling API calls. Only makes direct API calls if the
        Solcast integration is not available.

        Supports multiple resource IDs - values are combined by summing.

        IMPORTANT: We skip estimated_actuals API calls to conserve API budget.
        The hobbyist tier only allows 10 calls/day, and with split arrays each
        resource requires its own call. Estimated actuals are optional - we use
        cached full-day forecasts instead.
        """
        # Restore rate limit state on first run (persisted across restarts)
        if self._api_calls_date is None:
            await self._restore_rate_limit_state()
            # Restore the full-day forecast cache too. Without this, restarting
            # mid-day leaves _daily_forecast_date == None and the fetch logic
            # below falls into the "new day → cache today_remaining" fallback
            # that makes the sensor display only the rest-of-day forecast.
            await self._restore_daily_forecast_cache()

        # First, check if Solcast HA integration is installed and has data
        # This avoids doubling API calls if user has both integrations
        solcast_data = await self._try_read_from_solcast_integration()
        if solcast_data:
            # Guard: if the integration reports 0 but we have cached non-zero data,
            # the integration is likely rate-limited — use cached data instead.
            # Today's total forecast should never drop to 0 mid-day.
            new_kwh = solcast_data.get("today_forecast_kwh", 0)
            cached_kwh = self.data.get("today_forecast_kwh", 0) if self.data else 0
            if new_kwh == 0 and cached_kwh > 0:
                _LOGGER.info(
                    f"Solcast HA integration reported 0kWh but cached forecast is "
                    f"{cached_kwh:.1f}kWh — likely rate-limited, using cached data"
                )
                return self.data
            _LOGGER.debug("Using data from Solcast HA integration (no API calls needed)")
            # Persist good data so it survives restarts
            self.hass.async_create_task(self._save_forecast_cache(solcast_data))
            return solcast_data

        # Check if we're rate limited — but verify with a real API call
        # on first update after restore (persisted counter may be stale)
        if self._rate_limited:
            if self.data and self.data.get("today_forecast_kwh", 0) > 0:
                _LOGGER.debug(
                    f"Solcast API rate limited - using cached forecast data. "
                    f"API calls today: {self._api_calls_today}/{self.DAILY_API_LIMIT}"
                )
                return self.data
            # No in-memory data — counter may be stale from a previous timezone
            # mismatch or old persisted state. Try one verification call.
            if not getattr(self, "_rate_limit_verified", False):
                self._rate_limit_verified = True
                _LOGGER.info(
                    "Solcast rate-limited from restore — verifying with one API call"
                )
                # Temporarily clear rate limit so the fetch logic runs
                self._rate_limited = False
                self._api_calls_today = 0
                # Fall through to the fetch logic below
            else:
                # Already verified, genuinely rate limited
                restored = await self._restore_forecast_cache()
                if restored:
                    _LOGGER.info(
                        f"Solcast API rate limited - restored forecast from storage. "
                        f"API calls today: {self._api_calls_today}/{self.DAILY_API_LIMIT}"
                    )
                    return restored
                restored = await self._restore_from_ha_state()
                if restored:
                    _LOGGER.info(
                        f"Solcast API rate limited - restored forecast from HA sensor state. "
                        f"API calls today: {self._api_calls_today}/{self.DAILY_API_LIMIT}"
                    )
                    return restored
                _LOGGER.warning(
                    f"Solcast API rate limited and no cached forecast available. "
                    f"API calls today: {self._api_calls_today}/{self.DAILY_API_LIMIT}"
                )
                return self.data or {"available": False}

        # Solcast integration not available - make our own API calls
        # Hard guard: refuse to make API calls if daily limit already reached
        n_resources = len(self._resource_ids)
        if self._api_calls_today + n_resources > self.DAILY_API_LIMIT:
            _LOGGER.warning(
                f"Solcast API: skipping fetch — would exceed daily limit "
                f"({self._api_calls_today} + {n_resources} > {self.DAILY_API_LIMIT}). "
                f"Using cached data."
            )
            self._rate_limited = True
            if self.data and self.data.get("today_forecast_kwh", 0) > 0:
                return self.data
            restored = await self._restore_forecast_cache()
            if restored:
                return restored
            restored = await self._restore_from_ha_state()
            if restored:
                return restored
            return self.data or {"available": False}

        try:
            async with asyncio.timeout(60):  # Longer timeout for multiple API calls
                _LOGGER.info(
                    f"Fetching Solcast forecast for {n_resources} resource(s). "
                    f"API calls today: {self._api_calls_today}/{self.DAILY_API_LIMIT}"
                )

                # Fetch forecasts from first resource
                self._track_api_call()
                forecasts = await self._fetch_forecast_for_resource(self._resource_ids[0])
                if not forecasts:
                    _LOGGER.warning("No forecasts from Solcast API")
                    if self.data and self.data.get("today_forecast_kwh", 0) > 0:
                        return self.data
                    # Try persistent cache (survives restarts)
                    restored = await self._restore_forecast_cache()
                    if restored:
                        _LOGGER.info("Restored solar forecast from persistent cache after API failure")
                        return restored
                    # Last resort: read last known sensor state from HA
                    restored = await self._restore_from_ha_state()
                    if restored:
                        _LOGGER.info("Restored solar forecast from HA sensor state after API failure")
                        return restored
                    return {"available": False}

                # NOTE: We intentionally skip estimated_actuals to save API calls
                # With 10 calls/day limit and split arrays, we need to conserve budget
                # The full-day forecast will be estimated from cached values instead
                estimated_actuals = None

                # If multiple resources, fetch and combine
                if len(self._resource_ids) > 1:
                    for resource_id in self._resource_ids[1:]:
                        if not self._can_make_api_call():
                            _LOGGER.warning(
                                f"Solcast API daily limit reached — skipping resource {resource_id[:8]}..."
                            )
                            break
                        self._track_api_call()
                        additional_forecasts = await self._fetch_forecast_for_resource(resource_id)
                        if additional_forecasts:
                            forecasts = self._combine_forecasts(forecasts, additional_forecasts)
                        else:
                            _LOGGER.warning(f"Failed to fetch forecast from resource {resource_id[:8]}...")

                    _LOGGER.info(f"Combined data from {len(self._resource_ids)} Solcast sites")

            if not forecasts:
                return {"available": False}

            # Calculate totals
            now = dt_util.now()
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            today_end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
            tomorrow_end = today_end + timedelta(days=1)

            today_past = 0.0  # Production that already happened today (from estimated_actuals)
            today_remaining = 0.0  # Future production today (from forecasts)
            tomorrow_total = 0.0
            today_peak = 0.0
            tomorrow_peak = 0.0
            current_estimate = None
            period_hours = 0.5  # 30-minute periods

            # Sum up past production from estimated_actuals (today only)
            if estimated_actuals:
                for actual in estimated_actuals:
                    period_end_str = actual.get("period_end", "")
                    pv_estimate = self._get_pv_estimate(actual)

                    try:
                        period_end = datetime.fromisoformat(period_end_str.replace("Z", "+00:00"))
                        period_end_local = dt_util.as_local(period_end)

                        # Only count today's past production
                        if today_start <= period_end_local <= now:
                            today_past += pv_estimate * period_hours
                            today_peak = max(today_peak, pv_estimate)
                    except (ValueError, TypeError):
                        pass

            # Sum up future production from forecasts
            for forecast in forecasts:
                period_end_str = forecast.get("period_end", "")
                pv_estimate = self._get_pv_estimate(forecast)

                try:
                    period_end = datetime.fromisoformat(period_end_str.replace("Z", "+00:00"))
                    period_end_local = dt_util.as_local(period_end)

                    # Set current estimate to first forecast period
                    if current_estimate is None:
                        current_estimate = pv_estimate

                    if period_end_local <= today_end:
                        today_remaining += pv_estimate * period_hours
                        today_peak = max(today_peak, pv_estimate)
                    elif period_end_local <= tomorrow_end:
                        tomorrow_total += pv_estimate * period_hours
                        tomorrow_peak = max(tomorrow_peak, pv_estimate)

                except (ValueError, TypeError) as e:
                    _LOGGER.debug(f"Error parsing forecast period: {e}")

            # Full day calculation
            today_str = now.strftime("%Y-%m-%d")

            if today_past > 0:
                # We have estimated actuals - use actual + remaining
                today_forecast = today_past + today_remaining
                # Update cache with this more accurate value
                self._daily_forecast_date = today_str
                self._daily_forecast_kwh = today_forecast
                self._daily_forecast_peak_kw = today_peak
                _LOGGER.info(
                    f"Solcast forecast updated: Today total={today_forecast:.1f}kWh "
                    f"(past={today_past:.1f}kWh + remaining={today_remaining:.1f}kWh), "
                    f"peak={today_peak:.2f}kW, Tomorrow={tomorrow_total:.1f}kWh"
                )
            else:
                # No estimated actuals - use cached full-day or remaining as fallback
                if self._daily_forecast_date != today_str:
                    # Cached date doesn't match today — either a genuine new day
                    # (midnight rollover) or a restart where _restore_daily_forecast_cache
                    # couldn't find a valid cache. In the genuine new-day case
                    # `now` is early morning and `today_remaining` ≈ today_total,
                    # so caching it is fine. In the restart-mid-day case the
                    # value will be suspiciously low — log a hint so users can
                    # tell the two apart.
                    is_likely_partial_day = now.hour >= 10 and today_remaining < 5.0
                    if is_likely_partial_day:
                        _LOGGER.warning(
                            "Solcast: caching partial-day remaining (%.1fkWh) as today's "
                            "forecast because no full-day cache was restored. "
                            "If this is a restart after %02d:00, the forecast will be "
                            "under-reported until the next UTC day rollover.",
                            today_remaining, now.hour,
                        )
                    self._daily_forecast_date = today_str
                    self._daily_forecast_kwh = today_remaining
                    self._daily_forecast_peak_kw = today_peak
                    today_forecast = today_remaining
                    _LOGGER.info(
                        f"Solcast: New day, cached forecast for {today_str}: {today_remaining:.1f}kWh"
                    )
                else:
                    # Use cached value (from earlier fetch today or restored
                    # full-day cache from persistent storage). Never downgrade
                    # the cached full-day total to the current remaining — it's
                    # always an under-estimate after mid-morning.
                    today_forecast = self._daily_forecast_kwh or today_remaining
                    today_peak = self._daily_forecast_peak_kw or today_peak
                    _LOGGER.info(
                        f"Solcast forecast updated: Today={today_forecast:.1f}kWh (cached), "
                        f"remaining={today_remaining:.1f}kWh, Tomorrow={tomorrow_total:.1f}kWh"
                    )

            result = {
                "available": True,
                "today_forecast_kwh": round(today_forecast, 2),  # Full day (actuals + forecast)
                "today_remaining_kwh": round(today_remaining, 2),  # Remaining from now
                "today_total_kwh": round(today_forecast, 2),  # Alias for backward compat
                "tomorrow_total_kwh": round(tomorrow_total, 2),
                "today_peak_kw": round(today_peak, 2),
                "tomorrow_peak_kw": round(tomorrow_peak, 2),
                "current_estimate_kw": round(current_estimate, 2) if current_estimate else None,
                "forecast_periods": len(forecasts),
                "forecasts": forecasts,  # Raw forecast periods for optimizer
                "estimate_type": self._estimate_type,
                "last_update": dt_util.utcnow(),
                "source": "api",
            }
            # Persist good forecast data so it survives restarts
            self.hass.async_create_task(self._save_forecast_cache(result))
            return result

        except asyncio.TimeoutError as err:
            raise UpdateFailed("Timeout fetching Solcast forecast") from err
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Error fetching Solcast forecast: {err}") from err
        except Exception as err:
            raise UpdateFailed(f"Unexpected error fetching Solcast forecast: {err}") from err


class OctopusPriceCoordinator(DataUpdateCoordinator):
    """Coordinator to fetch Octopus Energy UK price data.

    Fetches half-hourly import and export rates from the Octopus Energy API.
    Converts to Amber-compatible format for use with existing tariff conversion.

    Key differences from Amber:
    - Prices in pence/kWh (not cents)
    - Prices include VAT (5%)
    - 30-minute intervals
    - Prices published daily after 4pm UK time for next day
    - Can go negative (you get paid to use electricity)
    - Price cap at 100p/kWh
    """

    def __init__(
        self,
        hass: HomeAssistant,
        product_code: str,
        tariff_code: str,
        gsp_region: str,
        export_product_code: str | None = None,
        export_tariff_code: str | None = None,
    ) -> None:
        """Initialize the coordinator.

        Args:
            hass: HomeAssistant instance
            product_code: Octopus product code (e.g., "AGILE-24-10-01")
            tariff_code: Full tariff code including region (e.g., "E-1R-AGILE-24-10-01-A")
            gsp_region: UK Grid Supply Point region code (e.g., "A")
            export_product_code: Optional export product code for Agile Outgoing/Flux
            export_tariff_code: Optional export tariff code
        """
        from ..octopus_api import OctopusAPIClient

        self.product_code = product_code
        self.tariff_code = tariff_code
        self.gsp_region = gsp_region
        self.export_product_code = export_product_code
        self.export_tariff_code = export_tariff_code
        self.session = async_get_clientsession(hass)
        self._client = OctopusAPIClient(self.session)

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_octopus_prices",
            update_interval=timedelta(minutes=30),  # Octopus updates less frequently than Amber
        )

    @staticmethod
    def _expand_to_half_hourly(rates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Expand block rates into individual 30-minute entries.

        Agile rates (already 30-min) pass through unchanged. Go rates (2 blocks/day)
        and Tracker rates (1 block/day) are split into 30-min chunks so the LP
        optimizer sees 48 price points instead of 1-2.

        Args:
            rates: List of rate dicts with valid_from, valid_to, and price fields

        Returns:
            List of rate dicts, each covering exactly 30 minutes
        """
        expanded: list[dict[str, Any]] = []

        for rate in rates:
            valid_from_str = rate.get("valid_from", "")
            valid_to_str = rate.get("valid_to", "")

            if not valid_from_str or not valid_to_str:
                expanded.append(rate)
                continue

            try:
                vf = datetime.fromisoformat(valid_from_str.replace("Z", "+00:00"))
                vt = datetime.fromisoformat(valid_to_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                expanded.append(rate)
                continue

            duration = vt - vf
            if duration <= timedelta(minutes=30):
                # Already 30-min or shorter — pass through
                expanded.append(rate)
                continue

            # Split into 30-min chunks
            chunk_start = vf
            while chunk_start < vt:
                chunk_end = min(chunk_start + timedelta(minutes=30), vt)
                chunk = dict(rate)
                chunk["valid_from"] = chunk_start.isoformat()
                chunk["valid_to"] = chunk_end.isoformat()
                expanded.append(chunk)
                chunk_start = chunk_end

        return expanded

    def _read_from_octopus_energy_integration(self) -> dict[str, Any] | None:
        """Try to read rates from the BottlecapDave/HomeAssistant-OctopusEnergy integration.

        When the octopus_energy integration is installed, read import and export rates
        directly from its coordinators instead of making our own API calls.

        Returns Amber-compatible format dict, or None if integration not available.
        """
        from datetime import timezone

        oe_data = self.hass.data.get("octopus_energy")
        if not oe_data or not isinstance(oe_data, dict):
            return self._read_from_octopus_energy_entities()

        now = datetime.now(timezone.utc)
        import_rates_raw: list[dict] = []
        export_rates_raw: list[dict] = []
        import_tariff = None
        export_tariff = None

        for account_id, account_data in oe_data.items():
            if not isinstance(account_data, dict):
                continue

            # Get account info to find meter points
            account_result = account_data.get("ACCOUNT")
            if not account_result:
                continue

            account_info = getattr(account_result, "account", None)
            if not account_info or not isinstance(account_info, dict):
                continue

            # Iterate electricity meter points
            meter_points = account_info.get("electricity_meter_points", [])
            for mp in meter_points:
                if not isinstance(mp, dict):
                    continue

                mpan = mp.get("mpan", "")
                meters = mp.get("meters", [])
                if not meters:
                    continue

                serial = meters[0].get("serial_number", "") if isinstance(meters[0], dict) else ""
                is_export = meters[0].get("is_export", False) if isinstance(meters[0], dict) else False

                # Get rates from coordinator
                rates_key = f"ELECTRICITY_RATES_{mpan}_{serial}"
                rates_result = account_data.get(rates_key)
                if not rates_result:
                    continue

                rates = getattr(rates_result, "rates", None) or getattr(rates_result, "original_rates", None)
                if not rates or not isinstance(rates, list):
                    continue

                # Get tariff code from active agreement
                agreements = mp.get("agreements", [])
                tariff_code = None
                for agreement in agreements:
                    if isinstance(agreement, dict):
                        tariff_code = agreement.get("tariff_code")
                        if tariff_code:
                            break

                if is_export:
                    export_rates_raw = rates
                    export_tariff = tariff_code
                else:
                    import_rates_raw = rates
                    import_tariff = tariff_code

        if not import_rates_raw:
            return self._read_from_octopus_energy_entities()

        # Promote BottlecapDave's active tariff/product code so callers (e.g.
        # the LP optimizer's AGILE/FLUX dynamic-pricing gate) see the live
        # tariff rather than whatever was set in the config flow.
        if import_tariff:
            self.tariff_code = import_tariff
            # Tariff code format: E-1R-AGILE-24-10-01-A (region letter trailing).
            # Derive product_code by stripping the leading E-{1R|2R}- prefix and
            # the trailing -A region letter, keeping the middle segment.
            try:
                parts = import_tariff.split("-")
                if len(parts) >= 5 and parts[0] == "E":
                    self.product_code = "-".join(parts[2:-1])
            except Exception:
                pass

        # Convert octopus_energy rate format to our Amber-compatible format
        current_prices: list[dict] = []
        forecast_prices: list[dict] = []
        export_forecast: list[dict] = []

        for rate in import_rates_raw:
            start = rate.get("start") or rate.get("valid_from")
            end = rate.get("end") or rate.get("valid_to")
            price_pence = rate.get("value_inc_vat", 0)

            if not start or not end:
                continue

            # Normalize to datetime objects
            if isinstance(start, str):
                start = datetime.fromisoformat(start.replace("Z", "+00:00"))
            if isinstance(end, str):
                end = datetime.fromisoformat(end.replace("Z", "+00:00"))

            # Duration in minutes — BottlecapDave usually emits 30-min slots,
            # but block tariffs (Go/Cosy off-peak windows) can come through
            # as wider intervals. Compute from timestamps so downstream LP
            # expansion sees the correct slot count.
            duration_min = max(1, int((end - start).total_seconds() // 60))

            if start <= now < end:
                interval_type = "CurrentInterval"
            elif end <= now:
                interval_type = "ActualInterval"
            else:
                interval_type = "ForecastInterval"

            amber_entry = {
                "nemTime": end.isoformat(),
                "perKwh": price_pence,  # pence/kWh maps to cents
                "channelType": "general",
                "type": interval_type,
                "duration": duration_min,
                "valid_from": start.isoformat(),
                "valid_to": end.isoformat(),
            }

            if interval_type == "CurrentInterval":
                current_prices.append(amber_entry)
            forecast_prices.append(amber_entry)

        for rate in export_rates_raw:
            start = rate.get("start") or rate.get("valid_from")
            end = rate.get("end") or rate.get("valid_to")
            price_pence = rate.get("value_inc_vat", 0)

            if not start or not end:
                continue

            if isinstance(start, str):
                start = datetime.fromisoformat(start.replace("Z", "+00:00"))
            if isinstance(end, str):
                end = datetime.fromisoformat(end.replace("Z", "+00:00"))

            duration_min = max(1, int((end - start).total_seconds() // 60))

            if start <= now < end:
                interval_type = "CurrentInterval"
            elif end <= now:
                interval_type = "ActualInterval"
            else:
                interval_type = "ForecastInterval"

            amber_entry = {
                "nemTime": end.isoformat(),
                "perKwh": -price_pence,  # Negative = you get paid (Amber convention)
                "channelType": "feedIn",
                "type": interval_type,
                "duration": duration_min,
                "valid_from": start.isoformat(),
                "valid_to": end.isoformat(),
            }

            if interval_type == "CurrentInterval":
                current_prices.append(amber_entry)
            export_forecast.append(amber_entry)

        if not export_forecast:
            default_export_pence = 4.1
            for price in forecast_prices:
                amber_entry = dict(price)
                amber_entry["perKwh"] = -default_export_pence
                amber_entry["channelType"] = "feedIn"

                if amber_entry.get("type") == "CurrentInterval":
                    current_prices.append(amber_entry)
                export_forecast.append(amber_entry)
            export_tariff = export_tariff or "synthetic_seg"

        combined_forecast = forecast_prices + export_forecast

        current_import = next(
            (p["perKwh"] for p in current_prices if p["channelType"] == "general"),
            None,
        )
        current_export = next(
            (p["perKwh"] for p in current_prices if p["channelType"] == "feedIn"),
            None,
        )

        _LOGGER.info(
            "🐙 Using octopus_energy integration data: "
            "current_import=%.2fp/kWh, current_export=%.2fp/kWh, "
            "periods=%d (import=%d, export=%d), "
            "import_tariff=%s, export_tariff=%s",
            current_import or 0,
            -(current_export or 0),
            len(combined_forecast),
            len(forecast_prices),
            len(export_forecast),
            import_tariff or "unknown",
            export_tariff or "none",
        )

        if not current_prices:
            entity_data = self._read_from_octopus_energy_entities()
            if entity_data:
                return entity_data

        return {
            "current": current_prices,
            "forecast": combined_forecast,
            "export_rates": export_forecast,
            "last_update": dt_util.utcnow(),
            "source": "octopus_energy_integration",
            "product_code": self.product_code,
            "tariff_code": import_tariff or self.tariff_code,
            "gsp_region": self.gsp_region,
        }

    @staticmethod
    def _parse_octopus_datetime(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            dt_value = value
        elif isinstance(value, str) and value:
            try:
                dt_value = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
        else:
            return None
        if dt_value.tzinfo is None:
            from datetime import timezone
            dt_value = dt_value.replace(tzinfo=timezone.utc)
        return dt_value

    @staticmethod
    def _octopus_rate_to_pence(value: Any) -> float | None:
        """Normalize BottlecapDave public entity GBP rates or internal pence rates."""
        try:
            rate = float(value)
        except (TypeError, ValueError):
            return None
        # Public current_rate entities are GBP/kWh (e.g. 0.245), while internal
        # coordinator/API rates are p/kWh (e.g. 24.5).
        return round(rate * 100 if abs(rate) <= 2 else rate, 6)

    def _octopus_state_entries(self, domain: str) -> list[Any]:
        states = getattr(self.hass, "states", None)
        if states is None:
            return []
        if hasattr(states, "async_all"):
            return list(states.async_all(domain))
        if isinstance(states, dict):
            return [
                state for entity_id, state in states.items()
                if str(entity_id).split(".", 1)[0] == domain
            ]
        return []

    def _build_octopus_amber_entry(
        self,
        start: Any,
        end: Any,
        rate_value: Any,
        channel: str,
        now: datetime,
    ) -> dict[str, Any] | None:
        start_dt = self._parse_octopus_datetime(start)
        end_dt = self._parse_octopus_datetime(end)
        rate_pence = self._octopus_rate_to_pence(rate_value)
        if start_dt is None or end_dt is None or rate_pence is None:
            return None

        if start_dt <= now < end_dt:
            interval_type = "CurrentInterval"
        elif end_dt <= now:
            interval_type = "ActualInterval"
        else:
            interval_type = "ForecastInterval"

        return {
            "nemTime": end_dt.isoformat(),
            "perKwh": -rate_pence if channel == "feedIn" else rate_pence,
            "channelType": channel,
            "type": interval_type,
            "duration": max(1, int((end_dt - start_dt).total_seconds() // 60)),
            "valid_from": start_dt.isoformat(),
            "valid_to": end_dt.isoformat(),
        }

    def _read_from_octopus_energy_entities(self) -> dict[str, Any] | None:
        """Read BottlecapDave's documented public entities as a compatibility fallback."""
        from datetime import timezone

        now = datetime.now(timezone.utc)
        current_prices: list[dict[str, Any]] = []
        import_forecast: list[dict[str, Any]] = []
        export_forecast: list[dict[str, Any]] = []
        import_tariff = None
        export_tariff = None

        for state in self._octopus_state_entries("sensor"):
            entity_id = getattr(state, "entity_id", "")
            if (
                not entity_id.startswith("sensor.octopus_energy_electricity_")
                or not entity_id.endswith("_current_rate")
            ):
                continue
            if getattr(state, "state", None) in (None, "unknown", "unavailable", ""):
                continue

            attrs = getattr(state, "attributes", None) or {}
            is_export = bool(attrs.get("is_export")) or "_export_" in entity_id
            channel = "feedIn" if is_export else "general"
            entry = self._build_octopus_amber_entry(
                attrs.get("start"),
                attrs.get("end"),
                getattr(state, "state", None),
                channel,
                now,
            )
            if not entry:
                continue
            if entry["type"] == "CurrentInterval":
                current_prices.append(entry)
            if channel == "feedIn":
                export_forecast.append(entry)
                export_tariff = attrs.get("tariff") or export_tariff
            else:
                import_forecast.append(entry)
                import_tariff = attrs.get("tariff") or import_tariff

        for state in self._octopus_state_entries("event"):
            entity_id = getattr(state, "entity_id", "")
            if (
                not entity_id.startswith("event.octopus_energy_electricity_")
                or not (
                    entity_id.endswith("_current_day_rates")
                    or entity_id.endswith("_next_day_rates")
                )
            ):
                continue

            attrs = getattr(state, "attributes", None) or {}
            rates = attrs.get("rates")
            if not isinstance(rates, list):
                continue
            is_export = bool(attrs.get("is_export")) or "_export_" in entity_id
            channel = "feedIn" if is_export else "general"
            for rate in rates:
                if not isinstance(rate, dict):
                    continue
                entry = self._build_octopus_amber_entry(
                    rate.get("start"),
                    rate.get("end"),
                    rate.get("value_inc_vat"),
                    channel,
                    now,
                )
                if not entry:
                    continue
                if entry["type"] == "CurrentInterval":
                    current_prices.append(entry)
                if channel == "feedIn":
                    export_forecast.append(entry)
                    export_tariff = attrs.get("tariff_code") or export_tariff
                else:
                    import_forecast.append(entry)
                    import_tariff = attrs.get("tariff_code") or import_tariff

        if not import_forecast and not any(
            price.get("channelType") == "general" for price in current_prices
        ):
            return None

        if not import_forecast:
            import_forecast = [
                price for price in current_prices
                if price.get("channelType") == "general"
            ]
        if not export_forecast:
            for price in import_forecast:
                entry = dict(price)
                entry["perKwh"] = -4.1
                entry["channelType"] = "feedIn"
                if entry.get("type") == "CurrentInterval":
                    current_prices.append(entry)
                export_forecast.append(entry)
            export_tariff = export_tariff or "synthetic_seg"

        if not any(price.get("channelType") == "feedIn" for price in current_prices):
            current_export = next(
                (price for price in export_forecast if price.get("type") == "CurrentInterval"),
                None,
            )
            if current_export:
                current_prices.append(current_export)

        combined_forecast = import_forecast + export_forecast
        if not current_prices:
            return None

        _LOGGER.info(
            "🐙 Using octopus_energy public entity data: periods=%d (import=%d, export=%d), "
            "import_tariff=%s, export_tariff=%s",
            len(combined_forecast),
            len(import_forecast),
            len(export_forecast),
            import_tariff or "unknown",
            export_tariff or "none",
        )

        return {
            "current": current_prices,
            "forecast": combined_forecast,
            "export_rates": export_forecast,
            "last_update": dt_util.utcnow(),
            "source": "octopus_energy_entities",
            "product_code": self.product_code,
            "tariff_code": import_tariff or self.tariff_code,
            "gsp_region": self.gsp_region,
        }

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from Octopus Energy integration or API, in Amber-compatible format.

        Prefers the octopus_energy integration (BottlecapDave) when installed
        to avoid double API calls and get the correct export tariff automatically.

        Returns:
            dict with 'current', 'forecast', 'export_rates', and 'last_update'
            in Amber-compatible format for use with tariff conversion.
        """
        try:
            # Try reading from octopus_energy integration first
            integration_data = self._read_from_octopus_energy_integration()
            if integration_data:
                return integration_data

            from datetime import timezone

            now = datetime.now(timezone.utc)

            # Fetch import rates for next 48 hours
            period_from = now - timedelta(hours=1)  # Include recent past
            period_to = now + timedelta(hours=48)

            import_rates = await self._client.get_current_rates(
                self.product_code,
                self.tariff_code,
                period_from=period_from,
                period_to=period_to,
                page_size=200,  # 48 hours = 96 periods, add buffer
            )

            # Expand block rates (Go/Tracker) into half-hourly entries
            import_rates = self._expand_to_half_hourly(import_rates)

            if not import_rates:
                raise UpdateFailed(
                    f"No import rates returned from Octopus API for {self.tariff_code}"
                )

            # Fetch export rates if configured
            export_rates = []
            if self.export_product_code and self.export_tariff_code:
                export_rates = await self._client.get_export_rates(
                    self.export_product_code,
                    self.export_tariff_code,
                    period_from=period_from,
                    period_to=period_to,
                    page_size=200,
                )
                export_rates = self._expand_to_half_hourly(export_rates)

            # Convert to Amber-compatible format
            current_prices = []
            forecast_prices = []

            for rate in import_rates:
                valid_from_str = rate.get("valid_from", "")
                valid_to_str = rate.get("valid_to", "")
                price_pence = rate.get("value_inc_vat", 0)

                if not valid_from_str or not valid_to_str:
                    continue

                # Parse timestamps
                try:
                    valid_from = datetime.fromisoformat(valid_from_str.replace("Z", "+00:00"))
                    valid_to = datetime.fromisoformat(valid_to_str.replace("Z", "+00:00"))
                except ValueError:
                    continue

                # Determine interval type based on timing
                # Octopus uses valid_to as the interval end time (same convention as Amber's nemTime)
                if valid_from <= now < valid_to:
                    interval_type = "CurrentInterval"
                elif valid_to <= now:
                    interval_type = "ActualInterval"
                else:
                    interval_type = "ForecastInterval"

                # Build Amber-compatible price entry
                # Note: price_pence is in pence/kWh, which maps directly to cents for Tesla
                # (Tesla doesn't care about currency, just the numeric value)
                amber_entry = {
                    "nemTime": valid_to.isoformat(),  # Amber uses interval END time
                    "perKwh": price_pence,  # pence/kWh (treated as cents)
                    "channelType": "general",
                    "type": interval_type,
                    "duration": 30,  # 30-minute intervals
                    "valid_from": valid_from.isoformat(),
                    "valid_to": valid_to.isoformat(),
                }

                if interval_type == "CurrentInterval":
                    current_prices.append(amber_entry)
                forecast_prices.append(amber_entry)

            # Process export rates if available
            export_forecast = []
            for rate in export_rates:
                valid_from_str = rate.get("valid_from", "")
                valid_to_str = rate.get("valid_to", "")
                price_pence = rate.get("value_inc_vat", 0)

                if not valid_from_str or not valid_to_str:
                    continue

                try:
                    valid_from = datetime.fromisoformat(valid_from_str.replace("Z", "+00:00"))
                    valid_to = datetime.fromisoformat(valid_to_str.replace("Z", "+00:00"))
                except ValueError:
                    continue

                if valid_from <= now < valid_to:
                    interval_type = "CurrentInterval"
                elif valid_to <= now:
                    interval_type = "ActualInterval"
                else:
                    interval_type = "ForecastInterval"

                # Export prices: Amber uses negative for "you get paid"
                # Octopus export rates are positive (payment to you)
                # Convert to Amber convention: negative = payment to you
                amber_entry = {
                    "nemTime": valid_to.isoformat(),
                    "perKwh": -price_pence,  # Negative = you get paid
                    "channelType": "feedIn",
                    "type": interval_type,
                    "duration": 30,
                    "valid_from": valid_from.isoformat(),
                    "valid_to": valid_to.isoformat(),
                }

                if interval_type == "CurrentInterval":
                    current_prices.append(amber_entry)
                export_forecast.append(amber_entry)

            # If no export rates configured, create synthetic export prices
            # (typically 0 for non-export tariffs, or use SEG rates)
            if not export_rates:
                for rate in import_rates:
                    valid_from_str = rate.get("valid_from", "")
                    valid_to_str = rate.get("valid_to", "")

                    if not valid_from_str or not valid_to_str:
                        continue

                    try:
                        valid_from = datetime.fromisoformat(valid_from_str.replace("Z", "+00:00"))
                        valid_to = datetime.fromisoformat(valid_to_str.replace("Z", "+00:00"))
                    except ValueError:
                        continue

                    if valid_from <= now < valid_to:
                        interval_type = "CurrentInterval"
                    elif valid_to <= now:
                        interval_type = "ActualInterval"
                    else:
                        interval_type = "ForecastInterval"

                    # Default export rate: Smart Export Guarantee minimum (typically 4.1p)
                    # or 0 if tariff doesn't support export
                    default_export_pence = 4.1  # SEG minimum

                    amber_entry = {
                        "nemTime": valid_to.isoformat(),
                        "perKwh": -default_export_pence,  # Negative = you get paid
                        "channelType": "feedIn",
                        "type": interval_type,
                        "duration": 30,
                        "valid_from": valid_from.isoformat(),
                        "valid_to": valid_to.isoformat(),
                    }

                    if interval_type == "CurrentInterval":
                        current_prices.append(amber_entry)
                    export_forecast.append(amber_entry)

            # Combine import and export forecasts
            combined_forecast = forecast_prices + export_forecast

            # Log summary
            current_import = next(
                (p["perKwh"] for p in current_prices if p["channelType"] == "general"),
                None,
            )
            current_export = next(
                (p["perKwh"] for p in current_prices if p["channelType"] == "feedIn"),
                None,
            )

            _LOGGER.info(
                "Octopus API data for %s: current_import=%.2fp/kWh, current_export=%.2fp/kWh, "
                "forecast_periods=%d (import=%d, export=%d)",
                self.tariff_code,
                current_import or 0,
                -(current_export or 0),  # Un-negate for display
                len(combined_forecast),
                len(forecast_prices),
                len(export_forecast),
            )

            return {
                "current": current_prices,
                "forecast": combined_forecast,
                "export_rates": export_forecast,
                "last_update": dt_util.utcnow(),
                "source": "octopus_api",
                "product_code": self.product_code,
                "tariff_code": self.tariff_code,
                "gsp_region": self.gsp_region,
            }

        except UpdateFailed:
            raise
        except Exception as err:
            raise UpdateFailed(f"Error fetching Octopus data: {err}") from err


class OctopusSavingSessionCoordinator(DataUpdateCoordinator):
    """Coordinator that polls for Octopus Saving Sessions.

    Supports two data sources:
    - Direct API: Uses OctopusSavingSessionsClient with GraphQL
    - Entity: Reads from Bottlecap Dave's Octopus integration event entity

    Polls every 15 minutes. Optionally auto-joins available sessions.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client=None,
        entity_id: str | None = None,
        auto_join: bool = False,
        octopoints_per_penny: int = 8,
    ) -> None:
        """Initialize the coordinator.

        Args:
            hass: HomeAssistant instance
            client: OctopusSavingSessionsClient (direct mode) or None
            entity_id: Bottlecap Dave event entity ID (entity mode) or None
            auto_join: Auto-join available sessions (direct API or Dave's integration)
            octopoints_per_penny: Conversion rate (default 8)
        """
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_octopus_saving_sessions",
            update_interval=timedelta(minutes=15),
        )
        self._client = client
        self._entity_id = entity_id
        self._auto_join = auto_join
        self._octopoints_per_penny = octopoints_per_penny

    async def _async_update_data(self) -> dict:
        """Fetch sessions from direct API or Bottlecap Dave entity."""
        from ..octopus_sessions import (
            SavingSession,
            saving_session_from_octopus_energy_event,
        )

        sessions: list[SavingSession] = []

        if self._client:
            # Direct API mode
            try:
                raw = await self._client.get_sessions()
                if self._auto_join:
                    for s in raw:
                        if not s.joined and s.session_type == "saving":
                            joined = await self._client.join_session(s.code)
                            if joined:
                                s.joined = True
                                _LOGGER.info(
                                    "Auto-joined saving session: %s (%s - %s)",
                                    s.code, s.start, s.end,
                                )
                sessions = raw
            except Exception as err:
                _LOGGER.error("Error fetching saving sessions from API: %s", err)

        elif self._entity_id:
            # Bottlecap Dave entity mode — reads from octopus_energy event entity
            state = self.hass.states.get(self._entity_id)
            if state:
                sessions_by_key: dict[tuple[datetime, datetime], SavingSession] = {}

                def add_session(session: SavingSession | None) -> None:
                    if session is None:
                        return
                    sessions_by_key[(session.start, session.end)] = session

                # Auto-join available sessions via Dave's service
                if self._auto_join:
                    available = state.attributes.get("available_events", [])
                    for ev in available:
                        try:
                            code = ev.get("code", "")
                            if not code:
                                continue
                            _LOGGER.info(
                                "🐙 Auto-joining saving session via octopus_energy: %s "
                                "(octopoints=%s/kWh)",
                                code, ev.get("octopoints_per_kwh", "?"),
                            )
                            await self.hass.services.async_call(
                                "octopus_energy",
                                "join_octoplus_saving_session_event",
                                {"event_code": code},
                                target={"entity_id": self._entity_id},
                                blocking=True,
                            )
                            _LOGGER.info(
                                "✅ Joined saving session %s via octopus_energy", code,
                            )
                            # Dave's integration schedules a refresh after joining, so
                            # expose the successfully joined event immediately for the
                            # next optimiser run instead of waiting for a later poll.
                            add_session(
                                saving_session_from_octopus_energy_event(
                                    ev,
                                    joined=True,
                                )
                            )
                        except Exception as err:
                            _LOGGER.error(
                                "Failed to auto-join saving session %s: %s", code, err,
                            )

                # Parse joined_events from entity attributes
                for ev in state.attributes.get("joined_events", []):
                    session = saving_session_from_octopus_energy_event(
                        ev,
                        joined=True,
                    )
                    if session is None:
                        _LOGGER.debug("Skipping malformed entity event: %s", ev)
                        continue
                    add_session(session)

                sessions = sorted(sessions_by_key.values(), key=lambda s: s.start)
            else:
                _LOGGER.debug(
                    "Saving sessions entity %s not available", self._entity_id
                )

        sessions = sorted(sessions, key=lambda s: s.start)
        now = dt_util.utcnow()
        if getattr(now, "tzinfo", None) is None:
            now = now.replace(tzinfo=dt_util.UTC)
        else:
            now = now.astimezone(dt_util.UTC)
        return {
            "sessions": sessions,
            "active_session": next(
                (s for s in sessions if s.is_active() and s.joined), None
            ),
            "next_session": next(
                (s for s in sessions if s.start > now and s.joined), None
            ),
        }


class FlowPowerTWAPTracker:
    """Tracks wholesale prices and calculates rolling 30-day TWAP.

    The TWAP (Time Weighted Average Price) replaces the hardcoded 8.0 c/kWh
    market average in the PEA formula with an actual rolling 30-day average.

    Formula: PEA = wholesale - TWAP - 1.7 (benchmark)
    Fallback: PEA = wholesale - 8.0 - 1.7 when < 12 samples available
    """

    def __init__(
        self,
        hass: HomeAssistant,
        region: str,
        entry_id: str,
        billing_day: int = 1,
    ) -> None:
        self.hass = hass
        self.region = region
        # Day-of-month the billing period resets on. Clamped to 1-28 so the
        # anchor is valid in every month (short Februaries included).
        self.billing_day = max(1, min(int(billing_day or 1), 28))
        self._price_history: list[dict] = []
        self._store = Store(hass, 1, f"power_sync.flow_power_twap.{entry_id}")
        self._last_store_save: float | None = None
        self._twap: float | None = None
        self._loaded = False

    async def async_load(self) -> None:
        """Load price history from persistent storage."""
        stored = await self._store.async_load()
        if stored and isinstance(stored.get("price_history"), list):
            self._price_history = stored["price_history"]
            self._prune_history()
            self._twap = self._calculate_twap()
            _LOGGER.info(
                "Loaded TWAP history: %d samples over %.1f days, TWAP=%.2f c/kWh%s "
                "(billing-anchored day %d: mtd=%s, trailing=%s, progress=%.0f%%)",
                len(self._price_history),
                self.twap_days,
                self._twap if self._twap is not None else FLOW_POWER_MARKET_AVG,
                " (fallback)" if self.using_fallback else "",
                self.billing_day,
                f"{self.mtd_twap:.2f}" if self.mtd_twap is not None else "n/a",
                f"{self.trailing_twap:.2f}" if self.trailing_twap is not None else "n/a",
                self.period_progress * 100,
            )
        self._loaded = True

    def record_price(self, wholesale_cents: float) -> None:
        """Record a wholesale price sample with 4-minute deduplication."""
        now = time.time()
        if self._price_history:
            if now - self._price_history[-1]["ts"] < 240:
                return
        self._price_history.append({"ts": round(now), "price": round(wholesale_cents, 2)})
        self._prune_history()
        self._twap = self._calculate_twap()
        # Save periodically (every 10 minutes)
        if self._last_store_save is None or now - self._last_store_save > 600:
            self.hass.async_create_task(self._async_save())
            self._last_store_save = now

    def _billing_period_start_ts(self, now_ts: float | None = None) -> float:
        """Epoch of the current billing period's local-midnight start.

        The period starts on ``billing_day`` each month; if we're earlier in the
        month than that day, the current period began last month.
        """
        now = (
            dt_util.now()
            if now_ts is None
            else dt_util.as_local(dt_util.utc_from_timestamp(now_ts))
        )
        day = min(self.billing_day, 28)
        if now.day >= day:
            start = now.replace(day=day, hour=0, minute=0, second=0, microsecond=0)
        else:
            first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            prev_month_last = first - timedelta(days=1)
            start = prev_month_last.replace(
                day=day, hour=0, minute=0, second=0, microsecond=0
            )
        return start.timestamp()

    def _mean_since(self, cutoff_ts: float) -> tuple[float | None, int]:
        """Mean price (c/kWh) and sample count for samples at/after ``cutoff_ts``."""
        prices = [e["price"] for e in self._price_history if e["ts"] >= cutoff_ts]
        if not prices:
            return None, 0
        return round(sum(prices) / len(prices), 2), len(prices)

    def _prune_history(self) -> None:
        """Drop samples we no longer need.

        Retain the whole billing-period-to-date plus a trailing window (the
        forward proxy for the remainder of the period), capped to bound storage.
        """
        now_ts = time.time()
        period_elapsed_days = (now_ts - self._billing_period_start_ts(now_ts)) / 86400
        retain_days = min(max(DEFAULT_TWAP_WINDOW_DAYS, period_elapsed_days) + 5, 45)
        cutoff = now_ts - retain_days * 86400
        self._price_history = [
            entry for entry in self._price_history if entry["ts"] > cutoff
        ]

    def _calculate_twap(self) -> float | None:
        """Billing-period-anchored TWAP reference.

        Flow Power settles PEA against the time-weighted average price over the
        billing period, not a flat trailing window. We blend billing-period-to-
        date actuals with the trailing-window mean (a zero-dependency proxy for
        the remainder of the period) weighted by how far through the period we
        are. Early in the period this ~= the old trailing-30d behaviour (no
        regression, stable); near period end it converges to the actual
        billing-period TWAP the customer is billed on. Returns None when there is
        not yet enough data to be meaningful.

        (Future: swap the trailing-mean forward proxy for the live KWatch/
        predispatch forecast mean over the remaining period.)
        """
        if len(self._price_history) < MIN_TWAP_SAMPLES:
            return None
        now_ts = time.time()
        period_start = self._billing_period_start_ts(now_ts)
        trailing_mean, _ = self._mean_since(0.0)
        if trailing_mean is None:
            return None
        mtd_mean, mtd_n = self._mean_since(period_start)
        if mtd_mean is None or mtd_n < MIN_TWAP_SAMPLES:
            # Too little billing-period data yet — lean on the trailing mean.
            return trailing_mean
        # Progress through the period, normalised to a nominal 30-day cycle.
        elapsed = max(now_ts - period_start, 0.0)
        w = min(elapsed / (DEFAULT_TWAP_WINDOW_DAYS * 86400), 1.0)
        return round(w * mtd_mean + (1.0 - w) * trailing_mean, 2)

    async def _async_save(self) -> None:
        """Save price history to persistent storage."""
        try:
            await self._store.async_save({
                "price_history": self._price_history,
                "region": self.region,
            })
        except Exception as err:
            _LOGGER.warning("Failed to save TWAP history: %s", err)

    async def async_save(self) -> None:
        """Public save for use on unload."""
        await self._async_save()

    @property
    def twap(self) -> float | None:
        """Return the current TWAP value, or None if insufficient data."""
        return self._twap

    @property
    def twap_days(self) -> float:
        """Return how many days of price data we have."""
        if not self._price_history:
            return 0.0
        oldest = self._price_history[0]["ts"]
        return round((time.time() - oldest) / 86400, 1)

    @property
    def sample_count(self) -> int:
        """Return the number of price samples."""
        return len(self._price_history)

    @property
    def using_fallback(self) -> bool:
        """Return True if we're using the hardcoded fallback instead of dynamic TWAP."""
        return self._twap is None

    @property
    def mtd_twap(self) -> float | None:
        """Billing-period-to-date time-weighted average price (c/kWh)."""
        mean, _ = self._mean_since(self._billing_period_start_ts())
        return mean

    @property
    def trailing_twap(self) -> float | None:
        """Trailing-window mean price (c/kWh) — the forward proxy / fallback."""
        mean, _ = self._mean_since(0.0)
        return mean

    @property
    def period_progress(self) -> float:
        """Fraction (0-1) through the current billing period, 30-day nominal."""
        elapsed = max(time.time() - self._billing_period_start_ts(), 0.0)
        return round(min(elapsed / (DEFAULT_TWAP_WINDOW_DAYS * 86400), 1.0), 3)
