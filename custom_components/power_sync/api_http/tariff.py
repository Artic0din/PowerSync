"""HTTP views for PowerSync."""
from __future__ import annotations

import logging
from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util
from ..const import (
    DOMAIN,
    CONF_AMBER_FORECAST_TYPE,
    CONF_AUTO_SYNC_ENABLED,
    CONF_MONITORING_MODE,
    CONF_DEMAND_CHARGE_ENABLED,
    CONF_DEMAND_CHARGE_RATE,
    CONF_DEMAND_CHARGE_START_TIME,
    CONF_DEMAND_CHARGE_END_TIME,
    CONF_DEMAND_CHARGE_DAYS,
    CONF_DEMAND_CHARGE_BILLING_DAY,
    CONF_BATTERY_CURTAILMENT_ENABLED,
    SERVICE_RESTORE_NORMAL,
    CONF_AEMO_SPIKE_ENABLED,
    CONF_AEMO_REGION,
    CONF_AEMO_SPIKE_THRESHOLD,
    CONF_GLOBIRD_PLAN,
    CONF_GLOBIRD_ZEROHERO_START,
    CONF_GLOBIRD_ZEROHERO_END,
    CONF_GLOBIRD_ZEROHERO_EXPORT_CAP_KWH,
    CONF_GLOBIRD_ZEROHERO_SUPER_EXPORT_RATE,
    CONF_GLOBIRD_ZEROHERO_CREDIT_AMOUNT,
    CONF_GLOBIRD_ZEROHERO_IMPORT_LIMIT_KW,
    CONF_GLOBIRD_ZEROCHARGE_START,
    CONF_GLOBIRD_ZEROCHARGE_END,
    CONF_GLOBIRD_ZEROCHARGE_IMPORT_CAP_KWH,
    GLOBIRD_PLAN_NOT_ZEROHERO,
    GLOBIRD_PLAN_ZEROHERO_CUSTOM,
    DEFAULT_GLOBIRD_ZEROHERO_START,
    DEFAULT_GLOBIRD_ZEROHERO_END,
    DEFAULT_GLOBIRD_ZEROHERO_EXPORT_CAP_KWH,
    DEFAULT_GLOBIRD_ZEROHERO_SUPER_EXPORT_RATE,
    DEFAULT_GLOBIRD_ZEROHERO_CREDIT_AMOUNT,
    DEFAULT_GLOBIRD_ZEROHERO_IMPORT_LIMIT_KW,
    DEFAULT_GLOBIRD_ZEROCHARGE_START,
    DEFAULT_GLOBIRD_ZEROCHARGE_END,
    DEFAULT_GLOBIRD_ZEROCHARGE_IMPORT_CAP_KWH,
    CONF_ELECTRICITY_PROVIDER,
    CONF_FLOW_POWER_STATE,
    CONF_FLOW_POWER_PRICE_SOURCE,
    CONF_NETWORK_DISTRIBUTOR,
    CONF_NETWORK_TARIFF_CODE,
    CONF_NETWORK_USE_MANUAL_RATES,
    CONF_NETWORK_TARIFF_TYPE,
    CONF_NETWORK_FLAT_RATE,
    CONF_NETWORK_PEAK_RATE,
    CONF_NETWORK_SHOULDER_RATE,
    CONF_NETWORK_OFFPEAK_RATE,
    CONF_NETWORK_PEAK_START,
    CONF_NETWORK_PEAK_END,
    CONF_NETWORK_OFFPEAK_START,
    CONF_NETWORK_OFFPEAK_END,
    CONF_NETWORK_OTHER_FEES,
    CONF_NETWORK_INCLUDE_GST,
    CONF_PEA_ENABLED,
    CONF_FLOW_POWER_BASE_RATE,
    CONF_FLOW_POWER_EXPORT_RATE,
    CONF_PEA_CUSTOM_VALUE,
    FLOW_POWER_DEFAULT_BASE_RATE,
    CONF_EXPORT_BOOST_ENABLED,
    CONF_EXPORT_PRICE_OFFSET,
    CONF_EXPORT_MIN_PRICE,
    CONF_EXPORT_BOOST_START,
    CONF_EXPORT_BOOST_END,
    CONF_EXPORT_BOOST_THRESHOLD,
    DEFAULT_EXPORT_PRICE_OFFSET,
    DEFAULT_EXPORT_MIN_PRICE,
    DEFAULT_EXPORT_BOOST_START,
    DEFAULT_EXPORT_BOOST_END,
    DEFAULT_EXPORT_BOOST_THRESHOLD,
    CONF_CHIP_MODE_ENABLED,
    CONF_CHIP_MODE_START,
    CONF_CHIP_MODE_END,
    CONF_CHIP_MODE_THRESHOLD,
    DEFAULT_CHIP_MODE_START,
    DEFAULT_CHIP_MODE_END,
    DEFAULT_CHIP_MODE_THRESHOLD,
    CONF_SPIKE_PROTECTION_ENABLED,
    CONF_FORECAST_DISCREPANCY_ALERT,
    CONF_FORECAST_DISCREPANCY_THRESHOLD,
    DEFAULT_FORECAST_DISCREPANCY_THRESHOLD,
    CONF_PRICE_SPIKE_ALERT,
    CONF_PRICE_SPIKE_IMPORT_THRESHOLD,
    CONF_PRICE_SPIKE_EXPORT_THRESHOLD,
    DEFAULT_PRICE_SPIKE_IMPORT_THRESHOLD,
    DEFAULT_PRICE_SPIKE_EXPORT_THRESHOLD,
    CONF_AC_INVERTER_CURTAILMENT_ENABLED,
    CONF_SIGENERGY_STATION_ID,
    CONF_SIGENERGY_MODBUS_HOST,
    CONF_FRONIUS_RESERVA_CONFIG_ENTRY_ID,
    CONF_FRONIUS_RESERVA_BATTERY_CAPACITY_KWH,
    CONF_FRONIUS_RESERVA_MAX_CHARGE_KW,
    CONF_FRONIUS_RESERVA_MAX_DISCHARGE_KW,
    BATTERY_SYSTEM_ANKER_SOLIX,
    CONF_ANKER_SOLIX_CONNECTION_TYPE,
    ANKER_SOLIX_CONNECTION_MODBUS,
    ANKER_SOLIX_CONNECTION_CLOUD_HA,
    CONF_BATTERY_SYSTEM,
    CONF_SUNGROW_HOST,
    CONF_SUNGROW_PORT,
    DEFAULT_SUNGROW_PORT,
    CONF_FOXESS_HOST,
    CONF_FOXESS_CONNECTION_TYPE,
    CONF_FOXESS_SERIAL_PORT,
    CONF_FOXESS_MODEL_FAMILY,
    FOXESS_CONNECTION_TCP,
    CONF_GOODWE_HOST,
    CONF_EV_PROVIDER,
)
from ..currency import (
    currency_for_entry,
    normalize_currency,
)
from .. import (
    _LOGGER,
    _battery_health_payload_is_newer,
    _configured_battery_capacity_kwh,
    _current_capacity_from_soh_kwh,
    _is_powersync_force_tariff,
    _parse_json_request,
    _select_restorable_tesla_tariff,
    _tariff_display_name,
    convert_custom_tariff_to_schedule,
    fetch_tesla_tariff_schedule,
    get_current_price_from_tariff_schedule,
)

class AEMOSpikeView(HomeAssistantView):
    """HTTP view to manage AEMO spike detection for all battery systems.

    GET: Returns current spike status and whether feature is enabled
    POST: Enable/disable the spike detection feature
    """

    url = "/api/power_sync/aemo_spike"
    name = "api:power_sync:aemo_spike"
    requires_auth = True

    def __init__(self, hass: HomeAssistant):
        """Initialize the view."""
        self._hass = hass

    async def get(self, request: web.Request) -> web.Response:
        """Handle GET request for AEMO spike status."""
        _LOGGER.info("AEMO spike status HTTP request")

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
            # Check if feature is enabled in config (shared key for all battery systems)
            enabled = entry.options.get(
                CONF_AEMO_SPIKE_ENABLED,
                entry.data.get(CONF_AEMO_SPIKE_ENABLED, False)
            )
            region = entry.options.get(
                CONF_AEMO_REGION,
                entry.data.get(CONF_AEMO_REGION)
            )
            threshold = entry.options.get(
                CONF_AEMO_SPIKE_THRESHOLD,
                entry.data.get(CONF_AEMO_SPIKE_THRESHOLD, 3000.0)
            )

            # Look up active spike manager (generic for non-Tesla, or Tesla-specific)
            entry_data = self._hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
            spike_manager = entry_data.get("generic_aemo_spike_manager") or entry_data.get("aemo_spike_manager")

            if spike_manager:
                status = spike_manager.get_status()
            else:
                # Determine battery system for status response
                battery_system = "tesla"
                if entry.data.get(CONF_SIGENERGY_STATION_ID):
                    battery_system = "sigenergy"
                elif entry.data.get(CONF_SUNGROW_HOST):
                    battery_system = "sungrow"
                elif entry.data.get(CONF_FOXESS_HOST) or entry.data.get(CONF_FOXESS_SERIAL_PORT):
                    battery_system = "foxess"
                elif entry.data.get(CONF_GOODWE_HOST):
                    battery_system = "goodwe"

                status = {
                    "enabled": enabled,
                    "region": region,
                    "threshold": threshold,
                    "in_spike_mode": False,
                    "last_price": None,
                    "spike_start_time": None,
                    "last_check": None,
                    "battery_system": battery_system,
                }

            result = {
                "success": True,
                "enabled": enabled,
                "region": region,
                "threshold": threshold,
                **status,
            }

            _LOGGER.info(
                "AEMO spike status: enabled=%s, region=%s, in_spike_mode=%s",
                enabled,
                region,
                status.get("in_spike_mode", False),
            )
            return web.json_response(result)

        except Exception as e:
            _LOGGER.error("Error fetching AEMO spike status: %s", e, exc_info=True)
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500
            )

    async def post(self, request: web.Request) -> web.Response:
        """Handle POST request to enable/disable AEMO spike detection."""
        _LOGGER.info("AEMO spike settings POST request")

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
            body = await _parse_json_request(request)

            # Handle enabling/disabling the feature
            if "enabled" in body:
                new_enabled = bool(body["enabled"])

                # Update config entry options (shared key for all battery systems)
                new_options = {**entry.options, CONF_AEMO_SPIKE_ENABLED: new_enabled}
                domain_data = self._hass.data.get(DOMAIN, {})
                entry_data = domain_data.get(entry.entry_id, {})
                if new_options != dict(entry.options):
                    entry_data["_skip_reload"] = True
                self._hass.config_entries.async_update_entry(entry, options=new_options)

                _LOGGER.info(
                    "AEMO spike detection %s",
                    "enabled" if new_enabled else "disabled",
                )

                # Note: The spike manager will be created/destroyed on next HA reload
                # For immediate effect, user should reload the integration

                return web.json_response({
                    "success": True,
                    "enabled": new_enabled,
                    "message": "Settings updated. Reload PowerSync integration to apply changes.",
                })

            # Handle AEMO region update
            if "region" in body:
                new_region = body["region"]
                if new_region not in ["NSW1", "QLD1", "VIC1", "SA1", "TAS1"]:
                    return web.json_response({
                        "success": False,
                        "error": f"Invalid region: {new_region}. Must be NSW1, QLD1, VIC1, SA1, or TAS1."
                    }, status=400)

                new_options = {**entry.options, CONF_AEMO_REGION: new_region}
                domain_data = self._hass.data.get(DOMAIN, {})
                entry_data = domain_data.get(entry.entry_id, {})
                if new_options != dict(entry.options):
                    entry_data["_skip_reload"] = True
                self._hass.config_entries.async_update_entry(entry, options=new_options)

                _LOGGER.info("AEMO region updated to %s", new_region)

                return web.json_response({
                    "success": True,
                    "region": new_region,
                    "message": "Region updated. Reload PowerSync integration to apply changes.",
                })

            return web.json_response({
                "success": False,
                "error": "No valid settings provided. Use 'enabled' or 'region'."
            }, status=400)

        except Exception as e:
            _LOGGER.error("Error updating AEMO spike settings: %s", e, exc_info=True)
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500
            )

class AmberUsageView(HomeAssistantView):
    """HTTP view for actual metered usage and cost data from Amber.

    GET ?period=yesterday|week|month|last_month — aggregated summary with savings
    GET ?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD — custom range with daily breakdown
    """

    url = "/api/power_sync/amber_usage"
    name = "api:power_sync:amber_usage"
    requires_auth = True

    def __init__(self, hass: HomeAssistant):
        """Initialize the view."""
        self._hass = hass

    async def get(self, request: web.Request) -> web.Response:
        """Handle GET request for Amber usage data."""
        # Find the power_sync entry
        entry = None
        for config_entry in self._hass.config_entries.async_entries(DOMAIN):
            entry = config_entry
            break

        if not entry:
            return web.json_response(
                {"success": False, "error": "PowerSync not configured"},
                status=503,
            )

        entry_data = self._hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
        usage_coord = entry_data.get("amber_usage_coordinator")
        if not usage_coord:
            return web.json_response(
                {"success": False, "error": "Amber usage tracking not available"},
                status=404,
            )

        try:
            period = request.query.get("period")
            start_date = request.query.get("start_date")
            end_date = request.query.get("end_date")

            if start_date and end_date:
                # Custom date range — return daily breakdown
                days = usage_coord.get_range(start_date, end_date)
                return web.json_response({
                    "success": True,
                    "range": {"start_date": start_date, "end_date": end_date},
                    "days": days,
                    "last_fetch": usage_coord.last_fetch_iso,
                })

            if not period:
                period = "yesterday"

            if period not in ("yesterday", "week", "month", "last_month"):
                return web.json_response(
                    {"success": False, "error": f"Invalid period: {period}"},
                    status=400,
                )

            summary = usage_coord.get_savings_summary(period)
            return web.json_response({
                "success": True,
                "period": period,
                **summary,
                "last_fetch": usage_coord.last_fetch_iso,
            })

        except Exception as e:
            _LOGGER.error("Error fetching Amber usage: %s", e, exc_info=True)
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500,
            )

class ConfigView(HomeAssistantView):
    """HTTP view to get backend configuration for mobile app auto-detection."""

    url = "/api/power_sync/backend_config"
    name = "api:power_sync:backend_config"
    requires_auth = True

    def __init__(self, hass: HomeAssistant):
        """Initialize the view."""
        self._hass = hass

    async def get(self, request: web.Request) -> web.Response:
        """Handle GET request for backend configuration."""
        _LOGGER.info("📱 Config HTTP request (mobile app auto-detection)")

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
            # Get battery system from config
            battery_system = entry.data.get(CONF_BATTERY_SYSTEM, "tesla")

            # Get electricity provider
            electricity_provider = entry.options.get(
                CONF_ELECTRICITY_PROVIDER,
                entry.data.get(CONF_ELECTRICITY_PROVIDER, "amber")
            )

            # Build features dict based on configuration
            features = {
                "solar_curtailment": entry.options.get(
                    CONF_BATTERY_CURTAILMENT_ENABLED,
                    entry.data.get(CONF_BATTERY_CURTAILMENT_ENABLED, False)
                ),
                "inverter_control": entry.options.get(
                    CONF_AC_INVERTER_CURTAILMENT_ENABLED,
                    entry.data.get(CONF_AC_INVERTER_CURTAILMENT_ENABLED, False)
                ),
                "spike_protection": entry.options.get(
                    CONF_SPIKE_PROTECTION_ENABLED,
                    entry.data.get(CONF_SPIKE_PROTECTION_ENABLED, False)
                ),
                "export_boost": entry.options.get(
                    CONF_EXPORT_BOOST_ENABLED,
                    entry.data.get(CONF_EXPORT_BOOST_ENABLED, False)
                ),
                "demand_charges": entry.options.get(
                    CONF_DEMAND_CHARGE_ENABLED,
                    entry.data.get(CONF_DEMAND_CHARGE_ENABLED, False)
                ),
                "auto_sync": entry.options.get(
                    CONF_AUTO_SYNC_ENABLED,
                    entry.data.get(CONF_AUTO_SYNC_ENABLED, True)
                ),
            }

            # Add Sigenergy-specific info if applicable
            sigenergy_config = None
            if battery_system == "sigenergy":
                sigenergy_config = {
                    "station_id": entry.data.get(CONF_SIGENERGY_STATION_ID),
                    "modbus_enabled": bool(entry.options.get(
                        CONF_SIGENERGY_MODBUS_HOST,
                        entry.data.get(CONF_SIGENERGY_MODBUS_HOST)
                    )),
                }

            # Add Sungrow-specific info if applicable
            sungrow_config = None
            if battery_system == "sungrow":
                sungrow_host = entry.options.get(
                    CONF_SUNGROW_HOST,
                    entry.data.get(CONF_SUNGROW_HOST)
                )
                sungrow_config = {
                    "host": sungrow_host,
                    "port": entry.options.get(
                        CONF_SUNGROW_PORT,
                        entry.data.get(CONF_SUNGROW_PORT, DEFAULT_SUNGROW_PORT)
                    ),
                    "modbus_enabled": bool(sungrow_host),
                    "aemo_spike_enabled": entry.options.get(
                        CONF_AEMO_SPIKE_ENABLED,
                        entry.data.get(CONF_AEMO_SPIKE_ENABLED, False)
                    ),
                    "aemo_region": entry.options.get(
                        CONF_AEMO_REGION,
                        entry.data.get(CONF_AEMO_REGION)
                    ),
                    "aemo_threshold": entry.options.get(
                        CONF_AEMO_SPIKE_THRESHOLD,
                        entry.data.get(CONF_AEMO_SPIKE_THRESHOLD, 3000.0)
                    ),
                }

            # Add FoxESS-specific info if applicable
            foxess_config = None
            if battery_system == "foxess":
                foxess_host = entry.options.get(
                    CONF_FOXESS_HOST,
                    entry.data.get(CONF_FOXESS_HOST)
                )
                foxess_config = {
                    "host": foxess_host,
                    "model_family": entry.data.get(CONF_FOXESS_MODEL_FAMILY, "unknown"),
                    "modbus_enabled": bool(foxess_host or entry.data.get(CONF_FOXESS_SERIAL_PORT)),
                    "connection_type": entry.data.get(CONF_FOXESS_CONNECTION_TYPE, FOXESS_CONNECTION_TCP),
                }

            # Add GoodWe-specific info if applicable
            goodwe_config = None
            if battery_system == "goodwe":
                goodwe_host = entry.options.get(
                    CONF_GOODWE_HOST,
                    entry.data.get(CONF_GOODWE_HOST)
                )
                goodwe_config = {
                    "host": goodwe_host,
                    "model_name": "auto-detected",
                    "modbus_enabled": bool(goodwe_host),
                }

            # Add Fronius GEN24 storage bridge info if applicable
            fronius_reserva_config = None
            if battery_system == "fronius_reserva":
                fronius_reserva_config = {
                    "config_entry_id": entry.data.get(CONF_FRONIUS_RESERVA_CONFIG_ENTRY_ID),
                    "battery_capacity_kwh": entry.options.get(
                        CONF_FRONIUS_RESERVA_BATTERY_CAPACITY_KWH,
                        entry.data.get(CONF_FRONIUS_RESERVA_BATTERY_CAPACITY_KWH),
                    ),
                    "max_charge_kw": entry.options.get(
                        CONF_FRONIUS_RESERVA_MAX_CHARGE_KW,
                        entry.data.get(CONF_FRONIUS_RESERVA_MAX_CHARGE_KW),
                    ),
                    "max_discharge_kw": entry.options.get(
                        CONF_FRONIUS_RESERVA_MAX_DISCHARGE_KW,
                        entry.data.get(CONF_FRONIUS_RESERVA_MAX_DISCHARGE_KW),
                    ),
                    "integration_domain": "fronius_modbus",
                    "modbus_enabled": bool(entry.data.get(CONF_FRONIUS_RESERVA_CONFIG_ENTRY_ID)),
                }

            # Get EV provider configuration
            ev_provider = entry.options.get(
                CONF_EV_PROVIDER,
                entry.data.get(CONF_EV_PROVIDER)
            )

            # Include battery health summary
            battery_health = None
            domain_data = self._hass.data.get(DOMAIN, {})
            entry_data = domain_data.get(entry.entry_id, {})
            health_data = entry_data.get("battery_health")

            # Tesla: prefer live RSA/TEDAPI BMS data from the cloud cache over
            # stale WiFi-scan data when it is newer. The cloud cache is populated
            # by GET requests to /api/power_sync/battery_health and expires after 1 h.
            import time as _bh_time
            bms_cloud = entry_data.get("battery_health_cloud")
            cloud_health = None
            if (
                battery_system == "tesla"
                and bms_cloud
                and bms_cloud.get("expires_at", 0) > _bh_time.monotonic()
            ):
                bms_val = bms_cloud.get("value", {})
                cloud_health = {
                    "health_percent": bms_val.get("health_percent"),
                    "original_capacity_kwh": bms_val.get("original_capacity_kwh"),
                    "current_capacity_kwh": bms_val.get("current_capacity_kwh"),
                    "battery_count": bms_val.get("battery_count"),
                    "last_scan": bms_val.get("last_scan"),
                    "source": bms_val.get("source", "rsa_bms"),
                }

            if health_data:
                # Stored WiFi-scan / mobile-app POST data
                original = health_data.get("original_capacity_wh", 0)
                current = health_data.get("current_capacity_wh", 0)
                stored_health = {
                    "health_percent": round((current / original) * 100, 1) if original > 0 else 0,
                    "original_capacity_kwh": round(original / 1000, 2),
                    "current_capacity_kwh": round(current / 1000, 2),
                    "degradation_percent": health_data.get("degradation_percent"),
                    "battery_count": health_data.get("battery_count", 1),
                    "last_scan": health_data.get("scanned_at"),
                    "source": health_data.get("source", "mobile_app_wifi_scan"),
                }
                if (
                    cloud_health
                    and _battery_health_payload_is_newer(
                        cloud_health.get("last_scan"),
                        stored_health.get("last_scan"),
                    )
                ):
                    battery_health = cloud_health
                else:
                    battery_health = stored_health
            elif cloud_health:
                battery_health = cloud_health

            if not battery_health:
                # Fall back to coordinator battery_soh (Sungrow, Sigenergy, GoodWe)
                for key in ("sungrow_coordinator", "sigenergy_coordinator", "goodwe_coordinator", "alphaess_coordinator", "solax_coordinator", "saj_h2_coordinator", "fronius_reserva_coordinator", "neovolt_coordinator", "solaredge_coordinator"):
                    coord = entry_data.get(key)
                    if coord and coord.data:
                        soh = coord.data.get("battery_soh")
                        if soh is not None and soh > 0:
                            health_percent = round(float(soh), 1)
                            battery_health = {
                                "health_percent": health_percent,
                                "source": "inverter_modbus",
                            }
                            rated_capacity_kwh = coord.data.get("battery_capacity_kwh")
                            if rated_capacity_kwh is None and key == "sungrow_coordinator":
                                rated_capacity_kwh = _configured_battery_capacity_kwh(entry)
                            if rated_capacity_kwh is not None:
                                rated_capacity_kwh = round(float(rated_capacity_kwh), 2)
                                battery_health["original_capacity_kwh"] = rated_capacity_kwh
                                current_capacity_kwh = _current_capacity_from_soh_kwh(
                                    rated_capacity_kwh,
                                    health_percent,
                                )
                                if current_capacity_kwh is not None:
                                    battery_health["current_capacity_kwh"] = current_capacity_kwh
                            break

            # Look up actual entity_ids from the entity registry
            # (HA derives entity_ids from device name, not our suggested_object_id)
            ent_reg = er.async_get(self._hass)
            sensor_keys = [
                "solar_power", "battery_power", "grid_power", "home_load",
                "battery_level", "grid_status", "firmware",
                "current_import_price", "current_export_price",
                "tariff_schedule", "battery_health", "battery_mode",
                "aemo_spike_status",
                "solcast_today_forecast", "solcast_tomorrow_forecast",
                "solcast_current_estimate",
            ]
            entity_ids = {}
            for key in sensor_keys:
                unique_id = f"{entry.entry_id}_{key}"
                eid = ent_reg.async_get_entity_id("sensor", DOMAIN, unique_id)
                if eid:
                    entity_ids[key] = eid

            result = {
                "success": True,
                "battery_system": battery_system,
                "electricity_provider": electricity_provider,
                "ev_provider": ev_provider,  # Tesla (fleet_api/tesla_ble/both) or None for OCPP-only
                "features": features,
                "battery_health": battery_health,
                "entity_ids": entity_ids,
                "sigenergy": sigenergy_config,
                "sungrow": sungrow_config,
                "foxess": foxess_config,
                "goodwe": goodwe_config,
                "fronius_reserva": fronius_reserva_config,
            }

            _LOGGER.info(f"✅ Config response: battery_system={battery_system}, provider={electricity_provider}")
            return web.json_response(result)

        except Exception as e:
            _LOGGER.error(f"Error fetching config: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500
            )

class ConfigViewLegacy(HomeAssistantView):
    """Legacy HTTP view at old URL for backwards compatibility."""

    url = "/api/power_sync/config"
    name = "api:power_sync:config"
    requires_auth = True

    def __init__(self, hass: HomeAssistant, config_view: ConfigView):
        """Initialize the view."""
        self._config_view = config_view

    async def get(self, request: web.Request) -> web.Response:
        """Handle GET request - delegate to main ConfigView."""
        _LOGGER.info("📱 Config HTTP request (legacy URL)")
        return await self._config_view.get(request)

class ProviderConfigView(HomeAssistantView):
    """HTTP view for electricity provider configuration.

    GET: Returns current provider type and all relevant settings
    POST: Updates provider settings via config entry options
    """

    url = "/api/power_sync/provider_config"
    name = "api:power_sync:provider_config"
    requires_auth = True

    def __init__(self, hass: HomeAssistant):
        """Initialize the view."""
        self._hass = hass

    async def get(self, request: web.Request) -> web.Response:
        """Handle GET request for provider configuration."""
        _LOGGER.info("⚡ Provider config HTTP GET request")

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
            # Get battery system and electricity provider
            battery_system = entry.data.get(CONF_BATTERY_SYSTEM, "tesla")
            electricity_provider = entry.options.get(
                CONF_ELECTRICITY_PROVIDER,
                entry.data.get(CONF_ELECTRICITY_PROVIDER, "amber")
            )

            # Build provider-specific config based on provider type
            config = {}

            if electricity_provider == "amber":
                # Amber Electric settings
                config = {
                    "auto_sync": entry.options.get(
                        CONF_AUTO_SYNC_ENABLED,
                        entry.data.get(CONF_AUTO_SYNC_ENABLED, True)
                    ),
                    "forecast_type": entry.options.get(
                        CONF_AMBER_FORECAST_TYPE,
                        entry.data.get(CONF_AMBER_FORECAST_TYPE, "predicted")
                    ),
                    "spike_protection_enabled": entry.options.get(
                        CONF_SPIKE_PROTECTION_ENABLED,
                        entry.data.get(CONF_SPIKE_PROTECTION_ENABLED, False)
                    ),
                    # Forecast Discrepancy Alert settings
                    "forecast_discrepancy_alert": entry.options.get(
                        CONF_FORECAST_DISCREPANCY_ALERT,
                        entry.data.get(CONF_FORECAST_DISCREPANCY_ALERT, False)
                    ),
                    "forecast_discrepancy_threshold": entry.options.get(
                        CONF_FORECAST_DISCREPANCY_THRESHOLD,
                        entry.data.get(CONF_FORECAST_DISCREPANCY_THRESHOLD, DEFAULT_FORECAST_DISCREPANCY_THRESHOLD)
                    ),
                    # Price Spike Alert settings
                    "price_spike_alert": entry.options.get(
                        CONF_PRICE_SPIKE_ALERT,
                        entry.data.get(CONF_PRICE_SPIKE_ALERT, False)
                    ),
                    "price_spike_import_threshold": entry.options.get(
                        CONF_PRICE_SPIKE_IMPORT_THRESHOLD,
                        entry.data.get(CONF_PRICE_SPIKE_IMPORT_THRESHOLD, DEFAULT_PRICE_SPIKE_IMPORT_THRESHOLD)
                    ),
                    "price_spike_export_threshold": entry.options.get(
                        CONF_PRICE_SPIKE_EXPORT_THRESHOLD,
                        entry.data.get(CONF_PRICE_SPIKE_EXPORT_THRESHOLD, DEFAULT_PRICE_SPIKE_EXPORT_THRESHOLD)
                    ),
                    # Export Boost settings
                    "export_boost_enabled": entry.options.get(
                        CONF_EXPORT_BOOST_ENABLED,
                        entry.data.get(CONF_EXPORT_BOOST_ENABLED, False)
                    ),
                    "export_price_offset": entry.options.get(
                        CONF_EXPORT_PRICE_OFFSET,
                        entry.data.get(CONF_EXPORT_PRICE_OFFSET, DEFAULT_EXPORT_PRICE_OFFSET)
                    ),
                    "export_min_price": entry.options.get(
                        CONF_EXPORT_MIN_PRICE,
                        entry.data.get(CONF_EXPORT_MIN_PRICE, DEFAULT_EXPORT_MIN_PRICE)
                    ),
                    "export_boost_start": entry.options.get(
                        CONF_EXPORT_BOOST_START,
                        entry.data.get(CONF_EXPORT_BOOST_START, DEFAULT_EXPORT_BOOST_START)
                    ),
                    "export_boost_end": entry.options.get(
                        CONF_EXPORT_BOOST_END,
                        entry.data.get(CONF_EXPORT_BOOST_END, DEFAULT_EXPORT_BOOST_END)
                    ),
                    "export_boost_threshold": entry.options.get(
                        CONF_EXPORT_BOOST_THRESHOLD,
                        entry.data.get(CONF_EXPORT_BOOST_THRESHOLD, DEFAULT_EXPORT_BOOST_THRESHOLD)
                    ),
                    # Chip Mode settings
                    "chip_mode_enabled": entry.options.get(
                        CONF_CHIP_MODE_ENABLED,
                        entry.data.get(CONF_CHIP_MODE_ENABLED, False)
                    ),
                    "chip_mode_start": entry.options.get(
                        CONF_CHIP_MODE_START,
                        entry.data.get(CONF_CHIP_MODE_START, DEFAULT_CHIP_MODE_START)
                    ),
                    "chip_mode_end": entry.options.get(
                        CONF_CHIP_MODE_END,
                        entry.data.get(CONF_CHIP_MODE_END, DEFAULT_CHIP_MODE_END)
                    ),
                    "chip_mode_threshold": entry.options.get(
                        CONF_CHIP_MODE_THRESHOLD,
                        entry.data.get(CONF_CHIP_MODE_THRESHOLD, DEFAULT_CHIP_MODE_THRESHOLD)
                    ),
                }

            elif electricity_provider == "covau":
                from ..const import (
                    CONF_COVAU_DISTRIBUTOR,
                    CONF_COVAU_EXPORT_ENERGY_ENTITY,
                    CONF_COVAU_IMPORT_ENERGY_ENTITY,
                    CONF_COVAU_MANUAL_TARIFF,
                    CONF_COVAU_PLAN_ID,
                    CONF_COVAU_PLAN_SNAPSHOT,
                    CONF_COVAU_POSTCODE,
                )

                def _covau_entry_value(key: str, default=None):
                    return entry.options.get(key, entry.data.get(key, default))

                entry_runtime = self._hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
                quota_runtime = entry_runtime.get("covau_quota_runtime")
                opt_coordinator = entry_runtime.get("optimization_coordinator")
                provider_contract = (
                    quota_runtime.contract()
                    if quota_runtime is not None
                    else opt_coordinator.get_provider_contract()
                    if opt_coordinator is not None
                    and hasattr(opt_coordinator, "get_provider_contract")
                    else None
                )
                if provider_contract is None:
                    snapshot_raw = _covau_entry_value(CONF_COVAU_PLAN_SNAPSHOT)
                    if isinstance(snapshot_raw, dict):
                        try:
                            from ..covau import (
                                CovaUPlanSnapshot,
                                covau_provider_contract,
                                covau_quota_rules,
                            )
                            from ..quota import QuotaLedger

                            snapshot = CovaUPlanSnapshot.from_dict(snapshot_raw)
                            provider_contract = covau_provider_contract(
                                snapshot,
                                QuotaLedger(covau_quota_rules(snapshot)),
                                import_energy_entity=_covau_entry_value(
                                    CONF_COVAU_IMPORT_ENERGY_ENTITY
                                ),
                                export_energy_entity=_covau_entry_value(
                                    CONF_COVAU_EXPORT_ENERGY_ENTITY
                                ),
                            )
                        except (KeyError, TypeError, ValueError):
                            _LOGGER.warning(
                                "CovaU provider config contains an invalid plan snapshot",
                                exc_info=True,
                            )

                config_metadata = {
                    "postcode": _covau_entry_value(CONF_COVAU_POSTCODE, ""),
                    "plan_id": _covau_entry_value(CONF_COVAU_PLAN_ID, ""),
                    "distributor": _covau_entry_value(CONF_COVAU_DISTRIBUTOR, ""),
                    "manual_tariff": bool(
                        _covau_entry_value(CONF_COVAU_MANUAL_TARIFF, False)
                    ),
                    "import_energy_entity": _covau_entry_value(
                        CONF_COVAU_IMPORT_ENERGY_ENTITY, ""
                    ),
                    "export_energy_entity": _covau_entry_value(
                        CONF_COVAU_EXPORT_ENERGY_ENTITY, ""
                    ),
                }
                # The mobile/public v1 contract lives at config root. Keep the
                # setup metadata as additive fields so older settings screens
                # still have their read-only identifiers during rollout.
                config = (
                    {**provider_contract, **config_metadata}
                    if isinstance(provider_contract, dict)
                    else config_metadata
                )

            elif electricity_provider == "flow_power":
                # Flow Power settings
                config = {
                    "auto_sync": entry.options.get(
                        CONF_AUTO_SYNC_ENABLED,
                        entry.data.get(CONF_AUTO_SYNC_ENABLED, True)
                    ),
                    "state": entry.options.get(
                        CONF_FLOW_POWER_STATE,
                        entry.data.get(CONF_FLOW_POWER_STATE, "NSW1")
                    ),
                    "price_source": entry.options.get(
                        CONF_FLOW_POWER_PRICE_SOURCE,
                        entry.data.get(CONF_FLOW_POWER_PRICE_SOURCE, "amber")
                    ),
                    # Network Tariff settings
                    "network_distributor": entry.options.get(
                        CONF_NETWORK_DISTRIBUTOR,
                        entry.data.get(CONF_NETWORK_DISTRIBUTOR, "")
                    ),
                    "network_tariff_code": entry.options.get(
                        CONF_NETWORK_TARIFF_CODE,
                        entry.data.get(CONF_NETWORK_TARIFF_CODE, "")
                    ),
                    "network_use_manual_rates": entry.options.get(
                        CONF_NETWORK_USE_MANUAL_RATES,
                        entry.data.get(CONF_NETWORK_USE_MANUAL_RATES, False)
                    ),
                    "network_tariff_type": entry.options.get(
                        CONF_NETWORK_TARIFF_TYPE,
                        entry.data.get(CONF_NETWORK_TARIFF_TYPE, "flat")
                    ),
                    "network_flat_rate": entry.options.get(
                        CONF_NETWORK_FLAT_RATE,
                        entry.data.get(CONF_NETWORK_FLAT_RATE, 0.0)
                    ),
                    "network_peak_rate": entry.options.get(
                        CONF_NETWORK_PEAK_RATE,
                        entry.data.get(CONF_NETWORK_PEAK_RATE, 0.0)
                    ),
                    "network_shoulder_rate": entry.options.get(
                        CONF_NETWORK_SHOULDER_RATE,
                        entry.data.get(CONF_NETWORK_SHOULDER_RATE, 0.0)
                    ),
                    "network_offpeak_rate": entry.options.get(
                        CONF_NETWORK_OFFPEAK_RATE,
                        entry.data.get(CONF_NETWORK_OFFPEAK_RATE, 0.0)
                    ),
                    "network_peak_start": entry.options.get(
                        CONF_NETWORK_PEAK_START,
                        entry.data.get(CONF_NETWORK_PEAK_START, "")
                    ),
                    "network_peak_end": entry.options.get(
                        CONF_NETWORK_PEAK_END,
                        entry.data.get(CONF_NETWORK_PEAK_END, "")
                    ),
                    "network_offpeak_start": entry.options.get(
                        CONF_NETWORK_OFFPEAK_START,
                        entry.data.get(CONF_NETWORK_OFFPEAK_START, "")
                    ),
                    "network_offpeak_end": entry.options.get(
                        CONF_NETWORK_OFFPEAK_END,
                        entry.data.get(CONF_NETWORK_OFFPEAK_END, "")
                    ),
                    "network_other_fees": entry.options.get(
                        CONF_NETWORK_OTHER_FEES,
                        entry.data.get(CONF_NETWORK_OTHER_FEES, 0.0)
                    ),
                    "network_include_gst": entry.options.get(
                        CONF_NETWORK_INCLUDE_GST,
                        entry.data.get(CONF_NETWORK_INCLUDE_GST, True)
                    ),
                    # PEA settings
                    "pea_enabled": entry.options.get(
                        CONF_PEA_ENABLED,
                        entry.data.get(CONF_PEA_ENABLED, False)
                    ),
                    "flow_power_base_rate": entry.options.get(
                        CONF_FLOW_POWER_BASE_RATE,
                        entry.data.get(CONF_FLOW_POWER_BASE_RATE, FLOW_POWER_DEFAULT_BASE_RATE)
                    ),
                    "flow_power_export_rate": entry.options.get(
                        CONF_FLOW_POWER_EXPORT_RATE,
                        entry.data.get(CONF_FLOW_POWER_EXPORT_RATE, None)
                    ),
                    "pea_custom_value": entry.options.get(
                        CONF_PEA_CUSTOM_VALUE,
                        entry.data.get(CONF_PEA_CUSTOM_VALUE, None)
                    ),
                    # Demand Charges settings
                    "demand_charge_enabled": entry.options.get(
                        CONF_DEMAND_CHARGE_ENABLED,
                        entry.data.get(CONF_DEMAND_CHARGE_ENABLED, False)
                    ),
                    "demand_charge_rate": entry.options.get(
                        CONF_DEMAND_CHARGE_RATE,
                        entry.data.get(CONF_DEMAND_CHARGE_RATE, 0.0)
                    ),
                    "demand_charge_start_time": entry.options.get(
                        CONF_DEMAND_CHARGE_START_TIME,
                        entry.data.get(CONF_DEMAND_CHARGE_START_TIME, "16:00")
                    ),
                    "demand_charge_end_time": entry.options.get(
                        CONF_DEMAND_CHARGE_END_TIME,
                        entry.data.get(CONF_DEMAND_CHARGE_END_TIME, "21:00")
                    ),
                    "demand_charge_days": entry.options.get(
                        CONF_DEMAND_CHARGE_DAYS,
                        entry.data.get(CONF_DEMAND_CHARGE_DAYS, [0, 1, 2, 3, 4])
                    ),
                    "demand_charge_billing_day": entry.options.get(
                        CONF_DEMAND_CHARGE_BILLING_DAY,
                        entry.data.get(CONF_DEMAND_CHARGE_BILLING_DAY, 1)
                    ),
                }

            elif electricity_provider in ("globird", "aemo_vpp", "other", "tou_only"):
                # Custom TOU / AEMO-style settings
                config = {
                    "aemo_region": entry.options.get(
                        CONF_AEMO_REGION,
                        entry.data.get(CONF_AEMO_REGION, "NSW1")
                    ),
                    "aemo_spike_threshold": entry.options.get(
                        CONF_AEMO_SPIKE_THRESHOLD,
                        entry.data.get(CONF_AEMO_SPIKE_THRESHOLD, 300)
                    ),
                    "aemo_spike_enabled": entry.options.get(
                        CONF_AEMO_SPIKE_ENABLED,
                        entry.data.get(CONF_AEMO_SPIKE_ENABLED, True)
                    ),
                }
                if electricity_provider == "globird":
                    config.update({
                        "globird_plan": entry.options.get(
                            CONF_GLOBIRD_PLAN,
                            entry.data.get(CONF_GLOBIRD_PLAN, GLOBIRD_PLAN_NOT_ZEROHERO)
                        ),
                        "globird_zerohero_start": entry.options.get(
                            CONF_GLOBIRD_ZEROHERO_START,
                            entry.data.get(CONF_GLOBIRD_ZEROHERO_START, DEFAULT_GLOBIRD_ZEROHERO_START)
                        ),
                        "globird_zerohero_end": entry.options.get(
                            CONF_GLOBIRD_ZEROHERO_END,
                            entry.data.get(CONF_GLOBIRD_ZEROHERO_END, DEFAULT_GLOBIRD_ZEROHERO_END)
                        ),
                        "globird_zerohero_export_cap_kwh": entry.options.get(
                            CONF_GLOBIRD_ZEROHERO_EXPORT_CAP_KWH,
                            entry.data.get(CONF_GLOBIRD_ZEROHERO_EXPORT_CAP_KWH, DEFAULT_GLOBIRD_ZEROHERO_EXPORT_CAP_KWH)
                        ),
                        "globird_zerohero_super_export_rate": entry.options.get(
                            CONF_GLOBIRD_ZEROHERO_SUPER_EXPORT_RATE,
                            entry.data.get(CONF_GLOBIRD_ZEROHERO_SUPER_EXPORT_RATE, DEFAULT_GLOBIRD_ZEROHERO_SUPER_EXPORT_RATE)
                        ),
                        "globird_zerohero_credit_amount": entry.options.get(
                            CONF_GLOBIRD_ZEROHERO_CREDIT_AMOUNT,
                            entry.data.get(CONF_GLOBIRD_ZEROHERO_CREDIT_AMOUNT, DEFAULT_GLOBIRD_ZEROHERO_CREDIT_AMOUNT)
                        ),
                        "globird_zerohero_import_limit_kw": entry.options.get(
                            CONF_GLOBIRD_ZEROHERO_IMPORT_LIMIT_KW,
                            entry.data.get(CONF_GLOBIRD_ZEROHERO_IMPORT_LIMIT_KW, DEFAULT_GLOBIRD_ZEROHERO_IMPORT_LIMIT_KW)
                        ),
                        "globird_zerocharge_start": entry.options.get(
                            CONF_GLOBIRD_ZEROCHARGE_START,
                            entry.data.get(CONF_GLOBIRD_ZEROCHARGE_START, DEFAULT_GLOBIRD_ZEROCHARGE_START)
                        ),
                        "globird_zerocharge_end": entry.options.get(
                            CONF_GLOBIRD_ZEROCHARGE_END,
                            entry.data.get(CONF_GLOBIRD_ZEROCHARGE_END, DEFAULT_GLOBIRD_ZEROCHARGE_END)
                        ),
                        "globird_zerocharge_import_cap_kwh": entry.options.get(
                            CONF_GLOBIRD_ZEROCHARGE_IMPORT_CAP_KWH,
                            entry.data.get(CONF_GLOBIRD_ZEROCHARGE_IMPORT_CAP_KWH, DEFAULT_GLOBIRD_ZEROCHARGE_IMPORT_CAP_KWH)
                        ),
                    })
                    if (
                        config.get("globird_plan") == GLOBIRD_PLAN_ZEROHERO_CUSTOM
                        and not any(
                            key in entry.options or key in entry.data
                            for key in (
                                CONF_GLOBIRD_ZEROCHARGE_START,
                                CONF_GLOBIRD_ZEROCHARGE_END,
                                CONF_GLOBIRD_ZEROCHARGE_IMPORT_CAP_KWH,
                            )
                        )
                    ):
                        config.pop("globird_zerocharge_start", None)
                        config.pop("globird_zerocharge_end", None)
                        config.pop("globird_zerocharge_import_cap_kwh", None)

            elif electricity_provider == "nz":
                # NZ TOU settings
                from ..const import (
                    CONF_NZ_RETAILER, CONF_NZ_DISTRIBUTION_ZONE,
                    CONF_NZ_PEAK_RATE, CONF_NZ_SHOULDER_RATE, CONF_NZ_OFFPEAK_RATE,
                    CONF_NZ_PEAK_EXPORT, CONF_NZ_OFFPEAK_EXPORT, CONF_NZ_DAILY_SUPPLY,
                )
                config = {
                    "nz_retailer": entry.options.get(
                        CONF_NZ_RETAILER,
                        entry.data.get(CONF_NZ_RETAILER, "nz_custom")
                    ),
                    "nz_distribution_zone": entry.options.get(
                        CONF_NZ_DISTRIBUTION_ZONE,
                        entry.data.get(CONF_NZ_DISTRIBUTION_ZONE, "other")
                    ),
                    "nz_peak_rate": entry.options.get(
                        CONF_NZ_PEAK_RATE,
                        entry.data.get(CONF_NZ_PEAK_RATE, 40.0)
                    ),
                    "nz_shoulder_rate": entry.options.get(
                        CONF_NZ_SHOULDER_RATE,
                        entry.data.get(CONF_NZ_SHOULDER_RATE, 25.0)
                    ),
                    "nz_offpeak_rate": entry.options.get(
                        CONF_NZ_OFFPEAK_RATE,
                        entry.data.get(CONF_NZ_OFFPEAK_RATE, 15.0)
                    ),
                    "nz_peak_export": entry.options.get(
                        CONF_NZ_PEAK_EXPORT,
                        entry.data.get(CONF_NZ_PEAK_EXPORT, 8.0)
                    ),
                    "nz_offpeak_export": entry.options.get(
                        CONF_NZ_OFFPEAK_EXPORT,
                        entry.data.get(CONF_NZ_OFFPEAK_EXPORT, 8.0)
                    ),
                    "nz_daily_supply": entry.options.get(
                        CONF_NZ_DAILY_SUPPLY,
                        entry.data.get(CONF_NZ_DAILY_SUPPLY, 200.0)
                    ),
                }

            # Add demand charge fields for all providers
            config["demand_charge_enabled"] = entry.options.get(
                CONF_DEMAND_CHARGE_ENABLED,
                entry.data.get(CONF_DEMAND_CHARGE_ENABLED, False)
            )
            config["demand_charge_rate"] = entry.options.get(
                CONF_DEMAND_CHARGE_RATE,
                entry.data.get(CONF_DEMAND_CHARGE_RATE, 0.0)
            )
            config["demand_charge_start_time"] = entry.options.get(
                CONF_DEMAND_CHARGE_START_TIME,
                entry.data.get(CONF_DEMAND_CHARGE_START_TIME, "16:00")
            )
            config["demand_charge_end_time"] = entry.options.get(
                CONF_DEMAND_CHARGE_END_TIME,
                entry.data.get(CONF_DEMAND_CHARGE_END_TIME, "21:00")
            )
            config["demand_charge_days"] = entry.options.get(
                CONF_DEMAND_CHARGE_DAYS,
                entry.data.get(CONF_DEMAND_CHARGE_DAYS, [0, 1, 2, 3, 4])
            )
            config["demand_charge_billing_day"] = entry.options.get(
                CONF_DEMAND_CHARGE_BILLING_DAY,
                entry.data.get(CONF_DEMAND_CHARGE_BILLING_DAY, 1)
            )

            # Add price spike alert fields for all providers (notifications are provider-agnostic)
            config["price_spike_alert"] = entry.options.get(
                CONF_PRICE_SPIKE_ALERT,
                entry.data.get(CONF_PRICE_SPIKE_ALERT, False)
            )
            config["price_spike_import_threshold"] = entry.options.get(
                CONF_PRICE_SPIKE_IMPORT_THRESHOLD,
                entry.data.get(CONF_PRICE_SPIKE_IMPORT_THRESHOLD, DEFAULT_PRICE_SPIKE_IMPORT_THRESHOLD)
            )
            config["price_spike_export_threshold"] = entry.options.get(
                CONF_PRICE_SPIKE_EXPORT_THRESHOLD,
                entry.data.get(CONF_PRICE_SPIKE_EXPORT_THRESHOLD, DEFAULT_PRICE_SPIKE_EXPORT_THRESHOLD)
            )

            # Add monitoring mode flag (applies to all providers)
            config["monitoring_mode"] = entry.options.get(
                CONF_MONITORING_MODE,
                entry.data.get(CONF_MONITORING_MODE, False)
            )

            result = {
                "success": True,
                "electricity_provider": electricity_provider,
                "battery_system": battery_system,
                "config": config,
            }

            if battery_system == BATTERY_SYSTEM_ANKER_SOLIX:
                entry_data = self._hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
                anker_coord = entry_data.get("anker_solix_coordinator")
                anker_data = getattr(anker_coord, "data", None) or {}
                connection_type = entry.options.get(
                    CONF_ANKER_SOLIX_CONNECTION_TYPE,
                    entry.data.get(CONF_ANKER_SOLIX_CONNECTION_TYPE, ANKER_SOLIX_CONNECTION_MODBUS),
                )
                dispatch_supported = bool(anker_data.get("dispatch_supported", False))
                limitations: list[str] = []
                if connection_type == ANKER_SOLIX_CONNECTION_CLOUD_HA:
                    limitations.append(
                        "Unofficial Anker cloud bridge: data can be stale and write controls may require the owner account."
                    )
                if not dispatch_supported:
                    limitations.append("Monitoring-only: required Anker write entities are unavailable.")
                if connection_type == ANKER_SOLIX_CONNECTION_MODBUS:
                    limitations.append("X1 direct Modbus does not support parallel systems in the current Anker register map.")

                result["battery_config"] = {
                    "connection_type": connection_type,
                    "control_path": anker_data.get("control_path", connection_type),
                    "dispatch_supported": dispatch_supported,
                    "supports_force_charge": dispatch_supported,
                    "supports_force_discharge": dispatch_supported,
                    "supports_restore_normal": dispatch_supported,
                    "supports_backup_reserve": False,
                    "limitations": limitations,
                }

            _LOGGER.info(f"✅ Provider config response: provider={electricity_provider}")
            return web.json_response(result)

        except Exception as e:
            _LOGGER.error(f"Error fetching provider config: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500
            )

    async def post(self, request: web.Request) -> web.Response:
        """Handle POST request to update provider configuration."""
        _LOGGER.info("⚡ Provider config HTTP POST request")

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

        if entry.options.get(
            CONF_ELECTRICITY_PROVIDER,
            entry.data.get(CONF_ELECTRICITY_PROVIDER),
        ) == "covau":
            return web.json_response(
                {
                    "success": False,
                    "error": "CovaU plan snapshots and settlement meters are read-only here; use Home Assistant options to change them",
                },
                status=405,
            )

        try:
            data = await request.json()
            _LOGGER.info(f"Provider config update request: {data}")

            # Map incoming config keys to config entry option keys
            key_mapping = {
                # Common
                "auto_sync": CONF_AUTO_SYNC_ENABLED,
                "monitoring_mode": CONF_MONITORING_MODE,
                # Amber
                "forecast_type": CONF_AMBER_FORECAST_TYPE,
                "spike_protection_enabled": CONF_SPIKE_PROTECTION_ENABLED,
                "forecast_discrepancy_alert": CONF_FORECAST_DISCREPANCY_ALERT,
                "forecast_discrepancy_threshold": CONF_FORECAST_DISCREPANCY_THRESHOLD,
                "price_spike_alert": CONF_PRICE_SPIKE_ALERT,
                "price_spike_import_threshold": CONF_PRICE_SPIKE_IMPORT_THRESHOLD,
                "price_spike_export_threshold": CONF_PRICE_SPIKE_EXPORT_THRESHOLD,
                "export_boost_enabled": CONF_EXPORT_BOOST_ENABLED,
                "export_price_offset": CONF_EXPORT_PRICE_OFFSET,
                "export_min_price": CONF_EXPORT_MIN_PRICE,
                "export_boost_start": CONF_EXPORT_BOOST_START,
                "export_boost_end": CONF_EXPORT_BOOST_END,
                "export_boost_threshold": CONF_EXPORT_BOOST_THRESHOLD,
                "chip_mode_enabled": CONF_CHIP_MODE_ENABLED,
                "chip_mode_start": CONF_CHIP_MODE_START,
                "chip_mode_end": CONF_CHIP_MODE_END,
                "chip_mode_threshold": CONF_CHIP_MODE_THRESHOLD,
                # Flow Power
                "state": CONF_FLOW_POWER_STATE,
                "price_source": CONF_FLOW_POWER_PRICE_SOURCE,
                "network_distributor": CONF_NETWORK_DISTRIBUTOR,
                "network_tariff_code": CONF_NETWORK_TARIFF_CODE,
                "network_use_manual_rates": CONF_NETWORK_USE_MANUAL_RATES,
                "network_tariff_type": CONF_NETWORK_TARIFF_TYPE,
                "network_flat_rate": CONF_NETWORK_FLAT_RATE,
                "network_peak_rate": CONF_NETWORK_PEAK_RATE,
                "network_shoulder_rate": CONF_NETWORK_SHOULDER_RATE,
                "network_offpeak_rate": CONF_NETWORK_OFFPEAK_RATE,
                "network_peak_start": CONF_NETWORK_PEAK_START,
                "network_peak_end": CONF_NETWORK_PEAK_END,
                "network_offpeak_start": CONF_NETWORK_OFFPEAK_START,
                "network_offpeak_end": CONF_NETWORK_OFFPEAK_END,
                "network_other_fees": CONF_NETWORK_OTHER_FEES,
                "network_include_gst": CONF_NETWORK_INCLUDE_GST,
                "pea_enabled": CONF_PEA_ENABLED,
                "flow_power_base_rate": CONF_FLOW_POWER_BASE_RATE,
                "flow_power_export_rate": CONF_FLOW_POWER_EXPORT_RATE,
                "pea_custom_value": CONF_PEA_CUSTOM_VALUE,
                "demand_charge_enabled": CONF_DEMAND_CHARGE_ENABLED,
                "demand_charge_rate": CONF_DEMAND_CHARGE_RATE,
                "demand_charge_start_time": CONF_DEMAND_CHARGE_START_TIME,
                "demand_charge_end_time": CONF_DEMAND_CHARGE_END_TIME,
                "demand_charge_days": CONF_DEMAND_CHARGE_DAYS,
                "demand_charge_billing_day": CONF_DEMAND_CHARGE_BILLING_DAY,
                # Globird / AEMO VPP
                "aemo_region": CONF_AEMO_REGION,
                "aemo_spike_threshold": CONF_AEMO_SPIKE_THRESHOLD,
                "aemo_spike_enabled": CONF_AEMO_SPIKE_ENABLED,
                "globird_plan": CONF_GLOBIRD_PLAN,
                "globird_zerohero_start": CONF_GLOBIRD_ZEROHERO_START,
                "globird_zerohero_end": CONF_GLOBIRD_ZEROHERO_END,
                "globird_zerohero_export_cap_kwh": CONF_GLOBIRD_ZEROHERO_EXPORT_CAP_KWH,
                "globird_zerohero_super_export_rate": CONF_GLOBIRD_ZEROHERO_SUPER_EXPORT_RATE,
                "globird_zerohero_credit_amount": CONF_GLOBIRD_ZEROHERO_CREDIT_AMOUNT,
                "globird_zerohero_import_limit_kw": CONF_GLOBIRD_ZEROHERO_IMPORT_LIMIT_KW,
                "globird_zerocharge_start": CONF_GLOBIRD_ZEROCHARGE_START,
                "globird_zerocharge_end": CONF_GLOBIRD_ZEROCHARGE_END,
                "globird_zerocharge_import_cap_kwh": CONF_GLOBIRD_ZEROCHARGE_IMPORT_CAP_KWH,
            }

            # Build new options dict starting with existing options
            new_options = dict(entry.options)

            # Update only the keys that were provided
            for key, value in data.items():
                if key in key_mapping:
                    new_options[key_mapping[key]] = value

            if (
                data.get("globird_plan")
                and data.get("globird_plan") != GLOBIRD_PLAN_ZEROHERO_CUSTOM
            ):
                for key in (
                    CONF_GLOBIRD_ZEROHERO_START,
                    CONF_GLOBIRD_ZEROHERO_END,
                    CONF_GLOBIRD_ZEROHERO_EXPORT_CAP_KWH,
                    CONF_GLOBIRD_ZEROHERO_SUPER_EXPORT_RATE,
                    CONF_GLOBIRD_ZEROHERO_CREDIT_AMOUNT,
                    CONF_GLOBIRD_ZEROHERO_IMPORT_LIMIT_KW,
                    CONF_GLOBIRD_ZEROCHARGE_START,
                    CONF_GLOBIRD_ZEROCHARGE_END,
                    CONF_GLOBIRD_ZEROCHARGE_IMPORT_CAP_KWH,
                ):
                    new_options.pop(key, None)

            # Update the config entry without triggering a full reload.
            # Set a flag so the update listener knows to skip the reload —
            # API-driven saves don't need a full integration restart.
            domain_data = self._hass.data.get(DOMAIN, {})
            entry_data = domain_data.get(entry.entry_id, {})
            if new_options != dict(entry.options):
                entry_data["_skip_reload"] = True
            self._hass.config_entries.async_update_entry(entry, options=new_options)
            if "monitoring_mode" in data:
                async_dispatcher_send(
                    self._hass,
                    f"{DOMAIN}_{entry.entry_id}_monitoring_mode",
                    bool(new_options.get(CONF_MONITORING_MODE, False)),
                )
                if bool(new_options.get(CONF_MONITORING_MODE, False)):
                    restore_data = {"source": "manual", "_force_restore": True}
                    try:
                        await self._hass.services.async_call(
                            DOMAIN,
                            SERVICE_RESTORE_NORMAL,
                            restore_data,
                            blocking=True,
                        )
                        # restore_normal releases force modes/native control,
                        # but never restores an IDLE/EV-elevated backup
                        # reserve — the restore-side monitoring gate would
                        # then block the optimizer's own retries, stranding
                        # it (OB-8). This is the one sanctioned bypass caller
                        # (see coordinator._restore_pre_idle_backup_reserve).
                        opt_coord = entry_data.get("optimization_coordinator")
                        if (
                            opt_coord
                            and getattr(opt_coord, "_pre_idle_backup_reserve", None)
                            is not None
                            and getattr(opt_coord, "battery_controller", None)
                        ):
                            await opt_coord._restore_pre_idle_backup_reserve(
                                opt_coord.battery_controller,
                                "monitoring enabled",
                                bypass_monitoring=True,
                            )
                    except Exception as err:
                        _LOGGER.warning(
                            "Monitoring mode enabled but restore normal failed: %s",
                            err,
                        )

            _LOGGER.info("✅ Provider config updated successfully")
            return web.json_response({"success": True})

        except Exception as e:
            _LOGGER.error(f"Error updating provider config: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500
            )

class TariffPriceView(HomeAssistantView):
    """HTTP view to get current electricity prices from Tesla tariff schedule.

    This endpoint is designed for Globird users who don't have an API like Amber.
    It calculates the current import/export prices based on the Tesla tariff
    that was manually configured in the Tesla app.
    """

    url = "/api/power_sync/tariff_price"
    name = "api:power_sync:tariff_price"
    requires_auth = True

    def __init__(self, hass: HomeAssistant):
        """Initialize the view."""
        self._hass = hass

    async def get(self, request: web.Request) -> web.Response:
        """Handle GET request for current electricity prices."""
        _LOGGER.info("💰 Tariff price HTTP request")

        # Find the power_sync entry
        entry = None
        entry_id = None
        for config_entry in self._hass.config_entries.async_entries(DOMAIN):
            entry = config_entry
            entry_id = config_entry.entry_id
            break

        if not entry:
            return web.json_response(
                {"success": False, "error": "PowerSync not configured"},
                status=503
            )

        try:
            # Get electricity provider to determine price source
            electricity_provider = entry.options.get(
                CONF_ELECTRICITY_PROVIDER,
                entry.data.get(CONF_ELECTRICITY_PROVIDER, "globird")
            )

            if electricity_provider == "flow_power":
                entry_data = self._hass.data.get(DOMAIN, {}).get(entry_id, {})
                tariff_schedule = entry_data.get("tariff_schedule")
                if tariff_schedule:
                    buy_price_cents, sell_price_cents, current_period = (
                        get_current_price_from_tariff_schedule(tariff_schedule)
                    )
                    result = {
                        "success": True,
                        "import": {
                            "perKwh": buy_price_cents,
                            "channelType": "general",
                            "type": "TariffInterval",
                            "duration": 30,
                            "spikeStatus": None,
                            "source": "flow_power_tariff_schedule",
                        },
                        "feedIn": {
                            "perKwh": -sell_price_cents,
                            "channelType": "feedIn",
                            "type": "TariffInterval",
                            "duration": 30,
                            "spikeStatus": None,
                            "source": "flow_power_tariff_schedule",
                        },
                        "provider": electricity_provider,
                        "current_period": current_period,
                        "utility": tariff_schedule.get("utility"),
                        "plan_name": tariff_schedule.get("plan_name"),
                    }
                    _LOGGER.info(
                        "✅ Flow Power tariff price response: period=%s, import=%.1fc, export=%.1fc",
                        current_period,
                        buy_price_cents,
                        sell_price_cents,
                    )
                    return web.json_response(result)

            # Dynamic pricing providers - fetch real-time prices from their API.
            # Flow Power uses the canonical tariff schedule above because raw
            # KWatch/AEMO prices are transformed by the Flow Power PEA formula.
            dynamic_providers = ("amber",)
            if electricity_provider in dynamic_providers:
                entry_data = self._hass.data.get(DOMAIN, {}).get(entry_id, {})

                # Try price coordinator first (most up-to-date)
                price_coordinator = entry_data.get("price_coordinator")
                if price_coordinator and price_coordinator.data:
                    price_data = price_coordinator.data
                    import_price_cents = price_data.get("import_cents", 0)
                    export_price_cents = price_data.get("export_cents", 0)
                    spike_status = price_data.get("spike_status")

                    result = {
                        "success": True,
                        "import": {
                            "perKwh": import_price_cents,
                            "channelType": "general",
                            "type": "CurrentInterval",
                            "duration": 5 if electricity_provider == "amber" else 30,
                            "spikeStatus": spike_status,
                            "source": electricity_provider,
                        },
                        "feedIn": {
                            "perKwh": -export_price_cents,
                            "channelType": "feedIn",
                            "type": "CurrentInterval",
                            "duration": 5 if electricity_provider == "amber" else 30,
                            "spikeStatus": None,
                            "source": electricity_provider,
                        },
                        "provider": electricity_provider,
                    }

                    _LOGGER.info(
                        f"✅ Price response ({electricity_provider}): import={import_price_cents:.1f}c, export={export_price_cents:.1f}c"
                    )
                    return web.json_response(result)

                # Fallback to stored amber_prices
                amber_prices = entry_data.get("amber_prices", {})
                if amber_prices:
                    import_price_cents = amber_prices.get("import_cents", 0)
                    export_price_cents = amber_prices.get("export_cents", 0)

                    result = {
                        "success": True,
                        "import": {
                            "perKwh": import_price_cents,
                            "channelType": "general",
                            "type": "CurrentInterval",
                            "duration": 5,
                            "spikeStatus": None,
                            "source": f"{electricity_provider}_stored",
                        },
                        "feedIn": {
                            "perKwh": -export_price_cents,
                            "channelType": "feedIn",
                            "type": "CurrentInterval",
                            "duration": 5,
                            "spikeStatus": None,
                            "source": f"{electricity_provider}_stored",
                        },
                        "provider": electricity_provider,
                    }

                    _LOGGER.info(
                        f"✅ Price response ({electricity_provider} stored): import={import_price_cents:.1f}c, export={export_price_cents:.1f}c"
                    )
                    return web.json_response(result)

                # No dynamic price data available
                _LOGGER.warning(f"No price data available for {electricity_provider}")
                return web.json_response({
                    "success": False,
                    "error": f"No price data available for {electricity_provider}. Check API connection."
                }, status=404)

            # Static TOU providers (GloBird, etc.) - use Tesla tariff
            # Check if optimizer has uploaded a fake tariff (force charge/discharge active)
            # If so, use the SAVED real tariff instead of fetching the fake one from Tesla
            force_charge_state = self._hass.data.get(DOMAIN, {}).get(entry_id, {}).get("force_charge_state", {})
            force_discharge_state = self._hass.data.get(DOMAIN, {}).get(entry_id, {}).get("force_discharge_state", {})

            saved_tariff = None
            force_mode_active = False
            if force_charge_state.get("active") and force_charge_state.get("saved_tariff"):
                saved_tariff = _select_restorable_tesla_tariff(force_charge_state["saved_tariff"])
                force_mode_active = True
                if saved_tariff:
                    _LOGGER.info("Force charge active - using saved real tariff instead of fake ML tariff")
                else:
                    _LOGGER.warning("Force charge saved tariff is a PowerSync force tariff; ignoring for price response")
            elif force_discharge_state.get("active") and force_discharge_state.get("saved_tariff"):
                saved_tariff = _select_restorable_tesla_tariff(force_discharge_state["saved_tariff"])
                force_mode_active = True
                if saved_tariff:
                    _LOGGER.info("Force discharge active - using saved real tariff instead of fake ML tariff")
                else:
                    _LOGGER.warning("Force discharge saved tariff is a PowerSync force tariff; ignoring for price response")

            if saved_tariff:
                # Use saved tariff to calculate current prices
                tariff_data = self._calculate_prices_from_saved_tariff(saved_tariff)
                if tariff_data:
                    buy_price_cents = tariff_data.get("buy_price", 0)
                    sell_price_cents = tariff_data.get("sell_price", 0)
                    current_period = tariff_data.get("current_period", "UNKNOWN")

                    result = {
                        "success": True,
                        "import": {
                            "perKwh": buy_price_cents,
                            "channelType": "general",
                            "type": "TariffInterval",
                            "duration": 30,
                            "spikeStatus": None,
                            "source": "saved_tariff",
                        },
                        "feedIn": {
                            "perKwh": -sell_price_cents,
                            "channelType": "feedIn",
                            "type": "TariffInterval",
                            "duration": 30,
                            "spikeStatus": None,
                            "source": "saved_tariff",
                        },
                        "current_period": current_period,
                        "utility": tariff_data.get("utility"),
                        "plan_name": tariff_data.get("plan_name"),
                        "force_mode_active": force_mode_active,
                    }

                    _LOGGER.info(
                        f"✅ Tariff price response (from saved): period={current_period}, buy={buy_price_cents:.1f}c, sell={sell_price_cents:.1f}c"
                    )
                    return web.json_response(result)

            # No force mode or no saved tariff - fetch from Tesla API
            _LOGGER.info("Fetching tariff from Tesla API")
            tariff_data = await self._fetch_tesla_tariff(entry)

            if not tariff_data:
                return web.json_response({
                    "success": False,
                    "error": "No tariff schedule available. Configure your rate plan in the Tesla app."
                }, status=404)

            # Get current prices (already in cents from _fetch_tesla_tariff)
            buy_price_cents = tariff_data.get("buy_price", 0)
            sell_price_cents = tariff_data.get("sell_price", 0)
            current_period = tariff_data.get("current_period", "UNKNOWN")

            result = {
                "success": True,
                "import": {
                    "perKwh": buy_price_cents,
                    "channelType": "general",
                    "type": "TariffInterval",
                    "duration": 30,
                    "spikeStatus": None,
                    "source": "tesla_tariff",
                },
                "feedIn": {
                    # Amber format: feedIn is negative when you get paid
                    # We negate to match Amber convention
                    "perKwh": -sell_price_cents,
                    "channelType": "feedIn",
                    "type": "TariffInterval",
                    "duration": 30,
                    "spikeStatus": None,
                    "source": "tesla_tariff",
                },
                "current_period": current_period,
                "utility": tariff_data.get("utility"),
                "plan_name": tariff_data.get("plan_name"),
                "last_sync": tariff_data.get("last_sync"),
            }

            _LOGGER.info(
                f"✅ Tariff price response: period={current_period}, buy={buy_price_cents:.1f}c, sell={sell_price_cents:.1f}c"
            )
            return web.json_response(result)

        except Exception as e:
            _LOGGER.error(f"Error fetching tariff price: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500
            )

    async def _fetch_tesla_tariff(self, entry: ConfigEntry) -> dict | None:
        """Fetch tariff from Tesla site_info API and extract current prices.

        Delegates to the standalone fetch_tesla_tariff_schedule function.
        """
        return await fetch_tesla_tariff_schedule(self._hass, entry)

    def _calculate_prices_from_saved_tariff(self, saved_tariff: dict) -> dict | None:
        """Calculate current prices from a saved tariff structure.

        The saved tariff is the original Tesla tariff_content before force charge/discharge
        uploaded a fake one. Structure:
          - seasons at saved_tariff["seasons"][season_name] with fromMonth/toMonth
          - TOU periods at seasons[season]["tou_periods"][period_name]["periods"] (list)
          - Buy rates at saved_tariff["energy_charges"][season]["rates"][period]
          - Sell rates at saved_tariff["sell_tariff"]["energy_charges"][season]["rates"][period]
        """
        if _is_powersync_force_tariff(saved_tariff):
            _LOGGER.warning(
                "Refusing to calculate static TOU prices from PowerSync force tariff (name: %s)",
                _tariff_display_name(saved_tariff),
            )
            return None

        try:
            from ..tariff_time import find_matching_tou_period

            now = dt_util.now()  # HA tz; container UTC would mis-classify season/period
            current_month = now.month

            utility = saved_tariff.get("utility", "")
            plan_name = saved_tariff.get("name", "")

            # Seasons are at the top level, NOT inside energy_charges
            seasons = saved_tariff.get("seasons", {})

            # Find current season by month
            current_season = None
            for season_name, season_data in seasons.items():
                if not isinstance(season_data, dict) or not season_data:
                    continue
                from_month = season_data.get("fromMonth", 1)
                to_month = season_data.get("toMonth", 12)
                if from_month <= to_month:
                    if from_month <= current_month <= to_month:
                        current_season = season_name
                        break
                else:
                    if current_month >= from_month or current_month <= to_month:
                        current_season = season_name
                        break

            if not current_season:
                # Default to first non-empty season
                for sn, sd in seasons.items():
                    if isinstance(sd, dict) and sd:
                        current_season = sn
                        break

            if not current_season:
                _LOGGER.warning("Saved tariff has no valid seasons")
                return None

            # TOU periods are inside seasons[current_season]["tou_periods"]
            tou_periods = seasons.get(current_season, {}).get("tou_periods", {})

            # Buy rates: energy_charges[current_season]["rates"][period]
            energy_charges = saved_tariff.get("energy_charges", {})
            season_charges = energy_charges.get(current_season, {})
            buy_rates = season_charges.get("rates", season_charges)

            # Sell rates: sell_tariff["energy_charges"][current_season]["rates"][period]
            sell_tariff = saved_tariff.get("sell_tariff", {})
            sell_energy = sell_tariff.get("energy_charges", {})
            sell_season = sell_energy.get(current_season, {})
            sell_rates = sell_season.get("rates", sell_season)
            current_period = find_matching_tou_period(
                tou_periods,
                now,
                default="OFF_PEAK",
                buy_rates=buy_rates if isinstance(buy_rates, dict) else None,
                sell_rates=sell_rates if isinstance(sell_rates, dict) else None,
            )
            buy_rate = 0.0
            if isinstance(buy_rates, dict):
                buy_rate = buy_rates.get(current_period, buy_rates.get("ALL", 0.0))

            sell_rate = 0.0
            if isinstance(sell_rates, dict):
                sell_rate = sell_rates.get(current_period, sell_rates.get("ALL", 0.0))

            # Rates are in $/kWh, convert to cents
            buy_price_cents = round(buy_rate * 100, 2)
            sell_price_cents = round(sell_rate * 100, 2)

            _LOGGER.debug(
                f"Calculated prices from saved tariff: season={current_season}, period={current_period}, "
                f"buy={buy_price_cents:.1f}c, sell={sell_price_cents:.1f}c"
            )

            return {
                "buy_price": buy_price_cents,
                "sell_price": sell_price_cents,
                "current_period": current_period,
                "utility": utility,
                "plan_name": plan_name,
            }

        except Exception as e:
            _LOGGER.error(f"Error calculating prices from saved tariff: {e}", exc_info=True)
            return None

class CustomTariffView(HomeAssistantView):
    """HTTP view to manage custom tariff for non-Amber users.

    This allows Globird/AEMO VPP/Other users to define their TOU tariff structure
    which is then used for EV charging price decisions and Sigenergy Cloud tariff sync.
    """

    url = "/api/power_sync/custom_tariff"
    name = "api:power_sync:custom_tariff"
    requires_auth = True

    def __init__(self, hass: HomeAssistant):
        """Initialize the view."""
        self._hass = hass

    def _get_store(self):
        """Get the automation store from hass.data."""
        entry_data = self._get_entry_data()
        if entry_data:
            return entry_data["automation_store"]
        return None

    def _get_entry_data(self):
        """Get the first PowerSync entry data with an automation store."""
        if DOMAIN not in self._hass.data:
            return None
        # Find any config entry to get the automation store
        for entry_id, entry_data in self._hass.data.get(DOMAIN, {}).items():
            if isinstance(entry_data, dict) and "automation_store" in entry_data:
                return entry_data
        return None

    async def get(self, request: web.Request) -> web.Response:
        """Handle GET request - return current custom tariff."""
        _LOGGER.info("📱 Custom tariff HTTP GET request")

        store = self._get_store()
        if not store:
            return web.json_response(
                {"success": False, "error": "Automation store not initialized"},
                status=503
            )

        try:
            custom_tariff = store.get_custom_tariff()
            return web.json_response({
                "success": True,
                "custom_tariff": custom_tariff
            })
        except Exception as e:
            _LOGGER.error(f"Error fetching custom tariff: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500
            )

    async def post(self, request: web.Request) -> web.Response:
        """Handle POST request - save custom tariff."""
        _LOGGER.info("📱 Custom tariff HTTP POST request")

        store = self._get_store()
        if not store:
            return web.json_response(
                {"success": False, "error": "Automation store not initialized"},
                status=503
            )

        try:
            data = await request.json()
            _LOGGER.debug(f"📱 Saving custom tariff: name={data.get('name')}")

            # Validate required fields
            if not data.get("name"):
                return web.json_response(
                    {"success": False, "error": "Tariff name is required"},
                    status=400
                )

            if not data.get("energy_charges"):
                return web.json_response(
                    {"success": False, "error": "Energy charges are required"},
                    status=400
                )

            entry_data = self._get_entry_data()
            entry = entry_data.get("entry") if entry_data else None
            tariff_currency = normalize_currency(
                data.get("currency"),
                currency_for_entry(entry, self._hass),
            )
            data["currency"] = tariff_currency

            store.set_custom_tariff(data)
            await store.async_save()

            # Also update the tariff_schedule in hass.data for immediate use
            tariff_schedule = convert_custom_tariff_to_schedule(
                data,
                currency=tariff_currency,
            )
            for entry_id, entry_data in self._hass.data.get(DOMAIN, {}).items():
                if isinstance(entry_data, dict) and "automation_store" in entry_data:
                    entry_data["tariff_schedule"] = tariff_schedule
                    _LOGGER.info(f"Updated tariff_schedule in hass.data for entry {entry_id}")
                    break

            return web.json_response({
                "success": True,
                "custom_tariff": store.get_custom_tariff()
            })
        except Exception as e:
            _LOGGER.error(f"Error saving custom tariff: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500
            )

    async def delete(self, request: web.Request) -> web.Response:
        """Handle DELETE request - remove custom tariff."""
        _LOGGER.info("📱 Custom tariff HTTP DELETE request")

        store = self._get_store()
        if not store:
            return web.json_response(
                {"success": False, "error": "Automation store not initialized"},
                status=503
            )

        try:
            deleted = store.delete_custom_tariff()
            await store.async_save()

            # Clear tariff_schedule in hass.data
            for entry_id, entry_data in self._hass.data.get(DOMAIN, {}).items():
                if isinstance(entry_data, dict) and "automation_store" in entry_data:
                    entry_data.pop("tariff_schedule", None)
                    break

            return web.json_response({
                "success": True,
                "deleted": deleted
            })
        except Exception as e:
            _LOGGER.error(f"Error deleting custom tariff: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500
            )

class CustomTariffTemplatesView(HomeAssistantView):
    """HTTP view to get preset tariff templates."""

    url = "/api/power_sync/custom_tariff/templates"
    name = "api:power_sync:custom_tariff_templates"
    requires_auth = True

    def __init__(self, hass: HomeAssistant):
        """Initialize the view."""
        self._hass = hass

    async def get(self, request: web.Request) -> web.Response:
        """Handle GET request - return preset tariff templates."""
        from ..tariff_templates import TARIFF_TEMPLATES

        _LOGGER.info("📱 Custom tariff templates HTTP GET request")

        try:
            return web.json_response({
                "success": True,
                "templates": TARIFF_TEMPLATES
            })
        except Exception as e:
            _LOGGER.error(f"Error fetching tariff templates: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500
            )

