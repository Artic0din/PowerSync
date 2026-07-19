"""HTTP views for PowerSync."""
from __future__ import annotations

import logging
import asyncio
import json
import re
from aiohttp import web
from datetime import (
    datetime,
    timedelta,
)
from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.util import dt as dt_util
from ..const import (
    DOMAIN,
    CONF_ELECTRICITY_PROVIDER,
    CONF_SIGENERGY_MODBUS_HOST,
    CONF_EV_PROVIDER,
    EV_PROVIDER_FLEET_API,
    EV_PROVIDER_TESLA_BLE,
    EV_PROVIDER_TESLEMETRY_BT,
    EV_PROVIDER_BOTH,
    TESLA_BLE_SENSOR_CHARGE_LEVEL,
    TESLA_BLE_SENSOR_CHARGING_STATE,
    TESLA_BLE_SENSOR_CHARGE_LIMIT,
    TESLA_BLE_SENSOR_CHARGE_POWER,
    TESLA_BLE_BINARY_ASLEEP,
    TESLA_BLE_BINARY_CHARGE_FLAP,
    TESLA_BLE_BINARY_STATUS,
    TESLA_BLE_BUTTON_WAKE_UP,
    TESLEMETRY_BT_SWITCH_CHARGE,
    TESLEMETRY_BT_SENSOR_BATTERY_LEVEL,
    TESLA_INTEGRATIONS,
    BYD_INTEGRATION,
    CONF_OCPP_ENABLED,
    CONF_ZAPTEC_CHARGER_ENTITY,
    CONF_ZAPTEC_INSTALLATION_ID,
    CONF_ZAPTEC_STANDALONE_ENABLED,
    CONF_ZAPTEC_USERNAME,
    CONF_ZAPTEC_CHARGER_ID,
    CONF_ZAPTEC_INSTALLATION_ID_CLOUD,
)
from .. import (
    _LOGGER,
    _apply_wall_connector_observation,
    _ble_prefix_for_vehicle,
    _configured_sigenergy_charger_capabilities,
    _configured_sigenergy_charger_state,
    _find_vehicle_status,
    _generic_charger_charging_state,
    _generic_charger_observation_from_config,
    _get_ev_vehicle_status,
    _get_ev_vehicles_status,
    _kw_from_wall_connector_power,
    _read_sigenergy_charger_state_for_entry,
    _resolve_ble_prefix,
    _resolve_ble_prefixes,
    _resolve_teslemetry_bt_prefix,
    _wall_connector_records,
    fetch_tesla_tariff_schedule,
    get_current_price_from_tariff_schedule,
)

class EVStatusView(HomeAssistantView):
    """HTTP view to get EV integration status for mobile app."""

    url = "/api/power_sync/ev/status"
    name = "api:power_sync:ev:status"
    requires_auth = True

    # Use imported TESLA_INTEGRATIONS from const.py

    def __init__(self, hass: HomeAssistant):
        """Initialize the view."""
        self._hass = hass

    def _get_powersync_config(self) -> dict:
        """Get PowerSync config entry options."""
        entries = self._hass.config_entries.async_entries(DOMAIN)
        if entries:
            return dict(entries[0].options)
        return {}

    def _get_tesla_ble_status(self) -> dict:
        """Check if Tesla BLE entities are available."""
        config = self._get_powersync_config()
        ev_provider = config.get(CONF_EV_PROVIDER, EV_PROVIDER_FLEET_API)

        # Only check for BLE if it's configured
        if ev_provider not in (EV_PROVIDER_TESLA_BLE, EV_PROVIDER_BOTH):
            return {"available": False, "configured": False}

        prefixes = _resolve_ble_prefixes(self._hass, config)

        # Check if any BLE status entity exists
        any_available = False
        any_connected = False
        for prefix in prefixes:
            status_entity = TESLA_BLE_BINARY_STATUS.format(prefix=prefix)
            state = self._hass.states.get(status_entity)
            if state is not None:
                any_available = True
                if state.state == "on":
                    any_connected = True

        if any_available:
            return {
                "available": True,
                "configured": True,
                "connected": any_connected,
                "entity_prefix": ", ".join(prefixes),
            }

        return {"available": False, "configured": True, "entity_prefix": ", ".join(prefixes)}

    def _get_teslemetry_bt_status(self) -> dict:
        """Check if Teslemetry Bluetooth entities are available."""
        config = self._get_powersync_config()
        ev_provider = config.get(CONF_EV_PROVIDER, EV_PROVIDER_FLEET_API)

        if ev_provider not in (EV_PROVIDER_TESLEMETRY_BT, EV_PROVIDER_BOTH):
            return {"available": False, "configured": False}

        tbt_prefix = _resolve_teslemetry_bt_prefix(self._hass)
        if not tbt_prefix:
            return {"available": False, "configured": True}

        charge_entity = TESLEMETRY_BT_SWITCH_CHARGE.format(prefix=tbt_prefix)
        state = self._hass.states.get(charge_entity)

        if state is not None:
            return {
                "available": True,
                "configured": True,
                "connected": state.state not in ("unavailable",),
                "entity_prefix": tbt_prefix,
            }

        return {"available": False, "configured": True, "entity_prefix": tbt_prefix}

    def _get_zaptec_status(self) -> dict:
        """Check if Zaptec charger entities are available.

        Supports two modes:
        1. Standalone (direct Zaptec Cloud API) — no HA integration needed
        2. HA integration (custom-components/zaptec) — uses HA entities
        Standalone takes priority if configured.
        """
        # Merge data + options to get all config (charger_id may be in either)
        entries = self._hass.config_entries.async_entries(DOMAIN)
        if entries:
            config = {**entries[0].data, **entries[0].options}
        else:
            config = {}
        configured_entity = config.get(CONF_ZAPTEC_CHARGER_ENTITY, "")
        installation_id = config.get(CONF_ZAPTEC_INSTALLATION_ID, "")
        standalone_enabled = config.get(CONF_ZAPTEC_STANDALONE_ENABLED, False)

        # Standalone mode: fetch live status from Zaptec Cloud API
        if standalone_enabled and config.get(CONF_ZAPTEC_USERNAME):
            charger_id = config.get(CONF_ZAPTEC_CHARGER_ID, "")
            cloud_installation_id = config.get(CONF_ZAPTEC_INSTALLATION_ID_CLOUD, "")

            # Get cached state from hass.data
            cloud_status = None
            for entry in entries:
                entry_data = self._hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
                zaptec_cached_state = entry_data.get("zaptec_cached_state")
                if zaptec_cached_state:
                    cloud_status = zaptec_cached_state
                    break

            return {
                "available": True,
                "configured": True,
                "standalone": True,
                "charger_id": charger_id,
                "installation_id": cloud_installation_id,
                "charger_entity": configured_entity,
                "detected_entities": [],
                "charger_status": None,
                "cloud_status": cloud_status,
            }

        # HA integration mode: check if zaptec integration is loaded
        zaptec_available = "zaptec" in self._hass.config_entries.async_domains()

        if not zaptec_available:
            return {"available": False, "configured": bool(configured_entity)}

        # Auto-detect Zaptec charger entities if not explicitly configured
        detected_entities = []
        try:
            entity_registry = er.async_get(self._hass)
            for entity in entity_registry.entities.values():
                if (entity.platform == "zaptec" and
                        entity.entity_id.startswith("switch.") and
                        "charger" in entity.entity_id.lower()):
                    state = self._hass.states.get(entity.entity_id)
                    detected_entities.append({
                        "entity_id": entity.entity_id,
                        "name": entity.name or entity.original_name or entity.entity_id,
                        "state": state.state if state else "unknown",
                    })
        except Exception as e:
            _LOGGER.debug(f"Error scanning for Zaptec entities: {e}")

        # Auto-detect installation device
        detected_installation_id = installation_id
        if not detected_installation_id:
            try:
                device_registry = dr.async_get(self._hass)
                for device in device_registry.devices.values():
                    for identifier in device.identifiers:
                        if identifier[0] == "zaptec" and "installation" in str(identifier[1]).lower():
                            detected_installation_id = device.id
                            break
                    if detected_installation_id:
                        break
            except Exception as e:
                _LOGGER.debug(f"Error scanning for Zaptec installation: {e}")

        # Get current charger status if configured
        charger_status = None
        active_entity = configured_entity or (detected_entities[0]["entity_id"] if detected_entities else "")
        if active_entity:
            state = self._hass.states.get(active_entity)
            if state:
                charger_status = {
                    "entity_id": active_entity,
                    "state": state.state,
                    "attributes": dict(state.attributes),
                }

        return {
            "available": zaptec_available,
            "configured": bool(configured_entity),
            "standalone": False,
            "charger_entity": configured_entity,
            "installation_id": detected_installation_id,
            "detected_entities": detected_entities,
            "charger_status": charger_status,
        }

    async def get(self, request: web.Request) -> web.Response:
        """Handle GET request for EV status."""
        try:
            # Get PowerSync config for EV provider setting
            config = self._get_powersync_config()
            ev_provider = config.get(CONF_EV_PROVIDER, EV_PROVIDER_FLEET_API)

            # Check for Tesla Fleet/Teslemetry integration
            active_integration = None
            tesla_entries = []
            fleet_api_available = False

            if ev_provider in (EV_PROVIDER_FLEET_API, EV_PROVIDER_BOTH):
                for integration in TESLA_INTEGRATIONS:
                    if integration in self._hass.config_entries.async_domains():
                        entries = self._hass.config_entries.async_entries(integration)
                        if entries:
                            active_integration = integration
                            tesla_entries = entries
                            fleet_api_available = True
                            break

            has_credentials = len(tesla_entries) > 0

            # Check Tesla BLE status
            ble_status = self._get_tesla_ble_status()

            # Check Teslemetry Bluetooth status
            tbt_status = self._get_teslemetry_bt_status()

            # Count vehicles
            vehicle_count = 0

            # Count from Fleet API
            if active_integration and tesla_entries:
                device_registry = dr.async_get(self._hass)
                seen_vins: set[str] = set()

                for device in device_registry.devices.values():
                    for identifier in device.identifiers:
                        if identifier[0] in TESLA_INTEGRATIONS:
                            potential_vin = identifier[1]
                            vin_key = str(potential_vin).strip().lower()
                            if (
                                len(str(potential_vin)) == 17
                                and not str(potential_vin).isdigit()
                                and vin_key not in seen_vins
                            ):
                                seen_vins.add(vin_key)
                                vehicle_count += 1
                            break

            # If BLE/BT is available and no Fleet API vehicles, count as 1 vehicle
            if (ble_status.get("available") or tbt_status.get("available")) and vehicle_count == 0:
                vehicle_count = 1

            # Count BYD vehicles
            byd_count = 0
            byd_available = BYD_INTEGRATION in self._hass.config_entries.async_domains()
            if byd_available:
                device_registry = dr.async_get(self._hass)
                for device in device_registry.devices.values():
                    for identifier in device.identifiers:
                        if identifier[0] == BYD_INTEGRATION:
                            byd_count += 1
                            break

            # Check Zaptec status
            zaptec_status = self._get_zaptec_status()

            # Check generic charger
            from ..const import CONF_GENERIC_CHARGER_ENABLED as _GCE
            generic_charger_enabled = False
            for entry in self._hass.config_entries.async_entries(DOMAIN):
                opts = {**entry.data, **entry.options}
                if opts.get(_GCE):
                    generic_charger_enabled = True
                    break

            # Determine overall configured status
            is_configured = (
                has_credentials
                or ble_status.get("available", False)
                or tbt_status.get("available", False)
                or zaptec_status.get("available", False)
                or byd_available
                or generic_charger_enabled
            )

            generic_count = 1 if generic_charger_enabled else 0

            return web.json_response({
                "success": True,
                "configured": is_configured,
                "linked": is_configured,
                "has_access_token": has_credentials,
                "token_expires_at": None,
                "vehicle_count": vehicle_count + byd_count + generic_count,
                "integration": active_integration,
                "ev_provider": ev_provider,
                "tesla_ble": ble_status,
                "teslemetry_bt": tbt_status,
                "zaptec": zaptec_status,
                "byd": {"available": byd_available, "vehicle_count": byd_count},
                "generic_charger": {"available": generic_charger_enabled, "vehicle_count": generic_count},
            })

        except Exception as e:
            _LOGGER.error(f"Error getting EV status: {e}", exc_info=True)
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

class EVVehiclesView(HomeAssistantView):
    """HTTP view to get Tesla vehicles for mobile app."""

    url = "/api/power_sync/ev/vehicles"
    name = "api:power_sync:ev:vehicles"
    requires_auth = True

    # Use imported TESLA_INTEGRATIONS from const.py

    def __init__(self, hass: HomeAssistant):
        """Initialize the view."""
        self._hass = hass

    def _get_powersync_config(self) -> dict:
        """Get PowerSync config entry options."""
        entries = self._hass.config_entries.async_entries(DOMAIN)
        if entries:
            return dict(entries[0].options)
        return {}

    @staticmethod
    def _supplement_fleet_with_ble(fleet_v: dict, ble_v: dict) -> None:
        """Supplement a Fleet API vehicle dict with fresher BLE data in-place."""
        ble_online = ble_v.get("is_online", False)
        if ble_v.get("battery_level") is not None:
            fleet_v["battery_level"] = ble_v["battery_level"]
        ble_cs = ble_v.get("charging_state")
        if ble_cs and ble_cs not in ("Unknown", "Asleep"):
            fleet_v["charging_state"] = ble_cs
        if ble_v.get("charge_limit_soc") is not None:
            fleet_v["charge_limit_soc"] = ble_v["charge_limit_soc"]
        # Sync is_plugged_in from BLE when it has a definitive
        # reading (charge_flap is "on" or "off", not "unavailable").
        if ble_v.get("plugged_in_definitive"):
            fleet_v["is_plugged_in"] = ble_v["is_plugged_in"]
            if not ble_v["is_plugged_in"]:
                fleet_v["charging_state"] = "Disconnected"
        # Sync charger_power from BLE (more real-time than Fleet API)
        if ble_v.get("charger_power") is not None:
            fleet_v["charger_power"] = ble_v["charger_power"]
        # Use BLE timestamp if it's fresher than Fleet API
        ble_ts = ble_v.get("data_updated_at", "")
        fleet_ts = fleet_v.get("data_updated_at", "")
        if ble_ts > fleet_ts:
            fleet_v["data_updated_at"] = ble_ts
        fleet_v["ble_connected"] = ble_online

    def _get_tesla_ble_vehicle(self, prefix: str, vehicle_index: int = 1) -> dict | None:
        """Get vehicle data from Tesla BLE entities."""
        # Check if BLE status entity exists
        status_entity = TESLA_BLE_BINARY_STATUS.format(prefix=prefix)
        status_state = self._hass.states.get(status_entity)

        if status_state is None:
            _LOGGER.debug(f"EV BLE: Status entity {status_entity} not found")
            return None

        # Get charge level
        battery_level = None
        charge_level_entity = TESLA_BLE_SENSOR_CHARGE_LEVEL.format(prefix=prefix)
        charge_level_state = self._hass.states.get(charge_level_entity)
        if charge_level_state and charge_level_state.state not in ("unknown", "unavailable"):
            try:
                battery_level = int(float(charge_level_state.state))
            except (ValueError, TypeError):
                pass

        # Get charging state
        charging_state = None
        charging_state_entity = TESLA_BLE_SENSOR_CHARGING_STATE.format(prefix=prefix)
        charging_state_state = self._hass.states.get(charging_state_entity)
        if charging_state_state:
            if charging_state_state.state in ("unknown", "unavailable"):
                # Check if car is asleep
                asleep_entity = TESLA_BLE_BINARY_ASLEEP.format(prefix=prefix)
                asleep_state = self._hass.states.get(asleep_entity)
                if asleep_state and asleep_state.state == "on":
                    charging_state = "Asleep"
                else:
                    charging_state = "Unknown"
            else:
                charging_state = charging_state_state.state

        # Get charge limit
        charge_limit = None
        charge_limit_entity = TESLA_BLE_SENSOR_CHARGE_LIMIT.format(prefix=prefix)
        charge_limit_state = self._hass.states.get(charge_limit_entity)
        if charge_limit_state and charge_limit_state.state not in ("unknown", "unavailable"):
            try:
                charge_limit = int(float(charge_limit_state.state))
            except (ValueError, TypeError):
                pass

        # Check if plugged in (charge flap open is a proxy)
        is_plugged_in = False
        plugged_in_definitive = False  # True when charge_flap has a real reading (on/off)
        charge_flap_entity = f"binary_sensor.{prefix}_charge_flap"
        charge_flap_state = self._hass.states.get(charge_flap_entity)
        if charge_flap_state:
            if charge_flap_state.state == "on":
                is_plugged_in = True
                plugged_in_definitive = True
            elif charge_flap_state.state == "off":
                is_plugged_in = False
                plugged_in_definitive = True
            # "unavailable"/"unknown" → not definitive, check cache below

        # Cache definitive BLE readings so they survive BLE disconnects.
        # When BLE loses connection, charge_flap goes "unavailable" — we use
        # the cached value (max 2h) to avoid falling back to stale Fleet API data.
        now_utc = dt_util.utcnow()
        ble_plug_cache_key = f"ev_ble_plug_cache_{prefix}"
        if plugged_in_definitive:
            self._hass.data.setdefault(DOMAIN, {}).setdefault("_ev_cache", {})[ble_plug_cache_key] = {
                "is_plugged_in": is_plugged_in,
                "cached_at": now_utc,
            }
        elif not plugged_in_definitive:
            cached = self._hass.data.get(DOMAIN, {}).get("_ev_cache", {}).get(ble_plug_cache_key)
            if cached and (now_utc - cached["cached_at"]).total_seconds() < 7200:
                is_plugged_in = cached["is_plugged_in"]
                plugged_in_definitive = True  # treat cached as definitive

        # Get charge power (only trust it if vehicle is actively charging)
        charger_power = None
        is_ble_charging = (
            charging_state and charging_state.lower() == "charging"
        )
        if is_ble_charging:
            charge_power_entity = TESLA_BLE_SENSOR_CHARGE_POWER.format(prefix=prefix)
            charge_power_state = self._hass.states.get(charge_power_entity)
            if charge_power_state and charge_power_state.state not in ("unknown", "unavailable"):
                try:
                    charger_power = float(charge_power_state.state)
                except (ValueError, TypeError):
                    pass

        # Check BLE connection status
        is_online = status_state.state == "on"

        # Use the most recent entity update time for data freshness
        data_updated_at = status_state.last_updated if hasattr(status_state, 'last_updated') else None
        for check_state in (charging_state_state, charge_level_state, charge_flap_state):
            if check_state and hasattr(check_state, 'last_updated') and check_state.last_updated:
                if data_updated_at is None or check_state.last_updated > data_updated_at:
                    data_updated_at = check_state.last_updated

        _LOGGER.debug(
            f"EV BLE: Found vehicle via BLE - battery={battery_level}, "
            f"charging={charging_state}, limit={charge_limit}, "
            f"power={charger_power}, online={is_online}, "
            f"plugged_in={is_plugged_in} (definitive={plugged_in_definitive})"
        )

        return {
            "id": f"ble_{prefix}",
            "vehicle_id": f"ble_{prefix}",
            "vin": None,  # BLE doesn't provide VIN
            "display_name": f"Tesla BLE ({prefix})",
            "model": None,
            "battery_level": battery_level,
            "charging_state": charging_state,
            "charge_limit_soc": charge_limit,
            "is_plugged_in": is_plugged_in,
            "plugged_in_definitive": plugged_in_definitive,
            "charger_power": charger_power,
            "is_online": is_online,
            "data_updated_at": data_updated_at.isoformat() if data_updated_at else dt_util.now().isoformat(),
            "source": "tesla_ble",
            "brand": "tesla",
        }

    def _get_byd_vehicles(self, start_id: int = 1) -> list[dict]:
        """Get BYD vehicles from hass-byd-vehicle integration."""
        if BYD_INTEGRATION not in self._hass.config_entries.async_domains():
            return []

        device_registry = dr.async_get(self._hass)
        entity_registry = er.async_get(self._hass)
        vehicles = []
        vehicle_id = start_id

        for device in device_registry.devices.values():
            is_byd = False
            for identifier in device.identifiers:
                if identifier[0] == BYD_INTEGRATION:
                    is_byd = True
                    break
            if not is_byd:
                continue

            battery_level = None
            charging_state = None
            is_plugged_in = False
            is_online = False
            battery_range = None
            time_to_full = None
            provider_battery_capacity_kwh = None

            for entity in entity_registry.entities.values():
                if entity.device_id != device.id:
                    continue

                state = self._hass.states.get(entity.entity_id)
                if not state or state.state in ("unknown", "unavailable"):
                    continue

                eid = entity.entity_id.lower()

                # Provider capacity is accepted only from an explicit capacity
                # entity; range and time-to-full remain telemetry only.
                if eid.startswith("sensor.") and (
                    "battery_capacity" in eid or "usable_capacity" in eid
                ):
                    try:
                        capacity_value = float(state.state)
                        unit = str(
                            state.attributes.get("unit_of_measurement", "kWh")
                        ).lower()
                        if unit == "wh":
                            capacity_value /= 1000
                        if 1 <= capacity_value <= 250:
                            provider_battery_capacity_kwh = capacity_value
                    except (ValueError, TypeError):
                        pass

                # Battery level: sensor.*_battery_level
                if eid.startswith("sensor.") and "battery_level" in eid:
                    try:
                        val = float(state.state)
                        if 0 <= val <= 100:
                            battery_level = int(val)
                    except (ValueError, TypeError):
                        pass

                # Charging: binary_sensor.*_charging (not charger_connected)
                if eid.startswith("binary_sensor.") and "charging" in eid and "charger" not in eid:
                    if state.state == "on":
                        charging_state = "Charging"

                # Charger connected: binary_sensor.*_charger_connected
                if eid.startswith("binary_sensor.") and "charger_connected" in eid:
                    if state.state == "on":
                        is_plugged_in = True

                # Online: binary_sensor.*_online
                if eid.startswith("binary_sensor.") and "online" in eid:
                    is_online = state.state == "on"

                # Range: sensor.*_range
                if eid.startswith("sensor.") and "range" in eid and "battery" not in eid:
                    try:
                        battery_range = float(state.state)
                    except (ValueError, TypeError):
                        pass

                # Time to full: sensor.*_time_to_full
                if eid.startswith("sensor.") and "time_to_full" in eid:
                    try:
                        time_to_full = float(state.state)
                    except (ValueError, TypeError):
                        pass

            # Derive charging state if not already set
            if charging_state is None:
                if is_plugged_in:
                    charging_state = "Stopped"
                else:
                    charging_state = "Disconnected"

            vehicles.append({
                "id": f"byd_{device.id}",
                "vehicle_id": f"byd_{device.id}",
                "vin": None,
                "display_name": device.name or f"BYD Vehicle {vehicle_id}",
                "model": device.model,
                "battery_level": battery_level,
                "battery_range": battery_range,
                "charging_state": charging_state,
                "charge_limit_soc": None,
                "is_plugged_in": is_plugged_in,
                "charger_power": None,
                "time_to_full_charge": time_to_full,
                "provider_battery_capacity_kwh": provider_battery_capacity_kwh,
                "is_online": is_online,
                "data_updated_at": dt_util.now().isoformat(),
                "source": "byd_cloud",
                "brand": "byd",
            })
            vehicle_id += 1

        _LOGGER.debug(f"EV BYD: Found {len(vehicles)} BYD vehicle(s)")
        return vehicles

    async def get(self, request: web.Request) -> web.Response:
        """Handle GET request for vehicle list."""
        try:
            vehicles = []

            # Get PowerSync config for EV provider setting
            config = self._get_powersync_config()
            ev_provider = config.get(CONF_EV_PROVIDER, EV_PROVIDER_FLEET_API)

            # Unified discovery: delegate Fleet API + BLE vehicle discovery to
            # the shared helper in ev_charging_planner so there's only one
            # source of truth for "what Tesla vehicles does this user have".
            # The planner's discovery already handles both paths (device
            # registry scan + BLE prefix fallback) with the right ev_provider
            # gating — we reuse it here instead of duplicating the logic.
            discovered_fleet: list[dict] = []
            ps_entries = self._hass.config_entries.async_entries(DOMAIN)
            ps_entry = ps_entries[0] if ps_entries else None

            if ev_provider in (EV_PROVIDER_FLEET_API, EV_PROVIDER_BOTH) and ps_entry:
                try:
                    from ..automations.ev_charging_planner import discover_all_tesla_vehicles
                    discovered = await discover_all_tesla_vehicles(self._hass, ps_entry)
                    # We only want Fleet API (device registry) results here —
                    # BLE vehicles are handled by the BLE section below so
                    # they receive full state via _get_tesla_ble_vehicle().
                    seen_vins: set[str] = set()
                    for vehicle in discovered:
                        if vehicle.get("source") != "fleet_api":
                            continue
                        vin_key = str(vehicle.get("vin") or "").strip().lower()
                        if vin_key and vin_key in seen_vins:
                            continue
                        if vin_key:
                            seen_vins.add(vin_key)
                        discovered_fleet.append(vehicle)
                except Exception as err:
                    _LOGGER.debug(
                        "EV discovery delegation failed, falling back to empty list: %s",
                        err,
                    )

            # Enrich each discovered Fleet API vehicle with current entity state
            if discovered_fleet:
                entity_registry = er.async_get(self._hass)

                vehicle_id = 0
                for disc in discovered_fleet:
                    device = disc.get("device")
                    vin = disc.get("vin")
                    if device is None or vin is None:
                        continue
                    vehicle_id += 1

                    battery_level = None
                    charging_state = None
                    charge_limit = None
                    is_plugged_in = False
                    charger_power = None
                    provider_battery_capacity_kwh = None
                    latest_entity_update = None

                    device_entities = []
                    sensor_entities = []
                    for entity in entity_registry.entities.values():
                        if entity.device_id != device.id:
                            continue
                        device_entities.append(entity.entity_id)

                        if entity.entity_id.startswith("sensor."):
                            state = self._hass.states.get(entity.entity_id)
                            state_val = state.state if state else "no_state"
                            sensor_entities.append(f"{entity.entity_id}={state_val}")

                        state = self._hass.states.get(entity.entity_id)
                        if not state:
                            continue

                        entity_id_lower = entity.entity_id.lower()

                        if entity.entity_id.startswith("sensor.") and (
                            "battery_capacity" in entity_id_lower
                            or "usable_capacity" in entity_id_lower
                        ) and state.state not in ("unknown", "unavailable"):
                            try:
                                capacity_value = float(state.state)
                                unit = str(
                                    state.attributes.get(
                                        "unit_of_measurement", "kWh"
                                    )
                                ).lower()
                                if unit == "wh":
                                    capacity_value /= 1000
                                if 1 <= capacity_value <= 250:
                                    provider_battery_capacity_kwh = capacity_value
                            except (ValueError, TypeError):
                                pass

                        if ("battery" in entity_id_lower and
                            "sensor." in entity_id_lower and
                            "range" not in entity_id_lower and
                            "heater" not in entity_id_lower):
                            if state.state not in ("unknown", "unavailable"):
                                try:
                                    val = float(state.state)
                                    if 0 <= val <= 100 and battery_level is None:
                                        battery_level = int(val)
                                except (ValueError, TypeError):
                                    pass

                        if (("charging" in entity_id_lower or "charge_state" in entity_id_lower) and
                            "sensor." in entity_id_lower and
                            "limit" not in entity_id_lower and
                            "rate" not in entity_id_lower and
                            "power" not in entity_id_lower):
                            if state.state in ("unknown", "unavailable") and charging_state is None:
                                charging_state = "Asleep"
                            elif state.state not in ("unknown", "unavailable") and charging_state is None:
                                # Capitalize first letter to match app's expected format
                                # Tesla Fleet: charging, complete, stopped, etc.
                                # App expects: Charging, Complete, Stopped, etc.
                                charging_state = state.state.capitalize()

                        if "charge_limit" in entity_id_lower or "charge_limit_soc" in entity_id_lower:
                            if state.state not in ("unknown", "unavailable"):
                                try:
                                    charge_limit = int(float(state.state))
                                except (ValueError, TypeError):
                                    pass

                        if ("plugged" in entity_id_lower or
                            "cable" in entity_id_lower or
                            "charger_connected" in entity_id_lower):
                            if state.state in ("on", "true", "connected"):
                                is_plugged_in = True

                        if ("charger_power" in entity_id_lower or
                            "charge_rate" in entity_id_lower or
                            "charging_power" in entity_id_lower):
                            if state.state not in ("unknown", "unavailable"):
                                try:
                                    charger_power = float(state.state)
                                except (ValueError, TypeError):
                                    pass

                        # Track most recent entity update for data freshness
                        if hasattr(state, 'last_updated') and state.last_updated:
                            if latest_entity_update is None or state.last_updated > latest_entity_update:
                                latest_entity_update = state.last_updated

                    _LOGGER.debug(f"EV: Device {device.name} has {len(device_entities)} entities")

                    # Don't report stale charger_power when not actively charging
                    if charging_state and charging_state.lower() != "charging":
                        charger_power = None

                    fleet_updated_at = latest_entity_update.isoformat() if latest_entity_update else dt_util.now().isoformat()
                    vehicles.append({
                        "id": vin or str(device.id),
                        "vehicle_id": vin or str(device.id),
                        "vin": vin,
                        "display_name": device.name or f"Tesla {vehicle_id}",
                        "model": device.model,
                        "battery_level": battery_level,
                        "charging_state": charging_state,
                        "charge_limit_soc": charge_limit,
                        "is_plugged_in": is_plugged_in,
                        "charger_power": charger_power,
                        "provider_battery_capacity_kwh": provider_battery_capacity_kwh,
                        "is_online": True,
                        "data_updated_at": fleet_updated_at,
                        "source": "fleet_api",
                        "brand": "tesla",
                    })

            # Get/supplement with Tesla BLE data (supports multiple BLE prefixes)
            if ev_provider in (EV_PROVIDER_TESLA_BLE, EV_PROVIDER_BOTH):
                ble_prefixes = _resolve_ble_prefixes(self._hass, config)

                if ev_provider == EV_PROVIDER_BOTH and vehicles:
                    # Supplement fleet vehicles with BLE data positionally
                    for i, prefix in enumerate(ble_prefixes):
                        ble_vehicle = self._get_tesla_ble_vehicle(prefix, vehicle_index=len(vehicles) + i + 1)
                        if not ble_vehicle:
                            continue
                        if i < len(vehicles):
                            # Supplement existing fleet vehicle with BLE data
                            self._supplement_fleet_with_ble(vehicles[i], ble_vehicle)
                        else:
                            # Extra BLE vehicle beyond fleet count — add standalone
                            vehicles.append(ble_vehicle)
                else:
                    # BLE-only mode: each prefix is a separate vehicle
                    for i, prefix in enumerate(ble_prefixes):
                        ble_vehicle = self._get_tesla_ble_vehicle(prefix, vehicle_index=len(vehicles) + i + 1)
                        if ble_vehicle:
                            vehicles.append(ble_vehicle)

            # Discover BYD vehicles from hass-byd-vehicle integration
            byd_vehicles = self._get_byd_vehicles(start_id=len(vehicles) + 1)
            vehicles.extend(byd_vehicles)

            # Supplement BYD vehicles with Zaptec charger state
            # BYD's charger_connected sensor doesn't reflect Zaptec cable state
            if byd_vehicles:
                entries = self._hass.config_entries.async_entries(DOMAIN)
                for entry in entries:
                    zaptec_state = self._hass.data.get(DOMAIN, {}).get(
                        entry.entry_id, {}
                    ).get("zaptec_cached_state")
                    if not zaptec_state:
                        continue
                    mode = zaptec_state.get("charger_operation_mode", "")
                    cable_locked = zaptec_state.get("cable_locked", False)
                    if cable_locked or mode in ("charging", "connected_waiting"):
                        for v in vehicles:
                            if v.get("brand") != "byd":
                                continue
                            if not v["is_plugged_in"]:
                                v["is_plugged_in"] = True
                                _LOGGER.debug(
                                    "EV BYD: Supplemented %s plug status from "
                                    "Zaptec (mode=%s, cable_locked=%s)",
                                    v.get("display_name"), mode, cable_locked,
                                )
                            if mode == "charging" and v["charging_state"] != "Charging":
                                v["charging_state"] = "Charging"
                            elif mode == "connected_waiting" and v["charging_state"] == "Disconnected":
                                v["charging_state"] = "Stopped"
                    break  # Only one PowerSync entry with Zaptec

            # Discover generic charger vehicle (SoC/status/power from configured HA entities).
            entries = self._hass.config_entries.async_entries(DOMAIN)
            for entry in entries:
                opts = {**entry.data, **entry.options}
                observation = _generic_charger_observation_from_config(self._hass, opts)
                if not observation:
                    continue

                power_kw = float(observation.get("ev_power_kw") or 0.0)
                battery_level = observation.get("ev_soc")
                charging_state = _generic_charger_charging_state(observation)

                vehicles.append({
                    "id": "generic_ev",
                    "vehicle_id": "generic_ev",
                    "vin": None,
                    "display_name": "EV",
                    "model": None,
                    "battery_level": battery_level,
                    "charging_state": charging_state,
                    "charge_limit_soc": None,
                    "is_plugged_in": bool(observation.get("is_connected")),
                    "charger_power": power_kw,
                    "ev_power_kw": power_kw,
                    "ev_soc": battery_level,
                    "is_connected": bool(observation.get("is_connected")),
                    "is_charging": bool(observation.get("is_charging")),
                    "is_online": True,
                    "data_updated_at": dt_util.now().isoformat(),
                    "source": "generic_charger",
                    "brand": "generic",
                })
                _LOGGER.debug(
                    "EV Generic: soc=%s, power=%.2fkW, connected=%s, charging=%s, state=%s",
                    battery_level,
                    power_kw,
                    bool(observation.get("is_connected")),
                    bool(observation.get("is_charging")),
                    charging_state,
                )
                break  # Only one generic charger entry

            for entry in entries:
                configured_state = _configured_sigenergy_charger_state(entry)
                if not configured_state:
                    continue

                from ..sigenergy_charger import sigenergy_charger_state_to_vehicle

                state = await _read_sigenergy_charger_state_for_entry(entry, hass)
                vehicles.append(
                    sigenergy_charger_state_to_vehicle(
                        state or configured_state,
                        updated_at=dt_util.now().isoformat(),
                        online=state is not None,
                        capabilities=_configured_sigenergy_charger_capabilities(
                            entry,
                            hass,
                        ),
                    )
                )
                _LOGGER.debug(
                    "EV Sigenergy charger: type=%s, online=%s, state=%s",
                    (state or configured_state).charger_type,
                    state is not None,
                    (state or configured_state).status,
                )
                break  # Only one Sigenergy charger entry

            if not vehicles:
                message = "No vehicles found"
                if ev_provider == EV_PROVIDER_FLEET_API:
                    message = "No Tesla integration installed (tesla_fleet or teslemetry)"
                elif ev_provider == EV_PROVIDER_TESLA_BLE:
                    ble_prefixes = _resolve_ble_prefixes(self._hass, config)
                    message = f"Tesla BLE entities not found (prefixes: {', '.join(ble_prefixes)})"
                return web.json_response({
                    "success": True,
                    "vehicles": [],
                    "message": message
                })

            # Attach one shared capacity contract to every provider vehicle.
            from ..automations.ev_vehicle_capacity import (
                resolve_ev_battery_capacity,
                vehicle_ids_match,
            )
            from ..const import CONF_GENERIC_CHARGER_BATTERY_CAPACITY_KWH

            vehicle_configs = []
            for entry_data in self._hass.data.get(DOMAIN, {}).values():
                if isinstance(entry_data, dict) and entry_data.get("automation_store"):
                    stored = getattr(entry_data["automation_store"], "_data", {}) or {}
                    vehicle_configs = stored.get("vehicle_charging_configs", [])
                    break
            fallback_capacity = config.get(CONF_GENERIC_CHARGER_BATTERY_CAPACITY_KWH)
            for vehicle in vehicles:
                stable_id = vehicle.get("vehicle_id") or vehicle.get("vin")
                stored_config = next(
                    (
                        item for item in vehicle_configs
                        if vehicle_ids_match(item.get("vehicle_id"), stable_id)
                    ),
                    {},
                )
                provider_capacity = vehicle.get("provider_battery_capacity_kwh")
                if provider_capacity is None:
                    provider_capacity = vehicle.get("battery_capacity_kwh")
                anonymous = vehicle.get("brand") == "generic" or str(
                    stable_id or ""
                ).lower().startswith("ocpp_")
                capacity = resolve_ev_battery_capacity(
                    manual_capacity_kwh=(
                        None if anonymous else stored_config.get("battery_capacity_kwh")
                    ),
                    charger_fallback_capacity_kwh=(
                        stored_config.get("charger_fallback_battery_capacity_kwh")
                        or (
                            stored_config.get("battery_capacity_kwh")
                            if anonymous else None
                        )
                        or fallback_capacity
                    ),
                    provider_capacity_kwh=provider_capacity,
                    model=vehicle.get("model"),
                    trim=vehicle.get("trim"),
                    anonymous_loadpoint=anonymous,
                )
                vehicle.update(capacity.to_dict())

            return web.json_response({
                "success": True,
                "vehicles": vehicles,
            })

        except Exception as e:
            _LOGGER.error(f"Error getting vehicles: {e}", exc_info=True)
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

class EVVehiclesSyncView(HomeAssistantView):
    """HTTP view to sync/refresh Tesla vehicles."""

    url = "/api/power_sync/ev/vehicles/sync"
    name = "api:power_sync:ev:vehicles:sync"
    requires_auth = True

    def __init__(self, hass: HomeAssistant):
        """Initialize the view."""
        self._hass = hass

    def _get_store(self):
        """Get the automation store from hass.data for config persistence."""
        if DOMAIN not in self._hass.data:
            return None
        for entry_id, entry_data in self._hass.data.get(DOMAIN, {}).items():
            if isinstance(entry_data, dict) and "automation_store" in entry_data:
                return entry_data["automation_store"]
        return None

    async def post(self, request: web.Request) -> web.Response:
        """Handle POST request to sync vehicles."""
        try:
            # Trigger a refresh of tesla_fleet integration
            tesla_entries = self._hass.config_entries.async_entries("tesla_fleet")
            for entry in tesla_entries:
                await self._hass.config_entries.async_reload(entry.entry_id)

            # Get updated vehicle list
            vehicles_view = EVVehiclesView(self._hass)
            response = await vehicles_view.get(request)

            # Parse response to get vehicles
            response_data = json.loads(response.body)
            vehicles = response_data.get("vehicles", [])

            # Auto-create vehicle configs for new vehicles
            if vehicles:
                store = self._get_store()
                if store:
                    stored_data = getattr(store, '_data', {}) or {}
                    vehicle_configs = stored_data.get("vehicle_charging_configs", [])
                    from ..automations.ev_vehicle_capacity import vehicle_ids_match

                    configs_added = 0
                    configs_updated = 0
                    for i, vehicle in enumerate(vehicles):
                        vehicle_id = vehicle.get("vehicle_id") or vehicle.get("vin")
                        existing = next(
                            (
                                config for config in vehicle_configs
                                if vehicle_ids_match(config.get("vehicle_id"), vehicle_id)
                            ),
                            None,
                        )
                        if vehicle_id and existing is None:
                            # Create default config for new vehicle
                            new_config = {
                                "vehicle_id": vehicle_id,
                                "display_name": vehicle.get("display_name", f"Vehicle {i + 1}"),
                                "priority": i + 1,  # First vehicle = 1 (primary), second = 2, etc.
                                "solar_charging_enabled": True,
                                "vehicle_model": vehicle.get("model"),
                                "vehicle_trim": vehicle.get("trim"),
                                "provider_battery_capacity_kwh": vehicle.get(
                                    "provider_battery_capacity_kwh"
                                ),
                            }
                            vehicle_configs.append(new_config)
                            configs_added += 1
                            _LOGGER.info(f"Auto-created vehicle config for {vehicle_id}")
                        elif existing is not None:
                            before = dict(existing)
                            if vehicle.get("model"):
                                existing["vehicle_model"] = vehicle["model"]
                            if vehicle.get("trim"):
                                existing["vehicle_trim"] = vehicle["trim"]
                            if vehicle.get("provider_battery_capacity_kwh") is not None:
                                existing["provider_battery_capacity_kwh"] = vehicle[
                                    "provider_battery_capacity_kwh"
                                ]
                            if existing != before:
                                configs_updated += 1

                    if configs_added > 0 or configs_updated > 0:
                        stored_data["vehicle_charging_configs"] = vehicle_configs
                        store._data = stored_data
                        await store.async_save()
                        _LOGGER.info(
                            "Saved EV config sync: %d added, %d updated",
                            configs_added,
                            configs_updated,
                        )

            # Return response with sync count
            response_data["synced"] = len(vehicles)
            return web.json_response(response_data)

        except Exception as e:
            _LOGGER.error(f"Error syncing vehicles: {e}", exc_info=True)
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

class EVVehicleCommandView(HomeAssistantView):
    """HTTP view to send commands to Tesla vehicles."""

    url = "/api/power_sync/ev/vehicles/{vehicle_id}/command"
    name = "api:power_sync:ev:vehicles:command"
    requires_auth = True

    # Use imported TESLA_INTEGRATIONS from const.py

    def __init__(self, hass: HomeAssistant):
        """Initialize the view."""
        self._hass = hass

    def _get_powersync_config(self) -> dict:
        """Get PowerSync config entry options."""
        entries = self._hass.config_entries.async_entries(DOMAIN)
        if entries:
            return dict(entries[0].options)
        return {}

    def _get_vin_from_vehicle_id(self, vehicle_id: str) -> str | None:
        """Look up VIN (or BLE vehicle_id) from vehicle_id (sequential number).

        The mobile app sends vehicle_id as a sequential number (1, 2, 3...)
        from the vehicles list. We need to map this back to the actual VIN
        or BLE vehicle_id (ble_{prefix}).

        Also accepts BLE identifiers (ble_*) and VINs (17-char) directly,
        returning them as-is for robustness.
        """
        # Accept pseudo-VINs for non-Tesla chargers directly
        if vehicle_id in ("generic_ev", "zaptec_standalone", "sigenergy_charger") or (
            vehicle_id and vehicle_id.startswith("ocpp_")
        ) or (
            vehicle_id and vehicle_id.startswith("byd_")
        ):
            return vehicle_id

        # Accept BLE identifiers directly (e.g. "ble_joanna_model_3_local")
        if vehicle_id and vehicle_id.startswith("ble_"):
            _LOGGER.debug(f"Vehicle ID {vehicle_id} is already a BLE identifier")
            return vehicle_id

        # Accept VINs directly (17-char alphanumeric)
        if vehicle_id and len(vehicle_id) == 17 and vehicle_id.isalnum():
            _LOGGER.debug(f"Vehicle ID {vehicle_id} is already a VIN")
            return vehicle_id

        config = self._get_powersync_config()
        ev_provider = config.get(CONF_EV_PROVIDER, EV_PROVIDER_FLEET_API)
        device_registry = dr.async_get(self._hass)

        # Build list of vehicles in same order as EVVehiclesView.get()
        vehicle_num = 0

        # Fleet API vehicles first
        if ev_provider in (EV_PROVIDER_FLEET_API, EV_PROVIDER_BOTH):
            for device in device_registry.devices.values():
                for identifier in device.identifiers:
                    if len(identifier) < 2:
                        continue
                    domain = identifier[0]
                    identifier_value = str(identifier[1])
                    if domain in TESLA_INTEGRATIONS:
                        if len(identifier_value) == 17 and not identifier_value.isdigit():
                            vehicle_num += 1
                            if str(vehicle_num) == str(vehicle_id):
                                _LOGGER.debug(f"Mapped vehicle_id {vehicle_id} to VIN {identifier_value}")
                                return identifier_value
                            break

        # BLE vehicles follow fleet vehicles in the list
        if ev_provider in (EV_PROVIDER_TESLA_BLE, EV_PROVIDER_BOTH):
            ble_prefixes = _resolve_ble_prefixes(self._hass, config)
            if ev_provider == EV_PROVIDER_BOTH:
                # In "both" mode, BLE vehicles that supplement fleet vehicles
                # share the fleet ID. Only extra BLE vehicles get new IDs.
                fleet_count = vehicle_num
                for i, prefix in enumerate(ble_prefixes):
                    if i >= fleet_count:
                        # This BLE vehicle is standalone (beyond fleet count)
                        vehicle_num += 1
                        if str(vehicle_num) == str(vehicle_id):
                            ble_vid = f"ble_{prefix}"
                            _LOGGER.debug(f"Mapped vehicle_id {vehicle_id} to BLE {ble_vid}")
                            return ble_vid
            else:
                # BLE-only mode: each prefix is a separate vehicle
                for prefix in ble_prefixes:
                    vehicle_num += 1
                    if str(vehicle_num) == str(vehicle_id):
                        ble_vid = f"ble_{prefix}"
                        _LOGGER.debug(f"Mapped vehicle_id {vehicle_id} to BLE {ble_vid}")
                        return ble_vid

        _LOGGER.warning(f"Could not find VIN for vehicle_id {vehicle_id}")
        return None

    async def _get_tesla_ev_entity(self, entity_pattern: str, vehicle_vin: str | None = None) -> str | None:
        """Find a Tesla EV entity by pattern."""
        import re

        entity_registry = er.async_get(self._hass)
        device_registry = dr.async_get(self._hass)

        # Find devices from Tesla integrations
        tesla_devices = []
        for device in device_registry.devices.values():
            for identifier in device.identifiers:
                # Handle identifiers with varying tuple lengths
                if len(identifier) < 2:
                    continue
                domain = identifier[0]
                identifier_value = str(identifier[1])
                if domain in TESLA_INTEGRATIONS:
                    if len(identifier_value) == 17 and not identifier_value.isdigit():
                        _LOGGER.debug(f"Found Tesla device: {device.name} with VIN {identifier_value}, looking for VIN {vehicle_vin}")
                        if vehicle_vin is None or identifier_value == vehicle_vin:
                            tesla_devices.append(device)
                            _LOGGER.debug(f"Added device {device.name} to tesla_devices list")
                            break

        if not tesla_devices:
            _LOGGER.debug(f"No Tesla devices found for VIN {vehicle_vin}")
            return None

        target_device = tesla_devices[0]
        _LOGGER.debug(f"Using target device: {target_device.name} for pattern {entity_pattern}")

        pattern = re.compile(entity_pattern, re.IGNORECASE)
        for entity in entity_registry.entities.values():
            if entity.device_id == target_device.id:
                if pattern.match(entity.entity_id):
                    return entity.entity_id

        return None

    async def _is_vehicle_asleep(self, vehicle_vin: str | None = None) -> bool:
        """Check if vehicle is asleep."""
        # Check binary_sensor.*_asleep (custom integration)
        asleep_entity = await self._get_tesla_ev_entity(r"binary_sensor\..*_asleep$", vehicle_vin)
        if asleep_entity:
            state = self._hass.states.get(asleep_entity)
            if state and state.state == "on":
                _LOGGER.debug(f"Vehicle is asleep (from {asleep_entity})")
                return True

        # Check binary_sensor.*_online (if asleep not available)
        online_entity = await self._get_tesla_ev_entity(r"binary_sensor\..*_online$", vehicle_vin)
        if online_entity:
            state = self._hass.states.get(online_entity)
            if state and state.state == "off":
                _LOGGER.debug(f"Vehicle is offline/asleep (from {online_entity})")
                return True

        return False

    async def _wait_for_vehicle_awake(self, vehicle_vin: str | None = None, timeout: int = 30) -> bool:
        """Wait for vehicle to wake up, polling every 2 seconds."""
        for i in range(timeout // 2):
            if not await self._is_vehicle_asleep(vehicle_vin):
                _LOGGER.info(f"Vehicle is awake after {i * 2} seconds")
                return True
            _LOGGER.debug(f"Waiting for vehicle to wake... ({i * 2}s)")
            await asyncio.sleep(2)

        _LOGGER.warning(f"Vehicle did not wake within {timeout} seconds")
        return False

    async def _wake_vehicle(self, vehicle_vin: str | None = None) -> bool:
        """Wake up a Tesla vehicle and wait for it to be awake."""
        config = self._get_powersync_config()
        ev_provider = config.get(CONF_EV_PROVIDER, EV_PROVIDER_FLEET_API)

        # Teslemetry BT handles wake internally — no explicit wake needed
        if ev_provider == EV_PROVIDER_TESLEMETRY_BT:
            _LOGGER.debug("Teslemetry BT handles wake internally, skipping")
            return True

        # Check if already awake
        if not await self._is_vehicle_asleep(vehicle_vin):
            _LOGGER.debug("Vehicle is already awake")
            return True

        _LOGGER.info("Vehicle is asleep, sending wake command...")
        ble_prefix = _ble_prefix_for_vehicle(self._hass, config, vehicle_vin)

        wake_sent = False

        if ev_provider in (EV_PROVIDER_TESLA_BLE, EV_PROVIDER_BOTH):
            wake_entity = TESLA_BLE_BUTTON_WAKE_UP.format(prefix=ble_prefix)
            if self._hass.states.get(wake_entity):
                try:
                    await self._hass.services.async_call(
                        "button", "press",
                        {"entity_id": wake_entity},
                        blocking=True,
                    )
                    _LOGGER.info(f"Sent wake command via BLE: {wake_entity}")
                    wake_sent = True
                except Exception as e:
                    _LOGGER.warning(f"BLE wake failed: {e}")

        # Try Fleet API
        if not wake_sent and ev_provider in (EV_PROVIDER_FLEET_API, EV_PROVIDER_BOTH):
            wake_entity = await self._get_tesla_ev_entity(r"button\..*wake(_up)?$", vehicle_vin)
            if wake_entity:
                try:
                    await self._hass.services.async_call(
                        "button", "press",
                        {"entity_id": wake_entity},
                        blocking=True,
                    )
                    _LOGGER.info(f"Sent wake command via Fleet API: {wake_entity}")
                    wake_sent = True
                except Exception as e:
                    _LOGGER.error(f"Fleet API wake failed: {e}")
                    return False

        if not wake_sent:
            _LOGGER.warning("No wake entity found")
            return False

        # Wait for vehicle to wake up (up to 30 seconds)
        return await self._wait_for_vehicle_awake(vehicle_vin, timeout=30)

    async def _is_vehicle_at_home(self, vehicle_vin: str | None = None) -> bool:
        """Check if vehicle is at home using binary_sensor or device_tracker."""
        # First try: binary_sensor.*_located_at_home (Teslemetry)
        # This is the most reliable method
        home_entity = await self._get_tesla_ev_entity(r"binary_sensor\..*_located_at_home$", vehicle_vin)
        if home_entity:
            state = self._hass.states.get(home_entity)
            if state and state.state not in ("unavailable", "unknown"):
                is_home = state.state.lower() == "on"
                _LOGGER.debug(f"Vehicle at home from {home_entity}: {state.state} (at_home={is_home})")
                return is_home

        # Second try: device_tracker.*_location
        location_entity = await self._get_tesla_ev_entity(r"device_tracker\..*_location$", vehicle_vin)
        if location_entity:
            state = self._hass.states.get(location_entity)
            if state and state.state not in ("unavailable", "unknown"):
                is_home = state.state.lower() == "home"
                _LOGGER.debug(f"Vehicle location from {location_entity}: {state.state} (at_home={is_home})")
                return is_home

        _LOGGER.warning("Could not determine vehicle location - no location entity found")
        return True  # Default to True to not block commands if we can't check

    async def _is_vehicle_plugged_in(self, vehicle_vin: str | None = None) -> bool:
        """Check if vehicle is plugged in."""
        # Check BLE charge_flap sensor for BLE vehicles
        if vehicle_vin and vehicle_vin.startswith("ble_"):
            ble_prefix = vehicle_vin[4:]
            charge_flap_entity = TESLA_BLE_BINARY_CHARGE_FLAP.format(prefix=ble_prefix)
            state = self._hass.states.get(charge_flap_entity)
            if state and state.state == "on":
                return True
            if state and state.state == "off":
                return False
            # unavailable/unknown — check cache
            ble_plug_cache_key = f"ev_ble_plug_cache_{ble_prefix}"
            cached = self._hass.data.get(DOMAIN, {}).get("_ev_cache", {}).get(ble_plug_cache_key)
            if cached:
                if (dt_util.utcnow() - cached["cached_at"]).total_seconds() < 7200:
                    return cached["is_plugged_in"]
            _LOGGER.debug(f"BLE vehicle {ble_prefix}: cannot determine plug state")
            return True  # Default True to not block commands

        negative_binary_evidence = None

        # Check binary_sensor.*_charger (Tesla Fleet)
        charger_entity = await self._get_tesla_ev_entity(r"binary_sensor\..*_charger$", vehicle_vin)
        if charger_entity:
            state = self._hass.states.get(charger_entity)
            if state:
                is_plugged = state.state.lower() == "on"
                _LOGGER.debug(f"Vehicle plugged in from {charger_entity}: {state.state} (plugged={is_plugged})")
                if is_plugged:
                    return True
                negative_binary_evidence = charger_entity

        # Check binary_sensor.*_charge_cable (Teslemetry)
        cable_entity = await self._get_tesla_ev_entity(r"binary_sensor\..*_charge_cable$", vehicle_vin)
        if cable_entity:
            state = self._hass.states.get(cable_entity)
            if state:
                is_plugged = state.state.lower() == "on"
                _LOGGER.debug(f"Vehicle plugged in from {cable_entity}: {state.state} (plugged={is_plugged})")
                if is_plugged:
                    return True
                negative_binary_evidence = cable_entity

        # Some Tesla integrations expose plugged-in-but-idle via charging state
        # only. Keep this in sync with the loadpoint status card so "Stopped"
        # / "Complete" does not show as plugged in there and then get blocked
        # here.
        charging_state_entity = await self._get_tesla_ev_entity(
            r"sensor\..*(?:_charging_state|_charging)$",
            vehicle_vin,
        )
        if charging_state_entity:
            state = self._hass.states.get(charging_state_entity)
            if state:
                from ..automations.loadpoint_status import charging_state_plugged_status

                plugged = charging_state_plugged_status(state.state)
                if plugged is True:
                    _LOGGER.debug(
                        "Vehicle plugged in from %s charging state: %s",
                        charging_state_entity,
                        state.state,
                    )
                    return True
                if plugged is False:
                    _LOGGER.debug(
                        "Vehicle unplugged from %s charging state: %s",
                        charging_state_entity,
                        state.state,
                    )
                    return False

        # In Fleet+BLE setups, loadpoint status may merge one BLE bridge into the
        # Fleet vehicle. If there is exactly one fresh BLE plug cache saying the
        # car is plugged in, treat it as authoritative over stale Fleet binaries.
        ble_cache = self._hass.data.get(DOMAIN, {}).get("_ev_cache", {})
        fresh_ble_plug_states = [
            cached.get("is_plugged_in")
            for key, cached in ble_cache.items()
            if key.startswith("ev_ble_plug_cache_")
            and cached.get("cached_at")
            and (dt_util.utcnow() - cached["cached_at"]).total_seconds() < 7200
        ]
        if len(fresh_ble_plug_states) == 1 and fresh_ble_plug_states[0] is True:
            _LOGGER.debug(
                "Vehicle plugged in from single fresh BLE plug cache despite %s reporting unplugged",
                negative_binary_evidence or "no Fleet plug binary",
            )
            return True

        if negative_binary_evidence:
            _LOGGER.debug(
                "Vehicle unplugged from %s with no overriding charging-state or BLE evidence",
                negative_binary_evidence,
            )
            return False

        _LOGGER.warning("Could not determine if vehicle is plugged in")
        return False

    async def _get_vehicle_charging_state(self, vehicle_vin: str | None = None) -> str:
        """Get current charging state."""
        # Check BLE charging_state sensor for BLE vehicles
        if vehicle_vin and vehicle_vin.startswith("ble_"):
            ble_prefix = vehicle_vin[4:]
            ble_entity = TESLA_BLE_SENSOR_CHARGING_STATE.format(prefix=ble_prefix)
            state = self._hass.states.get(ble_entity)
            if state and state.state not in ("unavailable", "unknown"):
                return state.state.lower()
            return ""

        # Tesla Fleet uses sensor.*_charging (no _state suffix)
        charging_entity = await self._get_tesla_ev_entity(r"sensor\..*_charging$", vehicle_vin)
        _LOGGER.debug(f"Charging state check for VIN {vehicle_vin}: found entity {charging_entity}")
        if charging_entity:
            state = self._hass.states.get(charging_entity)
            if state and state.state not in ("unavailable", "unknown"):
                _LOGGER.debug(f"Charging state for {charging_entity}: {state.state}")
                return state.state.lower()
        return ""

    def _get_zaptec_standalone(self) -> tuple | None:
        """Get Zaptec standalone client, charger_id, and cached state if configured.

        Returns (client, charger_id, cached_state) or None if not configured.
        """
        entries = self._hass.config_entries.async_entries(DOMAIN)
        for entry in entries:
            opts = {**entry.data, **entry.options}
            if opts.get(CONF_ZAPTEC_STANDALONE_ENABLED) and opts.get(CONF_ZAPTEC_USERNAME):
                entry_data = self._hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
                client = entry_data.get("zaptec_client")
                charger_id = opts.get(CONF_ZAPTEC_CHARGER_ID, "")
                cached_state = entry_data.get("zaptec_cached_state", {})
                if client and charger_id:
                    return client, charger_id, cached_state
        return None

    def _get_powersync_entry(self) -> ConfigEntry | None:
        """Return the integration config entry used for EV action dispatch."""
        entries = self._hass.config_entries.async_entries(DOMAIN)
        return entries[0] if entries else None

    def _get_vehicle_charging_config(self, *vehicle_ids: str | None) -> dict | None:
        """Return an app-managed charger config matching any supplied vehicle id."""
        wanted = {str(vehicle_id) for vehicle_id in vehicle_ids if vehicle_id}
        if not wanted:
            return None

        for entry_data in self._hass.data.get(DOMAIN, {}).values():
            if not isinstance(entry_data, dict):
                continue
            store = entry_data.get("automation_store")
            if not store:
                continue
            stored = getattr(store, "_data", {}) or {}
            for config in stored.get("vehicle_charging_configs", []):
                if str(config.get("vehicle_id")) in wanted:
                    return config
        return None

    def _manual_session_identity(self, vehicle_vin: str | None) -> tuple[str | None, dict]:
        """Return the loadpoint id and charger params for manual command ownership."""
        if vehicle_vin in (None, "zaptec_standalone") and self._get_zaptec_standalone():
            return "zaptec_standalone", {"charger_type": "zaptec"}

        if vehicle_vin and vehicle_vin.startswith("ocpp_"):
            charger_id = self._ocpp_charger_id_from_loadpoint(vehicle_vin)
            return vehicle_vin, {
                "charger_type": "ocpp",
                "ocpp_charger_id": charger_id,
            }

        if vehicle_vin == "generic_ev":
            from ..const import (
                CONF_GENERIC_CHARGER_AMPS_ENTITY,
                CONF_GENERIC_CHARGER_ENABLED,
                CONF_GENERIC_CHARGER_POWER_ENTITY,
                CONF_GENERIC_CHARGER_STATUS_ENTITY,
                CONF_GENERIC_CHARGER_SWITCH_ENTITY,
            )

            for entry in self._hass.config_entries.async_entries(DOMAIN):
                opts = {**entry.data, **entry.options}
                if opts.get(CONF_GENERIC_CHARGER_ENABLED):
                    return "generic_ev", {
                        "charger_type": "generic",
                        "charger_switch_entity": opts.get(CONF_GENERIC_CHARGER_SWITCH_ENTITY, ""),
                        "charger_amps_entity": opts.get(CONF_GENERIC_CHARGER_AMPS_ENTITY, ""),
                        "charger_status_entity": opts.get(CONF_GENERIC_CHARGER_STATUS_ENTITY, ""),
                        "charger_power_entity": opts.get(CONF_GENERIC_CHARGER_POWER_ENTITY, ""),
                    }

        if vehicle_vin in (None, "sigenergy_charger"):
            from ..const import (
                CONF_SIGENERGY_CHARGER_ENABLED,
                CONF_SIGENERGY_CHARGER_HOST,
                CONF_SIGENERGY_CHARGER_PORT,
                CONF_SIGENERGY_CHARGER_SLAVE_ID,
                CONF_SIGENERGY_CHARGER_TYPE,
                CONF_SIGENERGY_MODBUS_HOST,
            )

            for entry in self._hass.config_entries.async_entries(DOMAIN):
                opts = {**entry.data, **entry.options}
                if opts.get(CONF_SIGENERGY_CHARGER_ENABLED):
                    return "sigenergy_charger", {
                        "charger_type": "sigenergy",
                        "sigenergy_charger_host": opts.get(
                            CONF_SIGENERGY_CHARGER_HOST,
                            opts.get(CONF_SIGENERGY_MODBUS_HOST, ""),
                        ),
                        "sigenergy_charger_port": opts.get(CONF_SIGENERGY_CHARGER_PORT),
                        "sigenergy_charger_slave_id": opts.get(CONF_SIGENERGY_CHARGER_SLAVE_ID),
                        "sigenergy_charger_type": opts.get(CONF_SIGENERGY_CHARGER_TYPE),
                    }

        return vehicle_vin, {"charger_type": "tesla"}

    def _ocpp_charger_id_from_loadpoint(self, loadpoint_id: str) -> str:
        """Resolve an OCPP loadpoint id to the raw HA charger prefix."""
        candidates = [loadpoint_id]
        if loadpoint_id.startswith("ocpp_"):
            candidates.append(loadpoint_id[5:])

        for candidate in candidates:
            if self._hass.states.get(f"switch.{candidate}_charge_control"):
                return candidate

        if loadpoint_id != "ocpp_charger" and loadpoint_id.startswith("ocpp_"):
            return loadpoint_id[5:]
        return loadpoint_id

    def _manual_action_params(self, vehicle_vin: str | None) -> dict:
        """Build shared action parameters for mobile/manual EV commands."""
        manual_vehicle_id, manual_params = self._manual_session_identity(vehicle_vin)
        params = dict(manual_params)
        params["vehicle_id"] = manual_vehicle_id

        if params.get("charger_type") == "tesla":
            params["vehicle_vin"] = vehicle_vin
        else:
            params["vehicle_vin"] = None

        stored_config = self._get_vehicle_charging_config(vehicle_vin, manual_vehicle_id)
        if stored_config:
            for key in (
                "charger_type",
                "charger_switch_entity",
                "charger_amps_entity",
                "charger_status_entity",
                "charger_power_entity",
                "ocpp_charger_id",
                "sigenergy_charger_host",
                "sigenergy_charger_port",
                "sigenergy_charger_slave_id",
                "sigenergy_charger_type",
                "pre_charge_wake_entity",
                "pre_charge_wake_duration_seconds",
                "pre_charge_wake_on_service",
                "pre_charge_wake_off_service",
                "pre_charge_wake_on_service_data",
                "pre_charge_wake_off_service_data",
            ):
                if stored_config.get(key) is not None:
                    params[key] = stored_config.get(key)

            if params.get("charger_type") != "tesla":
                params["vehicle_vin"] = None
                params["vehicle_id"] = stored_config.get("vehicle_id") or manual_vehicle_id

        return params

    def _generic_charger_ready_for_start(self, params: dict) -> tuple[bool, str]:
        """Return whether the configured generic charger appears connected."""
        status_entity = params.get("charger_status_entity", "")
        if not status_entity:
            return True, ""

        state = self._hass.states.get(status_entity)
        if not state:
            return True, ""

        if state.state.lower() not in ("available", "disconnected"):
            return True, ""

        car_present_states = {
            "preparing",
            "charging",
            "suspendedev",
            "suspendedevse",
            "suspended_ev",
            "suspended_evse",
            "finishing",
        }
        car_on_connector = any(
            s.state.lower() in car_present_states
            for s in self._hass.states.async_all()
            if s.entity_id.startswith("sensor.")
            and s.entity_id.endswith("_status_connector")
            and s.state not in ("unavailable", "unknown")
        )
        if car_on_connector:
            _LOGGER.debug(
                "Generic Charger: %s = %s but connector shows car present, proceeding",
                status_entity,
                state.state,
            )
            return True, ""

        _LOGGER.warning(
            "Generic Charger: %s = %s, no connector shows car present",
            status_entity,
            state.state,
        )
        return False, "Vehicle is not plugged in"

    async def _execute_manual_ev_action(
        self,
        action_type: str,
        vehicle_vin: str | None,
        params_extra: dict | None,
        reason: str,
    ) -> bool:
        """Execute an EV command through the shared automation action layer."""
        entry = self._get_powersync_entry()
        if not entry:
            return False

        from ..automations.actions import _execute_single_action

        params = self._manual_action_params(vehicle_vin)
        params.update(params_extra or {})
        params["reason"] = reason
        return await _execute_single_action(self._hass, entry, action_type, params)

    def _manual_loadpoint_id(self, vehicle_vin: str | None) -> str:
        """Return the runtime loadpoint id used by manual EV actions."""
        from ..automations.actions import _ev_action_loadpoint_id

        return _ev_action_loadpoint_id(self._manual_action_params(vehicle_vin))

    def _active_non_manual_owner_message(self, vehicle_vin: str | None) -> str | None:
        """Return a blocking ownership message for non-manual active sessions."""
        entry = self._get_powersync_entry()
        if not entry:
            return None

        from ..automations.ev_ownership import get_ev_ownership, owner_family

        loadpoint_id = self._manual_loadpoint_id(vehicle_vin)
        _lease_id, lease = get_ev_ownership(self._hass, entry, loadpoint_id)
        if not lease:
            return None

        owner_mode = str(lease.get("owner_mode") or "dynamic")
        if owner_family(owner_mode) == "manual":
            return None
        return f"{owner_mode} already owns this loadpoint"

    async def _loadpoint_ready_for_manual_start(
        self,
        vehicle_vin: str | None,
        params: dict,
    ) -> tuple[bool, str]:
        """Return whether the selected loadpoint can be manually started."""
        charger_type = params.get("charger_type", "tesla")

        if charger_type == "generic":
            ready, message = self._generic_charger_ready_for_start(params)
            if not ready:
                return False, message
            switch_entity = params.get("charger_switch_entity", "").strip()
            if not switch_entity:
                return False, "Generic Charger: no switch entity configured"
            if "." not in switch_entity:
                return False, f"Generic Charger: switch entity '{switch_entity}' is not a valid entity_id (missing domain, e.g. 'switch.charger_charge')"
            if not self._hass.states.get(switch_entity):
                return False, f"Generic Charger: switch entity '{switch_entity}' not found in Home Assistant"

        if charger_type == "tesla":
            if not await self._is_vehicle_at_home(vehicle_vin):
                msg = "Vehicle is not at home"
                _LOGGER.warning(msg)
                return False, msg

            if not await self._is_vehicle_plugged_in(vehicle_vin):
                msg = "Vehicle is not plugged in"
                _LOGGER.warning(msg)
                return False, msg

        return True, ""

    def _schedule_manual_quick_stop(
        self,
        vehicle_vin: str | None,
        duration_minutes: int | None,
        source_mode: str | None,
    ) -> str | None:
        """Attach quick-control metadata and optional auto-stop timer."""
        entry = self._get_powersync_entry()
        if not entry:
            return None

        from ..automations import actions as ev_actions

        loadpoint_id = self._manual_loadpoint_id(vehicle_vin)
        state = ev_actions._dynamic_ev_state.get(entry.entry_id, {}).get(loadpoint_id)
        if not state:
            return None

        params = state.setdefault("params", {})
        params["quick_control"] = True
        params["source_mode"] = source_mode or "standard"
        if duration_minutes is not None:
            params["duration_minutes"] = duration_minutes

        if cancel_timer := state.get("cancel_timer"):
            cancel_timer()
            state["cancel_timer"] = None

        if duration_minutes is None:
            params.pop("expires_at", None)
            return None

        stops_at = dt_util.utcnow() + timedelta(minutes=duration_minutes)
        params["expires_at"] = stops_at.isoformat()

        async def _stop_manual_quick_charge(_now) -> None:
            entry_state = ev_actions._dynamic_ev_state.get(entry.entry_id, {}).get(loadpoint_id)
            entry_params = (entry_state or {}).get("params") or {}
            if not entry_state or not entry_params.get("quick_control"):
                _LOGGER.info(
                    "Quick EV stop skipped for %s because the session is no longer active",
                    loadpoint_id,
                )
                return

            await self._execute_manual_ev_action(
                "stop_ev_charging",
                vehicle_vin,
                {"source_mode": entry_params.get("source_mode")},
                "Quick EV charge duration elapsed",
            )

        state["cancel_timer"] = async_track_point_in_utc_time(
            self._hass,
            _stop_manual_quick_charge,
            stops_at,
        )
        return params["expires_at"]

    def _schedule_policy_quick_stop(
        self,
        vehicle_vin: str | None,
        duration_minutes: int,
        source_mode: str,
    ) -> str | None:
        """Attach dashboard policy metadata and stop dynamic sessions on expiry."""
        entry = self._get_powersync_entry()
        if not entry:
            return None

        from ..automations import actions as ev_actions
        from ..automations.ev_ownership import get_ev_ownership

        loadpoint_id = self._manual_loadpoint_id(vehicle_vin)
        state = ev_actions._dynamic_ev_state.get(entry.entry_id, {}).get(loadpoint_id)
        if not state:
            return None

        params = state.setdefault("params", {})
        params["quick_control"] = True
        params["source_mode"] = source_mode
        params["duration_minutes"] = duration_minutes

        if quick_stop_timer := state.get("quick_stop_timer"):
            quick_stop_timer()
            state["quick_stop_timer"] = None

        stops_at = dt_util.utcnow() + timedelta(minutes=duration_minutes)
        params["expires_at"] = stops_at.isoformat()

        _lease_id, lease = get_ev_ownership(self._hass, entry, loadpoint_id)
        if lease:
            lease.update({
                "quick_control": True,
                "source_mode": source_mode,
                "duration_minutes": duration_minutes,
                "expires_at": params["expires_at"],
            })
            state["ownership"] = lease

        async def _stop_policy_quick_charge(_now) -> None:
            entry_state = ev_actions._dynamic_ev_state.get(entry.entry_id, {}).get(loadpoint_id)
            entry_params = (entry_state or {}).get("params") or {}
            if not entry_state or not entry_params.get("quick_control"):
                _LOGGER.info(
                    "Dashboard EV policy stop skipped for %s because the session is no longer active",
                    loadpoint_id,
                )
                return

            await self._execute_manual_ev_action(
                "stop_ev_charging_dynamic",
                vehicle_vin,
                {
                    "vehicle_id": loadpoint_id,
                    "stop_charging": True,
                    "manual_stop": True,
                    "stop_reason": "Quick EV charge duration elapsed",
                },
                "Quick EV charge duration elapsed",
            )

        state["quick_stop_timer"] = async_track_point_in_utc_time(
            self._hass,
            _stop_policy_quick_charge,
            stops_at,
        )
        return params["expires_at"]

    async def _start_policy_charging(
        self,
        policy: str | None,
        vehicle_vin: str | None,
        duration_minutes: int | None,
    ) -> tuple[bool, str]:
        """Start dashboard EV charging through a source policy."""
        from ..ev_policy import build_ev_policy_action

        action = build_ev_policy_action(policy, duration_minutes)
        duration = action.params["duration_minutes"]

        if action.action_type == "start_ev_charging":
            return await self._start_charging(
                vehicle_vin,
                duration,
                action.params.get("source_mode"),
            )

        owner_message = self._active_non_manual_owner_message(vehicle_vin)
        if owner_message:
            return False, owner_message

        params = self._manual_action_params(vehicle_vin)
        ready, message = await self._loadpoint_ready_for_manual_start(vehicle_vin, params)
        if not ready:
            return False, message

        success = await self._execute_manual_ev_action(
            action.action_type,
            vehicle_vin,
            action.params,
            "Manual EV policy start from HA dashboard",
        )
        if success:
            expires_at = self._schedule_policy_quick_stop(
                vehicle_vin,
                duration,
                action.params.get("source_mode") or str(policy),
            )
            if expires_at:
                _LOGGER.info(
                    "Dashboard EV policy charge for %s expires at %s",
                    self._manual_loadpoint_id(vehicle_vin),
                    expires_at,
                )
            return True, f"{action.label} for {duration} minutes"

        return False, f"Failed to start {policy} charging"

    async def _start_charging(
        self,
        vehicle_vin: str | None = None,
        duration_minutes: int | None = None,
        source_mode: str | None = None,
    ) -> tuple[bool, str]:
        """Start charging. Returns (success, message)."""
        params = self._manual_action_params(vehicle_vin)
        charger_type = params.get("charger_type", "tesla")
        owner_message = self._active_non_manual_owner_message(vehicle_vin)
        if owner_message:
            return False, owner_message

        ready, message = await self._loadpoint_ready_for_manual_start(vehicle_vin, params)
        if not ready:
            return False, message

        success = await self._execute_manual_ev_action(
            "start_ev_charging",
            vehicle_vin,
            {
                "duration_minutes": duration_minutes,
                "source_mode": source_mode or "standard",
                "quick_control": True,
            },
            "Manual start from mobile",
        )
        if success:
            expires_at = self._schedule_manual_quick_stop(
                vehicle_vin,
                duration_minutes,
                source_mode,
            )
            duration_text = f" for {duration_minutes} minutes" if duration_minutes else ""
            if expires_at:
                _LOGGER.info("Quick EV charge for %s expires at %s", self._manual_loadpoint_id(vehicle_vin), expires_at)
            if charger_type == "zaptec":
                return True, f"Charging started via Zaptec{duration_text}"
            if charger_type == "sigenergy":
                return True, f"Charging started via Sigenergy charger{duration_text}"
            return True, f"Charging started{duration_text}"

        return False, "Failed to start charging"

    async def _stop_charging(self, vehicle_vin: str | None = None) -> tuple[bool, str]:
        """Stop charging. Returns (success, message)."""
        charger_type = self._manual_action_params(vehicle_vin).get("charger_type", "tesla")
        success = await self._execute_manual_ev_action(
            "stop_ev_charging",
            vehicle_vin,
            None,
            "Manual stop from mobile",
        )
        if success:
            if charger_type == "zaptec":
                return True, "Charging stopped via Zaptec"
            if charger_type == "sigenergy":
                return True, "Charging stopped via Sigenergy charger"
            return True, "Charging stopped"

        return False, "Failed to stop charging"

    async def _set_charge_limit(self, percent: int, vehicle_vin: str | None = None) -> tuple[bool, str]:
        """Set charge limit percentage. Returns (success, message)."""
        # Clamp to valid range (50-100%)
        percent = max(50, min(100, int(percent)))

        charger_type = self._manual_action_params(vehicle_vin).get("charger_type", "tesla")
        success = await self._execute_manual_ev_action(
            "set_ev_charge_limit",
            vehicle_vin,
            {"percent": percent},
            "Manual charge limit from mobile",
        )
        if success:
            if charger_type in ("generic", "ocpp", "zaptec", "sigenergy"):
                return True, "Charge limit is not supported for this charger"
            return True, f"Charge limit set to {percent}%"

        return False, "Failed to set charge limit"

    async def _set_charging_amps(self, amps: int, vehicle_vin: str | None = None) -> tuple[bool, str]:
        """Set charging amperage. Returns (success, message)."""
        # Clamp to valid range (1-48A for most, up to 80A for some)
        amps = max(1, min(80, int(amps)))

        params = self._manual_action_params(vehicle_vin)
        charger_type = params.get("charger_type", "tesla")

        # Tesla vehicles can only set amps when connected. Charger-native
        # integrations validate this through their own status/action handlers.
        if charger_type == "tesla" and not await self._is_vehicle_plugged_in(vehicle_vin):
            msg = "Vehicle is not plugged in - cannot set charging amps"
            _LOGGER.warning(msg)
            return False, msg

        success = await self._execute_manual_ev_action(
            "set_ev_charging_amps",
            vehicle_vin,
            {"amps": amps},
            "Manual charging amps from mobile",
        )
        if success:
            return True, f"Charging amps set to {amps}A"

        return False, "Failed to set charging amps"

    async def post(self, request: web.Request, vehicle_id: str) -> web.Response:
        """Handle POST request to send vehicle command."""
        try:
            data = await request.json()
            command = data.get("command")

            if not command:
                return web.json_response({
                    "success": False,
                    "error": "Missing 'command' parameter"
                }, status=400)

            valid_commands = [
                "wake_up",
                "start_charging",
                "start_policy_charging",
                "stop_charging",
                "set_charge_limit",
                "set_charging_amps",
            ]
            if command not in valid_commands:
                return web.json_response({
                    "success": False,
                    "error": f"Invalid command. Must be one of: {', '.join(valid_commands)}"
                }, status=400)

            # Get VIN from request body, or look up from vehicle_id in URL path
            vehicle_vin = data.get("vin")
            if not vehicle_vin and vehicle_id:
                # Map vehicle_id (sequential number) to VIN
                vehicle_vin = self._get_vin_from_vehicle_id(vehicle_id)
                _LOGGER.info(f"Mapped vehicle_id {vehicle_id} to VIN: {vehicle_vin}")

            success = False
            message = ""

            if command == "wake_up":
                success = await self._wake_vehicle(vehicle_vin)
                message = "Vehicle is awake" if success else "Failed to wake vehicle"

            elif command == "start_charging":
                duration_minutes = data.get("duration_minutes")
                if duration_minutes is not None:
                    try:
                        duration_minutes = int(duration_minutes)
                        if not (1 <= duration_minutes <= 1440):
                            return web.json_response({
                                "success": False,
                                "error": "duration_minutes must be 1-1440"
                            }, status=400)
                    except (TypeError, ValueError):
                        return web.json_response({
                            "success": False,
                            "error": "Invalid duration_minutes value"
                        }, status=400)

                source_mode = data.get("source_mode") or "standard"
                if source_mode not in ("standard", "grid_allowed"):
                    return web.json_response({
                        "success": False,
                        "error": "source_mode must be standard or grid_allowed"
                    }, status=400)

                success, message = await self._start_charging(
                    vehicle_vin,
                    duration_minutes,
                    source_mode,
                )

            elif command == "start_policy_charging":
                try:
                    success, message = await self._start_policy_charging(
                        data.get("policy"),
                        vehicle_vin,
                        data.get("duration_minutes"),
                    )
                except ValueError as err:
                    return web.json_response({
                        "success": False,
                        "error": str(err),
                    }, status=400)

            elif command == "stop_charging":
                success, message = await self._stop_charging(vehicle_vin)

            elif command == "set_charge_limit":
                percent = data.get("value") or data.get("percent") or data.get("limit")
                if percent is None:
                    return web.json_response({
                        "success": False,
                        "error": "Missing 'value' parameter for set_charge_limit (50-100)"
                    }, status=400)
                success, message = await self._set_charge_limit(int(percent), vehicle_vin)

            elif command == "set_charging_amps":
                amps = data.get("value") or data.get("amps")
                if amps is None:
                    return web.json_response({
                        "success": False,
                        "error": "Missing 'value' parameter for set_charging_amps (1-48)"
                    }, status=400)
                success, message = await self._set_charging_amps(int(amps), vehicle_vin)

            if success:
                return web.json_response({
                    "success": True,
                    "data": {"message": message}
                })
            else:
                return web.json_response({
                    "success": False,
                    "error": message
                }, status=500)

        except Exception as e:
            _LOGGER.error(f"Error executing vehicle command: {e}", exc_info=True)
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

class SolarSurplusStatusView(HomeAssistantView):
    """HTTP view to get solar surplus charging status for mobile app."""

    url = "/api/power_sync/ev/solar_surplus_status"
    name = "api:power_sync:ev:solar_surplus_status"
    requires_auth = True

    def __init__(self, hass: HomeAssistant):
        """Initialize the view."""
        self._hass = hass

    async def get(self, request: web.Request) -> web.Response:
        """Handle GET request for solar surplus status."""
        try:
            from ..automations.actions import _dynamic_ev_state, _calculate_solar_surplus
            from ..solar_surplus_config import get_stored_solar_surplus_config

            # Get config entry
            entries = self._hass.config_entries.async_entries(DOMAIN)
            if not entries:
                return web.json_response({
                    "success": False,
                    "error": "PowerSync not configured"
                }, status=400)

            entry = entries[0]
            entry_id = entry.entry_id
            entry_data = self._hass.data.get(DOMAIN, {}).get(entry_id, {})

            # Get data from tesla_coordinator (preferred) or sigenergy_coordinator
            battery_soc = 0.0
            solar_power_kw = 0.0
            grid_power_kw = 0.0
            battery_power_kw = 0.0
            load_power_kw = 0.0

            tesla_coordinator = entry_data.get("tesla_coordinator")
            sigenergy_coordinator = entry_data.get("sigenergy_coordinator")
            sungrow_coordinator = entry_data.get("sungrow_coordinator")
            foxess_coordinator = entry_data.get("foxess_coordinator")

            if tesla_coordinator and tesla_coordinator.data:
                # Tesla coordinator stores values in kW
                solar_power_kw = tesla_coordinator.data.get("solar_power", 0)
                grid_power_kw = tesla_coordinator.data.get("grid_power", 0)
                battery_power_kw = tesla_coordinator.data.get("battery_power", 0)
                load_power_kw = tesla_coordinator.data.get("load_power", 0)
                battery_soc = tesla_coordinator.data.get("battery_level", 0)
                _LOGGER.debug(f"Solar surplus status from tesla_coordinator: battery_soc={battery_soc}%")
            elif sigenergy_coordinator and sigenergy_coordinator.data:
                solar_power_kw = sigenergy_coordinator.data.get("solar_power", 0)
                grid_power_kw = sigenergy_coordinator.data.get("grid_power", 0)
                battery_power_kw = sigenergy_coordinator.data.get("battery_power", 0)
                load_power_kw = sigenergy_coordinator.data.get("load_power", 0)
                battery_soc = sigenergy_coordinator.data.get("battery_level", 0)
                _LOGGER.debug(f"Solar surplus status from sigenergy_coordinator: battery_soc={battery_soc}%")
            elif sungrow_coordinator and sungrow_coordinator.data:
                solar_power_kw = sungrow_coordinator.data.get("solar_power", 0)
                grid_power_kw = sungrow_coordinator.data.get("grid_power", 0)
                battery_power_kw = sungrow_coordinator.data.get("battery_power", 0)
                load_power_kw = sungrow_coordinator.data.get("load_power", 0)
                battery_soc = sungrow_coordinator.data.get("battery_level", 0)
                _LOGGER.debug(f"Solar surplus status from sungrow_coordinator: battery_soc={battery_soc}%")
            elif foxess_coordinator and foxess_coordinator.data:
                solar_power_kw = foxess_coordinator.data.get("solar_power", 0)
                grid_power_kw = foxess_coordinator.data.get("grid_power", 0)
                battery_power_kw = foxess_coordinator.data.get("battery_power", 0)
                load_power_kw = foxess_coordinator.data.get("load_power", 0)
                battery_soc = foxess_coordinator.data.get("battery_level", 0)
                _LOGGER.debug(f"Solar surplus status from foxess_coordinator: battery_soc={battery_soc}%")

            # Calculate surplus
            live_status = {
                "solar_power": solar_power_kw * 1000,  # _calculate_solar_surplus expects watts
                "grid_power": grid_power_kw * 1000,
                "battery_power": battery_power_kw * 1000,
                "load_power": load_power_kw * 1000,
                "battery_soc": battery_soc,
            }
            solar_config = get_stored_solar_surplus_config(entry_data)
            surplus_kw = _calculate_solar_surplus(live_status, 0, solar_config)

            # Get per-vehicle states
            vehicles_state = []
            entry_vehicles = _dynamic_ev_state.get(entry_id, {})

            for vehicle_id, state in entry_vehicles.items():
                if state.get("active"):
                    params = state.get("params", {})
                    vehicles_state.append({
                        "vehicle_id": vehicle_id,
                        "vehicle_name": state.get("vehicle_name"),
                        "active": state.get("active", False),
                        "mode": params.get("dynamic_mode", "battery_target"),
                        "current_amps": state.get("current_amps", 0),
                        "target_amps": state.get("target_amps", 0),
                        "allocated_surplus_kw": state.get("allocated_surplus_kw", 0),
                        "reason": state.get("reason", ""),
                        "paused": state.get("paused", False),
                        "paused_reason": state.get("paused_reason"),
                        "priority": state.get("priority", 1),
                        "charging_started": state.get("charging_started", False),
                    })

            # Get EV power and SoC — prefer vehicle sensor (e.g. sensor.tessy_charger_power),
            # fall back to Wall Connector data from coordinator
            ev_status = _get_ev_vehicle_status(self._hass, entry)
            ev_power_kw = ev_status["ev_power_kw"]
            ev_soc = ev_status["ev_soc"]
            if ev_power_kw <= 0 and tesla_coordinator and tesla_coordinator.data:
                ev_power_kw = tesla_coordinator.data.get("ev_power", 0)

            # If no dynamic vehicles but EV is charging, include it
            if not vehicles_state and ev_power_kw > 0.05:
                vehicles_state.append({
                    "vehicle_id": "wall_connector",
                    "vehicle_name": "Tesla EV",
                    "active": True,
                    "mode": "native",
                    "current_amps": 0,
                    "target_amps": 0,
                    "allocated_surplus_kw": 0,
                    "reason": "Charging via Tesla Wall Connector",
                    "paused": False,
                    "paused_reason": None,
                    "priority": 1,
                    "charging_started": True,
                    "current_power_kw": round(ev_power_kw, 2),
                    "current_soc": ev_soc,
                })

            return web.json_response({
                "success": True,
                "surplus_kw": round(surplus_kw, 2),
                "battery_soc": round(battery_soc, 1),
                "solar_power_kw": round(solar_power_kw, 2),
                "grid_power_kw": round(grid_power_kw, 2),
                "ev_power_kw": round(ev_power_kw, 2),
                "vehicles": vehicles_state,
            })

        except Exception as e:
            _LOGGER.error(f"Error getting solar surplus status: {e}", exc_info=True)
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

class VehicleChargingConfigView(HomeAssistantView):
    """HTTP view to manage vehicle charging configurations."""

    url = "/api/power_sync/ev/vehicle_config"
    name = "api:power_sync:ev:vehicle_config"
    requires_auth = True

    def __init__(self, hass: HomeAssistant):
        """Initialize the view."""
        self._hass = hass

    def _get_store(self):
        """Get the automation store from hass.data for config persistence."""
        if DOMAIN not in self._hass.data:
            return None
        # Find store from any entry
        for entry_id, entry_data in self._hass.data.get(DOMAIN, {}).items():
            if isinstance(entry_data, dict) and "automation_store" in entry_data:
                return entry_data["automation_store"]
        return None

    def _generic_capacity_fallback(self) -> float | None:
        """Return the optional shared capacity for anonymous charger profiles."""
        from ..const import CONF_GENERIC_CHARGER_BATTERY_CAPACITY_KWH

        entries = self._hass.config_entries.async_entries(DOMAIN)
        if not entries:
            return None
        options = {**entries[0].data, **entries[0].options}
        return options.get(CONF_GENERIC_CHARGER_BATTERY_CAPACITY_KWH)

    def _capacity_contract(self, config: dict) -> dict:
        """Resolve public capacity metadata for one stored vehicle profile."""
        from ..automations.ev_vehicle_capacity import (
            resolve_ev_battery_capacity_contract,
        )

        vehicle_id = str(config.get("vehicle_id") or "")
        charger_type = str(config.get("charger_type") or "").lower()
        anonymous = (
            charger_type in ("generic", "ocpp")
            or vehicle_id.lower().startswith(("generic_", "ocpp_"))
        ) and not (
            vehicle_id.lower().startswith(("ble_", "byd_"))
            or (len(vehicle_id) == 17 and vehicle_id.isalnum())
        )
        return resolve_ev_battery_capacity_contract(
            config,
            anonymous_loadpoint=anonymous,
            shared_charger_fallback_capacity_kwh=(
                self._generic_capacity_fallback()
            ),
        )

    def _dynamic_state_matches_config(self, state: dict, config: dict | None) -> bool:
        """Return true when runtime dynamic state belongs to a removed config."""
        if not config:
            return False
        params = state.get("params", {}) or {}
        if config.get("charger_type") and params.get("charger_type") != config.get("charger_type"):
            return False
        for key in (
            "charger_switch_entity",
            "charger_amps_entity",
            "charger_status_entity",
            "charger_power_entity",
            "ocpp_charger_id",
        ):
            if config.get(key) and params.get(key) == config.get(key):
                return True
        return False

    async def _cleanup_vehicle_runtime_state(
        self,
        vehicle_ids: set[str],
        removed_config: dict | None,
    ) -> list[str]:
        """Clear runtime EV state for deleted vehicle configs."""
        changes: list[str] = []
        runtime_ids = set(vehicle_ids)

        try:
            from ..automations.actions import _dynamic_ev_state

            for entry_id, vehicles in list(_dynamic_ev_state.items()):
                for vid, state in list(vehicles.items()):
                    if vid in vehicle_ids or self._dynamic_state_matches_config(state, removed_config):
                        vehicles.pop(vid, None)
                        runtime_ids.add(vid)
                        changes.append(f"dynamic_state:{vid}")

                entry_data = self._hass.data.get(DOMAIN, {}).get(entry_id)
                if not isinstance(entry_data, dict):
                    continue
                if vehicles:
                    entry_data["dynamic_ev_state"] = vehicles
                else:
                    _dynamic_ev_state.pop(entry_id, None)
                    entry_data.pop("dynamic_ev_state", None)
        except Exception as err:
            _LOGGER.debug("Deleted vehicle dynamic-state cleanup failed: %s", err)

        try:
            from ..automations.ev_charging_session import get_session_manager

            session_manager = get_session_manager()
            if session_manager:
                for vid in runtime_ids:
                    if vid in session_manager.active_sessions:
                        await session_manager.end_session(vid, "vehicle_deleted")
                        changes.append(f"session:{vid}")
        except Exception as err:
            _LOGGER.debug("Deleted vehicle session cleanup failed: %s", err)

        try:
            from ..automations.ev_charging_planner import (
                get_auto_schedule_executor,
                get_price_level_executor,
            )

            auto_executor = get_auto_schedule_executor()
            if auto_executor:
                for vid in runtime_ids:
                    if auto_executor._settings.pop(vid, None) is not None:
                        changes.append(f"auto_settings:{vid}")
                    if auto_executor._state.pop(vid, None) is not None:
                        changes.append(f"auto_state:{vid}")
                    auto_executor._cached_soc.pop(vid, None)
                    auto_executor._current_charge_amps.pop(vid, None)

            price_level = get_price_level_executor()
            if price_level:
                for vid in runtime_ids:
                    if price_level._vehicle_states.pop(vid, None) is not None:
                        changes.append(f"price_level_state:{vid}")
        except Exception as err:
            _LOGGER.debug("Deleted vehicle executor cleanup failed: %s", err)

        return changes

    async def get(self, request: web.Request) -> web.Response:
        """Handle GET request - get all vehicle charging configs."""
        try:
            store = self._get_store()
            if not store:
                return web.json_response({
                    "success": True,
                    "configs": []
                })

            # Get stored vehicle configs (use _data directly, it's already loaded)
            data = getattr(store, '_data', {}) or {}
            vehicle_configs = data.get("vehicle_charging_configs", [])
            resolved_configs = [
                {**config, **self._capacity_contract(config)}
                for config in vehicle_configs
            ]

            return web.json_response({
                "success": True,
                "configs": resolved_configs
            })

        except Exception as e:
            _LOGGER.error(f"Error fetching vehicle configs: {e}", exc_info=True)
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

    async def post(self, request: web.Request) -> web.Response:
        """Handle POST request - update vehicle charging config."""
        try:
            data = await request.json()
            vehicle_id = data.get("vehicle_id")

            if not vehicle_id:
                return web.json_response({
                    "success": False,
                    "error": "vehicle_id is required"
                }, status=400)

            store = self._get_store()
            if not store:
                return web.json_response({
                    "success": False,
                    "error": "Storage not available"
                }, status=503)

            # Get existing configs (use _data directly, it's already loaded at startup)
            stored_data = getattr(store, '_data', {}) or {}
            vehicle_configs = stored_data.get("vehicle_charging_configs", [])

            from ..automations.ev_vehicle_capacity import (
                validate_ev_battery_capacity,
                vehicle_ids_match,
            )
            existing_config = next(
                (
                    config for config in vehicle_configs
                    if vehicle_ids_match(config.get("vehicle_id"), vehicle_id)
                ),
                {},
            )
            charger_type = str(
                data.get("charger_type")
                or existing_config.get("charger_type")
                or ""
            ).lower()
            stable_id = str(vehicle_id).lower()
            anonymous = (
                charger_type in ("generic", "ocpp")
                or stable_id.startswith(("generic_", "ocpp_"))
            ) and not (
                stable_id.startswith(("ble_", "byd_"))
                or (len(stable_id) == 17 and stable_id.isalnum())
            )
            capacity_changed = "battery_capacity_kwh" in data
            if "battery_capacity_kwh" in data:
                try:
                    capacity = validate_ev_battery_capacity(
                        data["battery_capacity_kwh"]
                    )
                except ValueError as err:
                    return web.json_response({
                        "success": False,
                        "error": str(err),
                    }, status=400)
                if anonymous:
                    data["charger_fallback_battery_capacity_kwh"] = capacity
                    data.pop("battery_capacity_kwh", None)
                    if capacity is None:
                        existing_config.pop(
                            "charger_fallback_battery_capacity_kwh", None
                        )
                        existing_config.pop("battery_capacity_kwh", None)
                else:
                    data["battery_capacity_kwh"] = capacity

            # Find and update or add config
            config_found = False

            for i, config in enumerate(vehicle_configs):
                if vehicle_ids_match(config.get("vehicle_id"), vehicle_id):
                    # Update existing config
                    vehicle_configs[i] = {
                        **config,
                        **data,
                        "vehicle_id": config.get("vehicle_id") or vehicle_id,
                    }
                    config_found = True
                    break

            if not config_found:
                # Add new config with defaults
                new_config = {
                    "vehicle_id": vehicle_id,
                    "display_name": data.get("display_name", f"Vehicle {vehicle_id}"),
                    "charger_type": data.get(
                        "charger_type",
                        "generic" if stable_id.startswith("generic_") else (
                            "ocpp" if stable_id.startswith("ocpp_") else "tesla"
                        ),
                    ),
                    "charger_switch_entity": data.get("charger_switch_entity"),
                    "charger_amps_entity": data.get("charger_amps_entity"),
                    "charger_status_entity": data.get("charger_status_entity"),
                    "charger_power_entity": data.get("charger_power_entity"),
                    "ocpp_charger_id": data.get("ocpp_charger_id"),
                    "sigenergy_charger_host": data.get("sigenergy_charger_host"),
                    "sigenergy_charger_port": data.get("sigenergy_charger_port"),
                    "sigenergy_charger_slave_id": data.get("sigenergy_charger_slave_id"),
                    "sigenergy_charger_type": data.get("sigenergy_charger_type"),
                    "sigenergy_charger_charge_power_limit_entity": data.get(
                        "sigenergy_charger_charge_power_limit_entity"
                    ),
                    "sigenergy_charger_discharge_power_limit_entity": data.get(
                        "sigenergy_charger_discharge_power_limit_entity"
                    ),
                    "pre_charge_wake_entity": data.get("pre_charge_wake_entity"),
                    "pre_charge_wake_duration_seconds": data.get("pre_charge_wake_duration_seconds", 5),
                    "min_amps": data.get("min_amps", data.get("min_charge_amps", 5)),
                    "max_amps": data.get("max_amps", data.get("max_charge_amps", 32)),
                    "voltage": data.get("voltage", 240),
                    "phases": data.get("phases", 1),
                    "solar_charging_enabled": data.get("solar_charging_enabled", False),
                    "priority": data.get("priority", 1),
                    "home_battery_minimum": data.get("home_battery_minimum", 80),
                    "pause_if_battery_below": data.get("pause_if_battery_below", 70),
                    "battery_capacity_kwh": data.get("battery_capacity_kwh"),
                    "charger_fallback_battery_capacity_kwh": data.get(
                        "charger_fallback_battery_capacity_kwh"
                    ),
                    "provider_battery_capacity_kwh": data.get("provider_battery_capacity_kwh"),
                    "vehicle_model": data.get("vehicle_model", data.get("model")),
                    "vehicle_trim": data.get("vehicle_trim", data.get("trim")),
                }
                vehicle_configs.append(new_config)

            # Save updated configs (update key in existing _data, don't overwrite)
            if hasattr(store, '_data') and hasattr(store, 'async_save'):
                store._data["vehicle_charging_configs"] = vehicle_configs
                await store.async_save()

            # Sync charger params to AutoScheduleSettings so planner uses correct values
            try:
                from ..automations.ev_charging_planner import (
                    _vehicle_config_matches,
                    get_auto_schedule_executor,
                )
                executor = get_auto_schedule_executor()
                if executor:
                    saved_config = next(
                        (c for c in vehicle_configs if vehicle_ids_match(c.get("vehicle_id"), vehicle_id)),
                        None,
                    )
                    synced_vehicle_ids = [
                        vid
                        for vid in executor._settings
                        if _vehicle_config_matches(vid, vehicle_id)
                    ]
                    if saved_config and synced_vehicle_ids:
                        for synced_vehicle_id in synced_vehicle_ids:
                            settings = executor._settings[synced_vehicle_id]
                            if hasattr(settings, "apply_charger_config"):
                                settings.apply_charger_config(saved_config)
                            else:
                                if "max_amps" in saved_config or "max_charge_amps" in saved_config:
                                    settings.max_charge_amps = saved_config.get(
                                        "max_amps",
                                        saved_config.get("max_charge_amps"),
                                    )
                                if "min_amps" in saved_config or "min_charge_amps" in saved_config:
                                    settings.min_charge_amps = saved_config.get(
                                        "min_amps",
                                        saved_config.get("min_charge_amps"),
                                    )
                                if "voltage" in saved_config:
                                    settings.voltage = saved_config["voltage"]
                                if "phases" in saved_config:
                                    settings.phases = saved_config["phases"]
                                if "charger_type" in saved_config:
                                    settings.charger_type = saved_config["charger_type"]
                                if "charger_switch_entity" in saved_config:
                                    settings.charger_switch_entity = saved_config["charger_switch_entity"]
                                if "charger_amps_entity" in saved_config:
                                    settings.charger_amps_entity = saved_config["charger_amps_entity"]
                                if "charger_status_entity" in saved_config:
                                    settings.charger_status_entity = saved_config["charger_status_entity"]
                                if "charger_power_entity" in saved_config:
                                    settings.charger_power_entity = saved_config["charger_power_entity"]
                                if "ocpp_charger_id" in saved_config:
                                    settings.ocpp_charger_id = saved_config["ocpp_charger_id"]
                                if "pre_charge_wake_entity" in saved_config:
                                    settings.pre_charge_wake_entity = saved_config["pre_charge_wake_entity"]
                                if "pre_charge_wake_duration_seconds" in saved_config:
                                    settings.pre_charge_wake_duration_seconds = saved_config["pre_charge_wake_duration_seconds"]
                            state = executor.get_state(synced_vehicle_id)
                            state.current_plan = None
                            state.last_plan_update = None
                            await executor._regenerate_plan(
                                synced_vehicle_id, settings, state
                            )
                            _LOGGER.debug(
                                "Synced charger params to auto-schedule for %s: "
                                "max=%dA, voltage=%dV, phases=%d",
                                synced_vehicle_id,
                                settings.max_charge_amps,
                                settings.voltage,
                                settings.phases,
                            )
                    elif saved_config and vehicle_id in executor._settings:
                        settings = executor._settings[vehicle_id]
                        if hasattr(settings, "apply_charger_config"):
                            settings.apply_charger_config(saved_config)
                        else:
                            if "max_amps" in saved_config or "max_charge_amps" in saved_config:
                                settings.max_charge_amps = saved_config.get(
                                    "max_amps",
                                    saved_config.get("max_charge_amps"),
                                )
                            if "min_amps" in saved_config or "min_charge_amps" in saved_config:
                                settings.min_charge_amps = saved_config.get(
                                    "min_amps",
                                    saved_config.get("min_charge_amps"),
                                )
                            if "voltage" in saved_config:
                                settings.voltage = saved_config["voltage"]
                            if "phases" in saved_config:
                                settings.phases = saved_config["phases"]
                            if "charger_type" in saved_config:
                                settings.charger_type = saved_config["charger_type"]
                            if "charger_switch_entity" in saved_config:
                                settings.charger_switch_entity = saved_config["charger_switch_entity"]
                            if "charger_amps_entity" in saved_config:
                                settings.charger_amps_entity = saved_config["charger_amps_entity"]
                            if "charger_status_entity" in saved_config:
                                settings.charger_status_entity = saved_config["charger_status_entity"]
                            if "charger_power_entity" in saved_config:
                                settings.charger_power_entity = saved_config["charger_power_entity"]
                            if "ocpp_charger_id" in saved_config:
                                settings.ocpp_charger_id = saved_config["ocpp_charger_id"]
                            if "pre_charge_wake_entity" in saved_config:
                                settings.pre_charge_wake_entity = saved_config["pre_charge_wake_entity"]
                            if "pre_charge_wake_duration_seconds" in saved_config:
                                settings.pre_charge_wake_duration_seconds = saved_config["pre_charge_wake_duration_seconds"]
                        state = executor.get_state(vehicle_id)
                        state.current_plan = None
                        state.last_plan_update = None
                        await executor._regenerate_plan(vehicle_id, settings, state)
                        _LOGGER.debug(
                            "Synced charger params to auto-schedule for %s: "
                            "max=%dA, voltage=%dV, phases=%d",
                            vehicle_id,
                            settings.max_charge_amps,
                            settings.voltage,
                            settings.phases,
                        )
            except Exception:
                pass  # Non-critical — params will sync on next evaluation cycle

            if capacity_changed:
                for entry_data in self._hass.data.get(DOMAIN, {}).values():
                    if not isinstance(entry_data, dict):
                        continue
                    coordinator = entry_data.get("optimization_coordinator")
                    schedule_refresh = getattr(
                        coordinator, "_schedule_settings_reoptimization", None
                    )
                    if schedule_refresh:
                        schedule_refresh()

            saved = next(
                c for c in vehicle_configs
                if vehicle_ids_match(c.get("vehicle_id"), vehicle_id)
            )

            return web.json_response({
                "success": True,
                "config": {**saved, **self._capacity_contract(saved)},
            })

        except Exception as e:
            _LOGGER.error(f"Error updating vehicle config: {e}", exc_info=True)
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

    async def delete(self, request: web.Request) -> web.Response:
        """Handle DELETE request - remove a vehicle charging config."""
        try:
            vehicle_id = request.query.get("vehicle_id")
            if not vehicle_id:
                return web.json_response({
                    "success": False,
                    "error": "vehicle_id query parameter is required"
                }, status=400)

            store = self._get_store()
            if not store:
                return web.json_response({
                    "success": False,
                    "error": "Storage not available"
                }, status=503)

            stored_data = getattr(store, '_data', {}) or {}
            vehicle_configs = stored_data.get("vehicle_charging_configs", [])
            removed_config = next(
                (c for c in vehicle_configs if c.get("vehicle_id") == vehicle_id),
                None,
            )
            updated = [c for c in vehicle_configs if c.get("vehicle_id") != vehicle_id]

            if len(updated) == len(vehicle_configs):
                return web.json_response({
                    "success": False,
                    "error": f"Vehicle {vehicle_id} not found"
                }, status=404)

            cleanup_ids = {vehicle_id}
            if removed_config:
                if removed_config.get("charger_type") == "generic":
                    cleanup_ids.add("generic_ev")
                if removed_config.get("ocpp_charger_id"):
                    cleanup_ids.add(f"ocpp_{removed_config['ocpp_charger_id']}")

            store._data["vehicle_charging_configs"] = updated
            for key in ("auto_schedule_settings", "cached_vehicle_soc"):
                value = store._data.get(key)
                if isinstance(value, dict):
                    for cleanup_id in cleanup_ids:
                        value.pop(cleanup_id, None)
            await store.async_save()

            cleanup_changes = await self._cleanup_vehicle_runtime_state(
                cleanup_ids,
                removed_config,
            )

            _LOGGER.info(
                "Removed vehicle config: %s (cleanup=%s)",
                vehicle_id,
                cleanup_changes,
            )
            return web.json_response({
                "success": True,
                "cleanup": cleanup_changes,
            })

        except Exception as e:
            _LOGGER.error(f"Error deleting vehicle config: {e}", exc_info=True)
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

class SolarSurplusConfigView(HomeAssistantView):
    """HTTP view to manage global solar surplus settings."""

    url = "/api/power_sync/ev/solar_surplus_config"
    name = "api:power_sync:ev:solar_surplus_config"
    requires_auth = True

    def __init__(self, hass: HomeAssistant):
        """Initialize the view."""
        self._hass = hass

    def _get_store(self):
        """Get the automation store from hass.data for config persistence."""
        if DOMAIN not in self._hass.data:
            return None
        for entry_id, entry_data in self._hass.data.get(DOMAIN, {}).items():
            if isinstance(entry_data, dict) and "automation_store" in entry_data:
                return entry_data["automation_store"]
        return None

    async def get(self, request: web.Request) -> web.Response:
        """Handle GET request - get solar surplus config."""
        try:
            from ..solar_surplus_config import (
                DEFAULT_SOLAR_SURPLUS_CONFIG,
                normalize_solar_surplus_config,
            )

            store = self._get_store()
            default_config = dict(DEFAULT_SOLAR_SURPLUS_CONFIG)

            if not store:
                return web.json_response({
                    "success": True,
                    "config": default_config
                })

            stored_data = getattr(store, '_data', {}) or {}
            config = normalize_solar_surplus_config(stored_data.get("solar_surplus_config", {}))

            return web.json_response({
                "success": True,
                "config": config
            })

        except Exception as e:
            _LOGGER.error(f"Error fetching solar surplus config: {e}", exc_info=True)
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

    async def post(self, request: web.Request) -> web.Response:
        """Handle POST request - update solar surplus config."""
        try:
            from ..solar_surplus_config import normalize_solar_surplus_config

            data = await request.json()
            if "min_battery_soc" in data and "home_battery_minimum" not in data:
                data["home_battery_minimum"] = data["min_battery_soc"]

            store = self._get_store()
            if not store:
                return web.json_response({
                    "success": False,
                    "error": "Storage not available"
                }, status=503)

            # Get existing config (use _data directly)
            stored_data = getattr(store, '_data', {}) or {}
            current_config = stored_data.get("solar_surplus_config", {})
            updated_config = {**current_config, **data}

            # Validate config values
            if "household_buffer_kw" in updated_config:
                updated_config["household_buffer_kw"] = max(0, min(5, float(updated_config["household_buffer_kw"])))
            if "sustained_surplus_minutes" in updated_config:
                updated_config["sustained_surplus_minutes"] = max(1, min(30, int(updated_config["sustained_surplus_minutes"])))
            if "stop_delay_minutes" in updated_config:
                updated_config["stop_delay_minutes"] = max(1, min(30, int(updated_config["stop_delay_minutes"])))
            if "surplus_calculation" in updated_config:
                if updated_config["surplus_calculation"] not in ("grid_based", "direct"):
                    updated_config["surplus_calculation"] = "grid_based"
            if "dual_vehicle_strategy" in updated_config:
                if updated_config["dual_vehicle_strategy"] not in ("even", "priority_first", "priority_only"):
                    updated_config["dual_vehicle_strategy"] = "priority_first"
            if "home_battery_minimum" in updated_config:
                updated_config["home_battery_minimum"] = max(0, min(100, int(updated_config["home_battery_minimum"])))
            if "allow_parallel_charging" in updated_config:
                updated_config["allow_parallel_charging"] = bool(updated_config["allow_parallel_charging"])
            if "max_battery_charge_rate_kw" in updated_config:
                updated_config["max_battery_charge_rate_kw"] = max(1, min(30, float(updated_config["max_battery_charge_rate_kw"])))
            updated_config = normalize_solar_surplus_config(updated_config)

            # Save updated config (update key in existing _data, don't overwrite)
            if hasattr(store, '_data') and hasattr(store, 'async_save'):
                store._data["solar_surplus_config"] = updated_config
                await store.async_save()

            return web.json_response({
                "success": True,
                "config": updated_config
            })

        except Exception as e:
            _LOGGER.error(f"Error updating solar surplus config: {e}", exc_info=True)
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

class ChargingSessionsView(HomeAssistantView):
    """HTTP view to get EV charging session history."""

    url = "/api/power_sync/ev/sessions"
    name = "api:power_sync:ev:sessions"
    requires_auth = True

    def __init__(self, hass: HomeAssistant):
        """Initialize the view."""
        self._hass = hass

    def _get_session_manager(self):
        """Get the charging session manager."""
        from ..automations.ev_charging_session import get_session_manager
        return get_session_manager()

    async def get(self, request: web.Request) -> web.Response:
        """Handle GET request - get charging session history.

        Query parameters:
            vehicle_id: Filter by vehicle (optional)
            days: Number of days to look back (default 30)
            limit: Maximum sessions to return (default 100)
        """
        try:
            manager = self._get_session_manager()
            if not manager:
                return web.json_response({
                    "success": True,
                    "sessions": [],
                    "message": "Session tracking not initialized"
                })

            vehicle_id = request.query.get("vehicle_id")
            days = int(request.query.get("days", 30))
            limit = int(request.query.get("limit", 100))

            sessions = await manager.get_session_history(
                vehicle_id=vehicle_id,
                days=days,
                limit=limit,
            )

            # Also include any active sessions
            active_sessions = []
            for vid, session in manager.active_sessions.items():
                if vehicle_id is None or vid == vehicle_id:
                    active_sessions.append({
                        **session.to_dict(),
                        "is_active": True,
                    })

            return web.json_response({
                "success": True,
                "sessions": [s.to_dict() for s in sessions],
                "active_sessions": active_sessions,
            })

        except Exception as e:
            _LOGGER.error(f"Error fetching charging sessions: {e}", exc_info=True)
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

class ChargingStatisticsView(HomeAssistantView):
    """HTTP view to get EV charging statistics."""

    url = "/api/power_sync/ev/statistics"
    name = "api:power_sync:ev:statistics"
    requires_auth = True

    def __init__(self, hass: HomeAssistant):
        """Initialize the view."""
        self._hass = hass

    def _get_session_manager(self):
        """Get the charging session manager."""
        from ..automations.ev_charging_session import get_session_manager
        return get_session_manager()

    async def get(self, request: web.Request) -> web.Response:
        """Handle GET request - get charging statistics.

        Query parameters:
            vehicle_id: Filter by vehicle (optional)
            days: Number of days to analyze (default 30)
        """
        try:
            manager = self._get_session_manager()
            if not manager:
                return web.json_response({
                    "success": True,
                    "statistics": {
                        "period_days": 30,
                        "total_sessions": 0,
                        "total_energy_kwh": 0,
                        "solar_energy_kwh": 0,
                        "grid_energy_kwh": 0,
                        "solar_percentage": 0,
                        "total_cost_dollars": 0,
                        "total_savings_dollars": 0,
                        "avg_cost_per_kwh_cents": 0,
                        "avg_session_duration_minutes": 0,
                        "avg_session_energy_kwh": 0,
                        "by_vehicle": {},
                        "by_day": [],
                    },
                    "message": "Session tracking not initialized"
                })

            vehicle_id = request.query.get("vehicle_id")
            days = int(request.query.get("days", 30))

            statistics = await manager.get_statistics(
                vehicle_id=vehicle_id,
                days=days,
            )

            return web.json_response({
                "success": True,
                "statistics": statistics,
            })

        except Exception as e:
            _LOGGER.error(f"Error calculating charging statistics: {e}", exc_info=True)
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

class ChargingScheduleView(HomeAssistantView):
    """HTTP view to get/update charging schedules."""

    url = "/api/power_sync/ev/schedule"
    name = "api:power_sync:ev:schedule"
    requires_auth = True

    def __init__(self, hass: HomeAssistant):
        """Initialize the view."""
        self._hass = hass

    def _get_planner(self):
        """Get the charging planner."""
        from ..automations.ev_charging_planner import get_charging_planner
        return get_charging_planner()

    def _get_store(self):
        """Get the automation store."""
        if DOMAIN not in self._hass.data:
            return None
        for entry_id, entry_data in self._hass.data.get(DOMAIN, {}).items():
            if isinstance(entry_data, dict) and "automation_store" in entry_data:
                return entry_data["automation_store"]
        return None

    async def _get_vehicle_soc(self, vehicle_id: str) -> int:
        """Get current SoC for a vehicle from Home Assistant entities.

        Uses the same approach as EVVehiclesView to find Tesla vehicles.

        Args:
            vehicle_id: Vehicle identifier

        Returns:
            Current battery level (0-100), defaults to 50 if not found.
        """
        # Method 0: Generic charger SoC sensor
        from ..const import CONF_GENERIC_CHARGER_ENABLED
        from ..automations.generic_charger_soc import resolve_generic_charger_soc
        entries = self._hass.config_entries.async_entries(DOMAIN)
        for entry in entries:
            opts = {**entry.data, **entry.options}
            if opts.get(CONF_GENERIC_CHARGER_ENABLED):
                level = resolve_generic_charger_soc(self._hass, opts)
                if level is not None:
                    _LOGGER.debug("ChargingScheduleView: Found generic charger SoC: %.1f%%", level)
                    return int(level)
                break

        # Method 1a: Check Tesla BLE sensor with configured prefix
        config = {}
        if entries:
            config = dict(entries[0].options)

        ble_prefix = _resolve_ble_prefix(self._hass, config)
        ble_charge_level_entity = TESLA_BLE_SENSOR_CHARGE_LEVEL.format(prefix=ble_prefix)
        ble_state = self._hass.states.get(ble_charge_level_entity)

        if ble_state and ble_state.state not in ("unavailable", "unknown", "None", None):
            try:
                level = float(ble_state.state)
                if 0 <= level <= 100:
                    _LOGGER.debug(f"ChargingScheduleView: Found Tesla BLE SoC from {ble_charge_level_entity}: {level}%")
                    return int(level)
            except (ValueError, TypeError):
                pass

        # Method 1b: Check Teslemetry Bluetooth sensor
        tbt_prefix = _resolve_teslemetry_bt_prefix(self._hass)
        if tbt_prefix:
            tbt_soc_entity = TESLEMETRY_BT_SENSOR_BATTERY_LEVEL.format(prefix=tbt_prefix)
            tbt_state = self._hass.states.get(tbt_soc_entity)
            if tbt_state and tbt_state.state not in ("unavailable", "unknown", "None", None):
                try:
                    level = float(tbt_state.state)
                    if 0 <= level <= 100:
                        _LOGGER.debug(f"ChargingScheduleView: Found Teslemetry BT SoC from {tbt_soc_entity}: {level}%")
                        return int(level)
                except (ValueError, TypeError):
                    pass

        # Method 2: Check Tesla Fleet/Teslemetry entities via device registry
        entity_registry = er.async_get(self._hass)
        device_registry = dr.async_get(self._hass)

        tesla_integrations = TESLA_INTEGRATIONS

        for device in device_registry.devices.values():
            is_tesla_device = False
            device_vin = None
            for identifier in device.identifiers:
                if len(identifier) >= 2 and identifier[0] in tesla_integrations:
                    id_str = str(identifier[1])
                    # Only match vehicle devices (VIN format: 17 chars, not all digits)
                    if len(id_str) == 17 and not id_str.isdigit():
                        is_tesla_device = True
                        device_vin = id_str
                    break

            if not is_tesla_device:
                continue

            # Filter by vehicle_id (VIN) when not using default
            if vehicle_id != "_default" and device_vin:
                if device_vin != vehicle_id:
                    continue

            # Find battery/charge_level sensor for this Tesla device
            for entity in entity_registry.entities.values():
                if entity.device_id != device.id:
                    continue

                entity_id = entity.entity_id
                entity_id_lower = entity_id.lower()

                # Match battery level sensors (not power sensors, not powerwall)
                # We want: battery_level, charge_level (percentage sensors)
                # We don't want: battery_power, powerwall, battery (power sensors)
                if entity_id.startswith("sensor."):
                    # Skip powerwall entities entirely
                    if "powerwall" in entity_id_lower:
                        continue

                    # Skip power sensors (battery_power, etc)
                    if "battery_power" in entity_id_lower or entity_id_lower.endswith("_power"):
                        continue

                    # Only match explicit level sensors (battery_level, charge_level)
                    # NOT just "battery" which could match battery_power
                    if any(x in entity_id_lower for x in ["battery_level", "charge_level", "_level"]):
                        state = self._hass.states.get(entity_id)
                        if state and state.state not in ("unavailable", "unknown", "None", None):
                            try:
                                level = float(state.state)
                                if 0 <= level <= 100:
                                    _LOGGER.debug(f"ChargingScheduleView: Found Tesla Fleet/Teslemetry SoC from {entity_id}: {level}% (VIN: {device_vin})")
                                    return int(level)
                            except (ValueError, TypeError):
                                continue

        # Method 3: Check cached Tesla vehicles from PowerSync
        for entry_id, entry_data in self._hass.data.get(DOMAIN, {}).items():
            if isinstance(entry_data, dict):
                tesla_vehicles = entry_data.get("tesla_vehicles", [])
                for vehicle in tesla_vehicles:
                    vid = str(vehicle.get("id", ""))
                    if vehicle_id == "_default" or vehicle_id == vid or vehicle_id in vid:
                        battery_level = vehicle.get("battery_level")
                        if battery_level is not None:
                            _LOGGER.debug(f"ChargingScheduleView: Found vehicle SoC from cached data: {battery_level}%")
                            return int(battery_level)

        _LOGGER.warning(f"ChargingScheduleView: Could not find SoC for vehicle {vehicle_id}, using default 50%")
        return 50

    async def get(self, request: web.Request) -> web.Response:
        """Handle GET request - get charging plan/schedule.

        Query parameters:
            vehicle_id: Vehicle to get schedule for
            current_soc: Current state of charge (%)
            target_soc: Target state of charge (default 80%)
            target_time: Optional ISO format deadline
            priority: Charging priority (solar_only, solar_preferred, cost_optimized, time_critical)
        """
        try:
            planner = self._get_planner()
            if not planner:
                return web.json_response({
                    "success": False,
                    "error": "Charging planner not initialized"
                }, status=503)

            vehicle_id = request.query.get("vehicle_id", "_default")
            current_soc_param = request.query.get("current_soc")
            target_soc = int(request.query.get("target_soc", 80))
            target_time_str = request.query.get("target_time")
            priority_str = request.query.get("priority", "solar_preferred")

            # Get actual SoC from vehicle sensors if not provided, or if 0/50 (defaults)
            current_soc = 50  # Default fallback
            if current_soc_param and int(current_soc_param) not in (0, 50):
                # Explicit SoC provided, use it
                current_soc = int(current_soc_param)
            else:
                # Try to get actual SoC from Home Assistant sensors
                current_soc = await self._get_vehicle_soc(vehicle_id)

            # Parse target time
            target_time = None
            if target_time_str:
                try:
                    target_time = datetime.fromisoformat(target_time_str.replace("Z", "+00:00"))
                except ValueError:
                    pass

            # Parse priority
            from ..automations.ev_charging_planner import ChargingPriority
            try:
                priority = ChargingPriority(priority_str)
            except ValueError:
                priority = ChargingPriority.SOLAR_PREFERRED

            # Look up per-vehicle charger params from vehicle_charging_configs
            charger_power_kw = 7.0  # default
            matched_config = None
            store = self._get_store()
            if store:
                stored_data = getattr(store, '_data', {}) or {}
                from ..automations.ev_vehicle_capacity import vehicle_ids_match
                for vc in stored_data.get("vehicle_charging_configs", []):
                    if vehicle_ids_match(vc.get("vehicle_id"), vehicle_id) or vehicle_id == "_default":
                        matched_config = vc
                        max_amps = vc.get("max_amps", vc.get("max_charge_amps", 32))
                        voltage = vc.get("voltage", 230)
                        phases = vc.get("phases", 1)
                        charger_power_kw = (max_amps * voltage * phases) / 1000
                        break

            from ..automations.ev_vehicle_capacity import resolve_ev_battery_capacity
            from ..const import CONF_GENERIC_CHARGER_BATTERY_CAPACITY_KWH

            config_options = {}
            entries = self._hass.config_entries.async_entries(DOMAIN)
            if entries:
                config_options = {**entries[0].data, **entries[0].options}
            matched_config = matched_config or {}
            stable_id = str(vehicle_id or "").lower()
            anonymous = (
                vehicle_id == "_default"
                or stable_id.startswith(("generic_", "ocpp_"))
                or matched_config.get("charger_type") in ("generic", "ocpp")
            ) and not (
                stable_id.startswith(("ble_", "byd_"))
                or (len(stable_id) == 17 and stable_id.isalnum())
            )
            resolved_capacity = resolve_ev_battery_capacity(
                manual_capacity_kwh=(
                    None if anonymous else matched_config.get("battery_capacity_kwh")
                ),
                charger_fallback_capacity_kwh=(
                    matched_config.get("charger_fallback_battery_capacity_kwh")
                    or (
                        matched_config.get("battery_capacity_kwh")
                        if anonymous else None
                    )
                    or config_options.get(CONF_GENERIC_CHARGER_BATTERY_CAPACITY_KWH)
                ),
                provider_capacity_kwh=matched_config.get(
                    "provider_battery_capacity_kwh"
                ),
                model=matched_config.get("vehicle_model", matched_config.get("model")),
                trim=matched_config.get("vehicle_trim", matched_config.get("trim")),
                anonymous_loadpoint=anonymous,
            )

            # Generate plan
            plan = await planner.plan_charging(
                vehicle_id=vehicle_id,
                current_soc=current_soc,
                target_soc=target_soc,
                target_time=target_time,
                priority=priority,
                charger_power_kw=charger_power_kw,
                resolved_capacity=resolved_capacity,
            )

            return web.json_response({
                "success": True,
                "schedule": plan.to_dict(),
            })

        except Exception as e:
            _LOGGER.error(f"Error getting charging schedule: {e}", exc_info=True)
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

    async def post(self, request: web.Request) -> web.Response:
        """Handle POST request - update schedule settings.

        Body:
            vehicle_id: Vehicle to update
            schedule_enabled: Enable/disable scheduled charging
            default_target_soc: Default target SoC
            departure_time: Default departure time (HH:MM)
            departure_days: Days of week for departure (0=Mon)
            priority: Charging priority preference
        """
        try:
            data = await request.json()
            vehicle_id = data.get("vehicle_id", "_default")

            store = self._get_store()
            if not store:
                return web.json_response({
                    "success": False,
                    "error": "Storage not available"
                }, status=503)

            # Get existing schedule settings (use _data directly)
            stored_data = getattr(store, '_data', {}) or {}
            schedules = stored_data.get("charging_schedules", {})
            vehicle_schedule = schedules.get(vehicle_id, {})

            # Update fields
            if "schedule_enabled" in data:
                vehicle_schedule["schedule_enabled"] = bool(data["schedule_enabled"])
            if "default_target_soc" in data:
                vehicle_schedule["default_target_soc"] = max(20, min(100, int(data["default_target_soc"])))
            if "departure_time" in data:
                vehicle_schedule["departure_time"] = data["departure_time"]
            if "departure_days" in data:
                vehicle_schedule["departure_days"] = [int(d) for d in data["departure_days"] if 0 <= int(d) <= 6]
            if "priority" in data:
                valid_priorities = ["solar_only", "solar_preferred", "cost_optimized", "time_critical"]
                if data["priority"] in valid_priorities:
                    vehicle_schedule["priority"] = data["priority"]

            schedules[vehicle_id] = vehicle_schedule

            # Save updated schedules (update key in existing _data, don't overwrite)
            if hasattr(store, '_data') and hasattr(store, 'async_save'):
                store._data["charging_schedules"] = schedules
                await store.async_save()

            return web.json_response({
                "success": True,
                "schedule": vehicle_schedule,
            })

        except Exception as e:
            _LOGGER.error(f"Error updating charging schedule: {e}", exc_info=True)
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

class SurplusForecastView(HomeAssistantView):
    """HTTP view to get solar surplus forecast."""

    url = "/api/power_sync/ev/surplus_forecast"
    name = "api:power_sync:ev:surplus_forecast"
    requires_auth = True

    def __init__(self, hass: HomeAssistant):
        """Initialize the view."""
        self._hass = hass

    async def get(self, request: web.Request) -> web.Response:
        """Handle GET request - get surplus forecast.

        Query parameters:
            hours: Number of hours to forecast (default 24)
        """
        try:
            from ..automations.ev_charging_planner import SurplusForecaster

            hours = int(request.query.get("hours", 24))
            hours = max(1, min(48, hours))  # Limit to 48 hours

            entry = None
            for config_entry in self._hass.config_entries.async_entries(DOMAIN):
                entry = config_entry
                break

            forecaster = SurplusForecaster(self._hass, entry)
            forecast = await forecaster.forecast_surplus(hours)

            return web.json_response({
                "success": True,
                "forecast": [
                    {
                        "hour": f.hour,
                        "solar_kw": round(f.solar_kw, 2),
                        "load_kw": round(f.load_kw, 2),
                        "surplus_kw": round(f.surplus_kw, 2),
                        "confidence": round(f.confidence, 2),
                    }
                    for f in forecast
                ],
            })

        except Exception as e:
            _LOGGER.error(f"Error getting surplus forecast: {e}", exc_info=True)
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

class ChargingBoostView(HomeAssistantView):
    """HTTP view to trigger immediate boost charge."""

    url = "/api/power_sync/ev/boost"
    name = "api:power_sync:ev:boost"
    requires_auth = True

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry):
        """Initialize the view."""
        self._hass = hass
        self._config_entry = config_entry

    async def post(self, request: web.Request) -> web.Response:
        """Handle POST request - start boost charge.

        Body:
            vehicle_id: Vehicle to boost charge
            duration_minutes: Duration of boost (default 60)
            target_soc: Optional target SoC to reach
        """
        try:
            data = await request.json()
            vehicle_id = data.get("vehicle_id")
            try:
                duration_minutes = int(data.get("duration_minutes", 60))
                if not (1 <= duration_minutes <= 1440):
                    return web.json_response(
                        {"success": False, "error": "duration_minutes must be 1-1440"},
                        status=400
                    )
            except (ValueError, TypeError):
                return web.json_response(
                    {"success": False, "error": "Invalid duration_minutes value"},
                    status=400
                )
            target_soc = data.get("target_soc")
            target_soc_value = None
            if target_soc is not None:
                try:
                    target_soc_value = int(target_soc)
                    if not (0 <= target_soc_value <= 100):
                        return web.json_response(
                            {"success": False, "error": "target_soc must be 0-100"},
                            status=400,
                        )
                except (ValueError, TypeError):
                    return web.json_response(
                        {"success": False, "error": "Invalid target_soc value"},
                        status=400,
                    )

            from ..automations.actions import execute_actions

            # Execute start_ev_charging action with max amps
            boost_vehicle_id = vehicle_id if vehicle_id and vehicle_id != "_default" else "_default"
            action_vehicle_vin = None if boost_vehicle_id == "_default" else boost_vehicle_id
            action_params = {
                "vehicle_vin": action_vehicle_vin,
                "skip_ownership": True,
            }
            warnings: list[str] = []

            opts = {**self._config_entry.data, **self._config_entry.options}
            if boost_vehicle_id == "generic_ev":
                from ..const import (
                    CONF_GENERIC_CHARGER_AMPS_ENTITY,
                    CONF_GENERIC_CHARGER_POWER_ENTITY,
                    CONF_GENERIC_CHARGER_STATUS_ENTITY,
                    CONF_GENERIC_CHARGER_SWITCH_ENTITY,
                )

                action_vehicle_vin = None
                action_params.update({
                    "charger_type": "generic",
                    "vehicle_id": "generic_ev",
                    "vehicle_vin": None,
                    "charger_switch_entity": opts.get(CONF_GENERIC_CHARGER_SWITCH_ENTITY, ""),
                    "charger_amps_entity": opts.get(CONF_GENERIC_CHARGER_AMPS_ENTITY, ""),
                    "charger_status_entity": opts.get(CONF_GENERIC_CHARGER_STATUS_ENTITY, ""),
                    "charger_power_entity": opts.get(CONF_GENERIC_CHARGER_POWER_ENTITY, ""),
                })
            elif (
                boost_vehicle_id == "zaptec_standalone"
                or (
                    boost_vehicle_id == "_default"
                    and opts.get(CONF_ZAPTEC_STANDALONE_ENABLED)
                    and opts.get(CONF_ZAPTEC_USERNAME)
                )
            ):
                boost_vehicle_id = "zaptec_standalone"
                action_vehicle_vin = None
                action_params.update({
                    "charger_type": "zaptec",
                    "vehicle_id": "zaptec_standalone",
                    "vehicle_vin": None,
                })

            if target_soc_value is not None and target_soc_value >= 50:
                limit_success = await execute_actions(self._hass, self._config_entry, [{
                    "action_type": "set_ev_charge_limit",
                    "parameters": {
                        **action_params,
                        "percent": target_soc_value,
                    }
                }])
                if not limit_success:
                    warnings.append("Could not set EV charge limit before boost")
            elif target_soc_value is not None:
                warnings.append("EV charge limit not set because target SoC is below 50%")

            actions = [{
                "action_type": "start_ev_charging",
                "parameters": {
                    **action_params,
                    "amps": 32,
                }
            }]

            success = await execute_actions(self._hass, self._config_entry, actions)

            if success:
                # Also set to max charging amps
                amps_actions = [{
                    "action_type": "set_ev_charging_amps",
                    "parameters": {
                        **action_params,
                        "amps": 32,  # Max standard amps
                    }
                }]
                amps_success = await execute_actions(self._hass, self._config_entry, amps_actions)
                if not amps_success:
                    warnings.append("Could not set EV charging amps before boost")

                from ..automations.ev_ownership import (
                    claim_ev_ownership,
                    get_active_ev_owner_mode,
                    owner_family,
                    record_ev_command,
                    release_ev_ownership,
                )

                entry_data = self._hass.data.setdefault(DOMAIN, {}).setdefault(
                    self._config_entry.entry_id,
                    {},
                )
                if cancel_boost := entry_data.get("ev_boost_cancel"):
                    cancel_boost()
                    entry_data["ev_boost_cancel"] = None

                claim_ev_ownership(
                    self._hass,
                    self._config_entry,
                    boost_vehicle_id,
                    owner_mode="boost",
                    command="start_boost",
                    reason=f"Boost charge for {duration_minutes} minutes",
                    extra={
                        "duration_minutes": duration_minutes,
                        "target_soc": target_soc_value,
                    },
                )

                async def _stop_boost_when_elapsed(_now) -> None:
                    entry_data = self._hass.data.get(DOMAIN, {}).get(
                        self._config_entry.entry_id,
                        {},
                    )
                    entry_data.pop("ev_boost_cancel", None)
                    active_mode = get_active_ev_owner_mode(
                        self._hass,
                        self._config_entry,
                        boost_vehicle_id,
                    )
                    if owner_family(active_mode) != "boost":
                        _LOGGER.info(
                            "Boost stop skipped for %s because ownership is now %s",
                            boost_vehicle_id,
                            active_mode or "unowned",
                        )
                        return

                    stop_success = await execute_actions(self._hass, self._config_entry, [{
                        "action_type": "stop_ev_charging",
                        "parameters": {
                            **action_params,
                        },
                    }])
                    if stop_success:
                        release_ev_ownership(
                            self._hass,
                            self._config_entry,
                            boost_vehicle_id,
                            command="stop_boost",
                            reason="Boost duration elapsed",
                        )
                    else:
                        record_ev_command(
                            self._hass,
                            self._config_entry,
                            boost_vehicle_id,
                            command="stop_boost",
                            success=False,
                            reason="Boost duration elapsed but stop command failed",
                        )

                stops_at = dt_util.utcnow() + timedelta(minutes=duration_minutes)
                entry_data["ev_boost_cancel"] = async_track_point_in_utc_time(
                    self._hass,
                    _stop_boost_when_elapsed,
                    stops_at,
                )

            return web.json_response({
                "success": success,
                "message": "Boost charge started" if success else "Failed to start boost charge",
                "duration_minutes": duration_minutes,
                "target_soc": target_soc_value,
                "stops_at": stops_at.isoformat() if success else None,
                "warnings": warnings,
            })

        except Exception as e:
            _LOGGER.error(f"Error starting boost charge: {e}", exc_info=True)
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

class OCPPChargersView(HomeAssistantView):
    """HTTP view to get OCPP charger status for mobile app."""

    url = "/api/power_sync/ev/ocpp_chargers"
    name = "api:power_sync:ev:ocpp_chargers"
    requires_auth = True

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._hass = hass
        self._entry = entry

    async def get(self, request):
        """Get OCPP charger list and status."""
        import re
        from homeassistant.helpers import entity_registry as er
        from ..automations.ocpp_status import (
            is_ocpp_charging,
            is_ocpp_hardware_online,
            is_ocpp_vehicle_present,
            normalize_ocpp_status,
        )

        entity_reg = er.async_get(self._hass)

        # Detect OCPP chargers from the HACS OCPP integration via entity registry.
        # Non-greedy (\w+?) with end anchor ensures the prefix is everything before
        # the matched suffix — e.g. switch.evse001_charge_control → prefix "evse001",
        # not "evse001_charge" (which rsplit("_",1) would incorrectly produce).
        suffix_pattern = re.compile(
            r"^(sensor|switch|number)\.(\w+?)_(status_connector|status|availability|charge_control|current_power|power_active_import|power_offered|energy_meter|energy_active_import_register|energy_active_import_interval|energy_session|maximum_current)$",
            re.IGNORECASE,
        )
        charger_ids = set()
        for reg_entry in entity_reg.entities.values():
            if reg_entry.platform != "ocpp":
                continue
            m = suffix_pattern.match(reg_entry.entity_id)
            if m:
                charger_ids.add(m.group(2))

        _LOGGER.debug(
            "OCPP charger detection: %d platform=ocpp entities, prefixes=%s",
            sum(1 for e in entity_reg.entities.values() if e.platform == "ocpp"),
            sorted(charger_ids),
        )

        chargers = []
        entry_data = self._hass.data.get(DOMAIN, {}).get(self._entry.entry_id, {})
        ocpp_enabled = self._entry.options.get(
            CONF_OCPP_ENABLED,
            self._entry.data.get(CONF_OCPP_ENABLED, False)
        )

        # Build charger list from detected entities
        for idx, cid in enumerate(sorted(charger_ids)):
            status_entity = f"sensor.{cid}_status"
            # lbbrhzn/ocpp exposes sensor.*_status (charge-point level) and
            # sensor.*_status_connector (connector level).  The charge-point sensor
            # often stays "unknown" while the connector sensor is reliably updated,
            # so fall back to it when the primary status is unknown/unavailable.
            status_connector_entity = f"sensor.{cid}_status_connector"
            power_suffixes = (
                "_current_power",
                "_power_active_import",
            )
            energy_suffixes = (
                "_energy_meter",
                "_energy_active_import_register",
                "_energy_active_import_interval",
                "_energy_session",
            )

            status_state = self._hass.states.get(status_entity)
            status_connector_state = self._hass.states.get(status_connector_entity)
            power_state = next(
                (
                    self._hass.states.get(f"sensor.{cid}{suffix}")
                    for suffix in power_suffixes
                    if self._hass.states.get(f"sensor.{cid}{suffix}") is not None
                ),
                None,
            )
            energy_state = next(
                (
                    self._hass.states.get(f"sensor.{cid}{suffix}")
                    for suffix in energy_suffixes
                    if self._hass.states.get(f"sensor.{cid}{suffix}") is not None
                ),
                None,
            )

            effective_status = None
            if status_state and status_state.state not in ("unavailable", "unknown"):
                effective_status = status_state.state
            elif status_connector_state and status_connector_state.state not in ("unavailable", "unknown"):
                effective_status = status_connector_state.state
            status = effective_status if effective_status else "Unavailable"

            power_kw = 0.0
            energy_kwh = 0.0

            if power_state and power_state.state not in ("unavailable", "unknown"):
                try:
                    raw_power = float(power_state.state)
                    power_kw = raw_power / 1000 if raw_power > 100 else raw_power
                except (ValueError, TypeError):
                    pass

            if energy_state and energy_state.state not in ("unavailable", "unknown"):
                try:
                    energy_kwh = float(energy_state.state)
                except (ValueError, TypeError):
                    pass

            normalized_status = normalize_ocpp_status(status)
            is_connected = is_ocpp_hardware_online(status)
            is_vehicle_connected = is_ocpp_vehicle_present(status, power_kw * 1000)
            is_charging = is_ocpp_charging(status, power_kw * 1000)

            chargers.append({
                "id": idx + 1,
                "user_id": 1,
                "charger_id": cid,
                "vendor": "",
                "model": "",
                "serial_number": "",
                "firmware_version": "",
                "is_connected": is_connected,
                "is_vehicle_connected": is_vehicle_connected,
                "is_charging": is_charging,
                "status": status,
                "normalized_status": normalized_status,
                "current_power_kw": round(power_kw, 2),
                "energy_kwh": round(energy_kwh, 2),
            })

        # If no chargers detected from entities but OCPP is enabled, show a placeholder
        if not chargers and ocpp_enabled:
            chargers.append({
                "id": 1,
                "user_id": 1,
                "charger_id": "ocpp_charger",
                "vendor": "",
                "model": "OCPP Charger",
                "serial_number": "",
                "firmware_version": "",
                "is_connected": False,
                "status": "Waiting for connection",
                "current_power_kw": 0,
                "energy_kwh": 0,
            })

        return web.json_response({
            "success": True,
            "chargers": chargers,
        })

class OCPPChargerStartView(HomeAssistantView):
    """HTTP view to start charging on an OCPP charger."""

    url = "/api/power_sync/ev/ocpp_chargers/start"
    name = "api:power_sync:ev:ocpp_chargers:start"
    requires_auth = True

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._hass = hass
        self._entry = entry

    async def post(self, request: web.Request) -> web.Response:
        """Start charging on an OCPP charger.

        Body:
            charger_id: The charger ID prefix (e.g. 'my_charger')
        """
        try:
            data = await request.json()
            charger_id = data.get("charger_id")
            if not charger_id:
                return web.json_response(
                    {"success": False, "error": "charger_id is required"},
                    status=400,
                )

            entity_id = f"switch.{charger_id}_charge_control"
            state = self._hass.states.get(entity_id)
            if state is None:
                return web.json_response(
                    {"success": False, "error": f"Entity {entity_id} not found"},
                    status=404,
                )

            from ..automations.actions import _execute_single_action

            success = await _execute_single_action(
                self._hass,
                self._entry,
                "start_ev_charging",
                {
                    "charger_type": "ocpp",
                    "ocpp_charger_id": charger_id,
                    "vehicle_id": f"ocpp_{charger_id}",
                    "reason": "Manual OCPP start from mobile",
                },
            )
            if not success:
                return web.json_response(
                    {"success": False, "error": f"Failed to start charging on {charger_id}"},
                    status=500,
                )

            _LOGGER.info(
                "OCPP charger %s: start charging via shared action layer",
                charger_id,
            )
            return web.json_response({
                "success": True,
                "message": f"Charging started on {charger_id}",
            })

        except Exception as e:
            _LOGGER.error("Error starting OCPP charger: %s", e, exc_info=True)
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500,
            )

class OCPPChargerStopView(HomeAssistantView):
    """HTTP view to stop charging on an OCPP charger."""

    url = "/api/power_sync/ev/ocpp_chargers/stop"
    name = "api:power_sync:ev:ocpp_chargers:stop"
    requires_auth = True

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._hass = hass
        self._entry = entry

    async def post(self, request: web.Request) -> web.Response:
        """Stop charging on an OCPP charger.

        Body:
            charger_id: The charger ID prefix (e.g. 'my_charger')
        """
        try:
            data = await request.json()
            charger_id = data.get("charger_id")
            if not charger_id:
                return web.json_response(
                    {"success": False, "error": "charger_id is required"},
                    status=400,
                )

            entity_id = f"switch.{charger_id}_charge_control"
            state = self._hass.states.get(entity_id)
            if state is None:
                return web.json_response(
                    {"success": False, "error": f"Entity {entity_id} not found"},
                    status=404,
                )

            from ..automations.actions import _execute_single_action

            success = await _execute_single_action(
                self._hass,
                self._entry,
                "stop_ev_charging",
                {
                    "charger_type": "ocpp",
                    "ocpp_charger_id": charger_id,
                    "vehicle_id": f"ocpp_{charger_id}",
                    "reason": "Manual OCPP stop from mobile",
                },
            )
            if not success:
                return web.json_response(
                    {"success": False, "error": f"Failed to stop charging on {charger_id}"},
                    status=500,
                )

            _LOGGER.info("OCPP charger %s: stop charging via shared action layer", charger_id)
            return web.json_response({
                "success": True,
                "message": f"Charging stopped on {charger_id}",
            })

        except Exception as e:
            _LOGGER.error("Error stopping OCPP charger: %s", e, exc_info=True)
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500,
            )

class EVWidgetDataView(HomeAssistantView):
    """API endpoint for EV widget data (home screen widgets).

    GET /api/power_sync/ev/widget_data
    Returns compact data suitable for home screen widgets.
    """
    url = "/api/power_sync/ev/widget_data"
    name = "api:power_sync:ev:widget_data"
    requires_auth = True

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._hass = hass
        self._config_entry = entry

    async def get(self, request):
        """Get widget data for EV charging status."""
        try:
            from ..automations.actions import (
                DEFAULT_VEHICLE_ID,
                _dynamic_ev_state,
                _calculate_solar_surplus,
            )
            from ..automations.ev_charging_planner import (
                _vehicle_config_matches,
                get_auto_schedule_executor,
            )
            from ..solar_surplus_config import get_stored_solar_surplus_config

            entry_id = self._config_entry.entry_id
            entry_data = self._hass.data.get(DOMAIN, {}).get(entry_id, {})

            # Get data from coordinator (preferred over separate API call)
            solar_power_kw = 0.0
            grid_power_kw = 0.0
            battery_power_kw = 0.0
            load_power_kw = 0.0
            battery_soc = 0.0

            tesla_coordinator = entry_data.get("tesla_coordinator")
            sigenergy_coordinator = entry_data.get("sigenergy_coordinator")
            sungrow_coordinator = entry_data.get("sungrow_coordinator")
            foxess_coordinator = entry_data.get("foxess_coordinator")

            if tesla_coordinator and tesla_coordinator.data:
                solar_power_kw = tesla_coordinator.data.get("solar_power", 0)
                grid_power_kw = tesla_coordinator.data.get("grid_power", 0)
                battery_power_kw = tesla_coordinator.data.get("battery_power", 0)
                load_power_kw = tesla_coordinator.data.get("load_power", 0)
                battery_soc = tesla_coordinator.data.get("battery_level", 0)
            elif sigenergy_coordinator and sigenergy_coordinator.data:
                solar_power_kw = sigenergy_coordinator.data.get("solar_power", 0)
                grid_power_kw = sigenergy_coordinator.data.get("grid_power", 0)
                battery_power_kw = sigenergy_coordinator.data.get("battery_power", 0)
                load_power_kw = sigenergy_coordinator.data.get("load_power", 0)
                battery_soc = sigenergy_coordinator.data.get("battery_level", 0)
            elif sungrow_coordinator and sungrow_coordinator.data:
                solar_power_kw = sungrow_coordinator.data.get("solar_power", 0)
                grid_power_kw = sungrow_coordinator.data.get("grid_power", 0)
                battery_power_kw = sungrow_coordinator.data.get("battery_power", 0)
                load_power_kw = sungrow_coordinator.data.get("load_power", 0)
                battery_soc = sungrow_coordinator.data.get("battery_level", 0)
            elif foxess_coordinator and foxess_coordinator.data:
                solar_power_kw = foxess_coordinator.data.get("solar_power", 0)
                grid_power_kw = foxess_coordinator.data.get("grid_power", 0)
                battery_power_kw = foxess_coordinator.data.get("battery_power", 0)
                load_power_kw = foxess_coordinator.data.get("load_power", 0)
                battery_soc = foxess_coordinator.data.get("battery_level", 0)

            # Build live_status dict for surplus calculation (expects watts)
            live_status = {
                "solar_power": solar_power_kw * 1000,
                "grid_power": grid_power_kw * 1000,
                "battery_power": battery_power_kw * 1000,
                "load_power": load_power_kw * 1000,
                "battery_soc": battery_soc,
            }

            # Calculate current surplus
            solar_config = get_stored_solar_surplus_config(entry_data)
            surplus_kw = _calculate_solar_surplus(live_status, 0, solar_config)

            # Get actual EV power from Tesla Wall Connector API (ground truth)
            # This prevents showing phantom power when commanded amps > 0 but car isn't
            # actually charging (e.g. not plugged in, or charge command didn't take effect)
            actual_ev_power_kw = None  # None = no Tesla data available
            if tesla_coordinator and tesla_coordinator.data:
                actual_ev_power_kw = _kw_from_wall_connector_power(
                    tesla_coordinator.data.get("ev_power", 0)
                )

            # Per-vehicle Tesla telemetry is the source of truth for assigning
            # charge/connection state in multi-vehicle accounts.
            tesla_vehicles = _get_ev_vehicles_status(self._hass, self._config_entry)

            # Get dynamic EV state
            vehicles = _dynamic_ev_state.get(entry_id, {})
            auto_executor = get_auto_schedule_executor()

            widget_data = []
            for vehicle_id, state in vehicles.items():
                if not state.get("active"):
                    continue

                params = state.get("params", {})
                vehicle_name = (
                    params.get("vehicle_name")
                    or state.get("vehicle_name")
                    or params.get("display_name")
                    or (vehicle_id[:8] if len(vehicle_id) > 8 else vehicle_id)
                )
                matched_vehicle = _find_vehicle_status(
                    tesla_vehicles,
                    vehicle_id,
                    params.get("vehicle_vin"),
                    params.get("vehicle_id"),
                    vehicle_name,
                )
                charger_type = params.get("charger_type", "tesla")
                is_tesla_vehicle = charger_type == "tesla" or matched_vehicle is not None

                # Skip the legacy "_default" placeholder when no display name
                # is set. It is internal bookkeeping (single-vehicle/manual
                # paths) and would otherwise render as "_DEFAULT" in the
                # mobile app alongside the real vehicle, which is added later
                # via the tesla_vehicles / BYD discovery paths.
                if (
                    vehicle_id == DEFAULT_VEHICLE_ID
                    and not (params.get("vehicle_name") or state.get("vehicle_name"))
                ):
                    continue

                current_amps = state.get("current_amps", 0)
                voltage = params.get("voltage", 240)
                phases = params.get("phases", 1)
                commanded_power_kw = (current_amps * voltage * phases) / 1000

                observed_power_kw = None
                observed_connected = False
                observed_charging = False
                if matched_vehicle is not None:
                    try:
                        observed_power_kw = float(matched_vehicle.get("ev_power_kw") or 0)
                    except (TypeError, ValueError):
                        observed_power_kw = 0.0
                    observed_charging = (
                        bool(matched_vehicle.get("is_charging"))
                        or observed_power_kw > 0.05
                    )
                    observed_connected = (
                        bool(matched_vehicle.get("is_connected"))
                        or observed_charging
                    )

                # Cross-check with actual per-vehicle telemetry for Tesla systems.
                # Commanded amps can be non-zero when car isn't plugged in
                if matched_vehicle is not None:
                    current_power_kw = observed_power_kw or 0.0
                    actually_charging = observed_charging
                elif is_tesla_vehicle and actual_ev_power_kw is not None:
                    # With multiple Teslas, global Wall Connector power cannot
                    # safely identify which active session owns the connected car.
                    if len(tesla_vehicles) <= 1 and actual_ev_power_kw > 0.05:
                        current_power_kw = actual_ev_power_kw
                        actually_charging = True
                    else:
                        current_power_kw = 0.0
                        actually_charging = False
                else:
                    # Non-Tesla: trust commanded amps (no WC API available)
                    current_power_kw = commanded_power_kw
                    actually_charging = current_amps > 0

                # Determine charging source
                if current_amps == 0 or not actually_charging:
                    source = "idle"
                elif state.get("allocated_surplus_kw", 0) >= current_power_kw * 0.8:
                    source = "solar"
                else:
                    source = "grid"

                # Get vehicle SoC from BLE/Fleet sensors
                current_soc = 0
                target_soc = params.get("target_soc", 80)
                if matched_vehicle is not None and matched_vehicle.get("ev_soc") is not None:
                    current_soc = matched_vehicle.get("ev_soc") or 0
                else:
                    try:
                        ev_status = _get_ev_vehicle_status(self._hass, self._config_entry)
                        current_soc = ev_status.get("ev_soc") or 0
                    except Exception:
                        pass

                # Estimate ETA (rough calculation)
                effective_capacity_kwh = params.get(
                    "effective_battery_capacity_kwh",
                    params.get("battery_capacity_kwh", 60),
                )
                capacity_source = params.get(
                    "battery_capacity_source", "default_estimate"
                )
                manual_capacity_kwh = params.get("battery_capacity_kwh")
                if auto_executor:
                    matched_settings_id = next(
                        (
                            settings_id for settings_id in auto_executor._settings
                            if _vehicle_config_matches(settings_id, vehicle_id)
                        ),
                        None,
                    )
                    if matched_settings_id is not None:
                        resolved_capacity = auto_executor.resolve_vehicle_capacity(
                            matched_settings_id,
                            auto_executor._settings[matched_settings_id],
                        )
                        effective_capacity_kwh = (
                            resolved_capacity.effective_battery_capacity_kwh
                        )
                        capacity_source = resolved_capacity.battery_capacity_source
                        manual_capacity_kwh = resolved_capacity.battery_capacity_kwh
                eta_minutes = None
                if current_power_kw > 0 and target_soc > current_soc:
                    energy_needed_kwh = (
                        (target_soc - current_soc)
                        / 100
                        * effective_capacity_kwh
                        / 0.9
                    )
                    eta_minutes = int(energy_needed_kwh / current_power_kw * 60)

                # Determine connected status. For Tesla, only use the matched
                # vehicle's own sensors; global WC/charge-cable signals can
                # belong to another car on the account.
                if matched_vehicle is not None:
                    is_connected = observed_connected
                else:
                    is_connected = actually_charging

                allow_global_connection_fallback = (
                    not is_tesla_vehicle or len(tesla_vehicles) <= 1
                )
                if not is_connected and allow_global_connection_fallback:
                    # Check Tesla WC state from coordinator (state 4 = connected)
                    if tesla_coordinator and tesla_coordinator.data:
                        wc_data = tesla_coordinator.data.get("wall_connectors_raw")
                        if isinstance(wc_data, list):
                            for wc in wc_data:
                                if wc.get("wall_connector_state") in (2, 4, 6, 7, 11):
                                    is_connected = True
                                    break
                if not is_connected and allow_global_connection_fallback:
                    # Check charge_cable / charge_flap binary sensors
                    for pattern in ("charge_cable", "charge_flap"):
                        for state_obj in self._hass.states.async_all("binary_sensor"):
                            if pattern in state_obj.entity_id and state_obj.state == "on":
                                is_connected = True
                                break
                        if is_connected:
                            break
                if not is_connected and allow_global_connection_fallback:
                    # Check HA wall_connector vehicle sensors
                    for wc_state in self._hass.states.async_all("sensor"):
                        wc_eid = wc_state.entity_id.lower()
                        if "wall_connector" in wc_eid and "vehicle" in wc_eid and "power" not in wc_eid:
                            if wc_state.state.lower() not in ("disconnected", "unknown", "unavailable", ""):
                                is_connected = True
                                break

                widget_data.append({
                    "vehicle_name": vehicle_name,
                    "is_charging": actually_charging,
                    "is_connected": is_connected,
                    "current_soc": current_soc,
                    "target_soc": target_soc,
                    "current_power_kw": round(current_power_kw, 2),
                    "source": source,
                    "eta_minutes": eta_minutes,
                    "battery_capacity_kwh": manual_capacity_kwh,
                    "effective_battery_capacity_kwh": effective_capacity_kwh,
                    "battery_capacity_source": capacity_source,
                    "surplus_kw": round(surplus_kw, 2),
                })

            # Always check external chargers (Zaptec, OCPP) regardless of dynamic EV state
            # This ensures standalone chargers appear even when idle vehicles exist
            zaptec_cached = entry_data.get("zaptec_cached_state")
            if zaptec_cached:
                zaptec_mode = zaptec_cached.get("charger_operation_mode", "")
                zaptec_connected = zaptec_mode in ("charging", "connected_waiting", "connected_finishing")
                power_w = zaptec_cached.get("total_charge_power_w", 0)
                power_kw = power_w / 1000
                zaptec_charging = power_kw > 0.05 or zaptec_mode == "charging"
                if zaptec_connected or zaptec_charging:
                    if power_kw > 0.05:
                        if surplus_kw >= power_kw * 0.8:
                            zaptec_source = "solar"
                        else:
                            zaptec_source = "grid"
                    else:
                        zaptec_source = "idle"
                    widget_data.append({
                        "vehicle_name": "EV Charger",
                        "is_charging": zaptec_charging,
                        "is_connected": zaptec_connected,
                        "current_soc": 0,
                        "target_soc": 80,
                        "current_power_kw": round(power_kw, 2),
                        "source": zaptec_source,
                        "eta_minutes": None,
                        "surplus_kw": round(surplus_kw, 2),
                    })

            configured_sigenergy_state = _configured_sigenergy_charger_state(self._config_entry)
            if configured_sigenergy_state:
                from ..sigenergy_charger import sigenergy_charger_state_to_widget

                sigenergy_state = await _read_sigenergy_charger_state_for_entry(
                    self._config_entry,
                    self._hass,
                )
                widget_data.append(
                    sigenergy_charger_state_to_widget(
                        sigenergy_state or configured_sigenergy_state,
                        surplus_kw=surplus_kw,
                        capabilities=_configured_sigenergy_charger_capabilities(
                            self._config_entry,
                            self._hass,
                        ),
                    )
                )

            # Check OCPP chargers — built-in server
            ocpp_server = entry_data.get("ocpp_server")
            if ocpp_server:
                try:
                    for cp_id, cp in ocpp_server.charge_points.items():
                        meter_w = getattr(cp, 'meter_power_w', 0) or 0
                        power_kw = meter_w / 1000
                        is_charging = power_kw > 0.05
                        is_connected = is_charging or (hasattr(cp, 'active_transaction') and cp.active_transaction)
                        if is_connected or is_charging:
                            ocpp_source = "solar" if surplus_kw >= power_kw * 0.8 else "grid"
                            widget_data.append({
                                "vehicle_name": f"OCPP {cp_id[:8]}",
                                "is_charging": is_charging,
                                "is_connected": True,
                                "current_soc": 0,
                                "target_soc": 80,
                                "current_power_kw": round(power_kw, 2),
                                "source": ocpp_source if is_charging else "idle",
                                "eta_minutes": None,
                                "surplus_kw": round(surplus_kw, 2),
                            })
                except Exception as e:
                    _LOGGER.debug(f"Error checking built-in OCPP server for widget: {e}")

            # Check HACS OCPP integration entities
            # Looks for sensor.*_status, sensor.*_current_power from the ocpp platform
            try:
                from homeassistant.helpers import entity_registry as er
                from ..automations.ocpp_status import (
                    extract_hacs_ocpp_prefix,
                    is_hacs_ocpp_power_entity,
                    is_hacs_ocpp_status_entity,
                    is_ocpp_charging,
                    is_ocpp_vehicle_present,
                    normalize_ocpp_status,
                )
                ent_reg = er.async_get(self._hass)
                ocpp_chargers_found = {}
                for entity in ent_reg.entities.values():
                    if entity.platform != "ocpp":
                        continue
                    eid = entity.entity_id.lower()
                    state = self._hass.states.get(entity.entity_id)
                    if not state or state.state in ("unknown", "unavailable"):
                        continue
                    # Extract charger prefix (e.g. "my_charger" from
                    # "sensor.my_charger_status_connector").
                    prefix = extract_hacs_ocpp_prefix(eid)
                    if not prefix:
                        continue
                    if prefix not in ocpp_chargers_found:
                        ocpp_chargers_found[prefix] = {"name": prefix, "status": None, "power_kw": 0, "connected": False, "charging": False}
                    charger = ocpp_chargers_found[prefix]
                    if is_hacs_ocpp_status_entity(eid):
                        if eid.endswith("_status_connector") or charger["status"] is None:
                            charger["status"] = normalize_ocpp_status(state.state)
                            charger["connected"] = is_ocpp_vehicle_present(state.state)
                            charger["charging"] = is_ocpp_charging(state.state)
                    elif is_hacs_ocpp_power_entity(eid):
                        try:
                            pwr = float(state.state)
                            charger["power_kw"] = pwr / 1000 if pwr > 100 else pwr  # W or kW
                            power_w = pwr if pwr > 100 else pwr * 1000
                            charger["charging"] = charger["charging"] or power_w > 50
                            charger["connected"] = charger["connected"] or power_w > 50
                        except (ValueError, TypeError):
                            pass

                for prefix, charger in ocpp_chargers_found.items():
                    # Skip if already added from built-in OCPP server
                    if any(prefix in (w.get("vehicle_name", "").lower()) for w in widget_data):
                        continue
                    if charger["connected"] or charger["charging"]:
                        ocpp_source = "solar" if surplus_kw >= charger["power_kw"] * 0.8 else "grid"
                        widget_data.append({
                            "vehicle_name": f"OCPP {charger['name'][:12]}",
                            "is_charging": charger["charging"],
                            "is_connected": charger["connected"],
                            "current_soc": 0,
                            "target_soc": 80,
                            "current_power_kw": round(charger["power_kw"], 2),
                            "source": ocpp_source if charger["charging"] else "idle",
                            "eta_minutes": None,
                            "surplus_kw": round(surplus_kw, 2),
                        })
                        _LOGGER.debug("EV widget: HACS OCPP charger %s (status=%s, power=%.1fkW)", prefix, charger["status"], charger["power_kw"])
            except Exception as e:
                _LOGGER.debug(f"Error checking HACS OCPP integration for widget: {e}")

            # Check Tesla EV vehicles — per-vehicle data (power, SOC, connected status)
            # Supplement connected status from BLE/Fleet presence sensors
            # (same detection as HA dashboard strategy: charge_flap, charge_cable)
            # The device-based scan may miss BLE entities if they're on a different device
            for tv in tesla_vehicles:
                if tv["is_connected"]:
                    continue
                # Search for presence sensors matching this vehicle's name
                vname = (tv.get("vehicle_name") or "").lower().replace(" ", "_")
                if not vname:
                    continue
                for eid_suffix in ["_charge_flap", "_charge_cable", "_charging_state"]:
                    for prefix in [vname, vname.replace("-", "_")]:
                        sensor_domain = "binary_sensor" if eid_suffix in ("_charge_flap", "_charge_cable") else "sensor"
                        eid = f"{sensor_domain}.{prefix}{eid_suffix}"
                        state = self._hass.states.get(eid)
                        if state and state.state not in ("unknown", "unavailable"):
                            if sensor_domain == "binary_sensor" and state.state == "on":
                                tv["is_connected"] = True
                                _LOGGER.debug("EV widget: %s connected via %s", tv["vehicle_name"], eid)
                                break
                            elif sensor_domain == "sensor" and state.state.lower() in ("charging", "connected", "stopped", "complete"):
                                tv["is_connected"] = True
                                _LOGGER.debug("EV widget: %s connected via %s=%s", tv["vehicle_name"], eid, state.state)
                                break
                    if tv["is_connected"]:
                        break

            # Check Teslemetry/Fleet API sensors (binary_sensor.*_charge_cable, sensor.*_charging)
            for state in self._hass.states.async_all():
                eid = state.entity_id.lower()
                if state.state in ("unknown", "unavailable"):
                    continue
                # charge_cable (Teslemetry/Fleet) — "on" = plugged in
                if eid.startswith("binary_sensor.") and eid.endswith("_charge_cable") and "power_sync" not in eid:
                    if state.state == "on":
                        # Extract vehicle name from entity: binary_sensor.tessy_charge_cable → tessy
                        vname = eid.replace("binary_sensor.", "").replace("_charge_cable", "")
                        matched = False
                        for tv in tesla_vehicles:
                            tv_name = (tv.get("vehicle_name") or "").lower().replace(" ", "_")
                            if tv_name == vname or vname in tv_name or tv_name in vname:
                                if not tv["is_connected"]:
                                    tv["is_connected"] = True
                                    _LOGGER.debug("EV widget: %s connected via Teslemetry %s", tv["vehicle_name"], eid)
                                matched = True
                                break
                        if not matched:
                            # Check for SOC
                            soc_eid = f"sensor.{vname}_battery_level"
                            soc_state = self._hass.states.get(soc_eid)
                            soc = None
                            if soc_state and soc_state.state not in ("unknown", "unavailable"):
                                try:
                                    soc = int(float(soc_state.state))
                                except (ValueError, TypeError):
                                    pass
                            tesla_vehicles.append({
                                "vehicle_name": vname.replace("_", " ").title(),
                                "ev_power_kw": 0,
                                "ev_soc": soc,
                                "is_connected": True,
                                "is_charging": False,
                            })
                            _LOGGER.debug("EV widget: added Teslemetry vehicle %s via %s", vname, eid)

            # Also check BLE prefix entities (teslable_charge_flap etc)
            ble_prefix = self._config_entry.options.get("ble_prefix", self._config_entry.data.get("ble_prefix", ""))
            if ble_prefix:
                for prefix in ble_prefix.split(","):
                    prefix = prefix.strip()
                    if not prefix:
                        continue
                    flap = self._hass.states.get(f"binary_sensor.{prefix}_charge_flap")
                    if flap and flap.state == "on":
                        # Find matching vehicle or add as new
                        matched = False
                        for tv in tesla_vehicles:
                            if not tv["is_connected"]:
                                tv["is_connected"] = True
                                _LOGGER.debug("EV widget: %s connected via BLE %s_charge_flap", tv["vehicle_name"], prefix)
                                matched = True
                                break
                        if not matched and not any(tv["is_connected"] for tv in tesla_vehicles):
                            # BLE shows connected but no matching vehicle — add one
                            soc_state = self._hass.states.get(f"sensor.{prefix}_charge_level")
                            soc = None
                            if soc_state and soc_state.state not in ("unknown", "unavailable"):
                                try:
                                    soc = int(float(soc_state.state))
                                except (ValueError, TypeError):
                                    pass
                            tesla_vehicles.append({
                                "vehicle_name": prefix.replace("_", " ").title(),
                                "ev_power_kw": 0,
                                "ev_soc": soc,
                                "is_connected": True,
                                "is_charging": False,
                            })
                            _LOGGER.debug("EV widget: added BLE vehicle %s (connected, not charging)", prefix)

            _LOGGER.debug("EV widget: found %d vehicles: %s", len(tesla_vehicles), [{k: v for k, v in tv.items() if k != 'ev_power_kw'} for tv in tesla_vehicles])

            # Supplement from Wall Connector data (Tesla live_status)
            # WC state 4 = connected/ready, state 2 = charging
            if tesla_coordinator and tesla_coordinator.data:
                wc_data = (
                    tesla_coordinator.data.get("wall_connectors_raw")
                    or tesla_coordinator.data.get("wall_connectors")
                )
                for wc in _wall_connector_records(wc_data):
                    wc_state = wc.get("wall_connector_state", 0)
                    wc_pwr = _kw_from_wall_connector_power(
                        wc.get("wall_connector_power", wc.get("power"))
                    )
                    wc_vin = wc.get("vin") or wc.get("vehicle_vin")
                    # State 2=charging, 4=connected/ready, 6=ready, 1=disconnected
                    wc_connected = wc_state in (2, 4, 6, 7, 11) or wc_pwr > 0.05
                    wc_charging = wc_pwr > 0.05 or wc_state == 2

                    if wc_connected:
                        matched = _apply_wall_connector_observation(
                            tesla_vehicles,
                            wc_pwr,
                            wc_connected,
                            wc_charging,
                            wc_vin,
                        )
                        if matched:
                            _LOGGER.debug(
                                "EV widget: Wall Connector matched vehicle (state=%d, power=%.1fkW, vin=%s)",
                                wc_state,
                                wc_pwr,
                                "present" if wc_vin else "unknown",
                            )
                        elif not tesla_vehicles:
                            tesla_vehicles.append({
                                "vehicle_id": "wall_connector",
                                "vehicle_name": "Tesla EV",
                                "ev_power_kw": wc_pwr,
                                "ev_soc": None,
                                "is_connected": True,
                                "is_charging": wc_charging,
                            })

                # Legacy fallback: ev_power sensor only
                wc_power = _kw_from_wall_connector_power(
                    tesla_coordinator.data.get("ev_power", 0)
                )
                if not tesla_vehicles and wc_power > 0.05:
                    tesla_vehicles = [{
                        "vehicle_id": "wall_connector",
                        "vehicle_name": "Tesla EV",
                        "ev_power_kw": wc_power,
                        "ev_soc": None,
                        "is_connected": True,
                        "is_charging": True,
                    }]

            # Add all known Tesla vehicles to the widget — even idle ones.
            # Skip vehicles already represented in widget_data (by name) to avoid duplicates.
            existing_names = {(w.get("vehicle_name") or "").lower() for w in widget_data}
            for tv in tesla_vehicles:
                if (tv.get("vehicle_name") or "").lower() in existing_names:
                    continue
                tv_power_kw = tv["ev_power_kw"]
                tv_soc = tv["ev_soc"]
                if tv_power_kw > 0.05:
                    if surplus_kw >= tv_power_kw * 0.8:
                        tv_source = "solar"
                    else:
                        tv_source = "grid"
                else:
                    tv_source = "idle"
                widget_data.append({
                    "vehicle_name": tv["vehicle_name"],
                    "is_charging": tv["is_charging"],
                    "is_connected": tv["is_connected"],
                    "current_soc": tv_soc if tv_soc is not None else 0,
                    "target_soc": 80,
                    "current_power_kw": round(tv_power_kw, 2),
                    "source": tv_source,
                    "eta_minutes": None,
                    "surplus_kw": round(surplus_kw, 2),
                })

            # Check BYD vehicles
            if BYD_INTEGRATION in self._hass.config_entries.async_domains():
                byd_device_registry = dr.async_get(self._hass)
                byd_entity_registry = er.async_get(self._hass)
                for byd_device in byd_device_registry.devices.values():
                    is_byd = any(i[0] == BYD_INTEGRATION for i in byd_device.identifiers)
                    if not is_byd:
                        continue
                    byd_soc = 0
                    byd_charging = False
                    byd_connected = False
                    for byd_entity in byd_entity_registry.entities.values():
                        if byd_entity.device_id != byd_device.id:
                            continue
                        byd_state = self._hass.states.get(byd_entity.entity_id)
                        if not byd_state or byd_state.state in ("unknown", "unavailable"):
                            continue
                        eid = byd_entity.entity_id.lower()
                        if eid.startswith("sensor.") and "battery_level" in eid:
                            try:
                                byd_soc = int(float(byd_state.state))
                            except (ValueError, TypeError):
                                pass
                        if eid.startswith("binary_sensor.") and "charging" in eid and "charger" not in eid:
                            byd_charging = byd_state.state == "on"
                        if eid.startswith("binary_sensor.") and "plugged_in" in eid:
                            byd_connected = byd_state.state == "on"
                    # If charging, must be connected
                    if byd_charging:
                        byd_connected = True
                    if byd_connected or byd_charging:
                        widget_data.append({
                            "vehicle_name": byd_device.name or "BYD Vehicle",
                            "is_charging": byd_charging,
                            "is_connected": byd_connected,
                            "current_soc": byd_soc,
                            "target_soc": 100,
                            "current_power_kw": 0,
                            "source": "grid" if byd_charging else "idle",
                            "eta_minutes": None,
                            "surplus_kw": round(surplus_kw, 2),
                        })

            # Show all known vehicles regardless of connection state. Idle vehicles
            # appear with current_power_kw=0 and source="idle". Only fall back to a
            # placeholder if there are literally zero vehicles in the system.
            from ..automations.loadpoint_status import coalesce_ev_widget_data

            active_widget_data = coalesce_ev_widget_data(widget_data)

            if not active_widget_data:
                active_widget_data.append({
                    "vehicle_name": "No Active Vehicle",
                    "is_charging": False,
                    "is_connected": False,
                    "current_soc": 0,
                    "target_soc": 80,
                    "current_power_kw": 0,
                    "source": "idle",
                    "eta_minutes": None,
                    "surplus_kw": round(surplus_kw, 2),
                })

            return web.json_response({
                "success": True,
                "widgets": active_widget_data,
            })

        except Exception as e:
            _LOGGER.error(f"Error getting widget data: {e}", exc_info=True)
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

class EVLoadpointStatusView(HomeAssistantView):
    """API endpoint for normalized EV/loadpoint status.

    GET /api/power_sync/ev/loadpoints/status
    Returns PowerSync-owned sessions plus observed charger telemetry.
    """
    url = "/api/power_sync/ev/loadpoints/status"
    name = "api:power_sync:ev:loadpoints:status"
    requires_auth = True

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._hass = hass
        self._config_entry = entry

    def _site_snapshot(self) -> dict:
        """Get current site power data from the preferred coordinator."""
        entry_id = self._config_entry.entry_id
        entry_data = self._hass.data.get(DOMAIN, {}).get(entry_id, {})

        solar_power_kw = 0.0
        grid_power_kw = 0.0
        battery_power_kw = 0.0
        load_power_kw = 0.0
        battery_soc = 0.0

        for key in (
            "tesla_coordinator",
            "sigenergy_coordinator",
            "sungrow_coordinator",
            "foxess_coordinator",
        ):
            coordinator = entry_data.get(key)
            if coordinator and coordinator.data:
                solar_power_kw = coordinator.data.get("solar_power", 0) or 0
                grid_power_kw = coordinator.data.get("grid_power", 0) or 0
                battery_power_kw = coordinator.data.get("battery_power", 0) or 0
                load_power_kw = coordinator.data.get("load_power", 0) or 0
                battery_soc = coordinator.data.get("battery_level", 0) or 0
                break

        return {
            "battery_soc": battery_soc,
            "solar_power_kw": solar_power_kw,
            "grid_power_kw": grid_power_kw,
            "battery_power_kw": battery_power_kw,
            "load_power_kw": load_power_kw,
        }

    async def get(self, request):
        """Get normalized EV loadpoint status."""
        try:
            from ..automations.actions import _dynamic_ev_state, _calculate_solar_surplus
            from ..automations.loadpoint_status import (
                build_loadpoint_status,
            )
            from ..automations.ev_ownership import get_ev_last_commands, get_ev_ownerships
            from ..automations.ev_charging_planner import (
                get_ev_charging_coordinator,
                get_price_level_executor,
                get_scheduled_charging_executor,
            )
            from ..solar_surplus_config import get_stored_solar_surplus_config
            from ..const import (
                CONF_GENERIC_CHARGER_ENABLED,
            )

            entry_id = self._config_entry.entry_id
            entry_data = self._hass.data.get(DOMAIN, {}).get(entry_id, {})

            site = self._site_snapshot()
            live_status = {
                "solar_power": site["solar_power_kw"] * 1000,
                "grid_power": site["grid_power_kw"] * 1000,
                "battery_power": site["battery_power_kw"] * 1000,
                "load_power": site["load_power_kw"] * 1000,
                "battery_soc": site["battery_soc"],
            }
            solar_config = get_stored_solar_surplus_config(entry_data)
            site["surplus_kw"] = round(
                _calculate_solar_surplus(
                    live_status,
                    0,
                    solar_config,
                ),
                2,
            )

            observed_vehicles = []
            for vehicle in _get_ev_vehicles_status(self._hass, self._config_entry):
                observed_vehicles.append({
                    "vehicle_id": vehicle.get("vehicle_id"),
                    "vehicle_name": vehicle.get("vehicle_name"),
                    "charger_type": "tesla",
                    "ev_power_kw": vehicle.get("ev_power_kw", 0),
                    "ev_soc": vehicle.get("ev_soc"),
                    "is_connected": vehicle.get("is_connected", False),
                    "is_charging": vehicle.get("is_charging", False),
                })

            zaptec_cached = entry_data.get("zaptec_cached_state")
            if zaptec_cached:
                zaptec_mode = (zaptec_cached.get("charger_operation_mode") or "").lower()
                power_w = zaptec_cached.get("total_charge_power_w", 0) or 0
                power_kw = power_w / 1000
                zaptec_connected = zaptec_mode in (
                    "charging",
                    "connected_waiting",
                    "connected_finishing",
                )
                observed_vehicles.append({
                    "charger_id": "zaptec_standalone",
                    "vehicle_name": "Zaptec Charger",
                    "charger_type": "zaptec",
                    "ev_power_kw": power_kw,
                    "is_connected": zaptec_connected or power_kw > 0.05,
                    "is_charging": zaptec_mode == "charging" or power_kw > 0.05,
                    "blocking_reason": zaptec_mode or None,
                })

            opts = {**self._config_entry.data, **self._config_entry.options}
            if opts.get(CONF_GENERIC_CHARGER_ENABLED):
                vehicle_name = "EV"
                automation_store = entry_data.get("automation_store")
                if automation_store:
                    stored_data = getattr(automation_store, "_data", {}) or {}
                    for config in stored_data.get("vehicle_charging_configs", []):
                        if config.get("vehicle_id") == "generic_ev" or config.get("charger_type") == "generic":
                            vehicle_name = config.get("display_name") or vehicle_name
                            break

                generic_observation = _generic_charger_observation_from_config(
                    self._hass,
                    opts,
                    vehicle_name=vehicle_name,
                )
                if generic_observation:
                    observed_vehicles.append(generic_observation)

            configured_sigenergy_state = _configured_sigenergy_charger_state(self._config_entry)
            if configured_sigenergy_state:
                from ..sigenergy_charger import sigenergy_charger_state_to_loadpoint_observation

                sigenergy_state = await _read_sigenergy_charger_state_for_entry(
                    self._config_entry,
                    self._hass,
                )
                observed_vehicles.append(
                    sigenergy_charger_state_to_loadpoint_observation(
                        sigenergy_state or configured_sigenergy_state,
                        capabilities=_configured_sigenergy_charger_capabilities(
                            self._config_entry,
                            self._hass,
                        ),
                    )
                )

            ocpp_server = entry_data.get("ocpp_server")
            if ocpp_server:
                try:
                    for cp_id, cp in ocpp_server.charge_points.items():
                        power_w = getattr(cp, "meter_power_w", 0) or 0
                        active_transaction = bool(getattr(cp, "active_transaction", None))
                        observed_vehicles.append({
                            "charger_id": f"ocpp_{cp_id}",
                            "vehicle_name": f"OCPP {cp_id[:8]}",
                            "charger_type": "ocpp",
                            "ev_power_kw": power_w / 1000,
                            "is_connected": active_transaction or power_w > 50,
                            "is_charging": power_w > 50,
                        })
                except Exception as err:
                    _LOGGER.debug("Error checking built-in OCPP server for loadpoints: %s", err)

            try:
                from homeassistant.helpers import entity_registry as er_local
                from ..automations.ocpp_status import (
                    extract_hacs_ocpp_prefix,
                    is_hacs_ocpp_power_entity,
                    is_hacs_ocpp_status_entity,
                    is_ocpp_charging,
                    is_ocpp_vehicle_present,
                    normalize_ocpp_status,
                )

                ent_reg = er_local.async_get(self._hass)
                ocpp_chargers = {}
                for entity in ent_reg.entities.values():
                    if entity.platform != "ocpp":
                        continue
                    state = self._hass.states.get(entity.entity_id)
                    if not state or state.state in ("unknown", "unavailable"):
                        continue
                    entity_id = entity.entity_id.lower()
                    prefix = extract_hacs_ocpp_prefix(entity_id)
                    if not prefix:
                        continue
                    charger = ocpp_chargers.setdefault(prefix, {
                        "status": None,
                        "power_kw": 0.0,
                        "connected": False,
                        "charging": False,
                    })
                    if is_hacs_ocpp_status_entity(entity_id):
                        if entity_id.endswith("_status_connector") or charger["status"] is None:
                            charger["status"] = normalize_ocpp_status(state.state)
                            charger["connected"] = is_ocpp_vehicle_present(state.state)
                            charger["charging"] = is_ocpp_charging(state.state)
                    elif is_hacs_ocpp_power_entity(entity_id):
                        try:
                            raw_power = float(state.state)
                        except (TypeError, ValueError):
                            continue
                        power_kw = raw_power / 1000 if raw_power > 100 else raw_power
                        power_w = raw_power if raw_power > 100 else raw_power * 1000
                        charger["power_kw"] = power_kw
                        charger["connected"] = charger["connected"] or power_w > 50
                        charger["charging"] = charger["charging"] or power_w > 50

                for prefix, charger in ocpp_chargers.items():
                    observed_vehicles.append({
                        "charger_id": f"ocpp_{prefix}",
                        "vehicle_name": f"OCPP {prefix[:12]}",
                        "charger_type": "ocpp",
                        "ev_power_kw": charger["power_kw"],
                        "is_connected": charger["connected"],
                        "is_charging": charger["charging"],
                        "blocking_reason": charger["status"],
                    })
            except Exception as err:
                _LOGGER.debug("Error checking HACS OCPP integration for loadpoints: %s", err)

            loadpoints = build_loadpoint_status(
                _dynamic_ev_state.get(entry_id, {}),
                observed_vehicles,
                site,
                get_ev_ownerships(self._hass, self._config_entry),
                get_ev_last_commands(self._hass, self._config_entry),
            )

            from ..automations.ev_vehicle_capacity import (
                resolve_ev_battery_capacity,
                vehicle_ids_match,
            )
            from ..const import CONF_GENERIC_CHARGER_BATTERY_CAPACITY_KWH

            automation_store = entry_data.get("automation_store")
            stored_data = getattr(automation_store, "_data", {}) or {}
            vehicle_configs = stored_data.get("vehicle_charging_configs", [])
            for loadpoint in loadpoints:
                loadpoint_id = loadpoint.get("loadpoint_id")
                vehicle_config = next(
                    (
                        item for item in vehicle_configs
                        if vehicle_ids_match(item.get("vehicle_id"), loadpoint_id)
                    ),
                    {},
                )
                charger_type = str(
                    loadpoint.get("charger_type")
                    or vehicle_config.get("charger_type")
                    or ""
                ).lower()
                stable_id = str(loadpoint_id or "").lower()
                anonymous = (
                    charger_type in ("generic", "ocpp")
                    or stable_id.startswith(("generic_", "ocpp_"))
                ) and not (
                    stable_id.startswith(("ble_", "byd_"))
                    or (len(stable_id) == 17 and stable_id.isalnum())
                )
                capacity = resolve_ev_battery_capacity(
                    manual_capacity_kwh=(
                        None if anonymous else vehicle_config.get("battery_capacity_kwh")
                    ),
                    charger_fallback_capacity_kwh=(
                        vehicle_config.get("charger_fallback_battery_capacity_kwh")
                        or (
                            vehicle_config.get("battery_capacity_kwh")
                            if anonymous else None
                        )
                        or opts.get(CONF_GENERIC_CHARGER_BATTERY_CAPACITY_KWH)
                    ),
                    provider_capacity_kwh=vehicle_config.get(
                        "provider_battery_capacity_kwh"
                    ),
                    model=vehicle_config.get("vehicle_model"),
                    trim=vehicle_config.get("vehicle_trim"),
                    anonymous_loadpoint=anonymous,
                )
                loadpoint.update(capacity.to_dict())

            coordinator = get_ev_charging_coordinator()
            price_level = get_price_level_executor()
            scheduled = get_scheduled_charging_executor()

            return web.json_response({
                "success": True,
                "site": site,
                "loadpoints": loadpoints,
                "modes": {
                    "coordinator": coordinator.get_state() if coordinator else None,
                    "price_level": price_level.get_state() if price_level else None,
                    "scheduled": scheduled.get_state() if scheduled else None,
                },
            })

        except Exception as e:
            _LOGGER.error("Error getting EV loadpoint status: %s", e, exc_info=True)
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

class PriceRecommendationView(HomeAssistantView):
    """API endpoint for EV charging price recommendation.

    GET /api/power_sync/ev/price_recommendation
    Returns current price-based charging recommendation.
    """
    url = "/api/power_sync/ev/price_recommendation"
    name = "api:power_sync:ev:price_recommendation"
    requires_auth = True

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._hass = hass
        self._config_entry = entry

    async def get(self, request):
        """Get price-based charging recommendation."""
        try:
            from ..automations.actions import (
                get_price_recommendation,
                _calculate_solar_surplus,
            )
            from ..solar_surplus_config import (
                get_solar_surplus_min_battery_soc,
                get_stored_solar_surplus_config,
            )

            entry_id = self._config_entry.entry_id
            entry_data = self._hass.data.get(DOMAIN, {}).get(entry_id, {})

            # Get data from coordinator (preferred over separate API call)
            solar_power_kw = 0.0
            grid_power_kw = 0.0
            battery_power_kw = 0.0
            load_power_kw = 0.0
            battery_soc = 0.0

            tesla_coordinator = entry_data.get("tesla_coordinator")
            sigenergy_coordinator = entry_data.get("sigenergy_coordinator")
            sungrow_coordinator = entry_data.get("sungrow_coordinator")
            foxess_coordinator = entry_data.get("foxess_coordinator")

            if tesla_coordinator and tesla_coordinator.data:
                solar_power_kw = tesla_coordinator.data.get("solar_power", 0)
                grid_power_kw = tesla_coordinator.data.get("grid_power", 0)
                battery_power_kw = tesla_coordinator.data.get("battery_power", 0)
                load_power_kw = tesla_coordinator.data.get("load_power", 0)
                battery_soc = tesla_coordinator.data.get("battery_level", 0)
            elif sigenergy_coordinator and sigenergy_coordinator.data:
                solar_power_kw = sigenergy_coordinator.data.get("solar_power", 0)
                grid_power_kw = sigenergy_coordinator.data.get("grid_power", 0)
                battery_power_kw = sigenergy_coordinator.data.get("battery_power", 0)
                load_power_kw = sigenergy_coordinator.data.get("load_power", 0)
                battery_soc = sigenergy_coordinator.data.get("battery_level", 0)
            elif sungrow_coordinator and sungrow_coordinator.data:
                solar_power_kw = sungrow_coordinator.data.get("solar_power", 0)
                grid_power_kw = sungrow_coordinator.data.get("grid_power", 0)
                battery_power_kw = sungrow_coordinator.data.get("battery_power", 0)
                load_power_kw = sungrow_coordinator.data.get("load_power", 0)
                battery_soc = sungrow_coordinator.data.get("battery_level", 0)
            elif foxess_coordinator and foxess_coordinator.data:
                solar_power_kw = foxess_coordinator.data.get("solar_power", 0)
                grid_power_kw = foxess_coordinator.data.get("grid_power", 0)
                battery_power_kw = foxess_coordinator.data.get("battery_power", 0)
                load_power_kw = foxess_coordinator.data.get("load_power", 0)
                battery_soc = foxess_coordinator.data.get("battery_level", 0)

            solar_config = get_stored_solar_surplus_config(entry_data)
            home_battery_minimum = get_solar_surplus_min_battery_soc(solar_config)

            # Build live_status dict for surplus calculation (expects watts)
            live_status = {
                "solar_power": solar_power_kw * 1000,
                "grid_power": grid_power_kw * 1000,
                "battery_power": battery_power_kw * 1000,
                "load_power": load_power_kw * 1000,
                "battery_soc": battery_soc,
            }

            surplus_kw = _calculate_solar_surplus(live_status, 0, solar_config)

            # Get current prices based on electricity provider
            import_price_cents = 30.0  # Default
            export_price_cents = 8.0   # Default FiT
            price_source = "default"
            tariff_info = {}  # Additional tariff metadata for response

            # Get electricity provider from config
            electricity_provider = self._config_entry.options.get(
                CONF_ELECTRICITY_PROVIDER,
                self._config_entry.data.get(CONF_ELECTRICITY_PROVIDER, "amber")
            )

            if electricity_provider in ("amber", "flow_power"):
                # Amber/Flow Power: Read from coordinator data
                try:
                    amber_coordinator = entry_data.get("amber_coordinator")
                    if amber_coordinator and amber_coordinator.data:
                        current_prices = amber_coordinator.data.get("current", [])
                        for price in current_prices:
                            channel = price.get("channelType", "")
                            if channel == "general":
                                # perKwh is in cents for Amber
                                import_price_cents = price.get("perKwh", 30.0)
                                price_source = electricity_provider
                            elif channel == "feedIn":
                                # feedIn is negative when you earn (Amber format)
                                export_price_cents = price.get("perKwh", -8.0)
                                price_source = electricity_provider

                        _LOGGER.debug(f"Using {electricity_provider} coordinator prices: import={import_price_cents}c, export={export_price_cents}c")
                except Exception as e:
                    _LOGGER.debug(f"Could not read coordinator prices: {e}")

            elif electricity_provider in ("globird", "aemo_vpp", "nz"):
                # Globird/AEMO VPP/NZ: Read from Tesla/custom tariff with real-time TOU
                try:
                    tariff_prices = await self._fetch_tariff_prices()
                    if tariff_prices:
                        import_price_cents = tariff_prices.get("import_cents", import_price_cents)
                        export_price_cents = tariff_prices.get("export_cents", export_price_cents)
                        # Determine source based on tariff type
                        if tariff_prices.get("is_custom"):
                            price_source = "custom_tariff"
                        else:
                            price_source = "tesla_tariff"
                        # Capture tariff metadata for response
                        tariff_info = {
                            "tariff_name": tariff_prices.get("tariff_name"),
                            "utility": tariff_prices.get("utility"),
                            "current_period": tariff_prices.get("current_period"),
                            "is_custom": tariff_prices.get("is_custom", False),
                        }
                        _LOGGER.debug(f"Using {price_source} prices: import={import_price_cents}c, export={export_price_cents}c, period={tariff_info.get('current_period')}")
                except Exception as e:
                    _LOGGER.debug(f"Could not fetch tariff prices: {e}")

            # Fallback: Check stored data if still using defaults
            if price_source == "default":
                amber_prices = entry_data.get("amber_prices", {})
                if amber_prices:
                    import_price_cents = amber_prices.get("import_cents", 30.0)
                    export_price_cents = amber_prices.get("export_cents", 8.0)
                    price_source = "amber_stored"

                price_data = entry_data.get("price_data", {})
                if price_data:
                    import_price_cents = price_data.get("import_price_cents", import_price_cents)
                    export_price_cents = price_data.get("export_price_cents", export_price_cents)
                    price_source = "price_data"

            # Get recommendation
            recommendation = get_price_recommendation(
                import_price_cents=import_price_cents,
                export_price_cents=export_price_cents,
                surplus_kw=surplus_kw,
                battery_soc=battery_soc,
                min_battery_soc=home_battery_minimum,
            )

            # Look up EV battery SOC from BYD vehicle integration
            ev_battery_soc = None
            try:
                if BYD_INTEGRATION in self._hass.config_entries.async_domains():
                    device_reg = dr.async_get(self._hass)
                    entity_reg = er.async_get(self._hass)
                    for device in device_reg.devices.values():
                        if not any(i[0] == BYD_INTEGRATION for i in device.identifiers):
                            continue
                        for entity in entity_reg.entities.values():
                            if entity.device_id != device.id:
                                continue
                            eid = entity.entity_id.lower()
                            if eid.startswith("sensor.") and "battery_level" in eid:
                                state = self._hass.states.get(entity.entity_id)
                                if state and state.state not in ("unknown", "unavailable"):
                                    try:
                                        val = float(state.state)
                                        if 0 <= val <= 100:
                                            ev_battery_soc = round(val, 1)
                                    except (ValueError, TypeError):
                                        pass
                        if ev_battery_soc is not None:
                            break
            except Exception:
                pass

            # Build response with tariff info if available
            response = {
                "success": True,
                **recommendation,
                "battery_soc": round(battery_soc, 1),
                "price_source": price_source,
            }

            if ev_battery_soc is not None:
                response["ev_battery_soc"] = ev_battery_soc

            # Include tariff metadata for custom/Tesla tariff users
            if tariff_info:
                response["tariff_info"] = tariff_info

            return web.json_response(response)

        except Exception as e:
            _LOGGER.error(f"Error getting price recommendation: {e}", exc_info=True)
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

    async def _fetch_tariff_prices(self) -> dict | None:
        """Fetch current prices from Tesla/custom tariff (for Globird/non-API providers).

        Returns dict with import_cents, export_cents, and tariff metadata.
        Uses real-time TOU calculation to ensure prices update when periods change.
        """
        try:
            entry_data = self._hass.data.get(DOMAIN, {}).get(self._config_entry.entry_id, {})

            # Check stored tariff_schedule (from Tesla or custom tariff)
            tariff_schedule = entry_data.get("tariff_schedule", {})
            if tariff_schedule:
                # Use real-time TOU calculation if TOU periods are defined
                if tariff_schedule.get("tou_periods"):
                    buy_cents, sell_cents, current_period = get_current_price_from_tariff_schedule(tariff_schedule)
                    return {
                        "import_cents": buy_cents,
                        "export_cents": sell_cents,
                        "current_period": current_period,
                        "tariff_name": tariff_schedule.get("plan_name", "Custom Tariff"),
                        "utility": tariff_schedule.get("utility", "Unknown"),
                        "is_custom": tariff_schedule.get("is_custom", False),
                    }
                # Fallback to cached prices
                elif tariff_schedule.get("buy_price") is not None:
                    return {
                        "import_cents": tariff_schedule.get("buy_price", 30.0),
                        "export_cents": tariff_schedule.get("sell_price", 8.0),
                        "current_period": tariff_schedule.get("current_period", "UNKNOWN"),
                        "tariff_name": tariff_schedule.get("plan_name", "Tesla Tariff"),
                        "utility": tariff_schedule.get("utility", "Tesla"),
                        "is_custom": tariff_schedule.get("is_custom", False),
                    }

            # Fallback: Fetch fresh from Tesla API
            tariff_data = await fetch_tesla_tariff_schedule(self._hass, self._config_entry)
            if tariff_data:
                # Use real-time TOU calculation if available
                if tariff_data.get("tou_periods"):
                    buy_cents, sell_cents, current_period = get_current_price_from_tariff_schedule(tariff_data)
                else:
                    buy_cents = tariff_data.get("buy_price", 30.0)
                    sell_cents = tariff_data.get("sell_price", 8.0)
                    current_period = tariff_data.get("current_period", "UNKNOWN")

                return {
                    "import_cents": buy_cents,
                    "export_cents": sell_cents,
                    "current_period": current_period,
                    "tariff_name": tariff_data.get("plan_name", "Tesla Tariff"),
                    "utility": tariff_data.get("utility", "Tesla"),
                    "is_custom": False,
                }

            return None

        except Exception as e:
            _LOGGER.debug(f"Error fetching tariff prices: {e}")
            return None

class AutoScheduleSettingsView(HomeAssistantView):
    """API endpoint for auto-schedule settings per vehicle.

    GET /api/power_sync/ev/auto_schedule/settings
    Returns auto-schedule settings for all vehicles.

    POST /api/power_sync/ev/auto_schedule/settings
    Update settings for a vehicle.
    """
    url = "/api/power_sync/ev/auto_schedule/settings"
    name = "api:power_sync:ev:auto_schedule:settings"
    requires_auth = True

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._hass = hass
        self._config_entry = entry

    async def get(self, request):
        """Get auto-schedule settings for all vehicles."""
        try:
            from ..automations.ev_charging_planner import get_auto_schedule_executor

            executor = get_auto_schedule_executor()
            if not executor:
                return web.json_response({
                    "success": False,
                    "error": "Auto-schedule executor not initialized"
                }, status=503)

            settings = {}
            for vehicle_id, vehicle_settings in executor._settings.items():
                executor.resolve_vehicle_capacity(vehicle_id, vehicle_settings)
                settings[vehicle_id] = vehicle_settings.to_dict()

            return web.json_response({
                "success": True,
                "settings": settings,
            })

        except Exception as e:
            _LOGGER.error(f"Error getting auto-schedule settings: {e}", exc_info=True)
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

    async def post(self, request):
        """Update auto-schedule settings for a vehicle."""
        try:
            from ..automations.ev_charging_planner import get_auto_schedule_executor, ChargingPlanner

            data = await request.json()
            vehicle_id = data.get("vehicle_id", "_default")

            executor = get_auto_schedule_executor()
            if not executor:
                return web.json_response({
                    "success": False,
                    "error": "Auto-schedule executor not initialized"
                }, status=503)

            # Update settings
            updated_settings = executor.update_settings(vehicle_id, data)

            # Save to storage
            entry_id = self._config_entry.entry_id
            store = self._hass.data.get(DOMAIN, {}).get(entry_id, {}).get("store")
            if store:
                await executor.save_settings(store)

            # Regenerate plan immediately with new settings
            plan_data = None
            try:
                settings = executor.get_settings(vehicle_id)
                executor._sync_charger_params_from_vehicle_configs(vehicle_id, settings)
                state = executor.get_state(vehicle_id)
                await executor._regenerate_plan(vehicle_id, settings, state)
                if state.current_plan:
                    plan_data = state.current_plan.to_dict()
            except Exception as e:
                _LOGGER.warning(f"Failed to regenerate plan after settings update: {e}")

            return web.json_response({
                "success": True,
                "settings": updated_settings.to_dict(),
                "plan": plan_data,
            })

        except Exception as e:
            _LOGGER.error(f"Error updating auto-schedule settings: {e}", exc_info=True)
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

class AutoScheduleStatusView(HomeAssistantView):
    """API endpoint for auto-schedule status per vehicle.

    GET /api/power_sync/ev/auto_schedule/status
    Returns current auto-schedule execution status for all vehicles.
    """
    url = "/api/power_sync/ev/auto_schedule/status"
    name = "api:power_sync:ev:auto_schedule:status"
    requires_auth = True

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._hass = hass
        self._config_entry = entry

    async def get(self, request):
        """Get auto-schedule status for all vehicles."""
        try:
            from ..automations.ev_charging_planner import get_auto_schedule_executor

            executor = get_auto_schedule_executor()
            if not executor:
                return web.json_response({
                    "success": False,
                    "error": "Auto-schedule executor not initialized"
                }, status=503)

            # Get all states and settings
            states = executor.get_all_states()
            settings = {}
            for vehicle_id, vehicle_settings in executor._settings.items():
                executor.resolve_vehicle_capacity(vehicle_id, vehicle_settings)
                # Derive legacy fields from departure_times for backward compat
                legacy_departure_time = None
                legacy_departure_days = []
                if vehicle_settings.departure_times:
                    legacy_departure_days = sorted(vehicle_settings.departure_times.keys())
                    legacy_departure_time = next(iter(vehicle_settings.departure_times.values()), None)
                settings[vehicle_id] = {
                    "enabled": vehicle_settings.enabled,
                    "priority": vehicle_settings.priority.value,
                    "target_soc": vehicle_settings.target_soc,
                    "battery_capacity_kwh": vehicle_settings.battery_capacity_kwh,
                    "effective_battery_capacity_kwh": vehicle_settings.effective_battery_capacity_kwh,
                    "battery_capacity_source": vehicle_settings.battery_capacity_source,
                    "departure_time": legacy_departure_time,
                    "departure_days": legacy_departure_days,
                    "departure_times": {str(k): v for k, v in vehicle_settings.departure_times.items()},
                    "departure_priorities": {str(k): v for k, v in vehicle_settings.departure_priorities.items()},
                    # New per-day constraint fields
                    "departure_min_battery_to_start": {str(k): v for k, v in vehicle_settings.departure_min_battery_to_start.items()},
                    "departure_consume_battery_level": {str(k): v for k, v in vehicle_settings.departure_consume_battery_level.items()},
                    "departure_stop_at_battery_floor": {str(k): v for k, v in vehicle_settings.departure_stop_at_battery_floor.items()},
                    "departure_limit_grid_import": {str(k): v for k, v in vehicle_settings.departure_limit_grid_import.items()},
                    "departure_preserve_home_battery": {str(k): v for k, v in vehicle_settings.departure_preserve_home_battery.items()},
                    # New field names
                    "min_battery_to_start": vehicle_settings.min_battery_to_start,
                    "consume_battery_level": vehicle_settings.consume_battery_level,
                    "stop_at_battery_floor": vehicle_settings.stop_at_battery_floor,
                    "limit_grid_import": vehicle_settings.limit_grid_import,
                    "preserve_home_battery": vehicle_settings.preserve_home_battery,
                    # Backward compat aliases for older mobile clients
                    "home_battery_minimum": vehicle_settings.min_battery_to_start,
                    "no_grid_import": vehicle_settings.limit_grid_import,
                    "departure_no_grid_import": {str(k): v for k, v in vehicle_settings.departure_limit_grid_import.items()},
                    "departure_home_battery_min": {str(k): v for k, v in vehicle_settings.departure_min_battery_to_start.items()},
                }

            _LOGGER.debug(
                "Auto-schedule status: %s",
                {v: s["enabled"] for v, s in settings.items()},
            )

            return web.json_response({
                "success": True,
                "states": states,
                "settings": settings,
            })

        except Exception as e:
            _LOGGER.error(f"Error getting auto-schedule status: {e}", exc_info=True)
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

class AutoScheduleToggleView(HomeAssistantView):
    """API endpoint to enable/disable auto-schedule for a vehicle.

    POST /api/power_sync/ev/auto_schedule/toggle
    Toggle auto-schedule on/off for a vehicle.
    """
    url = "/api/power_sync/ev/auto_schedule/toggle"
    name = "api:power_sync:ev:auto_schedule:toggle"
    requires_auth = True

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._hass = hass
        self._config_entry = entry

    async def post(self, request):
        """Toggle auto-schedule for a vehicle."""
        try:
            from ..automations.ev_charging_planner import get_auto_schedule_executor

            data = await request.json()
            vehicle_id = data.get("vehicle_id", "_default")
            enabled = data.get("enabled")

            executor = get_auto_schedule_executor()
            if not executor:
                return web.json_response({
                    "success": False,
                    "error": "Auto-schedule executor not initialized"
                }, status=503)

            settings = executor.get_settings(vehicle_id)

            if enabled is not None:
                settings.enabled = bool(enabled)
            else:
                # Toggle
                settings.enabled = not settings.enabled

            # Save to storage
            entry_id = self._config_entry.entry_id
            store = self._hass.data.get(DOMAIN, {}).get(entry_id, {}).get("store")
            if store:
                await executor.save_settings(store)

            _LOGGER.info(f"Auto-schedule {'enabled' if settings.enabled else 'disabled'} for {vehicle_id}")

            return web.json_response({
                "success": True,
                "vehicle_id": vehicle_id,
                "enabled": settings.enabled,
            })

        except Exception as e:
            _LOGGER.error(f"Error toggling auto-schedule: {e}", exc_info=True)
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

class PriceLevelChargingSettingsView(HomeAssistantView):
    """API endpoint for price-level charging settings (Recovery + Opportunity).

    GET /api/power_sync/ev/price_level_charging/settings
    Returns price-level charging settings.

    POST /api/power_sync/ev/price_level_charging/settings
    Update price-level charging settings.
    """
    url = "/api/power_sync/ev/price_level_charging/settings"
    name = "api:power_sync:ev:price_level_charging:settings"
    requires_auth = True

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._hass = hass
        self._config_entry = entry

    def _get_store(self):
        """Get the automation store from hass.data."""
        entry_id = self._config_entry.entry_id
        return self._hass.data.get(DOMAIN, {}).get(entry_id, {}).get("automation_store")

    async def get(self, request):
        """Get price-level charging settings."""
        try:
            store = self._get_store()
            settings = {
                "enabled": False,
                "recovery_soc": 40,
                "recovery_price_cents": 30,
                "opportunity_price_cents": 10,
                "preserve_home_battery": False,
                "no_grid_import": False,
                "home_battery_minimum": 20,
            }

            if store:
                stored_data = getattr(store, '_data', {}) or {}
                stored_settings = stored_data.get("price_level_charging", {})
                settings.update(stored_settings)
            if settings.get("preserve_home_battery") and settings.get("no_grid_import"):
                settings["no_grid_import"] = False

            return web.json_response({
                "success": True,
                "settings": settings,
            })

        except Exception as e:
            _LOGGER.error(f"Error getting price-level charging settings: {e}", exc_info=True)
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

    async def post(self, request):
        """Update price-level charging settings."""
        try:
            data = await request.json()
            store = self._get_store()

            if not store:
                return web.json_response({
                    "success": False,
                    "error": "Storage not available"
                }, status=503)

            stored_data = getattr(store, '_data', {}) or {}
            settings = stored_data.get("price_level_charging", {
                "enabled": False,
                "recovery_soc": 40,
                "recovery_price_cents": 30,
                "opportunity_price_cents": 10,
                "preserve_home_battery": False,
                "no_grid_import": False,
                "home_battery_minimum": 20,
            })

            # Update with provided values
            for key in [
                "enabled",
                "recovery_soc",
                "recovery_price_cents",
                "opportunity_price_cents",
                "preserve_home_battery",
                "no_grid_import",
                "home_battery_minimum",
            ]:
                if key in data:
                    settings[key] = data[key]
            if settings.get("preserve_home_battery"):
                settings["no_grid_import"] = False
            elif settings.get("no_grid_import"):
                settings["preserve_home_battery"] = False

            stored_data["price_level_charging"] = settings
            store._data = stored_data
            await store.async_save()

            _LOGGER.info(
                f"💰 Price-level charging settings updated: enabled={settings.get('enabled')}, "
                f"recovery_soc={settings.get('recovery_soc')}%, "
                f"recovery_price={settings.get('recovery_price_cents')}c, "
                f"opportunity_price={settings.get('opportunity_price_cents')}c, "
                f"preserve_home_battery={settings.get('preserve_home_battery')}, "
                f"no_grid_import={settings.get('no_grid_import')}, "
                f"home_battery_minimum={settings.get('home_battery_minimum')}%"
            )

            return web.json_response({
                "success": True,
                "settings": settings,
            })

        except Exception as e:
            _LOGGER.error(f"Error updating price-level charging settings: {e}", exc_info=True)
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

class PriceLevelChargingStatusView(HomeAssistantView):
    """API endpoint for price-level charging status.

    GET /api/power_sync/ev/price_level_charging/status
    Returns current charging state and decision reason.
    """
    url = "/api/power_sync/ev/price_level_charging/status"
    name = "api:power_sync:ev:price_level_charging:status"
    requires_auth = True

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._hass = hass
        self._config_entry = entry

    async def get(self, request):
        """Get price-level charging status."""
        try:
            from ..automations.ev_charging_planner import get_price_level_executor

            executor = get_price_level_executor()
            if executor:
                state = executor.get_state()
                return web.json_response({
                    "success": True,
                    "status": state,
                })
            else:
                return web.json_response({
                    "success": False,
                    "error": "Price-level charging executor not initialized"
                }, status=503)

        except Exception as e:
            _LOGGER.error(f"Error getting price-level charging status: {e}", exc_info=True)
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

class ScheduledChargingSettingsView(HomeAssistantView):
    """API endpoint for scheduled charging settings (time window + max price).

    GET /api/power_sync/ev/scheduled_charging/settings
    Returns scheduled charging settings.

    POST /api/power_sync/ev/scheduled_charging/settings
    Update scheduled charging settings.
    """
    url = "/api/power_sync/ev/scheduled_charging/settings"
    name = "api:power_sync:ev:scheduled_charging:settings"
    requires_auth = True

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._hass = hass
        self._config_entry = entry

    def _get_store(self):
        """Get the automation store from hass.data."""
        entry_id = self._config_entry.entry_id
        return self._hass.data.get(DOMAIN, {}).get(entry_id, {}).get("automation_store")

    async def get(self, request):
        """Get scheduled charging settings."""
        try:
            store = self._get_store()
            settings = {
                "enabled": False,
                "start_time": "00:00",
                "end_time": "06:00",
                "max_price_cents": 30,
                "preserve_home_battery": False,
                "no_grid_import": False,
            }

            if store:
                stored_data = getattr(store, '_data', {}) or {}
                stored_settings = stored_data.get("scheduled_charging", {})
                settings.update(stored_settings)
            if settings.get("preserve_home_battery") and settings.get("no_grid_import"):
                settings["no_grid_import"] = False

            return web.json_response({
                "success": True,
                "settings": settings,
            })

        except Exception as e:
            _LOGGER.error(f"Error getting scheduled charging settings: {e}", exc_info=True)
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

    async def post(self, request):
        """Update scheduled charging settings."""
        try:
            data = await request.json()
            store = self._get_store()

            if not store:
                return web.json_response({
                    "success": False,
                    "error": "Storage not available"
                }, status=503)

            stored_data = getattr(store, '_data', {}) or {}
            settings = stored_data.get("scheduled_charging", {
                "enabled": False,
                "start_time": "00:00",
                "end_time": "06:00",
                "max_price_cents": 30,
                "preserve_home_battery": False,
                "no_grid_import": False,
            })

            # Update with provided values
            for key in [
                "enabled",
                "start_time",
                "end_time",
                "max_price_cents",
                "preserve_home_battery",
                "no_grid_import",
            ]:
                if key in data:
                    settings[key] = data[key]
            if settings.get("preserve_home_battery"):
                settings["no_grid_import"] = False
            elif settings.get("no_grid_import"):
                settings["preserve_home_battery"] = False

            stored_data["scheduled_charging"] = settings
            store._data = stored_data
            await store.async_save()

            _LOGGER.info(f"Scheduled charging settings updated: enabled={settings.get('enabled')}")

            return web.json_response({
                "success": True,
                "settings": settings,
            })

        except Exception as e:
            _LOGGER.error(f"Error updating scheduled charging settings: {e}", exc_info=True)
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

class ScheduledChargingStatusView(HomeAssistantView):
    """API endpoint for scheduled charging status.

    GET /api/power_sync/ev/scheduled_charging/status
    Returns current charging state and decision reason.
    """
    url = "/api/power_sync/ev/scheduled_charging/status"
    name = "api:power_sync:ev:scheduled_charging:status"
    requires_auth = True

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._hass = hass
        self._config_entry = entry

    async def get(self, request):
        """Get scheduled charging status."""
        try:
            from ..automations.ev_charging_planner import get_scheduled_charging_executor

            executor = get_scheduled_charging_executor()
            if executor:
                state = executor.get_state()
                return web.json_response({
                    "success": True,
                    "status": state,
                })
            else:
                return web.json_response({
                    "success": False,
                    "error": "Scheduled charging executor not initialized"
                }, status=503)

        except Exception as e:
            _LOGGER.error(f"Error getting scheduled charging status: {e}", exc_info=True)
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

class EVChargingCoordinatorStatusView(HomeAssistantView):
    """API endpoint for EV charging coordinator status.

    GET /api/power_sync/ev/coordinator/status
    Returns combined charging state from all modes.
    """
    url = "/api/power_sync/ev/coordinator/status"
    name = "api:power_sync:ev:coordinator:status"
    requires_auth = True

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._hass = hass
        self._config_entry = entry

    async def get(self, request):
        """Get coordinator status with all mode decisions."""
        try:
            from ..automations.ev_charging_planner import (
                get_ev_charging_coordinator,
                get_price_level_executor,
                get_scheduled_charging_executor,
            )

            coordinator = get_ev_charging_coordinator()
            price_level = get_price_level_executor()
            scheduled = get_scheduled_charging_executor()

            response = {
                "success": True,
                "coordinator": coordinator.get_state() if coordinator else None,
                "modes": {
                    "price_level": price_level.get_state() if price_level else None,
                    "scheduled": scheduled.get_state() if scheduled else None,
                },
            }

            return web.json_response(response)

        except Exception as e:
            _LOGGER.error(f"Error getting coordinator status: {e}", exc_info=True)
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

class HomePowerSettingsView(HomeAssistantView):
    """API endpoint for home power setup settings.

    GET /api/power_sync/ev/home_power/settings
    Returns home power settings.

    POST /api/power_sync/ev/home_power/settings
    Update home power settings.
    """
    url = "/api/power_sync/ev/home_power/settings"
    name = "api:power_sync:ev:home_power:settings"
    requires_auth = True

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._hass = hass
        self._config_entry = entry

    def _get_store(self):
        """Get the automation store from hass.data."""
        entry_id = self._config_entry.entry_id
        return self._hass.data.get(DOMAIN, {}).get(entry_id, {}).get("automation_store")

    async def get(self, request):
        """Get home power settings."""
        try:
            store = self._get_store()
            settings = {
                "phase_type": "single",
                "max_charge_speed_enabled": False,
                "max_amps_per_phase": 32,
                "max_grid_import_amps": 0,
                "default_voltage": 240,
            }

            if store:
                stored_data = getattr(store, '_data', {}) or {}
                stored_settings = stored_data.get("home_power_settings", {})
                settings.update(stored_settings)

            return web.json_response({
                "success": True,
                "settings": settings,
            })

        except Exception as e:
            _LOGGER.error(f"Error getting home power settings: {e}", exc_info=True)
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

    async def post(self, request):
        """Update home power settings."""
        try:
            data = await request.json()
            store = self._get_store()

            if not store:
                return web.json_response({
                    "success": False,
                    "error": "Storage not available"
                }, status=503)

            stored_data = getattr(store, '_data', {}) or {}
            settings = stored_data.get("home_power_settings", {
                "phase_type": "single",
                "max_charge_speed_enabled": False,
                "max_amps_per_phase": 32,
                "max_grid_import_amps": 0,
                "default_voltage": 240,
            })

            # Update with provided values
            for key in [
                "phase_type",
                "max_charge_speed_enabled",
                "max_amps_per_phase",
                "max_grid_import_amps",
                "default_voltage",
            ]:
                if key in data:
                    settings[key] = data[key]

            stored_data["home_power_settings"] = settings
            store._data = stored_data
            await store.async_save()

            _LOGGER.info(f"Home power settings updated: phase_type={settings.get('phase_type')}")

            return web.json_response({
                "success": True,
                "settings": settings,
            })

        except Exception as e:
            _LOGGER.error(f"Error updating home power settings: {e}", exc_info=True)
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

