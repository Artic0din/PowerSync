"""Energy-brand data update coordinators for PowerSync."""
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
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from ..const import (
    DOMAIN,
    UPDATE_INTERVAL_ENERGY,
    TESLEMETRY_API_BASE_URL,
    FLEET_API_BASE_URL,
    POWERSYNC_API_BASE_URL,
    TESLA_PROVIDER_TESLEMETRY,
    TESLA_PROVIDER_FLEET_API,
    TESLA_PROVIDER_POWERSYNC,
    POWER_SYNC_USER_AGENT,
    CONF_FLEET_API_BASE_URL,
    TESLA_SITE_INFO_CACHE_TTL_SECONDS,
    CONF_SIGENERGY_CHARGER_ENABLED,
    CONF_SIGENERGY_CHARGER_TYPE,
    SIGENERGY_CHARGER_EVAC,
    SIGENERGY_CHARGER_EVDC,
)
from ..sigenergy_model import sigenergy_home_load_kw
from ..pricing._shared import (
    SensitiveDataFilter,
    _parse_retry_after,
    _fetch_with_retry,
    _get_current_prices,
)

_LOGGER = logging.getLogger(__name__)
_LOGGER.addFilter(SensitiveDataFilter())

ENERGY_ACC_STORE_VERSION = 1
ENERGY_ACC_SAVE_DELAY = 300  # Flush at most every 5 minutes
SOLAREDGE_DAILY_TOTALS_STORE_VERSION = 1
LIFETIME_TOTALS_STORE_VERSION = 1
TESLA_OUTAGE_NOTIFY_FAILURES = 5
TESLA_OUTAGE_NOTIFY_MIN_SECONDS = 300
LIFETIME_TOTAL_KEYS = (
    "lifetime_solar_kwh",
    "lifetime_grid_import_kwh",
    "lifetime_grid_export_kwh",
    "lifetime_battery_charged_kwh",
    "lifetime_battery_discharged_kwh",
    "lifetime_home_kwh",
)


def _configured_ac_inverter_power_kw(hass: HomeAssistant, entry_id: str) -> float:
    """Return the latest separately configured AC inverter output in kW."""
    attrs = (
        hass.data.get(DOMAIN, {})
        .get(entry_id, {})
        .get("inverter_attributes")
        or {}
    )
    power_w = attrs.get("power_output_w")
    if power_w is None:
        power_w = attrs.get("dc_power")
    try:
        return max(0.0, float(power_w or 0) / 1000.0)
    except (TypeError, ValueError):
        return 0.0


def _is_night_for_solar_telemetry(hass: HomeAssistant) -> bool:
    """Return whether real solar telemetry should be impossible or near-zero."""
    try:
        sun_state = getattr(hass, "states", None).get("sun.sun")
        if sun_state is not None:
            if sun_state.state == "below_horizon":
                return True
            if sun_state.state == "above_horizon":
                return False
    except Exception:
        pass

    local_hour = dt_util.now().hour
    return local_hour >= 18 or local_hour < 6


def _stored_battery_health_capacity_kwh(hass: HomeAssistant, entry_id: str) -> float | None:
    """Return the latest BMS-scanned current Powerwall capacity in kWh."""
    health = (
        hass.data.get(DOMAIN, {})
        .get(entry_id, {})
        .get("battery_health")
        or {}
    )
    capacity_wh = health.get("current_capacity_wh")
    try:
        capacity_kwh = float(capacity_wh) / 1000.0
    except (TypeError, ValueError):
        return None
    return round(capacity_kwh, 2) if capacity_kwh > 0 else None


class EnergyAccumulator:
    """Accumulates daily energy totals from instantaneous power readings.

    Integrates power (kW) over time to estimate daily energy (kWh).
    Resets at local midnight. Persisted via HA Store to survive restarts.
    """

    def __init__(self, hass: HomeAssistant | None = None, store_key: str = "") -> None:
        self._hass = hass
        self._last_update: datetime | None = None
        self._last_date: Any = None
        self.solar_kwh: float = 0.0
        self.grid_import_kwh: float = 0.0
        self.grid_export_kwh: float = 0.0
        self.battery_charge_kwh: float = 0.0
        self.battery_discharge_kwh: float = 0.0
        self.load_kwh: float = 0.0
        self.import_cost_today: float = 0.0
        self.export_earnings_today: float = 0.0
        self.mtd_solar_kwh: float = 0.0
        self.mtd_grid_import_kwh: float = 0.0
        self.mtd_grid_export_kwh: float = 0.0
        self.mtd_battery_charge_kwh: float = 0.0
        self.mtd_battery_discharge_kwh: float = 0.0
        self.mtd_load_kwh: float = 0.0
        self.mtd_import_cost: float = 0.0
        self.mtd_export_earnings: float = 0.0
        self._last_month: Any = None
        self._store: Store | None = None
        if hass and store_key:
            self._store = Store(
                hass,
                ENERGY_ACC_STORE_VERSION,
                f"power_sync.energy_acc.{store_key}",
            )

    async def async_restore(self) -> None:
        """Restore accumulated energy from persistent storage."""
        if not self._store:
            return
        try:
            data = await self._store.async_load()
        except Exception as e:
            _LOGGER.warning("Failed to load persisted energy accumulator: %s", e)
            return
        if not data:
            return
        stored_date = data.get("date")
        today = dt_util.now().strftime("%Y-%m-%d")
        if stored_date == today:
            self.solar_kwh = float(data.get("solar_kwh", 0.0))
            self.grid_import_kwh = float(data.get("grid_import_kwh", 0.0))
            self.grid_export_kwh = float(data.get("grid_export_kwh", 0.0))
            self.battery_charge_kwh = float(data.get("battery_charge_kwh", 0.0))
            self.battery_discharge_kwh = float(data.get("battery_discharge_kwh", 0.0))
            self.load_kwh = float(data.get("load_kwh", 0.0))
            self.import_cost_today = float(data.get("import_cost_today", 0.0))
            self.export_earnings_today = float(data.get("export_earnings_today", 0.0))
            _LOGGER.info(
                "Restored energy accumulator: solar=%.2f grid_in=%.2f grid_out=%.2f "
                "charge=%.2f discharge=%.2f load=%.2f kWh, cost=$%.2f earn=$%.2f (date=%s)",
                self.solar_kwh, self.grid_import_kwh, self.grid_export_kwh,
                self.battery_charge_kwh, self.battery_discharge_kwh, self.load_kwh,
                self.import_cost_today, self.export_earnings_today,
                stored_date,
            )
        else:
            _LOGGER.debug(
                "Energy accumulator data from %s (today=%s), starting fresh",
                stored_date, today,
            )
        stored_month = data.get("month")
        current_month = dt_util.now().strftime("%Y-%m")
        if stored_month == current_month:
            self.mtd_solar_kwh = float(data.get("mtd_solar_kwh", 0.0))
            self.mtd_grid_import_kwh = float(data.get("mtd_grid_import_kwh", 0.0))
            self.mtd_grid_export_kwh = float(data.get("mtd_grid_export_kwh", 0.0))
            self.mtd_battery_charge_kwh = float(data.get("mtd_battery_charge_kwh", 0.0))
            self.mtd_battery_discharge_kwh = float(data.get("mtd_battery_discharge_kwh", 0.0))
            self.mtd_load_kwh = float(data.get("mtd_load_kwh", 0.0))
            self.mtd_import_cost = float(data.get("mtd_import_cost", 0.0))
            self.mtd_export_earnings = float(data.get("mtd_export_earnings", 0.0))

    async def async_flush(self) -> None:
        """Immediately write current energy data to persistent storage.

        Called during integration unload so the next restore gets the latest
        values, preventing total_increasing sensors from going backwards.
        """
        if not self._store:
            return
        await self._store.async_save(self._data_to_save())

    def _schedule_save(self) -> None:
        """Schedule a coalesced write of energy data to persistent storage."""
        if not self._store:
            return
        self._store.async_delay_save(
            self._data_to_save,
            ENERGY_ACC_SAVE_DELAY,
        )

    def _data_to_save(self) -> dict:
        """Return energy data dict for Store serialization."""
        return {
            "date": dt_util.now().strftime("%Y-%m-%d"),
            "solar_kwh": round(self.solar_kwh, 4),
            "grid_import_kwh": round(self.grid_import_kwh, 4),
            "grid_export_kwh": round(self.grid_export_kwh, 4),
            "battery_charge_kwh": round(self.battery_charge_kwh, 4),
            "battery_discharge_kwh": round(self.battery_discharge_kwh, 4),
            "load_kwh": round(self.load_kwh, 4),
            "import_cost_today": round(self.import_cost_today, 4),
            "export_earnings_today": round(self.export_earnings_today, 4),
            "month": dt_util.now().strftime("%Y-%m"),
            "mtd_solar_kwh": round(self.mtd_solar_kwh, 4),
            "mtd_grid_import_kwh": round(self.mtd_grid_import_kwh, 4),
            "mtd_grid_export_kwh": round(self.mtd_grid_export_kwh, 4),
            "mtd_battery_charge_kwh": round(self.mtd_battery_charge_kwh, 4),
            "mtd_battery_discharge_kwh": round(self.mtd_battery_discharge_kwh, 4),
            "mtd_load_kwh": round(self.mtd_load_kwh, 4),
            "mtd_import_cost": round(self.mtd_import_cost, 4),
            "mtd_export_earnings": round(self.mtd_export_earnings, 4),
        }

    def update(
        self,
        solar_kw: float,
        grid_kw: float,
        battery_kw: float,
        load_kw: float,
        buy_price_per_kwh: float | None = None,
        sell_price_per_kwh: float | None = None,
    ) -> None:
        """Update accumulators with current power readings.

        Sign conventions (standard PowerSync format):
            solar_kw: always >= 0
            grid_kw: positive = importing, negative = exporting
            battery_kw: positive = discharging, negative = charging
            load_kw: always >= 0

        Optional cost tracking:
            buy_price_per_kwh: current import price in $/kWh (None = skip cost tracking)
            sell_price_per_kwh: current export/feed-in price in $/kWh (None = skip cost tracking)
        """
        now = dt_util.now()  # Local time for midnight reset

        # Reset MTD at month rollover
        if self._last_month is not None and now.month != self._last_month:
            self.mtd_solar_kwh = 0.0
            self.mtd_grid_import_kwh = 0.0
            self.mtd_grid_export_kwh = 0.0
            self.mtd_battery_charge_kwh = 0.0
            self.mtd_battery_discharge_kwh = 0.0
            self.mtd_load_kwh = 0.0
            self.mtd_import_cost = 0.0
            self.mtd_export_earnings = 0.0

        # Reset at local midnight
        if self._last_date is not None and now.date() != self._last_date:
            _LOGGER.info(
                "Energy accumulator midnight reset: solar=%.2f grid_in=%.2f grid_out=%.2f "
                "charge=%.2f discharge=%.2f load=%.2f kWh, cost=$%.2f earn=$%.2f",
                self.solar_kwh, self.grid_import_kwh, self.grid_export_kwh,
                self.battery_charge_kwh, self.battery_discharge_kwh, self.load_kwh,
                self.import_cost_today, self.export_earnings_today,
            )
            self.solar_kwh = 0.0
            self.grid_import_kwh = 0.0
            self.grid_export_kwh = 0.0
            self.battery_charge_kwh = 0.0
            self.battery_discharge_kwh = 0.0
            self.load_kwh = 0.0
            self.import_cost_today = 0.0
            self.export_earnings_today = 0.0

        # Integrate power × time
        if self._last_update is not None:
            delta_h = (now - self._last_update).total_seconds() / 3600
            if 0 < delta_h < 0.1:  # Sanity: skip if > 6 min gap (stale/restart)
                self.solar_kwh += max(0, solar_kw) * delta_h
                self.grid_import_kwh += max(0, grid_kw) * delta_h
                self.grid_export_kwh += max(0, -grid_kw) * delta_h
                self.battery_charge_kwh += max(0, -battery_kw) * delta_h
                self.battery_discharge_kwh += max(0, battery_kw) * delta_h
                self.load_kwh += max(0, load_kw) * delta_h
                # Accumulate costs if prices available
                if buy_price_per_kwh is not None:
                    self.import_cost_today += max(0, grid_kw) * buy_price_per_kwh * delta_h
                if sell_price_per_kwh is not None:
                    self.export_earnings_today += max(0, -grid_kw) * sell_price_per_kwh * delta_h
                # MTD accumulation
                self.mtd_solar_kwh += max(0, solar_kw) * delta_h
                self.mtd_grid_import_kwh += max(0, grid_kw) * delta_h
                self.mtd_grid_export_kwh += max(0, -grid_kw) * delta_h
                self.mtd_battery_charge_kwh += max(0, -battery_kw) * delta_h
                self.mtd_battery_discharge_kwh += max(0, battery_kw) * delta_h
                self.mtd_load_kwh += max(0, load_kw) * delta_h
                if buy_price_per_kwh is not None:
                    self.mtd_import_cost += max(0, grid_kw) * buy_price_per_kwh * delta_h
                if sell_price_per_kwh is not None:
                    self.mtd_export_earnings += max(0, -grid_kw) * sell_price_per_kwh * delta_h
                self._schedule_save()

        self._last_update = now
        self._last_date = now.date()
        self._last_month = now.month

    def as_dict(self) -> dict:
        """Return accumulated totals as a dict for energy_summary."""
        avg_today = (
            round((self.import_cost_today - self.export_earnings_today) / self.load_kwh, 4)
            if self.load_kwh > 0 else None
        )
        avg_mtd = (
            round((self.mtd_import_cost - self.mtd_export_earnings) / self.mtd_load_kwh, 4)
            if self.mtd_load_kwh > 0 else None
        )
        return {
            "pv_today_kwh": round(self.solar_kwh, 3),
            "grid_import_today_kwh": round(self.grid_import_kwh, 3),
            "grid_export_today_kwh": round(self.grid_export_kwh, 3),
            "charge_today_kwh": round(self.battery_charge_kwh, 3),
            "discharge_today_kwh": round(self.battery_discharge_kwh, 3),
            "load_today_kwh": round(self.load_kwh, 3),
            "import_cost_today": round(self.import_cost_today, 4),
            "export_earnings_today": round(self.export_earnings_today, 4),
            "avg_cost_per_kwh_today": avg_today,
            "mtd_import_cost": round(self.mtd_import_cost, 4),
            "mtd_export_earnings": round(self.mtd_export_earnings, 4),
            "mtd_load_kwh": round(self.mtd_load_kwh, 3),
            "avg_cost_per_kwh_mtd": avg_mtd,
        }


class TeslaEnergyCoordinator(DataUpdateCoordinator):
    """Coordinator to fetch Tesla energy data from Tesla API (Teslemetry or Fleet API)."""

    def __init__(
        self,
        hass: HomeAssistant,
        site_id: str,
        api_token: str,
        api_provider: str = TESLA_PROVIDER_TESLEMETRY,
        token_getter: callable = None,
        entry_id: str = "",
        fleet_base_url: str | None = None,
    ) -> None:
        """Initialize the coordinator.

        Args:
            hass: HomeAssistant instance
            site_id: Tesla energy site ID
            api_token: Initial API token (used if token_getter not provided)
            api_provider: API provider (teslemetry or fleet_api)
            token_getter: Optional callable that returns (token, provider) tuple.
                          If provided, this is called before each request to get fresh token.
            entry_id: Config entry ID for price lookups
            fleet_base_url: Regional Fleet API base URL override (EU/AP users).
                            Stored in entry.data[CONF_FLEET_API_BASE_URL].
        """
        self.site_id = site_id
        self._api_token = api_token  # Fallback token
        self._token_getter = token_getter  # Callable to get fresh token
        self.api_provider = api_provider
        self._entry_id = entry_id
        self._fleet_base_url = fleet_base_url  # Per-entry regional URL override
        self.session = async_get_clientsession(hass)
        self._site_info_cache = None  # Cache site_info (normally refreshed every 6 hours)
        self._site_info_last_fetch: float = 0  # Timestamp of last successful fetch
        self._site_info_fetch_failed = False  # Negative cache to avoid retrying on every sync cycle
        self._energy_acc = EnergyAccumulator(hass, "tesla")
        self._firmware = None  # Extracted from site_info gateways
        self._last_valid_battery_level_pct: float | None = None

        # Tesla Energy Site capability detection (populated by probe on first site_info fetch).
        # Keys: storm_mode, off_grid_vehicle_charging_reserve, vpp_programs.
        # Value True means the feature is supported by this site; False means unsupported
        # (either Tesla returned 4xx on probe, or the feature is not available in this country).
        self.tesla_capabilities: dict[str, bool] = {}
        self._capabilities_probed = False
        self._site_country: str | None = None  # From site_info (used to gate region-locked features)

        # Cached current-state values for new energy-site controls (populated opportunistically)
        self._storm_mode_enabled: bool | None = None
        self._off_grid_reserve_percent: int | None = None
        self._vpp_programs_cache: list[dict] | None = None

        # Grid status tracking (off-grid / islanding detection)
        self._last_grid_status: str = "Active"  # "Active" or "Islanded"

        # Tesla server outage tracking
        self._consecutive_failures: int = 0
        self._failure_streak_start: float = 0  # monotonic timestamp
        self._outage_notified: bool = False
        self._outage_start: float = 0  # monotonic timestamp
        self._last_outage_notification: float = 0  # monotonic timestamp (cooldown)

        # Lifetime energy totals (refreshed hourly from calendar_history period=lifetime)
        self._lifetime_totals: dict[str, float] | None = None
        self._lifetime_last_fetch: float = 0
        self._lifetime_fetch_failed: bool = False
        self._lifetime_totals_restored: bool = False
        self._lifetime_totals_store = Store(
            hass,
            LIFETIME_TOTALS_STORE_VERSION,
            f"power_sync.lifetime_totals.{entry_id or site_id}",
        )

        # Determine API base URL based on provider
        if api_provider == TESLA_PROVIDER_POWERSYNC:
            self.api_base_url = POWERSYNC_API_BASE_URL
            _LOGGER.info(f"TeslaEnergyCoordinator initialized with PowerSync.cc proxy for site {site_id}")
        elif api_provider == TESLA_PROVIDER_FLEET_API:
            self.api_base_url = fleet_base_url or FLEET_API_BASE_URL
            _LOGGER.info(f"TeslaEnergyCoordinator initialized with Fleet API for site {site_id} (base: {self.api_base_url})")
        else:
            self.api_base_url = TESLEMETRY_API_BASE_URL
            _LOGGER.info(f"TeslaEnergyCoordinator initialized with Teslemetry for site {site_id}")

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_tesla_energy",
            update_interval=UPDATE_INTERVAL_ENERGY,
        )

    def _resolve_battery_level_pct(self, live_status: dict[str, Any]) -> float | None:
        """Return Tesla SOC, preserving the last valid value when omitted."""
        raw_soc = live_status.get("percentage_charged")
        if raw_soc is not None:
            try:
                soc = float(raw_soc)
            except (TypeError, ValueError):
                soc = None
            if soc is not None and 0 <= soc <= 100:
                self._last_valid_battery_level_pct = soc
                return soc

        if self._last_valid_battery_level_pct is not None:
            _LOGGER.debug(
                "Tesla live_status omitted percentage_charged; keeping last valid SOC %.1f%%",
                self._last_valid_battery_level_pct,
            )
            return self._last_valid_battery_level_pct

        _LOGGER.debug("Tesla live_status omitted percentage_charged and no cached SOC is available")
        return None

    def _record_tesla_update_failure(self, now: float) -> tuple[bool, float]:
        """Record a Tesla update failure and return whether to send outage notice."""
        self._consecutive_failures += 1
        if self._consecutive_failures == 1 or not self._failure_streak_start:
            self._failure_streak_start = now
        failure_duration = now - self._failure_streak_start
        should_notify = (
            self._consecutive_failures >= TESLA_OUTAGE_NOTIFY_FAILURES
            and failure_duration >= TESLA_OUTAGE_NOTIFY_MIN_SECONDS
            and not self._outage_notified
        )
        return should_notify, failure_duration

    def _local_powerwall_energy_data(self) -> dict[str, Any] | None:
        """Return energy data from the paired local Powerwall coordinator."""
        entry_data = self.hass.data.get(DOMAIN, {}).get(self._entry_id, {})
        local_runtime = entry_data.get("powerwall_local") or {}
        local_coordinator = local_runtime.get("coordinator")
        snap = getattr(local_coordinator, "data", None)
        if snap is None:
            return None

        def _kw(value: Any) -> float:
            try:
                return round(float(value or 0.0) / 1000.0, 3)
            except (TypeError, ValueError):
                return 0.0

        solar_kw = _kw(getattr(snap, "solar_w", None))
        grid_kw = _kw(getattr(snap, "grid_w", None))
        battery_kw = _kw(getattr(snap, "battery_w", None))
        raw_load_kw = _kw(getattr(snap, "load_w", None))

        # The raw gateway load (snap.load_w) includes EV charging power (eg a
        # Tesla Wall Connector), same as Tesla cloud's live_status.load_power
        # above. The main cloud path subtracts ev_power_kw before it ever
        # reaches the load estimator (see load_kw computation earlier in this
        # method) — mirror that here using the same "observed EV power"
        # signal PowerwallLocalCoordinator.snapshot_as_api() subtracts via
        # its _observed_ev_power_w() (powerwall_local/coordinator.py), so a
        # Tesla cloud outage with a car charging doesn't poison home_load's
        # recorder history with EV draw. Defensive: EV power may be
        # unavailable (older/duck-typed coordinator) — treat as 0 and never
        # let load go negative.
        observed_ev_power_w = getattr(local_coordinator, "_observed_ev_power_w", None)
        ev_power_kw = 0.0
        if callable(observed_ev_power_w):
            try:
                ev_power_kw = max(0.0, _kw(observed_ev_power_w()))
            except Exception:
                ev_power_kw = 0.0
        load_kw = max(0.0, raw_load_kw - ev_power_kw)

        self._energy_acc.update(max(0, solar_kw), grid_kw, battery_kw, load_kw, 0.0, 0.0)

        raw_grid_status = str(getattr(snap, "grid_status", "") or "")
        grid_status = "Off-Grid" if "island" in raw_grid_status.lower() else "Active"
        soc_pct = getattr(snap, "soc", None)
        if soc_pct is not None:
            try:
                soc_pct = float(soc_pct)
            except (TypeError, ValueError):
                soc_pct = None
            else:
                self._last_valid_battery_level_pct = soc_pct

        total_pack_kwh: float | None = None
        total_pack_wh = getattr(snap, "total_pack_full_wh", None)
        if total_pack_wh is not None:
            try:
                total_pack_kwh = round(float(total_pack_wh) / 1000.0, 2)
            except (TypeError, ValueError):
                total_pack_kwh = None
        if total_pack_kwh is None:
            total_pack_kwh = _stored_battery_health_capacity_kwh(self.hass, self._entry_id)

        energy_left_kwh: float | None = None
        remaining_wh = getattr(snap, "total_pack_remaining_wh", None)
        if remaining_wh is not None:
            try:
                energy_left_kwh = round(float(remaining_wh) / 1000.0, 2)
            except (TypeError, ValueError):
                energy_left_kwh = None
        if energy_left_kwh is None and total_pack_kwh is not None and soc_pct is not None:
            energy_left_kwh = round(total_pack_kwh * (soc_pct / 100.0), 2)

        backup_hours: float | None = None
        if energy_left_kwh is not None and load_kw and load_kw > 0.05:
            backup_hours = round(min(999.0, energy_left_kwh / load_kw), 1)

        return {
            "solar_power": solar_kw,
            "grid_power": grid_kw,
            "battery_power": battery_kw,
            "load_power": load_kw,
            "battery_level": soc_pct,
            "grid_status": grid_status,
            "ev_power": ev_power_kw,
            "last_update": dt_util.utcnow(),
            "energy_summary": self._energy_acc.as_dict(),
            "firmware": self._firmware,
            "battery_max_charge_power": None,
            "battery_max_discharge_power": None,
            "battery_max_charge_power_w": None,
            "battery_max_discharge_power_w": None,
            "total_pack_energy_kwh": total_pack_kwh,
            "energy_left_kwh": energy_left_kwh,
            "backup_time_remaining_hours": backup_hours,
            "grid_services_active": False,
            "grid_services_power_kw": 0.0,
            "lifetime_totals": self._lifetime_totals,
            "data_source": "powerwall_local",
        }

    def _get_current_token(self) -> str | None:
        """Get the current API token, fetching fresh if token_getter is available.

        Returns None if token_getter is set but returned no token — callers must
        treat this as a transient failure and raise UpdateFailed rather than
        falling back to the potentially stale startup token.
        """
        if self._token_getter:
            try:
                token, provider = self._token_getter()
                if token:
                    # Update provider and base URL if it changed
                    if provider != self.api_provider:
                        self.api_provider = provider
                        if provider == TESLA_PROVIDER_POWERSYNC:
                            self.api_base_url = POWERSYNC_API_BASE_URL
                        elif provider == TESLA_PROVIDER_FLEET_API:
                            self.api_base_url = self._fleet_base_url or FLEET_API_BASE_URL
                        else:
                            self.api_base_url = TESLEMETRY_API_BASE_URL
                        _LOGGER.debug("Token provider changed to %s", provider)
                    return token
                # token_getter returned None — fleet integration may be mid-refresh
                _LOGGER.warning("Token getter returned no token (fleet integration may be refreshing) — skipping poll")
                return None
            except Exception as e:
                _LOGGER.warning("Token getter failed — skipping poll: %s", e)
                return None
        return self._api_token

    def _coerce_lifetime_totals(self, data: Any) -> dict[str, float]:
        """Extract persisted lifetime totals as floats."""
        if not isinstance(data, dict):
            return {}
        totals: dict[str, float] = {}
        for key in LIFETIME_TOTAL_KEYS:
            value = data.get(key)
            if value is None:
                continue
            try:
                totals[key] = float(value)
            except (TypeError, ValueError):
                continue
        return totals

    def _clamp_lifetime_totals(self, totals: dict[str, float]) -> dict[str, float]:
        """Keep lifetime counters monotonic for total_increasing sensors."""
        previous = self._lifetime_totals or {}
        if not previous:
            return totals

        clamped = dict(totals)
        for key, value in totals.items():
            previous_value = previous.get(key)
            if previous_value is None or value >= previous_value:
                continue
            clamped[key] = previous_value
            _LOGGER.debug(
                "Keeping %s monotonic: Tesla reported %.3f kWh after %.3f kWh",
                key,
                value,
                previous_value,
            )
        return clamped

    async def async_restore_lifetime_totals(self) -> None:
        """Restore persisted lifetime totals before the first coordinator state."""
        if self._lifetime_totals_restored:
            return
        self._lifetime_totals_restored = True

        if not hasattr(self._lifetime_totals_store, "async_load"):
            return
        try:
            data = await self._lifetime_totals_store.async_load()
        except Exception as err:
            _LOGGER.warning("Failed to load persisted lifetime totals: %s", err)
            return

        totals = self._coerce_lifetime_totals(data)
        if not totals:
            return

        self._lifetime_totals = totals
        _LOGGER.info("Restored Tesla lifetime totals from storage")

    async def async_flush_lifetime_totals(self) -> None:
        """Persist lifetime totals so recorder-safe maxima survive restarts."""
        if not self._lifetime_totals or not hasattr(self._lifetime_totals_store, "async_save"):
            return
        await self._lifetime_totals_store.async_save(
            {key: round(value, 3) for key, value in self._lifetime_totals.items()}
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from Tesla API (Teslemetry or Fleet API)."""
        if not self._energy_acc._last_update:
            await self._energy_acc.async_restore()
        if not self._lifetime_totals_restored:
            await self.async_restore_lifetime_totals()

        current_token = self._get_current_token()
        if not current_token:
            raise UpdateFailed("Tesla token temporarily unavailable — will retry next poll")
        headers = {
            "Authorization": f"Bearer {current_token}",
            "Content-Type": "application/json",
            "User-Agent": POWER_SYNC_USER_AGENT,
        }

        try:
            # Get live status from Tesla API with retry logic
            # Note: Both Teslemetry and Fleet API can be slow, so we use retries
            data = await _fetch_with_retry(
                self.session,
                f"{self.api_base_url}/api/1/energy_sites/{self.site_id}/live_status",
                headers,
                max_retries=3,  # More retries for reliability
                timeout_seconds=60,  # Longer timeout
                raise_auth_failed=self.api_provider != TESLA_PROVIDER_FLEET_API,
            )

            # Tesla returns {"response": null} occasionally during transient failures
            # or right after a token mint when the account state is still propagating.
            # Treat null/missing response as a temporary outage to avoid crashing.
            live_status = data.get("response") or {}
            _LOGGER.debug("Tesla API live_status response: %s", live_status)
            if not live_status:
                local_energy_data = self._local_powerwall_energy_data()
                if local_energy_data is not None:
                    _LOGGER.warning(
                        "Tesla returned empty live_status response; using paired "
                        "Powerwall local snapshot for energy telemetry"
                    )
                    return local_energy_data
                raise UpdateFailed("Tesla returned empty live_status response")

            # Extract EV charging power from Tesla Wall Connectors
            ev_power_kw = 0.0
            wall_connectors_raw = live_status.get("wall_connectors")
            if wall_connectors_raw:
                try:
                    # wall_connectors can be a JSON string or a list
                    if isinstance(wall_connectors_raw, str):
                        import ast
                        wall_connectors = ast.literal_eval(wall_connectors_raw)
                    else:
                        wall_connectors = wall_connectors_raw
                    for wc in wall_connectors:
                        wc_power = wc.get("wall_connector_power", 0) or 0
                        if wc_power > 0:
                            ev_power_kw += wc_power / 1000
                except Exception:
                    pass

            # Fallback: get EV power from BLE/Fleet vehicle sensors when
            # Wall Connector isn't reporting through Powerwall gateway.
            # Without this, EV charging power is counted as home load.
            if ev_power_kw == 0:
                try:
                    entry = self.hass.config_entries.async_get_entry(self._entry_id)
                    if entry:
                        from .. import _get_ev_vehicle_status
                        ev_status = _get_ev_vehicle_status(self.hass, entry)
                        ev_power_kw = ev_status.get("ev_power_kw", 0) or 0
                except Exception:
                    pass

            # Map Teslemetry API response to our data structure
            solar_kw = live_status.get("solar_power", 0) / 1000
            grid_kw = live_status.get("grid_power", 0) / 1000
            battery_kw = live_status.get("battery_power", 0) / 1000
            load_kw = (live_status.get("load_power", 0) / 1000) - ev_power_kw

            # Accumulate daily energy from power readings (with cost tracking)
            buy, sell = _get_current_prices(self.hass, self._entry_id)
            self._energy_acc.update(max(0, solar_kw), grid_kw, battery_kw, load_kw, buy, sell)

            # Fetch site_info periodically to detect firmware updates (every 6 hours)
            _site_info_stale = (
                time.monotonic() - self._site_info_last_fetch
            ) > TESLA_SITE_INFO_CACHE_TTL_SECONDS
            if _site_info_stale and not self._site_info_fetch_failed:
                try:
                    await self.async_get_site_info()
                except Exception:
                    pass  # Non-critical, don't fail the update

            # Grid status: "Active" (on-grid) or "Islanded" (off-grid/blackout)
            grid_status = live_status.get("grid_status", "Active")

            # Detect grid status transitions and send push notifications.
            # Tesla API returns grid_status "Active" (on-grid) or "Inactive"
            # (off-grid). Only notify on real transitions, not initial load.
            is_on_grid = grid_status == "Active"
            prev_status = self._last_grid_status
            self._last_grid_status = grid_status
            if prev_status is not None and grid_status != prev_status:
                try:
                    from ..automations.actions import _send_expo_push
                    if not is_on_grid:
                        _LOGGER.warning(
                            "Grid outage detected — Powerwall off-grid (site %s)",
                            self.site_id,
                        )
                        await _send_expo_push(
                            self.hass,
                            "Grid Outage Detected",
                            "Your Powerwall is running off-grid. Grid power is unavailable.",
                        )
                    else:
                        _LOGGER.info(
                            "Grid restored — Powerwall back on-grid (site %s)",
                            self.site_id,
                        )
                        await _send_expo_push(
                            self.hass,
                            "Grid Power Restored",
                            "Grid power has been restored. Your Powerwall is back on-grid.",
                        )
                except Exception:
                    pass

            # Derive the per-site nameplate power from cached site_info
            # (refreshed every 6 hours). Powerwall 2 is 5 kW continuous and
            # Powerwall 3 is 11.5 kW continuous; nameplate_power on Tesla's
            # /live_status payload is the total site rating in watts so it
            # covers single- and multi-unit installs. Both charge and
            # discharge use the same ceiling.
            nameplate_w = None
            if self._site_info_cache:
                nameplate_w = self._site_info_cache.get("nameplate_power")
            nameplate_kw = round(nameplate_w / 1000.0, 2) if nameplate_w else None

            # Total pack energy (nameplate Wh) and energy_left (stored Wh) come
            # from live_status when Tesla supplies them. When live_status omits
            # pack capacity, prefer the BMS-scanned Battery Health capacity over
            # the static battery_count × per-unit nameplate fallback.
            total_pack_kwh: float | None = None
            tpe_w = live_status.get("total_pack_energy")
            if tpe_w is not None:
                try:
                    total_pack_kwh = round(float(tpe_w) / 1000.0, 2)
                except (TypeError, ValueError):
                    total_pack_kwh = None
            if total_pack_kwh is None:
                total_pack_kwh = _stored_battery_health_capacity_kwh(
                    self.hass,
                    self._entry_id,
                )
            if total_pack_kwh is None and self._site_info_cache:
                # Last-resort fallback when no BMS scan has populated live
                # capacity yet.
                count = (
                    (self._site_info_cache.get("components") or {}).get("battery_count")
                    or self._site_info_cache.get("battery_count")
                )
                if count:
                    try:
                        total_pack_kwh = round(int(count) * 13.5, 2)
                    except (TypeError, ValueError):
                        pass

            soc_pct = self._resolve_battery_level_pct(live_status)
            energy_left_kwh: float | None = None
            el_w = live_status.get("energy_left")
            if el_w is not None:
                try:
                    energy_left_kwh = round(float(el_w) / 1000.0, 2)
                except (TypeError, ValueError):
                    energy_left_kwh = None
            if energy_left_kwh is None and total_pack_kwh is not None and soc_pct is not None:
                energy_left_kwh = round(total_pack_kwh * (soc_pct / 100.0), 2)

            # Backup time remaining (hours): stored kWh / current home load.
            # Caps at 999 to keep the UI sane when load drops near zero.
            backup_hours: float | None = None
            if energy_left_kwh is not None and load_kw and load_kw > 0.05:
                backup_hours = round(min(999.0, energy_left_kwh / load_kw), 1)

            # Grid services / VPP — present in live_status when site is enrolled.
            # When the site has no VPP the field is typically absent or 0;
            # default the power reading to 0 so the sensor reads a real value
            # ("0 W") rather than "Unknown" — much more useful for graphs.
            grid_services_active = bool(live_status.get("grid_services_active", False))
            grid_services_power_kw: float = 0.0
            gsp = live_status.get("grid_services_power")
            if gsp is not None:
                try:
                    grid_services_power_kw = round(float(gsp) / 1000.0, 3)
                except (TypeError, ValueError):
                    grid_services_power_kw = 0.0

            energy_data = {
                "solar_power": solar_kw,
                "grid_power": grid_kw,
                "battery_power": battery_kw,
                "load_power": load_kw,
                "battery_level": soc_pct,
                "grid_status": grid_status,
                "ev_power": ev_power_kw,
                "last_update": dt_util.utcnow(),
                "energy_summary": self._energy_acc.as_dict(),
                "firmware": self._firmware,
                # BMS ceiling for the mobile force-mode picker's Max chip
                "battery_max_charge_power": nameplate_kw,
                "battery_max_discharge_power": nameplate_kw,
                "battery_max_charge_power_w": nameplate_w,
                "battery_max_discharge_power_w": nameplate_w,
                # Powerwall extended fields
                "total_pack_energy_kwh": total_pack_kwh,
                "energy_left_kwh": energy_left_kwh,
                "backup_time_remaining_hours": backup_hours,
                "grid_services_active": grid_services_active,
                "grid_services_power_kw": grid_services_power_kw,
                "lifetime_totals": self._lifetime_totals,
            }

            # Refresh lifetime totals once per hour (best-effort, never fails the poll)
            _lifetime_stale = (time.monotonic() - self._lifetime_last_fetch) > 3600
            if _lifetime_stale and not self._lifetime_fetch_failed:
                try:
                    await self.async_refresh_lifetime_totals()
                    energy_data["lifetime_totals"] = self._lifetime_totals
                except Exception as err:
                    _LOGGER.debug("Lifetime totals refresh failed: %s", err)

            # Tesla API recovered — send recovery notification if we were in outage
            if self._outage_notified:
                outage_mins = int((time.monotonic() - self._outage_start) / 60)
                _LOGGER.warning(
                    "Tesla API recovered after %d min outage (site %s)",
                    outage_mins, self.site_id,
                )
                try:
                    from ..automations.actions import _send_expo_push
                    await _send_expo_push(
                        self.hass,
                        "Tesla Server Recovered",
                        f"Tesla API is back online after {outage_mins} min outage",
                    )
                except Exception:
                    pass
            self._consecutive_failures = 0
            self._failure_streak_start = 0
            self._outage_notified = False

            return energy_data

        except ConfigEntryAuthFailed:
            # Don't retry — let HA's reauth flow take over
            raise
        except (UpdateFailed, Exception) as err:
            now = time.monotonic()
            should_notify, failure_duration = self._record_tesla_update_failure(now)

            # Notify only after a sustained failure window. Refreshes can be
            # requested faster than the normal update interval, so attempt
            # count alone can report a short Tesla empty-response burst as a
            # server outage.
            if should_notify:
                self._outage_notified = True
                self._outage_start = self._failure_streak_start
                self._last_outage_notification = now
                _LOGGER.error(
                    "Tesla server outage detected: %d consecutive failures over %.0fs (site %s)",
                    self._consecutive_failures, failure_duration, self.site_id,
                )
                try:
                    from ..automations.actions import _send_expo_push
                    await _send_expo_push(
                        self.hass,
                        "Tesla Server Outage",
                        f"Tesla API unreachable — optimization paused. Error: {err}",
                    )
                except Exception:
                    pass
            elif self._outage_notified and (now - self._last_outage_notification) > 1800:
                # Repeat notification every 30 min during ongoing outage
                outage_mins = int((now - self._outage_start) / 60)
                self._last_outage_notification = now
                try:
                    from ..automations.actions import _send_expo_push
                    await _send_expo_push(
                        self.hass,
                        "Tesla Server Outage",
                        f"Tesla API still unreachable after {outage_mins} min",
                    )
                except Exception:
                    pass

            if isinstance(err, UpdateFailed):
                raise
            raise UpdateFailed(f"Unexpected error fetching Tesla energy data: {err}") from err

    async def async_get_site_info(
        self,
        max_age: float | None = None,
    ) -> dict[str, Any] | None:
        """
        Fetch site_info from Tesla API (Teslemetry or Fleet API).

        Includes installation_time_zone which is critical for correct TOU schedule alignment.
        Results are cached since site info (especially timezone) doesn't change.

        Returns:
            Site info dict containing installation_time_zone, or None if fetch fails
        """
        cache_ttl = (
            TESLA_SITE_INFO_CACHE_TTL_SECONDS
            if max_age is None
            else max(0, float(max_age))
        )

        # Return cached value if still fresh.
        if (
            self._site_info_cache
            and (time.monotonic() - self._site_info_last_fetch) <= cache_ttl
        ):
            _LOGGER.debug("Returning cached site_info")
            return self._site_info_cache

        # Don't retry if a previous fetch already failed (avoids spamming logs every sync cycle)
        if self._site_info_fetch_failed:
            return None

        current_token = self._get_current_token()
        headers = {
            "Authorization": f"Bearer {current_token}",
            "Content-Type": "application/json",
            "User-Agent": POWER_SYNC_USER_AGENT,
        }

        try:
            _LOGGER.info(f"Fetching site_info for site {self.site_id}")

            data = await _fetch_with_retry(
                self.session,
                f"{self.api_base_url}/api/1/energy_sites/{self.site_id}/site_info",
                headers,
                max_retries=3,
                timeout_seconds=60,
                raise_auth_failed=self.api_provider != TESLA_PROVIDER_FLEET_API,
            )

            site_info = data.get("response", {})

            # Log timezone info for debugging
            installation_tz = site_info.get("installation_time_zone")
            if installation_tz:
                _LOGGER.info(f"Found Powerwall timezone: {installation_tz}")
            else:
                _LOGGER.warning("No installation_time_zone in site_info response")

            # Log battery capacity info for debugging
            _LOGGER.debug(f"Site info keys: {list(site_info.keys())}")
            components = site_info.get("components", {})
            if components:
                _LOGGER.debug(f"Site info components keys: {list(components.keys())}")
                # Log battery-related fields
                battery_fields = {k: v for k, v in site_info.items()
                                 if 'battery' in k.lower() or 'pack' in k.lower() or 'energy' in k.lower() or 'power' in k.lower()}
                if battery_fields:
                    _LOGGER.debug(f"Site info battery fields: {battery_fields}")
                component_battery = {k: v for k, v in components.items()
                                    if 'battery' in k.lower() or 'nameplate' in k.lower()}
                if component_battery:
                    _LOGGER.debug(f"Components battery fields: {component_battery}")

            # Extract firmware version
            gateways = components.get("gateways", []) or site_info.get("gateways", [])
            if gateways:
                gateway = gateways[0]
                _LOGGER.info("Gateway keys: %s", list(gateway.keys()))
                fw_version = (
                    gateway.get("firmware_version")
                    or gateway.get("version")
                    or gateway.get("gateway_firmware_version")
                    or gateway.get("fw_version")
                    or ""
                )
                if fw_version:
                    self._firmware = fw_version
                    _LOGGER.info("Firmware version: %s", fw_version)
                else:
                    _LOGGER.info("No firmware key found in gateway: %s", gateway)

            # Extract country (used for region-gating; Tesla reports ISO country code
            # in site_info for Energy Sites, though the key has varied historically).
            self._site_country = (
                site_info.get("country")
                or site_info.get("installation_country")
                or components.get("country")
            )

            # Opportunistically capture current state for new energy-site controls.
            # Tesla returns these in site_info when available; otherwise we fall back
            # to explicit GET calls during the capability probe.
            if "off_grid_vehicle_charging_reserve_percent" in site_info:
                self._off_grid_reserve_percent = site_info.get(
                    "off_grid_vehicle_charging_reserve_percent"
                )
            elif "off_grid_vehicle_charging_reserve_percent" in components:
                self._off_grid_reserve_percent = components.get(
                    "off_grid_vehicle_charging_reserve_percent"
                )

            storm_mode_active = (
                site_info.get("storm_mode_active")
                if "storm_mode_active" in site_info
                else components.get("storm_mode_active")
            )
            storm_mode_enabled = (
                site_info.get("user_settings", {}).get("storm_mode_enabled")
                if isinstance(site_info.get("user_settings"), dict)
                else None
            )
            if storm_mode_enabled is not None:
                self._storm_mode_enabled = bool(storm_mode_enabled)
            elif storm_mode_active is not None:
                self._storm_mode_enabled = bool(storm_mode_active)

            # Cache the result with timestamp
            self._site_info_cache = site_info
            self._site_info_last_fetch = time.monotonic()

            # Schedule one-shot capability probe on first successful fetch.
            # Runs in background to avoid blocking the main fetch path.
            if not self._capabilities_probed:
                self._capabilities_probed = True
                self.hass.async_create_task(
                    self._async_probe_tesla_capabilities(),
                    name=f"{DOMAIN}_tesla_capability_probe",
                )

            return site_info

        except UpdateFailed as err:
            _LOGGER.warning("Failed to fetch site_info: %s (will not retry until next restart)", err)
            self._site_info_fetch_failed = True
            return None
        except Exception as err:
            _LOGGER.warning("Unexpected error fetching site_info: %s (will not retry until next restart)", err)
            self._site_info_fetch_failed = True
            return None

    def invalidate_site_info_cache(self) -> None:
        """Force the next async_get_site_info() call to re-fetch from Tesla.

        Call this after any write that modifies site_info-level fields
        (backup reserve, operation mode, grid export rule, grid charging,
        storm mode, off-grid EV reserve, VPP enrollment) so that HA
        entities reading from the cache don't display stale values for
        up to six hours until the next natural refresh.
        """
        # Clear the cached payload itself, not just the timestamp.
        # async_get_site_info() returns cached data while it is inside the
        # caller's max_age window. Resetting only _site_info_last_fetch can
        # still leave a shorter-uptime HA instance inside that window, so clear
        # the cached payload itself to force the next call to refetch.
        self._site_info_cache = None
        self._site_info_last_fetch = 0
        self._site_info_fetch_failed = False
        _LOGGER.debug("Tesla site_info cache invalidated — next read will refetch")

    async def set_grid_charging_enabled(self, enabled: bool) -> bool:
        """
        Enable or disable grid charging (imports) for the Powerwall.

        Args:
            enabled: True to allow grid charging, False to disallow

        Returns:
            bool: True if successful, False otherwise
        """
        # Note: The API field is inverted - True means charging is DISALLOWED
        disallow_value = not enabled

        current_token = self._get_current_token()
        headers = {
            "Authorization": f"Bearer {current_token}",
            "Content-Type": "application/json",
            "User-Agent": POWER_SYNC_USER_AGENT,
        }

        try:
            _LOGGER.info(f"Setting grid charging {'enabled' if enabled else 'disabled'} for site {self.site_id}")

            url = f"{self.api_base_url}/api/1/energy_sites/{self.site_id}/grid_import_export"
            payload = {
                "disallow_charge_from_grid_with_solar_installed": disallow_value
            }

            async with self.session.post(url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as response:
                if response.status not in [200, 201, 202]:
                    text = await response.text()
                    _LOGGER.error(f"Failed to set grid charging: {response.status} - {text}")
                    return False

                data = await response.json()
                _LOGGER.debug(f"Set grid charging response: {data}")

                # Check for actual success in response body
                response_data = data.get("response", data)
                if isinstance(response_data, dict) and "result" in response_data:
                    if not response_data["result"]:
                        reason = response_data.get("reason", "Unknown reason")
                        _LOGGER.error(f"Set grid charging failed: {reason}")
                        return False

                _LOGGER.info(f"✅ Grid charging {'enabled' if enabled else 'disabled'} successfully for site {self.site_id}")
                self.invalidate_site_info_cache()
                return True

        except asyncio.TimeoutError:
            _LOGGER.error("Timeout setting grid charging")
            return False
        except Exception as err:
            _LOGGER.error(f"Error setting grid charging: {err}")
            return False

    # ------------------------------------------------------------------
    # Unified Tesla Energy Site API helper
    # ------------------------------------------------------------------

    def _tesla_headers(self) -> dict[str, str]:
        """Build authorization headers using the freshest token."""
        return {
            "Authorization": f"Bearer {self._get_current_token()}",
            "Content-Type": "application/json",
            "User-Agent": POWER_SYNC_USER_AGENT,
        }

    async def _tesla_api_call(
        self,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
        max_retries: int = 3,
        timeout_seconds: int = 30,
    ) -> tuple[int, dict | None]:
        """Make a Tesla Energy Site API call with retry/backoff.

        Returns (status_code, response_json_or_none). Retries on 429/5xx using
        Retry-After if provided, otherwise exponential backoff. Does NOT raise
        on 4xx — callers interpret status codes (e.g. probe uses 4xx to detect
        unsupported features).
        """
        url = f"{self.api_base_url}{path}"
        last_status = 0
        retry_after_delay: float | None = None

        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    wait_time = retry_after_delay or (2 ** attempt)
                    retry_after_delay = None
                    await asyncio.sleep(wait_time)

                headers = self._tesla_headers()
                request = self.session.request(
                    method,
                    url,
                    headers=headers,
                    json=json_body if method.upper() != "GET" else None,
                    timeout=aiohttp.ClientTimeout(total=timeout_seconds),
                )
                async with request as response:
                    last_status = response.status
                    if response.status == 200:
                        try:
                            return response.status, await response.json()
                        except Exception:
                            return response.status, None

                    if response.status in (429, 500, 502, 503, 504):
                        retry_after_delay = _parse_retry_after(response)
                        _LOGGER.warning(
                            "Tesla %s %s attempt %d/%d: %s",
                            method, path, attempt + 1, max_retries, response.status,
                        )
                        continue

                    # Non-retryable status — return as-is for caller inspection
                    try:
                        return response.status, await response.json()
                    except Exception:
                        return response.status, None

            except asyncio.TimeoutError:
                _LOGGER.warning(
                    "Tesla %s %s attempt %d/%d timed out",
                    method, path, attempt + 1, max_retries,
                )
                continue
            except aiohttp.ClientError as err:
                _LOGGER.warning(
                    "Tesla %s %s attempt %d/%d network error: %s",
                    method, path, attempt + 1, max_retries, err,
                )
                continue

        return last_status or 0, None

    # ------------------------------------------------------------------
    # Capability probe (run once after first site_info fetch)
    # ------------------------------------------------------------------

    async def _async_probe_tesla_capabilities(self) -> None:
        """Probe Tesla Energy Site endpoints to determine which features are supported.

        Tesla does not expose clean feature flags; instead we attempt a harmless
        GET on each new endpoint and interpret the response:
          - 200: feature supported → True
          - 404 / 501 / 400 "not_supported": unsupported → False
          - other 4xx: unknown (assume supported so user can retry)
          - 5xx / network error: unknown (assume supported; probe again later)
        Results are cached in self.tesla_capabilities and persist until restart.
        """
        _LOGGER.info("Probing Tesla Energy Site capabilities for site %s", self.site_id)

        async def _probe(name: str, path: str) -> bool:
            status, _body = await self._tesla_api_call("GET", path, max_retries=1, timeout_seconds=15)
            if status == 200:
                _LOGGER.info("Tesla capability '%s' supported (200)", name)
                return True
            if status in (400, 404, 405, 501):
                _LOGGER.info("Tesla capability '%s' unsupported (%d)", name, status)
                return False
            _LOGGER.info(
                "Tesla capability '%s' probe inconclusive (%d) — assuming supported",
                name, status,
            )
            return True

        # Run probes sequentially to be gentle on Tesla rate limits.
        base = f"/api/1/energy_sites/{self.site_id}"
        self.tesla_capabilities["storm_mode"] = await _probe(
            "storm_mode", f"{base}/storm_mode",
        )
        self.tesla_capabilities["off_grid_vehicle_charging_reserve"] = await _probe(
            "off_grid_vehicle_charging_reserve",
            f"{base}/off_grid_vehicle_charging_reserve",
        )
        # VPP programs endpoint returns the list of programs the site is eligible for.
        # An empty list still means the endpoint is supported (just no programs).
        status, body = await self._tesla_api_call(
            "GET", f"{base}/programs", max_retries=1, timeout_seconds=15,
        )
        if status == 200:
            programs = []
            if isinstance(body, dict):
                resp = body.get("response", body)
                if isinstance(resp, dict):
                    programs = resp.get("programs") or resp.get("enrolled_programs") or []
                elif isinstance(resp, list):
                    programs = resp
            self._vpp_programs_cache = programs if isinstance(programs, list) else []
            self.tesla_capabilities["vpp_programs"] = True
            _LOGGER.info(
                "Tesla capability 'vpp_programs' supported — %d programs available",
                len(self._vpp_programs_cache),
            )
        elif status in (400, 404, 405, 501):
            self.tesla_capabilities["vpp_programs"] = False
            _LOGGER.info("Tesla capability 'vpp_programs' unsupported (%d)", status)
        else:
            self.tesla_capabilities["vpp_programs"] = True
            _LOGGER.info(
                "Tesla capability 'vpp_programs' probe inconclusive (%d) — assuming supported",
                status,
            )

        # Notify platforms so entities can be (re)created now that capabilities are known.
        # The probe can complete before async_setup_entry publishes its full
        # hass.data entry, so create the per-entry dict instead of writing to a
        # throwaway default.
        entry_data = self.hass.data.setdefault(DOMAIN, {}).setdefault(self._entry_id, {})
        entry_data["tesla_capabilities"] = dict(self.tesla_capabilities)
        entry_data["tesla_site_country"] = self._site_country

        # Prune orphaned entities from prior sessions where a capability was
        # supported at the time but is no longer. Without this, the entity
        # registry keeps stale unique_ids which HA displays as "unavailable"
        # and the dashboard strategy will surface them as broken controls.
        self._cleanup_unsupported_tesla_entities()

    def _cleanup_unsupported_tesla_entities(self) -> None:
        """Remove registry entries for Tesla capabilities that the current
        site does not support. Called after every capability probe so that
        upgrading from a version where a capability was incorrectly detected
        (or switching sites) cleans up the orphans automatically."""
        try:
            from homeassistant.helpers import entity_registry as er
        except Exception:
            return
        try:
            ent_reg = er.async_get(self.hass)
        except Exception:
            return

        removed = 0

        def _remove_by_unique_id(domain: str, unique_id: str) -> None:
            nonlocal removed
            eid = ent_reg.async_get_entity_id(domain, DOMAIN, unique_id)
            if eid:
                try:
                    ent_reg.async_remove(eid)
                    removed += 1
                    _LOGGER.debug("Removed orphaned Tesla entity %s", eid)
                except Exception as err:
                    _LOGGER.debug("Failed to remove %s: %s", eid, err)

        if self.tesla_capabilities.get("storm_mode") is False:
            _remove_by_unique_id("switch", f"{self._entry_id}_tesla_storm_watch")
            _remove_by_unique_id("binary_sensor", f"{self._entry_id}_tesla_storm_watch_active")

        if self.tesla_capabilities.get("off_grid_vehicle_charging_reserve") is False:
            _remove_by_unique_id("number", f"{self._entry_id}_tesla_off_grid_ev_reserve")

        if self.tesla_capabilities.get("vpp_programs") is False:
            # Remove every vpp_* switch created under this entry
            try:
                for reg_entry in list(ent_reg.entities.values()):
                    if (reg_entry.config_entry_id == self._entry_id
                        and reg_entry.domain == "switch"
                        and reg_entry.platform == DOMAIN
                        and "_tesla_vpp_" in (reg_entry.unique_id or "")):
                        ent_reg.async_remove(reg_entry.entity_id)
                        removed += 1
                        _LOGGER.debug("Removed orphaned VPP switch %s", reg_entry.entity_id)
            except Exception as err:
                _LOGGER.debug("Failed to scan VPP switches: %s", err)

        if removed > 0:
            _LOGGER.info(
                "Cleaned up %d orphaned Tesla capability entities (site no longer supports them)",
                removed,
            )

    # ------------------------------------------------------------------
    # New Energy Site controls (storm mode, off-grid EV reserve, VPP programs)
    # ------------------------------------------------------------------

    async def async_set_storm_watch(self, enabled: bool) -> bool:
        """Enable or disable Tesla Storm Watch (predictive pre-charging)."""
        path = f"/api/1/energy_sites/{self.site_id}/storm_mode"
        status, _body = await self._tesla_api_call(
            "POST", path, json_body={"enabled": bool(enabled)},
        )
        if status == 200:
            self._storm_mode_enabled = bool(enabled)
            self.invalidate_site_info_cache()
            _LOGGER.info("Storm Watch %s for site %s", "enabled" if enabled else "disabled", self.site_id)
            return True
        _LOGGER.error("Failed to set storm mode for site %s: HTTP %s", self.site_id, status)
        return False

    async def async_get_storm_watch_status(self) -> dict | None:
        """Fetch current storm watch enabled + active state."""
        path = f"/api/1/energy_sites/{self.site_id}/storm_mode"
        status, body = await self._tesla_api_call("GET", path)
        if status != 200 or not isinstance(body, dict):
            return None
        resp = body.get("response", body)
        if not isinstance(resp, dict):
            return None
        if "enabled" in resp:
            self._storm_mode_enabled = bool(resp.get("enabled"))
        return resp

    async def async_set_off_grid_ev_reserve(self, percent: int) -> bool:
        """Set off-grid vehicle charging reserve percent (0-100)."""
        try:
            percent = int(percent)
        except (TypeError, ValueError):
            _LOGGER.error("Invalid off-grid EV reserve value: %r", percent)
            return False
        percent = max(0, min(100, percent))
        path = f"/api/1/energy_sites/{self.site_id}/off_grid_vehicle_charging_reserve"
        status, _body = await self._tesla_api_call(
            "POST", path, json_body={"off_grid_vehicle_charging_reserve_percent": percent},
        )
        if status == 200:
            self._off_grid_reserve_percent = percent
            self.invalidate_site_info_cache()
            _LOGGER.info("Off-grid EV reserve set to %d%% for site %s", percent, self.site_id)
            return True
        _LOGGER.error("Failed to set off-grid EV reserve for site %s: HTTP %s", self.site_id, status)
        return False

    async def async_get_vpp_programs(self, force_refresh: bool = False) -> list[dict]:
        """Fetch VPP / grid-services programs the site is eligible for.

        Each program is a dict; Tesla's schema has varied but typically includes
        ``id`` / ``program_id``, ``name``, and an ``enrolled`` / ``is_enrolled``
        flag.
        """
        if self._vpp_programs_cache is not None and not force_refresh:
            return self._vpp_programs_cache
        path = f"/api/1/energy_sites/{self.site_id}/programs"
        status, body = await self._tesla_api_call("GET", path)
        if status != 200 or not isinstance(body, dict):
            return self._vpp_programs_cache or []
        resp = body.get("response", body)
        programs: list[dict] = []
        if isinstance(resp, dict):
            raw = resp.get("programs") or resp.get("enrolled_programs") or []
            if isinstance(raw, list):
                programs = [p for p in raw if isinstance(p, dict)]
        elif isinstance(resp, list):
            programs = [p for p in resp if isinstance(p, dict)]
        self._vpp_programs_cache = programs
        return programs

    async def async_set_vpp_enrollment(self, program_id: str, enrolled: bool) -> bool:
        """Opt in or out of a Tesla VPP / grid-services program."""
        if not program_id:
            _LOGGER.error("Missing program_id for VPP enrollment")
            return False
        path = f"/api/1/energy_sites/{self.site_id}/programs"
        payload = {
            "program_id": program_id,
            "enrolled": bool(enrolled),
        }
        status, _body = await self._tesla_api_call("POST", path, json_body=payload)
        if status == 200:
            # Invalidate caches so next reads pick up new state.
            self._vpp_programs_cache = None
            self.invalidate_site_info_cache()
            _LOGGER.info(
                "VPP program %s %s for site %s",
                program_id, "enrolled" if enrolled else "unenrolled", self.site_id,
            )
            return True
        _LOGGER.error(
            "Failed to set VPP enrollment for site %s program %s: HTTP %s",
            self.site_id, program_id, status,
        )
        return False

    async def async_get_calendar_history(
        self,
        period: str = "day",
        kind: str = "energy",
        end_date: str | None = None,
    ) -> dict[str, Any] | None:
        """
        Fetch calendar history from Tesla API.

        Args:
            period: 'day', 'week', 'month', 'year', or 'lifetime'
            kind: 'energy' or 'power'
            end_date: Optional end date in YYYY-MM-DD format (defaults to today)

        Returns:
            Calendar history data with time_series array, or None if fetch fails
        """
        current_token = self._get_current_token()
        headers = {
            "Authorization": f"Bearer {current_token}",
            "Content-Type": "application/json",
            "User-Agent": POWER_SYNC_USER_AGENT,
        }

        try:
            # Get site timezone from site_info
            site_info = await self.async_get_site_info()
            timezone = "Australia/Brisbane"  # Default fallback
            if site_info:
                timezone = site_info.get("installation_time_zone", timezone)

            # Calculate end_date in site's timezone
            from zoneinfo import ZoneInfo
            from datetime import timedelta
            user_tz = ZoneInfo(timezone)

            # Use provided end_date or default to now
            if end_date:
                try:
                    reference_date = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=user_tz)
                except ValueError:
                    reference_date = datetime.now(user_tz)
            else:
                reference_date = datetime.now(user_tz)

            end_dt = reference_date.replace(hour=23, minute=59, second=59)
            end_date_iso = end_dt.isoformat()

            _LOGGER.info(f"Fetching calendar history for site {self.site_id}: period={period}, kind={kind}, end_date={end_date}")

            params = {
                "kind": kind,
                "period": period,
                "end_date": end_date_iso,
                "time_zone": timezone,
            }

            url = f"{self.api_base_url}/api/1/energy_sites/{self.site_id}/calendar_history"

            async with self.session.get(
                url,
                headers=headers,
                params=params,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status != 200:
                    text = await response.text()
                    _LOGGER.error(f"Failed to fetch calendar history: {response.status} - {text}")
                    return None

                data = await response.json()
                result = data.get("response", {})
                time_series = result.get("time_series", [])

                _LOGGER.info(f"Fetched {len(time_series)} raw records from Tesla for period='{period}'")

                # Tesla API often returns all historical data regardless of period
                # Filter client-side based on requested period and end_date
                if time_series and period in ["day", "week", "month", "year"]:
                    # Calculate cutoff date based on period, relative to reference_date
                    if period == "day":
                        cutoff = reference_date.replace(hour=0, minute=0, second=0, microsecond=0)
                    elif period == "week":
                        cutoff = (reference_date - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
                    elif period == "month":
                        cutoff = (reference_date - timedelta(days=30)).replace(hour=0, minute=0, second=0, microsecond=0)
                    elif period == "year":
                        cutoff = (reference_date - timedelta(days=365)).replace(hour=0, minute=0, second=0, microsecond=0)

                    # End of reference day as upper bound
                    end_of_day = reference_date.replace(hour=23, minute=59, second=59, microsecond=999999)

                    filtered_series = []
                    for entry in time_series:
                        try:
                            ts_str = entry.get("timestamp", "")
                            if ts_str:
                                entry_dt = datetime.fromisoformat(ts_str)
                                if cutoff <= entry_dt <= end_of_day:
                                    filtered_series.append(entry)
                        except (ValueError, TypeError) as e:
                            _LOGGER.warning(f"Failed to parse timestamp: {entry.get('timestamp')}: {e}")
                            continue

                    _LOGGER.info(f"Filtered calendar history from {len(time_series)} to {len(filtered_series)} records for period='{period}' (cutoff={cutoff.date()}, end={end_of_day.date()})")
                    time_series = filtered_series

                _LOGGER.info(f"Successfully fetched calendar history: {len(time_series)} records for period='{period}'")

                return {
                    "period": period,
                    "time_series": time_series,
                    "serial_number": result.get("serial_number"),
                    "installation_date": result.get("installation_date"),
                }

        except asyncio.TimeoutError:
            _LOGGER.error("Timeout fetching calendar history")
            return None
        except Exception as err:
            _LOGGER.error(f"Error fetching calendar history: {err}")
            return None

    async def async_refresh_lifetime_totals(self) -> dict[str, float] | None:
        """Sum calendar_history period=lifetime into a small dict of kWh totals.

        Tesla returns Wh per bucket (yearly bins from install date). Result is
        cached in ``self._lifetime_totals`` so sensors return the last good value
        between refreshes; on permanent failure (e.g. unsupported endpoint),
        ``_lifetime_fetch_failed`` short-circuits subsequent calls.
        """
        history = await self.async_get_calendar_history(period="lifetime")
        if not history:
            return self._lifetime_totals

        totals = {key: 0.0 for key in LIFETIME_TOTAL_KEYS}
        for ts in history.get("time_series", []) or []:
            totals["lifetime_solar_kwh"] += (ts.get("solar_energy_exported") or 0)
            totals["lifetime_grid_import_kwh"] += (ts.get("grid_energy_imported") or 0)
            totals["lifetime_grid_export_kwh"] += (
                (ts.get("grid_energy_exported_from_solar") or 0)
                + (ts.get("grid_energy_exported_from_battery") or 0)
            )
            totals["lifetime_battery_charged_kwh"] += (
                (ts.get("battery_energy_imported_from_grid") or 0)
                + (ts.get("battery_energy_imported_from_solar") or 0)
            )
            totals["lifetime_battery_discharged_kwh"] += (ts.get("battery_energy_exported") or 0)
            totals["lifetime_home_kwh"] += (
                (ts.get("consumer_energy_imported_from_grid") or 0)
                + (ts.get("consumer_energy_imported_from_solar") or 0)
                + (ts.get("consumer_energy_imported_from_battery") or 0)
            )

        # Tesla returns Wh; convert to kWh
        for k in totals:
            totals[k] = round(totals[k] / 1000.0, 3)

        totals = self._clamp_lifetime_totals(totals)
        self._lifetime_totals = totals
        self._lifetime_last_fetch = time.monotonic()
        await self.async_flush_lifetime_totals()
        return totals


class SigenergyEnergyCoordinator(DataUpdateCoordinator):
    """Coordinator to fetch Sigenergy energy data via Modbus.

    Polls the Sigenergy inverter system via Modbus TCP to get real-time
    power data (solar, battery, grid, load) and battery state of charge.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        port: int = 502,
        slave_id: int = 1,
        entry_id: str = "",
        max_export_limit_kw: Optional[float] = None,
        configured_charge_rate_limit_kw: Optional[float] = None,
        configured_discharge_rate_limit_kw: Optional[float] = None,
    ) -> None:
        """Initialize the coordinator.

        Args:
            hass: HomeAssistant instance
            host: IP address of Sigenergy system
            port: Modbus TCP port (default: 502)
            slave_id: Modbus slave ID (default: 1)
            entry_id: Config entry ID for price lookups
            max_export_limit_kw: User-configured DNSP export limit in kW
            configured_charge_rate_limit_kw: User-configured normal charge cap in kW
            configured_discharge_rate_limit_kw: User-configured normal discharge cap in kW
        """
        from ..inverters.sigenergy import SigenergyController

        self.host = host
        self.port = port
        self.slave_id = slave_id
        self._entry_id = entry_id
        self._controller = SigenergyController(
            host,
            port,
            slave_id,
            max_export_limit_kw=max_export_limit_kw,
            configured_charge_rate_limit_kw=configured_charge_rate_limit_kw,
            configured_discharge_rate_limit_kw=configured_discharge_rate_limit_kw,
        )
        self._energy_acc = EnergyAccumulator(hass, "sigenergy")
        # Rated charge/discharge power in kW — cached after first successful
        # read from input registers 30079/30081. Static hardware spec so it
        # only needs to be fetched once.
        self._rated_charge_power_kw: Optional[float] = None
        self._rated_discharge_power_kw: Optional[float] = None

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_sigenergy_energy",
            update_interval=UPDATE_INTERVAL_ENERGY,
        )

    async def _async_read_evdc_charger_state(self):
        """Read EVDC charger state when the configured charger is DC-side."""
        try:
            entry = self.hass.config_entries.async_get_entry(self._entry_id)
        except Exception:
            entry = None
        if not entry:
            return None

        opts = {**entry.data, **entry.options}
        if not opts.get(CONF_SIGENERGY_CHARGER_ENABLED):
            return None
        charger_type = str(
            opts.get(CONF_SIGENERGY_CHARGER_TYPE, SIGENERGY_CHARGER_EVAC)
        ).lower()
        if charger_type != SIGENERGY_CHARGER_EVDC:
            return None

        from ..sigenergy_charger_config import resolve_sigenergy_charger_connection

        config = resolve_sigenergy_charger_connection(
            entry,
            hass=self.hass,
            fallback_host=self.host,
        )
        host = str(config["host"]).strip()
        if not host:
            return None

        from ..sigenergy_charger import SigenergyEVChargerController

        controller = SigenergyEVChargerController(
            host=host,
            port=config["port"],
            slave_id=config["slave_id"],
            charger_type=SIGENERGY_CHARGER_EVDC,
        )
        try:
            return await controller.read_state()
        except Exception as err:
            _LOGGER.debug("Sigenergy EVDC state read failed during energy update: %s", err)
            return None
        finally:
            await controller.disconnect()

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from Sigenergy system via Modbus."""
        if not self._energy_acc._last_update:
            await self._energy_acc.async_restore()
        try:
            status = await self._controller.get_status()

            attrs = status.attributes or {}

            # If Modbus returned no battery data, keep previous readings
            # rather than reporting SOC=0% which causes optimizer issues.
            if "battery_soc" not in attrs:
                if self.data:
                    _LOGGER.warning(
                        "Sigenergy Modbus returned no battery data — keeping previous readings"
                    )
                    return self.data
                raise UpdateFailed("Sigenergy Modbus connection failed — no data available")

            # Map Sigenergy data to standard format (same as Tesla)
            # Power values in kW from Modbus, we keep them in kW for sensors
            dc_solar_kw = attrs.get("pv_power_kw", 0)
            ac_solar_kw = attrs.get("third_party_pv_power_kw", 0)  # AC-coupled via Smart Port
            solar_kw = dc_solar_kw + ac_solar_kw
            grid_kw = attrs.get("grid_power_kw", 0)  # Positive = importing, negative = exporting

            # Sigenergy battery sign convention is OPPOSITE to Tesla:
            # Sigenergy Modbus: Positive = charging (into battery), Negative = discharging (out of battery)
            # Tesla/PowerSync: Positive = discharging (out of battery), Negative = charging (into battery)
            # So we negate the value to match Tesla convention
            battery_kw_raw = attrs.get("battery_power_kw", 0)
            battery_kw = -battery_kw_raw  # Flip sign to match Tesla convention

            evdc_state = await self._async_read_evdc_charger_state()
            evdc_power_kw = (
                evdc_state.power_kw
                if evdc_state and evdc_state.power_kw is not None
                else 0.0
            )

            # Balance-derived Sigenergy load includes DC-side EVDC power. Keep
            # home load separate so EVDC charging/discharge is modeled as an EV
            # branch rather than household demand.
            load_kw = sigenergy_home_load_kw(
                solar_kw=solar_kw,
                grid_kw=grid_kw,
                battery_kw=battery_kw,
                evdc_power_kw=evdc_power_kw,
            )

            # Accumulate daily energy from power readings (with cost tracking)
            buy, sell = _get_current_prices(self.hass, self._entry_id)
            self._energy_acc.update(max(0, solar_kw), grid_kw, battery_kw, load_kw, buy, sell)

            # Rated charge/discharge power — hardware spec, static. Fetch once
            # from the ESS rated power registers via the controller's internal
            # read path, then cache for the lifetime of the coordinator.
            if self._rated_charge_power_kw is None or self._rated_discharge_power_kw is None:
                try:
                    rc_regs = await self._controller._read_input_registers(
                        self._controller.REG_ESS_RATED_CHARGE_POWER, 2
                    )
                    rd_regs = await self._controller._read_input_registers(
                        self._controller.REG_ESS_RATED_DISCHARGE_POWER, 2
                    )
                    if rc_regs and len(rc_regs) >= 2:
                        raw = self._controller._to_unsigned32(rc_regs[0], rc_regs[1])
                        if 0 < raw < 0xFFFFFFFE:
                            self._rated_charge_power_kw = raw / 1000.0
                    if rd_regs and len(rd_regs) >= 2:
                        raw = self._controller._to_unsigned32(rd_regs[0], rd_regs[1])
                        if 0 < raw < 0xFFFFFFFE:
                            self._rated_discharge_power_kw = raw / 1000.0
                except Exception as e:
                    _LOGGER.debug("Sigenergy rated power read failed (will retry): %s", e)

            energy_data = {
                "solar_power": solar_kw,  # kW (DC + AC-coupled)
                "grid_power": grid_kw,  # kW, positive = importing, negative = exporting
                "battery_power": battery_kw,  # kW, positive = discharging, negative = charging
                "load_power": load_kw,  # kW, calculated from energy balance
                "ev_power": evdc_power_kw,  # kW, positive = EV charging, negative = V2X discharge
                "ev_power_kw": evdc_power_kw,
                "ev_charger_type": evdc_state.charger_type if evdc_state else None,
                "ev_charger_status": evdc_state.status if evdc_state else None,
                "ev_charger_connected": evdc_state.is_connected if evdc_state else False,
                "ev_charger_charging": evdc_state.is_charging if evdc_state else False,
                "ev_charger_discharging": evdc_state.is_discharging if evdc_state else False,
                "ev_soc": evdc_state.vehicle_soc if evdc_state else None,
                "battery_level": attrs.get("battery_soc", 0),  # %
                "last_update": dt_util.utcnow(),
                # Extra Sigenergy-specific data
                "active_power_kw": attrs.get("active_power_kw", 0),
                "export_limit_kw": attrs.get("export_limit_kw"),
                "ems_work_mode": attrs.get("ems_work_mode"),
                "is_curtailed": status.is_curtailed,
                "third_party_pv_power_kw": ac_solar_kw,  # AC-coupled solar via Smart Port
                # Battery health data
                "battery_soh": attrs.get("battery_soh"),  # % State of Health
                "battery_capacity_kwh": attrs.get("battery_capacity_kwh"),  # kWh rated capacity
                # Rated BMS power for the mobile force-mode picker's "Max" chip
                "battery_max_charge_power": self._rated_charge_power_kw,
                "battery_max_discharge_power": self._rated_discharge_power_kw,
                "battery_max_charge_power_w": (
                    int(self._rated_charge_power_kw * 1000)
                    if self._rated_charge_power_kw else None
                ),
                "battery_max_discharge_power_w": (
                    int(self._rated_discharge_power_kw * 1000)
                    if self._rated_discharge_power_kw else None
                ),
                "energy_summary": self._energy_acc.as_dict(),
            }

            _LOGGER.debug(
                "Sigenergy data: solar=%.2f kW (dc=%.2f, ac=%.2f), grid=%.2f kW, battery=%.2f kW (%.0f%%), evdc=%.2f kW, load=%.2f kW, curtailed=%s",
                energy_data["solar_power"],
                dc_solar_kw,
                ac_solar_kw,
                energy_data["grid_power"],
                energy_data["battery_power"],
                energy_data["battery_level"],
                energy_data["ev_power"],
                energy_data["load_power"],
                energy_data["is_curtailed"],
            )

            return energy_data

        except Exception as err:
            raise UpdateFailed(f"Error fetching Sigenergy energy data: {err}") from err

    async def set_backup_mode(self) -> bool:
        """Set Sigenergy to STANDBY for IDLE (prevents all charge/discharge)."""
        async with self._controller:
            return await self._controller.set_standby_mode()

    async def set_no_discharge_mode(self) -> bool:
        """Block Sigenergy battery discharge while still allowing battery charge."""
        async with self._controller:
            mode_ok = await self._controller.set_self_consumption_mode()
            limit_ok = await self._controller.set_discharge_rate_limit(0)
            return bool(mode_ok and limit_ok)

    async def restore_no_discharge_mode(self) -> bool:
        """Restore Sigenergy discharge capacity after no-discharge preserve mode."""
        async with self._controller:
            return await self._controller.restore_normal()

    async def restore_work_mode_from_idle(self) -> bool:
        """Restore self-consumption mode after IDLE."""
        async with self._controller:
            return await self._controller.restore_from_standby()

    async def async_shutdown(self) -> None:
        """Disconnect from Sigenergy system on shutdown."""
        await self._controller.disconnect()


class AlphaESSEnergyCoordinator(DataUpdateCoordinator):
    """Coordinator to fetch AlphaESS energy data via Modbus (primary) with
    optional AlphaESS Cloud API fallback.

    AlphaESS hybrid inverter-battery systems (SMILE / Storion) expose a rich
    Modbus TCP register map (slave ID 0x55 by default). Cloud is used only
    when Modbus is unreachable.

    Sign conventions (unlike Sigenergy):
      - Battery power (reg 0126H): NEGATIVE = charging, POSITIVE = discharging
        → already matches PowerSync convention, no flip needed.
      - Grid power (reg 0021H): POSITIVE = importing, NEGATIVE = exporting
        (standard grid-meter convention).
    """

    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        port: int = 502,
        slave_id: int = 85,
        entry_id: str = "",
        max_export_limit_kw: Optional[float] = None,
        cloud_client: Optional[Any] = None,
    ) -> None:
        """Initialize the coordinator.

        Args:
            hass: HomeAssistant instance.
            host: IP address of AlphaESS inverter.
            port: Modbus TCP port (default 502).
            slave_id: Modbus slave ID (default 85 = 0x55).
            entry_id: Config entry ID for price lookups.
            max_export_limit_kw: User-configured DNSP export safety cap.
            cloud_client: Optional AlphaESSCloudClient for telemetry fallback.
        """
        from ..inverters.alphaess import AlphaESSController

        self.host = host
        self.port = port
        self.slave_id = slave_id
        self._entry_id = entry_id
        self._controller = AlphaESSController(
            host, port, slave_id, max_export_limit_kw=max_export_limit_kw
        )
        self._energy_acc = EnergyAccumulator(hass, "alphaess")
        self._cloud = cloud_client
        self._modbus_failures = 0  # Consecutive failures → cloud fallback

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_alphaess_energy",
            update_interval=UPDATE_INTERVAL_ENERGY,
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch AlphaESS data, preferring Modbus and falling back to cloud."""
        if not self._energy_acc._last_update:
            await self._energy_acc.async_restore()

        attrs: dict[str, Any] = {}
        is_curtailed = False
        source = "modbus"

        try:
            status = await self._controller.get_status()
            attrs = status.attributes or {}
            is_curtailed = status.is_curtailed

            if "battery_soc" not in attrs:
                raise UpdateFailed("AlphaESS Modbus returned no battery data")

            self._modbus_failures = 0

        except Exception as modbus_err:
            self._modbus_failures += 1
            _LOGGER.warning(
                "AlphaESS Modbus read failed (%d consecutive): %s",
                self._modbus_failures,
                modbus_err,
            )

            # Try cloud fallback if configured
            if self._cloud is not None:
                try:
                    cloud_data = await self._cloud.get_last_power_data()
                    attrs = _normalize_alphaess_cloud_data(cloud_data)
                    source = "cloud"
                    _LOGGER.info("AlphaESS fell back to cloud telemetry")
                except Exception as cloud_err:
                    _LOGGER.error("AlphaESS cloud fallback also failed: %s", cloud_err)
                    if self.data:
                        return self.data
                    raise UpdateFailed(
                        f"AlphaESS Modbus and cloud both failed: "
                        f"modbus={modbus_err}; cloud={cloud_err}"
                    ) from modbus_err
            else:
                if self.data:
                    return self.data
                raise UpdateFailed(f"AlphaESS Modbus failed: {modbus_err}") from modbus_err

        solar_kw = attrs.get("pv_power_kw", 0) or 0
        grid_kw = attrs.get("grid_power_kw", 0) or 0  # + import, − export
        # AlphaESS battery sign already matches PowerSync: + = discharge, − = charge
        battery_kw = attrs.get("battery_power_kw", 0) or 0

        # Load from balance: solar + grid + battery (with sign conventions above)
        load_kw = solar_kw + grid_kw + battery_kw

        buy, sell = _get_current_prices(self.hass, self._entry_id)
        self._energy_acc.update(max(0, solar_kw), grid_kw, battery_kw, load_kw, buy, sell)

        # BMS-reported power limits (W) — used to default force-mode power and
        # to cap the mobile app slider so users can't request more than the
        # battery can deliver.
        max_charge_w = attrs.get("battery_max_charge_power_w")
        max_discharge_w = attrs.get("battery_max_discharge_power_w")

        energy_data = {
            "solar_power": solar_kw,
            "grid_power": grid_kw,
            "battery_power": battery_kw,
            "load_power": load_kw,
            "battery_level": attrs.get("battery_soc", 0),
            "battery_soh": attrs.get("battery_soh"),
            "battery_capacity_kwh": attrs.get("battery_capacity_kwh"),
            # Expose BMS limits in both W (raw) and kW (display-friendly)
            "battery_max_charge_power_w": max_charge_w,
            "battery_max_discharge_power_w": max_discharge_w,
            "battery_max_charge_power": (max_charge_w / 1000.0) if max_charge_w else None,
            "battery_max_discharge_power": (max_discharge_w / 1000.0) if max_discharge_w else None,
            "export_limit_percent": attrs.get("export_limit_percent"),
            "is_curtailed": is_curtailed,
            "work_mode_raw": attrs.get("work_mode_raw"),
            "data_source": source,
            "last_update": dt_util.utcnow(),
            "energy_summary": self._energy_acc.as_dict(),
        }

        _LOGGER.debug(
            "AlphaESS (%s): solar=%.2f kW, grid=%.2f kW, battery=%.2f kW (%.1f%%), "
            "load=%.2f kW, curtailed=%s",
            source,
            energy_data["solar_power"],
            energy_data["grid_power"],
            energy_data["battery_power"],
            energy_data["battery_level"],
            energy_data["load_power"],
            energy_data["is_curtailed"],
        )
        return energy_data

    async def set_backup_mode(self) -> bool:
        """IDLE hold — release dispatch but write zero-power dispatch if needed."""
        async with self._controller:
            return await self._controller.set_standby_mode()

    async def restore_work_mode_from_idle(self) -> bool:
        """Restore self-consumption after IDLE hold."""
        async with self._controller:
            return await self._controller.restore_from_standby()

    # Safety floor when no BMS reading is available (e.g. first poll hasn't
    # completed). SMILE5 rated power, well inside every supported model's
    # BMS limit. The controller further clamps against 0x012C/0x012D.
    _DEFAULT_FORCE_POWER_W = 5000.0

    def _resolve_force_power_w(self, requested_w: float, direction: str) -> float:
        """Pick the force-mode power to actually write.

        - If the caller passed a positive value, use it (controller clamps to BMS max).
        - Otherwise, read the last BMS-reported max from self.data
          (battery_max_charge_power_w / battery_max_discharge_power_w).
        - If the BMS value isn't available yet, fall back to _DEFAULT_FORCE_POWER_W.

        Args:
            requested_w: Power from the caller (mobile app / service call).
            direction: "charge" or "discharge" — selects which BMS field to read.
        """
        if requested_w and requested_w > 0:
            return float(requested_w)

        field = (
            "battery_max_charge_power_w"
            if direction == "charge"
            else "battery_max_discharge_power_w"
        )
        bms_w = (self.data or {}).get(field)
        if bms_w and bms_w > 0:
            _LOGGER.info(
                "AlphaESS: caller passed power_w<=0, auto-defaulting to BMS %s max = %.0f W",
                direction, bms_w,
            )
            return float(bms_w)

        _LOGGER.warning(
            "AlphaESS: no BMS %s power reading available yet — using safety default %.0f W",
            direction, self._DEFAULT_FORCE_POWER_W,
        )
        return self._DEFAULT_FORCE_POWER_W

    async def force_charge(self, duration_min: int = 30, power_w: float = 0.0) -> bool:
        """Force-charge the battery via the Note29 dispatch block.

        Args:
            duration_min: Force-mode duration in minutes. Passed down to Para6
                as seconds — the inverter auto-stops when the timer elapses.
                HA also runs its own expiry timer as a belt-and-braces fallback.
            power_w: Charge power in watts (positive). 0 or negative falls back
                to the BMS-reported max charge power, then to a 5 kW safety
                default if the BMS reading isn't available yet.
        """
        power_w = self._resolve_force_power_w(power_w, "charge")
        duration_seconds = max(60, int(duration_min) * 60)
        _LOGGER.info(
            "AlphaESS coordinator: force_charge(power_w=%.0f, duration=%dm/%ds)",
            power_w, duration_min, duration_seconds,
        )
        async with self._controller:
            return await self._controller.force_charge(
                power_kw=power_w / 1000.0,
                duration_seconds=duration_seconds,
            )

    async def force_discharge(self, duration_min: int = 30, power_w: float = 0.0) -> bool:
        """Force-discharge the battery via the Note29 dispatch block.

        Same fallback chain as force_charge — see its docstring.
        """
        power_w = self._resolve_force_power_w(power_w, "discharge")
        duration_seconds = max(60, int(duration_min) * 60)
        _LOGGER.info(
            "AlphaESS coordinator: force_discharge(power_w=%.0f, duration=%dm/%ds)",
            power_w, duration_min, duration_seconds,
        )
        async with self._controller:
            return await self._controller.force_discharge(
                power_kw=power_w / 1000.0,
                duration_seconds=duration_seconds,
            )

    async def restore_normal(self) -> bool:
        """Release dispatch and restore export limit to normal."""
        _LOGGER.info("AlphaESS coordinator: restore_normal")
        async with self._controller:
            return await self._controller.restore_normal()

    async def async_shutdown(self) -> None:
        """Release dispatch and disconnect on shutdown.

        AlphaESS has no auto-revert: if we leave 0722H=1, the battery stays
        locked in forced mode. We must release dispatch before dropping the
        connection (disconnect itself is intentionally pure — see the
        controller's disconnect() docstring for why).
        """
        try:
            await self._controller.release_dispatch()
        except Exception as e:
            _LOGGER.warning("AlphaESS release_dispatch on shutdown failed: %s", e)
        await self._controller.disconnect()
        if self._cloud is not None:
            try:
                await self._cloud.close()
            except Exception:
                pass


def _normalize_alphaess_cloud_data(cloud_data: dict) -> dict:
    """Translate AlphaESS cloud getLastPowerData response to Modbus-shaped attrs.

    Cloud fields (per AlphaESS Open API):
      - ppv:   PV power (W, positive)
      - pgrid: grid power (W, + import)
      - pbat:  battery power (W) — cloud convention has been observed as
               + discharge / − charge (same as Modbus 0126H); kept without flip.
      - soc:   battery state of charge (%)
    """
    attrs: dict[str, Any] = {}
    if not isinstance(cloud_data, dict):
        return attrs

    ppv = cloud_data.get("ppv")
    if isinstance(ppv, (int, float)):
        attrs["pv_power_w"] = ppv
        attrs["pv_power_kw"] = round(ppv / 1000.0, 3)

    pgrid = cloud_data.get("pgrid")
    if isinstance(pgrid, (int, float)):
        attrs["grid_power_w"] = pgrid
        attrs["grid_power_kw"] = round(pgrid / 1000.0, 3)

    pbat = cloud_data.get("pbat")
    if isinstance(pbat, (int, float)):
        attrs["battery_power_w"] = pbat
        attrs["battery_power_kw"] = round(pbat / 1000.0, 3)

    soc = cloud_data.get("soc")
    if isinstance(soc, (int, float)):
        attrs["battery_soc"] = round(float(soc), 1)

    return attrs


class SungrowEnergyCoordinator(DataUpdateCoordinator):
    """Coordinator to fetch Sungrow SH-series battery system data via Modbus.

    Polls the Sungrow hybrid inverter via Modbus TCP to get real-time
    power data (solar, battery, grid, load), battery SOC/SOH, and control settings.
    """

    _TEMPORARY_DISCHARGE_CAP_MAX_KW = 0.1
    _OPTIMIZATION_MAX_DISCHARGE_W_KEY = "optimization_max_discharge_w"
    _BLOCKED_DISCHARGE_IMPORT_KW = 0.15
    _BLOCKED_DISCHARGE_LOAD_KW = 0.15
    _BLOCKED_DISCHARGE_GRID_LOAD_RATIO = 0.6
    _BLOCKED_DISCHARGE_BATTERY_KW = 0.1
    _BLOCKED_DISCHARGE_RESERVE_MARGIN = 2.0
    _EXPORT_CONTROL_STORAGE_VERSION = 1

    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        port: int = 502,
        slave_id: int = 1,
        entry_id: str = "",
    ) -> None:
        """Initialize the coordinator.

        Args:
            hass: HomeAssistant instance
            host: IP address of Sungrow inverter
            port: Modbus TCP port (default: 502)
            slave_id: Modbus slave ID (default: 1)
            entry_id: Config entry ID for price lookups
        """
        from ..inverters.sungrow_sh import SungrowSHController

        self.host = host
        self.port = port
        self.slave_id = slave_id
        self._entry_id = entry_id
        self._controller = SungrowSHController(host, port, slave_id)
        self._energy_acc = EnergyAccumulator(hass, "sungrow")
        self._export_control_store = (
            Store(
                hass,
                self._EXPORT_CONTROL_STORAGE_VERSION,
                f"{DOMAIN}.sungrow_export_control.{entry_id}",
            )
            if entry_id
            else None
        )
        # Sungrow/WiNet Modbus is sensitive to overlapping TCP operations.
        # Keep each coordinator poll or control command as one serialized
        # transaction so a refresh cannot close/reopen the shared client in the
        # middle of a force charge/discharge sequence.
        self._modbus_lock = asyncio.Lock()

        # Midnight baselines for computing daily import/export from total registers
        # Used when daily registers (13035/13044) read 0 (e.g. SH10RS + SBH)
        self._total_import_baseline: float | None = None
        self._total_export_baseline: float | None = None
        self._baseline_date: str | None = None  # ISO date string
        self._pre_control_charge_limit_kw: float | None = None
        self._pre_control_discharge_limit_kw: float | None = None
        self._pre_control_export_limit_w: int | None = None
        self._pre_control_export_limit_captured = False

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_sungrow_energy",
            update_interval=UPDATE_INTERVAL_ENERGY,
        )

    def _update_total_baselines(self, data: dict) -> None:
        """Track midnight baselines for total import/export registers.

        Some Sungrow systems (e.g. SH10RS + SBH) have no working daily
        import/export registers — they permanently read 0.  We derive
        daily values from the total (lifetime) registers by subtracting
        a baseline captured at midnight (or on first read of the day).
        """
        today = dt_util.now().date().isoformat()
        total_import = data.get("total_import")
        total_export = data.get("total_export")

        if self._baseline_date != today:
            # New day — capture baselines from current total values
            if total_import is not None:
                self._total_import_baseline = total_import
            if total_export is not None:
                self._total_export_baseline = total_export
            self._baseline_date = today
            _LOGGER.info(
                "Sungrow daily baseline reset: import=%.1f export=%.1f kWh (total)",
                self._total_import_baseline or 0, self._total_export_baseline or 0,
            )

    def _build_energy_summary(self, data: dict) -> dict:
        """Build energy summary using Sungrow register-based daily values.

        The inverter tracks daily energy counters in hardware, which are more
        reliable than the software accumulator (immune to transient bad reads
        from firmware that returns garbage for S32 power registers).

        Falls back to the accumulator for any values the registers don't provide
        (e.g. cost tracking).
        """
        summary = self._energy_acc.as_dict()

        # Override kWh counters with register-based values when available.
        # Some Sungrow systems have no external energy meter paired, so the
        # daily import/export registers (13035/13044) permanently read 0.
        # Detect this by checking whether the register reads 0 while the
        # software accumulator has already recorded energy — if so, try
        # deriving daily values from the total (lifetime) registers.
        daily_pv = data.get("daily_pv_generation")
        daily_import = data.get("daily_import")
        daily_export = data.get("daily_export")
        daily_discharge = data.get("daily_battery_discharge")
        daily_charge = data.get("daily_battery_charge")

        # Update midnight baselines for total register delta method
        self._update_total_baselines(data)

        if daily_pv is not None:
            summary["pv_today_kwh"] = daily_pv
        else:
            # No daily PV register (e.g. FoxESS) — use energy accumulator
            summary["pv_today_kwh"] = self._energy_acc.solar_kwh
        # For import/export: prefer daily register → total delta → accumulator
        if daily_import is not None and daily_import > 0:
            summary["grid_import_today_kwh"] = daily_import
        else:
            # Daily register missing or 0 — derive from total register delta
            total_import = data.get("total_import")
            if total_import is not None and self._total_import_baseline is not None:
                derived = round(total_import - self._total_import_baseline, 2)
                if derived >= 0:
                    summary["grid_import_today_kwh"] = derived
            # else: keep accumulator value (already in summary)

        if daily_export is not None and daily_export > 0:
            summary["grid_export_today_kwh"] = daily_export
        else:
            # Daily register missing or 0 — derive from total register delta
            total_export = data.get("total_export")
            if total_export is not None and self._total_export_baseline is not None:
                derived = round(total_export - self._total_export_baseline, 2)
                if derived >= 0:
                    summary["grid_export_today_kwh"] = derived
            # else: keep accumulator value (already in summary)
        if daily_discharge is not None:
            summary["discharge_today_kwh"] = daily_discharge
        if daily_charge is not None:
            summary["charge_today_kwh"] = daily_charge

        # Use the final (possibly corrected) import/export values for load calc
        final_import = summary.get("grid_import_today_kwh", 0)
        final_export = summary.get("grid_export_today_kwh", 0)

        # Calculate daily load from energy balance (no register for this)
        if all(v is not None for v in (daily_pv, daily_discharge, daily_charge)):
            summary["load_today_kwh"] = round(max(0,
                daily_pv + final_import + (daily_discharge or 0) - final_export - (daily_charge or 0)
            ), 2)

        # Recompute daily avg using possibly-overridden load from hardware registers
        load_kwh = summary.get("load_today_kwh", 0.0) or 0.0
        if load_kwh > 0:
            import_cost = summary.get("import_cost_today", 0.0) or 0.0
            export_earn = summary.get("export_earnings_today", 0.0) or 0.0
            summary["avg_cost_per_kwh_today"] = round((import_cost - export_earn) / load_kwh, 4)
        else:
            summary["avg_cost_per_kwh_today"] = None

        return summary

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from Sungrow system via Modbus."""
        if not self._energy_acc._last_update:
            await self._energy_acc.async_restore()
        try:
            async with self._modbus_lock:
                data = await self._controller.get_battery_data()

            # If Modbus returned no battery data, keep previous readings
            # rather than reporting SOC=0% which causes the optimizer to
            # incorrectly schedule IDLE (thinking the battery is empty).
            if "battery_soc" not in data:
                if self.data:
                    _LOGGER.warning(
                        "Sungrow Modbus returned no battery data — keeping previous readings"
                    )
                    return self.data
                raise UpdateFailed("Sungrow Modbus connection failed — no data available")

            # Map Sungrow data to standard format
            battery_power_w = data.get("battery_power", 0)  # Signed: positive = discharging
            export_power_w = data.get("export_power", 0)  # Signed: positive = exporting
            meter_power_w = data.get("meter_power")  # Signed: positive = importing, negative = exporting
            load_power_w = data.get("load_power")
            pv_power_w = data.get("pv_power")  # Direct PV DC power from register 5017-5018

            # Convert to kW for consistency with other coordinators
            battery_kw = battery_power_w / 1000
            if meter_power_w is not None:
                grid_kw = meter_power_w / 1000
            else:
                grid_kw = -export_power_w / 1000  # Invert: positive = importing, negative = exporting
            load_kw = (load_power_w or 0) / 1000

            # Use direct PV reading if available; otherwise calculate from energy balance
            if pv_power_w is not None:
                solar_kw = max(0, pv_power_w / 1000)
                # Derive load from energy balance: Load = Solar + Grid_Import + Battery_Discharge
                # (more reliable than the load register on some firmware)
                calc_load_kw = max(0.0, solar_kw + grid_kw + battery_kw)
                no_pv_load_kw = max(0.0, grid_kw + battery_kw)
                pv_tracks_battery_discharge = (
                    _is_night_for_solar_telemetry(self.hass)
                    and solar_kw > 0.05
                    and battery_kw > 0.05
                    and abs(solar_kw - battery_kw) <= max(0.1, battery_kw * 0.1)
                )
                if pv_tracks_battery_discharge:
                    aliased_solar_kw = solar_kw
                    _LOGGER.debug(
                        "Sungrow SH PV register appears to be reporting battery discharge power "
                        "at night (pv=%.2fkW battery=%.2fkW grid=%.2fkW load=%.2fkW); "
                        "using zero solar and inferred home load %.2fkW",
                        solar_kw,
                        battery_kw,
                        grid_kw,
                        load_kw,
                        no_pv_load_kw,
                    )
                    solar_kw = 0.0
                    inflated_load_floor = no_pv_load_kw + max(
                        0.1, aliased_solar_kw * 0.5
                    )
                    if (
                        load_power_w is None
                        or load_kw <= 0.01
                        or load_kw > inflated_load_floor
                    ):
                        load_kw = no_pv_load_kw
                    calc_load_kw = no_pv_load_kw
                if abs(load_kw) > 100:
                    # Load register is garbage, use calculated value
                    load_kw = calc_load_kw
                elif load_power_w is None or (load_kw <= 0.01 and calc_load_kw > 0.05):
                    # Some Sungrow firmware reports the load register as 0 W
                    # while PV/grid/battery registers still describe real load.
                    load_kw = calc_load_kw
            else:
                # Fallback: estimate solar from energy balance
                solar_kw = max(0, load_kw - grid_kw - battery_kw)

            ac_inverter_kw = _configured_ac_inverter_power_kw(self.hass, self._entry_id)
            if ac_inverter_kw > 0:
                combined_load_kw = max(0.0, solar_kw + ac_inverter_kw + grid_kw + battery_kw)
                if combined_load_kw > load_kw:
                    load_kw = combined_load_kw

            # Accumulate daily energy from power readings (with cost tracking)
            buy, sell = _get_current_prices(self.hass, self._entry_id)
            self._energy_acc.update(max(0, solar_kw), grid_kw, battery_kw, load_kw, buy, sell)

            # Sanity-check SOC — 0xFFFF (6553.5%) means Modbus returned invalid data
            raw_soc = data.get("battery_soc", 0)
            if raw_soc > 100:
                _LOGGER.warning(
                    "Sungrow returned invalid SOC=%.1f%% (possible Modbus conflict). "
                    "Check for other integrations using port 502.",
                    raw_soc,
                )
                raw_soc = 0

            energy_data = {
                "solar_power": max(0, solar_kw),  # kW, clamp to 0 if calculated negative
                "grid_power": grid_kw,  # kW, positive = importing, negative = exporting
                "battery_power": battery_kw,  # kW, positive = discharging, negative = charging
                "load_power": load_kw,  # kW
                "battery_level": raw_soc,  # %
                "last_update": dt_util.utcnow(),
                # Sungrow-specific data
                "battery_soh": data.get("battery_soh"),  # % State of Health
                "battery_voltage": data.get("battery_voltage"),
                "battery_current": data.get("battery_current"),
                "battery_temp": data.get("battery_temp"),
                "inverter_temperature": data.get("inverter_temperature"),
                "ems_mode": data.get("ems_mode"),
                "ems_mode_name": data.get("ems_mode_name"),
                "charge_cmd": data.get("charge_cmd"),
                "min_soc": data.get("min_soc"),
                "max_soc": data.get("max_soc"),
                "backup_reserve": data.get("backup_reserve"),
                "charge_rate_limit_kw": data.get("charge_rate_limit_kw"),
                "discharge_rate_limit_kw": data.get("discharge_rate_limit_kw"),
                "bms_max_discharge_current_a": data.get(
                    "bms_max_discharge_current_a"
                ),
                "discharge_rate_limit_source": data.get(
                    "discharge_rate_limit_source"
                ),
                "export_limit_w": data.get("export_limit_w"),
                "export_limit_enabled": data.get("export_limit_enabled"),
                "meter_power": meter_power_w,
                "ac_inverter_solar_power": ac_inverter_kw,
                # Aliases for the mobile force-mode picker's Max chip.
                # The *_rate_limit_kw values already reflect BMS-reported
                # current × voltage, so reuse them rather than duplicate.
                "battery_max_charge_power": data.get("charge_rate_limit_kw"),
                "battery_max_discharge_power": data.get("discharge_rate_limit_kw"),
                "battery_max_charge_power_w": (
                    int(data["charge_rate_limit_kw"] * 1000)
                    if data.get("charge_rate_limit_kw") else None
                ),
                "battery_max_discharge_power_w": (
                    int(data["discharge_rate_limit_kw"] * 1000)
                    if data.get("discharge_rate_limit_kw") else None
                ),
                "energy_summary": self._build_energy_summary(data),
            }

            es = energy_data["energy_summary"]
            _LOGGER.debug(
                "Sungrow data: solar=%.2f kW, grid=%.2f kW, battery=%.2f kW (%.0f%%), load=%.2f kW | "
                "daily: pv=%.2f import=%.2f export=%.2f charge=%.2f discharge=%.2f load=%.2f kWh",
                energy_data["solar_power"],
                energy_data["grid_power"],
                energy_data["battery_power"],
                energy_data["battery_level"],
                energy_data["load_power"],
                es.get("pv_today_kwh", 0),
                es.get("grid_import_today_kwh", 0),
                es.get("grid_export_today_kwh", 0),
                es.get("charge_today_kwh", 0),
                es.get("discharge_today_kwh", 0),
                es.get("load_today_kwh", 0),
            )

            return energy_data

        except Exception as err:
            raise UpdateFailed(f"Error fetching Sungrow energy data: {err}") from err

    # Battery control methods - delegate to controller
    async def force_charge(self, duration_minutes: int = 30, power_w: float = 0) -> bool:
        """Set Sungrow to forced charge mode.

        Args:
            duration_minutes: Duration in minutes (not used by Sungrow - charge until manually stopped)
            power_w: Target forced charge power in watts.

        Returns:
            True if successful
        """
        async with self._modbus_lock, self._controller:
            target_power_w = power_w if power_w > 0 else 5000
            return await self._controller.force_charge(power_w=target_power_w)

    async def force_discharge(self, duration_minutes: int = 30, power_w: float = 0) -> bool:
        """Set Sungrow to forced discharge mode.

        Args:
            duration_minutes: Duration in minutes (not used by Sungrow - discharge until manually stopped)
            power_w: Target forced discharge power in watts.

        Returns:
            True if successful
        """
        async with self._modbus_lock, self._controller:
            target_power_w = power_w if power_w > 0 else 5000
            return await self._controller.force_discharge(power_w=target_power_w)

    async def force_grid_export(
        self,
        duration_minutes: int = 30,
        export_limit_w: float = 0,
    ) -> bool:
        """Force battery discharge while limiting grid export separately.

        Spread-export wants a target grid export rate, not a lower inverter
        discharge ceiling. Keep the battery discharge cap at the normal inverter
        limit so home load spikes can still be served by the battery, and use
        Sungrow's export-limit register to constrain export to grid.
        """
        async with self._modbus_lock, self._controller:
            target_export_w = max(0, int(round(export_limit_w or 0)))

            await self._capture_export_limit_for_restore()
            await self._capture_discharge_limit_for_restore()

            normal_limit_kw = await self._resolve_normal_discharge_limit_kw()
            if normal_limit_kw is None or normal_limit_kw <= 0:
                normal_limit_kw = max(target_export_w / 1000.0, 5.0)
            configured_limit_kw = self._configured_optimization_discharge_limit_kw()
            if (
                configured_limit_kw is not None
                and configured_limit_kw > 0
                and normal_limit_kw > configured_limit_kw
            ):
                _LOGGER.info(
                    "Sungrow spread export: clamping discharge headroom from %.2fkW "
                    "to configured max %.2fkW",
                    normal_limit_kw,
                    configured_limit_kw,
                )
                normal_limit_kw = configured_limit_kw

            if not await self._persist_export_control_state(target_export_w):
                return False

            forced_power_w = int(round(normal_limit_kw * 1000))
            limit_changed = False
            export_limit_changed = False
            try:
                if getattr(self._controller, "rate_limit_writable", None) is False:
                    self._pre_control_discharge_limit_kw = None
                    _LOGGER.debug(
                        "Sungrow spread export: discharge limit register already known "
                        "not writable; continuing with grid export limit only"
                    )
                else:
                    limit_changed = await self._controller.set_discharge_rate_limit(normal_limit_kw)
                    if not limit_changed:
                        if getattr(self._controller, "rate_limit_writable", None) is False:
                            self._pre_control_discharge_limit_kw = None
                            _LOGGER.warning(
                                "Sungrow spread export: discharge limit register is not writable; "
                                "continuing with grid export limit only"
                            )
                        else:
                            _LOGGER.warning(
                                "Sungrow spread export: failed to set discharge limit to %.2fkW",
                                normal_limit_kw,
                            )
                            await self._restore_captured_export_limit()
                            return False

                export_limit_changed = await self._controller.set_export_limit(target_export_w)
                if not export_limit_changed:
                    _LOGGER.warning(
                        "Sungrow spread export: failed to set grid export limit to %dW",
                        target_export_w,
                    )
                    await self._restore_captured_export_limit()
                    await self._restore_captured_discharge_limit()
                    return False

                result = await self._controller.force_discharge(power_w=forced_power_w)
            except Exception:
                await self._restore_captured_export_limit()
                if limit_changed:
                    await self._restore_captured_discharge_limit()
                raise

            if not result:
                await self._restore_captured_export_limit()
                await self._restore_captured_discharge_limit()

            return result

    async def restore_normal(self) -> bool:
        """Restore Sungrow to self-consumption mode.

        Returns:
            True if successful
        """
        async with self._modbus_lock, self._controller:
            normal_ok = await self._controller.restore_normal()
            export_limit_ok = await self._restore_captured_export_limit()
            charge_limit_ok = await self._restore_captured_charge_limit()
            limit_ok = await self._restore_captured_discharge_limit()
            return bool(normal_ok and export_limit_ok and charge_limit_ok and limit_ok)

    async def set_max_soc(self, percent: int) -> bool:
        """Set maximum battery SOC percentage.

        Args:
            percent: Maximum SOC percentage (0-100)

        Returns:
            True if successful
        """
        async with self._modbus_lock, self._controller:
            return await self._controller.set_max_soc(percent)

    async def set_backup_reserve(self, percent: int) -> bool:
        """Set backup reserve percentage.

        Args:
            percent: Backup reserve SOC percentage (0-100)

        Returns:
            True if successful
        """
        async with self._modbus_lock, self._controller:
            return await self._controller.set_backup_reserve(percent)

    async def set_backup_mode(self) -> bool:
        """Block Sungrow discharge for IDLE while still allowing battery charge."""
        async with self._modbus_lock, self._controller:
            await self._capture_discharge_limit_for_restore()
            limit_ok = await self._controller.set_discharge_rate_limit(0)
            if not limit_ok:
                # Some Sungrow firmware exposes 10 W as the minimum writable
                # discharge cap. Use that as a near-zero fallback.
                limit_ok = await self._controller.set_discharge_rate_limit(0.01)
            return bool(limit_ok)

    async def set_no_discharge_mode(self) -> bool:
        """Block Sungrow battery discharge while still allowing battery charge."""
        async with self._modbus_lock, self._controller:
            await self._capture_discharge_limit_for_restore()
            limit_ok = await self._controller.set_discharge_rate_limit(0)
            if not limit_ok:
                limit_ok = await self._controller.set_discharge_rate_limit(0.01)
            return bool(limit_ok)

    async def restore_no_discharge_mode(self) -> bool:
        """Restore Sungrow from scheduled EV no-discharge preserve mode."""
        async with self._modbus_lock, self._controller:
            normal_ok = await self._controller.restore_normal()
            limit_ok = await self._restore_captured_discharge_limit()
            return bool(normal_ok and limit_ok)

    async def _capture_discharge_limit_for_restore(self) -> None:
        """Save the normal Sungrow discharge limit before a temporary cap."""
        if getattr(self, "_pre_control_discharge_limit_kw", None) is not None:
            return

        try:
            current_limit_kw = await self._resolve_normal_discharge_limit_kw()
            if current_limit_kw is not None:
                self._pre_control_discharge_limit_kw = current_limit_kw
        except Exception as err:
            _LOGGER.debug(
                "Could not capture Sungrow discharge limit before temporary cap: %s",
                err,
            )

    async def _capture_export_limit_for_restore(self) -> None:
        """Save the current Sungrow export limit before a temporary target."""
        if getattr(self, "_pre_control_export_limit_captured", False):
            return

        export_limit_w: int | None = None
        export_limit_enabled: bool | None = None

        coord_data = getattr(self, "data", None) or {}
        if "export_limit_enabled" in coord_data:
            export_limit_enabled = bool(coord_data.get("export_limit_enabled"))
        if coord_data.get("export_limit_w") is not None:
            try:
                export_limit_w = int(float(coord_data.get("export_limit_w")))
            except (TypeError, ValueError):
                export_limit_w = None

        if export_limit_enabled is None or (export_limit_enabled and export_limit_w is None):
            try:
                live_data = await self._controller.get_battery_data()
            except Exception as err:
                _LOGGER.debug(
                    "Could not read live Sungrow export limit for restore target: %s",
                    err,
                )
            else:
                if "export_limit_enabled" in live_data:
                    export_limit_enabled = bool(live_data.get("export_limit_enabled"))
                if live_data.get("export_limit_w") is not None:
                    try:
                        export_limit_w = int(float(live_data.get("export_limit_w")))
                    except (TypeError, ValueError):
                        export_limit_w = None

        self._pre_control_export_limit_w = (
            export_limit_w if export_limit_enabled and export_limit_w is not None else None
        )
        self._pre_control_export_limit_captured = True

    async def _persist_export_control_state(self, target_export_w: int) -> bool:
        """Persist temporary Sungrow export ownership before changing registers."""
        store = getattr(self, "_export_control_store", None)
        if store is None:
            return True

        baseline_limit_w = getattr(self, "_pre_control_export_limit_w", None)
        state = {
            "active": True,
            "baseline_enabled": baseline_limit_w is not None,
            "baseline_limit_w": baseline_limit_w,
            "target_export_w": int(target_export_w),
        }
        try:
            await store.async_save(state)
        except Exception as err:
            _LOGGER.warning(
                "Could not persist Sungrow temporary export state; refusing control write: %s",
                err,
            )
            return False
        return True

    async def _clear_persisted_export_control_state(self) -> bool:
        """Clear temporary Sungrow export ownership after registers are restored."""
        store = getattr(self, "_export_control_store", None)
        if store is None:
            return True

        try:
            await store.async_save({"active": False})
        except Exception as err:
            _LOGGER.warning(
                "Could not clear persisted Sungrow temporary export state: %s",
                err,
            )
            return False
        return True

    async def async_restore_persisted_export_control(self) -> bool:
        """Recover an optimizer-owned Sungrow export limit after a reload."""
        store = getattr(self, "_export_control_store", None)
        if store is None:
            return True

        try:
            state = await store.async_load()
        except Exception as err:
            _LOGGER.warning("Could not load persisted Sungrow export state: %s", err)
            return False

        if not isinstance(state, dict) or not state.get("active"):
            return True

        baseline_limit_w: int | None = None
        if state.get("baseline_enabled"):
            try:
                baseline_limit_w = int(round(float(state.get("baseline_limit_w"))))
            except (TypeError, ValueError):
                _LOGGER.warning(
                    "Persisted Sungrow export state has an invalid enabled baseline; "
                    "leaving it intact for recovery"
                )
                return False
            if baseline_limit_w < 0:
                _LOGGER.warning(
                    "Persisted Sungrow export state has a negative baseline; "
                    "leaving it intact for recovery"
                )
                return False

        self._pre_control_export_limit_w = baseline_limit_w
        self._pre_control_export_limit_captured = True
        _LOGGER.warning(
            "Recovering interrupted Sungrow temporary export control (target=%sW, baseline=%s)",
            state.get("target_export_w"),
            f"{baseline_limit_w}W" if baseline_limit_w is not None else "disabled",
        )
        try:
            return await self.restore_normal()
        except Exception as err:
            _LOGGER.warning(
                "Could not restore interrupted Sungrow temporary export control; "
                "the persisted recovery state was retained: %s",
                err,
            )
            return False

    async def _resolve_normal_discharge_limit_kw(self) -> float | None:
        """Resolve the Sungrow discharge cap to restore for self-consumption.

        Sungrow's writable max-discharge register is both the current cap and the
        value we temporarily lower for manual force discharge. Prefer the highest
        known normal limit so self-consumption does not inherit a lower optimiser
        or manual cap.
        """
        candidates: list[float] = []

        def add_kw(value: Any) -> None:
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                return
            if parsed > self._TEMPORARY_DISCHARGE_CAP_MAX_KW:
                candidates.append(parsed)

        def add_w(value: Any) -> None:
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                return
            if parsed / 1000.0 > self._TEMPORARY_DISCHARGE_CAP_MAX_KW:
                candidates.append(parsed / 1000.0)

        coord_data = getattr(self, "data", None) or {}
        add_kw(coord_data.get("battery_max_discharge_power"))
        add_w(coord_data.get("battery_max_discharge_power_w"))
        add_kw(coord_data.get("discharge_rate_limit_kw"))
        add_kw(coord_data.get("battery_max_charge_power"))
        add_w(coord_data.get("battery_max_charge_power_w"))
        add_kw(coord_data.get("charge_rate_limit_kw"))
        add_kw(self._configured_optimization_discharge_limit_kw())

        try:
            live_data = await self._controller.get_battery_data()
        except Exception as err:
            _LOGGER.debug("Could not read live Sungrow limits for restore target: %s", err)
        else:
            add_kw(live_data.get("discharge_rate_limit_kw"))
            add_kw(live_data.get("charge_rate_limit_kw"))

        return max(candidates) if candidates else None

    async def _capture_charge_limit_for_restore(self) -> None:
        """Save the current Sungrow charge limit before a temporary cap."""
        if getattr(self, "_pre_control_charge_limit_kw", None) is not None:
            return

        current_limit_kw = None
        coord_data = getattr(self, "data", None) or {}
        try:
            current_limit_kw = coord_data.get("battery_max_charge_power")
            charge_limit_w = coord_data.get("battery_max_charge_power_w")
            if current_limit_kw is None and charge_limit_w:
                current_limit_kw = float(charge_limit_w) / 1000.0
            if current_limit_kw is None:
                live_data = await self._controller.get_battery_data()
                current_limit_kw = live_data.get("charge_rate_limit_kw")
            if current_limit_kw is not None and float(current_limit_kw) > 0:
                self._pre_control_charge_limit_kw = float(current_limit_kw)
        except Exception as err:
            _LOGGER.debug(
                "Could not capture Sungrow charge limit before temporary cap: %s",
                err,
            )

    async def _restore_captured_charge_limit(self) -> bool:
        """Restore a Sungrow charge limit saved before temporary control."""
        restore_limit_kw = getattr(self, "_pre_control_charge_limit_kw", None)
        if restore_limit_kw is None or restore_limit_kw <= 0:
            return True

        limit_ok = await self._controller.set_charge_rate_limit(restore_limit_kw)
        if limit_ok:
            self._pre_control_charge_limit_kw = None
        return bool(limit_ok)

    async def _restore_captured_discharge_limit(self) -> bool:
        """Restore a Sungrow discharge limit saved before temporary control."""
        captured_limit_kw = getattr(self, "_pre_control_discharge_limit_kw", None)
        if captured_limit_kw is None:
            return await self._restore_stale_low_discharge_limit()

        if getattr(self._controller, "rate_limit_writable", None) is False:
            _LOGGER.debug(
                "Retrying Sungrow discharge limit restore despite a previous failed write"
            )

        restore_limit_kw = await self._resolve_normal_discharge_limit_kw()
        if restore_limit_kw is None:
            restore_limit_kw = captured_limit_kw
        else:
            restore_limit_kw = max(restore_limit_kw, captured_limit_kw)
        restore_limit_kw = self._clamp_discharge_restore_limit_kw(restore_limit_kw)
        if restore_limit_kw is None or restore_limit_kw <= 0:
            return True

        limit_ok = await self._controller.set_discharge_rate_limit(restore_limit_kw)
        if limit_ok:
            self._pre_control_discharge_limit_kw = None
        else:
            _LOGGER.warning(
                "Sungrow discharge limit restore to %.2fkW failed; keeping restore target for retry",
                restore_limit_kw,
            )
        return bool(limit_ok)

    async def _restore_stale_low_discharge_limit(self) -> bool:
        """Repair a stale near-zero Sungrow discharge cap after reload/restart."""
        current_limit_kw = await self._read_current_discharge_limit_kw()
        blocked_without_cap_read = (
            current_limit_kw is None and self._discharge_appears_blocked_after_restore()
        )
        if (
            (current_limit_kw is None and not blocked_without_cap_read)
            or (current_limit_kw is not None and current_limit_kw <= 0)
            or (
                current_limit_kw is not None
                and current_limit_kw > self._TEMPORARY_DISCHARGE_CAP_MAX_KW
            )
        ):
            return True

        restore_limit_kw = await self._resolve_normal_discharge_limit_kw()
        current_limit_label = (
            f"{current_limit_kw:.2f}kW"
            if current_limit_kw is not None
            else "unknown"
        )
        restore_source_label = (
            current_limit_label
            if current_limit_kw is not None
            else "from blocked telemetry"
        )
        if restore_limit_kw is None or restore_limit_kw <= self._TEMPORARY_DISCHARGE_CAP_MAX_KW:
            _LOGGER.warning(
                "Sungrow discharge limit is still %s after restore, "
                "but no safe normal discharge limit could be resolved",
                current_limit_label,
            )
            return True
        restore_limit_kw = self._clamp_discharge_restore_limit_kw(restore_limit_kw)
        if restore_limit_kw is None or restore_limit_kw <= self._TEMPORARY_DISCHARGE_CAP_MAX_KW:
            return True

        _LOGGER.info(
            "Restoring stale Sungrow discharge cap %s to %.2fkW",
            restore_source_label,
            restore_limit_kw,
        )
        self._pre_control_discharge_limit_kw = restore_limit_kw
        limit_ok = await self._controller.set_discharge_rate_limit(restore_limit_kw)
        if limit_ok:
            self._pre_control_discharge_limit_kw = None
        else:
            _LOGGER.warning(
                "Sungrow stale discharge cap repair to %.2fkW failed; keeping restore target for retry",
                restore_limit_kw,
            )
        return bool(limit_ok)

    def _clamp_discharge_restore_limit_kw(self, limit_kw: float | None) -> float | None:
        """Apply the configured Sungrow optimizer max to restore targets."""
        if limit_kw is None or limit_kw <= 0:
            return limit_kw

        configured_limit_kw = self._configured_optimization_discharge_limit_kw()
        if (
            configured_limit_kw is not None
            and configured_limit_kw > 0
            and limit_kw > configured_limit_kw
        ):
            _LOGGER.info(
                "Sungrow discharge limit restore: clamping target from %.2fkW "
                "to configured max %.2fkW",
                limit_kw,
                configured_limit_kw,
            )
            return configured_limit_kw
        return limit_kw

    def _discharge_appears_blocked_after_restore(self) -> bool:
        """Return True when self-consumption telemetry looks discharge-capped.

        Some SH10RS/SBH firmware does not expose the writable max-discharge
        register, so we cannot always read the stale 10 W cap directly.
        """
        coord_data = getattr(self, "data", None) or {}

        def read_float(*keys: str) -> float | None:
            for key in keys:
                try:
                    value = coord_data.get(key)
                    if value is None:
                        continue
                    return float(value)
                except (TypeError, ValueError):
                    continue
            return None

        battery_kw = read_float("battery_power", "battery_power_kw")
        grid_kw = read_float("grid_power", "grid_power_kw")
        load_kw = read_float("load_power", "home_load")
        soc = read_float("battery_level", "battery_soc")
        reserve = read_float("backup_reserve", "min_soc")
        bms_max_discharge_current_a = read_float("bms_max_discharge_current_a")

        if battery_kw is None or grid_kw is None or soc is None:
            return False
        if soc <= 0:
            return False
        configured_reserve = None
        if reserve is None:
            try:
                entry = self.hass.config_entries.async_get_entry(self._entry_id)
                raw_reserve = entry.options.get(
                    "hardware_backup_reserve",
                    entry.data.get("hardware_backup_reserve"),
                )
                configured_reserve = float(raw_reserve)
                if 0 <= configured_reserve <= 1:
                    configured_reserve *= 100
            except (AttributeError, TypeError, ValueError):
                configured_reserve = None

        effective_reserve = reserve if reserve is not None else configured_reserve
        if (
            effective_reserve is not None
            and soc <= effective_reserve + self._BLOCKED_DISCHARGE_RESERVE_MARGIN
        ):
            return False

        # A zero BMS discharge-current allowance is positive evidence that the
        # inverter is protecting the battery.  Do not infer the same thing from
        # SOC alone: some Sungrow installations normally expose and discharge
        # through a displayed 5% SOC before reaching their configured 0% floor.
        if (
            bms_max_discharge_current_a is not None
            and bms_max_discharge_current_a <= 0
        ):
            return False

        if abs(battery_kw) > self._BLOCKED_DISCHARGE_BATTERY_KW:
            return False
        if grid_kw < self._BLOCKED_DISCHARGE_IMPORT_KW:
            return False
        if load_kw is None:
            return True

        return (
            load_kw >= self._BLOCKED_DISCHARGE_LOAD_KW
            and grid_kw >= load_kw * self._BLOCKED_DISCHARGE_GRID_LOAD_RATIO
        )

    async def _read_current_discharge_limit_kw(self) -> float | None:
        """Return the current Sungrow discharge cap from coordinator or live data."""
        coord_data = getattr(self, "data", None) or {}
        for value in (
            coord_data.get("discharge_rate_limit_kw"),
            coord_data.get("battery_max_discharge_power"),
        ):
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                continue
            if parsed > 0:
                return parsed

        try:
            live_data = await self._controller.get_battery_data()
        except Exception as err:
            _LOGGER.debug("Could not read live Sungrow discharge cap for restore: %s", err)
            return None

        try:
            parsed = float(live_data.get("discharge_rate_limit_kw"))
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    def _configured_optimization_discharge_limit_kw(self) -> float | None:
        """Return configured optimiser max discharge limit, if available."""
        hass = getattr(self, "hass", None)
        entry_id = getattr(self, "_entry_id", None)
        if not hass or not entry_id:
            return None

        try:
            entry = hass.config_entries.async_get_entry(entry_id)
        except Exception:
            return None
        if not entry:
            return None

        value = entry.options.get(
            self._OPTIMIZATION_MAX_DISCHARGE_W_KEY,
            entry.data.get(self._OPTIMIZATION_MAX_DISCHARGE_W_KEY),
        )
        try:
            watts = float(value)
        except (TypeError, ValueError):
            return None
        return watts / 1000.0 if watts > 0 else None

    async def _restore_captured_export_limit(self) -> bool:
        """Restore a Sungrow export limit saved before temporary control."""
        if not getattr(self, "_pre_control_export_limit_captured", False):
            return True

        restore_limit_w = getattr(self, "_pre_control_export_limit_w", None)
        if restore_limit_w is None:
            limit_ok = await self._controller.set_export_limit(None)
        else:
            limit_ok = await self._controller.set_export_limit(int(restore_limit_w))

        persisted_ok = False
        if limit_ok:
            persisted_ok = await self._clear_persisted_export_control_state()
        if limit_ok and persisted_ok:
            self._pre_control_export_limit_w = None
            self._pre_control_export_limit_captured = False
        return bool(limit_ok and persisted_ok)

    async def restore_work_mode_from_idle(self) -> bool:
        """Restore self-consumption mode and discharge limit after IDLE."""
        async with self._modbus_lock, self._controller:
            normal_ok = await self._controller.restore_normal()
            charge_limit_ok = await self._restore_captured_charge_limit()
            limit_ok = await self._restore_captured_discharge_limit()
            return bool(normal_ok and charge_limit_ok and limit_ok)

    async def set_charge_rate_limit(self, kw: float) -> bool:
        """Set maximum charge rate in kW.

        Args:
            kw: Maximum charge rate in kW

        Returns:
            True if successful
        """
        async with self._modbus_lock, self._controller:
            return await self._controller.set_charge_rate_limit(kw)

    async def set_discharge_rate_limit(self, kw: float) -> bool:
        """Set maximum discharge rate in kW.

        Args:
            kw: Maximum discharge rate in kW

        Returns:
            True if successful
        """
        async with self._modbus_lock, self._controller:
            return await self._controller.set_discharge_rate_limit(kw)

    async def set_export_limit(self, watts: int | None) -> bool:
        """Set export power limit in watts.

        Args:
            watts: Export limit in watts, or None to disable

        Returns:
            True if successful
        """
        async with self._modbus_lock, self._controller:
            return await self._controller.set_export_limit(watts)

    async def async_shutdown(self) -> None:
        """Stop polling and disconnect from Sungrow after active Modbus work."""
        self.update_interval = None
        async with self._modbus_lock:
            await self._controller.disconnect()


class DualSungrowCoordinator(DataUpdateCoordinator):
    """Coordinator that aggregates two Sungrow SH inverters.

    Wraps two SungrowEnergyCoordinator instances (primary = grid-facing,
    secondary = on primary's backup port) and presents a single coordinator
    interface to the optimizer.  Power values are summed, SOC is
    capacity-weighted, and commands are split across both inverters.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        coord1: SungrowEnergyCoordinator,
        coord2: SungrowEnergyCoordinator,
        soc_cap: int = 100,
        cap1_kwh: float = 25.6,
        cap2_kwh: float = 25.6,
    ) -> None:
        self._coord1 = coord1  # Primary (grid-facing)
        self._coord2 = coord2  # Secondary (on backup port)
        self._soc_cap = soc_cap  # Max SOC for grid-forming inverter (100 = disabled)
        self._cap1 = cap1_kwh
        self._cap2 = cap2_kwh
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_sungrow_dual",
            update_interval=timedelta(seconds=30),
        )

    # ------------------------------------------------------------------
    # SOC-proportional power splitting
    # ------------------------------------------------------------------

    async def _split_power(self, total_kw: float, prefer_lower_soc: bool) -> tuple[float, float]:
        """Split power between inverters proportionally to SOC.

        prefer_lower_soc=True for charging (fill the emptier one faster).
        prefer_lower_soc=False for discharging (drain the fuller one faster).
        Returns (power_kw_for_coord1, power_kw_for_coord2).
        """
        soc1 = (self._coord1.data or {}).get("battery_level", 50) or 50
        soc2 = (self._coord2.data or {}).get("battery_level", 50) or 50

        total_cap = self._cap1 + self._cap2

        if abs(soc1 - soc2) < 2:
            return total_kw * self._cap1 / total_cap, total_kw * self._cap2 / total_cap

        if prefer_lower_soc:
            w1 = max(1, 100 - soc1) * self._cap1
            w2 = max(1, 100 - soc2) * self._cap2
        else:
            w1 = max(1, soc1) * self._cap1
            w2 = max(1, soc2) * self._cap2

        total_w = w1 + w2
        p1 = total_kw * w1 / total_w
        p2 = total_kw * w2 / total_w
        _LOGGER.debug(
            "Split %.2f kW: inv1=%.2f kW (soc=%.0f%%, cap=%.1f), inv2=%.2f kW (soc=%.0f%%, cap=%.1f), prefer_lower=%s",
            total_kw, p1, soc1, self._cap1, p2, soc2, self._cap2, prefer_lower_soc,
        )
        return p1, p2

    @staticmethod
    def _power_limit_kw(data: dict[str, Any], direction: str) -> float | None:
        """Return a per-inverter force-mode power limit from coordinator data."""
        raw_w = data.get(f"battery_max_{direction}_power_w")
        if raw_w and raw_w > 0:
            return float(raw_w) / 1000.0

        raw_kw = data.get(f"battery_max_{direction}_power")
        if raw_kw and raw_kw > 0:
            return float(raw_kw)

        return None

    def _combined_power_limit_w(self, direction: str) -> int | None:
        limits_kw = [
            self._power_limit_kw(self._coord1.data or {}, direction),
            self._power_limit_kw(self._coord2.data or {}, direction),
        ]
        if any(limit is None or limit <= 0 for limit in limits_kw):
            return None
        return int(round(sum(float(limit) for limit in limits_kw) * 1000.0))

    def _max_split_kw(self, direction: str) -> tuple[float, float] | None:
        limit1 = self._power_limit_kw(self._coord1.data or {}, direction)
        limit2 = self._power_limit_kw(self._coord2.data or {}, direction)
        if not limit1 or not limit2:
            return None
        return limit1, limit2

    # ------------------------------------------------------------------
    # Data aggregation
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> dict[str, Any]:
        """Aggregate data from both sub-coordinators."""
        d1 = self._coord1.data or {}
        d2 = self._coord2.data or {}

        if not d1 and not d2:
            raise UpdateFailed("No data from either Sungrow inverter")

        # Sum power values (kW)
        solar = (d1.get("solar_power", 0) or 0) + (d2.get("solar_power", 0) or 0)
        battery = (d1.get("battery_power", 0) or 0) + (d2.get("battery_power", 0) or 0)
        load = (d1.get("load_power", 0) or 0) + (d2.get("load_power", 0) or 0)
        # Grid: use primary only (it's the grid-facing inverter)
        grid = d1.get("grid_power", 0) or 0

        # Capacity-weighted SOC
        soc1 = d1.get("battery_level", 0) or 0
        soc2 = d2.get("battery_level", 0) or 0
        combined_soc = (soc1 * self._cap1 + soc2 * self._cap2) / (self._cap1 + self._cap2)

        # SOC divergence warning
        if abs(soc1 - soc2) > 5:
            _LOGGER.info(
                "Sungrow dual SOC divergence: inv1=%.1f%%, inv2=%.1f%% (delta=%.1f%%)",
                soc1, soc2, abs(soc1 - soc2),
            )

        # Enforce grid-forming inverter SOC cap
        if self._soc_cap < 100:
            max_soc1 = d1.get("max_soc")
            if max_soc1 is None or abs(max_soc1 - self._soc_cap) > 1:
                _LOGGER.info(
                    "Enforcing SOC cap: setting inv1 max_soc to %d%% (current register: %s)",
                    self._soc_cap, max_soc1,
                )
                await self._coord1.set_max_soc(self._soc_cap)

        # Combine energy summaries
        es1 = d1.get("energy_summary", {}) or {}
        es2 = d2.get("energy_summary", {}) or {}
        combined_energy = {}
        for key in (
            "pv_today_kwh", "grid_import_today_kwh", "grid_export_today_kwh",
            "charge_today_kwh", "discharge_today_kwh", "load_today_kwh",
            "import_cost_today", "export_earnings_today",
            "mtd_import_cost", "mtd_export_earnings", "mtd_load_kwh",
        ):
            combined_energy[key] = round(
                (es1.get(key, 0) or 0) + (es2.get(key, 0) or 0), 4
            )
        load_today = combined_energy.get("load_today_kwh", 0) or 0
        combined_energy["avg_cost_per_kwh_today"] = (
            round((combined_energy["import_cost_today"] - combined_energy["export_earnings_today"]) / load_today, 4)
            if load_today > 0 else None
        )
        mtd_load = combined_energy.get("mtd_load_kwh", 0) or 0
        combined_energy["avg_cost_per_kwh_mtd"] = (
            round((combined_energy["mtd_import_cost"] - combined_energy["mtd_export_earnings"]) / mtd_load, 4)
            if mtd_load > 0 else None
        )
        charge_limit_w = self._combined_power_limit_w("charge")
        discharge_limit_w = self._combined_power_limit_w("discharge")

        return {
            "solar_power": max(0, solar),
            "grid_power": grid,
            "battery_power": battery,
            "load_power": load,
            "battery_level": combined_soc,
            "last_update": dt_util.utcnow(),
            # Use primary's Sungrow-specific fields
            "battery_soh": d1.get("battery_soh"),
            "battery_voltage": d1.get("battery_voltage"),
            "battery_current": d1.get("battery_current"),
            "battery_temp": d1.get("battery_temp"),
            "inverter_temperature": d1.get("inverter_temperature"),
            "ems_mode": d1.get("ems_mode"),
            "ems_mode_name": d1.get("ems_mode_name"),
            "charge_cmd": d1.get("charge_cmd"),
            "min_soc": d1.get("min_soc"),
            "max_soc": d1.get("max_soc"),
            "backup_reserve": d1.get("backup_reserve"),
            "charge_rate_limit_kw": d1.get("charge_rate_limit_kw"),
            "discharge_rate_limit_kw": d1.get("discharge_rate_limit_kw"),
            "export_limit_w": d1.get("export_limit_w"),
            "export_limit_enabled": d1.get("export_limit_enabled"),
            "battery_max_charge_power_w": charge_limit_w,
            "battery_max_discharge_power_w": discharge_limit_w,
            "battery_max_charge_power": (
                charge_limit_w / 1000.0
                if charge_limit_w
                else None
            ),
            "battery_max_discharge_power": (
                discharge_limit_w / 1000.0
                if discharge_limit_w
                else None
            ),
            "energy_summary": combined_energy,
            # Per-inverter SOC for monitoring
            "battery_level_1": soc1,
            "battery_level_2": soc2,
        }

    # ------------------------------------------------------------------
    # Command splitting — delegate to both sub-coordinators
    # ------------------------------------------------------------------

    async def force_charge(self, duration_minutes: int = 30, power_w: float = 0) -> bool:
        """Force charge on both inverters with SOC-proportional power split."""
        if power_w > 0:
            p1, p2 = await self._split_power(power_w / 1000, prefer_lower_soc=True)
            r1 = await self._coord1.force_charge(duration_minutes, power_w=p1 * 1000)
            r2 = await self._coord2.force_charge(duration_minutes, power_w=p2 * 1000)
        else:
            r1 = await self._coord1.force_charge(duration_minutes)
            r2 = await self._coord2.force_charge(duration_minutes)
        return r1 and r2

    async def force_discharge(self, duration_minutes: int = 30, power_w: float = 0) -> bool:
        """Force discharge on both inverters with SOC-proportional power split."""
        if power_w > 0:
            max_split = self._max_split_kw("discharge")
            if max_split and (power_w / 1000.0) >= sum(max_split):
                p1, p2 = max_split
            else:
                p1, p2 = await self._split_power(power_w / 1000, prefer_lower_soc=False)
            r1 = await self._coord1.force_discharge(duration_minutes, power_w=p1 * 1000)
            r2 = await self._coord2.force_discharge(duration_minutes, power_w=p2 * 1000)
        else:
            r1 = await self._coord1.force_discharge(duration_minutes)
            r2 = await self._coord2.force_discharge(duration_minutes)
        return r1 and r2

    async def force_grid_export(
        self,
        duration_minutes: int = 30,
        export_limit_w: float = 0,
    ) -> bool:
        """Force discharge both inverters while limiting site export on primary."""
        max_split = self._max_split_kw("discharge")
        if max_split:
            _p1, p2 = max_split
            r1 = await self._coord1.force_grid_export(
                duration_minutes,
                export_limit_w=export_limit_w,
            )
            if not r1:
                return False
            try:
                r2 = await self._coord2.force_discharge(
                    duration_minutes,
                    power_w=p2 * 1000,
                )
            except Exception:
                await self.restore_normal()
                raise
        else:
            r1 = await self._coord1.force_grid_export(
                duration_minutes,
                export_limit_w=export_limit_w,
            )
            if not r1:
                return False
            try:
                r2 = await self._coord2.force_discharge(duration_minutes)
            except Exception:
                await self.restore_normal()
                raise

        if not r2:
            await self.restore_normal()
        return r1 and r2

    async def restore_normal(self) -> bool:
        """Restore self-consumption on both inverters."""
        r1 = await self._coord1.restore_normal()
        r2 = await self._coord2.restore_normal()
        return r1 and r2

    async def set_backup_reserve(self, percent: int) -> bool:
        """Set backup reserve on both inverters."""
        r1 = await self._coord1.set_backup_reserve(percent)
        r2 = await self._coord2.set_backup_reserve(percent)
        return r1 and r2

    async def set_backup_mode(self) -> bool:
        """Set idle/backup mode on both inverters."""
        r1 = await self._coord1.set_backup_mode()
        r2 = await self._coord2.set_backup_mode()
        return r1 and r2

    async def restore_work_mode_from_idle(self) -> bool:
        """Restore work mode from idle on both inverters."""
        r1 = await self._coord1.restore_work_mode_from_idle()
        r2 = await self._coord2.restore_work_mode_from_idle()
        return r1 and r2

    async def set_charge_rate_limit(self, kw: float) -> bool:
        """Split charge rate proportionally between both inverters."""
        p1, p2 = await self._split_power(kw, prefer_lower_soc=True)
        r1 = await self._coord1.set_charge_rate_limit(p1)
        r2 = await self._coord2.set_charge_rate_limit(p2)
        return r1 and r2

    async def set_discharge_rate_limit(self, kw: float) -> bool:
        """Split discharge rate proportionally between both inverters."""
        p1, p2 = await self._split_power(kw, prefer_lower_soc=False)
        r1 = await self._coord1.set_discharge_rate_limit(p1)
        r2 = await self._coord2.set_discharge_rate_limit(p2)
        return r1 and r2

    async def set_max_soc(self, percent: int) -> bool:
        """Set max SOC on primary (grid-forming) inverter only."""
        return await self._coord1.set_max_soc(percent)

    async def set_export_limit(self, watts: int | None) -> bool:
        """Set export limit on primary only (it's grid-facing)."""
        return await self._coord1.set_export_limit(watts)

    async def async_shutdown(self) -> None:
        """Shutdown both sub-coordinators."""
        await self._coord1.async_shutdown()
        await self._coord2.async_shutdown()


class FoxESSEnergyCoordinator(DataUpdateCoordinator):
    """Coordinator to fetch FoxESS battery system data via Modbus.

    Polls the FoxESS inverter via Modbus TCP or RS485 to get real-time
    power data (solar, battery, grid, load), battery SOC, and control settings.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        port: int = 502,
        slave_id: int = 247,
        connection_type: str = "tcp",
        serial_port: str | None = None,
        baudrate: int = 9600,
        model_family: str | None = None,
        entry_id: str = "",
    ) -> None:
        """Initialize the coordinator."""
        from ..inverters.foxess import FoxESSController

        self.host = host
        self.port = port
        self.slave_id = slave_id
        self._entry_id = entry_id
        self._controller = FoxESSController(
            host=host,
            port=port,
            slave_id=slave_id,
            connection_type=connection_type,
            serial_port=serial_port,
            baudrate=baudrate,
            model_family=model_family,
        )

        self._energy_acc = EnergyAccumulator(hass, "foxess")

        # Serialise all Modbus access so that data polls (every 30s) can't
        # clobber an in-progress force charge/discharge. Without this, the
        # data poll's connect() closes the TCP connection that force charge
        # opened, causing the reg=46003 write to fail silently (the
        # _connected=False guard fires before the DEBUG log, so no WRITE or
        # verify log appears — just "write failed on attempt N/3").
        self._modbus_lock = asyncio.Lock()

        super().__init__(
            hass,
            _LOGGER,
            name="FoxESS Energy",
            update_interval=timedelta(seconds=30),
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from FoxESS system via Modbus."""
        if not self._energy_acc._last_update:
            await self._energy_acc.async_restore()
        try:
            async with self._modbus_lock, self._controller:
                status = await self._controller.get_status()
                energy_summary = await self._controller.get_energy_summary()

            if not status.attributes:
                raise UpdateFailed("No data from FoxESS controller")

            attrs = status.attributes

            # Map to standard format (convention: positive = discharging, negative = charging)
            battery_kw = attrs.get("battery_power_kw", 0) or 0
            grid_kw = attrs.get("grid_power_kw", 0) or 0
            load_kw = attrs.get("load_power_kw", 0) or 0
            solar_kw = attrs.get("pv_power_kw", 0) or 0
            ct2_kw = attrs.get("ct2_power_kw", 0) or 0

            # Total solar = DC PV strings + AC-coupled CT2 meter
            total_solar_kw = solar_kw + max(0, ct2_kw)

            # Accumulate daily energy from power readings (with cost tracking)
            buy, sell = _get_current_prices(self.hass, self._entry_id)
            self._energy_acc.update(total_solar_kw, grid_kw, battery_kw, load_kw, buy, sell)

            # Merge Modbus energy registers (charge/discharge) with accumulated values
            acc = self._energy_acc.as_dict()
            if energy_summary:
                # Prefer Modbus registers for charge/discharge (more accurate)
                acc["charge_today_kwh"] = energy_summary.get("charge_today_kwh", acc["charge_today_kwh"])
                acc["discharge_today_kwh"] = energy_summary.get("discharge_today_kwh", acc["discharge_today_kwh"])

            energy_data = {
                "solar_power": max(0, total_solar_kw),
                "ct2_power": ct2_kw,
                "pv1_power": attrs.get("pv1_power_kw", 0) or 0,
                "pv2_power": attrs.get("pv2_power_kw", 0) or 0,
                "pv3_power": attrs.get("pv3_power_kw", 0) or 0,
                "grid_power": grid_kw,
                "battery_power": battery_kw,
                "load_power": load_kw,
                "battery_level": attrs.get("battery_soc", 0),
                "last_update": dt_util.utcnow(),
                # FoxESS-specific data
                "work_mode": attrs.get("work_mode"),
                "work_mode_name": attrs.get("work_mode_name"),
                "min_soc": attrs.get("min_soc"),
                "max_charge_current_a": attrs.get("max_charge_current_a"),
                "max_discharge_current_a": attrs.get("max_discharge_current_a"),
                "battery_voltage_v": attrs.get("battery_voltage_v"),
                "battery_temperature": attrs.get("battery_temperature"),
                "model_family": attrs.get("model_family"),
                "energy_summary": acc,
                "battery_soh": attrs.get("soh"),
                "nominal_power_w": attrs.get("nominal_power_w"),
                "nominal_energy_kwh": attrs.get("nominal_energy_kwh"),
                "total_charged_energy_kwh": attrs.get("total_charged_energy_kwh"),
            }

            # Max charge/discharge power is taken directly from nominal_power_w
            # (register 39053 on H3-Smart). Empirically this matches the inverter's
            # rated capacity and is more reliable than current×voltage arithmetic.
            _nominal_w = attrs.get("nominal_power_w")
            if _nominal_w and _nominal_w > 0:
                energy_data["battery_max_charge_power_w"] = int(_nominal_w)
                energy_data["battery_max_charge_power"] = round(_nominal_w / 1000.0, 2)
                energy_data["battery_max_discharge_power_w"] = int(_nominal_w)
                energy_data["battery_max_discharge_power"] = round(_nominal_w / 1000.0, 2)

            _LOGGER.debug(
                "FoxESS data: solar=%.2f kW, grid=%.2f kW, battery=%.2f kW (%.0f%%), load=%.2f kW, mode=%s",
                energy_data["solar_power"],
                energy_data["grid_power"],
                energy_data["battery_power"],
                energy_data["battery_level"],
                energy_data["load_power"],
                energy_data.get("work_mode_name", "?"),
            )

            return energy_data

        except Exception as err:
            raise UpdateFailed(f"Error fetching FoxESS energy data: {err}") from err

    # Per-model fallback voltage for current→power conversion when the live
    # pack voltage read is missing. HV families (H3-Pro, H3-Smart) run around
    # 500 V nominal; LV families (H1, H3, KH) around 51.2 V. The previous
    # single 300 V fallback silently capped HV systems at 50 A × 300 V = 15 kW.
    _FALLBACK_PACK_VOLTAGE = {
        "H3-Pro": 500,
        "H3-Smart": 500,
        "H1": 51.2,
        "H3": 51.2,
        "KH": 51.2,
    }

    def _resolve_pack_voltage_from_attrs(self, attrs: dict | None) -> float:
        """Pick the best pack voltage from an attrs dict, with model-aware fallback."""
        v = (attrs or {}).get("battery_voltage_v")
        if isinstance(v, (int, float)) and v > 100:
            return float(v)
        family = getattr(getattr(self, "_controller", None), "_model_family", None)
        family_str = family.value if family and hasattr(family, "value") else None
        return float(self._FALLBACK_PACK_VOLTAGE.get(family_str, 300))

    def _resolve_pack_voltage(self, for_logging: str = "") -> float:
        """Pick the best pack voltage we have, falling back by model family.

        Uses self.data (most recent coordinator refresh) as the source and
        logs when we fall back so a misbehaving voltage register is visible.
        """
        v = (self.data or {}).get("battery_voltage_v")
        if isinstance(v, (int, float)) and v > 100:
            return float(v)

        family = getattr(getattr(self, "_controller", None), "_model_family", None)
        family_str = family.value if family and hasattr(family, "value") else None
        fallback = self._FALLBACK_PACK_VOLTAGE.get(family_str, 300)
        _LOGGER.warning(
            "FoxESS%s: live battery voltage unavailable (got %r), "
            "falling back to %sV based on model family %s",
            f" {for_logging}" if for_logging else "",
            v,
            fallback,
            family_str or "UNKNOWN",
        )
        return float(fallback)

    async def force_charge(
        self,
        duration_minutes: int = 30,
        power_w: float = 0,
        min_timeout_seconds: int = 600,
    ) -> bool:
        """Set FoxESS to force charge mode.

        Args:
            duration_minutes: How long to charge
            power_w: Charge power in watts. If 0, reads max_charge_current from
                     the inverter and uses that (respects user's FoxESS app setting).
            min_timeout_seconds: Minimum hardware remote-control timeout.
        """
        async with self._modbus_lock, self._controller:
            if power_w <= 0 and self.data:
                # Use inverter's configured max charge current (set via FoxESS app)
                max_charge_a = self.data.get("max_charge_current_a")
                if max_charge_a and max_charge_a > 0:
                    voltage = self._resolve_pack_voltage("force_charge")
                    power_w = max_charge_a * voltage
                    _LOGGER.info(
                        "FoxESS force_charge using inverter max: %.0fA × %.0fV → %.0fW",
                        max_charge_a, voltage, power_w,
                    )
            if power_w <= 0:
                power_w = 5000  # Fallback default
            return await self._controller.force_charge(
                duration_minutes,
                power_w=power_w,
                min_timeout_seconds=min_timeout_seconds,
            )

    async def force_discharge(
        self,
        duration_minutes: int = 30,
        power_w: float = 0,
        min_timeout_seconds: int = 600,
    ) -> bool:
        """Set FoxESS to force discharge mode.

        Args:
            duration_minutes: How long to discharge
            power_w: Discharge power in watts. If 0, reads max_discharge_current from
                     the inverter and uses that (respects user's FoxESS app setting).
        """
        async with self._modbus_lock, self._controller:
            if power_w <= 0 and self.data:
                # Use inverter's configured max discharge current (set via FoxESS app)
                max_discharge_a = self.data.get("max_discharge_current_a")
                if max_discharge_a and max_discharge_a > 0:
                    voltage = self._resolve_pack_voltage("force_discharge")
                    power_w = max_discharge_a * voltage
                    _LOGGER.info(
                        "FoxESS force_discharge using inverter max: %.0fA × %.0fV → %.0fW",
                        max_discharge_a, voltage, power_w,
                    )
            if power_w <= 0:
                power_w = 5000  # Fallback default
            return await self._controller.force_discharge(
                duration_minutes,
                power_w=power_w,
                min_timeout_seconds=min_timeout_seconds,
            )

    async def restore_normal(self) -> bool:
        """Restore FoxESS to normal (Self Use) operation."""
        async with self._modbus_lock, self._controller:
            return await self._controller.restore_normal()

    async def set_backup_reserve(self, percent: int) -> bool:
        """Set minimum SOC (backup reserve)."""
        async with self._modbus_lock, self._controller:
            return await self._controller.set_backup_reserve(percent)

    async def set_backup_mode(self) -> bool:
        """Set FoxESS to Backup mode (IDLE — prevents self-consumption discharge)."""
        async with self._modbus_lock, self._controller:
            return await self._controller.set_backup_mode()

    async def set_no_discharge_mode(self) -> bool:
        """Block FoxESS self-consumption discharge while still allowing charge."""
        async with self._modbus_lock, self._controller:
            return await self._controller.set_backup_mode()

    async def restore_no_discharge_mode(self) -> bool:
        """Restore FoxESS from scheduled EV no-discharge preserve mode."""
        async with self._modbus_lock, self._controller:
            return await self._controller.restore_work_mode_from_idle()

    async def restore_work_mode_from_idle(self) -> bool:
        """Restore work mode to Self Use after IDLE Backup mode."""
        async with self._modbus_lock, self._controller:
            return await self._controller.restore_work_mode_from_idle()

    async def set_work_mode(self, mode: int) -> bool:
        """Set FoxESS work mode."""
        async with self._modbus_lock, self._controller:
            return await self._controller.set_work_mode(mode)

    async def set_charge_rate_limit(self, amps: float) -> bool:
        """Set maximum charge current in amps."""
        async with self._modbus_lock, self._controller:
            return await self._controller.set_charge_rate_limit(amps)

    async def set_discharge_rate_limit(self, amps: float) -> bool:
        """Set maximum discharge current in amps."""
        async with self._modbus_lock, self._controller:
            return await self._controller.set_discharge_rate_limit(amps)

    async def curtail(self, home_load_w: int | None = None) -> bool:
        """Apply FoxESS solar export curtailment via the shared Modbus session."""
        async with self._modbus_lock, self._controller:
            return await self._controller.curtail(home_load_w)

    async def restore_curtailment(self) -> bool:
        """Restore FoxESS solar export after curtailment via the shared Modbus session."""
        async with self._modbus_lock, self._controller:
            return await self._controller.restore()

    async def async_shutdown(self) -> None:
        """Disconnect from FoxESS system on shutdown."""
        await self._controller.disconnect()


class FoxESSEntityEnergyCoordinator(DataUpdateCoordinator):
    """Bridge coordinator for FoxESS via nathanmarlor/foxess_modbus entities."""

    def __init__(
        self,
        hass: HomeAssistant,
        foxess_entry_id: str | None = None,
        entity_prefix: str = "",
        entry_id: str = "",
    ) -> None:
        from ..inverters.foxess_entity import FoxESSEntityController

        self._entry_id = entry_id
        self._controller = FoxESSEntityController(
            hass,
            foxess_entry_id=foxess_entry_id,
            entity_prefix=entity_prefix,
        )
        self._energy_acc = EnergyAccumulator(hass, "foxess_entity")
        self._validated = False

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_foxess_entity_energy",
            update_interval=timedelta(seconds=30),
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Return FoxESS data assembled from foxess_modbus entity states."""
        if not self._energy_acc._last_update:
            await self._energy_acc.async_restore()

        try:
            if not self._validated:
                await self._controller.connect()
                self._validated = True
            status = self._controller.get_status()
        except Exception as exc:
            if self.data:
                _LOGGER.warning(
                    "FoxESS entity bridge read failed, returning stale data: %s",
                    exc,
                )
                return self.data
            raise UpdateFailed(f"FoxESS entity bridge read failed: {exc}") from exc

        solar_kw = status.get("solar_power", 0.0) or 0.0
        grid_kw = status.get("grid_power", 0.0) or 0.0
        battery_kw = status.get("battery_power", 0.0) or 0.0
        load_kw = status.get("load_power", 0.0) or 0.0
        soc = status.get("battery_level", 0.0) or 0.0

        buy, sell = _get_current_prices(self.hass, self._entry_id)
        self._energy_acc.update(max(0.0, solar_kw), grid_kw, battery_kw, load_kw, buy, sell)
        energy_summary = self._energy_acc.as_dict()
        for status_key, summary_key in (
            ("daily_solar_energy_kwh", "pv_today_kwh"),
            ("daily_grid_import_kwh", "grid_import_today_kwh"),
            ("daily_grid_export_kwh", "grid_export_today_kwh"),
            ("daily_battery_charge_kwh", "charge_today_kwh"),
            ("daily_battery_discharge_kwh", "discharge_today_kwh"),
        ):
            value = status.get(status_key)
            if isinstance(value, (int, float)) and value >= 0:
                energy_summary[summary_key] = round(float(value), 3)

        data = {
            "solar_power": solar_kw,
            "grid_power": grid_kw,
            "battery_power": battery_kw,
            "load_power": load_kw,
            "battery_level": soc,
            "last_update": dt_util.utcnow(),
            "battery_temperature": status.get("battery_temperature"),
            "battery_soh": status.get("battery_soh"),
            "backup_reserve": status.get("backup_reserve"),
            "min_soc": status.get("min_soc"),
            "mode": status.get("mode"),
            "work_mode": status.get("work_mode"),
            "work_mode_name": status.get("work_mode_name"),
            "max_charge_current_a": status.get("max_charge_current_a"),
            "max_discharge_current_a": status.get("max_discharge_current_a"),
            "energy_summary": energy_summary,
        }
        for key in (
            "battery_max_charge_power_w",
            "battery_max_charge_power",
            "battery_max_discharge_power_w",
            "battery_max_discharge_power",
        ):
            if status.get(key) is not None:
                data[key] = status[key]
        for idx in range(1, 7):
            for suffix in ("power", "voltage", "current"):
                key = f"pv{idx}_{suffix}"
                if status.get(key) is not None:
                    data[key] = status[key]

        _LOGGER.debug(
            "FoxESS entity data: solar=%.2f kW, grid=%.2f kW, battery=%.2f kW (%.0f%%), load=%.2f kW, mode=%s",
            data["solar_power"],
            data["grid_power"],
            data["battery_power"],
            data["battery_level"],
            data["load_power"],
            data.get("work_mode_name", "?"),
        )

        return data

    async def force_charge(
        self,
        duration_minutes: int = 30,
        power_w: float = 0,
        min_timeout_seconds: float | None = None,
    ) -> bool:
        return await self._controller.force_charge(duration_minutes, power_w)

    async def force_discharge(
        self,
        duration_minutes: int = 30,
        power_w: float = 0,
        min_timeout_seconds: float | None = None,
    ) -> bool:
        return await self._controller.force_discharge(duration_minutes, power_w)

    async def restore_normal(self) -> bool:
        return await self._controller.restore_normal()

    async def set_backup_reserve(self, percent: int) -> bool:
        return await self._controller.set_backup_reserve(percent)

    async def get_backup_reserve(self) -> int | None:
        return await self._controller.get_backup_reserve()

    async def set_backup_mode(self) -> bool:
        return await self._controller.set_backup_mode()

    async def restore_work_mode_from_idle(self) -> bool:
        return await self._controller.restore_work_mode_from_idle()

    async def set_work_mode(self, mode: int | str) -> bool:
        return await self._controller.set_work_mode(mode)

    async def set_operation_mode(self, mode: str) -> bool:
        return await self._controller.set_operation_mode(mode)

    async def set_charge_rate_limit(self, amps: float) -> bool:
        return await self._controller.set_charge_rate_limit(amps)

    async def set_discharge_rate_limit(self, amps: float) -> bool:
        return await self._controller.set_discharge_rate_limit(amps)

    async def curtail(self, home_load_w: int | None = None) -> bool:
        return await self._controller.curtail(home_load_w)

    async def restore_curtailment(self) -> bool:
        return await self._controller.restore()

    async def async_shutdown(self) -> None:
        await self._controller.disconnect()


class FoxESSCloudEnergyCoordinator(DataUpdateCoordinator):
    """Coordinator to fetch and control FoxESS systems through FoxESS Cloud."""

    def __init__(
        self,
        hass: HomeAssistant,
        api_key: str,
        device_sn: str,
        entry_id: str = "",
    ) -> None:
        """Initialize the cloud coordinator."""
        from ..foxess_api import FoxESSCloudClient

        self._entry_id = entry_id
        self.device_sn = device_sn
        self._client = FoxESSCloudClient(
            api_key=api_key,
            device_sn=device_sn,
            session=async_get_clientsession(hass),
        )
        # Keep compatibility with older curtailment code paths that reached for
        # foxess_coordinator._controller.curtail()/restore().
        self._controller = self
        self._energy_acc = EnergyAccumulator(hass, "foxess_cloud")
        self._store = Store(hass, 1, f"{DOMAIN}.foxess_cloud.{entry_id}") if entry_id else None
        self._stored_scheduler_groups: list[dict] | None = None
        self._last_backup_reserve = 10

        super().__init__(
            hass,
            _LOGGER,
            name="FoxESS Cloud Energy",
            update_interval=timedelta(seconds=60),
        )

    @staticmethod
    def _to_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _real_data_map(payload: Any) -> dict[str, Any]:
        """Flatten FoxESS realtime response variants into variable -> value."""
        if isinstance(payload, dict) and isinstance(payload.get("datas"), list):
            rows = payload["datas"]
        elif isinstance(payload, list) and payload:
            first = payload[0]
            rows = first.get("datas", []) if isinstance(first, dict) else []
        elif isinstance(payload, dict):
            result = payload.get("data") or payload.get("result")
            if isinstance(result, list) and result:
                rows = result[0].get("datas", []) if isinstance(result[0], dict) else []
            else:
                rows = []
        else:
            rows = []

        data = {}
        for item in rows:
            if not isinstance(item, dict):
                continue
            key = item.get("variable") or item.get("key") or item.get("name")
            if key:
                data[str(key)] = item.get("value")
        return data

    @staticmethod
    def _soc_from_values(values: dict[str, Any]) -> float | None:
        """Return battery SoC (%) from a flattened realtime map, or None if absent.

        FoxESS Cloud realtime can omit the SoC variable for a device (cloud lag,
        model variant, or a transient gap) or return it as null. Distinguish a
        missing reading from a genuine 0% so callers can keep the previous SOC
        instead of telling the optimizer the battery is empty.
        """
        raw = values.get("SoC")
        if raw is None:
            raw = values.get("soc")
        if raw is None:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    async def _load_stored_scheduler(self) -> None:
        if self._stored_scheduler_groups is not None or not self._store:
            return
        try:
            stored = await self._store.async_load()
        except Exception as err:
            _LOGGER.debug("FoxESS Cloud: failed to load stored scheduler state: %s", err)
            stored = None
        self._stored_scheduler_groups = (stored or {}).get("scheduler_groups")

    async def _save_current_scheduler(self) -> None:
        """Persist current non-hidden scheduler groups before a temporary action."""
        if self._stored_scheduler_groups:
            return
        try:
            from ..foxess_api import filter_public_scheduler_groups

            result = await self._client.get_scheduler()
            groups = []
            if isinstance(result, dict):
                groups = result.get("groups") or result.get("schedulerList") or []
            self._stored_scheduler_groups = filter_public_scheduler_groups(groups)
            if self._store:
                await self._store.async_save({"scheduler_groups": self._stored_scheduler_groups})
        except Exception as err:
            _LOGGER.warning("FoxESS Cloud: failed to snapshot scheduler before control action: %s", err)
            self._stored_scheduler_groups = []

    async def _restore_stored_scheduler(self) -> bool:
        await self._load_stored_scheduler()
        if self._stored_scheduler_groups is not None:
            await self._client.set_scheduler_v3(self._stored_scheduler_groups)
        if self._store:
            await self._store.async_save({"scheduler_groups": []})
        self._stored_scheduler_groups = []
        return True

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch FoxESS Cloud realtime data."""
        if not self._energy_acc._last_update:
            await self._energy_acc.async_restore()

        try:
            raw = await self._client.get_real_data()
            values = self._real_data_map(raw)

            def _first_present(*keys: str) -> Any:
                for key in keys:
                    val = values.get(key)
                    if val is not None:
                        return val
                return None

            solar_kw = self._to_float(values.get("pvPower") or values.get("generationPower")) / 1000.0
            load_kw = self._to_float(values.get("loadsPower")) / 1000.0

            # Battery power: FoxESS exposes different variables per model. KH/K-series
            # report invBatPower (or split batChargePower/batDischargePower) rather than
            # batPower, so reading batPower alone yields 0 on those units. Prefer the
            # signed inverter-battery reading, then fall back to charge/discharge
            # magnitudes. Positive = discharging, matching the PowerSync convention.
            inv_bat = _first_present("invBatPower", "batPower")
            if inv_bat is not None:
                battery_kw = self._to_float(inv_bat) / 1000.0
            else:
                charge_kw = self._to_float(_first_present("batChargePower", "chargePower")) / 1000.0
                discharge_kw = self._to_float(_first_present("batDischargePower", "dischargePower")) / 1000.0
                battery_kw = discharge_kw - charge_kw

            # Grid power: prefer the meter reading; otherwise net import minus export.
            meter = _first_present("meterPower")
            if meter is not None:
                grid_kw = self._to_float(meter) / 1000.0
            else:
                import_kw = self._to_float(values.get("gridConsumptionPower")) / 1000.0
                export_kw = self._to_float(values.get("feedinPower")) / 1000.0
                grid_kw = import_kw - export_kw

            # If FoxESS Cloud realtime returned no usable SoC, keep the previous
            # readings rather than reporting SOC=0% — a 0% reading makes the
            # optimizer think the battery is empty and schedule IDLE. This mirrors
            # the Sungrow/Sigenergy Modbus coordinators' missing-battery-data guard.
            soc = self._soc_from_values(values)
            if soc is None:
                if self.data:
                    _LOGGER.warning(
                        "FoxESS Cloud realtime returned no battery SoC — keeping previous readings"
                    )
                    return self.data
                raise UpdateFailed(
                    "FoxESS Cloud realtime returned no battery SoC — no data available"
                )

            buy, sell = _get_current_prices(self.hass, self._entry_id)
            self._energy_acc.update(solar_kw, grid_kw, battery_kw, load_kw, buy, sell)
            acc = self._energy_acc.as_dict()

            charge_total = self._to_float(values.get("chargeEnergyToTal"), acc["charge_today_kwh"])
            discharge_total = self._to_float(values.get("dischargeEnergyToTal"), acc["discharge_today_kwh"])
            acc["charge_today_kwh"] = charge_total
            acc["discharge_today_kwh"] = discharge_total

            data = {
                "solar_power": max(0, solar_kw),
                "grid_power": grid_kw,
                "battery_power": battery_kw,
                "load_power": load_kw,
                "battery_level": soc,
                "last_update": dt_util.utcnow(),
                "work_mode": values.get("workMode"),
                "work_mode_name": values.get("workMode"),
                "energy_summary": acc,
                "cloud_backend": True,
            }
            _LOGGER.debug(
                "FoxESS Cloud data: solar=%.2f kW, grid=%.2f kW, battery=%.2f kW, load=%.2f kW, soc=%.0f%%",
                data["solar_power"], data["grid_power"], data["battery_power"],
                data["load_power"], data["battery_level"],
            )
            return data
        except Exception as err:
            raise UpdateFailed(f"Error fetching FoxESS Cloud energy data: {err}") from err

    async def force_charge(
        self,
        duration_minutes: int = 30,
        power_w: float = 0,
        min_timeout_seconds: int = 600,
    ) -> bool:
        """Set FoxESS to force charge mode through Scheduler V3."""
        await self._save_current_scheduler()
        await self._client.force_charge(
            duration_minutes,
            power_w=power_w,
            target_soc=100,
            min_soc=self._last_backup_reserve,
        )
        return True

    async def force_discharge(
        self,
        duration_minutes: int = 30,
        power_w: float = 0,
        min_timeout_seconds: int = 600,
    ) -> bool:
        """Set FoxESS to force discharge mode through Scheduler V3."""
        await self._save_current_scheduler()
        await self._client.force_discharge(
            duration_minutes,
            power_w=power_w,
            target_soc=self._last_backup_reserve,
            min_soc=self._last_backup_reserve,
        )
        return True

    async def restore_normal(self) -> bool:
        """Restore scheduler state and set SelfUse mode."""
        await self._restore_stored_scheduler()
        await self._client.set_work_mode("SelfUse")
        return True

    async def set_backup_reserve(self, percent: int) -> bool:
        """Set minimum SOC through FoxESS Cloud."""
        value = int(max(0, min(100, percent)))
        self._last_backup_reserve = value
        await self._client.set_battery_soc(value, value)
        return True

    async def set_backup_mode(self) -> bool:
        """Set FoxESS Backup mode through cloud settings."""
        await self._client.set_work_mode("Backup")
        return True

    async def restore_work_mode_from_idle(self) -> bool:
        """Restore work mode to SelfUse after idle hold."""
        return await self.restore_normal()

    async def set_work_mode(self, mode: int | str) -> bool:
        """Set FoxESS work mode through cloud settings."""
        mode_map = {0: "SelfUse", 1: "FeedIn", 2: "Backup"}
        cloud_mode = mode_map.get(mode, mode)
        await self._client.set_work_mode(cloud_mode)
        return True

    async def set_charge_rate_limit(self, amps: float) -> bool:
        """Set maximum charge current in amps."""
        await self._client.set_device_setting("MaxSetChargeCurrent", float(amps))
        return True

    async def set_discharge_rate_limit(self, amps: float) -> bool:
        """Set maximum discharge current in amps."""
        await self._client.set_device_setting("MaxSetDischargeCurrent", float(amps))
        return True

    async def curtail(self, home_load_w: int | None = None) -> bool:
        """Curtail export through FoxESS Cloud export limit settings.

        On FoxESS the ``ExportLimit`` key is the export ceiling in watts (not a
        0/1 enable). ``ExportLimitPower``/``ActivePowerLimit`` are best-effort —
        not every model exposes them — so writes that report the key unsupported
        are tolerated rather than aborting the curtailment.
        """
        await self._save_current_scheduler()
        limit = max(0, float(home_load_w or 0))
        await self._client.set_device_setting("ExportLimit", limit)
        await self._client.set_device_setting_optional("ExportLimitPower", limit)
        await self._client.set_device_setting_optional("ActivePowerLimit", limit)
        await self._client.set_scheduler_v3(
            [
                {
                    "startHour": 0,
                    "startMinute": 0,
                    "endHour": 23,
                    "endMinute": 59,
                    "workMode": "SelfUse",
                    "exportLimit": limit,
                    "pvLimit": limit,
                    "minSocOnGrid": self._last_backup_reserve,
                }
            ]
        )
        return True

    async def restore(self) -> bool:
        """Compatibility alias for curtailment restore."""
        return await self.restore_curtailment()

    async def restore_curtailment(self) -> bool:
        """Restore export limit after cloud curtailment."""
        await self._restore_stored_scheduler()
        await self._client.set_device_setting("ExportLimit", 30000)
        await self._client.set_device_setting_optional("ExportLimitPower", 30000)
        await self._client.set_device_setting_optional("ActivePowerLimit", 30000)
        return True

    async def async_shutdown(self) -> None:
        """Shutdown cloud coordinator."""
        await self._energy_acc.async_flush()
        await self._client.close()


class GoodWeEnergyCoordinator(DataUpdateCoordinator):
    """Coordinator to fetch GoodWe battery system data via goodwe library.

    Polls the GoodWe inverter to get real-time power data (solar, battery,
    grid, load), battery SOC, and provides battery control.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        port: int = 8899,
        comm_addr: int = 0,
        entry_id: str = "",
        ems_entity_prefix: str | None = None,
        entity_telemetry_prefix: str | None = None,
    ) -> None:
        """Initialize the coordinator."""
        from ..inverters.goodwe_battery import GoodWeBatteryController

        self.host = host
        self.port = port
        self._entry_id = entry_id
        # When ems_entity_prefix is set (e.g. "goodwe"), control commands are
        # relayed through the community GoodWe HA integration's EMS entities
        # (select.<prefix>_ems_mode, number.<prefix>_ems_power_limit) instead of
        # opening a direct UDP connection.  This is necessary when the inverter is
        # only reachable via a Modbus TCP gateway — the EMS mode registers accept
        # Modbus TCP writes whereas the standard operation-mode registers do not.
        self._ems_prefix = ems_entity_prefix
        self._controller = GoodWeBatteryController(
            host=host, port=port, comm_addr=comm_addr
        )
        self._telemetry_controller = self._controller
        self._entity_telemetry_prefix = (entity_telemetry_prefix or "").strip()
        self._using_entity_telemetry = bool(self._entity_telemetry_prefix)
        if self._using_entity_telemetry:
            from ..inverters.goodwe_entity import GoodWeEntityTelemetryController

            self._telemetry_controller = GoodWeEntityTelemetryController(
                hass,
                entity_prefix=self._entity_telemetry_prefix,
            )
        self._connected = False
        self._telemetry_validated = False
        self._entity_telemetry_rated_power_w: int | None = None
        self._entity_telemetry_rated_power_probe_attempted = False
        self._energy_acc = EnergyAccumulator(hass, "goodwe")
        self._discharge_floor_pct: int = 10  # updated by set_backup_reserve

        super().__init__(
            hass,
            _LOGGER,
            name="GoodWe Energy",
            update_interval=timedelta(seconds=30),
        )

    async def _probe_entity_telemetry_rated_power(self) -> int | None:
        """Best-effort one-time direct probe for GoodWe nameplate power."""
        if self._entity_telemetry_rated_power_probe_attempted:
            return self._entity_telemetry_rated_power_w

        self._entity_telemetry_rated_power_probe_attempted = True
        try:
            await asyncio.wait_for(self._controller.connect(), timeout=5.0)
            direct_data = await asyncio.wait_for(
                self._controller.get_runtime_data(),
                timeout=5.0,
            )
            rated_power_w = direct_data.get("rated_power_w")
            value = int(round(float(rated_power_w)))
            if value <= 0:
                raise ValueError("rated_power_w missing")
        except Exception as exc:
            _LOGGER.debug(
                "GoodWe entity telemetry rated-power probe failed; "
                "continuing without direct physical discharge limit: %s",
                exc,
            )
            return None

        self._entity_telemetry_rated_power_w = value
        _LOGGER.info(
            "GoodWe entity telemetry cached rated_power_w=%dW from one-time direct capability probe",
            value,
        )
        return value

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from GoodWe inverter."""
        if not self._energy_acc._last_update:
            await self._energy_acc.async_restore()
        try:
            if self._using_entity_telemetry:
                if not self._telemetry_validated:
                    await self._telemetry_controller.connect()
                    self._telemetry_validated = True
                data = self._telemetry_controller.get_runtime_data()
                if not data.get("rated_power_w"):
                    rated_power_w = await self._probe_entity_telemetry_rated_power()
                    if rated_power_w:
                        data["rated_power_w"] = rated_power_w
            else:
                if not self._connected:
                    await self._controller.connect()
                    self._connected = True

                data = await self._controller.get_runtime_data()

            solar_kw = data["solar_power"]
            grid_kw = data["grid_power"]
            battery_kw = data["battery_power"]
            load_kw = data["load_power"]

            # Accumulate daily energy from power readings (with cost tracking)
            buy, sell = _get_current_prices(self.hass, self._entry_id)
            self._energy_acc.update(max(0, solar_kw), grid_kw, battery_kw, load_kw, buy, sell)

            energy_data = {
                "solar_power": solar_kw,
                "grid_power": grid_kw,
                "battery_power": battery_kw,
                "load_power": load_kw,
                "battery_level": data["battery_level"],
                "last_update": dt_util.utcnow(),
                # GoodWe-specific
                "battery_temperature": data.get("battery_temperature"),
                "battery_soh": data.get("battery_soh"),
                "model_name": data.get("model_name"),
                "serial_number": data.get("serial_number"),
                "rated_power_w": data.get("rated_power_w"),
                # Inverter nameplate rating as the BMS ceiling — GoodWe ET/EH
                # hybrid inverters match their battery's charge/discharge rate
                # to rated_power_w in practice, so reuse it as the force-mode
                # picker's Max value. Symmetric for charge + discharge.
                "battery_max_charge_power_w": data.get("rated_power_w"),
                "battery_max_discharge_power_w": data.get("rated_power_w"),
                "battery_max_charge_power": (
                    round(data["rated_power_w"] / 1000.0, 2)
                    if data.get("rated_power_w") else None
                ),
                "battery_max_discharge_power": (
                    round(data["rated_power_w"] / 1000.0, 2)
                    if data.get("rated_power_w") else None
                ),
                "energy_summary": self._energy_acc.as_dict(),
            }
            if data.get("work_mode") is not None:
                energy_data["work_mode"] = data.get("work_mode")
                energy_data["work_mode_name"] = data.get("work_mode_name")
            if data.get("entity_telemetry"):
                energy_data["entity_telemetry"] = True
            for status_key, summary_key in (
                ("daily_solar_energy_kwh", "pv_today_kwh"),
                ("daily_grid_import_kwh", "grid_import_today_kwh"),
                ("daily_grid_export_kwh", "grid_export_today_kwh"),
                ("daily_battery_charge_kwh", "charge_today_kwh"),
                ("daily_battery_discharge_kwh", "discharge_today_kwh"),
            ):
                value = data.get(status_key)
                if isinstance(value, (int, float)) and value >= 0:
                    energy_data["energy_summary"][summary_key] = round(float(value), 3)

            _LOGGER.debug(
                "GoodWe data%s: solar=%.2f kW, grid=%.2f kW, battery=%.2f kW (%.0f%%), load=%.2f kW",
                " (entity telemetry)" if self._using_entity_telemetry else "",
                energy_data["solar_power"],
                energy_data["grid_power"],
                energy_data["battery_power"],
                energy_data["battery_level"],
                energy_data["load_power"],
            )

            return energy_data

        except Exception as err:
            if self._using_entity_telemetry and self.data:
                _LOGGER.warning(
                    "GoodWe entity telemetry read failed, returning stale data: %s",
                    err,
                )
                return self.data
            if self._using_entity_telemetry:
                self._telemetry_validated = False
            else:
                self._connected = False
            raise UpdateFailed(f"Error fetching GoodWe data: {err}") from err

    def _goodwe_ems_mode_attempts(
        self,
        mode_entity: str,
        preferred_option: str,
        fallback_option: str | None = None,
    ) -> list[str]:
        """Return supported GoodWe EMS mode attempts in preference order."""
        attempts = [preferred_option]
        if fallback_option and fallback_option != preferred_option:
            attempts.append(fallback_option)

        state = self.hass.states.get(mode_entity)
        raw_options = state.attributes.get("options") if state else None
        if not isinstance(raw_options, (list, tuple, set)):
            return attempts

        options = {str(option) for option in raw_options}
        if preferred_option in options:
            return [preferred_option]
        if fallback_option and fallback_option in options:
            _LOGGER.warning(
                "GoodWe EMS mode %s is not exposed by %s; falling back to %s",
                preferred_option,
                mode_entity,
                fallback_option,
            )
            return [fallback_option]

        _LOGGER.warning(
            "GoodWe EMS modes %s are not exposed by %s (available: %s); trying %s",
            attempts,
            mode_entity,
            sorted(options),
            preferred_option,
        )
        return [preferred_option]

    async def _ems_set_mode(
        self,
        ems_option: str,
        power_w: float,
        fallback_option: str | None = None,
        reset_power_limit: bool = False,
        restore_operation_mode: bool = False,
    ) -> bool:
        """Control via the community GoodWe HA integration's EMS entities.

        Uses select.<prefix>_ems_mode and number.<prefix>_ems_power_limit.
        These registers accept Modbus TCP writes, unlike the standard
        operation-mode / work-mode registers which require UDP.
        """
        p = self._ems_prefix
        mode_entity = f"select.{p}_ems_mode"
        power_entity = f"number.{p}_ems_power_limit"

        # GoodWe EMS power limit register is 16-bit unsigned, max 32768 W
        GOODWE_EMS_MAX_W = 32768
        try:
            power_limit_log: int | str = "unchanged"
            if power_w > 0:
                capped_w = min(int(power_w), GOODWE_EMS_MAX_W)
                await self.hass.services.async_call(
                    "number", "set_value",
                    {"entity_id": power_entity, "value": capped_w},
                    blocking=True,
                )
                power_limit_log = capped_w
            elif reset_power_limit:
                state = self.hass.states.get(power_entity)
                rated_power_w = (self.data or {}).get("rated_power_w")
                try:
                    restore_limit = int(float(rated_power_w))
                    if restore_limit <= 0:
                        raise ValueError
                except (TypeError, ValueError):
                    raw_max = state.attributes.get("max") if state else None
                    try:
                        restore_limit = int(float(raw_max))
                    except (TypeError, ValueError):
                        restore_limit = GOODWE_EMS_MAX_W
                restore_limit = max(1, min(restore_limit, GOODWE_EMS_MAX_W))
                try:
                    await self.hass.services.async_call(
                        "number", "set_value",
                        {"entity_id": power_entity, "value": restore_limit},
                        blocking=True,
                    )
                    power_limit_log = restore_limit
                except Exception as reset_exc:
                    _LOGGER.warning(
                        "GoodWe EMS control could not reset %s power limit to %dW: %s",
                        power_entity,
                        restore_limit,
                        reset_exc,
                    )

            attempts = self._goodwe_ems_mode_attempts(
                mode_entity,
                ems_option,
                fallback_option,
            )
            last_exc: Exception | None = None
            for option in attempts:
                try:
                    await self.hass.services.async_call(
                        "select",
                        "select_option",
                        {"entity_id": mode_entity, "option": option},
                        blocking=True,
                    )
                    _LOGGER.info(
                        "GoodWe EMS control: set %s=%s power_limit=%sW",
                        mode_entity,
                        option,
                        power_limit_log,
                    )
                    if restore_operation_mode:
                        await self._ems_restore_operation_mode()
                    return True
                except Exception as select_exc:
                    last_exc = select_exc
                    if option != attempts[-1]:
                        _LOGGER.warning(
                            "GoodWe EMS control failed for %s=%s; trying %s: %s",
                            mode_entity,
                            option,
                            attempts[-1],
                            select_exc,
                        )

            if last_exc:
                raise last_exc
            return False
        except Exception as exc:
            _LOGGER.error("GoodWe EMS control failed (%s=%s): %s", mode_entity, ems_option, exc)
            return False

    async def _ems_restore_operation_mode(self) -> None:
        """Best-effort restore of the companion GoodWe operation-mode select."""
        p = self._ems_prefix
        operation_entity = f"select.{p}_inverter_operation_mode"
        state = self.hass.states.get(operation_entity)
        if state is None:
            return

        raw_options = state.attributes.get("options")
        options = raw_options if isinstance(raw_options, (list, tuple, set)) else []
        option_lookup = {
            str(option).strip().lower().replace(" ", "_"): str(option)
            for option in options
        }
        selected_option = (
            option_lookup.get("general")
            or option_lookup.get("general_mode")
            or "general"
        )

        try:
            await self.hass.services.async_call(
                "select",
                "select_option",
                {"entity_id": operation_entity, "option": selected_option},
                blocking=True,
            )
            _LOGGER.info(
                "GoodWe EMS control: restored %s=%s",
                operation_entity,
                selected_option,
            )
        except Exception as exc:
            _LOGGER.warning(
                "GoodWe EMS control could not restore %s to general mode: %s",
                operation_entity,
                exc,
            )

    async def force_charge(self, duration_minutes: int = 30, power_w: float = 0) -> bool:
        """Set GoodWe to force charge mode."""
        if self._ems_prefix:
            if power_w <= 0:
                power_w = (self.data or {}).get("rated_power_w", 5000)
            return await self._ems_set_mode("charge_pv", power_w, fallback_option="charge_battery")
        if not self._connected:
            await self._controller.connect()
            self._connected = True
        rated = (self.data or {}).get("rated_power_w", 5000)
        pct = min(100, max(10, int((power_w / rated) * 100))) if power_w > 0 else 100
        return await self._controller.force_charge(power_pct=pct)

    async def force_discharge(self, duration_minutes: int = 30, power_w: float = 0) -> bool:
        """Set GoodWe to force discharge mode."""
        if self._ems_prefix:
            if power_w <= 0:
                power_w = (self.data or {}).get("rated_power_w", 5000)
            return await self._ems_set_mode("sell_power", power_w, fallback_option="discharge_battery")
        if not self._connected:
            await self._controller.connect()
            self._connected = True
        rated = (self.data or {}).get("rated_power_w", 5000)
        pct = min(100, max(10, int((power_w / rated) * 100))) if power_w > 0 else 100
        return await self._controller.force_discharge(power_pct=pct, soc_floor=self._discharge_floor_pct)

    async def restore_normal(self) -> bool:
        """Restore GoodWe to normal operation."""
        if self._ems_prefix:
            return await self._ems_set_mode(
                "auto",
                0,
                reset_power_limit=True,
                restore_operation_mode=True,
            )
        if not self._connected:
            await self._controller.connect()
            self._connected = True
        return await self._controller.restore_normal()

    async def set_backup_reserve(self, percent: int) -> bool:
        """Set minimum SOC (backup reserve) via DOD."""
        if not self._connected:
            await self._controller.connect()
            self._connected = True
        self._discharge_floor_pct = max(10, percent)
        return await self._controller.set_backup_reserve(percent)

    async def async_shutdown(self) -> None:
        """Disconnect from GoodWe system on shutdown."""
        if self._using_entity_telemetry:
            await self._telemetry_controller.disconnect()
        await self._controller.disconnect()
        self._connected = False


class SolaxBatteryEnergyCoordinator(DataUpdateCoordinator):
    """Bridge coordinator for Solax Hybrid via the wills106/homeassistant-solax-modbus integration.

    Reads entity states published by the solax_modbus integration and assembles
    the standard PowerSync data dict. Control (force_charge, restore_normal, etc.)
    is delegated to SolaxBatteryController which writes via HA service calls.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        solax_entry_id: str | None = None,
        entity_prefix: str = "solax",
        battery_nominal_v: float = 51.2,
        max_charge_current_a: float = 25.0,
        max_discharge_current_a: float = 25.0,
        entry_id: str = "",
    ) -> None:
        from ..inverters.solax_battery import SolaxBatteryController

        self._entry_id = entry_id
        self._controller = SolaxBatteryController(
            hass,
            solax_entry_id=solax_entry_id,
            entity_prefix=entity_prefix,
            battery_nominal_v=battery_nominal_v,
            max_charge_current_a=max_charge_current_a,
            max_discharge_current_a=max_discharge_current_a,
        )
        self._energy_acc = EnergyAccumulator(hass, "solax")

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_solax_energy",
            update_interval=timedelta(seconds=30),
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Return Solax data assembled from HA entity states."""
        if not self._energy_acc._last_update:
            await self._energy_acc.async_restore()

        try:
            status = self._controller.get_status()
        except Exception as exc:
            if self.data:
                _LOGGER.warning("Solax entity read failed, returning stale data: %s", exc)
                return self.data
            raise UpdateFailed(f"Solax entity read failed: {exc}") from exc

        solar_kw = status.get("solar_power", 0.0) or 0.0
        grid_kw = status.get("grid_power", 0.0) or 0.0
        battery_kw = status.get("battery_power", 0.0) or 0.0
        load_kw = status.get("load_power", 0.0) or 0.0
        soc = status.get("battery_level", 0.0) or 0.0

        buy, sell = _get_current_prices(self.hass, self._entry_id)
        self._energy_acc.update(max(0.0, solar_kw), grid_kw, battery_kw, load_kw, buy, sell)
        energy_summary = self._energy_acc.as_dict()
        for status_key, summary_key in (
            ("daily_solar_energy_kwh", "pv_today_kwh"),
            ("daily_grid_import_kwh", "grid_import_today_kwh"),
            ("daily_grid_export_kwh", "grid_export_today_kwh"),
            ("daily_battery_charge_kwh", "charge_today_kwh"),
            ("daily_battery_discharge_kwh", "discharge_today_kwh"),
        ):
            value = status.get(status_key)
            if isinstance(value, (int, float)) and value >= 0:
                energy_summary[summary_key] = round(float(value), 3)

        return {
            "solar_power": solar_kw,
            "grid_power": grid_kw,
            "battery_power": battery_kw,
            "load_power": load_kw,
            "battery_level": soc,
            "battery_temperature": status.get("battery_temperature"),
            "pv1_power": status.get("pv1_power"),
            "pv2_power": status.get("pv2_power"),
            "pv3_power": status.get("pv3_power"),
            "pv1_voltage": status.get("pv1_voltage"),
            "pv2_voltage": status.get("pv2_voltage"),
            "pv3_voltage": status.get("pv3_voltage"),
            "pv1_current": status.get("pv1_current"),
            "pv2_current": status.get("pv2_current"),
            "pv3_current": status.get("pv3_current"),
            "mode": status.get("mode"),
            "backup_reserve": status.get("backup_reserve"),
            "min_soc": status.get("min_soc"),
            "energy_summary": energy_summary,
        }

    async def force_charge(self, duration_minutes: int, power_w: int) -> bool:
        return await self._controller.force_charge(duration_minutes, power_w)

    async def force_discharge(self, duration_minutes: int, power_w: int) -> bool:
        return await self._controller.force_discharge(duration_minutes, power_w)

    async def restore_normal(self) -> bool:
        return await self._controller.restore_normal()

    async def set_backup_reserve(self, percent: int) -> bool:
        return await self._controller.set_backup_reserve(percent)

    async def set_operation_mode(self, mode: str) -> bool:
        return await self._controller.set_operation_mode(mode)

    async def curtail(self, home_load_w: int | None = None) -> bool:
        return await self._controller.curtail(home_load_w)

    async def restore_curtailment(self) -> bool:
        return await self._controller.restore()

    async def async_shutdown(self) -> None:
        await self._controller.disconnect()


class SolarEdgeEnergyCoordinator(DataUpdateCoordinator):
    """Bridge coordinator for SolarEdge Home battery telemetry via HA entities."""

    def __init__(
        self,
        hass: HomeAssistant,
        entity_prefix: str = "solaredge",
        solaredge_entry_id: str | None = None,
        entry_id: str = "",
    ) -> None:
        from ..inverters.solaredge import SolarEdgeEnergyController

        self._entry_id = entry_id
        self._controller = SolarEdgeEnergyController(
            hass,
            entity_prefix=entity_prefix,
            solaredge_entry_id=solaredge_entry_id,
        )
        self._energy_acc = EnergyAccumulator(hass, "solaredge")
        self._daily_total_store = Store(
            hass,
            SOLAREDGE_DAILY_TOTALS_STORE_VERSION,
            f"power_sync.solaredge_daily_totals.{entry_id or entity_prefix or 'default'}",
        )
        self._daily_total_baselines_restored = False
        self._daily_total_baseline_date: str | None = None
        self._daily_total_import_baseline: float | None = None
        self._daily_total_export_baseline: float | None = None
        self._daily_total_recorder_baselines_checked = False
        self._validated = False

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_solaredge_energy",
            update_interval=timedelta(seconds=30),
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Return SolarEdge data assembled from HA entity states."""
        if not self._energy_acc._last_update:
            await self._energy_acc.async_restore()

        try:
            if not self._validated:
                await self._controller.connect()
                self._validated = True
            status = self._controller.get_status()
        except Exception as exc:
            if self.data:
                _LOGGER.warning(
                    "SolarEdge entity bridge read failed, returning stale data: %s",
                    exc,
                )
                return self.data
            raise UpdateFailed(f"SolarEdge entity bridge read failed: {exc}") from exc

        solar_kw = status.get("solar_power", 0.0) or 0.0
        grid_kw = status.get("grid_power", 0.0) or 0.0
        battery_kw = status.get("battery_power", 0.0) or 0.0
        load_kw = status.get("load_power", 0.0) or 0.0
        soc = status.get("battery_level")
        ev_power_kw = status.get("ev_power")

        buy, sell = _get_current_prices(self.hass, self._entry_id)
        self._energy_acc.update(max(0.0, solar_kw), grid_kw, battery_kw, load_kw, buy, sell)
        energy_summary = self._energy_acc.as_dict()
        await self._apply_daily_total_deltas(status, energy_summary)
        for status_key, summary_key in (
            ("daily_solar_energy_kwh", "pv_today_kwh"),
            ("daily_grid_import_kwh", "grid_import_today_kwh"),
            ("daily_grid_export_kwh", "grid_export_today_kwh"),
            ("daily_battery_charge_kwh", "charge_today_kwh"),
            ("daily_battery_discharge_kwh", "discharge_today_kwh"),
        ):
            value = status.get(status_key)
            if isinstance(value, (int, float)) and value >= 0:
                energy_summary[summary_key] = round(float(value), 3)

        data = {
            "solar_power": solar_kw,
            "grid_power": grid_kw,
            "battery_power": battery_kw,
            "load_power": load_kw,
            "battery_level": soc,
            "ev_power": ev_power_kw,
            "ev_power_kw": ev_power_kw,
            "ev_charger_type": "solaredge" if ev_power_kw is not None else None,
            "ev_charger_connected": ev_power_kw is not None and ev_power_kw > 0.05,
            "ev_charger_charging": ev_power_kw is not None and ev_power_kw > 0.05,
            "ev_charger_discharging": False,
            "last_update": dt_util.utcnow(),
            "battery_temperature": status.get("battery_temperature"),
            "battery_soh": status.get("battery_soh"),
            "backup_reserve": status.get("backup_reserve"),
            "min_soc": status.get("min_soc"),
            "control_available": status.get("control_available", False),
            "missing_control_entities": status.get("missing_control_entities", []),
            "control_entities": status.get("control_entities", {}),
            "energy_summary": energy_summary,
        }
        for idx in range(1, 5):
            key = f"pv{idx}_power"
            if status.get(key) is not None:
                data[key] = status[key]

        _LOGGER.debug(
            "SolarEdge entity data: solar=%.2f kW, grid=%.2f kW, battery=%.2f kW (%s%%), load=%.2f kW",
            data["solar_power"],
            data["grid_power"],
            data["battery_power"],
            data["battery_level"],
            data["load_power"],
        )

        return data

    async def _restore_daily_total_baselines(self) -> None:
        """Restore SolarEdge lifetime-counter baselines for the current day."""
        if self._daily_total_baselines_restored:
            return
        self._daily_total_baselines_restored = True
        try:
            stored = await self._daily_total_store.async_load()
        except Exception as exc:
            _LOGGER.debug("Failed to restore SolarEdge daily total baselines: %s", exc)
            return
        if not isinstance(stored, dict):
            return
        today = dt_util.now().date().isoformat()
        if stored.get("date") != today:
            return
        self._daily_total_baseline_date = today
        self._daily_total_import_baseline = self._float_or_none(stored.get("import_baseline_kwh"))
        self._daily_total_export_baseline = self._float_or_none(stored.get("export_baseline_kwh"))

    async def _save_daily_total_baselines(self) -> None:
        """Persist SolarEdge lifetime-counter baselines."""
        try:
            await self._daily_total_store.async_save(
                {
                    "date": self._daily_total_baseline_date,
                    "import_baseline_kwh": self._daily_total_import_baseline,
                    "export_baseline_kwh": self._daily_total_export_baseline,
                }
            )
        except Exception as exc:
            _LOGGER.debug("Failed to save SolarEdge daily total baselines: %s", exc)

    async def _apply_daily_total_deltas(self, status: dict[str, Any], energy_summary: dict[str, Any]) -> None:
        """Convert SolarEdge M1 lifetime counters into current-day deltas."""
        await self._restore_daily_total_baselines()
        today = dt_util.now().date().isoformat()
        total_import = self._float_or_none(status.get("total_grid_import_kwh"))
        total_export = self._float_or_none(status.get("total_grid_export_kwh"))
        changed = False

        if self._daily_total_baseline_date != today:
            self._daily_total_baseline_date = today
            self._daily_total_recorder_baselines_checked = False
            self._daily_total_import_baseline = await self._recorder_daily_total_baseline(
                status.get("total_grid_import_entity_id"),
                total_import,
            )
            self._daily_total_export_baseline = await self._recorder_daily_total_baseline(
                status.get("total_grid_export_entity_id"),
                total_export,
            )
            if self._daily_total_import_baseline is None:
                self._daily_total_import_baseline = total_import
            if self._daily_total_export_baseline is None:
                self._daily_total_export_baseline = total_export
            self._daily_total_recorder_baselines_checked = True
            changed = total_import is not None or total_export is not None
            if changed:
                _LOGGER.info(
                    "SolarEdge daily import/export baseline reset: import=%.3f export=%.3f kWh",
                    total_import or 0.0,
                    total_export or 0.0,
                )
        else:
            if self._daily_total_import_baseline is None and total_import is not None:
                self._daily_total_import_baseline = await self._recorder_daily_total_baseline(
                    status.get("total_grid_import_entity_id"),
                    total_import,
                )
                if self._daily_total_import_baseline is None:
                    self._daily_total_import_baseline = total_import
                changed = True
            if self._daily_total_export_baseline is None and total_export is not None:
                self._daily_total_export_baseline = await self._recorder_daily_total_baseline(
                    status.get("total_grid_export_entity_id"),
                    total_export,
                )
                if self._daily_total_export_baseline is None:
                    self._daily_total_export_baseline = total_export
                changed = True

        if not self._daily_total_recorder_baselines_checked:
            changed = await self._improve_daily_total_baselines_from_recorder(
                status,
                total_import,
                total_export,
            ) or changed
            self._daily_total_recorder_baselines_checked = True

        import_delta, import_changed = self._daily_total_delta(
            total_import,
            "_daily_total_import_baseline",
        )
        export_delta, export_changed = self._daily_total_delta(
            total_export,
            "_daily_total_export_baseline",
        )
        changed = changed or import_changed or export_changed

        if import_delta is not None:
            energy_summary["grid_import_today_kwh"] = import_delta
        if export_delta is not None:
            energy_summary["grid_export_today_kwh"] = export_delta
        if changed:
            await self._save_daily_total_baselines()

    async def _improve_daily_total_baselines_from_recorder(
        self,
        status: dict[str, Any],
        total_import: float | None,
        total_export: float | None,
    ) -> bool:
        """Lower same-day baselines when recorder has a closer midnight value."""
        changed = False
        if self._daily_total_import_baseline is not None:
            baseline = await self._recorder_daily_total_baseline(
                status.get("total_grid_import_entity_id"),
                total_import,
            )
            if baseline is not None and baseline < self._daily_total_import_baseline:
                self._daily_total_import_baseline = baseline
                changed = True
        if self._daily_total_export_baseline is not None:
            baseline = await self._recorder_daily_total_baseline(
                status.get("total_grid_export_entity_id"),
                total_export,
            )
            if baseline is not None and baseline < self._daily_total_export_baseline:
                self._daily_total_export_baseline = baseline
                changed = True
        return changed

    async def _recorder_daily_total_baseline(
        self,
        entity_id: Any,
        current_total: float | None,
    ) -> float | None:
        """Return the lifetime counter value at local midnight from recorder history."""
        if not entity_id or current_total is None:
            return None
        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.history import get_significant_states

            recorder = get_instance(self.hass)
            if recorder is None:
                return None

            now = dt_util.now()
            day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            history_start = day_start - timedelta(days=1)
            entity_id = str(entity_id)
            history = await recorder.async_add_executor_job(
                get_significant_states,
                self.hass,
                history_start,
                now,
                [entity_id],
            )
            states = sorted(
                (history or {}).get(entity_id, []) or [],
                key=lambda state: getattr(state, "last_changed", None) or getattr(state, "last_updated", None) or day_start,
            )
            if not states:
                return None

            last_before_midnight = None
            first_after_midnight = None
            for state in states:
                state_time = getattr(state, "last_changed", None) or getattr(state, "last_updated", None)
                value = self._history_energy_kwh(state)
                if state_time is None or value is None:
                    continue
                if self._datetime_lte(state_time, day_start):
                    last_before_midnight = value
                elif first_after_midnight is None:
                    first_after_midnight = value

            baseline = last_before_midnight if last_before_midnight is not None else first_after_midnight
            if baseline is None:
                return None
            if baseline > current_total:
                return None
            _LOGGER.debug(
                "SolarEdge recorder baseline for %s: %.3f kWh (current %.3f kWh)",
                entity_id,
                baseline,
                current_total,
            )
            return baseline
        except Exception as exc:
            _LOGGER.debug("Failed to derive SolarEdge daily baseline from recorder: %s", exc)
            return None

    def _daily_total_delta(self, total: float | None, baseline_attr: str) -> tuple[float | None, bool]:
        """Return daily delta from a lifetime total, resetting if the total rolls back."""
        if total is None:
            return (None, False)
        baseline = getattr(self, baseline_attr)
        if baseline is None:
            setattr(self, baseline_attr, total)
            return (0.0, True)
        if total < baseline:
            setattr(self, baseline_attr, total)
            return (0.0, True)
        return (round(total - baseline, 3), False)

    @staticmethod
    def _history_energy_kwh(state: Any) -> float | None:
        state_value = getattr(state, "state", None)
        if state_value in ("unavailable", "unknown", None, ""):
            return None
        try:
            value = float(state_value)
        except (TypeError, ValueError):
            return None
        unit = str((getattr(state, "attributes", {}) or {}).get("unit_of_measurement", "")).lower()
        if unit == "wh":
            return value / 1000.0
        if unit == "mwh":
            return value * 1000.0
        return value

    @staticmethod
    def _datetime_lte(left: Any, right: Any) -> bool:
        try:
            return left <= right
        except TypeError:
            left_tz = getattr(left, "tzinfo", None)
            right_tz = getattr(right, "tzinfo", None)
            if left_tz is not None and right_tz is None:
                right = right.replace(tzinfo=left_tz)
            elif left_tz is None and right_tz is not None:
                left = left.replace(tzinfo=right_tz)
            return left <= right

    @staticmethod
    def _float_or_none(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    async def async_shutdown(self) -> None:
        await self._controller.disconnect()

    async def force_charge(self, duration_minutes: int = 30, power_w: int = 0) -> bool:
        return await self._controller.force_charge(duration_minutes, power_w)

    async def force_discharge(self, duration_minutes: int = 30, power_w: int = 0) -> bool:
        return await self._controller.force_discharge(duration_minutes, power_w)

    async def restore_normal(self) -> bool:
        return await self._controller.restore_normal()

    async def set_backup_mode(self) -> bool:
        return await self._controller.set_backup_mode()

    async def restore_work_mode_from_idle(self) -> bool:
        return await self._controller.restore_work_mode_from_idle()

    async def set_backup_reserve(self, percent: int) -> bool:
        return await self._controller.set_backup_reserve(percent)

    async def get_backup_reserve(self) -> int | None:
        return await self._controller.get_backup_reserve()

    async def set_operation_mode(self, mode: str) -> bool:
        return await self._controller.set_operation_mode(mode)


class SajH2EnergyCoordinator(DataUpdateCoordinator):
    """Bridge coordinator for SAJ H2 / HS2 via the saj_h2_modbus integration."""

    def __init__(
        self,
        hass: HomeAssistant,
        saj_entry_id: str,
        battery_capacity_kwh: float = 10.0,
        entry_id: str = "",
        min_soc_pct: float = 5.0,
        inverter_rated_kw: float = 10.0,
    ) -> None:
        from ..inverters.saj_h2 import SajH2BatteryController

        self._entry_id = entry_id
        self._controller = SajH2BatteryController(
            hass,
            saj_entry_id=saj_entry_id,
            battery_capacity_kwh=battery_capacity_kwh,
            min_soc_pct=min_soc_pct,
            inverter_rated_kw=inverter_rated_kw,
        )
        self._energy_acc = EnergyAccumulator(hass, "saj_h2")

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_saj_h2_energy",
            update_interval=timedelta(seconds=30),
        )

    def set_min_soc_pct(self, min_soc_pct: float) -> None:
        """Propagate min_soc updates from the optimizer's backup_reserve setting."""
        self._controller.set_min_soc_pct(min_soc_pct)

    async def _async_update_data(self) -> dict[str, Any]:
        """Return SAJ data assembled from HA entity states."""
        if not self._energy_acc._last_update:
            await self._energy_acc.async_restore()

        if not self._controller._entity_map:
            self._controller._discover_entities()

        try:
            status = self._controller.get_status()
        except Exception as exc:
            if self.data:
                _LOGGER.warning("SAJ H2 entity read failed, returning stale data: %s", exc)
                return self.data
            raise UpdateFailed(f"SAJ H2 entity read failed: {exc}") from exc

        solar_kw = status.get("solar_power", 0.0) or 0.0
        grid_kw = status.get("grid_power", 0.0) or 0.0
        battery_kw = status.get("battery_power", 0.0) or 0.0
        load_kw = status.get("load_power", 0.0) or 0.0
        soc = status.get("battery_level", 0.0) or 0.0

        buy, sell = _get_current_prices(self.hass, self._entry_id)
        self._energy_acc.update(max(0.0, solar_kw), grid_kw, battery_kw, load_kw, buy, sell)
        energy_summary = self._energy_acc.as_dict()
        for status_key, summary_key in (
            ("daily_solar_energy_kwh", "pv_today_kwh"),
            ("daily_grid_import_kwh", "grid_import_today_kwh"),
            ("daily_grid_export_kwh", "grid_export_today_kwh"),
        ):
            value = status.get(status_key)
            if isinstance(value, (int, float)) and value >= 0:
                energy_summary[summary_key] = round(float(value), 3)

        return {
            "solar_power": solar_kw,
            "grid_power": grid_kw,
            "battery_power": battery_kw,
            "load_power": load_kw,
            "battery_level": soc,
            "pv1_power": status.get("pv1_power"),
            "pv2_power": status.get("pv2_power"),
            "pv3_power": status.get("pv3_power"),
            "battery_temperature": status.get("battery_temperature"),
            "battery_soh": status.get("battery_soh"),
            "battery_capacity_kwh": status.get("battery_capacity_kwh"),
            "battery_max_charge_power_w": status.get("battery_max_charge_power_w"),
            "battery_max_discharge_power_w": status.get("battery_max_discharge_power_w"),
            "app_mode": status.get("app_mode"),
            "energy_summary": energy_summary,
        }

    async def force_charge(self, duration_minutes: int, power_w: int) -> bool:
        return await self._controller.force_charge(duration_minutes, power_w)

    async def force_discharge(self, duration_minutes: int, power_w: int) -> bool:
        return await self._controller.force_discharge(duration_minutes, power_w)

    async def restore_normal(self) -> bool:
        return await self._controller.restore_normal()

    async def set_backup_mode(self) -> bool:
        """IDLE hold — lock battery at current SOC, no discharge."""
        return await self._controller.set_idle()

    async def restore_work_mode_from_idle(self) -> bool:
        """Exit IDLE — restore full self-consumption."""
        return await self._controller.restore_normal()

    async def async_shutdown(self) -> None:
        await self._controller.disconnect()


class FroniusReservaEnergyCoordinator(DataUpdateCoordinator):
    """Bridge coordinator for Fronius GEN24 storage via the fronius_modbus integration."""

    def __init__(
        self,
        hass: HomeAssistant,
        fronius_entry_id: str,
        battery_capacity_kwh: float = 9.6,
        entry_id: str = "",
        max_charge_kw: float = 5.0,
        max_discharge_kw: float = 5.0,
    ) -> None:
        from ..inverters.fronius_reserva import FroniusReservaBatteryController

        self._entry_id = entry_id
        self._controller = FroniusReservaBatteryController(
            hass,
            fronius_entry_id=fronius_entry_id,
            battery_capacity_kwh=battery_capacity_kwh,
            max_charge_kw=max_charge_kw,
            max_discharge_kw=max_discharge_kw,
        )
        self._energy_acc = EnergyAccumulator(hass, "fronius_reserva")

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_fronius_reserva_energy",
            update_interval=timedelta(seconds=30),
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Return Fronius GEN24 storage data assembled from HA entity states."""
        if not self._energy_acc._last_update:
            await self._energy_acc.async_restore()

        if not self._controller._entity_map:
            self._controller._discover_entities()

        try:
            status = self._controller.get_status()
        except Exception as exc:
            if self.data:
                _LOGGER.warning("Fronius GEN24 storage entity read failed, returning stale data: %s", exc)
                return self.data
            raise UpdateFailed(f"Fronius GEN24 storage entity read failed: {exc}") from exc

        solar_kw = status.get("solar_power", 0.0) or 0.0
        grid_kw = status.get("grid_power", 0.0) or 0.0
        battery_kw = status.get("battery_power", 0.0) or 0.0
        load_kw = status.get("load_power", 0.0) or 0.0
        soc = status.get("battery_level")
        if soc is None and self.data:
            soc = self.data.get("battery_level")
            if soc is not None:
                _LOGGER.warning(
                    "Fronius GEN24 storage SOC unavailable; using previous %.1f%% reading",
                    soc,
                )

        buy, sell = _get_current_prices(self.hass, self._entry_id)
        self._energy_acc.update(max(0.0, solar_kw), grid_kw, battery_kw, load_kw, buy, sell)

        return {
            "solar_power": solar_kw,
            "grid_power": grid_kw,
            "battery_power": battery_kw,
            "load_power": load_kw,
            "battery_level": soc,
            "battery_temperature": status.get("battery_temperature"),
            "battery_capacity_kwh": status.get("battery_capacity_kwh"),
            "battery_max_charge_power_w": status.get("battery_max_charge_power_w"),
            "battery_max_discharge_power_w": status.get("battery_max_discharge_power_w"),
            "battery_max_charge_power": (
                status.get("battery_max_charge_power_w") / 1000.0
                if status.get("battery_max_charge_power_w") else None
            ),
            "battery_max_discharge_power": (
                status.get("battery_max_discharge_power_w") / 1000.0
                if status.get("battery_max_discharge_power_w") else None
            ),
            "backup_reserve": status.get("backup_reserve"),
            "min_soc": status.get("min_soc"),
            "mode": status.get("mode"),
            "energy_summary": self._energy_acc.as_dict(),
        }

    async def force_charge(self, duration_minutes: int, power_w: int) -> bool:
        return await self._controller.force_charge(duration_minutes, power_w)

    async def force_discharge(self, duration_minutes: int, power_w: int) -> bool:
        return await self._controller.force_discharge(duration_minutes, power_w)

    async def restore_normal(self) -> bool:
        return await self._controller.restore_normal()

    async def set_backup_reserve(self, percent: int) -> bool:
        return await self._controller.set_backup_reserve(percent)

    async def get_backup_reserve(self) -> int | None:
        return await self._controller.get_backup_reserve()

    async def set_backup_mode(self) -> bool:
        """IDLE hold — lock battery at current SOC, no charge or discharge."""
        return await self._controller.set_idle()

    async def restore_work_mode_from_idle(self) -> bool:
        """Exit IDLE — restore automatic storage control."""
        return await self._controller.restore_normal()

    async def async_shutdown(self) -> None:
        await self._controller.disconnect()


class NeovoltEnergyCoordinator(DataUpdateCoordinator):
    """Bridge coordinator for Neovolt / Bytewatt via the Neovolt Modbus integration."""

    def __init__(
        self,
        hass: HomeAssistant,
        neovolt_entry_id: str | list[str],
        entry_id: str = "",
        max_charge_kw: float = 5.0,
        max_discharge_kw: float = 5.0,
        min_soc_pct: float = 10.0,
        surplus_balancer_mode: str = "auto",
        soc_balance_tolerance_pct: float = 5.0,
        battery_capacities_kwh: list[float | int | str | None] | None = None,
    ) -> None:
        from ..inverters.neovolt import NeovoltFleetBatteryController

        self._entry_id = entry_id
        neovolt_entry_ids = (
            [neovolt_entry_id]
            if isinstance(neovolt_entry_id, str)
            else list(neovolt_entry_id)
        )
        self._controller = NeovoltFleetBatteryController(
            hass,
            neovolt_entry_ids=neovolt_entry_ids,
            max_charge_kw=max_charge_kw,
            max_discharge_kw=max_discharge_kw,
            min_soc_pct=min_soc_pct,
            surplus_balancer_mode=surplus_balancer_mode,
            soc_balance_tolerance_pct=soc_balance_tolerance_pct,
            battery_capacities_kwh=battery_capacities_kwh,
        )
        self._energy_acc = EnergyAccumulator(hass, "neovolt")

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_neovolt_energy",
            update_interval=timedelta(seconds=30),
        )

    def set_min_soc_pct(self, min_soc_pct: float) -> None:
        """Propagate min_soc updates from the optimizer backup reserve setting."""
        self._controller.set_min_soc_pct(min_soc_pct)

    async def _async_update_data(self) -> dict[str, Any]:
        """Return Neovolt data assembled from HA entity states."""
        if not self._energy_acc._last_update:
            await self._energy_acc.async_restore()

        if hasattr(self._controller, "_entity_map") and not self._controller._entity_map:
            self._controller._discover_entities()

        try:
            status = self._controller.get_status()
        except Exception as exc:
            if self.data:
                _LOGGER.warning("Neovolt entity read failed, returning stale data: %s", exc)
                return self.data
            raise UpdateFailed(f"Neovolt entity read failed: {exc}") from exc

        try:
            surplus_balancer = await self._controller.balance_solar_surplus(status)
        except Exception as exc:
            _LOGGER.warning("Neovolt surplus balancer skipped: %s", exc)
            surplus_balancer = status.get("surplus_balancer", {})

        solar_kw = status.get("solar_power", 0.0) or 0.0
        grid_kw = status.get("grid_power", 0.0) or 0.0
        battery_kw = status.get("battery_power", 0.0) or 0.0
        load_kw = status.get("load_power", 0.0) or 0.0
        soc = status.get("battery_level", 0.0) or 0.0

        buy, sell = _get_current_prices(self.hass, self._entry_id)
        self._energy_acc.update(max(0.0, solar_kw), grid_kw, battery_kw, load_kw, buy, sell)

        return {
            "solar_power": solar_kw,
            "grid_power": grid_kw,
            "battery_power": battery_kw,
            "load_power": load_kw,
            "battery_level": soc,
            "battery_capacity_kwh": status.get("battery_capacity_kwh"),
            "battery_soh": status.get("battery_soh"),
            "battery_max_charge_power_w": status.get("battery_max_charge_power_w"),
            "battery_max_discharge_power_w": status.get("battery_max_discharge_power_w"),
            "neovolt_surplus_balancer": surplus_balancer,
            "energy_summary": self._energy_acc.as_dict(),
        }

    async def force_charge(
        self,
        duration_minutes: int,
        power_w: int,
        *,
        preserve_restore_modes: bool = False,
    ) -> bool:
        return await self._controller.force_charge(
            duration_minutes,
            power_w,
            preserve_restore_modes=preserve_restore_modes,
        )

    async def force_discharge(
        self,
        duration_minutes: int,
        power_w: int,
        *,
        preserve_restore_modes: bool = False,
    ) -> bool:
        return await self._controller.force_discharge(
            duration_minutes,
            power_w,
            preserve_restore_modes=preserve_restore_modes,
        )

    async def restore_normal(self) -> bool:
        return await self._controller.restore_normal()

    async def set_backup_reserve(self, percent: int) -> bool:
        return await self._controller.set_backup_reserve(percent)

    async def set_backup_mode(self) -> bool:
        return await self._controller.set_idle()

    async def restore_work_mode_from_idle(self) -> bool:
        return await self._controller.restore_normal()

    async def async_shutdown(self) -> None:
        await self._controller.disconnect()


class AnkerSolixEnergyCoordinator(DataUpdateCoordinator):
    """Coordinator for Anker Solix direct Modbus or HA entity bridge."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        entry_id: str = "",
        connection_type: str = "modbus",
        host: str | None = None,
        port: int = 502,
        slave_id: int = 1,
        integration_domain: str = "anker_solix_official",
        anker_entry_id: str | None = None,
        entity_prefix: str | None = None,
        battery_capacity_kwh: float | None = None,
        max_charge_kw: float = 5.0,
        max_discharge_kw: float = 5.0,
    ) -> None:
        from ..const import ANKER_SOLIX_CONNECTION_MODBUS
        from ..inverters.anker_solix import (
            AnkerSolixEntityController,
            AnkerSolixX1ModbusController,
        )

        self._entry_id = entry_id
        self.connection_type = connection_type
        if connection_type == ANKER_SOLIX_CONNECTION_MODBUS:
            self._controller = AnkerSolixX1ModbusController(
                host=host or "",
                port=port,
                slave_id=slave_id,
                battery_capacity_kwh=battery_capacity_kwh,
                max_charge_kw=max_charge_kw,
                max_discharge_kw=max_discharge_kw,
            )
        else:
            self._controller = AnkerSolixEntityController(
                hass,
                integration_domain=integration_domain,
                config_entry_id=anker_entry_id,
                entity_prefix=entity_prefix,
                battery_capacity_kwh=battery_capacity_kwh,
                max_charge_kw=max_charge_kw,
                max_discharge_kw=max_discharge_kw,
            )
        self._energy_acc = EnergyAccumulator(hass, "anker_solix")

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_anker_solix_energy",
            update_interval=timedelta(seconds=30),
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Return Anker Solix data from direct Modbus or HA entity states."""
        if not self._energy_acc._last_update:
            await self._energy_acc.async_restore()

        try:
            status = await self._controller.get_status() if asyncio.iscoroutinefunction(self._controller.get_status) else self._controller.get_status()
        except Exception as exc:
            if self.data:
                _LOGGER.warning("Anker Solix read failed, returning stale data: %s", exc)
                return self.data
            raise UpdateFailed(f"Anker Solix read failed: {exc}") from exc

        solar_kw = status.get("solar_power", 0.0) or 0.0
        grid_kw = status.get("grid_power", 0.0) or 0.0
        battery_kw = status.get("battery_power", 0.0) or 0.0
        load_kw = status.get("load_power", 0.0) or 0.0
        soc = status.get("battery_level", 0.0) or 0.0

        buy, sell = _get_current_prices(self.hass, self._entry_id)
        self._energy_acc.update(max(0.0, solar_kw), grid_kw, battery_kw, load_kw, buy, sell)

        return {
            "solar_power": solar_kw,
            "grid_power": grid_kw,
            "battery_power": battery_kw,
            "load_power": load_kw,
            "battery_level": soc,
            "battery_capacity_kwh": status.get("battery_capacity_kwh"),
            "battery_max_charge_power_w": status.get("battery_max_charge_power_w"),
            "battery_max_discharge_power_w": status.get("battery_max_discharge_power_w"),
            "battery_status": status.get("battery_status"),
            "operating_mode": status.get("operating_mode") or status.get("mode"),
            "control_path": status.get("control_path"),
            "dispatch_supported": status.get("dispatch_supported", True),
            "energy_summary": self._energy_acc.as_dict(),
        }

    async def force_charge(self, duration_minutes: int, power_w: int) -> bool:
        return await self._controller.force_charge(duration_minutes, power_w)

    async def force_discharge(self, duration_minutes: int, power_w: int) -> bool:
        return await self._controller.force_discharge(duration_minutes, power_w)

    async def restore_normal(self) -> bool:
        return await self._controller.restore_normal()

    async def set_self_consumption_mode(self) -> bool:
        if hasattr(self._controller, "set_self_consumption_mode"):
            return await self._controller.set_self_consumption_mode()
        return await self.restore_normal()

    async def set_backup_mode(self) -> bool:
        if hasattr(self._controller, "set_backup_mode"):
            return await self._controller.set_backup_mode()
        return False

    async def restore_work_mode_from_idle(self) -> bool:
        if hasattr(self._controller, "restore_work_mode_from_idle"):
            return await self._controller.restore_work_mode_from_idle()
        return await self.restore_normal()

    async def set_backup_reserve(self, percent: int) -> bool:
        if hasattr(self._controller, "set_backup_reserve"):
            return await self._controller.set_backup_reserve(percent)
        return False

    async def get_backup_reserve(self) -> int | None:
        if hasattr(self._controller, "get_backup_reserve"):
            return await self._controller.get_backup_reserve()
        return None

    async def async_shutdown(self) -> None:
        await self._controller.disconnect()


class ESYSunhomeEnergyCoordinator(DataUpdateCoordinator):
    """Bridge coordinator for ESY Sunhome via the upstream esy_sunhome integration.

    Reads entity states published by the esy_sunhome integration (which handles the
    ESY cloud MQTT connection) and assembles the standard PowerSync data dict.
    Control commands are sent via HA's select.select_option service on the ESY
    mode-select entity (Regular Mode / Emergency Mode / Electricity Sell Mode).

    W-level charge/discharge setpoints are not supported by ESY Sunhome hardware;
    force_charge/force_discharge map to coarse mode switches only.
    """

    ESY_DOMAIN = "esy_sunhome"

    # Maps ESY sensor translation_key → internal slot name
    _SENSOR_KEYS = {
        "batterySoc": "battery_soc",
        "pvPower": "pv_w",
        "gridPower": "grid_w",
        "loadPower": "load_w",
        "batteryImport": "battery_import_w",
        "batteryExport": "battery_export_w",
        "batteryPower": "battery_abs_w",
        "ratedPower": "rated_w",
        "inverterTemp": "inv_temp",
        "dailyPowerGeneration": "daily_gen_kwh",
        "dailyPowerConsumption": "daily_load_kwh",
        "dailyBattCharge": "daily_charge_kwh",
        "dailyBattDischarge": "daily_discharge_kwh",
        "batteryStatusText": "battery_status_text",
        "batterySoh": "battery_soh",
    }
    _MODE_SELECT_KEY = "code"

    def __init__(
        self,
        hass: HomeAssistant,
        esy_entry_id: str,
        entry_id: str = "",
    ) -> None:
        self._esy_entry_id = esy_entry_id
        self._entry_id = entry_id
        self._entity_map: dict[str, str] = {}   # esy_key → ha entity_id
        self._mode_select_entity_id: str | None = None
        self._energy_acc = EnergyAccumulator(hass, "esy_sunhome")

        super().__init__(
            hass,
            _LOGGER,
            name="ESY Sunhome Energy",
            update_interval=timedelta(seconds=30),
        )

    def _discover_entities(self) -> None:
        """Discover esy_sunhome entities from the HA entity registry once."""
        from homeassistant.helpers import entity_registry as er

        esy_entry = self.hass.config_entries.async_get_entry(self._esy_entry_id)
        if not esy_entry:
            _LOGGER.warning("ESY Sunhome config entry %s not found", self._esy_entry_id)
            return

        # device_id in ESY config entry is the numeric cloud device ID, used as
        # the unique_id prefix: "{device_id}_{translation_key}"
        device_id = esy_entry.data.get("device_id", "")
        if not device_id:
            _LOGGER.warning("ESY Sunhome config entry missing device_id")
            return

        registry = er.async_get(self.hass)
        uid_to_eid: dict[str, str] = {
            reg_entry.unique_id: reg_entry.entity_id
            for reg_entry in er.async_entries_for_config_entry(registry, self._esy_entry_id)
            if reg_entry.unique_id
        }

        for esy_key in self._SENSOR_KEYS:
            uid = f"{device_id}_{esy_key}"
            if uid in uid_to_eid:
                self._entity_map[esy_key] = uid_to_eid[uid]

        mode_uid = f"{device_id}_{self._MODE_SELECT_KEY}"
        self._mode_select_entity_id = uid_to_eid.get(mode_uid)

        _LOGGER.info(
            "ESY Sunhome entity discovery: %d/%d sensors found, mode_select=%s",
            len(self._entity_map), len(self._SENSOR_KEYS), self._mode_select_entity_id,
        )

    def _state_float(self, esy_key: str, default: float | None = None) -> float | None:
        entity_id = self._entity_map.get(esy_key)
        if not entity_id:
            return default
        state = self.hass.states.get(entity_id)
        if not state or state.state in ("unavailable", "unknown", ""):
            return default
        try:
            return float(state.state)
        except (ValueError, TypeError):
            return default

    async def _async_update_data(self) -> dict[str, Any]:
        """Return ESY Sunhome data assembled from HA entity states."""
        if not self._energy_acc._last_update:
            await self._energy_acc.async_restore()

        if not self._entity_map:
            self._discover_entities()

        if not self._entity_map:
            if self.data:
                _LOGGER.warning("ESY Sunhome: entity map empty, returning stale data")
                return self.data
            raise UpdateFailed("ESY Sunhome entities not yet available — is esy_sunhome integration running?")

        pv_w = self._state_float("pvPower", 0.0) or 0.0
        grid_w = self._state_float("gridPower", 0.0) or 0.0   # positive = import (already HA convention)
        load_w = self._state_float("loadPower", 0.0) or 0.0
        battery_import_w = self._state_float("batteryImport")
        battery_export_w = self._state_float("batteryExport")
        battery_abs_w = self._state_float("batteryPower", 0.0) or 0.0

        # Signed battery power: positive = discharging, negative = charging
        if battery_import_w is not None or battery_export_w is not None:
            battery_w = (battery_export_w or 0.0) - (battery_import_w or 0.0)
        else:
            battery_w = battery_abs_w  # unsigned fallback; direction unknown

        solar_kw = pv_w / 1000.0
        grid_kw = grid_w / 1000.0
        battery_kw = battery_w / 1000.0
        load_kw = load_w / 1000.0
        battery_level = self._state_float("batterySoc")

        rated_w = self._state_float("ratedPower", 5000.0) or 5000.0

        work_mode_name = None
        if self._mode_select_entity_id:
            ms = self.hass.states.get(self._mode_select_entity_id)
            if ms and ms.state not in ("unavailable", "unknown"):
                work_mode_name = ms.state

        buy, sell = _get_current_prices(self.hass, self._entry_id)
        self._energy_acc.update(max(0.0, solar_kw), grid_kw, battery_kw, load_kw, buy, sell)

        _LOGGER.debug(
            "ESY Sunhome data: solar=%.2f kW, grid=%.2f kW, battery=%.2f kW (%.0f%%), load=%.2f kW",
            solar_kw, grid_kw, battery_kw, battery_level or 0.0, load_kw,
        )

        return {
            "solar_power": solar_kw,
            "grid_power": grid_kw,
            "battery_power": battery_kw,
            "load_power": load_kw,
            "battery_level": battery_level,
            "last_update": dt_util.utcnow(),
            "work_mode": work_mode_name,
            "work_mode_name": work_mode_name,
            "battery_max_charge_power_w": rated_w,
            "battery_max_discharge_power_w": rated_w,
            "battery_max_charge_power": round(rated_w / 1000.0, 2),
            "battery_max_discharge_power": round(rated_w / 1000.0, 2),
            "inverter_temperature": self._state_float("inverterTemp"),
            "battery_status_text": (
                self.hass.states.get(self._entity_map["batteryStatusText"]).state
                if "batteryStatusText" in self._entity_map
                   and self.hass.states.get(self._entity_map["batteryStatusText"]) is not None
                   and self.hass.states.get(self._entity_map["batteryStatusText"]).state
                      not in ("unavailable", "unknown")
                else None
            ),
            "battery_soh": self._state_float("batterySoh"),
            "daily_generation_kwh": self._state_float("dailyPowerGeneration"),
            "daily_consumption_kwh": self._state_float("dailyPowerConsumption"),
            "daily_battery_charge_kwh": self._state_float("dailyBattCharge"),
            "daily_battery_discharge_kwh": self._state_float("dailyBattDischarge"),
            "energy_summary": self._energy_acc.as_dict(),
        }

    async def _set_mode(self, option: str) -> bool:
        """Switch the ESY operating mode via its mode-select entity."""
        if not self._mode_select_entity_id:
            self._discover_entities()
        if not self._mode_select_entity_id:
            _LOGGER.error("ESY Sunhome: mode select entity not found — cannot change mode")
            return False
        try:
            await self.hass.services.async_call(
                "select", "select_option",
                {"entity_id": self._mode_select_entity_id, "option": option},
                blocking=True,
            )
            _LOGGER.info("ESY Sunhome: set mode → '%s'", option)
            return True
        except Exception as exc:
            _LOGGER.error("ESY Sunhome: failed to set mode '%s': %s", option, exc)
            return False

    async def force_charge(self, duration_minutes: int = 30, power_w: float = 0) -> bool:
        """Force grid-charge via Emergency Mode (rate is inverter-decided)."""
        return await self._set_mode("Emergency Mode")

    async def force_discharge(self, duration_minutes: int = 30, power_w: float = 0) -> bool:
        """Force grid-export via Electricity Sell Mode (rate is inverter-decided)."""
        return await self._set_mode("Electricity Sell Mode")

    async def restore_normal(self) -> bool:
        """Return to Regular Mode (self-consumption)."""
        return await self._set_mode("Regular Mode")

    async def set_backup_reserve(self, percent: int) -> bool:
        _LOGGER.info("ESY Sunhome: set_backup_reserve not supported on this hardware")
        return True

    async def set_self_consumption_mode(self) -> bool:
        return await self._set_mode("Regular Mode")

    async def set_autonomous_mode(self) -> bool:
        return await self._set_mode("Regular Mode")

    async def set_work_mode(self, mode: str) -> bool:
        _mode_map = {
            "self_consumption": "Regular Mode",
            "regular": "Regular Mode",
            "feed_in": "Electricity Sell Mode",
            "electricity_sell": "Electricity Sell Mode",
            "backup": "Emergency Mode",
            "emergency": "Emergency Mode",
        }
        return await self._set_mode(_mode_map.get(mode.lower(), "Regular Mode"))

    async def restore_work_mode_from_idle(self) -> bool:
        return await self._set_mode("Regular Mode")

    async def set_charge_rate_limit(self, amps: float) -> bool:
        _LOGGER.info("ESY Sunhome: set_charge_rate_limit not supported on this hardware")
        return True

    async def set_discharge_rate_limit(self, amps: float) -> bool:
        _LOGGER.info("ESY Sunhome: set_discharge_rate_limit not supported on this hardware")
        return True

    async def async_shutdown(self) -> None:
        pass


