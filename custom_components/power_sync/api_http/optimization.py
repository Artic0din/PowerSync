"""HTTP views for PowerSync."""
from __future__ import annotations

import logging
from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from ..const import (
    DOMAIN,
    CONF_ELECTRICITY_PROVIDER,
    supports_no_idle_mode_provider,
    CONF_BATTERY_SYSTEM,
    CONF_OPTIMIZATION_PROVIDER,
    CONF_OPTIMIZATION_ENABLED,
    CONF_OPTIMIZATION_EV_INTEGRATION,
    CONF_OPTIMIZATION_PLANNED_EV_LOAD_ENTITY,
    OPT_PROVIDER_NATIVE,
    OPT_PROVIDER_POWERSYNC,
    CONF_OPTIMIZATION_COST_FUNCTION,
    CONF_OPTIMIZATION_BACKUP_RESERVE,
    CONF_OPTIMIZATION_AUTO_APPLY_RESERVE,
    CONF_OPTIMIZATION_MANUAL_RESERVE,
    CONF_OPTIMIZATION_HORIZON,
    CONF_HARDWARE_BACKUP_RESERVE,
    CONF_OPTIMIZATION_BATTERY_CAPACITY_WH,
    CONF_OPTIMIZATION_ALLOW_GRID_CHARGE,
    CONF_OPTIMIZATION_MAX_CHARGE_W,
    CONF_OPTIMIZATION_MAX_DISCHARGE_W,
    CONF_OPTIMIZATION_MAX_GRID_IMPORT_W,
    CONF_OPTIMIZATION_MAX_GRID_EXPORT_W,
    CONF_OPTIMIZATION_MAX_GRID_CHARGE_PRICE,
    CONF_OPTIMIZATION_GRID_CHARGE_SOC_CAP,
    CONF_OPTIMIZATION_SPREAD_EXPORT_ENABLED,
    CONF_OPTIMIZATION_SPREAD_IMPORT_ENABLED,
    CONF_OPTIMIZATION_DISABLE_IDLE,
    CONF_PROFIT_MAX_ENABLED,
    CONF_CHARGE_BY_TIME_ENABLED,
    CONF_CHARGE_BY_TIME_TARGET_TIME,
    CONF_CHARGE_BY_TIME_TARGET_SOC,
    CONF_PROFIT_MAX_TARGET_TIME,
    CONF_PROFIT_MAX_TARGET_SOC,
    DEFAULT_OPTIMIZATION_BACKUP_RESERVE,
    DEFAULT_CHARGE_BY_TIME_TARGET_TIME,
    DEFAULT_CHARGE_BY_TIME_TARGET_SOC,
    BATTERY_CAPACITY_DEFAULTS,
    BATTERY_POWER_DEFAULTS,
)
from .. import (
    _LOGGER,
    _optimizer_settings_groups,
)

class OptimizationView(HomeAssistantView):
    """HTTP view to get optimization schedule and status."""

    url = "/api/power_sync/optimization"
    name = "api:power_sync:optimization"
    requires_auth = True

    def __init__(self, hass: HomeAssistant):
        """Initialize the view."""
        self._hass = hass

    async def get(self, request: web.Request) -> web.Response:
        """Handle GET request for optimization status."""
        _LOGGER.debug("Optimization status GET request")

        # Find the optimization coordinator
        opt_coordinator = None
        for entry_id, data in self._hass.data.get(DOMAIN, {}).items():
            if isinstance(data, dict) and "optimization_coordinator" in data:
                opt_coordinator = data["optimization_coordinator"]
                break

        if not opt_coordinator:
            # Optimization not enabled - return disabled status
            _LOGGER.info("Optimization not configured")
            return web.json_response({
                "success": True,
                "enabled": False,
                "optimizer_available": False,
                "status": "not_configured",
                "message": "Smart Optimization is not enabled. Enable it in settings."
            })

        api_data = opt_coordinator.get_api_data()
        _LOGGER.debug(f"Optimization GET response: enabled={api_data.get('enabled')}, "
                      f"predicted_cost=${api_data.get('predicted_cost', 0):.2f}, "
                      f"savings=${api_data.get('predicted_savings', 0):.2f}, "
                      f"has_schedule={api_data.get('schedule') is not None}")
        return web.json_response(api_data)

    async def post(self, request: web.Request) -> web.Response:
        """Handle POST request to force re-optimization."""
        # Find the optimization coordinator
        opt_coordinator = None
        for entry_id, data in self._hass.data.get(DOMAIN, {}).items():
            if isinstance(data, dict) and "optimization_coordinator" in data:
                opt_coordinator = data["optimization_coordinator"]
                break

        if not opt_coordinator:
            return web.json_response({
                "success": False,
                "error": "Optimization not configured"
            }, status=400)

        try:
            result = await opt_coordinator.force_reoptimize()
            if result and hasattr(result, 'actions'):
                return web.json_response({
                    "success": True,
                    "message": "Re-optimization triggered",
                    "schedule": result.to_api_response() if hasattr(result, 'to_api_response') else None
                })
            elif result and hasattr(result, 'success') and not result.success:
                return web.json_response({
                    "success": False,
                    "error": f"Optimization failed: {getattr(result, 'status', 'unknown')}"
                }, status=500)
            else:
                # result is None - missing forecast data
                return web.json_response({
                    "success": False,
                    "error": "Missing forecast data. Ensure price data (Amber/Octopus) and Solcast solar forecast are configured."
                }, status=400)
        except Exception as e:
            _LOGGER.error(f"Error in force re-optimization: {e}", exc_info=True)
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

class OptimizationSettingsView(HomeAssistantView):
    """HTTP view to get/set optimization settings."""

    url = "/api/power_sync/optimization/settings"
    name = "api:power_sync:optimization:settings"
    requires_auth = True

    def __init__(self, hass: HomeAssistant):
        """Initialize the view."""
        self._hass = hass

    async def get(self, request: web.Request) -> web.Response:
        """Handle GET request for optimization settings."""
        # Find the optimization coordinator
        opt_coordinator = None
        config_entry = None
        entries = self._hass.config_entries.async_entries(DOMAIN)
        if entries:
            config_entry = entries[0]
        for entry_id, data in self._hass.data.get(DOMAIN, {}).items():
            if isinstance(data, dict) and "optimization_coordinator" in data:
                opt_coordinator = data["optimization_coordinator"]
                break

        if not opt_coordinator:
            battery_system = (
                config_entry.data.get(CONF_BATTERY_SYSTEM, "tesla")
                if config_entry
                else "tesla"
            )
            default_capacity_wh = BATTERY_CAPACITY_DEFAULTS.get(battery_system, 13500)
            default_power_w = BATTERY_POWER_DEFAULTS.get(battery_system, 5000)

            def _entry_int_setting(key: str, default: int) -> int:
                if not config_entry:
                    return default
                value = config_entry.options.get(
                    key,
                    config_entry.data.get(key, default),
                )
                try:
                    parsed = int(float(value))
                except (TypeError, ValueError):
                    return default
                return parsed if parsed > 0 else default

            def _entry_optional_nonnegative_int_setting(key: str) -> int | None:
                if not config_entry:
                    return None
                if key in config_entry.options:
                    value = config_entry.options.get(key)
                elif key in config_entry.data:
                    value = config_entry.data.get(key)
                else:
                    return None
                try:
                    parsed = int(float(value))
                except (TypeError, ValueError):
                    return None
                return parsed if parsed >= 0 else None

            def _entry_percent_setting(key: str, default_ratio: float) -> int:
                if not config_entry:
                    return int(round(default_ratio * 100))
                value = config_entry.options.get(
                    key,
                    config_entry.data.get(key, default_ratio),
                )
                try:
                    parsed = float(value)
                except (TypeError, ValueError):
                    parsed = default_ratio
                if parsed <= 1:
                    parsed *= 100
                return max(0, min(100, int(round(parsed))))

            def _entry_price_cents_setting(key: str) -> float:
                if not config_entry:
                    return 0.0
                value = config_entry.options.get(key, config_entry.data.get(key))
                try:
                    parsed = float(value)
                except (TypeError, ValueError):
                    return 0.0
                if parsed <= 0:
                    return 0.0
                return round(parsed * 100.0, 3) if parsed <= 1 else round(parsed, 3)

            charge_by_time_enabled = bool(
                config_entry
                and config_entry.options.get(
                    CONF_CHARGE_BY_TIME_ENABLED,
                    config_entry.data.get(
                        CONF_CHARGE_BY_TIME_ENABLED,
                        config_entry.options.get(
                            CONF_PROFIT_MAX_ENABLED,
                            config_entry.data.get(CONF_PROFIT_MAX_ENABLED, False),
                        ),
                    ),
                )
            )
            charge_by_time_target_time = (
                config_entry.options.get(
                    CONF_CHARGE_BY_TIME_TARGET_TIME,
                    config_entry.data.get(
                        CONF_CHARGE_BY_TIME_TARGET_TIME,
                        config_entry.options.get(
                            CONF_PROFIT_MAX_TARGET_TIME,
                            config_entry.data.get(
                                CONF_PROFIT_MAX_TARGET_TIME,
                                DEFAULT_CHARGE_BY_TIME_TARGET_TIME,
                            ),
                        ),
                    ),
                )
                if config_entry
                else DEFAULT_CHARGE_BY_TIME_TARGET_TIME
            )
            charge_by_time_target_soc = _entry_percent_setting(
                CONF_CHARGE_BY_TIME_TARGET_SOC,
                DEFAULT_CHARGE_BY_TIME_TARGET_SOC,
            )
            if (
                config_entry
                and CONF_CHARGE_BY_TIME_TARGET_SOC not in config_entry.options
                and CONF_CHARGE_BY_TIME_TARGET_SOC not in config_entry.data
            ):
                charge_by_time_target_soc = _entry_percent_setting(
                    CONF_PROFIT_MAX_TARGET_SOC,
                    DEFAULT_CHARGE_BY_TIME_TARGET_SOC,
                )

            backup_reserve = DEFAULT_OPTIMIZATION_BACKUP_RESERVE
            hardware_reserve = 0
            auto_apply_reserve = False
            manual_reserve = None
            if config_entry:
                raw_backup = config_entry.data.get(
                    CONF_OPTIMIZATION_BACKUP_RESERVE,
                    config_entry.options.get(
                        CONF_OPTIMIZATION_BACKUP_RESERVE,
                        DEFAULT_OPTIMIZATION_BACKUP_RESERVE,
                    ),
                )
                try:
                    backup_reserve = float(raw_backup)
                    if backup_reserve > 1:
                        backup_reserve = backup_reserve / 100
                except (TypeError, ValueError):
                    backup_reserve = DEFAULT_OPTIMIZATION_BACKUP_RESERVE
                raw_hardware = config_entry.data.get(
                    CONF_HARDWARE_BACKUP_RESERVE,
                    config_entry.options.get(CONF_HARDWARE_BACKUP_RESERVE, 0),
                )
                try:
                    hardware_reserve = float(raw_hardware)
                    if hardware_reserve <= 1:
                        hardware_reserve = hardware_reserve * 100
                except (TypeError, ValueError):
                    hardware_reserve = 0
                auto_apply_reserve = bool(
                    config_entry.options.get(
                        CONF_OPTIMIZATION_AUTO_APPLY_RESERVE,
                        config_entry.data.get(CONF_OPTIMIZATION_AUTO_APPLY_RESERVE, False),
                    )
                )
                raw_manual = config_entry.options.get(
                    CONF_OPTIMIZATION_MANUAL_RESERVE,
                    config_entry.data.get(CONF_OPTIMIZATION_MANUAL_RESERVE),
                )
                try:
                    if raw_manual is not None:
                        manual_reserve = float(raw_manual)
                        if manual_reserve > 1:
                            manual_reserve = manual_reserve / 100
                except (TypeError, ValueError):
                    manual_reserve = None

                electricity_provider = config_entry.options.get(
                    CONF_ELECTRICITY_PROVIDER,
                    config_entry.data.get(CONF_ELECTRICITY_PROVIDER, ""),
                )
                disable_idle_enabled = (
                    supports_no_idle_mode_provider(electricity_provider)
                    and bool(
                        config_entry.options.get(
                            CONF_OPTIMIZATION_DISABLE_IDLE,
                            config_entry.data.get(CONF_OPTIMIZATION_DISABLE_IDLE, False),
                        )
                    )
                )
            else:
                disable_idle_enabled = False

            return web.json_response({
                "success": True,
                "enabled": bool(
                    config_entry
                    and config_entry.options.get(CONF_OPTIMIZATION_ENABLED, False)
                ),
                "cost_function": "cost",
                "backup_reserve": round(backup_reserve * 100),
                "auto_apply_reserve_enabled": auto_apply_reserve,
                "manual_backup_reserve": (
                    round(manual_reserve * 100) if manual_reserve is not None else None
                ),
                "ev_integration": bool(
                    config_entry
                    and config_entry.options.get(
                        CONF_OPTIMIZATION_EV_INTEGRATION,
                        config_entry.data.get(CONF_OPTIMIZATION_EV_INTEGRATION, False),
                    )
                ),
                "planned_ev_load_entity": (
                    config_entry.options.get(
                        CONF_OPTIMIZATION_PLANNED_EV_LOAD_ENTITY,
                        config_entry.data.get(CONF_OPTIMIZATION_PLANNED_EV_LOAD_ENTITY),
                    )
                    if config_entry
                    else None
                ),
                "profit_max_enabled": bool(
                    config_entry
                    and config_entry.options.get(
                        CONF_PROFIT_MAX_ENABLED,
                        config_entry.data.get(CONF_PROFIT_MAX_ENABLED, False),
                    )
                ),
                "charge_by_time_enabled": charge_by_time_enabled,
                "spread_export_enabled": bool(
                    config_entry
                    and config_entry.options.get(
                        CONF_OPTIMIZATION_SPREAD_EXPORT_ENABLED,
                        config_entry.data.get(CONF_OPTIMIZATION_SPREAD_EXPORT_ENABLED, False),
                    )
                ),
                "spread_import_enabled": bool(
                    config_entry
                    and config_entry.options.get(
                        CONF_OPTIMIZATION_SPREAD_IMPORT_ENABLED,
                        config_entry.data.get(CONF_OPTIMIZATION_SPREAD_IMPORT_ENABLED, False),
                    )
                ),
                "disable_idle_enabled": disable_idle_enabled,
                "config": {
                    "battery_capacity_wh": _entry_int_setting(
                        CONF_OPTIMIZATION_BATTERY_CAPACITY_WH,
                        default_capacity_wh,
                    ),
                    "max_charge_w": _entry_int_setting(
                        CONF_OPTIMIZATION_MAX_CHARGE_W,
                        default_power_w,
                    ),
                    "max_discharge_w": _entry_int_setting(
                        CONF_OPTIMIZATION_MAX_DISCHARGE_W,
                        default_power_w,
                    ),
                    "max_grid_export_w": _entry_optional_nonnegative_int_setting(
                        CONF_OPTIMIZATION_MAX_GRID_EXPORT_W
                    ),
                    "max_grid_import_w": (
                        _entry_int_setting(CONF_OPTIMIZATION_MAX_GRID_IMPORT_W, 0)
                        if config_entry
                        else 0
                    ),
                    "max_grid_charge_price": _entry_price_cents_setting(
                        CONF_OPTIMIZATION_MAX_GRID_CHARGE_PRICE
                    ),
                    "grid_charge_soc_cap": _entry_percent_setting(
                        CONF_OPTIMIZATION_GRID_CHARGE_SOC_CAP,
                        1.0,
                    ),
                    "horizon_hours": (
                        _entry_int_setting(CONF_OPTIMIZATION_HORIZON, 48)
                        if config_entry
                        else 48
                    ),
                    "allow_grid_charge": bool(
                        config_entry.options.get(
                            CONF_OPTIMIZATION_ALLOW_GRID_CHARGE,
                            config_entry.data.get(CONF_OPTIMIZATION_ALLOW_GRID_CHARGE, True),
                        )
                        if config_entry
                        else True
                    ),
                    "planned_ev_load_entity": (
                        config_entry.options.get(
                            CONF_OPTIMIZATION_PLANNED_EV_LOAD_ENTITY,
                            config_entry.data.get(CONF_OPTIMIZATION_PLANNED_EV_LOAD_ENTITY),
                        )
                        if config_entry
                        else None
                    ),
                    "spread_export_enabled": bool(
                        config_entry.options.get(
                            CONF_OPTIMIZATION_SPREAD_EXPORT_ENABLED,
                            config_entry.data.get(CONF_OPTIMIZATION_SPREAD_EXPORT_ENABLED, False),
                        )
                        if config_entry
                        else False
                    ),
                    "spread_import_enabled": bool(
                        config_entry.options.get(
                            CONF_OPTIMIZATION_SPREAD_IMPORT_ENABLED,
                            config_entry.data.get(CONF_OPTIMIZATION_SPREAD_IMPORT_ENABLED, False),
                        )
                        if config_entry
                        else False
                    ),
                    "disable_idle_enabled": disable_idle_enabled,
                    "auto_apply_reserve_enabled": auto_apply_reserve,
                    "manual_backup_reserve": (
                        round(manual_reserve * 100)
                        if manual_reserve is not None
                        else None
                    ),
                    "charge_by_time_enabled": charge_by_time_enabled,
                    "charge_by_time_target_time": charge_by_time_target_time,
                    "charge_by_time_target_soc": charge_by_time_target_soc,
                    "profit_max_target_time": charge_by_time_target_time,
                    "profit_max_target_soc": charge_by_time_target_soc,
                    "backup_reserve": round(backup_reserve * 100),
                    "hardware_backup_reserve": round(hardware_reserve),
                    "battery_specs_source": "manual"
                    if config_entry
                    and (
                        config_entry.options.get(CONF_OPTIMIZATION_BATTERY_CAPACITY_WH)
                        or config_entry.data.get(CONF_OPTIMIZATION_BATTERY_CAPACITY_WH)
                        or config_entry.options.get(CONF_OPTIMIZATION_MAX_CHARGE_W)
                        or config_entry.data.get(CONF_OPTIMIZATION_MAX_CHARGE_W)
                        or config_entry.options.get(CONF_OPTIMIZATION_MAX_DISCHARGE_W)
                        or config_entry.data.get(CONF_OPTIMIZATION_MAX_DISCHARGE_W)
                    )
                    else "default",
                },
                "settings_groups": _optimizer_settings_groups(),
            })

        return web.json_response({
            "success": True,
            "enabled": opt_coordinator.enabled,
            "optimiser_available": opt_coordinator.optimiser_available,
            "cost_function": opt_coordinator._cost_function.value,
            "ev_integration": opt_coordinator._ev_integration_enabled,
            "planned_ev_load_entity": opt_coordinator._planned_ev_load_entity_id,
            "profit_max_enabled": opt_coordinator.profit_max_mode,
            "charge_by_time_enabled": opt_coordinator.charge_by_time_enabled,
            "spread_export_enabled": opt_coordinator._config.spread_export_enabled,
            "spread_import_enabled": opt_coordinator._config.spread_import_enabled,
            "disable_idle_enabled": opt_coordinator.disable_idle_enabled,
            "auto_apply_reserve_enabled": opt_coordinator.auto_apply_reserve_enabled,
            "settings_groups": _optimizer_settings_groups(),
            "manual_backup_reserve": (
                round(opt_coordinator.manual_backup_reserve * 100)
                if opt_coordinator.manual_backup_reserve is not None
                else None
            ),
            "config": {
                "battery_capacity_wh": opt_coordinator._config.battery_capacity_wh,
                "max_charge_w": opt_coordinator._config.max_charge_w,
                "max_discharge_w": opt_coordinator._config.max_discharge_w,
                "max_grid_export_w": opt_coordinator._config.max_grid_export_w,
                "max_grid_import_w": opt_coordinator._config.max_grid_import_w,
                "max_grid_charge_price": (
                    round(opt_coordinator._config.max_grid_charge_price * 100, 3)
                    if opt_coordinator._config.max_grid_charge_price is not None
                    else 0
                ),
                "grid_charge_soc_cap": max(
                    0,
                    min(
                        100,
                        int(round(opt_coordinator._config.grid_charge_soc_cap * 100)),
                    ),
                ),
                "allow_grid_charge": opt_coordinator._config.allow_grid_charge,
                "planned_ev_load_entity": opt_coordinator._planned_ev_load_entity_id,
                "spread_export_enabled": opt_coordinator._config.spread_export_enabled,
                "spread_import_enabled": opt_coordinator._config.spread_import_enabled,
                "disable_idle_enabled": opt_coordinator.disable_idle_enabled,
                "charge_by_time_enabled": opt_coordinator.charge_by_time_enabled,
                "auto_apply_reserve_enabled": opt_coordinator.auto_apply_reserve_enabled,
                "manual_backup_reserve": (
                    round(opt_coordinator.manual_backup_reserve * 100)
                    if opt_coordinator.manual_backup_reserve is not None
                    else None
                ),
                "backup_reserve": round(opt_coordinator._config.backup_reserve * 100),
                "hardware_backup_reserve": opt_coordinator._startup_backup_reserve if opt_coordinator._startup_backup_reserve is not None else 0,
                "battery_specs_source": opt_coordinator._battery_specs_source,
                "interval_minutes": opt_coordinator._config.interval_minutes,
                "horizon_hours": opt_coordinator._config.horizon_hours,
                "charge_by_time_target_time": opt_coordinator._config.charge_by_time_target_time,
                "charge_by_time_target_soc": (
                    max(0, min(100, int(round(opt_coordinator._charge_by_time_target_soc() * 100))))
                    if hasattr(opt_coordinator, "_charge_by_time_target_soc")
                    else int(round(DEFAULT_CHARGE_BY_TIME_TARGET_SOC * 100))
                ),
                "profit_max_target_time": opt_coordinator._config.charge_by_time_target_time,
                "profit_max_target_soc": (
                    max(0, min(100, int(round(opt_coordinator._charge_by_time_target_soc() * 100))))
                    if hasattr(opt_coordinator, "_charge_by_time_target_soc")
                    else int(round(DEFAULT_CHARGE_BY_TIME_TARGET_SOC * 100))
                ),
            }
        })

    async def post(self, request: web.Request) -> web.Response:
        """Handle POST request to update optimization settings."""
        try:
            settings = await request.json()
            changes = []

            # Find the config entry and optimization coordinator
            opt_coordinator = None
            config_entry = None
            entry_id = None

            # First, find the config entry directly
            entries = self._hass.config_entries.async_entries(DOMAIN)
            if entries:
                config_entry = entries[0]
                entry_id = config_entry.entry_id

                # Then look for optimizer in hass.data
                entry_data = self._hass.data.get(DOMAIN, {}).get(entry_id)
                if isinstance(entry_data, dict):
                    opt_coordinator = entry_data.get("optimization_coordinator")

            # If coordinator exists, use it
            if opt_coordinator:
                result = await opt_coordinator.set_settings(settings)
                return web.json_response(result)

            # Otherwise, update config entry directly
            if not config_entry:
                return web.json_response({
                    "success": False,
                    "error": "PowerSync not configured"
                }, status=400)

            # Build updated data and options
            new_data = dict(config_entry.data)
            new_options = dict(config_entry.options)

            # Track whether enabled actually changed (to avoid unnecessary reloads)
            enabled_changed = False
            if "enabled" in settings:
                from ..const import CONF_OPTIMIZATION_PROVIDER, OPT_PROVIDER_POWERSYNC, OPT_PROVIDER_NATIVE
                was_enabled = config_entry.options.get(CONF_OPTIMIZATION_ENABLED, False)
                if settings["enabled"] != was_enabled:
                    enabled_changed = True
                    if settings["enabled"]:
                        new_data[CONF_OPTIMIZATION_PROVIDER] = OPT_PROVIDER_POWERSYNC
                        new_options[CONF_OPTIMIZATION_ENABLED] = True
                        changes.append("Enabled Smart Optimization")
                    else:
                        new_data[CONF_OPTIMIZATION_PROVIDER] = OPT_PROVIDER_NATIVE
                        new_options[CONF_OPTIMIZATION_ENABLED] = False
                        changes.append("Disabled Smart Optimization")

            if "ev_integration" in settings:
                from ..const import CONF_OPTIMIZATION_EV_INTEGRATION
                new_options[CONF_OPTIMIZATION_EV_INTEGRATION] = settings["ev_integration"]
                changes.append(f"Set EV integration to {settings['ev_integration']}")

            if "planned_ev_load_entity" in settings:
                raw_entity = settings.get("planned_ev_load_entity")
                entity_id = raw_entity.strip() if isinstance(raw_entity, str) else None
                entity_id = entity_id or None
                new_data[CONF_OPTIMIZATION_PLANNED_EV_LOAD_ENTITY] = entity_id
                new_options[CONF_OPTIMIZATION_PLANNED_EV_LOAD_ENTITY] = entity_id
                changes.append(f"Set planned EV load entity to {entity_id or 'cleared'}")

            if "profit_max_enabled" in settings:
                from ..const import CONF_PROFIT_MAX_ENABLED
                new_options[CONF_PROFIT_MAX_ENABLED] = bool(settings["profit_max_enabled"])
                changes.append(f"Set profit maximisation mode to {settings['profit_max_enabled']}")

            if "charge_by_time_enabled" in settings:
                new_options[CONF_CHARGE_BY_TIME_ENABLED] = bool(settings["charge_by_time_enabled"])
                changes.append(f"Set Charge By Time to {settings['charge_by_time_enabled']}")

            target_time_key = (
                "charge_by_time_target_time"
                if "charge_by_time_target_time" in settings
                else "profit_max_target_time"
                if "profit_max_target_time" in settings
                else None
            )
            if target_time_key:
                target_time = str(settings[target_time_key])
                new_data[CONF_CHARGE_BY_TIME_TARGET_TIME] = target_time
                new_options[CONF_CHARGE_BY_TIME_TARGET_TIME] = target_time
                new_data[CONF_PROFIT_MAX_TARGET_TIME] = target_time
                new_options[CONF_PROFIT_MAX_TARGET_TIME] = target_time
                changes.append(f"Set Charge By Time target time to {target_time}")

            target_soc_key = (
                "charge_by_time_target_soc"
                if "charge_by_time_target_soc" in settings
                else "profit_max_target_soc"
                if "profit_max_target_soc" in settings
                else None
            )
            if target_soc_key:
                target_soc = settings[target_soc_key]
                try:
                    target_soc = float(target_soc)
                except (TypeError, ValueError):
                    target_soc = DEFAULT_CHARGE_BY_TIME_TARGET_SOC
                if target_soc > 1:
                    target_soc = target_soc / 100.0
                target_soc = max(0.0, min(1.0, target_soc))
                new_data[CONF_CHARGE_BY_TIME_TARGET_SOC] = target_soc
                new_options[CONF_CHARGE_BY_TIME_TARGET_SOC] = target_soc
                new_data[CONF_PROFIT_MAX_TARGET_SOC] = target_soc
                new_options[CONF_PROFIT_MAX_TARGET_SOC] = target_soc
                changes.append(f"Set Charge By Time target SOC to {int(round(target_soc * 100))}%")

            if "allow_grid_charge" in settings:
                new_options[CONF_OPTIMIZATION_ALLOW_GRID_CHARGE] = bool(settings["allow_grid_charge"])
                changes.append(f"Set grid charging to {settings['allow_grid_charge']}")

            if "max_grid_charge_price" in settings:
                raw_price_cap = settings.get("max_grid_charge_price")
                try:
                    price_cap = float(raw_price_cap)
                except (TypeError, ValueError):
                    price_cap = 0.0
                if price_cap <= 0:
                    new_data.pop(CONF_OPTIMIZATION_MAX_GRID_CHARGE_PRICE, None)
                    new_options.pop(CONF_OPTIMIZATION_MAX_GRID_CHARGE_PRICE, None)
                    changes.append("Cleared max_grid_charge_price")
                else:
                    price_cap = price_cap / 100.0 if price_cap > 1 else price_cap
                    new_data[CONF_OPTIMIZATION_MAX_GRID_CHARGE_PRICE] = price_cap
                    new_options[CONF_OPTIMIZATION_MAX_GRID_CHARGE_PRICE] = price_cap
                    changes.append(
                        f"Set max_grid_charge_price to {round(price_cap * 100, 3)}c/kWh"
                    )

            if "grid_charge_soc_cap" in settings:
                try:
                    soc_cap = float(settings["grid_charge_soc_cap"])
                except (TypeError, ValueError):
                    soc_cap = 100.0
                if soc_cap > 1:
                    soc_cap = soc_cap / 100.0
                soc_cap = max(0.0, min(1.0, soc_cap))
                new_data[CONF_OPTIMIZATION_GRID_CHARGE_SOC_CAP] = soc_cap
                new_options[CONF_OPTIMIZATION_GRID_CHARGE_SOC_CAP] = soc_cap
                changes.append(
                    f"Set grid_charge_soc_cap to {int(round(soc_cap * 100))}%"
                )

            if "spread_export_enabled" in settings:
                new_options[CONF_OPTIMIZATION_SPREAD_EXPORT_ENABLED] = bool(settings["spread_export_enabled"])
                changes.append(f"Set spread export to {settings['spread_export_enabled']}")

            if "spread_import_enabled" in settings:
                new_options[CONF_OPTIMIZATION_SPREAD_IMPORT_ENABLED] = bool(settings["spread_import_enabled"])
                changes.append(f"Set spread import to {settings['spread_import_enabled']}")

            if "disable_idle_enabled" in settings:
                electricity_provider = new_options.get(
                    CONF_ELECTRICITY_PROVIDER,
                    new_data.get(CONF_ELECTRICITY_PROVIDER, ""),
                )
                disable_idle = (
                    bool(settings["disable_idle_enabled"])
                    and supports_no_idle_mode_provider(electricity_provider)
                )
                new_data[CONF_OPTIMIZATION_DISABLE_IDLE] = disable_idle
                new_options[CONF_OPTIMIZATION_DISABLE_IDLE] = disable_idle
                changes.append(f"Set No Idle mode to {disable_idle}")

            if "auto_apply_reserve_enabled" in settings:
                auto_apply = bool(settings["auto_apply_reserve_enabled"])
                was_auto_apply = bool(
                    new_options.get(
                        CONF_OPTIMIZATION_AUTO_APPLY_RESERVE,
                        new_data.get(CONF_OPTIMIZATION_AUTO_APPLY_RESERVE, False),
                    )
                )
                current_live = new_data.get(
                    CONF_OPTIMIZATION_BACKUP_RESERVE,
                    new_options.get(
                        CONF_OPTIMIZATION_BACKUP_RESERVE,
                        DEFAULT_OPTIMIZATION_BACKUP_RESERVE,
                    ),
                )
                try:
                    current_live = float(current_live)
                except (TypeError, ValueError):
                    current_live = DEFAULT_OPTIMIZATION_BACKUP_RESERVE
                if current_live > 1:
                    current_live = current_live / 100.0

                manual_restore = new_options.get(
                    CONF_OPTIMIZATION_MANUAL_RESERVE,
                    new_data.get(CONF_OPTIMIZATION_MANUAL_RESERVE),
                )
                try:
                    manual_restore = (
                        float(manual_restore)
                        if manual_restore is not None
                        else current_live
                    )
                except (TypeError, ValueError):
                    manual_restore = current_live
                if manual_restore > 1:
                    manual_restore = manual_restore / 100.0
                manual_restore = max(0.0, min(1.0, manual_restore))
                if auto_apply and not was_auto_apply:
                    manual_restore = current_live

                new_data[CONF_OPTIMIZATION_AUTO_APPLY_RESERVE] = auto_apply
                new_options[CONF_OPTIMIZATION_AUTO_APPLY_RESERVE] = auto_apply
                new_data[CONF_OPTIMIZATION_MANUAL_RESERVE] = manual_restore
                new_options[CONF_OPTIMIZATION_MANUAL_RESERVE] = manual_restore
                if not auto_apply:
                    new_data[CONF_OPTIMIZATION_BACKUP_RESERVE] = manual_restore
                    new_options[CONF_OPTIMIZATION_BACKUP_RESERVE] = manual_restore
                changes.append(f"Set auto-apply optimizer reserve to {auto_apply}")

            if "manual_backup_reserve" in settings:
                manual_restore = settings["manual_backup_reserve"]
                try:
                    manual_restore = float(manual_restore)
                except (TypeError, ValueError):
                    manual_restore = None
                if manual_restore is not None:
                    if manual_restore > 1:
                        manual_restore = manual_restore / 100.0
                    manual_restore = max(0.0, min(1.0, manual_restore))
                    new_data[CONF_OPTIMIZATION_MANUAL_RESERVE] = manual_restore
                    new_options[CONF_OPTIMIZATION_MANUAL_RESERVE] = manual_restore
                    changes.append(
                        f"Set manual optimizer reserve to {int(manual_restore * 100)}%"
                    )

            if "cost_function" in settings:
                from ..const import CONF_OPTIMIZATION_COST_FUNCTION
                new_data[CONF_OPTIMIZATION_COST_FUNCTION] = settings["cost_function"]
                changes.append(f"Set cost function to {settings['cost_function']}")

            if "backup_reserve" in settings:
                from ..const import CONF_OPTIMIZATION_BACKUP_RESERVE
                # Store as decimal (0.0-1.0) to match config flow convention
                reserve = settings["backup_reserve"]
                if reserve > 1:
                    reserve = reserve / 100.0
                new_data[CONF_OPTIMIZATION_BACKUP_RESERVE] = reserve
                new_options[CONF_OPTIMIZATION_BACKUP_RESERVE] = reserve
                new_data[CONF_OPTIMIZATION_MANUAL_RESERVE] = reserve
                new_options[CONF_OPTIMIZATION_MANUAL_RESERVE] = reserve
                changes.append(f"Set backup reserve to {int(reserve * 100)}%")

            if "hardware_backup_reserve" in settings:
                from ..const import CONF_HARDWARE_BACKUP_RESERVE
                hw_reserve = settings["hardware_backup_reserve"]
                if hw_reserve > 1:
                    hw_reserve = hw_reserve / 100.0
                new_data[CONF_HARDWARE_BACKUP_RESERVE] = hw_reserve
                new_options[CONF_HARDWARE_BACKUP_RESERVE] = hw_reserve
                new_options.pop("_user_backup_reserve", None)
                changes.append(f"Set hardware backup reserve to {int(hw_reserve * 100)}%")
                # Also update the optimizer's startup reserve so force mode
                # restores to this value instead of the Tesla API value
                opt_coord = self._hass.data.get(DOMAIN, {}).get(config_entry.entry_id, {}).get("optimization_coordinator")
                if opt_coord:
                    opt_coord._startup_backup_reserve = int(hw_reserve * 100)
                    opt_coord._sync_brand_restore_targets(int(hw_reserve * 100))
                    _LOGGER.info("Updated startup backup reserve to %d%%", int(hw_reserve * 100))

            if "max_grid_export_w" in settings:
                raw_export_cap = settings.get("max_grid_export_w")
                if raw_export_cap in (None, "", []):
                    new_data.pop(CONF_OPTIMIZATION_MAX_GRID_EXPORT_W, None)
                    new_options.pop(CONF_OPTIMIZATION_MAX_GRID_EXPORT_W, None)
                    changes.append("Cleared max_grid_export_w")
                else:
                    try:
                        export_cap_w = int(float(raw_export_cap))
                    except (TypeError, ValueError):
                        export_cap_w = None
                    if export_cap_w is not None and export_cap_w >= 0:
                        new_options[CONF_OPTIMIZATION_MAX_GRID_EXPORT_W] = export_cap_w
                        changes.append(f"Set max_grid_export_w to {export_cap_w}")

            spec_key_map = {
                "battery_capacity_wh": CONF_OPTIMIZATION_BATTERY_CAPACITY_WH,
                "max_charge_w": CONF_OPTIMIZATION_MAX_CHARGE_W,
                "max_discharge_w": CONF_OPTIMIZATION_MAX_DISCHARGE_W,
                "max_grid_import_w": CONF_OPTIMIZATION_MAX_GRID_IMPORT_W,
                "horizon_hours": CONF_OPTIMIZATION_HORIZON,
            }
            for payload_key, option_key in spec_key_map.items():
                if payload_key not in settings:
                    continue
                try:
                    spec_value = int(float(settings[payload_key]))
                except (TypeError, ValueError):
                    continue
                if spec_value > 0:
                    new_options[option_key] = spec_value
                    changes.append(f"Set {payload_key} to {spec_value}")
                else:
                    new_options.pop(option_key, None)
                    changes.append(f"Cleared {payload_key}")

            # Update the config entry (both data and options)
            self._hass.config_entries.async_update_entry(config_entry, data=new_data, options=new_options)
            _LOGGER.info(f"Optimization settings updated via API: {changes}")

            # Only reload if enabled/disabled state actually changed
            if enabled_changed:
                _LOGGER.info("Scheduling integration reload to apply optimization changes")
                # Fire and forget - don't await, return immediately to avoid timeout
                self._hass.async_create_task(
                    self._hass.config_entries.async_reload(config_entry.entry_id)
                )
                return web.json_response({
                    "success": True,
                    "changes": changes,
                    "message": "Settings saved. Integration reloading in background..."
                })

            return web.json_response({
                "success": True,
                "changes": changes,
                "message": "Settings saved."
            })

        except Exception as e:
            _LOGGER.error(f"Error updating optimization settings: {e}", exc_info=True)
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

