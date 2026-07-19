"""HTTP views for PowerSync."""
from __future__ import annotations

import logging
from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from typing import Any
from ..const import (
    DOMAIN,
    CONF_SIGENERGY_STATION_ID,
    CONF_SIGENERGY_CHARGE_RATE_LIMIT_KW,
    CONF_SIGENERGY_DISCHARGE_RATE_LIMIT_KW,
    CONF_SIGENERGY_EXPORT_LIMIT_KW,
    CONF_BATTERY_SYSTEM,
    CONF_SUNGROW_HOST,
    BATTERY_SYSTEM_FOXESS,
    CONF_FOXESS_HOST,
    CONF_FOXESS_SERIAL_PORT,
    CONF_GOODWE_HOST,
    CONF_OPTIMIZATION_MAX_CHARGE_W,
    CONF_OPTIMIZATION_MAX_DISCHARGE_W,
)
from ..optimization.coordinator import sigenergy_capped_optimizer_limit_w
from .. import (
    _LOGGER,
    _network_envelope_blocks_unguarded_export_write,
    _parse_json_request,
    _sigenergy_controls_export_limit_kw,
    _sigenergy_controls_rate_limit_kw,
    _validate_sigenergy_settings_payload,
)

class SigenergyTariffView(HomeAssistantView):
    """HTTP view to get current Sigenergy tariff schedule for mobile app."""

    url = "/api/power_sync/sigenergy_tariff"
    name = "api:power_sync:sigenergy_tariff"
    requires_auth = True

    def __init__(self, hass: HomeAssistant):
        """Initialize the view."""
        self._hass = hass

    async def get(self, request: web.Request) -> web.Response:
        """Handle GET request for Sigenergy tariff schedule."""
        _LOGGER.debug("📊 Sigenergy tariff HTTP request")

        # Find the power_sync entry and data
        entry = None
        entry_data = None
        for config_entry in self._hass.config_entries.async_entries(DOMAIN):
            entry = config_entry
            entry_data = self._hass.data.get(DOMAIN, {}).get(config_entry.entry_id, {})
            break

        if not entry:
            return web.json_response(
                {"success": False, "error": "PowerSync not configured"},
                status=503
            )

        # Check if this is a Sigenergy system
        battery_system = entry.data.get(CONF_BATTERY_SYSTEM, "tesla")
        if battery_system != "sigenergy":
            return web.json_response({
                "success": False,
                "error": "Not a Sigenergy system",
                "battery_system": battery_system
            })

        # Get stored tariff data
        tariff_data = entry_data.get("sigenergy_tariff")
        if not tariff_data:
            return web.json_response({
                "success": True,
                "message": "No tariff synced yet",
                "buy_prices": [],
                "sell_prices": [],
            })

        return web.json_response({
            "success": True,
            "buy_prices": tariff_data.get("buy_prices", []),
            "sell_prices": tariff_data.get("sell_prices", []),
            "synced_at": tariff_data.get("synced_at"),
            "sync_mode": tariff_data.get("sync_mode"),
        })

class SungrowSettingsView(HomeAssistantView):
    """HTTP view to get Sungrow battery settings for mobile app Controls."""

    url = "/api/power_sync/sungrow_settings"
    name = "api:power_sync:sungrow_settings"
    requires_auth = True

    def __init__(self, hass: HomeAssistant):
        """Initialize the view."""
        self._hass = hass

    async def get(self, request: web.Request) -> web.Response:
        """Handle GET request for Sungrow settings."""
        _LOGGER.info("⚙️ Sungrow settings HTTP request")

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

        # Check this is a Sungrow setup
        is_sungrow = bool(entry.data.get(CONF_SUNGROW_HOST))
        if not is_sungrow:
            return web.json_response(
                {
                    "success": False,
                    "error": "Not a Sungrow battery system",
                    "reason": "not_sungrow"
                },
                status=200
            )

        try:
            # Get Sungrow coordinator data
            entry_data = self._hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
            sungrow_coordinator = entry_data.get("sungrow_coordinator")

            if not sungrow_coordinator or not sungrow_coordinator.data:
                return web.json_response(
                    {"success": False, "error": "Sungrow data not available"},
                    status=503
                )

            data = sungrow_coordinator.data

            result = {
                "success": True,
                "battery_soc": data.get("battery_level"),
                "battery_soh": data.get("battery_soh"),
                "battery_power": data.get("battery_power"),
                "charge_rate_limit_kw": data.get("charge_rate_limit_kw"),
                "discharge_rate_limit_kw": data.get("discharge_rate_limit_kw"),
                "rate_limit_writable": data.get("rate_limit_writable", False),
                "export_limit_w": data.get("export_limit_w"),
                "export_limit_enabled": data.get("export_limit_enabled"),
                "backup_reserve": data.get("backup_reserve"),
                "min_soc": data.get("min_soc"),
                "max_soc": data.get("max_soc"),
                "ems_mode": data.get("ems_mode"),
                "ems_mode_name": data.get("ems_mode_name"),
            }

            _LOGGER.info(
                "✅ Sungrow settings: SOC=%.1f%%, SOH=%.1f%%, backup_reserve=%s",
                data.get("battery_level", 0),
                data.get("battery_soh", 0),
                (
                    f"{data['backup_reserve']:.1f}%"
                    if isinstance(data.get("backup_reserve"), (int, float))
                    else "unknown"
                ),
            )
            return web.json_response(result)

        except Exception as e:
            _LOGGER.error(f"Error fetching Sungrow settings: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500
            )

    async def post(self, request: web.Request) -> web.Response:
        """Handle POST request to update Sungrow settings."""
        return await SungrowDiagnosticsView(self._hass).post(request)

class SungrowDiagnosticsView(HomeAssistantView):
    """HTTP view to inspect raw Sungrow SH Modbus telemetry registers."""

    url = "/api/power_sync/sungrow_diagnostics"
    name = "api:power_sync:sungrow_diagnostics"
    requires_auth = True

    def __init__(self, hass: HomeAssistant):
        """Initialize the view."""
        self._hass = hass

    async def get(self, request: web.Request) -> web.Response:
        """Handle GET request for raw Sungrow register diagnostics."""
        entry = None
        for config_entry in self._hass.config_entries.async_entries(DOMAIN):
            entry = config_entry
            break

        if not entry:
            return web.json_response(
                {"success": False, "error": "PowerSync not configured"},
                status=503,
            )

        if not entry.data.get(CONF_SUNGROW_HOST):
            return web.json_response(
                {
                    "success": False,
                    "error": "Not a Sungrow battery system",
                    "reason": "not_sungrow",
                },
                status=200,
            )

        entry_data = self._hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
        sungrow_coordinator = entry_data.get("sungrow_coordinator")
        if not sungrow_coordinator or not getattr(sungrow_coordinator, "_controller", None):
            return web.json_response(
                {"success": False, "error": "Sungrow coordinator not available"},
                status=503,
            )

        controller = sungrow_coordinator._controller

        async def read_input(name: str, address: int, count: int = 1) -> dict:
            regs = await controller._read_input_register(address, count)
            return {"name": name, "address": address, "function": 4, "count": count, "registers": regs}

        async def read_holding(name: str, address: int, count: int = 1) -> dict:
            regs = await controller._read_register(address, count)
            return {"name": name, "address": address, "function": 3, "count": count, "registers": regs}

        try:
            async with sungrow_coordinator._modbus_lock:
                raw_reads = [
                    await read_input("load_power", controller.REG_LOAD_POWER, 2),
                    await read_input("export_power", controller.REG_EXPORT_POWER, 2),
                    await read_input("total_active_power", controller.REG_TOTAL_ACTIVE_POWER, 2),
                    await read_input("battery_block", controller.REG_BATTERY_VOLTAGE, 7),
                    await read_input("battery_power_s32", controller.REG_BATTERY_POWER_S32, 2),
                    await read_input("meter_active_power", controller.REG_METER_ACTIVE_POWER, 2),
                    await read_input("battery_current_precise", controller.REG_BATTERY_CURRENT_PRECISE, 1),
                    await read_input("pv_dc_power", controller.REG_TOTAL_DC_POWER, 2),
                    await read_holding("ems_mode", controller.REG_EMS_MODE, 1),
                    await read_holding("charge_command", controller.REG_CHARGE_CMD, 1),
                    await read_holding("forced_power", controller.REG_CHARGE_DISCHARGE_POWER, 1),
                    await read_holding("max_charge_power", controller.REG_MAX_CHARGE_POWER, 1),
                    await read_holding("max_discharge_power", controller.REG_MAX_DISCHARGE_POWER, 1),
                    await read_holding("export_limit_setting", controller.REG_EXPORT_LIMIT_SETTING, 1),
                    await read_holding("export_limit_enabled", controller.REG_EXPORT_LIMIT_ENABLED, 1),
                ]

            by_name = {item["name"]: item for item in raw_reads}

            def regs_for(name: str) -> list | None:
                regs = by_name.get(name, {}).get("registers")
                return regs if isinstance(regs, list) else None

            def decode_power(name: str) -> int | None:
                regs = regs_for(name)
                if regs and len(regs) >= 2:
                    return controller._read_power_s32_with_fallback(regs, name)
                return None

            def decode_signed32(name: str) -> int | None:
                regs = regs_for(name)
                if regs and len(regs) >= 2:
                    return controller._to_signed32(regs[0], regs[1])
                return None

            def decode_unsigned32(name: str) -> int | None:
                regs = regs_for(name)
                if regs and len(regs) >= 2:
                    return controller._to_unsigned32(regs[0], regs[1])
                return None

            decoded: dict[str, Any] = {
                "load_power_w": decode_power("load_power"),
                "export_power_w": decode_power("export_power"),
                "total_active_power_w": decode_power("total_active_power"),
                "battery_power_s32_w": decode_signed32("battery_power_s32"),
                "meter_active_power_w": decode_signed32("meter_active_power"),
                "pv_dc_power_w": decode_unsigned32("pv_dc_power"),
            }

            battery_block = regs_for("battery_block")
            if battery_block and len(battery_block) >= 7:
                decoded.update(
                    {
                        "battery_voltage_v": battery_block[0] / 10,
                        "battery_current_a": controller._to_signed16(battery_block[1]) / 10,
                        "battery_power_s16_w": controller._to_signed16(battery_block[2]),
                        "battery_soc_percent": battery_block[3] / 10,
                        "battery_soh_percent": battery_block[4] / 10,
                        "battery_temperature_c": controller._to_signed16(battery_block[5]) / 10,
                        "daily_battery_discharge_kwh": battery_block[6] / 10,
                    }
                )

            current = sungrow_coordinator.data or {}
            return web.json_response(
                {
                    "success": True,
                    "coordinator": {
                        "solar_power_kw": current.get("solar_power"),
                        "grid_power_kw": current.get("grid_power"),
                        "battery_power_kw": current.get("battery_power"),
                        "load_power_kw": current.get("load_power"),
                        "battery_soc": current.get("battery_level"),
                        "ems_mode": current.get("ems_mode"),
                        "ems_mode_name": current.get("ems_mode_name"),
                        "charge_cmd": current.get("charge_cmd"),
                    },
                    "decoded": decoded,
                    "raw": raw_reads,
                }
            )

        except Exception as e:
            _LOGGER.error("Error fetching Sungrow diagnostics: %s", e, exc_info=True)
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500,
            )

    async def post(self, request: web.Request) -> web.Response:
        """Handle POST request to update Sungrow settings."""
        _LOGGER.info("⚙️ Sungrow settings POST request")

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

        # Check this is a Sungrow setup
        is_sungrow = bool(entry.data.get(CONF_SUNGROW_HOST))
        if not is_sungrow:
            return web.json_response(
                {"success": False, "error": "Not a Sungrow battery system"},
                status=400
            )

        try:
            body = await _parse_json_request(request)

            # Get Sungrow coordinator
            entry_data = self._hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
            sungrow_coordinator = entry_data.get("sungrow_coordinator")

            if not sungrow_coordinator:
                return web.json_response(
                    {"success": False, "error": "Sungrow coordinator not available"},
                    status=503
                )

            # Process settings updates
            results = {}

            if "backup_reserve" in body:
                try:
                    val = int(body["backup_reserve"])
                    if not (0 <= val <= 100):
                        return web.json_response(
                            {"success": False, "error": "backup_reserve must be 0-100"},
                            status=400
                        )
                    success = await sungrow_coordinator.set_backup_reserve(val)
                except (ValueError, TypeError):
                    return web.json_response(
                        {"success": False, "error": "Invalid backup_reserve value"},
                        status=400
                    )
                results["backup_reserve"] = success

            if "charge_rate_limit_kw" in body:
                try:
                    val = float(body["charge_rate_limit_kw"])
                    if not (0.0 <= val <= 100.0):
                        return web.json_response(
                            {"success": False, "error": "charge_rate_limit_kw must be 0-100"},
                            status=400
                        )
                    success = await sungrow_coordinator.set_charge_rate_limit(val)
                except (ValueError, TypeError):
                    return web.json_response(
                        {"success": False, "error": "Invalid charge_rate_limit_kw value"},
                        status=400
                    )
                results["charge_rate_limit_kw"] = success

            if "discharge_rate_limit_kw" in body:
                try:
                    val = float(body["discharge_rate_limit_kw"])
                    if not (0.0 <= val <= 100.0):
                        return web.json_response(
                            {"success": False, "error": "discharge_rate_limit_kw must be 0-100"},
                            status=400
                        )
                    success = await sungrow_coordinator.set_discharge_rate_limit(val)
                except (ValueError, TypeError):
                    return web.json_response(
                        {"success": False, "error": "Invalid discharge_rate_limit_kw value"},
                        status=400
                    )
                results["discharge_rate_limit_kw"] = success

            if "export_limit_w" in body:
                export_limit = body["export_limit_w"]
                blocked_reason = _network_envelope_blocks_unguarded_export_write(
                    self._hass, entry
                )
                if blocked_reason is not None:
                    success = False
                    _LOGGER.warning(
                        "Sungrow export-limit write blocked by network envelope (%s)",
                        blocked_reason,
                    )
                elif export_limit is None:
                    success = await sungrow_coordinator.set_export_limit(None)
                else:
                    try:
                        val = int(export_limit)
                        if not (0 <= val <= 100000):
                            return web.json_response(
                                {"success": False, "error": "export_limit_w must be 0-100000"},
                                status=400
                            )
                        success = await sungrow_coordinator.set_export_limit(val)
                    except (ValueError, TypeError):
                        return web.json_response(
                            {"success": False, "error": "Invalid export_limit_w value"},
                            status=400
                        )
                results["export_limit_w"] = success

            if "force_charge" in body:
                if body["force_charge"]:
                    success = await sungrow_coordinator.force_charge()
                else:
                    success = await sungrow_coordinator.restore_normal()
                results["force_charge"] = success

            if "force_discharge" in body:
                if body["force_discharge"]:
                    blocked_reason = _network_envelope_blocks_unguarded_export_write(
                        self._hass, entry
                    )
                    if blocked_reason is not None:
                        success = False
                        _LOGGER.warning(
                            "Sungrow direct force discharge blocked by network envelope (%s)",
                            blocked_reason,
                        )
                    else:
                        success = await sungrow_coordinator.force_discharge()
                else:
                    success = await sungrow_coordinator.restore_normal()
                results["force_discharge"] = success

            # Trigger coordinator refresh to get updated values
            await sungrow_coordinator.async_request_refresh()

            return web.json_response({
                "success": True,
                "results": results,
            })

        except Exception as e:
            _LOGGER.error(f"Error updating Sungrow settings: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500
            )

class SigenergySettingsView(HomeAssistantView):
    """HTTP view to get/set Sigenergy battery settings for mobile app Controls."""

    url = "/api/power_sync/sigenergy_settings"
    name = "api:power_sync:sigenergy_settings"
    requires_auth = True

    def __init__(self, hass: HomeAssistant):
        """Initialize the view."""
        self._hass = hass

    async def get(self, request: web.Request) -> web.Response:
        """Handle GET request for Sigenergy settings."""
        _LOGGER.info("Sigenergy settings HTTP GET request")

        entry = None
        for config_entry in self._hass.config_entries.async_entries(DOMAIN):
            entry = config_entry
            break

        if not entry:
            return web.json_response(
                {"success": False, "error": "PowerSync not configured"},
                status=503
            )

        is_sigenergy = bool(entry.data.get(CONF_SIGENERGY_STATION_ID))
        if not is_sigenergy:
            return web.json_response(
                {
                    "success": False,
                    "error": "Not a Sigenergy battery system",
                    "reason": "not_sigenergy"
                },
                status=200
            )

        try:
            entry_data = self._hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
            sigenergy_coordinator = entry_data.get("sigenergy_coordinator")

            if not sigenergy_coordinator or not sigenergy_coordinator.data:
                return web.json_response(
                    {"success": False, "error": "Sigenergy data not available"},
                    status=503
                )

            data = sigenergy_coordinator.data
            controller = sigenergy_coordinator._controller
            configured_export_limit_kw = entry.data.get(
                CONF_SIGENERGY_EXPORT_LIMIT_KW
            )
            configured_charge_limit_kw = entry.data.get(
                CONF_SIGENERGY_CHARGE_RATE_LIMIT_KW
            )
            configured_discharge_limit_kw = entry.data.get(
                CONF_SIGENERGY_DISCHARGE_RATE_LIMIT_KW
            )

            # Read current charge/discharge limits and backup reserve from Modbus
            effective_charge_limit_kw = None
            effective_discharge_limit_kw = None
            backup_reserve = None
            effective_export_limit_kw = None

            try:
                if await controller.connect():
                    # Read charge rate limit (U32, gain 1000, kW)
                    charge_regs = await controller._read_holding_registers(
                        controller.REG_ESS_MAX_CHARGE_LIMIT, 2
                    )
                    if charge_regs and len(charge_regs) >= 2:
                        raw = controller._to_unsigned32(charge_regs[0], charge_regs[1])
                        if raw < controller.EXPORT_LIMIT_UNLIMITED:
                            effective_charge_limit_kw = round(raw / controller.GAIN_POWER, 2)

                    # Read discharge rate limit (U32, gain 1000, kW)
                    discharge_regs = await controller._read_holding_registers(
                        controller.REG_ESS_MAX_DISCHARGE_LIMIT, 2
                    )
                    if discharge_regs and len(discharge_regs) >= 2:
                        raw = controller._to_unsigned32(discharge_regs[0], discharge_regs[1])
                        if raw < controller.EXPORT_LIMIT_UNLIMITED:
                            effective_discharge_limit_kw = round(raw / controller.GAIN_POWER, 2)

                    # Read backup reserve
                    backup_reserve = await controller.get_backup_reserve()

                    # Read grid export limit (U32, gain 1000, kW)
                    export_regs = await controller._read_holding_registers(
                        controller.REG_GRID_EXPORT_LIMIT, 2
                    )
                    if export_regs and len(export_regs) >= 2:
                        raw = controller._to_unsigned32(export_regs[0], export_regs[1])
                        if raw < controller.EXPORT_LIMIT_UNLIMITED:
                            effective_export_limit_kw = round(
                                raw / controller.GAIN_POWER, 2
                            )

                    # Read EMS work mode (U16)
                    ems_regs = await controller._read_input_registers(
                        controller.REG_EMS_WORK_MODE, 1
                    )
                    ems_work_mode = ems_regs[0] if ems_regs else None
            except Exception as reg_err:
                _LOGGER.warning("Failed to read Sigenergy registers: %s", reg_err)

            # Determine if curtailment is active
            curtailment_state = entry_data.get("sigenergy_curtailment_state", "normal")

            result = {
                "success": True,
                "battery_soc": data.get("battery_level"),
                "battery_soh": data.get("battery_soh"),
                "battery_power": data.get("battery_power"),
                "solar_power": data.get("solar_power"),
                "grid_power": data.get("grid_power"),
                "load_power": data.get("load_power"),
                "charge_rate_limit_kw": _sigenergy_controls_rate_limit_kw(
                    configured_charge_limit_kw,
                    effective_charge_limit_kw,
                ),
                "configured_charge_rate_limit_kw": configured_charge_limit_kw,
                "effective_charge_rate_limit_kw": effective_charge_limit_kw,
                "discharge_rate_limit_kw": _sigenergy_controls_rate_limit_kw(
                    configured_discharge_limit_kw,
                    effective_discharge_limit_kw,
                ),
                "configured_discharge_rate_limit_kw": configured_discharge_limit_kw,
                "effective_discharge_rate_limit_kw": effective_discharge_limit_kw,
                # Controls edits a durable site cap.  Curtailment can
                # temporarily write 0 kW to the live register, but that
                # effective value must not replace the configured slider
                # value or invite an accidental cap change on refresh.
                "export_limit_kw": _sigenergy_controls_export_limit_kw(
                    configured_export_limit_kw,
                    effective_export_limit_kw,
                ),
                "configured_export_limit_kw": configured_export_limit_kw,
                "effective_export_limit_kw": effective_export_limit_kw,
                "backup_reserve": backup_reserve,
                "ems_work_mode": data.get("ems_work_mode"),
                "solar_curtailment_enabled": curtailment_state == "curtailed",
            }

            _LOGGER.info(
                "Sigenergy settings: SOC=%.1f%%, charge=%.1fkW, discharge=%.1fkW, backup=%s%%",
                data.get("battery_level", 0),
                effective_charge_limit_kw if effective_charge_limit_kw is not None else 0,
                effective_discharge_limit_kw if effective_discharge_limit_kw is not None else 0,
                backup_reserve if backup_reserve is not None else "?",
            )
            return web.json_response(result)

        except Exception as e:
            _LOGGER.error(f"Error fetching Sigenergy settings: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500
            )

    async def post(self, request: web.Request) -> web.Response:
        """Handle POST request to update Sigenergy settings."""
        _LOGGER.info("Sigenergy settings POST request")

        entry = None
        for config_entry in self._hass.config_entries.async_entries(DOMAIN):
            entry = config_entry
            break

        if not entry:
            return web.json_response(
                {"success": False, "error": "PowerSync not configured"},
                status=503
            )

        is_sigenergy = bool(entry.data.get(CONF_SIGENERGY_STATION_ID))
        if not is_sigenergy:
            return web.json_response(
                {"success": False, "error": "Not a Sigenergy battery system"},
                status=400
            )

        try:
            body = await _parse_json_request(request)
            validated, validation_error = _validate_sigenergy_settings_payload(body)
            if validation_error:
                return web.json_response(
                    {"success": False, "error": validation_error},
                    status=400,
                )

            entry_data = self._hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
            sigenergy_coordinator = entry_data.get("sigenergy_coordinator")

            if not sigenergy_coordinator:
                return web.json_response(
                    {"success": False, "error": "Sigenergy coordinator not available"},
                    status=503
                )

            controller = sigenergy_coordinator._controller
            results = {}
            new_data = dict(entry.data)
            new_options = dict(entry.options)
            persisted_changed = False
            optimizer_cap_updates = {}

            if "backup_reserve" in validated:
                val = validated["backup_reserve"]
                success = await controller.set_backup_reserve(val)
                if success:
                    controller._restore_backup_reserve_pct = val
                results["backup_reserve"] = success

            if "charge_rate_limit_kw" in validated:
                val = validated["charge_rate_limit_kw"]
                success = await controller.apply_configured_charge_rate_limit(val)
                results["charge_rate_limit_kw"] = success
                if success:
                    if new_data.get(CONF_SIGENERGY_CHARGE_RATE_LIMIT_KW) != val:
                        new_data[CONF_SIGENERGY_CHARGE_RATE_LIMIT_KW] = val
                        persisted_changed = True
                    optimizer_cap_updates["max_charge_w"] = val

            if "discharge_rate_limit_kw" in validated:
                val = validated["discharge_rate_limit_kw"]
                success = await controller.apply_configured_discharge_rate_limit(val)
                results["discharge_rate_limit_kw"] = success
                if success:
                    if new_data.get(CONF_SIGENERGY_DISCHARGE_RATE_LIMIT_KW) != val:
                        new_data[CONF_SIGENERGY_DISCHARGE_RATE_LIMIT_KW] = val
                        persisted_changed = True
                    optimizer_cap_updates["max_discharge_w"] = val

            if "export_limit_kw" in validated:
                configured_limit_kw = validated["export_limit_kw"]
                curtailment_active = (
                    entry_data.get("sigenergy_curtailment_state") == "curtailed"
                )
                success = await controller.apply_configured_export_limit(
                    configured_limit_kw,
                    curtailment_active=curtailment_active,
                )

                if success:
                    # Treat the Controls value as the durable site cap, not
                    # just a one-off Modbus write.  Commit config and LP state
                    # only after the inverter accepted the hardware command.
                    if configured_limit_kw is None:
                        export_persisted_changed = CONF_SIGENERGY_EXPORT_LIMIT_KW in new_data
                        new_data.pop(CONF_SIGENERGY_EXPORT_LIMIT_KW, None)
                    else:
                        export_persisted_changed = (
                            new_data.get(CONF_SIGENERGY_EXPORT_LIMIT_KW)
                            != configured_limit_kw
                        )
                        new_data[CONF_SIGENERGY_EXPORT_LIMIT_KW] = configured_limit_kw
                    persisted_changed = persisted_changed or export_persisted_changed

                    opt_coordinator = entry_data.get("optimization_coordinator")
                    if opt_coordinator:
                        opt_coordinator.update_config(
                            max_grid_export_w=(
                                None
                                if configured_limit_kw is None
                                else int(round(configured_limit_kw * 1000))
                            )
                        )
                        self._hass.async_create_task(
                            opt_coordinator.force_reoptimize()
                        )
                results["export_limit_kw"] = success

            if optimizer_cap_updates:
                opt_coordinator = entry_data.get("optimization_coordinator")
                if opt_coordinator:
                    optimizer_config_updates = {}
                    optimizer_key_map = {
                        "max_charge_w": CONF_OPTIMIZATION_MAX_CHARGE_W,
                        "max_discharge_w": CONF_OPTIMIZATION_MAX_DISCHARGE_W,
                    }
                    for optimizer_key, configured_cap_kw in optimizer_cap_updates.items():
                        persisted_key = optimizer_key_map[optimizer_key]
                        raw_limit_w = entry.options.get(
                            persisted_key,
                            entry.data.get(persisted_key),
                        )
                        if raw_limit_w is None:
                            raw_limit_w = getattr(
                                opt_coordinator._config,
                                optimizer_key,
                            )
                            new_options[persisted_key] = int(raw_limit_w)
                            persisted_changed = True
                        optimizer_config_updates[optimizer_key] = (
                            sigenergy_capped_optimizer_limit_w(
                                raw_limit_w,
                                configured_cap_kw,
                            )
                        )
                    opt_coordinator.update_config(**optimizer_config_updates)
                    self._hass.async_create_task(
                        opt_coordinator.force_reoptimize()
                    )

            if persisted_changed:
                entry_data["_skip_reload"] = True
                self._hass.config_entries.async_update_entry(
                    entry,
                    data=new_data,
                    options=new_options,
                )

            # Trigger coordinator refresh to get updated values
            await sigenergy_coordinator.async_request_refresh()

            return web.json_response({
                "success": True,
                "results": results,
            })

        except Exception as e:
            _LOGGER.error(f"Error updating Sigenergy settings: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500
            )

class FoxESSSettingsView(HomeAssistantView):
    """HTTP view to get/set FoxESS battery settings for mobile app Controls."""

    url = "/api/power_sync/foxess_settings"
    name = "api:power_sync:foxess_settings"
    requires_auth = True

    def __init__(self, hass: HomeAssistant):
        """Initialize the view."""
        self._hass = hass

    async def get(self, request: web.Request) -> web.Response:
        """Handle GET request for FoxESS settings."""
        _LOGGER.info("FoxESS settings HTTP GET request")

        entry = None
        for config_entry in self._hass.config_entries.async_entries(DOMAIN):
            entry = config_entry
            break

        if not entry:
            return web.json_response(
                {"success": False, "error": "PowerSync not configured"},
                status=503
            )

        is_foxess = bool(entry.data.get(CONF_BATTERY_SYSTEM) == BATTERY_SYSTEM_FOXESS or entry.data.get(CONF_FOXESS_HOST) or entry.data.get(CONF_FOXESS_SERIAL_PORT))
        if not is_foxess:
            return web.json_response(
                {"success": False, "error": "Not a FoxESS battery system", "reason": "not_foxess"},
                status=200
            )

        try:
            entry_data = self._hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
            foxess_coordinator = entry_data.get("foxess_coordinator")

            if not foxess_coordinator or not foxess_coordinator.data:
                return web.json_response(
                    {"success": False, "error": "FoxESS data not available"},
                    status=503
                )

            data = foxess_coordinator.data
            # Get model-specific work mode options from the controller's register map
            work_mode_options = {}
            work_mode_index = data.get("work_mode")  # default: raw register value
            try:
                controller = foxess_coordinator._controller
                if controller and controller._register_map:
                    reg = controller._register_map
                    work_mode_options = reg.get_work_mode_names()
                    # Map register value back to 0-based index for mobile app
                    reverse_map = {
                        reg.work_mode_self_use: 0,
                        reg.work_mode_feed_in: 1,
                        reg.work_mode_backup: 2,
                    }
                    work_mode_index = reverse_map.get(data.get("work_mode"), data.get("work_mode"))
            except Exception:
                pass
            result = {
                "success": True,
                "battery_soc": data.get("battery_level"),
                "battery_power": data.get("battery_power"),
                "solar_power": data.get("solar_power"),
                "grid_power": data.get("grid_power"),
                "load_power": data.get("load_power"),
                "work_mode": work_mode_index,
                "work_mode_name": data.get("work_mode_name"),
                "work_mode_options": work_mode_options,
                "min_soc": data.get("min_soc"),
                "max_charge_current_a": data.get("max_charge_current_a"),
                "max_discharge_current_a": data.get("max_discharge_current_a"),
                "battery_max_charge_power": data.get("battery_max_charge_power"),
                "battery_max_discharge_power": data.get("battery_max_discharge_power"),
                "model_family": data.get("model_family"),
            }

            _LOGGER.info(
                "FoxESS settings: SOC=%.1f%%, mode=%s, min_soc=%s",
                data.get("battery_level", 0),
                data.get("work_mode_name", "?"),
                data.get("min_soc", "?"),
            )
            return web.json_response(result)

        except Exception as e:
            _LOGGER.error(f"Error fetching FoxESS settings: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500
            )

    async def post(self, request: web.Request) -> web.Response:
        """Handle POST request to update FoxESS settings."""
        _LOGGER.info("FoxESS settings POST request")

        entry = None
        for config_entry in self._hass.config_entries.async_entries(DOMAIN):
            entry = config_entry
            break

        if not entry:
            return web.json_response(
                {"success": False, "error": "PowerSync not configured"},
                status=503
            )

        is_foxess = bool(entry.data.get(CONF_BATTERY_SYSTEM) == BATTERY_SYSTEM_FOXESS or entry.data.get(CONF_FOXESS_HOST) or entry.data.get(CONF_FOXESS_SERIAL_PORT))
        if not is_foxess:
            return web.json_response(
                {"success": False, "error": "Not a FoxESS battery system"},
                status=400
            )

        try:
            body = await _parse_json_request(request)

            entry_data = self._hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
            foxess_coordinator = entry_data.get("foxess_coordinator")

            if not foxess_coordinator:
                return web.json_response(
                    {"success": False, "error": "FoxESS coordinator not available"},
                    status=503
                )

            results = {}

            if "min_soc" in body:
                try:
                    val = int(body["min_soc"])
                    if not (0 <= val <= 100):
                        return web.json_response(
                            {"success": False, "error": "min_soc must be 0-100"},
                            status=400
                        )
                    success = await foxess_coordinator.set_backup_reserve(val)
                except (ValueError, TypeError):
                    return web.json_response(
                        {"success": False, "error": "Invalid min_soc value"},
                        status=400
                    )
                results["min_soc"] = success

            if "work_mode" in body:
                try:
                    requested_mode = int(body["work_mode"])
                except (ValueError, TypeError):
                    return web.json_response(
                        {"success": False, "error": "Invalid work_mode value"},
                        status=400
                    )
                if not (0 <= requested_mode <= 4):
                    return web.json_response(
                        {"success": False, "error": "work_mode must be 0-4"},
                        status=400
                    )
                # App sends 0-based indices: 0=Self Use, 1=Feed-in, 2=Backup,
                # 3=Force Charge, 4=Force Discharge.
                # Translate to model-specific register values (H3-Pro/Smart use 1-based).
                blocked_reason = (
                    _network_envelope_blocks_unguarded_export_write(
                        self._hass, entry
                    )
                    if requested_mode in (0, 1, 4)
                    else None
                )
                if blocked_reason is not None:
                    results["work_mode"] = False
                    _LOGGER.warning(
                        "FoxESS export-capable work mode blocked by network envelope (%s)",
                        blocked_reason,
                    )
                    requested_mode = -1
                controller = foxess_coordinator._controller
                if requested_mode < 0:
                    success = False
                elif controller and hasattr(controller, '_register_map') and controller._register_map:
                    reg = controller._register_map
                    mode_map = {
                        0: reg.work_mode_self_use,
                        1: reg.work_mode_feed_in,
                        2: reg.work_mode_backup,
                    }
                    if requested_mode in mode_map:
                        success = await foxess_coordinator.set_work_mode(mode_map[requested_mode])
                    elif requested_mode == 3:
                        success = await foxess_coordinator.force_charge()
                    elif requested_mode == 4:
                        success = await foxess_coordinator.force_discharge()
                    else:
                        success = await foxess_coordinator.set_work_mode(requested_mode)
                else:
                    success = await foxess_coordinator.set_work_mode(requested_mode)
                results["work_mode"] = success

            if "max_charge_current_a" in body:
                try:
                    val = float(body["max_charge_current_a"])
                    if not (0.0 <= val <= 200.0):
                        return web.json_response(
                            {"success": False, "error": "max_charge_current_a must be 0-200"},
                            status=400
                        )
                    success = await foxess_coordinator.set_charge_rate_limit(val)
                except (ValueError, TypeError):
                    return web.json_response(
                        {"success": False, "error": "Invalid max_charge_current_a value"},
                        status=400
                    )
                results["max_charge_current_a"] = success

            if "max_discharge_current_a" in body:
                try:
                    val = float(body["max_discharge_current_a"])
                    if not (0.0 <= val <= 200.0):
                        return web.json_response(
                            {"success": False, "error": "max_discharge_current_a must be 0-200"},
                            status=400
                        )
                    success = await foxess_coordinator.set_discharge_rate_limit(val)
                except (ValueError, TypeError):
                    return web.json_response(
                        {"success": False, "error": "Invalid max_discharge_current_a value"},
                        status=400
                    )
                results["max_discharge_current_a"] = success

            if "force_charge" in body:
                if body["force_charge"]:
                    success = await foxess_coordinator.force_charge()
                else:
                    success = await foxess_coordinator.restore_normal()
                results["force_charge"] = success

            if "force_discharge" in body:
                if body["force_discharge"]:
                    blocked_reason = _network_envelope_blocks_unguarded_export_write(
                        self._hass, entry
                    )
                    if blocked_reason is not None:
                        success = False
                        _LOGGER.warning(
                            "FoxESS direct force discharge blocked by network envelope (%s)",
                            blocked_reason,
                        )
                    else:
                        success = await foxess_coordinator.force_discharge()
                else:
                    success = await foxess_coordinator.restore_normal()
                results["force_discharge"] = success

            await foxess_coordinator.async_request_refresh()

            return web.json_response({
                "success": True,
                "results": results,
            })

        except Exception as e:
            _LOGGER.error(f"Error updating FoxESS settings: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500
            )

class GoodWeSettingsView(HomeAssistantView):
    """HTTP view to get/set GoodWe battery settings for mobile app Controls."""

    url = "/api/power_sync/goodwe_settings"
    name = "api:power_sync:goodwe_settings"
    requires_auth = True

    def __init__(self, hass: HomeAssistant):
        """Initialize the view."""
        self._hass = hass

    async def get(self, request: web.Request) -> web.Response:
        """Handle GET request for GoodWe settings."""
        entry = None
        for config_entry in self._hass.config_entries.async_entries(DOMAIN):
            entry = config_entry
            break

        if not entry:
            return web.json_response(
                {"success": False, "error": "PowerSync not configured"},
                status=503
            )

        is_goodwe = bool(entry.data.get(CONF_GOODWE_HOST))
        if not is_goodwe:
            return web.json_response(
                {"success": False, "error": "Not a GoodWe battery system", "reason": "not_goodwe"},
                status=200
            )

        try:
            entry_data = self._hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
            goodwe_coordinator = entry_data.get("goodwe_coordinator")

            if not goodwe_coordinator or not goodwe_coordinator.data:
                return web.json_response(
                    {"success": False, "error": "GoodWe data not available"},
                    status=503
                )

            data = goodwe_coordinator.data
            result = {
                "success": True,
                "battery_soc": data.get("battery_level"),
                "battery_power": data.get("battery_power"),
                "solar_power": data.get("solar_power"),
                "grid_power": data.get("grid_power"),
                "load_power": data.get("load_power"),
                "model_name": data.get("model_name"),
                "serial_number": data.get("serial_number"),
                "rated_power_w": data.get("rated_power_w"),
                "battery_temperature": data.get("battery_temperature"),
                "battery_soh": data.get("battery_soh"),
            }
            return web.json_response(result)

        except Exception as e:
            _LOGGER.error(f"Error fetching GoodWe settings: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500
            )

    async def post(self, request: web.Request) -> web.Response:
        """Handle POST request to update GoodWe settings."""
        entry = None
        for config_entry in self._hass.config_entries.async_entries(DOMAIN):
            entry = config_entry
            break

        if not entry:
            return web.json_response(
                {"success": False, "error": "PowerSync not configured"},
                status=503
            )

        is_goodwe = bool(entry.data.get(CONF_GOODWE_HOST))
        if not is_goodwe:
            return web.json_response(
                {"success": False, "error": "Not a GoodWe battery system"},
                status=400
            )

        try:
            body = await _parse_json_request(request)
            entry_data = self._hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
            goodwe_coordinator = entry_data.get("goodwe_coordinator")

            if not goodwe_coordinator:
                return web.json_response(
                    {"success": False, "error": "GoodWe coordinator not available"},
                    status=503
                )

            action = body.get("action")
            if action == "force_charge":
                duration = body.get("duration", 30)
                await self._hass.services.async_call(
                    DOMAIN, "force_charge", {"duration": duration}, blocking=True
                )
                return web.json_response({"success": True, "action": "force_charge"})
            elif action == "force_discharge":
                duration = body.get("duration", 30)
                await self._hass.services.async_call(
                    DOMAIN, "force_discharge", {"duration": duration}, blocking=True
                )
                return web.json_response({"success": True, "action": "force_discharge"})
            elif action == "restore_normal":
                await self._hass.services.async_call(
                    DOMAIN, "restore_normal", {}, blocking=True
                )
                return web.json_response({"success": True, "action": "restore_normal"})
            elif action == "set_backup_reserve":
                percent = body.get("percent", 20)
                await self._hass.services.async_call(
                    DOMAIN,
                    "set_backup_reserve",
                    {"percent": percent, "source": "user"},
                    blocking=True,
                )
                return web.json_response({"success": True, "action": "set_backup_reserve"})
            else:
                return web.json_response(
                    {"success": False, "error": f"Unknown action: {action}"},
                    status=400
                )

        except Exception as e:
            _LOGGER.error(f"Error in GoodWe settings POST: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500
            )

