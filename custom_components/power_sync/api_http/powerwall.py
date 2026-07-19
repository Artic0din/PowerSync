"""HTTP views for PowerSync."""
from __future__ import annotations

import logging
import aiohttp
import asyncio
import time
from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util
from typing import Any
from ..const import (
    DOMAIN,
    CONF_TESLA_ENERGY_SITE_ID,
    CONF_BATTERY_CURTAILMENT_ENABLED,
    CONF_FLEET_API_BASE_URL,
    get_tesla_api_base_url,
    CONF_AC_INVERTER_CURTAILMENT_ENABLED,
    CONF_INVERTER_BRAND,
    CONF_INVERTER_MODEL,
    CONF_INVERTER_HOST,
    CONF_INVERTER_PORT,
    CONF_INVERTER_SLAVE_ID,
    CONF_INVERTER_TOKEN,
    CONF_INVERTER_RATED_POWER_W,
    CONF_FRONIUS_LOAD_FOLLOWING,
    CONF_ENPHASE_USERNAME,
    CONF_ENPHASE_PASSWORD,
    CONF_ENPHASE_SERIAL,
    CONF_ENPHASE_NORMAL_PROFILE,
    CONF_ENPHASE_ZERO_EXPORT_PROFILE,
    CONF_ENPHASE_IS_INSTALLER,
    DEFAULT_INVERTER_PORT,
    DEFAULT_INVERTER_SLAVE_ID,
    CONF_SIGENERGY_STATION_ID,
    CONF_SIGENERGY_EXPORT_LIMIT_KW,
    CONF_ALPHAESS_MODBUS_HOST,
    BATTERY_SYSTEM_NEOVOLT,
    CONF_BATTERY_SYSTEM,
    CONF_SUNGROW_HOST,
    BATTERY_SYSTEM_FOXESS,
    CONF_FOXESS_HOST,
    CONF_FOXESS_SERIAL_PORT,
    CONF_GOODWE_HOST,
)
from ..history_migration import (
    apply_history_relink,
    preview_history_relink,
)
from ..inverters import get_inverter_controller
from .. import (
    _LOGGER,
    _battery_health_payload_is_newer,
    _calculate_cost_from_statistics,
    _calculate_cost_from_tariff,
    _calendar_result_from_energy_summary,
    _configured_battery_capacity_kwh,
    _current_capacity_from_soh_kwh,
    _find_calendar_energy_summary_source,
    _get_neovolt_entry_ids,
    _get_tesla_coord_for_view,
    _is_history_relink_entry,
    _resolve_history_relink_entry,
    get_tesla_api_token,
)

class CalendarHistoryView(HomeAssistantView):
    """HTTP view to get calendar history for mobile app."""

    url = "/api/power_sync/calendar_history"
    name = "api:power_sync:calendar_history"
    requires_auth = True
    _CACHE_TTL_SECONDS = 300
    _REQUEST_TIMEOUT_SECONDS = 6.0

    def __init__(self, hass: HomeAssistant):
        """Initialize the view."""
        self._hass = hass
        self._cache: dict[tuple[str, str, str], tuple[float, dict[str, Any], int]] = {}
        self._inflight: dict[tuple[str, str, str], asyncio.Task[tuple[dict[str, Any], int]]] = {}

    def _calendar_cache_key(
        self,
        tesla_coordinator: Any,
        period: str,
        end_date: str | None,
    ) -> tuple[str, str, str]:
        """Return a stable cache key for one calendar-history request."""
        site_id = str(getattr(tesla_coordinator, "site_id", "") or "unknown")
        return (site_id, period, end_date or "")

    def _cached_calendar_result(
        self,
        key: tuple[str, str, str],
    ) -> tuple[dict[str, Any], int] | None:
        """Return a fresh cached calendar-history response, if one exists."""
        cached = self._cache.get(key)
        if not cached:
            return None
        cached_at, result, status = cached
        if time.monotonic() - cached_at > self._CACHE_TTL_SECONDS:
            return None
        response = dict(result)
        response["cached"] = True
        return response, status

    def _store_calendar_result(
        self,
        key: tuple[str, str, str],
        result: dict[str, Any],
        status: int,
    ) -> None:
        """Cache successful calendar-history responses for short-term reuse."""
        if status == 200 and result.get("success"):
            self._cache[key] = (time.monotonic(), dict(result), status)

    async def _build_calendar_history_response(
        self,
        *,
        tesla_coordinator: Any,
        tariff_schedule: dict | None,
        period: str,
        end_date: str | None,
    ) -> tuple[dict[str, Any], int]:
        """Fetch and shape calendar history without blocking duplicate requests."""
        try:
            history = await tesla_coordinator.async_get_calendar_history(period=period, end_date=end_date)
        except Exception as e:
            _LOGGER.error(f"Error fetching calendar history: {e}")
            return {"success": False, "error": str(e)}, 500

        if not history:
            _LOGGER.error("Failed to fetch calendar history")
            return {
                "success": False,
                "error": "Failed to fetch calendar history from Tesla API",
            }, 500

        time_series = []
        for entry_data in history.get("time_series", []):
            time_series.append({
                "timestamp": entry_data.get("timestamp", ""),
                # Normalized fields for compatibility
                "solar_generation": entry_data.get("solar_energy_exported", 0),
                "battery_discharge": entry_data.get("battery_energy_exported", 0),
                "battery_charge": entry_data.get("battery_energy_imported", 0),
                "grid_import": entry_data.get("grid_energy_imported", 0),
                "grid_export": entry_data.get("grid_energy_exported_from_solar", 0) + entry_data.get("grid_energy_exported_from_battery", 0),
                "home_consumption": entry_data.get("consumer_energy_imported_from_grid", 0) + entry_data.get("consumer_energy_imported_from_solar", 0) + entry_data.get("consumer_energy_imported_from_battery", 0),
                # Detailed breakdown fields from Tesla API (for detail screens)
                "solar_energy_exported": entry_data.get("solar_energy_exported", 0),
                "battery_energy_exported": entry_data.get("battery_energy_exported", 0),
                "battery_energy_imported_from_grid": entry_data.get("battery_energy_imported_from_grid", 0),
                "battery_energy_imported_from_solar": entry_data.get("battery_energy_imported_from_solar", 0),
                "consumer_energy_imported_from_grid": entry_data.get("consumer_energy_imported_from_grid", 0),
                "consumer_energy_imported_from_solar": entry_data.get("consumer_energy_imported_from_solar", 0),
                "consumer_energy_imported_from_battery": entry_data.get("consumer_energy_imported_from_battery", 0),
                "grid_energy_exported_from_solar": entry_data.get("grid_energy_exported_from_solar", 0),
                "grid_energy_exported_from_battery": entry_data.get("grid_energy_exported_from_battery", 0),
            })

        result = {
            "success": True,
            "period": period,
            "time_series": time_series,
            "serial_number": history.get("serial_number"),
            "installation_date": history.get("installation_date"),
        }
        cost_summary = await _calculate_cost_from_statistics(self._hass, period, end_date)
        if not cost_summary and tariff_schedule:
            cost_summary = _calculate_cost_from_tariff(tariff_schedule, time_series)
        if cost_summary:
            load_kwh = sum(e.get("home_consumption", 0) for e in time_series) / 1000
            if load_kwh > 0:
                cost_summary["avg_cost_per_kwh"] = round(
                    ((cost_summary.get("import_cost") or 0) - (cost_summary.get("export_earnings") or 0)) / load_kwh, 4
                )
            result["cost_summary"] = cost_summary

        _LOGGER.info(f"✅ Calendar history HTTP response: {len(time_series)} records for period '{period}'")
        return result, 200

    async def get(self, request: web.Request) -> web.Response:
        """Handle GET request for calendar history."""
        # Get period from query params (default: day)
        period = request.query.get("period", "day")
        # Get end_date from query params (format: YYYY-MM-DD)
        end_date = request.query.get("end_date")

        # Validate period
        valid_periods = ["day", "week", "month", "year"]
        if period not in valid_periods:
            return web.json_response(
                {"success": False, "error": f"Invalid period. Must be one of: {valid_periods}"},
                status=400
            )

        _LOGGER.info(f"📊 Calendar history HTTP request for period: {period}, end_date: {end_date}")

        # Find the power_sync entry and coordinator
        # Check ALL entries, not just the first one (important during reload)
        tesla_coordinator = None
        for _entry_id, data in self._hass.data.get(DOMAIN, {}).items():
            if isinstance(data, dict):
                # Look for Tesla coordinator (this is the main data source for calendar history)
                if "tesla_coordinator" in data and data["tesla_coordinator"] is not None:
                    tesla_coordinator = data["tesla_coordinator"]
                    break  # Found it, no need to continue

        # Look up tariff schedule for cost calculation (shared across all battery types)
        tariff_schedule = None
        for _eid, _data in self._hass.data.get(DOMAIN, {}).items():
            if isinstance(_data, dict):
                ts = _data.get("tariff_schedule")
                if ts and ts.get("buy_rates"):
                    tariff_schedule = ts
                    break

        summary_system, summary_coordinator, summary_entry_id = _find_calendar_energy_summary_source(self._hass)
        if summary_coordinator and not tesla_coordinator:
            _LOGGER.info(
                "Calendar history using %s daily energy summary",
                summary_system,
            )
            result = await _calendar_result_from_energy_summary(
                self._hass,
                period,
                end_date,
                summary_coordinator,
                summary_entry_id,
                tariff_schedule,
                summary_system,
            )
            return web.json_response(result)

        if not tesla_coordinator:
            # Check if we have ANY power_sync entries - if yes, system might still be loading
            has_entries = bool(self._hass.data.get(DOMAIN, {}))
            if has_entries:
                _LOGGER.debug("Calendar history requested but Tesla coordinator not ready yet (system loading)")
                return web.json_response(
                    {
                        "success": False,
                        "error": "System is still loading, please retry",
                        "reason": "loading"
                    },
                    status=200  # Return 200 with error in body so mobile app handles gracefully
                )
            else:
                _LOGGER.debug("Calendar history requested but Tesla coordinator not available (non-Tesla system)")
                return web.json_response(
                    {
                        "success": False,
                        "error": "Calendar history requires Tesla Powerwall",
                        "reason": "tesla_not_configured"
                    },
                    status=200  # Return 200 with error in body so mobile app handles gracefully
                )

        cache_key = self._calendar_cache_key(tesla_coordinator, period, end_date)
        cached = self._cached_calendar_result(cache_key)
        if cached:
            result, status = cached
            return web.json_response(result, status=status)

        task = self._inflight.get(cache_key)
        if task and task.done():
            try:
                result, status = task.result()
                self._store_calendar_result(cache_key, result, status)
                return web.json_response(result, status=status)
            finally:
                self._inflight.pop(cache_key, None)

        if not task:
            task = self._hass.async_create_task(
                self._build_calendar_history_response(
                    tesla_coordinator=tesla_coordinator,
                    tariff_schedule=tariff_schedule,
                    period=period,
                    end_date=end_date,
                ),
                name=f"powersync_calendar_history_{period}",
            )
            self._inflight[cache_key] = task

        try:
            result, status = await asyncio.wait_for(
                asyncio.shield(task),
                timeout=self._REQUEST_TIMEOUT_SECONDS,
            )
        except (asyncio.TimeoutError, TimeoutError):
            cached = self._cache.get(cache_key)
            if cached:
                _cached_at, result, status = cached
                stale_result = dict(result)
                stale_result["cached"] = True
                stale_result["stale"] = True
                stale_result["refresh_pending"] = True
                _LOGGER.warning(
                    "Calendar history still loading after %.1fs; returning stale cache",
                    self._REQUEST_TIMEOUT_SECONDS,
                )
                return web.json_response(stale_result, status=status)

            _LOGGER.warning(
                "Calendar history still loading after %.1fs; returning loading response",
                self._REQUEST_TIMEOUT_SECONDS,
            )
            return web.json_response(
                {
                    "success": False,
                    "error": "Calendar history is still loading, please retry",
                    "reason": "loading",
                    "refresh_pending": True,
                },
                status=200,
            )

        if task.done():
            self._inflight.pop(cache_key, None)
        self._store_calendar_result(cache_key, result, status)
        return web.json_response(result, status=status)

class HistoryRelinkView(HomeAssistantView):
    """HTTP view to preview/apply Sungrow history relinks."""

    url = "/api/power_sync/history_relink"
    name = "api:power_sync:history_relink"
    requires_auth = True

    def __init__(self, hass: HomeAssistant):
        """Initialize the view."""
        self._hass = hass

    async def get(self, request: web.Request) -> web.Response:
        """Handle history relink preview requests."""
        entry = _resolve_history_relink_entry(
            self._hass,
            request.query.get("entry_id"),
        )
        if entry is None:
            return web.json_response(
                {"success": False, "error": "PowerSync entry not found"},
                status=404,
            )
        if not _is_history_relink_entry(entry):
            return web.json_response(
                {
                    "success": False,
                    "error": "History relink is only available for Sungrow entries",
                },
                status=400,
            )
        return web.json_response(preview_history_relink(self._hass, entry))

    async def post(self, request: web.Request) -> web.Response:
        """Handle history relink apply requests."""
        try:
            data = await request.json()
        except Exception:
            data = {}

        entry = _resolve_history_relink_entry(
            self._hass,
            data.get("entry_id"),
        )
        if entry is None:
            return web.json_response(
                {"success": False, "error": "PowerSync entry not found"},
                status=404,
            )
        if not _is_history_relink_entry(entry):
            return web.json_response(
                {
                    "success": False,
                    "error": "History relink is only available for Sungrow entries",
                },
                status=400,
            )
        if data.get("confirm") is not True:
            return web.json_response(
                {"success": False, "error": "confirm must be true"},
                status=400,
            )

        return web.json_response(apply_history_relink(self._hass, entry))

class PowerwallSettingsView(HomeAssistantView):
    """HTTP view to get Powerwall settings for mobile app Controls."""

    url = "/api/power_sync/powerwall_settings"
    name = "api:power_sync:powerwall_settings"
    requires_auth = True

    def __init__(self, hass: HomeAssistant):
        """Initialize the view."""
        self._hass = hass

    async def get(self, request: web.Request) -> web.Response:
        """Handle GET request for Powerwall settings."""
        _LOGGER.info("⚙️ Powerwall settings HTTP request")

        # Find the power_sync entry and get token/site_id
        entry = None
        for config_entry in self._hass.config_entries.async_entries(DOMAIN):
            entry = config_entry
            break

        if not entry:
            return web.json_response(
                {"success": False, "error": "PowerSync not configured"},
                status=503
            )

        # Check if this is a non-Tesla setup - Powerwall settings may not apply.
        is_sigenergy = bool(entry.data.get(CONF_SIGENERGY_STATION_ID))
        is_sungrow = bool(entry.data.get(CONF_SUNGROW_HOST))
        is_foxess = bool(entry.data.get(CONF_BATTERY_SYSTEM) == BATTERY_SYSTEM_FOXESS or entry.data.get(CONF_FOXESS_HOST) or entry.data.get(CONF_FOXESS_SERIAL_PORT))
        is_neovolt_pw = bool(_get_neovolt_entry_ids(entry.data, self._hass))
        is_alphaess_pw = bool(entry.data.get(CONF_ALPHAESS_MODBUS_HOST))
        if is_alphaess_pw:
            entry_data = self._hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
            curtailment_state = entry_data.get("alphaess_curtailment_state", "normal")
            grid_export_rule = "never" if curtailment_state == "curtailed" else "battery_ok"
            solar_curtailment_enabled = entry.options.get(
                CONF_BATTERY_CURTAILMENT_ENABLED,
                entry.data.get(CONF_BATTERY_CURTAILMENT_ENABLED, False),
            )
            return web.json_response(
                {
                    "success": True,
                    "backup_reserve": 0,
                    "operation_mode": "self_consumption",
                    "grid_export_rule": grid_export_rule,
                    "grid_charging_enabled": True,
                    "solar_curtailment_enabled": solar_curtailment_enabled,
                    "manual_export_override": entry_data.get("manual_export_override", False),
                    "capabilities": {},
                }
            )
        is_goodwe = bool(entry.data.get(CONF_GOODWE_HOST))
        if is_goodwe:
            # Return current export rule derived from curtailment state
            entry_data = self._hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
            curtailment_state = entry_data.get("goodwe_curtailment_state", "normal")
            grid_export_rule = "never" if curtailment_state == "curtailed" else "battery_ok"
            solar_curtailment_enabled = entry.options.get(
                CONF_BATTERY_CURTAILMENT_ENABLED,
                entry.data.get(CONF_BATTERY_CURTAILMENT_ENABLED, False),
            )
            return web.json_response(
                {
                    "success": True,
                    "backup_reserve": 0,
                    "operation_mode": "self_consumption",
                    "grid_export_rule": grid_export_rule,
                    "grid_charging_enabled": True,
                    "solar_curtailment_enabled": solar_curtailment_enabled,
                    "manual_export_override": entry_data.get("manual_export_override", False),
                    "capabilities": {},
                }
            )
        if is_foxess:
            _LOGGER.info("Powerwall settings not available for FoxESS battery systems")
            return web.json_response(
                {
                    "success": False,
                    "error": "Powerwall settings are not available for FoxESS battery systems",
                    "reason": "foxess_not_supported"
                },
                status=200
            )
        if is_neovolt_pw:
            _LOGGER.info("Powerwall settings not available for Neovolt battery systems")
            return web.json_response(
                {
                    "success": False,
                    "error": "Powerwall settings are not available for Neovolt battery systems",
                    "reason": "neovolt_not_supported",
                    "battery_system": BATTERY_SYSTEM_NEOVOLT,
                },
                status=200
            )
        if is_sigenergy:
            _LOGGER.info("Powerwall settings not available for Sigenergy battery systems")
            return web.json_response(
                {
                    "success": False,
                    "error": "Powerwall settings are not available for Sigenergy battery systems",
                    "reason": "sigenergy_not_supported"
                },
                status=200
            )
        if is_sungrow:
            _LOGGER.info("Powerwall settings not available for Sungrow battery systems")
            return web.json_response(
                {
                    "success": False,
                    "error": "Powerwall settings are not available for Sungrow battery systems",
                    "reason": "sungrow_not_supported"
                },
                status=200
            )

        try:
            current_token, provider = get_tesla_api_token(self._hass, entry)
            site_id = entry.data.get(CONF_TESLA_ENERGY_SITE_ID)

            if not site_id or not current_token:
                return web.json_response(
                    {"success": False, "error": "Missing Tesla site ID or token"},
                    status=503
                )

            session = async_get_clientsession(self._hass)
            headers = {
                "Authorization": f"Bearer {current_token}",
                "Content-Type": "application/json",
            }
            api_base = get_tesla_api_base_url(provider, entry.data.get(CONF_FLEET_API_BASE_URL))

            # Fetch site info
            async with session.get(
                f"{api_base}/api/1/energy_sites/{site_id}/site_info",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status != 200:
                    text = await response.text()
                    _LOGGER.error(f"Failed to get site info: {response.status} - {text}")
                    return web.json_response(
                        {"success": False, "error": f"Failed to get site info: {response.status}"},
                        status=500
                    )
                data = await response.json()
                site_info = data.get("response", {})

            # Extract settings from site_info
            backup_reserve = site_info.get("backup_reserve_percent", 20)
            operation_mode = site_info.get("default_real_mode", "autonomous")

            # Get grid settings from components
            components = site_info.get("components", {})
            # Try components first, then site_info
            api_export_rule = components.get("customer_preferred_export_rule") or site_info.get("customer_preferred_export_rule")
            disallow_charge = components.get("disallow_charge_from_grid_with_solar_installed", False)

            # For VPP users, the API doesn't return customer_preferred_export_rule
            # Use non_export_configured to derive the value, or default to battery_ok
            if api_export_rule is None:
                non_export = components.get("non_export_configured", False)
                api_export_rule = "never" if non_export else "battery_ok"

            # Check if solar curtailment is enabled - if so, use server's target rule
            # (more accurate than stale Tesla API values)
            solar_curtailment_enabled = entry.options.get(
                CONF_BATTERY_CURTAILMENT_ENABLED,
                entry.data.get(CONF_BATTERY_CURTAILMENT_ENABLED, False)
            )

            if solar_curtailment_enabled:
                # Use cached rule (what server is targeting) if available
                entry_data = self._hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
                cached_rule = entry_data.get("cached_export_rule")
                if cached_rule:
                    grid_export_rule = cached_rule
                    _LOGGER.debug(f"Using server's target export rule '{cached_rule}' (API reported '{api_export_rule}')")
                else:
                    grid_export_rule = api_export_rule
            else:
                grid_export_rule = api_export_rule

            # Check if manual export override is active
            entry_data = self._hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
            manual_export_override = entry_data.get("manual_export_override", False)

            # Include capability flags and current state for new energy-site
            # controls (storm watch, off-grid EV reserve, VPP programs). These
            # let the mobile app render the right controls for each site.
            tesla_capabilities = entry_data.get("tesla_capabilities", {}) if entry_data else {}
            tesla_coord = entry_data.get("tesla_coordinator") if entry_data else None
            storm_watch_enabled = None
            off_grid_ev_reserve = None
            if tesla_coord is not None:
                storm_watch_enabled = getattr(tesla_coord, "_storm_mode_enabled", None)
                off_grid_ev_reserve = getattr(tesla_coord, "_off_grid_reserve_percent", None)

            result = {
                "success": True,
                "backup_reserve": backup_reserve,
                "operation_mode": operation_mode,
                "grid_export_rule": grid_export_rule,
                "grid_charging_enabled": not disallow_charge,
                "solar_curtailment_enabled": solar_curtailment_enabled,
                "manual_export_override": manual_export_override,
                "capabilities": {
                    "storm_mode": bool(tesla_capabilities.get("storm_mode", False)),
                    "off_grid_vehicle_charging_reserve": bool(
                        tesla_capabilities.get("off_grid_vehicle_charging_reserve", False)
                    ),
                    "vpp_programs": bool(tesla_capabilities.get("vpp_programs", False)),
                },
                "storm_watch_enabled": storm_watch_enabled,
                "off_grid_ev_reserve_percent": off_grid_ev_reserve,
                "site_country": entry_data.get("tesla_site_country") if entry_data else None,
            }

            _LOGGER.info(f"✅ Powerwall settings: reserve={backup_reserve}%, mode={operation_mode}, export={grid_export_rule}, manual_override={manual_export_override}")
            return web.json_response(result)

        except Exception as e:
            _LOGGER.error(f"Error fetching Powerwall settings: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500
            )

class PowerwallTypeView(HomeAssistantView):
    """HTTP view to get Powerwall type (PW2/PW3) for mobile app Settings."""

    url = "/api/power_sync/powerwall_type"
    name = "api:power_sync:powerwall_type"
    requires_auth = True

    def __init__(self, hass: HomeAssistant):
        """Initialize the view."""
        self._hass = hass

    async def get(self, request: web.Request) -> web.Response:
        """Handle GET request for Powerwall type."""
        _LOGGER.info("🔋 Powerwall type HTTP request")

        # Find the power_sync entry and get token/site_id
        entry = None
        for config_entry in self._hass.config_entries.async_entries(DOMAIN):
            entry = config_entry
            break

        if not entry:
            return web.json_response(
                {"success": False, "error": "PowerSync not configured"},
                status=503
            )

        try:
            current_token, provider = get_tesla_api_token(self._hass, entry)
            site_id = entry.data.get(CONF_TESLA_ENERGY_SITE_ID)

            if not site_id or not current_token:
                return web.json_response(
                    {"success": False, "error": "Missing Tesla site ID or token"},
                    status=503
                )

            session = async_get_clientsession(self._hass)
            headers = {
                "Authorization": f"Bearer {current_token}",
                "Content-Type": "application/json",
            }
            api_base = get_tesla_api_base_url(provider, entry.data.get(CONF_FLEET_API_BASE_URL))

            # Fetch site info
            async with session.get(
                f"{api_base}/api/1/energy_sites/{site_id}/site_info",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status != 200:
                    text = await response.text()
                    _LOGGER.error(f"Failed to get site info: {response.status} - {text}")
                    return web.json_response(
                        {"success": False, "error": f"Failed to get site info: {response.status}"},
                        status=500
                    )
                data = await response.json()
                site_info = data.get("response", {})

            # Extract gateway info - gateways array contains part_name
            components = site_info.get("components", {})
            gateways = components.get("gateways", [])
            if not gateways:
                # Try top-level gateways
                gateways = site_info.get("gateways", [])

            powerwall_type = "unknown"
            part_name = None

            if gateways and len(gateways) > 0:
                gateway = gateways[0]  # Primary gateway
                part_name = gateway.get("part_name", "")

                # Detect type from part_name
                if "Powerwall 3" in part_name:
                    powerwall_type = "PW3"
                elif "Powerwall 2" in part_name or "Powerwall+" in part_name:
                    powerwall_type = "PW2"
                elif "Powerwall" in part_name:
                    # Generic Powerwall, try to determine from part_number
                    part_number = gateway.get("part_number", "")
                    if part_number.startswith("170"):  # PW3 part numbers start with 170
                        powerwall_type = "PW3"
                    else:
                        powerwall_type = "PW2"  # Default to PW2 for older units

            _LOGGER.info(f"✅ Powerwall type: {powerwall_type} (part_name: {part_name})")

            return web.json_response({
                "success": True,
                "powerwall_type": powerwall_type,
                "part_name": part_name,
                "gateway_count": len(gateways),
            })

        except Exception as e:
            _LOGGER.error(f"Error fetching Powerwall type: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500
            )

class StormWatchView(HomeAssistantView):
    """GET/POST Tesla Storm Watch enabled state."""

    url = "/api/power_sync/tesla/storm_watch"
    name = "api:power_sync:tesla:storm_watch"
    requires_auth = True

    def __init__(self, hass: HomeAssistant):
        self._hass = hass

    async def get(self, request: web.Request) -> web.Response:
        entry, coord = _get_tesla_coord_for_view(self._hass)
        if coord is None:
            return web.json_response({"success": False, "error": "Tesla not configured"}, status=503)
        if not coord.tesla_capabilities.get("storm_mode", True):
            return web.json_response({
                "success": False,
                "supported": False,
                "error": "Storm Watch is not available for this site",
            }, status=200)
        status = await coord.async_get_storm_watch_status()
        return web.json_response({
            "success": True,
            "supported": True,
            "enabled": coord._storm_mode_enabled,
            "raw": status,
        })

    async def post(self, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"success": False, "error": "Invalid JSON"}, status=400)
        enabled = payload.get("enabled")
        if enabled is None:
            return web.json_response({"success": False, "error": "Missing 'enabled'"}, status=400)

        entry, coord = _get_tesla_coord_for_view(self._hass)
        if coord is None:
            return web.json_response({"success": False, "error": "Tesla not configured"}, status=503)
        if not coord.tesla_capabilities.get("storm_mode", True):
            return web.json_response({
                "success": False, "error": "Storm Watch not supported for this site",
            }, status=400)

        ok = await coord.async_set_storm_watch(bool(enabled))
        return web.json_response({
            "success": ok,
            "enabled": coord._storm_mode_enabled,
        }, status=200 if ok else 500)

class OffGridEvReserveView(HomeAssistantView):
    """GET/POST off-grid vehicle charging reserve percent."""

    url = "/api/power_sync/tesla/off_grid_ev_reserve"
    name = "api:power_sync:tesla:off_grid_ev_reserve"
    requires_auth = True

    def __init__(self, hass: HomeAssistant):
        self._hass = hass

    async def get(self, request: web.Request) -> web.Response:
        entry, coord = _get_tesla_coord_for_view(self._hass)
        if coord is None:
            return web.json_response({"success": False, "error": "Tesla not configured"}, status=503)
        if not coord.tesla_capabilities.get("off_grid_vehicle_charging_reserve", True):
            return web.json_response({
                "success": False,
                "supported": False,
                "error": "Off-grid EV reserve not available for this site",
            }, status=200)
        return web.json_response({
            "success": True,
            "supported": True,
            "percent": coord._off_grid_reserve_percent,
        })

    async def post(self, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"success": False, "error": "Invalid JSON"}, status=400)
        percent = payload.get("percent")
        try:
            percent = int(percent)
        except (ValueError, TypeError):
            return web.json_response({"success": False, "error": "Invalid 'percent'"}, status=400)
        if percent < 0 or percent > 100:
            return web.json_response({"success": False, "error": "percent must be 0-100"}, status=400)

        entry, coord = _get_tesla_coord_for_view(self._hass)
        if coord is None:
            return web.json_response({"success": False, "error": "Tesla not configured"}, status=503)
        if not coord.tesla_capabilities.get("off_grid_vehicle_charging_reserve", True):
            return web.json_response({
                "success": False, "error": "Not supported for this site",
            }, status=400)

        ok = await coord.async_set_off_grid_ev_reserve(percent)
        return web.json_response({
            "success": ok,
            "percent": coord._off_grid_reserve_percent,
        }, status=200 if ok else 500)

class VppProgramsView(HomeAssistantView):
    """GET VPP program list / POST enroll or unenroll."""

    url = "/api/power_sync/tesla/vpp_programs"
    name = "api:power_sync:tesla:vpp_programs"
    requires_auth = True

    def __init__(self, hass: HomeAssistant):
        self._hass = hass

    async def get(self, request: web.Request) -> web.Response:
        entry, coord = _get_tesla_coord_for_view(self._hass)
        if coord is None:
            return web.json_response({"success": False, "error": "Tesla not configured"}, status=503)
        if not coord.tesla_capabilities.get("vpp_programs", True):
            return web.json_response({
                "success": False,
                "supported": False,
                "programs": [],
                "error": "VPP programs not available for this site",
            }, status=200)
        force = request.query.get("refresh", "").lower() in ("1", "true", "yes")
        programs = await coord.async_get_vpp_programs(force_refresh=force)
        return web.json_response({
            "success": True,
            "supported": True,
            "programs": programs,
        })

    async def post(self, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"success": False, "error": "Invalid JSON"}, status=400)
        program_id = payload.get("program_id")
        enrolled = payload.get("enrolled")
        if not program_id or enrolled is None:
            return web.json_response({
                "success": False, "error": "Missing 'program_id' or 'enrolled'",
            }, status=400)

        entry, coord = _get_tesla_coord_for_view(self._hass)
        if coord is None:
            return web.json_response({"success": False, "error": "Tesla not configured"}, status=503)
        if not coord.tesla_capabilities.get("vpp_programs", True):
            return web.json_response({
                "success": False, "error": "VPP programs not supported",
            }, status=400)

        ok = await coord.async_set_vpp_enrollment(str(program_id), bool(enrolled))
        programs = await coord.async_get_vpp_programs(force_refresh=True) if ok else []
        return web.json_response({
            "success": ok,
            "programs": programs,
        }, status=200 if ok else 500)

class BatteryHealthView(HomeAssistantView):
    """HTTP view for battery health data.

    GET: Returns stored battery health from last TEDAPI scan
    POST: Accepts battery health scan data from mobile app
    """

    url = "/api/power_sync/battery_health"
    name = "api:power_sync:battery_health"
    requires_auth = True

    def __init__(self, hass: HomeAssistant):
        """Initialize the view."""
        self._hass = hass

    async def _sync_live_battery_health_to_sensor(
        self,
        entry,
        payload: dict[str, Any],
        *,
        persist: bool = True,
    ) -> None:
        """Mirror live Tesla battery-health payloads into the HA sensor state."""
        if not payload or not payload.get("available"):
            return

        entry_data = self._hass.data[DOMAIN][entry.entry_id]
        current = entry_data.get("battery_health") or {}
        if current and not _battery_health_payload_is_newer(
            payload.get("last_scan"),
            current.get("scanned_at"),
        ):
            return

        health_percent = payload.get("health_percent")
        battery_health_data = {
            "original_capacity_wh": payload.get("original_capacity_wh"),
            "current_capacity_wh": payload.get("current_capacity_wh"),
            "degradation_percent": (
                round(100 - float(health_percent), 1)
                if health_percent is not None
                else None
            ),
            "battery_count": payload.get("battery_count", 1),
            "scanned_at": payload.get("last_scan", dt_util.now().isoformat()),
            "source": payload.get("source", "ha_local_tedapi"),
        }

        if payload.get("individual_batteries"):
            battery_health_data["individual_batteries"] = payload.get("individual_batteries")
        if payload.get("site"):
            battery_health_data["site"] = payload.get("site")
        if payload.get("raw_vitals") is not None:
            battery_health_data["raw_vitals"] = payload.get("raw_vitals")

        entry_data["battery_health"] = battery_health_data

        store = entry_data.get("store")
        if persist and store:
            stored_data = await store.async_load() or {}
            stored_data["battery_health"] = battery_health_data
            await store.async_save(stored_data)

        from homeassistant.helpers.dispatcher import async_dispatcher_send
        async_dispatcher_send(
            self._hass,
            f"{DOMAIN}_battery_health_update_{entry.entry_id}",
            battery_health_data,
        )

    def _get_coordinator_bms(self, entry) -> tuple[str, dict] | None:
        """Extract BMS telemetry from the active coordinator for non-Tesla systems.

        Returns (brand, bms_dict) with normalised snake_case keys, or None.
        """
        entry_data = self._hass.data.get(DOMAIN, {}).get(entry.entry_id, {})

        brand_map = {
            "sungrow_coordinator": "sungrow",
            "sigenergy_coordinator": "sigenergy",
            "goodwe_coordinator": "goodwe",
            "alphaess_coordinator": "alphaess",
            "foxess_coordinator": "foxess",
            "fronius_reserva_coordinator": "fronius_reserva",
            "neovolt_coordinator": "neovolt",
            "solaredge_coordinator": "solaredge",
            "anker_solix_coordinator": "anker_solix",
        }

        for coord_key, brand in brand_map.items():
            coord = entry_data.get(coord_key)
            if not coord or not coord.data:
                continue

            d = coord.data
            bms: dict = {}

            if brand == "sungrow":
                soh_percent = None
                if (v := d.get("battery_soh")) is not None:
                    soh_percent = round(float(v), 1)
                    bms["soh_percent"] = soh_percent
                rated_capacity_kwh = d.get("battery_capacity_kwh")
                if rated_capacity_kwh is None:
                    rated_capacity_kwh = _configured_battery_capacity_kwh(entry)
                if rated_capacity_kwh is not None:
                    rated_capacity_kwh = round(float(rated_capacity_kwh), 2)
                    bms["rated_capacity_kwh"] = rated_capacity_kwh
                    current_capacity_kwh = _current_capacity_from_soh_kwh(
                        rated_capacity_kwh,
                        soh_percent,
                    )
                    if current_capacity_kwh is not None:
                        bms["current_capacity_kwh"] = current_capacity_kwh
                if (v := d.get("battery_temp")) is not None: bms["temperature_c"] = round(float(v), 1)
                if (v := d.get("inverter_temperature")) is not None: bms["inverter_temperature_c"] = round(float(v), 1)
                if (v := d.get("battery_voltage")) is not None: bms["voltage_v"] = round(float(v), 1)
                if (v := d.get("battery_current")) is not None: bms["current_a"] = round(float(v), 1)
                if (v := d.get("battery_level")) is not None: bms["soc_percent"] = round(float(v), 1)
                if (v := d.get("min_soc")) is not None: bms["min_soc_percent"] = round(float(v), 1)
                if (v := d.get("max_soc")) is not None: bms["max_soc_percent"] = round(float(v), 1)
                if (v := d.get("battery_max_charge_power_w")) is not None: bms["max_charge_power_w"] = int(v)
                if (v := d.get("battery_max_discharge_power_w")) is not None: bms["max_discharge_power_w"] = int(v)

            elif brand == "sigenergy":
                if (v := d.get("battery_soh")) is not None: bms["soh_percent"] = round(float(v), 1)
                if (v := d.get("battery_capacity_kwh")) is not None: bms["rated_capacity_kwh"] = round(float(v), 2)
                if (v := d.get("battery_level")) is not None: bms["soc_percent"] = round(float(v), 1)
                if (v := d.get("battery_max_charge_power_w")) is not None: bms["max_charge_power_w"] = int(v)
                if (v := d.get("battery_max_discharge_power_w")) is not None: bms["max_discharge_power_w"] = int(v)

            elif brand == "goodwe":
                if (v := d.get("battery_soh")) is not None: bms["soh_percent"] = round(float(v), 1)
                if (v := d.get("battery_temperature")) is not None: bms["temperature_c"] = round(float(v), 1)
                if (v := d.get("battery_max_charge_power_w")) is not None: bms["max_charge_power_w"] = int(v)
                if (v := d.get("battery_max_discharge_power_w")) is not None: bms["max_discharge_power_w"] = int(v)
                if (v := d.get("model_name")) is not None: bms["model_name"] = str(v)
                if (v := d.get("serial_number")) is not None: bms["serial_number"] = str(v)

            elif brand == "alphaess":
                if (v := d.get("battery_soh")) is not None: bms["soh_percent"] = round(float(v), 1)
                if (v := d.get("battery_capacity_kwh")) is not None: bms["rated_capacity_kwh"] = round(float(v), 2)
                if (v := d.get("battery_level")) is not None: bms["soc_percent"] = round(float(v), 1)
                if (v := d.get("battery_max_charge_power_w")) is not None: bms["max_charge_power_w"] = int(v)
                if (v := d.get("battery_max_discharge_power_w")) is not None: bms["max_discharge_power_w"] = int(v)

            elif brand == "neovolt":
                if (v := d.get("battery_soh")) is not None: bms["soh_percent"] = round(float(v), 1)
                if (v := d.get("battery_capacity_kwh")) is not None: bms["rated_capacity_kwh"] = round(float(v), 2)
                if (v := d.get("battery_level")) is not None: bms["soc_percent"] = round(float(v), 1)
                if (v := d.get("battery_max_charge_power_w")) is not None: bms["max_charge_power_w"] = int(v)
                if (v := d.get("battery_max_discharge_power_w")) is not None: bms["max_discharge_power_w"] = int(v)

            elif brand == "anker_solix":
                if (v := d.get("battery_capacity_kwh")) is not None: bms["rated_capacity_kwh"] = round(float(v), 2)
                if (v := d.get("battery_level")) is not None: bms["soc_percent"] = round(float(v), 1)
                if (v := d.get("battery_max_charge_power_w")) is not None: bms["max_charge_power_w"] = int(v)
                if (v := d.get("battery_max_discharge_power_w")) is not None: bms["max_discharge_power_w"] = int(v)
                if (v := d.get("control_path")) is not None: bms["control_path"] = str(v)

            elif brand == "fronius_reserva":
                if (v := d.get("battery_temperature")) is not None: bms["temperature_c"] = round(float(v), 1)
                if (v := d.get("battery_capacity_kwh")) is not None: bms["rated_capacity_kwh"] = round(float(v), 2)
                if (v := d.get("battery_level")) is not None: bms["soc_percent"] = round(float(v), 1)
                if (v := d.get("min_soc")) is not None: bms["min_soc_percent"] = round(float(v), 1)
                if (v := d.get("battery_max_charge_power_w")) is not None: bms["max_charge_power_w"] = int(v)
                if (v := d.get("battery_max_discharge_power_w")) is not None: bms["max_discharge_power_w"] = int(v)

            elif brand == "foxess":
                if (v := d.get("battery_soh")) is not None: bms["soh_percent"] = round(float(v), 1)
                if (v := d.get("battery_temperature")) is not None: bms["temperature_c"] = round(float(v), 1)
                if (v := d.get("battery_level")) is not None: bms["soc_percent"] = round(float(v), 1)
                if (v := d.get("battery_voltage_v")) is not None: bms["voltage_v"] = round(float(v), 1)
                if (v := d.get("nominal_energy_kwh")) is not None: bms["rated_capacity_kwh"] = round(float(v), 2)
                if (v := d.get("min_soc")) is not None: bms["min_soc_percent"] = round(float(v), 1)
                if (v := d.get("max_charge_current_a")) is not None: bms["max_charge_current_a"] = round(float(v), 1)
                if (v := d.get("max_discharge_current_a")) is not None: bms["max_discharge_current_a"] = round(float(v), 1)
                if (v := d.get("battery_max_charge_power_w")) is not None: bms["max_charge_power_w"] = int(v)
                if (v := d.get("battery_max_discharge_power_w")) is not None: bms["max_discharge_power_w"] = int(v)

            if bms:
                return brand, bms

        return None

    async def _try_fleet_api_bms_fetch(self, entry) -> dict | None:
        """Attempt a signed DeviceControllerQuery via Fleet API and return a response dict.

        Requires RSA key + DIN from Powerwall pairing and a valid Fleet API OAuth token.
        Returns None on any missing prerequisite or failure — callers fall through gracefully.
        """
        import base64 as _b64
        import aiohttp as _aio

        from ..const import (
            CONF_POWERWALL_LOCAL_PRIVATE_KEY,
            CONF_POWERWALL_LOCAL_DIN,
            CONF_POWERWALL_LOCAL_IP,
            CONF_POWERWALL_LOCAL_ENERGY_SITE_ID,
        )
        from ..powerwall_local.views import _get_fleet_api_context
        from ..powerwall_local.fleet_api_bms import (
            build_device_controller_query_envelope,
            build_signed_routable_message,
            parse_device_controller_response,
        )
        from ..powerwall_local.bms_health import (
            assign_pack_roles_from_battery_blocks,
            has_pw3_stack,
            known_expansion_dins_from_gateway_config,
            reconcile_pack_remaining_with_aggregate,
            serial_from_din,
            trim_excess_pw3_follower_placeholders,
        )
        from ..powerwall_local.client import is_loopback_host

        private_key_pem = entry.data.get(CONF_POWERWALL_LOCAL_PRIVATE_KEY)
        din = entry.data.get(CONF_POWERWALL_LOCAL_DIN)
        if not private_key_pem or not din:
            return None

        fleet_token, fleet_base, fleet_site_id = _get_fleet_api_context(self._hass, entry)
        # Don't bail yet — local gateway path can succeed without Fleet API credentials.

        # Run RSA key loading + signing in an executor to avoid blocking the event loop.
        try:
            key_bytes = private_key_pem.encode() if isinstance(private_key_pem, str) else private_key_pem
            envelope = build_device_controller_query_envelope(din)

            def _sign_in_thread() -> bytes:
                return build_signed_routable_message(envelope, din, key_bytes, ttl_seconds=300)

            signed = await self._hass.async_add_executor_job(_sign_in_thread)
        except Exception as err:
            _LOGGER.error("fleet_api_bms: signing failed: %s", err)
            return None

        # Try local TEDAPI gateway first — it has direct CAN bus visibility of all
        # connected Powerwall units, so follower BMS signals are non-None here even
        # though the Fleet API relay drops them. Fall back to Fleet API when the
        # gateway is unreachable (e.g. user is away from home).
        data = None
        source = "ha_local_tedapi"
        local_ip = str(entry.data.get(CONF_POWERWALL_LOCAL_IP) or "").strip()
        if local_ip and not is_loopback_host(local_ip):
            try:
                from ..powerwall_local.transport import get_insecure_ssl_context
                from ..powerwall_local import tedapi_combined_pb2 as _pb2
                ssl_ctx = await get_insecure_ssl_context(self._hass)
                connector = _aio.TCPConnector(ssl=ssl_ctx, limit=2)
                async with _aio.ClientSession(
                    connector=connector,
                    timeout=_aio.ClientTimeout(total=12),
                ) as sess:
                    async with sess.post(
                        f"https://{local_ip}/tedapi/v1r",
                        data=signed,
                        headers={"Content-Type": "application/octet-stream"},
                    ) as resp:
                        if resp.status == 200:
                            raw = await resp.read()
                            resp_msg = _pb2.RoutableMessage()
                            resp_msg.ParseFromString(raw)
                            fault = resp_msg.signed_message_status.message_fault
                            if fault == _pb2.MESSAGEFAULT_ERROR_NONE:
                                local_data = parse_device_controller_response(
                                    resp_msg.protobuf_message_as_bytes
                                )
                                if local_data:
                                    _LOGGER.info("fleet_api_bms: local gateway BMS fetch OK")
                                    data = local_data
                            else:
                                _LOGGER.debug(
                                    "fleet_api_bms: local gateway fault %s",
                                    _pb2.MessageFault_E.Name(fault),
                                )
                        else:
                            body_text = await resp.text()
                            _LOGGER.debug(
                                "fleet_api_bms: local gateway HTTP %d — %s",
                                resp.status, body_text[:200],
                            )
            except Exception as err:
                _LOGGER.debug("fleet_api_bms: local gateway unreachable: %s", err)
        elif local_ip:
            _LOGGER.debug(
                "fleet_api_bms: skipping loopback placeholder gateway host %s",
                local_ip,
            )

        if data is None:
            # Fleet API relay fallback.
            if not fleet_token or not fleet_base or not fleet_site_id:
                _LOGGER.debug("fleet_api_bms: no local gateway and no Fleet API credentials")
                return None
            source = "ha_fleet_api_relay"
            fleet_url = f"{fleet_base}/api/1/energy_sites/{fleet_site_id}/device_command"
            fleet_headers = {
                "Authorization": f"Bearer {fleet_token}",
                "Content-Type": "application/json",
            }
            fleet_payload = {
                "data": {
                    "target_id": din,
                    "routable_message": _b64.b64encode(signed).decode(),
                    "command_timeout_s": 10,
                    "identifier_type": 1,
                }
            }
            try:
                async with _aio.ClientSession() as sess:
                    async with sess.post(
                        fleet_url, json=fleet_payload, headers=fleet_headers,
                        timeout=_aio.ClientTimeout(total=35),
                    ) as resp:
                        if resp.status != 200:
                            body_text = await resp.text()
                            _LOGGER.warning(
                                "fleet_api_bms: HTTP %d — %s", resp.status, body_text[:400]
                            )
                            return None
                        body = await resp.json()
            except Exception as err:
                _LOGGER.error("fleet_api_bms: request error: %s", err)
                return None

            envelope_b64 = (body.get("response") or {}).get("message_envelope_as_bytes")
            if not envelope_b64:
                _LOGGER.warning(
                    "fleet_api_bms: no message_envelope_as_bytes in response: %s",
                    str(body)[:400],
                )
                return None
            try:
                data = parse_device_controller_response(_b64.b64decode(envelope_b64))
            except Exception as err:
                _LOGGER.warning("fleet_api_bms: decode error: %s", err)
                return None
            if data is None:
                _LOGGER.warning("fleet_api_bms: failed to extract text from response envelope")
                return None

        gateway_config = None
        try:
            runtime = self._hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
            local_runtime = runtime.get("powerwall_local") or {}
            local_coordinator = local_runtime.get("coordinator")
            local_snapshot = getattr(local_coordinator, "data", None)
            raw_snapshot = getattr(local_snapshot, "raw", None)
            if isinstance(raw_snapshot, dict) and isinstance(raw_snapshot.get("config"), dict):
                gateway_config = raw_snapshot["config"]
            else:
                local_client = local_runtime.get("client") or getattr(local_coordinator, "client", None)
                local_transport = getattr(local_client, "_transport", None)
                if (
                    local_transport is not None
                    and getattr(local_client, "local_access_enabled", True) is not False
                    and not is_loopback_host(getattr(local_client, "host", None))
                ):
                    gateway_config = await local_transport.read_config(din)
        except Exception as err:
            _LOGGER.debug("fleet_api_bms: config.json read for expansion mapping failed: %s", err)

        # Primary source: control.systemStatus aggregate (matches cloud worker).
        # This is authoritative for the whole site — never override with partial per-pack sums.
        status = ((data.get("control") or {}).get("systemStatus") or {})
        current_wh = status.get("nominalFullPackEnergyWh") or 0
        rem_wh = status.get("nominalEnergyRemainingWh") or 0

        # batteryBlocks: one entry per Powerwall inverter unit (not per battery module).
        # PW3 with 2 expansion packs = 1 batteryBlock entry, 3 PW3BMS entries in msa.
        battery_blocks = (data.get("control") or {}).get("batteryBlocks") or []
        bb_count = len(battery_blocks) if battery_blocks else 1

        # Per-pack BMS breakdown from components.msa.
        # Only PW3BMS components carry BMS_nominalFullPackEnergy; PVS/PVAC/TESYNC etc. have 0.
        all_comps = (data.get("components") or {}).get("msa") or []
        is_pw3_stack = has_pw3_stack(battery_blocks, all_comps, din)
        _LOGGER.debug(
            "fleet_api_bms: batteryBlocks=%d, msa_total=%d entries: %s",
            bb_count, len(all_comps),
            [(c.get("partNumber"), c.get("serialNumber"),
              {s["name"]: s.get("value") for s in (c.get("signals") or [])
               if s["name"] in ("BMS_nominalFullPackEnergy", "BMS_nominalEnergyRemaining")})
             for c in all_comps],
        )
        individual = []
        bms_module_count = 0
        for pack in all_comps:
            sigs = {s["name"]: s.get("value") for s in (pack.get("signals") or [])}
            if "BMS_nominalFullPackEnergy" not in sigs:
                continue  # skip non-BMS components (PVS, PVAC, etc.) — they have no BMS signal key
            bms_module_count += 1
            # BMS_nominalFullPackEnergy is in kWh; convert to Wh for the app.
            pack_full_wh = (sigs.get("BMS_nominalFullPackEnergy") or 0) * 1000
            pack_rem_wh = (sigs.get("BMS_nominalEnergyRemaining") or 0) * 1000
            # Follower PW3 base modules report the BMS signal key but with None values.
            is_follower = (
                is_pw3_stack
                and pack_full_wh == 0
                and sigs.get("BMS_nominalFullPackEnergy") is None
            )
            individual.append({
                "nominalFullPackEnergyWh": pack_full_wh,
                "nominalEnergyRemainingWh": pack_rem_wh,
                "bmsSerialNumber": pack.get("serialNumber") or None,
                "serialNumber": pack.get("serialNumber") or None,
                "role": "unknown",
                "isExpansion": bool(is_pw3_stack and individual and not is_follower),
                "isFollower": is_follower,
            })

        if is_pw3_stack:
            dropped_followers = trim_excess_pw3_follower_placeholders(
                individual,
                battery_blocks,
                din,
            )
            if dropped_followers:
                bms_module_count = max(0, bms_module_count - dropped_followers)
                _LOGGER.warning(
                    "fleet_api_bms: dropping %d excess PW3 follower placeholder(s); "
                    "batteryBlocks report %d physical follower unit(s)",
                    dropped_followers,
                    max(0, len(battery_blocks) - 1),
                )

        # Follower units report None BMS signals — their contribution is inferable from the
        # aggregate systemStatus total minus the sum of leader packs. The serial number comes
        # from batteryBlocks[].din (format: "partNum--serial"). For multiple followers the
        # remaining energy is split evenly among them.
        follower_indices = [
            i for i, p in enumerate(individual)
            if p.get("isFollower") and p.get("nominalFullPackEnergyWh") == 0
        ]
        if is_pw3_stack and follower_indices and current_wh:
            leader_full_wh = sum(
                p["nominalFullPackEnergyWh"] for p in individual if not p.get("isFollower")
            )
            leader_rem_wh = sum(
                p["nominalEnergyRemainingWh"] for p in individual if not p.get("isFollower")
            )
            n_followers = len(follower_indices)
            inferred_full_wh = max(0.0, current_wh - leader_full_wh) / n_followers
            inferred_rem_wh = max(0.0, rem_wh - leader_rem_wh) / n_followers
            follower_dins = [
                block.get("din") for block in battery_blocks
                if block.get("din") and block.get("din") != din
            ]
            for seq, idx in enumerate(follower_indices):
                f_din = follower_dins[seq] if seq < len(follower_dins) else None
                serial = serial_from_din(f_din)
                individual[idx] = {
                    "nominalFullPackEnergyWh": inferred_full_wh,
                    "nominalEnergyRemainingWh": inferred_rem_wh,
                    "bmsSerialNumber": None,
                    "physicalDin": f_din,
                    "serialNumber": serial,
                    "role": "follower",
                    "isExpansion": False,
                    "isFollower": True,
                }
            _LOGGER.debug(
                "fleet_api_bms: inferred follower data: %d unit(s), %.0f Wh each",
                n_followers, inferred_full_wh,
            )

        # Role labelling: PW3 has leader/follower base units plus expansion
        # BMS modules; PW2 has separate Powerwalls and should stay as plain
        # powerwall packs.
        assign_pack_roles_from_battery_blocks(
            individual,
            battery_blocks,
            din,
            all_comps,
            known_expansion_dins_from_gateway_config(gateway_config),
        )

        # Ghost expansion pack filter. Phantom packs (registered slots not physically
        # installed) report:
        #   1. plausible full-capacity values (~13.5–14.5 kWh)
        #   2. NO serialNumber from MSA components
        #   3. near-zero remaining energy regardless of system SOC
        #
        # Real BMS modules satisfy AT LEAST ONE of: have a serialNumber, or report
        # remaining energy that tracks system SOC. The previous filter keyed off
        # (!serialNumber) alone, which mis-dropped real expansion packs whose firmware
        # doesn't populate serialNumber in the MSA components surface (observed on
        # PW3 + 1 expansion sites — current_wh reported only the leader's energy, so
        # the cross-validation kept-sum check passed despite the real expansion being
        # kept). Require BOTH the missing-serial signal AND a near-zero remaining-
        # energy signature before flagging as ghost; cross-validate as before.
        if individual and current_wh > 0:
            GHOST_REMAINING_ABS_WH = 500
            GHOST_REMAINING_FRACTION = 0.05
            ghost_candidates = [
                p for p in individual
                if p.get("isExpansion")
                and not p.get("serialNumber")
                and (
                    p["nominalEnergyRemainingWh"] < GHOST_REMAINING_ABS_WH
                    or (
                        p["nominalFullPackEnergyWh"] > 0
                        and p["nominalEnergyRemainingWh"] / p["nominalFullPackEnergyWh"]
                        < GHOST_REMAINING_FRACTION
                    )
                )
            ]
            if ghost_candidates:
                kept = [p for p in individual if p not in ghost_candidates]
                kept_full_wh = sum(p["nominalFullPackEnergyWh"] for p in kept)
                if kept_full_wh > 0 and abs(current_wh - kept_full_wh) / current_wh < 0.10:
                    ghost_count = len(ghost_candidates)
                    _LOGGER.info(
                        "fleet_api_bms: dropping %d placeholder expansion pack(s) (no serial + near-empty) — "
                        "system %.0f Wh matches real-pack sum %.0f Wh (ratio %.3f); "
                        "expansion slots registered but not physically installed",
                        ghost_count, current_wh, kept_full_wh, kept_full_wh / current_wh,
                    )
                    individual = kept
                    bms_module_count -= ghost_count

        individual = reconcile_pack_remaining_with_aggregate(
            individual,
            rem_wh,
            current_wh,
            logger=_LOGGER,
        )

        # Module count: BMS signal presence is the most accurate count (includes follower packs
        # that report the signal key but have None values). batteryBlocks counts inverter units
        # (one per PW3 stack), not individual battery modules — use it only as a floor.
        batt_count = max(bms_module_count, bb_count) if bms_module_count else bb_count

        # Cross-validate against Tesla's own site_info battery_count. PW3 units each expose
        # two BMS sub-modules in the msa components list, so bms_module_count can be 2× the
        # actual physical Powerwall count. site_info.battery_count is Tesla's authoritative
        # count of physical units — prefer it when the BMS count exceeds it.
        try:
            _coord = (self._hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
                      .get("tesla_coordinator"))
            _site_batt_count = (
                (_coord._site_info_cache or {}).get("battery_count")
                if _coord and hasattr(_coord, "_site_info_cache")
                else None
            )
            if _site_batt_count and batt_count > _site_batt_count:
                _LOGGER.info(
                    "fleet_api_bms: BMS module count (%d) exceeds site battery_count (%d) — "
                    "using site battery_count for rated capacity (PW3 has 2 BMS sub-modules per unit)",
                    batt_count, _site_batt_count,
                )
                batt_count = _site_batt_count
        except Exception:
            pass

        # Rated capacity: 13.5 kWh per physical Powerwall unit.
        original_wh = 13500 * batt_count
        health_percent = round((current_wh / original_wh) * 100, 1) if original_wh > 0 else 0

        bms: dict = {}
        if health_percent: bms["soh_percent"] = health_percent
        if original_wh: bms["rated_capacity_kwh"] = round(original_wh / 1000, 2)
        if current_wh: bms["current_capacity_kwh"] = round(current_wh / 1000, 2)

        response: dict = {
            "success": True,
            "available": True,
            "brand": "tesla",
            "source": source,
            "health_percent": health_percent,
            "original_capacity_wh": original_wh,
            "current_capacity_wh": current_wh,
            "original_capacity_kwh": round(original_wh / 1000, 2),
            "current_capacity_kwh": round(current_wh / 1000, 2),
            "battery_count": batt_count,
            "last_scan": dt_util.now().isoformat(),
            "site": {
                "gateway_din": din,
                "energy_site_id": fleet_site_id or entry.data.get(CONF_POWERWALL_LOCAL_ENERGY_SITE_ID),
            },
        }
        if bms:
            response["bms"] = bms
        if individual:
            response["individual_batteries"] = individual
        if rem_wh:
            response["nominal_energy_remaining_wh"] = rem_wh

        _LOGGER.info(
            "fleet_api_bms: Tesla health %.1f%% (%d Wh / %d Wh), %d module(s), %d batteryBlock(s), %d msa_bms [%s]",
            health_percent, current_wh, original_wh, batt_count, bb_count, len(individual), source,
        )
        return response

    async def _try_fleet_api_solar_strings_fetch(self, entry) -> dict | None:
        """Fetch Powerwall DC string diagnostics via signed TEDAPI queries."""
        import base64 as _b64
        import aiohttp as _aio

        from ..const import (
            CONF_POWERWALL_LOCAL_PRIVATE_KEY,
            CONF_POWERWALL_LOCAL_DIN,
            CONF_POWERWALL_LOCAL_IP,
        )
        from ..powerwall_local.views import _get_fleet_api_context
        from ..powerwall_local.fleet_api_bms import (
            build_device_controller_query_envelope,
            build_pw3_components_query_envelope,
            build_signed_routable_message,
            normalize_legacy_pvac_strings,
            normalize_pw3_components_strings,
            parse_device_controller_response,
        )

        private_key_pem = entry.data.get(CONF_POWERWALL_LOCAL_PRIVATE_KEY)
        din = entry.data.get(CONF_POWERWALL_LOCAL_DIN)
        if not private_key_pem or not din:
            return None

        fleet_token, fleet_base, fleet_site_id = _get_fleet_api_context(self._hass, entry)
        key_bytes = private_key_pem.encode() if isinstance(private_key_pem, str) else private_key_pem

        async def _read_query(envelope_builder, log_label: str) -> tuple[dict | None, str | None]:
            try:
                envelope = envelope_builder(din)

                def _sign_in_thread() -> bytes:
                    return build_signed_routable_message(envelope, din, key_bytes, ttl_seconds=300)

                signed = await self._hass.async_add_executor_job(_sign_in_thread)
            except Exception as err:
                _LOGGER.debug("fleet_api_solar_strings: %s signing failed: %s", log_label, err)
                return None, None

            local_ip = entry.data.get(CONF_POWERWALL_LOCAL_IP)
            if local_ip:
                try:
                    from ..powerwall_local.transport import get_insecure_ssl_context
                    from ..powerwall_local import tedapi_combined_pb2 as _pb2
                    ssl_ctx = await get_insecure_ssl_context(self._hass)
                    connector = _aio.TCPConnector(ssl=ssl_ctx, limit=2)
                    async with _aio.ClientSession(
                        connector=connector,
                        timeout=_aio.ClientTimeout(total=12),
                    ) as sess:
                        async with sess.post(
                            f"https://{local_ip}/tedapi/v1r",
                            data=signed,
                            headers={"Content-Type": "application/octet-stream"},
                        ) as resp:
                            if resp.status == 200:
                                raw = await resp.read()
                                resp_msg = _pb2.RoutableMessage()
                                resp_msg.ParseFromString(raw)
                                fault = resp_msg.signed_message_status.message_fault
                                if fault == _pb2.MESSAGEFAULT_ERROR_NONE:
                                    data = parse_device_controller_response(
                                        resp_msg.protobuf_message_as_bytes
                                    )
                                    if data:
                                        return data, "ha_local_tedapi"
                                else:
                                    _LOGGER.debug(
                                        "fleet_api_solar_strings: local %s fault %s",
                                        log_label,
                                        _pb2.MessageFault_E.Name(fault),
                                    )
                            else:
                                body_text = await resp.text()
                                _LOGGER.debug(
                                    "fleet_api_solar_strings: local %s HTTP %d - %s",
                                    log_label, resp.status, body_text[:200],
                                )
                except Exception as err:
                    _LOGGER.debug("fleet_api_solar_strings: local %s unreachable: %s", log_label, err)

            if not fleet_token or not fleet_base or not fleet_site_id:
                return None, None

            fleet_url = f"{fleet_base}/api/1/energy_sites/{fleet_site_id}/device_command"
            fleet_headers = {
                "Authorization": f"Bearer {fleet_token}",
                "Content-Type": "application/json",
            }
            fleet_payload = {
                "data": {
                    "target_id": din,
                    "routable_message": _b64.b64encode(signed).decode(),
                    "command_timeout_s": 10,
                    "identifier_type": 1,
                }
            }
            try:
                async with _aio.ClientSession() as sess:
                    async with sess.post(
                        fleet_url,
                        json=fleet_payload,
                        headers=fleet_headers,
                        timeout=_aio.ClientTimeout(total=35),
                    ) as resp:
                        if resp.status != 200:
                            body_text = await resp.text()
                            _LOGGER.debug(
                                "fleet_api_solar_strings: %s HTTP %d - %s",
                                log_label, resp.status, body_text[:400],
                            )
                            return None, None
                        body = await resp.json()
            except Exception as err:
                _LOGGER.debug("fleet_api_solar_strings: %s request error: %s", log_label, err)
                return None, None

            envelope_b64 = (body.get("response") or {}).get("message_envelope_as_bytes")
            if not envelope_b64:
                return None, None
            try:
                data = parse_device_controller_response(_b64.b64decode(envelope_b64))
            except Exception as err:
                _LOGGER.debug("fleet_api_solar_strings: %s decode error: %s", log_label, err)
                return None, None
            return data, "ha_fleet_api_relay" if data else None

        data, transport_source = await _read_query(
            build_pw3_components_query_envelope,
            "pw3_components",
        )
        saw_response = data is not None
        last_transport_source = transport_source if data is not None else None
        diagnostics = normalize_pw3_components_strings(data) if data else None

        if diagnostics is None:
            data, transport_source = await _read_query(
                build_device_controller_query_envelope,
                "legacy_pvac",
            )
            saw_response = saw_response or data is not None
            if data is not None:
                last_transport_source = transport_source
            diagnostics = normalize_legacy_pvac_strings(data) if data else None

        if diagnostics is None:
            if saw_response:
                return {
                    "success": True,
                    "available": False,
                    "brand": "tesla",
                    "source": None,
                    "transport_source": last_transport_source,
                    "strings": [],
                    "groups": [],
                    "last_scan": dt_util.now().isoformat(),
                    "site": {
                        "gateway_din": din,
                        "energy_site_id": fleet_site_id,
                    },
                }
            return None

        response = {
            "success": True,
            "available": True,
            "brand": "tesla",
            "transport_source": transport_source,
            "last_scan": dt_util.now().isoformat(),
            "site": {
                "gateway_din": din,
                "energy_site_id": fleet_site_id,
            },
            **diagnostics,
        }
        _LOGGER.debug(
            "fleet_api_solar_strings: fetched %d string(s), %d group(s) via %s/%s",
            len(response.get("strings") or []),
            len(response.get("groups") or []),
            response.get("source"),
            transport_source,
        )
        return response

    async def get(self, request: web.Request) -> web.Response:
        """Handle GET request - return stored battery health data."""
        _LOGGER.info("🔋 Battery health HTTP request")

        # Find the power_sync entry
        entry = None
        for config_entry in self._hass.config_entries.async_entries(DOMAIN):
            entry = config_entry
            break

        if not entry:
            return web.json_response(
                {"success": False, "error": "PowerSync not configured"},
                status=503
            )

        try:
            import time as _t

            domain_data = self._hass.data.get(DOMAIN, {})
            entry_data = domain_data.get(entry.entry_id, {})
            battery_system = entry.data.get(CONF_BATTERY_SYSTEM, "tesla")

            # Tesla: always go via Fleet API (with 1-hour cache). Never use stale
            # WiFi-scan data from storage — the Fleet API path supersedes it.
            if battery_system == "tesla":
                refresh = request.query.get("refresh") == "1"
                cache = entry_data.get("battery_health_cloud")
                now = _t.monotonic()
                if not refresh and cache and cache.get("expires_at", 0) > now:
                    await self._sync_live_battery_health_to_sensor(entry, cache["value"])
                    return web.json_response(cache["value"])
                fleet_result = await self._try_fleet_api_bms_fetch(entry)
                if fleet_result:
                    entry_data["battery_health_cloud"] = {
                        "value": fleet_result,
                        "expires_at": now + 3600,
                    }
                    await self._sync_live_battery_health_to_sensor(entry, fleet_result)
                    return web.json_response(fleet_result)
                return web.json_response({
                    "success": True,
                    "available": False,
                    "brand": "tesla",
                    "message": (
                        "Powerwall not paired. Complete pairing in Local Control "
                        "to enable Battery Health."
                    ),
                })

            # Non-Tesla: live BMS telemetry from the modbus coordinator
            bms_result = self._get_coordinator_bms(entry)
            if bms_result:
                brand, bms = bms_result
                return web.json_response({
                    "success": True,
                    "available": True,
                    "brand": brand,
                    "source": "inverter_modbus",
                    "bms": bms,
                })

            return web.json_response({
                "success": True,
                "available": False,
                "message": "No battery health data available.",
            })

        except Exception as e:
            _LOGGER.error(f"Error fetching battery health: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500
            )

    async def post(self, request: web.Request) -> web.Response:
        """Handle POST request - receive battery health scan data from mobile app."""
        _LOGGER.info("🔋 Battery health scan data received via HTTP")

        # Find the power_sync entry
        entry = None
        for config_entry in self._hass.config_entries.async_entries(DOMAIN):
            entry = config_entry
            break

        if not entry:
            return web.json_response(
                {"success": False, "error": "PowerSync not configured"},
                status=503
            )

        try:
            data = await request.json()

            original_capacity_wh = data.get("original_capacity_wh")
            current_capacity_wh = data.get("current_capacity_wh")
            degradation_percent = data.get("degradation_percent")
            battery_count = data.get("battery_count", 1)
            scanned_at = data.get("scanned_at", dt_util.now().isoformat())
            individual_batteries = data.get("individual_batteries")
            # Extended fields (cloud RSA path provides richer metadata)
            source = data.get("source") or "mobile_app"
            gateway_din = data.get("gateway_din")
            energy_site_id = data.get("energy_site_id")
            site_name = data.get("site_name")
            raw_vitals = data.get("raw_vitals")

            # Validate required fields
            if original_capacity_wh is None or current_capacity_wh is None or degradation_percent is None:
                return web.json_response(
                    {"success": False, "error": "Missing required fields: original_capacity_wh, current_capacity_wh, degradation_percent"},
                    status=400
                )

            health_percent = round((current_capacity_wh / original_capacity_wh) * 100, 1) if original_capacity_wh > 0 else 0

            _LOGGER.info(
                f"🔋 Battery health received ({source}): {health_percent}% health ({current_capacity_wh}Wh / {original_capacity_wh}Wh, {battery_count} units)"
            )

            # Build battery health data
            battery_health_data = {
                "original_capacity_wh": original_capacity_wh,
                "current_capacity_wh": current_capacity_wh,
                "degradation_percent": degradation_percent,
                "battery_count": battery_count,
                "scanned_at": scanned_at,
                "source": source,
            }

            if individual_batteries:
                battery_health_data["individual_batteries"] = individual_batteries
                _LOGGER.info(f"  Individual batteries: {len(individual_batteries)} units")

            site_info: dict = {}
            if gateway_din: site_info["gateway_din"] = gateway_din
            if energy_site_id is not None: site_info["energy_site_id"] = energy_site_id
            if site_name: site_info["site_name"] = site_name
            if site_info:
                battery_health_data["site"] = site_info
                _LOGGER.info(f"  Site: {site_info}")

            if raw_vitals is not None:
                battery_health_data["raw_vitals"] = raw_vitals

            # Store in hass.data
            self._hass.data[DOMAIN][entry.entry_id]["battery_health"] = battery_health_data

            # Persist to storage
            store = self._hass.data[DOMAIN][entry.entry_id].get("store")
            if store:
                stored_data = await store.async_load() or {}
                stored_data["battery_health"] = battery_health_data
                await store.async_save(stored_data)
                _LOGGER.debug("Battery health persisted to storage")

            # Notify sensor via dispatcher
            from homeassistant.helpers.dispatcher import async_dispatcher_send
            async_dispatcher_send(
                self._hass,
                f"{DOMAIN}_battery_health_update_{entry.entry_id}",
                battery_health_data,
            )

            return web.json_response({
                "success": True,
                "message": f"Battery health synced: {health_percent}% health",
                "data": battery_health_data,
            })

        except Exception as e:
            _LOGGER.error(f"Error processing battery health scan: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500
            )

class InverterStatusView(HomeAssistantView):
    """HTTP view to get AC-coupled inverter status for mobile app."""

    url = "/api/power_sync/inverter_status"
    name = "api:power_sync:inverter_status"
    requires_auth = True

    def __init__(self, hass: HomeAssistant):
        """Initialize the view."""
        self._hass = hass

    async def get(self, request: web.Request) -> web.Response:
        """Handle GET request for inverter status."""
        _LOGGER.info("☀️ Inverter status HTTP request")

        # Find the power_sync entry
        entry = None
        for config_entry in self._hass.config_entries.async_entries(DOMAIN):
            entry = config_entry
            break

        if not entry:
            return web.json_response(
                {"success": False, "error": "PowerSync not configured"},
                status=503
            )

        # Check if inverter curtailment is enabled
        inverter_enabled = entry.options.get(
            CONF_AC_INVERTER_CURTAILMENT_ENABLED,
            entry.data.get(CONF_AC_INVERTER_CURTAILMENT_ENABLED, False)
        )

        if not inverter_enabled:
            return web.json_response({
                "success": True,
                "enabled": False,
                "message": "Inverter curtailment not enabled"
            })

        # Get inverter configuration
        inverter_brand = entry.options.get(
            CONF_INVERTER_BRAND,
            entry.data.get(CONF_INVERTER_BRAND, "sungrow")
        )
        inverter_host = entry.options.get(
            CONF_INVERTER_HOST,
            entry.data.get(CONF_INVERTER_HOST, "")
        )
        inverter_port = entry.options.get(
            CONF_INVERTER_PORT,
            entry.data.get(CONF_INVERTER_PORT, DEFAULT_INVERTER_PORT)
        )
        inverter_slave_id = entry.options.get(
            CONF_INVERTER_SLAVE_ID,
            entry.data.get(CONF_INVERTER_SLAVE_ID, DEFAULT_INVERTER_SLAVE_ID)
        )
        inverter_model = entry.options.get(
            CONF_INVERTER_MODEL,
            entry.data.get(CONF_INVERTER_MODEL)
        )
        inverter_token = entry.options.get(
            CONF_INVERTER_TOKEN,
            entry.data.get(CONF_INVERTER_TOKEN)
        )
        fronius_load_following = entry.options.get(
            CONF_FRONIUS_LOAD_FOLLOWING,
            entry.data.get(CONF_FRONIUS_LOAD_FOLLOWING, False)
        )
        inverter_rated_power_w = entry.options.get(
            CONF_INVERTER_RATED_POWER_W,
            entry.data.get(CONF_INVERTER_RATED_POWER_W),
        )
        # Enphase Enlighten credentials for automatic JWT token refresh
        enphase_username = entry.options.get(
            CONF_ENPHASE_USERNAME,
            entry.data.get(CONF_ENPHASE_USERNAME)
        )
        enphase_password = entry.options.get(
            CONF_ENPHASE_PASSWORD,
            entry.data.get(CONF_ENPHASE_PASSWORD)
        )
        enphase_serial = entry.options.get(
            CONF_ENPHASE_SERIAL,
            entry.data.get(CONF_ENPHASE_SERIAL)
        )
        enphase_normal_profile = entry.options.get(
            CONF_ENPHASE_NORMAL_PROFILE,
            entry.data.get(CONF_ENPHASE_NORMAL_PROFILE)
        )
        enphase_zero_export_profile = entry.options.get(
            CONF_ENPHASE_ZERO_EXPORT_PROFILE,
            entry.data.get(CONF_ENPHASE_ZERO_EXPORT_PROFILE)
        )
        enphase_is_installer = entry.options.get(
            CONF_ENPHASE_IS_INSTALLER,
            entry.data.get(CONF_ENPHASE_IS_INSTALLER, False)
        )

        if not inverter_host:
            return web.json_response({
                "success": True,
                "enabled": True,
                "error": "Inverter not configured (no host)"
            })

        controller = None
        try:
            controller = get_inverter_controller(
                brand=inverter_brand,
                host=inverter_host,
                port=inverter_port,
                slave_id=inverter_slave_id,
                model=inverter_model,
                token=inverter_token,
                load_following=fronius_load_following,
                enphase_username=enphase_username,
                enphase_password=enphase_password,
                enphase_serial=enphase_serial,
                enphase_normal_profile=enphase_normal_profile,
                enphase_zero_export_profile=enphase_zero_export_profile,
                enphase_is_installer=enphase_is_installer,
                max_export_limit_kw=entry.data.get(CONF_SIGENERGY_EXPORT_LIMIT_KW),
                rated_power_w=inverter_rated_power_w,
                hass=self._hass,
            )

            if not controller:
                return web.json_response({
                    "success": False,
                    "enabled": True,
                    "error": f"Unsupported inverter brand: {inverter_brand}"
                })

            # Get status from controller
            state = await controller.get_status()

            # Convert state to dict
            state_dict = state.to_dict()

            # Use tracked inverter_last_state as source of truth for is_curtailed
            # This fixes Fronius simple mode where power_limit_enabled is False
            # but the inverter is actually curtailed using soft export limit
            entry_data = self._hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
            inverter_last_state = entry_data.get("inverter_last_state")
            if inverter_last_state == "curtailed":
                state_dict["is_curtailed"] = True
                if state_dict.get("status") == "online":
                    state_dict["status"] = "curtailed"
            elif inverter_last_state in ("normal", "running"):
                state_dict["is_curtailed"] = False

            # Check if it's nighttime for sleep detection
            is_night = False
            try:
                sun_state = self._hass.states.get("sun.sun")
                if sun_state:
                    is_night = sun_state.state == "below_horizon"
                else:
                    # Fallback to hour-based check (6pm-6am) — use HA tz, not container UTC
                    local_hour = dt_util.now().hour
                    is_night = local_hour >= 18 or local_hour < 6
            except Exception:
                pass

            # Apply sleep detection at night if:
            # - Status is offline/error, OR
            # - Power output is very low (< 100W, e.g. Sungrow PID recovery mode)
            if is_night:
                power_output = state_dict.get('power_output_w', 0) or 0
                status = state_dict.get('status')
                if status in ('offline', 'error') or power_output < 100:
                    state_dict['status'] = 'sleep'
                    state_dict['error_message'] = 'Inverter in sleep mode (night)'

            result = {
                "success": True,
                "enabled": True,
                "brand": inverter_brand,
                "model": inverter_model,
                "host": inverter_host,
                **state_dict
            }

            _LOGGER.info(f"✅ Inverter status: {state_dict.get('status')}, curtailed: {state_dict.get('is_curtailed')}")
            return web.json_response(result)

        except Exception as e:
            _LOGGER.error(f"Error getting inverter status: {e}", exc_info=True)
            # Determine if it's likely nighttime (inverter sleep) vs actual offline
            # Use sun.sun entity if available for accurate sunrise/sunset
            is_night = False
            try:
                sun_state = self._hass.states.get("sun.sun")
                if sun_state:
                    is_night = sun_state.state == "below_horizon"
                else:
                    # Fallback to hour-based check (6pm-6am) — use HA tz, not container UTC
                    local_hour = dt_util.now().hour
                    is_night = local_hour >= 18 or local_hour < 6
            except Exception:
                pass

            status = "sleep" if is_night else "offline"
            description = "Inverter in sleep mode (night)" if is_night else "Cannot reach inverter"

            return web.json_response({
                "success": True,
                "enabled": True,
                "status": status,
                "is_curtailed": False,
                "power_output_w": None,
                "power_limit_percent": None,
                "brand": inverter_brand,
                "model": inverter_model,
                "host": inverter_host,
                "error_message": description
            })
        finally:
            if controller:
                try:
                    await controller.disconnect()
                except Exception as err:
                    _LOGGER.debug("Error disconnecting inverter status controller: %s", err)

