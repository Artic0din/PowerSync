"""PowerSync options flow."""

from __future__ import annotations

from ._shared import *  # noqa: F403
from ._shared import (  # noqa: F401
    BATTERY_SYSTEM_CONNECTION_KEYS,
    CONF_NETWORK_TARIFF_COMBINED,
    CUSTOM_TOU_PROVIDER_OPTIONS,
    SUNGROW_LEGACY_DUAL_KEYS,
    _LOGGER,
)
from .helpers import *  # noqa: F403
from .setup import PowerSyncConfigFlow

class PowerSyncOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for PowerSync."""

    async def _restore_owned_curtailment_limits(self) -> None:
        """Restore curtailment limits PowerSync has marked active."""
        entry_data = self.hass.data.get(DOMAIN, {}).get(
            self.config_entry.entry_id, {}
        )
        if not entry_data:
            return

        async def _restore_controller(
            label: str,
            coord_key: str,
            state_key: str,
            *extra_state_keys: str,
            restore_when_state_lost: bool = False,
        ) -> None:
            was_curtailed = entry_data.get(state_key) == "curtailed"
            if not was_curtailed and not restore_when_state_lost:
                return

            coord = entry_data.get(coord_key)
            controller = getattr(coord, "_controller", coord)
            if not controller or not hasattr(controller, "restore"):
                _LOGGER.warning(
                    "%s curtailment was active but no restore controller is available",
                    label,
                )
                return

            try:
                success = await controller.restore()
            except Exception as err:
                _LOGGER.error("%s curtailment restore failed: %s", label, err)
                return

            if success:
                entry_data[state_key] = "normal"
                for key in extra_state_keys:
                    entry_data.pop(key, None)
                if was_curtailed:
                    _LOGGER.info(
                        "Solar curtailment disabled - restored %s export limit",
                        label,
                    )
                else:
                    _LOGGER.info(
                        "Solar curtailment disabled - restored %s export limit "
                        "(curtailment state was not marked active)",
                        label,
                    )
            else:
                _LOGGER.error("%s curtailment restore returned false", label)

        await _restore_controller(
            "Sigenergy",
            "sigenergy_coordinator",
            "sigenergy_curtailment_state",
            "_last_sigenergy_curtailment_reapply",
        )
        await _restore_controller(
            "AlphaESS",
            "alphaess_coordinator",
            "alphaess_curtailment_state",
        )
        await _restore_controller(
            "GoodWe",
            "goodwe_coordinator",
            "goodwe_curtailment_state",
            "_last_goodwe_curtailment_reapply",
            restore_when_state_lost=True,
        )

        if entry_data.get("foxess_curtailment_state") == "curtailed":
            fc = entry_data.get("foxess_coordinator")
            controller = getattr(fc, "_controller", fc)
            restore = (
                getattr(fc, "restore_curtailment", None)
                or getattr(controller, "restore", None)
            )
            if restore:
                try:
                    success = await restore()
                except Exception as err:
                    _LOGGER.error("FoxESS curtailment restore failed: %s", err)
                else:
                    if success:
                        entry_data["foxess_curtailment_state"] = "normal"
                        entry_data.pop("_last_foxess_curtailment_reapply", None)
                        _LOGGER.info(
                            "Solar curtailment disabled - restored FoxESS export control"
                        )
                    else:
                        _LOGGER.error("FoxESS curtailment restore returned false")
            else:
                _LOGGER.warning(
                    "FoxESS curtailment was active but no restore controller is available"
                )

        if entry_data.get("solaredge_curtailment_state") == "curtailed":
            controller = entry_data.get("solaredge_controller")
            if controller and hasattr(controller, "restore"):
                try:
                    success = await controller.restore()
                except Exception as err:
                    _LOGGER.error("SolarEdge curtailment restore failed: %s", err)
                else:
                    if success:
                        entry_data["solaredge_curtailment_state"] = "normal"
                        _LOGGER.info(
                            "Solar curtailment disabled - restored SolarEdge active power"
                        )
                    else:
                        _LOGGER.error("SolarEdge curtailment restore returned false")
            else:
                _LOGGER.warning(
                    "SolarEdge curtailment was active but no restore controller is available"
                )

        if entry_data.get("sungrow_curtailment_state") == "curtailed":
            sungrow_coord = entry_data.get("sungrow_coordinator")
            if sungrow_coord and hasattr(sungrow_coord, "set_export_limit"):
                try:
                    success = await sungrow_coord.set_export_limit(None)
                except Exception as err:
                    _LOGGER.error("Sungrow curtailment restore failed: %s", err)
                else:
                    if success:
                        entry_data["sungrow_curtailment_state"] = "normal"
                        entry_data["sungrow_power_limit_w"] = None
                        _LOGGER.info(
                            "Solar curtailment disabled - restored Sungrow export limit"
                        )
                    else:
                        _LOGGER.error("Sungrow curtailment restore returned false")
            else:
                _LOGGER.warning(
                    "Sungrow curtailment was active but no export-limit coordinator is available"
                )

        if entry_data.get("inverter_last_state") == "curtailed":
            controller = entry_data.get("inverter_controller")
            if controller and hasattr(controller, "restore"):
                try:
                    import inspect

                    restore_sig = inspect.signature(controller.restore)
                    if "verify" in restore_sig.parameters:
                        success = await controller.restore(verify=False)
                    else:
                        success = await controller.restore()
                except Exception as err:
                    _LOGGER.error("AC inverter curtailment restore failed: %s", err)
                else:
                    if success:
                        entry_data["inverter_last_state"] = "running"
                        entry_data["inverter_power_limit_w"] = None
                        _LOGGER.info(
                            "Solar curtailment disabled - restored AC inverter"
                        )
                    else:
                        _LOGGER.error("AC inverter curtailment restore returned false")
            else:
                _LOGGER.warning(
                    "AC inverter curtailment was active but no restore controller is available"
                )

    async def _restore_export_rule(self) -> None:
        """Restore active curtailment controls when curtailment is disabled."""
        await self._restore_owned_curtailment_limits()

        battery_system = self._get_option(CONF_BATTERY_SYSTEM, BATTERY_SYSTEM_TESLA)
        if battery_system != BATTERY_SYSTEM_TESLA:
            return

        site_id = self.config_entry.data.get(CONF_TESLA_ENERGY_SITE_ID)
        if not site_id:
            _LOGGER.warning("Cannot restore export rule - no Tesla site ID configured")
            return

        # Determine API provider and get token
        api_provider = self.config_entry.data.get(
            CONF_TESLA_API_PROVIDER, TESLA_PROVIDER_TESLEMETRY
        )

        if api_provider == TESLA_PROVIDER_FLEET_API:
            # Try to get Fleet API token from Tesla Fleet integration
            tesla_fleet_entries = self.hass.config_entries.async_entries("tesla_fleet")
            api_token = None
            for tesla_entry in tesla_fleet_entries:
                if tesla_entry.state == ConfigEntryState.LOADED:
                    try:
                        if CONF_TOKEN in tesla_entry.data:
                            token_data = tesla_entry.data[CONF_TOKEN]
                            if CONF_ACCESS_TOKEN in token_data:
                                api_token = token_data[CONF_ACCESS_TOKEN]
                                break
                    except Exception:
                        pass
            if not api_token:
                _LOGGER.error(
                    "Cannot restore export rule - Fleet API token not available"
                )
                return
            base_url = self.config_entry.data.get(CONF_FLEET_API_BASE_URL, FLEET_API_BASE_URL)
        elif api_provider == TESLA_PROVIDER_POWERSYNC:
            # PowerSync.cc proxy users store their `psync_...` token in the
            # same CONF_TESLEMETRY_API_TOKEN slot, but it must be sent to
            # the PowerSync proxy base URL, not the Teslemetry base URL.
            # Prior code fell through to the `else` branch and hit the
            # wrong endpoint — the call always 401'd silently and the
            # export rule was never restored after curtailment.
            # Guard `.startswith` with `isinstance(api_token, str)` so a
            # non-string truthy value from storage doesn't raise
            # AttributeError before the try block below.
            api_token = self.config_entry.data.get(CONF_TESLEMETRY_API_TOKEN)
            if not isinstance(api_token, str) or not api_token.startswith("psync_"):
                _LOGGER.error(
                    "Cannot restore export rule - PowerSync token missing or "
                    "not a valid psync_ token"
                )
                return
            base_url = POWERSYNC_API_BASE_URL
        else:
            # Teslemetry
            api_token = self.config_entry.data.get(CONF_TESLEMETRY_API_TOKEN)
            if not api_token:
                _LOGGER.error(
                    "Cannot restore export rule - Teslemetry API token not configured"
                )
                return
            base_url = TESLEMETRY_API_BASE_URL

        try:
            session = async_get_clientsession(self.hass)
            headers = {
                "Authorization": f"Bearer {api_token}",
                "Content-Type": "application/json",
            }
            url = f"{base_url}/api/1/energy_sites/{site_id}/grid_import_export"

            async with session.post(
                url,
                headers=headers,
                json={"customer_preferred_export_rule": "battery_ok"},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status == 200:
                    _LOGGER.info(
                        "✅ Solar curtailment disabled - restored export rule to 'battery_ok'"
                    )
                else:
                    error_text = await response.text()
                    _LOGGER.error(
                        f"Failed to restore export rule: {response.status} - {error_text}"
                    )
        except Exception as e:
            _LOGGER.error(f"Error restoring export rule: {e}")

    def _get_option(self, key: str, default: Any = None) -> Any:
        """Get option value with fallback to data for backwards compatibility."""
        return self.config_entry.options.get(
            key, self.config_entry.data.get(key, default)
        )

    def _effective_battery_system(self) -> str:
        """Return the configured battery/control method."""
        return self._get_option(CONF_BATTERY_SYSTEM, BATTERY_SYSTEM_TESLA)

    def _schedule_entry_reload(self) -> None:
        """Reload the entry after structural connection changes."""
        self.hass.async_create_task(
            self.hass.config_entries.async_reload(self.config_entry.entry_id)
        )

    def _save_battery_system_selection(self, battery_system: str) -> None:
        """Persist the selected battery/control method in data and options.

        Popping every OTHER brand's connection/detection keys is essential: the
        per-brand save helpers merge additively, so without this a stale
        CONF_SUNGROW_HOST (etc.) would survive a Sungrow -> GoodWe switch and let
        the runtime dispatch build the wrong coordinator against a dead endpoint.
        """
        new_data = dict(self.config_entry.data)
        new_options = dict(self.config_entry.options)
        new_data[CONF_BATTERY_SYSTEM] = battery_system
        new_options[CONF_BATTERY_SYSTEM] = battery_system

        for brand, keys in BATTERY_SYSTEM_CONNECTION_KEYS.items():
            if brand == battery_system:
                continue
            for key in keys:
                new_data.pop(key, None)
                new_options.pop(key, None)

        self.hass.config_entries.async_update_entry(
            self.config_entry,
            data=new_data,
            options=new_options,
        )

    def _save_connection_and_reload(
        self,
        data_updates: dict[str, Any],
        option_updates: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Persist connection/configuration changes and reload the integration."""
        new_data = dict(self.config_entry.data)
        new_options = dict(self.config_entry.options)
        new_data.update(data_updates)
        new_options.update(option_updates if option_updates is not None else data_updates)
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            data=new_data,
            options=new_options,
        )
        self._schedule_entry_reload()
        return self.async_create_entry(title="", data=new_options)

    def _electricity_provider(self) -> str:
        """Return the configured electricity provider."""
        return self._get_option(CONF_ELECTRICITY_PROVIDER, "amber")

    def _selector_unit(self, unit_kind: str = "minor_rate") -> str:
        """Return a provider-aware unit label for options selectors."""
        return selector_unit_for_provider(
            self._electricity_provider(),
            self.hass,
            unit_kind,
        )

    def _currency(self) -> str:
        """Return the configured currency."""
        return currency_for_provider(self._electricity_provider(), self.hass)

    def _globird_plan_schema(self, current: dict[str, Any] | None = None) -> vol.Schema:
        """Build the GloBird plan selector schema."""
        return _build_globird_plan_schema(
            current,
            rate_unit=self._selector_unit(),
            currency_unit=self._currency(),
        )

    def _save_and_finish(self, section_data: dict[str, Any]) -> FlowResult:
        """Save a single section's data merged with existing options and finish."""
        final = dict(self.config_entry.options)
        final.update(section_data)
        self._apply_legacy_data_key_removals()
        return self.async_create_entry(title="", data=final)

    def _remove_legacy_data_keys(self, keys: tuple[str, ...]) -> None:
        """Mark option-owned keys for removal from legacy config entry data."""
        pending = set(getattr(self, "_legacy_data_keys_to_remove", ()))
        pending.update(keys)
        self._legacy_data_keys_to_remove = tuple(sorted(pending))

    def _apply_legacy_data_key_removals(self) -> None:
        """Remove pending option-owned keys from legacy config entry data."""
        keys = getattr(self, "_legacy_data_keys_to_remove", ())
        if not keys:
            return
        new_data = dict(self.config_entry.data)
        for key in keys:
            new_data.pop(key, None)
        if new_data != self.config_entry.data:
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data=new_data,
            )

    def _remove_legacy_sungrow_dual_options(
        self,
        data: dict[str, Any],
        options: dict[str, Any],
    ) -> None:
        """Remove retired dual-Sungrow configuration keys."""
        for key in SUNGROW_LEGACY_DUAL_KEYS:
            data.pop(key, None)
            options.pop(key, None)

    def _has_weather_entities(self) -> bool:
        """Return whether HA currently exposes any weather entities."""
        try:
            return bool(self.hass.states.async_all("weather"))
        except Exception as err:
            _LOGGER.debug("Unable to enumerate weather entities: %s", err)
            return False

    def _add_weather_entity_selector(self, schema_dict: dict[vol.Marker, Any]) -> None:
        """Add the optional HA weather entity selector only when it can be used."""
        current_weather_entity = _normalize_optional_entity(
            self._get_option(CONF_WEATHER_ENTITY, None)
        )
        if not current_weather_entity and not self._has_weather_entities():
            return

        selector_key = vol.Optional(
            CONF_WEATHER_ENTITY,
            description=(
                {"suggested_value": current_weather_entity}
                if current_weather_entity
                else None
            ),
        )
        schema_dict[selector_key] = EntitySelector(
            EntitySelectorConfig(domain="weather")
        )

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Show options menu -- user picks which section to reconfigure."""
        battery_system = self._effective_battery_system()

        # Build menu options based on current config
        menu_options = ["pricing", "battery_system"]
        current_provider = self._get_option(CONF_ELECTRICITY_PROVIDER, "amber")
        if current_provider in ("flow_power", "globird"):
            menu_options.append("provider_portal")

        # Battery connection settings
        if battery_system == BATTERY_SYSTEM_TESLA:
            menu_options.append("tesla_connection")
        elif battery_system == BATTERY_SYSTEM_SIGENERGY:
            menu_options.append("sigenergy_connection")
        elif battery_system == BATTERY_SYSTEM_SUNGROW:
            menu_options.append("sungrow_connection")
            menu_options.append("history_relink")
        elif battery_system == BATTERY_SYSTEM_FOXESS:
            menu_options.append("foxess_connection_options")
        elif battery_system == BATTERY_SYSTEM_GOODWE:
            menu_options.append("goodwe_connection_options")
        elif battery_system == BATTERY_SYSTEM_ALPHAESS:
            menu_options.append("alphaess_connection")
        elif battery_system == BATTERY_SYSTEM_ESY_SUNHOME:
            menu_options.append("esy_sunhome_connection")
        elif battery_system == BATTERY_SYSTEM_SOLAX:
            menu_options.append("solax_battery_options")
        elif battery_system == BATTERY_SYSTEM_SAJ_H2:
            menu_options.append("saj_h2_connection")
        elif battery_system == BATTERY_SYSTEM_FRONIUS_RESERVA:
            menu_options.append("fronius_reserva_connection")
        elif battery_system == BATTERY_SYSTEM_NEOVOLT:
            menu_options.append("neovolt_connection")
        elif battery_system == BATTERY_SYSTEM_SOLAREDGE:
            menu_options.append("solaredge_connection")
        elif battery_system == BATTERY_SYSTEM_ANKER_SOLIX:
            menu_options.append("anker_solix")
        elif battery_system == BATTERY_SYSTEM_CUSTOM:
            menu_options.append("custom_battery")

        menu_options.extend([
            "optimization",
            "network_export",
            "inverter",
            "curtailment",
            "demand_charges",
            "ev_charging",
            "weather",
            "auto_update",
            "cloud_flow",
        ])

        return self.async_show_menu(
            step_id="init",
            menu_options=menu_options,
        )

    @staticmethod
    def _network_export_power_state_valid(
        hass: HomeAssistant,
        entity_id: str,
        *,
        allow_negative: bool = False,
    ) -> bool:
        """Return whether an entity currently exposes finite W/kW power."""
        state = hass.states.get(entity_id) if entity_id else None
        if state is None or state.state in (None, "", "unknown", "unavailable"):
            return False
        try:
            value = float(state.state)
        except (TypeError, ValueError):
            return False
        if value != value or value in (float("inf"), float("-inf")):
            return False
        if value < 0 and not allow_negative:
            return False
        unit = str((state.attributes or {}).get("unit_of_measurement") or "").lower()
        return unit in {"w", "kw", "watt", "watts", "kilowatt", "kilowatts"}

    def _network_export_active_source_error(self, entity_id: str) -> str | None:
        """Return an active-mode provenance error for a limit source."""
        from homeassistant.helpers import entity_registry as er

        registry_entry = er.async_get(self.hass).async_get(entity_id)
        if registry_entry is None:
            return "network_export_source_unregistered"
        platform = str(getattr(registry_entry, "platform", "") or "").lower()
        if platform in {"template", DOMAIN}:
            return "network_export_source_untrusted"
        if not getattr(registry_entry, "unique_id", None):
            return "network_export_source_untrusted"
        if not (
            getattr(registry_entry, "device_id", None)
            or getattr(registry_entry, "config_entry_id", None)
        ):
            return "network_export_source_untrusted"
        if getattr(registry_entry, "config_entry_id", None) == self.config_entry.entry_id:
            return "network_export_source_untrusted"
        return None

    async def async_step_network_export(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure a read-only SAPN/CSIP-AUS network export envelope."""
        from ..network_envelope import NETWORK_EXPORT_ACTIVE_MODE_RELEASED

        errors: dict[str, str] = {}
        if user_input is not None:
            mode = str(user_input.get(CONF_NETWORK_EXPORT_MODE) or "off")
            limit_entity = str(
                user_input.get(CONF_NETWORK_EXPORT_LIMIT_ENTITY) or ""
            ).strip()
            pcc_entity = str(
                user_input.get(CONF_NETWORK_EXPORT_PCC_POWER_ENTITY) or ""
            ).strip()
            scope = str(
                user_input.get(CONF_NETWORK_EXPORT_SCOPE) or "aggregate_pcc"
            )
            phase_count = int(
                user_input.get(CONF_NETWORK_EXPORT_SITE_PHASE_COUNT) or 1
            )
            fallback_limit = user_input.get(CONF_NETWORK_EXPORT_FALLBACK_LIMIT_W)
            all_der_attested = bool(
                user_input.get(CONF_NETWORK_EXPORT_ALL_DER_ATTESTED, False)
            )

            if mode not in {"off", "monitoring", "active"}:
                errors[CONF_NETWORK_EXPORT_MODE] = "network_export_invalid_mode"
            elif mode == "active" and not NETWORK_EXPORT_ACTIVE_MODE_RELEASED:
                errors[CONF_NETWORK_EXPORT_MODE] = (
                    "network_export_active_release_pending"
                )
            elif mode != "off" and not limit_entity:
                errors[CONF_NETWORK_EXPORT_LIMIT_ENTITY] = (
                    "network_export_limit_required"
                )
            elif mode == "active":
                if fallback_limit is None:
                    errors[CONF_NETWORK_EXPORT_FALLBACK_LIMIT_W] = (
                        "network_export_fallback_required"
                    )
                elif not pcc_entity:
                    errors[CONF_NETWORK_EXPORT_PCC_POWER_ENTITY] = (
                        "network_export_pcc_required"
                    )
                elif not all_der_attested:
                    errors[CONF_NETWORK_EXPORT_ALL_DER_ATTESTED] = (
                        "network_export_der_attestation_required"
                    )
                elif phase_count > 1 and scope != "aggregate_pcc":
                    errors[CONF_NETWORK_EXPORT_SCOPE] = (
                        "network_export_aggregate_required"
                    )
                else:
                    source_error = self._network_export_active_source_error(
                        limit_entity
                    )
                    if source_error:
                        errors[CONF_NETWORK_EXPORT_LIMIT_ENTITY] = source_error
                    elif not self._network_export_power_state_valid(
                        self.hass, limit_entity
                    ):
                        errors[CONF_NETWORK_EXPORT_LIMIT_ENTITY] = (
                            "network_export_limit_invalid"
                        )
                    elif not self._network_export_power_state_valid(
                        self.hass, pcc_entity, allow_negative=True
                    ):
                        errors[CONF_NETWORK_EXPORT_PCC_POWER_ENTITY] = (
                            "network_export_pcc_invalid"
                        )

            if not errors:
                updates = {
                    CONF_NETWORK_EXPORT_MODE: mode,
                    CONF_NETWORK_EXPORT_LIMIT_ENTITY: limit_entity,
                    CONF_NETWORK_EXPORT_STATUS_ENTITY: str(
                        user_input.get(CONF_NETWORK_EXPORT_STATUS_ENTITY) or ""
                    ).strip(),
                    CONF_NETWORK_EXPORT_EXPIRY_ENTITY: str(
                        user_input.get(CONF_NETWORK_EXPORT_EXPIRY_ENTITY) or ""
                    ).strip(),
                    CONF_NETWORK_EXPORT_SCHEDULE_ENTITY: str(
                        user_input.get(CONF_NETWORK_EXPORT_SCHEDULE_ENTITY) or ""
                    ).strip(),
                    CONF_NETWORK_EXPORT_PCC_POWER_ENTITY: pcc_entity,
                    CONF_NETWORK_EXPORT_SCOPE: scope,
                    CONF_NETWORK_EXPORT_FALLBACK_LIMIT_W: (
                        int(round(float(fallback_limit)))
                        if fallback_limit is not None
                        else None
                    ),
                    CONF_NETWORK_EXPORT_SAFETY_MARGIN_W: int(
                        round(
                            float(
                                user_input.get(
                                    CONF_NETWORK_EXPORT_SAFETY_MARGIN_W, 250
                                )
                            )
                        )
                    ),
                    CONF_NETWORK_EXPORT_ALL_DER_ATTESTED: all_der_attested,
                    CONF_NETWORK_EXPORT_SITE_PHASE_COUNT: phase_count,
                }
                return self._save_connection_and_reload(updates)

        default_mode = self._get_option(CONF_NETWORK_EXPORT_MODE, "off")
        if default_mode == "active" and not NETWORK_EXPORT_ACTIVE_MODE_RELEASED:
            default_mode = "monitoring"
        mode_options = [
            SelectOptionDict(value="off", label="Off"),
            SelectOptionDict(
                value="monitoring",
                label="Monitoring only (recommended first)",
            ),
        ]
        if NETWORK_EXPORT_ACTIVE_MODE_RELEASED:
            mode_options.append(
                SelectOptionDict(
                    value="active",
                    label="Active export guard (advanced)",
                )
            )

        return self.async_show_form(
            step_id="network_export",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_NETWORK_EXPORT_MODE,
                        default=default_mode,
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=mode_options,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Optional(
                        CONF_NETWORK_EXPORT_LIMIT_ENTITY,
                        default=self._get_option(
                            CONF_NETWORK_EXPORT_LIMIT_ENTITY, ""
                        ),
                    ): EntitySelector(EntitySelectorConfig(domain="sensor")),
                    vol.Optional(
                        CONF_NETWORK_EXPORT_STATUS_ENTITY,
                        default=self._get_option(
                            CONF_NETWORK_EXPORT_STATUS_ENTITY, ""
                        ),
                    ): EntitySelector(EntitySelectorConfig()),
                    vol.Optional(
                        CONF_NETWORK_EXPORT_EXPIRY_ENTITY,
                        default=self._get_option(
                            CONF_NETWORK_EXPORT_EXPIRY_ENTITY, ""
                        ),
                    ): EntitySelector(EntitySelectorConfig()),
                    vol.Optional(
                        CONF_NETWORK_EXPORT_SCHEDULE_ENTITY,
                        default=self._get_option(
                            CONF_NETWORK_EXPORT_SCHEDULE_ENTITY, ""
                        ),
                    ): EntitySelector(EntitySelectorConfig(domain="sensor")),
                    vol.Optional(
                        CONF_NETWORK_EXPORT_PCC_POWER_ENTITY,
                        default=self._get_option(
                            CONF_NETWORK_EXPORT_PCC_POWER_ENTITY, ""
                        ),
                    ): EntitySelector(EntitySelectorConfig(domain="sensor")),
                    vol.Required(
                        CONF_NETWORK_EXPORT_SCOPE,
                        default=self._get_option(
                            CONF_NETWORK_EXPORT_SCOPE, "aggregate_pcc"
                        ),
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(
                                    value="aggregate_pcc",
                                    label="Aggregate connection point",
                                ),
                                SelectOptionDict(
                                    value="per_phase",
                                    label="Per-phase source (monitoring only)",
                                ),
                            ],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Optional(
                        CONF_NETWORK_EXPORT_FALLBACK_LIMIT_W,
                        description=(
                            {"suggested_value": self._get_option(
                                CONF_NETWORK_EXPORT_FALLBACK_LIMIT_W
                            )}
                            if self._get_option(
                                CONF_NETWORK_EXPORT_FALLBACK_LIMIT_W
                            ) is not None
                            else None
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0,
                            max=100000,
                            step=50,
                            unit_of_measurement="W",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_NETWORK_EXPORT_SAFETY_MARGIN_W,
                        default=self._get_option(
                            CONF_NETWORK_EXPORT_SAFETY_MARGIN_W, 250
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=250,
                            max=10000,
                            step=50,
                            unit_of_measurement="W",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_NETWORK_EXPORT_SITE_PHASE_COUNT,
                        default=self._get_option(
                            CONF_NETWORK_EXPORT_SITE_PHASE_COUNT, 1
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=1,
                            max=3,
                            step=1,
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_NETWORK_EXPORT_ALL_DER_ATTESTED,
                        default=self._get_option(
                            CONF_NETWORK_EXPORT_ALL_DER_ATTESTED, False
                        ),
                    ): BooleanSelector(),
                }
            ),
            errors=errors,
        )

    async def _route_to_battery_options(self, battery_system: str) -> FlowResult:
        """Route to the selected battery/control method options page."""
        if battery_system == BATTERY_SYSTEM_TESLA:
            return await self.async_step_tesla_connection()
        if battery_system == BATTERY_SYSTEM_SIGENERGY:
            return await self.async_step_sigenergy_connection()
        if battery_system == BATTERY_SYSTEM_SUNGROW:
            return await self.async_step_sungrow_connection()
        if battery_system == BATTERY_SYSTEM_FOXESS:
            return await self.async_step_foxess_connection_options()
        if battery_system == BATTERY_SYSTEM_GOODWE:
            return await self.async_step_goodwe_connection_options()
        if battery_system == BATTERY_SYSTEM_ALPHAESS:
            return await self.async_step_alphaess_connection()
        if battery_system == BATTERY_SYSTEM_ESY_SUNHOME:
            return await self.async_step_esy_sunhome_connection()
        if battery_system == BATTERY_SYSTEM_SOLAX:
            return await self.async_step_solax_battery_options()
        if battery_system == BATTERY_SYSTEM_SAJ_H2:
            return await self.async_step_saj_h2_connection()
        if battery_system == BATTERY_SYSTEM_FRONIUS_RESERVA:
            return await self.async_step_fronius_reserva_connection()
        if battery_system == BATTERY_SYSTEM_NEOVOLT:
            return await self.async_step_neovolt_connection()
        if battery_system == BATTERY_SYSTEM_SOLAREDGE:
            return await self.async_step_solaredge_connection()
        if battery_system == BATTERY_SYSTEM_ANKER_SOLIX:
            return await self.async_step_anker_solix()
        if battery_system == BATTERY_SYSTEM_CUSTOM:
            return await self.async_step_custom_battery()
        return await self.async_step_tesla_connection()

    async def async_step_battery_system(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Menu handler: choose or change battery/control method."""
        if user_input is not None:
            battery_system = user_input.get(
                CONF_BATTERY_SYSTEM, BATTERY_SYSTEM_TESLA
            )
            self._save_battery_system_selection(battery_system)
            return await self._route_to_battery_options(battery_system)

        return self.async_show_form(
            step_id="battery_system",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_BATTERY_SYSTEM,
                        default=self._effective_battery_system(),
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(value=k, label=v)
                                for k, v in BATTERY_SYSTEMS.items()
                            ],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
        )

    async def async_step_custom_battery(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Menu handler: custom external-controller entities."""
        default_capacity_wh, default_charge_w, default_discharge_w = (
            _default_optimizer_specs_for(BATTERY_SYSTEM_CUSTOM)
        )
        default_capacity_kwh = default_capacity_wh / 1000
        default_charge_kw = default_charge_w / 1000
        default_discharge_kw = default_discharge_w / 1000

        if user_input is not None:
            backup_reserve = (
                user_input.get(
                    CONF_OPTIMIZATION_BACKUP_RESERVE,
                    int(DEFAULT_OPTIMIZATION_BACKUP_RESERVE * 100),
                )
                / 100.0
            )
            capacity_wh = _form_kwh_to_wh(
                user_input.get(CONF_OPTIMIZATION_BATTERY_CAPACITY_WH),
                default_capacity_kwh,
            )
            charge_w = _form_kw_to_w(
                user_input.get(CONF_OPTIMIZATION_MAX_CHARGE_W),
                default_charge_kw,
            )
            discharge_w = _form_kw_to_w(
                user_input.get(CONF_OPTIMIZATION_MAX_DISCHARGE_W),
                default_discharge_kw,
            )
            max_grid_export_w = _form_optional_kw_to_w(
                user_input.get(CONF_OPTIMIZATION_MAX_GRID_EXPORT_W)
            )
            max_grid_import_w = _form_kw_to_w(
                user_input.get(CONF_OPTIMIZATION_MAX_GRID_IMPORT_W),
                0,
            )
            updates = {
                CONF_BATTERY_SYSTEM: BATTERY_SYSTEM_CUSTOM,
                CONF_CUSTOM_BATTERY_LEVEL_ENTITY: user_input[
                    CONF_CUSTOM_BATTERY_LEVEL_ENTITY
                ],
                CONF_CUSTOM_BATTERY_POWER_ENTITY: user_input[
                    CONF_CUSTOM_BATTERY_POWER_ENTITY
                ],
                CONF_CUSTOM_GRID_POWER_ENTITY: user_input[
                    CONF_CUSTOM_GRID_POWER_ENTITY
                ],
                CONF_CUSTOM_SOLAR_POWER_ENTITY: user_input[
                    CONF_CUSTOM_SOLAR_POWER_ENTITY
                ],
                CONF_CUSTOM_LOAD_POWER_ENTITY: user_input[
                    CONF_CUSTOM_LOAD_POWER_ENTITY
                ],
                CONF_OPTIMIZATION_PROVIDER: OPT_PROVIDER_POWERSYNC,
                CONF_OPTIMIZATION_ENABLED: True,
                CONF_MONITORING_MODE: True,
                CONF_OPTIMIZATION_EV_INTEGRATION: False,
                CONF_OPTIMIZATION_COST_FUNCTION: COST_FUNCTION_COST,
                CONF_OPTIMIZATION_BACKUP_RESERVE: backup_reserve,
                CONF_OPTIMIZATION_BATTERY_CAPACITY_WH: capacity_wh,
                CONF_OPTIMIZATION_MAX_CHARGE_W: charge_w,
                CONF_OPTIMIZATION_MAX_DISCHARGE_W: discharge_w,
                CONF_OPTIMIZATION_MAX_GRID_IMPORT_W: max_grid_import_w,
                CONF_OPTIMIZATION_ALLOW_GRID_CHARGE: bool(
                    user_input.get(CONF_OPTIMIZATION_ALLOW_GRID_CHARGE, True)
                ),
                CONF_OPTIMIZATION_SPREAD_EXPORT_ENABLED: False,
                CONF_OPTIMIZATION_SPREAD_IMPORT_ENABLED: False,
            }
            if max_grid_export_w is not None:
                updates[CONF_OPTIMIZATION_MAX_GRID_EXPORT_W] = max_grid_export_w
            return self._save_connection_and_reload(updates)

        current_capacity_kwh = _stored_wh_to_kwh(
            self._get_option(
                CONF_OPTIMIZATION_BATTERY_CAPACITY_WH,
                default_capacity_wh,
            ),
            default_capacity_wh,
        )
        current_charge_kw = _stored_w_to_kw(
            self._get_option(CONF_OPTIMIZATION_MAX_CHARGE_W, default_charge_w),
            default_charge_w,
        )
        current_discharge_kw = _stored_w_to_kw(
            self._get_option(
                CONF_OPTIMIZATION_MAX_DISCHARGE_W,
                default_discharge_w,
            ),
            default_discharge_w,
        )
        current_max_grid_export_kw = _stored_optional_w_to_kw(
            self._get_option(CONF_OPTIMIZATION_MAX_GRID_EXPORT_W)
        )
        current_max_grid_import_kw = _stored_w_to_kw(
            self._get_option(CONF_OPTIMIZATION_MAX_GRID_IMPORT_W, 0),
            0,
        )
        current_backup_reserve = _stored_ratio_to_percent(
            self._get_option(
                CONF_OPTIMIZATION_BACKUP_RESERVE,
                DEFAULT_OPTIMIZATION_BACKUP_RESERVE,
            ),
            DEFAULT_OPTIMIZATION_BACKUP_RESERVE,
        )

        return self.async_show_form(
            step_id="custom_battery",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CUSTOM_BATTERY_LEVEL_ENTITY,
                        default=self._get_option(
                            CONF_CUSTOM_BATTERY_LEVEL_ENTITY, ""
                        ),
                    ): EntitySelector(EntitySelectorConfig(domain="sensor")),
                    vol.Required(
                        CONF_CUSTOM_BATTERY_POWER_ENTITY,
                        default=self._get_option(
                            CONF_CUSTOM_BATTERY_POWER_ENTITY, ""
                        ),
                    ): EntitySelector(EntitySelectorConfig(domain="sensor")),
                    vol.Required(
                        CONF_CUSTOM_GRID_POWER_ENTITY,
                        default=self._get_option(CONF_CUSTOM_GRID_POWER_ENTITY, ""),
                    ): EntitySelector(EntitySelectorConfig(domain="sensor")),
                    vol.Required(
                        CONF_CUSTOM_SOLAR_POWER_ENTITY,
                        default=self._get_option(CONF_CUSTOM_SOLAR_POWER_ENTITY, ""),
                    ): EntitySelector(EntitySelectorConfig(domain="sensor")),
                    vol.Required(
                        CONF_CUSTOM_LOAD_POWER_ENTITY,
                        default=self._get_option(CONF_CUSTOM_LOAD_POWER_ENTITY, ""),
                    ): EntitySelector(EntitySelectorConfig(domain="sensor")),
                    vol.Required(
                        CONF_OPTIMIZATION_BACKUP_RESERVE,
                        default=current_backup_reserve,
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0,
                            max=100,
                            step=1,
                            unit_of_measurement="%",
                            mode=NumberSelectorMode.SLIDER,
                        )
                    ),
                    vol.Required(
                        CONF_OPTIMIZATION_BATTERY_CAPACITY_WH,
                        default=current_capacity_kwh,
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=1,
                            max=200,
                            step=0.1,
                            unit_of_measurement="kWh",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_OPTIMIZATION_MAX_CHARGE_W,
                        default=current_charge_kw,
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0.1,
                            max=50,
                            step=0.1,
                            unit_of_measurement="kW",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_OPTIMIZATION_MAX_DISCHARGE_W,
                        default=current_discharge_kw,
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0.1,
                            max=50,
                            step=0.1,
                            unit_of_measurement="kW",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Optional(
                        CONF_OPTIMIZATION_MAX_GRID_EXPORT_W,
                        description=(
                            {"suggested_value": current_max_grid_export_kw}
                            if current_max_grid_export_kw is not None
                            else None
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0,
                            max=100,
                            step=0.1,
                            unit_of_measurement="kW",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_OPTIMIZATION_MAX_GRID_IMPORT_W,
                        default=current_max_grid_import_kw,
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0,
                            max=100,
                            step=0.1,
                            unit_of_measurement="kW",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_OPTIMIZATION_ALLOW_GRID_CHARGE,
                        default=self._get_option(
                            CONF_OPTIMIZATION_ALLOW_GRID_CHARGE, True
                        ),
                    ): BooleanSelector(),
                }
            ),
        )

    async def async_step_alphaess_connection(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Menu handler: AlphaESS Modbus and optional Cloud connection."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = (user_input.get(CONF_ALPHAESS_MODBUS_HOST) or "").strip()
            port = int(
                user_input.get(
                    CONF_ALPHAESS_MODBUS_PORT,
                    DEFAULT_ALPHAESS_MODBUS_PORT,
                )
            )
            slave_id = int(
                user_input.get(
                    CONF_ALPHAESS_MODBUS_SLAVE_ID,
                    DEFAULT_ALPHAESS_MODBUS_SLAVE_ID,
                )
            )
            export_limit_kw = user_input.get(CONF_ALPHAESS_EXPORT_LIMIT_KW)
            app_id = (user_input.get(CONF_ALPHAESS_CLOUD_APP_ID) or "").strip()
            app_secret = (
                user_input.get(CONF_ALPHAESS_CLOUD_APP_SECRET) or ""
            ).strip()
            serial = (user_input.get(CONF_ALPHAESS_CLOUD_SERIAL) or "").strip()

            if not host:
                errors["base"] = "alphaess_host_required"
            else:
                from ..inverters.alphaess import AlphaESSController

                controller = AlphaESSController(
                    host=host,
                    port=port,
                    slave_id=slave_id,
                    max_export_limit_kw=export_limit_kw,
                )
                try:
                    connected = await controller.connect()
                    if not connected:
                        errors["base"] = "alphaess_connection_failed"
                    else:
                        state = await controller.get_status()
                        if (
                            state.attributes is None
                            or "battery_soc" not in state.attributes
                        ):
                            errors["base"] = "alphaess_no_data"
                finally:
                    try:
                        await controller.disconnect()
                    except Exception:
                        pass

            if not errors and (app_id or app_secret):
                if not app_id or not app_secret:
                    errors["base"] = "alphaess_cloud_partial"
                else:
                    from ..alphaess_api import AlphaESSCloudClient

                    client = AlphaESSCloudClient(
                        app_id=app_id,
                        app_secret=app_secret,
                        serial=serial,
                    )
                    try:
                        ok, msg = await client.test_connection()
                        if not ok:
                            errors["base"] = "alphaess_cloud_invalid"
                            _LOGGER.warning(
                                "AlphaESS cloud validation failed: %s", msg
                            )
                    finally:
                        try:
                            await client.close()
                        except Exception:
                            pass

            if not errors:
                updates = {
                    CONF_BATTERY_SYSTEM: BATTERY_SYSTEM_ALPHAESS,
                    CONF_ALPHAESS_MODBUS_HOST: host,
                    CONF_ALPHAESS_MODBUS_PORT: port,
                    CONF_ALPHAESS_MODBUS_SLAVE_ID: slave_id,
                    CONF_ALPHAESS_DC_CURTAILMENT_ENABLED: user_input.get(
                        CONF_ALPHAESS_DC_CURTAILMENT_ENABLED, False
                    ),
                    CONF_ALPHAESS_CLOUD_ENABLED: bool(app_id and app_secret),
                }
                if export_limit_kw is not None:
                    updates[CONF_ALPHAESS_EXPORT_LIMIT_KW] = float(export_limit_kw)
                if app_id and app_secret:
                    updates[CONF_ALPHAESS_CLOUD_APP_ID] = app_id
                    updates[CONF_ALPHAESS_CLOUD_APP_SECRET] = app_secret
                    updates[CONF_ALPHAESS_CLOUD_SERIAL] = serial
                option_updates = {
                    key: value
                    for key, value in updates.items()
                    if key != CONF_ALPHAESS_CLOUD_APP_SECRET
                }
                return self._save_connection_and_reload(updates, option_updates)

        return self.async_show_form(
            step_id="alphaess_connection",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_ALPHAESS_MODBUS_HOST,
                        default=self._get_option(CONF_ALPHAESS_MODBUS_HOST, ""),
                    ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
                    vol.Optional(
                        CONF_ALPHAESS_MODBUS_PORT,
                        default=self._get_option(
                            CONF_ALPHAESS_MODBUS_PORT,
                            DEFAULT_ALPHAESS_MODBUS_PORT,
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=1, max=65535, step=1, mode=NumberSelectorMode.BOX
                        )
                    ),
                    vol.Optional(
                        CONF_ALPHAESS_MODBUS_SLAVE_ID,
                        default=self._get_option(
                            CONF_ALPHAESS_MODBUS_SLAVE_ID,
                            DEFAULT_ALPHAESS_MODBUS_SLAVE_ID,
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=1, max=255, step=1, mode=NumberSelectorMode.BOX
                        )
                    ),
                    vol.Optional(
                        CONF_ALPHAESS_EXPORT_LIMIT_KW,
                        description={
                            "suggested_value": self._get_option(
                                CONF_ALPHAESS_EXPORT_LIMIT_KW
                            )
                        },
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0.0,
                            max=100.0,
                            step=0.1,
                            mode=NumberSelectorMode.BOX,
                            unit_of_measurement="kW",
                        )
                    ),
                    vol.Optional(
                        CONF_ALPHAESS_DC_CURTAILMENT_ENABLED,
                        default=self._get_option(
                            CONF_ALPHAESS_DC_CURTAILMENT_ENABLED, False
                        ),
                    ): BooleanSelector(),
                    vol.Optional(
                        CONF_ALPHAESS_CLOUD_APP_ID,
                        default=self._get_option(CONF_ALPHAESS_CLOUD_APP_ID, ""),
                    ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
                    vol.Optional(
                        CONF_ALPHAESS_CLOUD_APP_SECRET,
                        description={"suggested_value": ""},
                    ): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                    vol.Optional(
                        CONF_ALPHAESS_CLOUD_SERIAL,
                        default=self._get_option(CONF_ALPHAESS_CLOUD_SERIAL, ""),
                    ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
                }
            ),
            errors=errors,
        )

    async def async_step_anker_solix(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Menu handler: Anker Solix direct Modbus or HA integration bridge."""
        errors: dict[str, str] = {}

        if user_input is not None:
            connection_type = user_input.get(
                CONF_ANKER_SOLIX_CONNECTION_TYPE,
                ANKER_SOLIX_CONNECTION_MODBUS,
            )
            capacity_kwh = float(
                user_input.get(
                    CONF_ANKER_SOLIX_BATTERY_CAPACITY_KWH,
                    DEFAULT_ANKER_SOLIX_BATTERY_CAPACITY_KWH,
                )
            )
            max_charge_kw = float(
                user_input.get(
                    CONF_ANKER_SOLIX_MAX_CHARGE_KW,
                    DEFAULT_ANKER_SOLIX_MAX_CHARGE_KW,
                )
            )
            max_discharge_kw = float(
                user_input.get(
                    CONF_ANKER_SOLIX_MAX_DISCHARGE_KW,
                    DEFAULT_ANKER_SOLIX_MAX_DISCHARGE_KW,
                )
            )
            updates = {
                CONF_BATTERY_SYSTEM: BATTERY_SYSTEM_ANKER_SOLIX,
                CONF_ANKER_SOLIX_CONNECTION_TYPE: connection_type,
                CONF_ANKER_SOLIX_BATTERY_CAPACITY_KWH: capacity_kwh,
                CONF_ANKER_SOLIX_MAX_CHARGE_KW: max_charge_kw,
                CONF_ANKER_SOLIX_MAX_DISCHARGE_KW: max_discharge_kw,
            }

            try:
                if connection_type == ANKER_SOLIX_CONNECTION_MODBUS:
                    host = (
                        user_input.get(CONF_ANKER_SOLIX_MODBUS_HOST) or ""
                    ).strip()
                    port = int(
                        user_input.get(
                            CONF_ANKER_SOLIX_MODBUS_PORT,
                            DEFAULT_ANKER_SOLIX_MODBUS_PORT,
                        )
                    )
                    slave_id = int(
                        user_input.get(
                            CONF_ANKER_SOLIX_MODBUS_SLAVE_ID,
                            DEFAULT_ANKER_SOLIX_MODBUS_SLAVE_ID,
                        )
                    )
                    if not host:
                        errors["base"] = "anker_solix_host_required"
                    else:
                        from ..inverters.anker_solix import AnkerSolixX1ModbusController

                        controller = AnkerSolixX1ModbusController(
                            host=host,
                            port=port,
                            slave_id=slave_id,
                            battery_capacity_kwh=capacity_kwh,
                            max_charge_kw=max_charge_kw,
                            max_discharge_kw=max_discharge_kw,
                        )
                        try:
                            if not await controller.connect():
                                errors["base"] = "cannot_connect"
                        finally:
                            await controller.disconnect()
                        updates.update(
                            {
                                CONF_ANKER_SOLIX_MODBUS_HOST: host,
                                CONF_ANKER_SOLIX_MODBUS_PORT: port,
                                CONF_ANKER_SOLIX_MODBUS_SLAVE_ID: slave_id,
                            }
                        )
                else:
                    domain = (
                        "anker_solix_official"
                        if connection_type == ANKER_SOLIX_CONNECTION_OFFICIAL_HA
                        else "anker_solix"
                    )
                    anker_entries = self.hass.config_entries.async_entries(domain)
                    if not anker_entries:
                        errors["base"] = "anker_solix_ha_not_installed"
                    else:
                        selected_entry_id = (
                            anker_entries[0].entry_id
                            if len(anker_entries) == 1
                            else user_input.get(CONF_ANKER_SOLIX_CONFIG_ENTRY_ID, "")
                        )
                        entity_prefix = (
                            user_input.get(CONF_ANKER_SOLIX_ENTITY_PREFIX) or ""
                        ).strip()
                        from ..inverters.anker_solix import AnkerSolixEntityController

                        controller = AnkerSolixEntityController(
                            self.hass,
                            integration_domain=domain,
                            config_entry_id=selected_entry_id,
                            entity_prefix=entity_prefix,
                            battery_capacity_kwh=capacity_kwh,
                            max_charge_kw=max_charge_kw,
                            max_discharge_kw=max_discharge_kw,
                        )
                        await controller.connect()
                        updates.update(
                            {
                                CONF_ANKER_SOLIX_CONFIG_ENTRY_ID: selected_entry_id,
                                CONF_ANKER_SOLIX_ENTITY_PREFIX: entity_prefix,
                            }
                        )
            except Exception as exc:
                _LOGGER.debug("Anker Solix options validation failed: %s", exc)
                errors["base"] = "cannot_connect"

            if not errors:
                return self._save_connection_and_reload(updates)

        current = {
            key: self._get_option(key)
            for key in (
                CONF_ANKER_SOLIX_CONNECTION_TYPE,
                CONF_ANKER_SOLIX_MODBUS_HOST,
                CONF_ANKER_SOLIX_MODBUS_PORT,
                CONF_ANKER_SOLIX_MODBUS_SLAVE_ID,
                CONF_ANKER_SOLIX_CONFIG_ENTRY_ID,
                CONF_ANKER_SOLIX_ENTITY_PREFIX,
                CONF_ANKER_SOLIX_BATTERY_CAPACITY_KWH,
                CONF_ANKER_SOLIX_MAX_CHARGE_KW,
                CONF_ANKER_SOLIX_MAX_DISCHARGE_KW,
            )
        }
        if user_input is not None:
            current.update(user_input)
        connection_type = current.get(
            CONF_ANKER_SOLIX_CONNECTION_TYPE,
            ANKER_SOLIX_CONNECTION_MODBUS,
        )
        schema_fields: dict[Any, Any] = {
            vol.Required(
                CONF_ANKER_SOLIX_CONNECTION_TYPE,
                default=connection_type,
            ): SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(value=k, label=v)
                        for k, v in ANKER_SOLIX_CONNECTION_TYPES.items()
                    ],
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
        }

        if connection_type == ANKER_SOLIX_CONNECTION_MODBUS:
            schema_fields[
                vol.Required(
                    CONF_ANKER_SOLIX_MODBUS_HOST,
                    default=current.get(CONF_ANKER_SOLIX_MODBUS_HOST) or "",
                )
            ] = TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT))
            schema_fields[
                vol.Required(
                    CONF_ANKER_SOLIX_MODBUS_PORT,
                    default=current.get(CONF_ANKER_SOLIX_MODBUS_PORT)
                    or DEFAULT_ANKER_SOLIX_MODBUS_PORT,
                )
            ] = NumberSelector(
                NumberSelectorConfig(
                    min=1, max=65535, step=1, mode=NumberSelectorMode.BOX
                )
            )
            schema_fields[
                vol.Required(
                    CONF_ANKER_SOLIX_MODBUS_SLAVE_ID,
                    default=current.get(CONF_ANKER_SOLIX_MODBUS_SLAVE_ID)
                    or DEFAULT_ANKER_SOLIX_MODBUS_SLAVE_ID,
                )
            ] = NumberSelector(
                NumberSelectorConfig(
                    min=1, max=247, step=1, mode=NumberSelectorMode.BOX
                )
            )
        else:
            domain = (
                "anker_solix_official"
                if connection_type == ANKER_SOLIX_CONNECTION_OFFICIAL_HA
                else "anker_solix"
            )
            anker_entries = self.hass.config_entries.async_entries(domain)
            if len(anker_entries) > 1:
                schema_fields[
                    vol.Required(
                        CONF_ANKER_SOLIX_CONFIG_ENTRY_ID,
                        default=current.get(CONF_ANKER_SOLIX_CONFIG_ENTRY_ID)
                        or "",
                    )
                ] = SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(
                                value=e.entry_id,
                                label=e.title or e.entry_id,
                            )
                            for e in anker_entries
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                )
            schema_fields[
                vol.Optional(
                    CONF_ANKER_SOLIX_ENTITY_PREFIX,
                    default=current.get(CONF_ANKER_SOLIX_ENTITY_PREFIX) or "",
                )
            ] = TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT))

        schema_fields[
            vol.Required(
                CONF_ANKER_SOLIX_BATTERY_CAPACITY_KWH,
                default=current.get(CONF_ANKER_SOLIX_BATTERY_CAPACITY_KWH)
                or DEFAULT_ANKER_SOLIX_BATTERY_CAPACITY_KWH,
            )
        ] = NumberSelector(
            NumberSelectorConfig(
                min=1,
                max=200,
                step=0.1,
                unit_of_measurement="kWh",
                mode=NumberSelectorMode.BOX,
            )
        )
        schema_fields[
            vol.Required(
                CONF_ANKER_SOLIX_MAX_CHARGE_KW,
                default=current.get(CONF_ANKER_SOLIX_MAX_CHARGE_KW)
                or DEFAULT_ANKER_SOLIX_MAX_CHARGE_KW,
            )
        ] = NumberSelector(
            NumberSelectorConfig(
                min=0.1,
                max=50,
                step=0.1,
                unit_of_measurement="kW",
                mode=NumberSelectorMode.BOX,
            )
        )
        schema_fields[
            vol.Required(
                CONF_ANKER_SOLIX_MAX_DISCHARGE_KW,
                default=current.get(CONF_ANKER_SOLIX_MAX_DISCHARGE_KW)
                or DEFAULT_ANKER_SOLIX_MAX_DISCHARGE_KW,
            )
        ] = NumberSelector(
            NumberSelectorConfig(
                min=0.1,
                max=50,
                step=0.1,
                unit_of_measurement="kW",
                mode=NumberSelectorMode.BOX,
            )
        )

        return self.async_show_form(
            step_id="anker_solix",
            data_schema=vol.Schema(schema_fields),
            errors=errors,
        )

    async def async_step_auto_update(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Menu handler: scheduled PowerSync HACS auto-update settings."""
        errors: dict[str, str] = {}

        if user_input is not None:
            from ..auto_update import normalize_auto_update_time

            try:
                update_time = normalize_auto_update_time(
                    user_input.get(CONF_AUTO_UPDATE_TIME, DEFAULT_AUTO_UPDATE_TIME)
                )
            except (TypeError, ValueError):
                errors[CONF_AUTO_UPDATE_TIME] = "invalid_time"
            else:
                return self._save_and_finish({
                    CONF_AUTO_UPDATE_ENABLED: user_input.get(
                        CONF_AUTO_UPDATE_ENABLED,
                        False,
                    ),
                    CONF_AUTO_UPDATE_TIME: update_time,
                })

        return self.async_show_form(
            step_id="auto_update",
            data_schema=vol.Schema({
                vol.Optional(
                    CONF_AUTO_UPDATE_ENABLED,
                    default=self._get_option(CONF_AUTO_UPDATE_ENABLED, False),
                ): BooleanSelector(),
                vol.Optional(
                    CONF_AUTO_UPDATE_TIME,
                    default=self._get_option(
                        CONF_AUTO_UPDATE_TIME,
                        DEFAULT_AUTO_UPDATE_TIME,
                    ),
                ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
            }),
            errors=errors,
        )

    async def async_step_cloud_flow(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Menu handler: opt-in PowerSync Cloud energy-flow reporter.

        Pushes local grid/solar/battery/load telemetry to PowerSync Cloud
        (ChargeHQ-compatible shape) so accounts without a Tesla energy site
        can still get charge-on-solar decisions. Requires the
        `ha_flow_reporter` beta flag on the account -- the reporter simply
        backs off if it isn't granted yet.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            enabled = bool(user_input.get(CONF_CLOUD_FLOW_REPORT, False))
            grid_entity = user_input.get(CONF_CLOUD_FLOW_GRID_ENTITY, "")
            if enabled and not grid_entity:
                errors[CONF_CLOUD_FLOW_GRID_ENTITY] = "cloud_flow_grid_entity_required"
            else:
                return self._save_and_finish({
                    CONF_CLOUD_FLOW_REPORT: enabled,
                    CONF_CLOUD_FLOW_GRID_ENTITY: grid_entity,
                    CONF_CLOUD_FLOW_SOLAR_ENTITY: user_input.get(
                        CONF_CLOUD_FLOW_SOLAR_ENTITY, ""
                    ),
                    CONF_CLOUD_FLOW_BATTERY_POWER_ENTITY: user_input.get(
                        CONF_CLOUD_FLOW_BATTERY_POWER_ENTITY, ""
                    ),
                    CONF_CLOUD_FLOW_BATTERY_SOC_ENTITY: user_input.get(
                        CONF_CLOUD_FLOW_BATTERY_SOC_ENTITY, ""
                    ),
                    CONF_CLOUD_FLOW_LOAD_ENTITY: user_input.get(
                        CONF_CLOUD_FLOW_LOAD_ENTITY, ""
                    ),
                    CONF_CLOUD_FLOW_INVERT_GRID: bool(
                        user_input.get(CONF_CLOUD_FLOW_INVERT_GRID, False)
                    ),
                })

        current_grid_entity = self._get_option(CONF_CLOUD_FLOW_GRID_ENTITY, "")
        current_solar_entity = self._get_option(CONF_CLOUD_FLOW_SOLAR_ENTITY, "")
        current_battery_power_entity = self._get_option(
            CONF_CLOUD_FLOW_BATTERY_POWER_ENTITY, ""
        )
        current_battery_soc_entity = self._get_option(
            CONF_CLOUD_FLOW_BATTERY_SOC_ENTITY, ""
        )
        current_load_entity = self._get_option(CONF_CLOUD_FLOW_LOAD_ENTITY, "")

        return self.async_show_form(
            step_id="cloud_flow",
            data_schema=vol.Schema({
                vol.Optional(
                    CONF_CLOUD_FLOW_REPORT,
                    default=self._get_option(CONF_CLOUD_FLOW_REPORT, False),
                ): BooleanSelector(),
                vol.Optional(
                    CONF_CLOUD_FLOW_GRID_ENTITY,
                    description=(
                        {"suggested_value": current_grid_entity}
                        if current_grid_entity
                        else None
                    ),
                ): EntitySelector(EntitySelectorConfig(domain="sensor")),
                vol.Optional(
                    CONF_CLOUD_FLOW_SOLAR_ENTITY,
                    description=(
                        {"suggested_value": current_solar_entity}
                        if current_solar_entity
                        else None
                    ),
                ): EntitySelector(EntitySelectorConfig(domain="sensor")),
                vol.Optional(
                    CONF_CLOUD_FLOW_BATTERY_POWER_ENTITY,
                    description=(
                        {"suggested_value": current_battery_power_entity}
                        if current_battery_power_entity
                        else None
                    ),
                ): EntitySelector(EntitySelectorConfig(domain="sensor")),
                vol.Optional(
                    CONF_CLOUD_FLOW_BATTERY_SOC_ENTITY,
                    description=(
                        {"suggested_value": current_battery_soc_entity}
                        if current_battery_soc_entity
                        else None
                    ),
                ): EntitySelector(EntitySelectorConfig(domain="sensor")),
                vol.Optional(
                    CONF_CLOUD_FLOW_LOAD_ENTITY,
                    description=(
                        {"suggested_value": current_load_entity}
                        if current_load_entity
                        else None
                    ),
                ): EntitySelector(EntitySelectorConfig(domain="sensor")),
                vol.Optional(
                    CONF_CLOUD_FLOW_INVERT_GRID,
                    default=self._get_option(CONF_CLOUD_FLOW_INVERT_GRID, False),
                ): BooleanSelector(),
            }),
            errors=errors,
        )

    async def async_step_pricing(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Menu handler: choose or change electricity provider, then configure it."""
        self._from_menu = True

        if user_input is not None:
            provider = user_input.get(CONF_ELECTRICITY_PROVIDER, "amber")
            self._provider = provider
            # Save the provider choice immediately so downstream steps see it
            current_options = dict(self.config_entry.options)
            current_options[CONF_ELECTRICITY_PROVIDER] = provider
            self.hass.config_entries.async_update_entry(
                self.config_entry, options=current_options
            )

            if provider == "amber":
                return await self.async_step_amber_options()
            if provider == "flow_power":
                return await self.async_step_flow_power_options()
            if provider == "covau":
                return await self.async_step_covau_options()
            if provider in CUSTOM_TOU_PROVIDER_OPTIONS:
                return await self._async_route_custom_tou_options(provider)
            if provider == "localvolts":
                return await self.async_step_localvolts_options()
            if provider == "octopus":
                return await self.async_step_octopus_options()
            if provider == "epex":
                return await self.async_step_epex_options()
            if provider == "nz":
                return await self.async_step_nz_options()
            return await self.async_step_amber_options()

        current_provider = self._get_option(CONF_ELECTRICITY_PROVIDER, "amber")

        return self.async_show_form(
            step_id="pricing",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_ELECTRICITY_PROVIDER, default=current_provider
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(value=k, label=v)
                                for k, v in ELECTRICITY_PROVIDERS.items()
                            ],
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                }
            ),
        )

    def _covau_options_energy_entity_valid(self, entity_id: str) -> bool:
        if not entity_id:
            return True
        state = self.hass.states.get(entity_id)
        if state is None:
            return False
        attrs = state.attributes or {}
        return (
            str(attrs.get("state_class") or "").lower() == "total_increasing"
            and str(attrs.get("device_class") or "").lower() == "energy"
            and str(attrs.get("unit_of_measurement") or "").lower()
            in {"wh", "kwh", "mwh"}
        )

    async def async_step_covau_options(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Update an exact CovaU snapshot and its measured settlement meters."""
        errors: dict[str, str] = {}
        current_plan_id = str(self._get_option(CONF_COVAU_PLAN_ID, "") or "")
        current_distributor = str(
            self._get_option(CONF_COVAU_DISTRIBUTOR, "") or ""
        )
        current_snapshot = self._get_option(CONF_COVAU_PLAN_SNAPSHOT, None)
        current_raw = self._get_option(CONF_COVAU_PLAN_RAW, None)
        refresh_key = "covau_refresh_public_plan"

        if user_input is not None:
            plan_id = str(user_input.get(CONF_COVAU_PLAN_ID) or "")
            distributor = str(user_input.get(CONF_COVAU_DISTRIBUTOR) or "")
            import_entity = str(
                user_input.get(CONF_COVAU_IMPORT_ENERGY_ENTITY) or ""
            )
            export_entity = str(
                user_input.get(CONF_COVAU_EXPORT_ENERGY_ENTITY) or ""
            )
            refresh_public_plan = bool(user_input.get(refresh_key, False))
            metadata = SUPPORTED_SOLARMAX_PLANS.get(plan_id)
            keeping_cached_manual = (
                plan_id == current_plan_id
                and isinstance(current_snapshot, dict)
                and bool(current_snapshot.get("manual"))
            )
            if metadata is None and not keeping_cached_manual:
                errors[CONF_COVAU_PLAN_ID] = "covau_unsupported_plan"
            elif metadata is not None and distributor != metadata["distributor"]:
                errors[CONF_COVAU_DISTRIBUTOR] = "covau_distributor_mismatch"
            elif keeping_cached_manual and distributor != current_distributor:
                errors[CONF_COVAU_DISTRIBUTOR] = "covau_distributor_mismatch"
            elif import_entity and not self._covau_options_energy_entity_valid(import_entity):
                errors[CONF_COVAU_IMPORT_ENERGY_ENTITY] = "covau_energy_meter_invalid"
            elif export_entity and not self._covau_options_energy_entity_valid(export_entity):
                errors[CONF_COVAU_EXPORT_ENERGY_ENTITY] = "covau_energy_meter_invalid"
            else:
                snapshot_dict = current_snapshot
                raw = current_raw
                if (
                    not keeping_cached_manual
                    and (
                        refresh_public_plan
                        or plan_id != current_plan_id
                        or not isinstance(snapshot_dict, dict)
                    )
                ):
                    try:
                        raw = await async_fetch_covau_plan(self.hass, plan_id)
                        snapshot_dict = normalize_covau_plan(raw, plan_id).to_dict()
                    except Exception as err:
                        _LOGGER.warning(
                            "CovaU public plan fetch failed for %s: %s", plan_id, err
                        )
                        errors["base"] = "cannot_connect"
                if not errors:
                    return self._save_connection_and_reload({
                        CONF_ELECTRICITY_PROVIDER: "covau",
                        CONF_COVAU_PLAN_ID: plan_id,
                        CONF_COVAU_DISTRIBUTOR: distributor,
                        CONF_COVAU_PLAN_RAW: raw,
                        CONF_COVAU_PLAN_SNAPSHOT: snapshot_dict,
                        CONF_COVAU_IMPORT_ENERGY_ENTITY: import_entity,
                        CONF_COVAU_EXPORT_ENERGY_ENTITY: export_entity,
                    })

        plan_options = [
            SelectOptionDict(
                value=plan_id,
                label=f"{metadata['display_name']} — {metadata['distributor']}",
            )
            for plan_id, metadata in SUPPORTED_SOLARMAX_PLANS.items()
        ]
        if current_plan_id and current_plan_id not in SUPPORTED_SOLARMAX_PLANS:
            plan_options.append(
                SelectOptionDict(
                    value=current_plan_id,
                    label=f"Cached manual plan — {current_plan_id}",
                )
            )
        distributors = sorted(
            {item["distributor"] for item in SUPPORTED_SOLARMAX_PLANS.values()}
            | ({current_distributor} if current_distributor else set())
        )
        return self.async_show_form(
            step_id="covau_options",
            data_schema=vol.Schema({
                vol.Required(
                    CONF_COVAU_PLAN_ID,
                    default=current_plan_id or next(iter(SUPPORTED_SOLARMAX_PLANS)),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=plan_options,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_COVAU_DISTRIBUTOR,
                    default=current_distributor or distributors[0],
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=value, label=value)
                            for value in distributors
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(
                    CONF_COVAU_IMPORT_ENERGY_ENTITY,
                    default=self._get_option(CONF_COVAU_IMPORT_ENERGY_ENTITY, ""),
                ): EntitySelector(EntitySelectorConfig(domain="sensor")),
                vol.Optional(
                    CONF_COVAU_EXPORT_ENERGY_ENTITY,
                    default=self._get_option(CONF_COVAU_EXPORT_ENERGY_ENTITY, ""),
                ): EntitySelector(EntitySelectorConfig(domain="sensor")),
                vol.Required(refresh_key, default=False): BooleanSelector(),
            }),
            errors=errors,
        )

    async def async_step_provider_portal(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Menu handler: configure provider portal account login."""
        provider = self._get_option(CONF_ELECTRICITY_PROVIDER, "amber")
        if provider == "flow_power":
            return await self.async_step_flow_power_portal_options()
        if provider == "globird":
            return await self.async_step_globird_portal_options()
        return await self.async_step_init()

    async def async_step_tesla_connection(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Menu handler: Tesla Energy/EV API provider + local gateway IP."""
        errors: dict[str, str] = {}

        if user_input is not None:
            tesla_provider = user_input.get(
                CONF_TESLA_API_PROVIDER, TESLA_PROVIDER_TESLEMETRY
            )
            ev_choice = user_input.get(
                CONF_TESLA_EV_API_PROVIDER, TESLA_EV_API_PROVIDER_NONE
            )
            # Optional Powerwall local LAN access. Empty gateway IP clears it
            # (back to cloud-only mode); a non-empty IP requires the gateway
            # customer password.
            gateway_ip_raw = user_input.get(CONF_POWERWALL_LOCAL_IP, "")
            gateway_ip = (gateway_ip_raw or "").strip()

            # Validate EV provider
            detected = _detect_tesla_ev_integrations(self.hass)
            if (
                ev_choice == TESLA_EV_API_PROVIDER_FLEET_API
                and not detected["tesla_fleet"]
            ):
                errors[CONF_TESLA_EV_API_PROVIDER] = "tesla_fleet_not_installed"
            elif (
                ev_choice == TESLA_EV_API_PROVIDER_TESLEMETRY
                and not detected["teslemetry"]
                and not self.config_entry.data.get(CONF_TESLA_EV_TELEMETRY_TOKEN)
            ):
                self._pending_init_tesla_input = dict(user_input)
                self._tesla_connection_return = True
                return await self.async_step_options_tesla_ev_token()

            if not errors:
                new_data = dict(self.config_entry.data)
                new_data[CONF_TESLA_API_PROVIDER] = tesla_provider
                new_data[CONF_TESLA_EV_API_PROVIDER] = ev_choice
                # Persist gateway IP changes; remove the key entirely when
                # cleared so the diagnostic binary_sensor flips correctly
                # rather than reading an empty string as "set".
                if gateway_ip:
                    new_data[CONF_POWERWALL_LOCAL_IP] = gateway_ip
                else:
                    new_data.pop(CONF_POWERWALL_LOCAL_IP, None)
                self.hass.config_entries.async_update_entry(
                    self.config_entry, data=new_data
                )

                # If user picked PowerSync or Teslemetry, route to token step
                self._tesla_provider = tesla_provider
                if tesla_provider == TESLA_PROVIDER_POWERSYNC:
                    self._tesla_connection_return = True
                    return await self.async_step_powersync_token()
                if tesla_provider == TESLA_PROVIDER_TESLEMETRY:
                    self._tesla_connection_return = True
                    return await self.async_step_teslemetry_token()

                # Fleet API -- save directly
                self._schedule_entry_reload()
                return self.async_create_entry(
                    title="", data=dict(self.config_entry.options)
                )

        current_tesla_provider = self.config_entry.data.get(
            CONF_TESLA_API_PROVIDER, TESLA_PROVIDER_TESLEMETRY
        )
        current_ev_provider = self.config_entry.data.get(
            CONF_TESLA_EV_API_PROVIDER, TESLA_EV_API_PROVIDER_NONE
        )
        current_gateway_ip = self.config_entry.data.get(
            CONF_POWERWALL_LOCAL_IP, ""
        )

        tesla_providers = {
            TESLA_PROVIDER_POWERSYNC: "PowerSync (Free - sign in with Tesla, recommended)",
            TESLA_PROVIDER_FLEET_API: "Tesla Fleet API (Free - requires Tesla Fleet integration)",
            TESLA_PROVIDER_TESLEMETRY: "Teslemetry (~$4/month)",
        }
        tesla_ev_providers = _build_tesla_ev_provider_choices(self.hass)

        return self.async_show_form(
            step_id="tesla_connection",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_TESLA_API_PROVIDER,
                        default=current_tesla_provider,
                    ): SelectSelector(SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=k, label=v)
                            for k, v in tesla_providers.items()
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )),
                    vol.Required(
                        CONF_TESLA_EV_API_PROVIDER,
                        default=current_ev_provider,
                    ): SelectSelector(SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=k, label=v)
                            for k, v in tesla_ev_providers.items()
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )),
                    # Optional gateway LAN IP for direct local features.
                    # Pairing is cloud-based; gateway control uses RSA
                    # signing — no password required.
                    vol.Optional(
                        CONF_POWERWALL_LOCAL_IP,
                        default=current_gateway_ip,
                    ): str,
                }
            ),
            errors=errors,
        )

    async def async_step_sigenergy_connection(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Menu handler: Sigenergy Modbus connection and Cloud credentials."""
        from ..sigenergy_api import encode_sigenergy_password

        errors: dict[str, str] = {}

        if user_input is not None:
            modbus_host = user_input.get(CONF_SIGENERGY_MODBUS_HOST, "").strip()
            if not modbus_host:
                errors["base"] = "modbus_host_required"
            else:
                new_data = dict(self.config_entry.data)
                new_data[CONF_SIGENERGY_MODBUS_HOST] = modbus_host
                new_data[CONF_SIGENERGY_MODBUS_PORT] = user_input.get(
                    CONF_SIGENERGY_MODBUS_PORT, DEFAULT_SIGENERGY_MODBUS_PORT
                )
                new_data[CONF_SIGENERGY_MODBUS_SLAVE_ID] = user_input.get(
                    CONF_SIGENERGY_MODBUS_SLAVE_ID, DEFAULT_SIGENERGY_MODBUS_SLAVE_ID
                )
                new_data[CONF_SIGENERGY_DC_CURTAILMENT_ENABLED] = user_input.get(
                    CONF_SIGENERGY_DC_CURTAILMENT_ENABLED, False
                )
                export_limit = user_input.get(CONF_SIGENERGY_EXPORT_LIMIT_KW)
                if export_limit is not None:
                    new_data[CONF_SIGENERGY_EXPORT_LIMIT_KW] = export_limit
                elif CONF_SIGENERGY_EXPORT_LIMIT_KW in new_data:
                    del new_data[CONF_SIGENERGY_EXPORT_LIMIT_KW]

                # Cloud credentials
                sigen_username = user_input.get(CONF_SIGENERGY_USERNAME, "").strip()
                sigen_password = user_input.get(CONF_SIGENERGY_PASSWORD, "").strip()
                sigen_pass_enc = user_input.get(CONF_SIGENERGY_PASS_ENC, "").strip()
                sigen_device_id = user_input.get(CONF_SIGENERGY_DEVICE_ID, "").strip()
                sigen_cloud_region = user_input.get(
                    CONF_SIGENERGY_CLOUD_REGION,
                    DEFAULT_SIGENERGY_CLOUD_REGION,
                )
                sigen_station_id = user_input.get(CONF_SIGENERGY_STATION_ID, "").strip()

                if sigen_pass_enc:
                    final_pass_enc = sigen_pass_enc
                elif sigen_password:
                    final_pass_enc = encode_sigenergy_password(sigen_password)
                else:
                    final_pass_enc = ""

                if sigen_username:
                    new_data[CONF_SIGENERGY_USERNAME] = sigen_username
                if final_pass_enc:
                    new_data[CONF_SIGENERGY_PASS_ENC] = final_pass_enc
                if sigen_device_id:
                    new_data[CONF_SIGENERGY_DEVICE_ID] = sigen_device_id
                previous_cloud_region = new_data.get(
                    CONF_SIGENERGY_CLOUD_REGION,
                    DEFAULT_SIGENERGY_CLOUD_REGION,
                )
                new_data[CONF_SIGENERGY_CLOUD_REGION] = sigen_cloud_region
                if previous_cloud_region != sigen_cloud_region:
                    new_data.pop(CONF_SIGENERGY_ACCESS_TOKEN, None)
                    new_data.pop(CONF_SIGENERGY_REFRESH_TOKEN, None)
                    new_data.pop(CONF_SIGENERGY_TOKEN_EXPIRES_AT, None)
                if sigen_station_id:
                    previous_station_id = new_data.get(CONF_SIGENERGY_STATION_ID)
                    new_data[CONF_SIGENERGY_STATION_ID] = sigen_station_id
                    if previous_station_id != sigen_station_id:
                        new_data.pop(CONF_SIGENERGY_TARIFF_STATION_ID, None)
                        new_data.pop(CONF_SIGENERGY_TARIFF_STATION_SOURCE_ID, None)

                self.hass.config_entries.async_update_entry(
                    self.config_entry, data=new_data
                )
                self._schedule_entry_reload()
                return self.async_create_entry(
                    title="", data=dict(self.config_entry.options)
                )

        current_modbus_host = self._get_option(CONF_SIGENERGY_MODBUS_HOST, "")
        current_modbus_port = self._get_option(
            CONF_SIGENERGY_MODBUS_PORT, DEFAULT_SIGENERGY_MODBUS_PORT
        )
        current_modbus_slave_id = self._get_option(
            CONF_SIGENERGY_MODBUS_SLAVE_ID, DEFAULT_SIGENERGY_MODBUS_SLAVE_ID
        )
        current_dc_curtailment = self._get_option(
            CONF_SIGENERGY_DC_CURTAILMENT_ENABLED, False
        )
        current_export_limit = self.config_entry.data.get(
            CONF_SIGENERGY_EXPORT_LIMIT_KW
        )
        current_sigen_username = self.config_entry.data.get(CONF_SIGENERGY_USERNAME, "")
        current_sigen_device_id = self.config_entry.data.get(
            CONF_SIGENERGY_DEVICE_ID, ""
        )
        current_sigen_cloud_region = self.config_entry.data.get(
            CONF_SIGENERGY_CLOUD_REGION, DEFAULT_SIGENERGY_CLOUD_REGION
        )
        current_sigen_station_id = self.config_entry.data.get(
            CONF_SIGENERGY_STATION_ID, ""
        )

        return self.async_show_form(
            step_id="sigenergy_connection",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SIGENERGY_MODBUS_HOST,
                        default=current_modbus_host,
                    ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
                    vol.Optional(
                        CONF_SIGENERGY_MODBUS_PORT,
                        default=current_modbus_port,
                    ): NumberSelector(NumberSelectorConfig(
                        min=1, max=65535, step=1, mode=NumberSelectorMode.BOX,
                    )),
                    vol.Optional(
                        CONF_SIGENERGY_MODBUS_SLAVE_ID,
                        default=current_modbus_slave_id,
                    ): NumberSelector(NumberSelectorConfig(
                        min=0, max=247, step=1, mode=NumberSelectorMode.BOX,
                    )),
                    vol.Optional(
                        CONF_SIGENERGY_DC_CURTAILMENT_ENABLED,
                        default=current_dc_curtailment,
                    ): BooleanSelector(),
                    vol.Optional(
                        CONF_SIGENERGY_EXPORT_LIMIT_KW,
                        description={"suggested_value": current_export_limit},
                    ): NumberSelector(NumberSelectorConfig(
                        min=0, max=100, step=0.1, unit_of_measurement="kW",
                        mode=NumberSelectorMode.BOX,
                    )),
                    vol.Optional(
                        CONF_SIGENERGY_USERNAME,
                        default=current_sigen_username,
                        description={"suggested_value": current_sigen_username},
                    ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
                    vol.Optional(
                        CONF_SIGENERGY_PASSWORD,
                        description={"suggested_value": ""},
                    ): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
                    vol.Optional(
                        CONF_SIGENERGY_PASS_ENC,
                        description={"suggested_value": ""},
                    ): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
                    vol.Optional(
                        CONF_SIGENERGY_DEVICE_ID,
                        default=current_sigen_device_id,
                        description={"suggested_value": current_sigen_device_id},
                    ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
                    vol.Required(
                        CONF_SIGENERGY_CLOUD_REGION,
                        default=current_sigen_cloud_region,
                    ): SelectSelector(SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=k, label=v)
                            for k, v in SIGENERGY_CLOUD_REGIONS.items()
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )),
                    vol.Optional(
                        CONF_SIGENERGY_STATION_ID,
                        default=current_sigen_station_id,
                        description={"suggested_value": current_sigen_station_id},
                    ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
                }
            ),
            errors=errors,
        )

    async def async_step_sungrow_connection(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Menu handler: Sungrow Modbus connection settings."""
        errors: dict[str, str] = {}

        if user_input is not None:
            modbus_host = user_input.get(CONF_SUNGROW_HOST, "").strip()
            if not modbus_host:
                errors["base"] = "sungrow_host_required"
            else:
                new_data = dict(self.config_entry.data)
                new_options = dict(self.config_entry.options)
                sungrow_port = user_input.get(
                    CONF_SUNGROW_PORT, DEFAULT_SUNGROW_PORT
                )
                sungrow_slave_id = user_input.get(
                    CONF_SUNGROW_SLAVE_ID, DEFAULT_SUNGROW_SLAVE_ID
                )
                new_data[CONF_SUNGROW_HOST] = modbus_host
                new_data[CONF_SUNGROW_PORT] = sungrow_port
                new_data[CONF_SUNGROW_SLAVE_ID] = sungrow_slave_id
                new_options[CONF_SUNGROW_HOST] = modbus_host
                new_options[CONF_SUNGROW_PORT] = sungrow_port
                new_options[CONF_SUNGROW_SLAVE_ID] = sungrow_slave_id
                self._remove_legacy_sungrow_dual_options(new_data, new_options)

                self.hass.config_entries.async_update_entry(
                    self.config_entry, data=new_data
                )
                self._schedule_entry_reload()
                return self.async_create_entry(title="", data=new_options)

        current_host = self._get_option(CONF_SUNGROW_HOST, "")
        current_port = self._get_option(CONF_SUNGROW_PORT, DEFAULT_SUNGROW_PORT)
        current_slave_id = self._get_option(
            CONF_SUNGROW_SLAVE_ID, DEFAULT_SUNGROW_SLAVE_ID
        )

        return self.async_show_form(
            step_id="sungrow_connection",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SUNGROW_HOST,
                        default=current_host,
                    ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
                    vol.Optional(
                        CONF_SUNGROW_PORT,
                        default=current_port,
                    ): NumberSelector(NumberSelectorConfig(
                        min=1, max=65535, step=1, mode=NumberSelectorMode.BOX,
                    )),
                    vol.Optional(
                        CONF_SUNGROW_SLAVE_ID,
                        default=current_slave_id,
                    ): NumberSelector(NumberSelectorConfig(
                        min=1, max=247, step=1, mode=NumberSelectorMode.BOX,
                    )),
                }
            ),
            errors=errors,
        )

    async def async_step_history_relink(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Menu handler: relink mkaiser Sungrow history to PowerSync entities."""
        errors: dict[str, str] = {}

        if user_input is not None:
            if user_input.get("confirm") is not True:
                errors["base"] = "confirm_required"
            else:
                result = apply_history_relink(self.hass, self.config_entry)
                if result.get("applied_count", 0) > 0:
                    return self.async_create_entry(
                        title="",
                        data=dict(self.config_entry.options),
                    )
                errors["base"] = "no_ready_history_relinks"

        preview = preview_history_relink(self.hass, self.config_entry)
        return self.async_show_form(
            step_id="history_relink",
            data_schema=vol.Schema(
                {
                    vol.Required("confirm", default=False): BooleanSelector(),
                }
            ),
            errors=errors,
            description_placeholders={
                "summary": format_history_relink_summary(preview),
            },
        )

    async def async_step_foxess_connection_options(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Menu handler: FoxESS Modbus connection settings."""
        errors: dict[str, str] = {}

        if user_input is not None:
            conn_type = user_input.get(
                CONF_FOXESS_CONNECTION_TYPE, FOXESS_CONNECTION_TCP
            )
            modbus_host = user_input.get(CONF_FOXESS_HOST, "").strip()
            serial_port = user_input.get(CONF_FOXESS_SERIAL_PORT, "").strip()
            cloud_api_key = user_input.get(CONF_FOXESS_CLOUD_API_KEY, "").strip()
            cloud_device_sn = user_input.get(CONF_FOXESS_CLOUD_DEVICE_SN, "").strip()
            entity_entry_id = user_input.get(CONF_FOXESS_ENTITY_CONFIG_ENTRY_ID, "").strip()
            entity_prefix = user_input.get(CONF_FOXESS_ENTITY_PREFIX, "").strip()
            entity_entries = _foxess_modbus_entry_options(self.hass)
            if conn_type == FOXESS_CONNECTION_ENTITY and not entity_entry_id and len(entity_entries) == 1:
                entity_entry_id = entity_entries[0]["value"]

            if conn_type == FOXESS_CONNECTION_TCP and not modbus_host:
                errors["base"] = "foxess_host_required"
            elif conn_type == FOXESS_CONNECTION_SERIAL and not serial_port:
                errors["base"] = "foxess_serial_required"
            elif conn_type == FOXESS_CONNECTION_CLOUD and (not cloud_api_key or not cloud_device_sn):
                errors["base"] = "foxess_cloud_required"
            elif conn_type == FOXESS_CONNECTION_ENTITY:
                valid, error = await _validate_foxess_entity_bridge(
                    self.hass,
                    entity_entry_id,
                    entity_prefix,
                )
                if not valid:
                    errors["base"] = error or "foxess_entity_connect_failed"
            if not errors:
                new_data = dict(self.config_entry.data)
                new_data[CONF_FOXESS_CONNECTION_TYPE] = conn_type
                if conn_type == FOXESS_CONNECTION_TCP:
                    new_data[CONF_FOXESS_HOST] = modbus_host
                    new_data[CONF_FOXESS_PORT] = user_input.get(
                        CONF_FOXESS_PORT, DEFAULT_FOXESS_PORT
                    )
                elif conn_type == FOXESS_CONNECTION_SERIAL:
                    new_data[CONF_FOXESS_SERIAL_PORT] = serial_port
                    new_data[CONF_FOXESS_SERIAL_BAUDRATE] = user_input.get(
                        CONF_FOXESS_SERIAL_BAUDRATE, DEFAULT_FOXESS_SERIAL_BAUDRATE
                    )
                elif conn_type == FOXESS_CONNECTION_ENTITY:
                    if entity_entry_id:
                        new_data[CONF_FOXESS_ENTITY_CONFIG_ENTRY_ID] = entity_entry_id
                    else:
                        new_data.pop(CONF_FOXESS_ENTITY_CONFIG_ENTRY_ID, None)
                    if entity_prefix:
                        new_data[CONF_FOXESS_ENTITY_PREFIX] = entity_prefix
                    else:
                        new_data.pop(CONF_FOXESS_ENTITY_PREFIX, None)
                else:
                    new_data[CONF_FOXESS_CLOUD_API_KEY] = cloud_api_key
                    new_data[CONF_FOXESS_CLOUD_DEVICE_SN] = cloud_device_sn
                new_data[CONF_FOXESS_SLAVE_ID] = user_input.get(
                    CONF_FOXESS_SLAVE_ID, DEFAULT_FOXESS_SLAVE_ID
                )

                self.hass.config_entries.async_update_entry(
                    self.config_entry, data=new_data
                )
                self._schedule_entry_reload()
                return self.async_create_entry(
                    title="", data=dict(self.config_entry.options)
                )

        current_conn_type = self._get_option(
            CONF_FOXESS_CONNECTION_TYPE, FOXESS_CONNECTION_TCP
        )
        current_host = self._get_option(CONF_FOXESS_HOST, "")
        current_port = self._get_option(CONF_FOXESS_PORT, DEFAULT_FOXESS_PORT)
        current_slave_id = self._get_option(
            CONF_FOXESS_SLAVE_ID, DEFAULT_FOXESS_SLAVE_ID
        )
        current_serial_port = self._get_option(CONF_FOXESS_SERIAL_PORT, "")
        current_baudrate = self._get_option(
            CONF_FOXESS_SERIAL_BAUDRATE, DEFAULT_FOXESS_SERIAL_BAUDRATE
        )
        current_cloud_api_key = self._get_option(CONF_FOXESS_CLOUD_API_KEY, "")
        current_cloud_device_sn = self._get_option(CONF_FOXESS_CLOUD_DEVICE_SN, "")
        current_entity_entry_id = self._get_option(CONF_FOXESS_ENTITY_CONFIG_ENTRY_ID, "")
        current_entity_prefix = self._get_option(CONF_FOXESS_ENTITY_PREFIX, "")

        foxess_conn_types = {
            FOXESS_CONNECTION_TCP: "Modbus TCP",
            FOXESS_CONNECTION_SERIAL: "RS485 Serial",
            FOXESS_CONNECTION_CLOUD: "FoxESS Cloud API",
            FOXESS_CONNECTION_ENTITY: "Entity bridge (foxess_modbus)",
        }
        schema_fields: dict[Any, Any] = {
            vol.Required(
                CONF_FOXESS_CONNECTION_TYPE,
                default=current_conn_type,
            ): SelectSelector(SelectSelectorConfig(
                options=[
                    SelectOptionDict(value=k, label=v)
                    for k, v in foxess_conn_types.items()
                ],
                mode=SelectSelectorMode.DROPDOWN,
            )),
            vol.Optional(
                CONF_FOXESS_HOST,
                default=current_host,
            ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
            vol.Optional(
                CONF_FOXESS_PORT,
                default=current_port,
            ): NumberSelector(NumberSelectorConfig(
                min=1, max=65535, step=1, mode=NumberSelectorMode.BOX,
            )),
            vol.Optional(
                CONF_FOXESS_SLAVE_ID,
                default=current_slave_id,
            ): NumberSelector(NumberSelectorConfig(
                min=1, max=247, step=1, mode=NumberSelectorMode.BOX,
            )),
            vol.Optional(
                CONF_FOXESS_SERIAL_PORT,
                default=current_serial_port,
            ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
            vol.Optional(
                CONF_FOXESS_SERIAL_BAUDRATE,
                default=current_baudrate,
            ): NumberSelector(NumberSelectorConfig(
                min=300, max=115200, step=1, mode=NumberSelectorMode.BOX,
            )),
            vol.Optional(
                CONF_FOXESS_CLOUD_API_KEY,
                default=current_cloud_api_key,
            ): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
            vol.Optional(
                CONF_FOXESS_CLOUD_DEVICE_SN,
                default=current_cloud_device_sn,
            ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
        }
        entity_entry_options = _foxess_modbus_entry_options(self.hass)
        if entity_entry_options:
            schema_fields[
                vol.Optional(
                    CONF_FOXESS_ENTITY_CONFIG_ENTRY_ID,
                    default=current_entity_entry_id or entity_entry_options[0]["value"],
                )
            ] = SelectSelector(
                SelectSelectorConfig(
                    options=entity_entry_options,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
        schema_fields[
            vol.Optional(CONF_FOXESS_ENTITY_PREFIX, default=current_entity_prefix)
        ] = TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT))

        return self.async_show_form(
            step_id="foxess_connection_options",
            data_schema=vol.Schema(schema_fields),
            errors=errors,
        )

    async def async_step_goodwe_connection_options(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Menu handler: GoodWe connection settings."""
        errors: dict[str, str] = {}

        if user_input is not None:
            goodwe_host = user_input.get(CONF_GOODWE_HOST, "").strip()
            if not goodwe_host:
                errors["base"] = "goodwe_connect_failed"
            else:
                ems_prefix = user_input.get(CONF_GOODWE_EMS_ENTITY_PREFIX, "").strip()
                protocol = user_input.get(CONF_GOODWE_PROTOCOL, "udp")
                ems_control_mode = resolve_goodwe_ems_control_mode_for_protocol(
                    self.hass,
                    user_input.get(CONF_GOODWE_EMS_CONTROL_MODE),
                    ems_prefix,
                    protocol,
                )
                resolved_ems_prefix = (
                    resolve_goodwe_ems_entity_prefix(self.hass, ems_prefix)
                    if ems_control_mode == GOODWE_EMS_CONTROL_ENTITY
                    else ems_prefix
                )
                ems_error = validate_goodwe_ems_control_mode(
                    self.hass,
                    ems_control_mode,
                    resolved_ems_prefix,
                )
                if ems_error:
                    errors["base"] = ems_error
                else:
                    new_data = dict(self.config_entry.data)
                    new_options = dict(self.config_entry.options)
                    port = resolve_goodwe_port(
                        protocol, user_input.get(CONF_GOODWE_PORT)
                    )
                    goodwe_values = {
                        CONF_GOODWE_HOST: goodwe_host,
                        CONF_GOODWE_PORT: port,
                        CONF_GOODWE_PROTOCOL: protocol,
                        CONF_GOODWE_EMS_CONTROL_MODE: ems_control_mode,
                    }
                    new_data.update(goodwe_values)
                    new_options.update(goodwe_values)
                    if ems_control_mode == GOODWE_EMS_CONTROL_ENTITY:
                        new_data[CONF_GOODWE_EMS_ENTITY_PREFIX] = resolved_ems_prefix
                        new_options[CONF_GOODWE_EMS_ENTITY_PREFIX] = resolved_ems_prefix
                    else:
                        new_data.pop(CONF_GOODWE_EMS_ENTITY_PREFIX, None)
                        new_options.pop(CONF_GOODWE_EMS_ENTITY_PREFIX, None)

                    self.hass.config_entries.async_update_entry(
                        self.config_entry, data=new_data
                    )
                    self._schedule_entry_reload()
                    return self.async_create_entry(
                        title="", data=new_options
                    )

        current_host = self._get_option(CONF_GOODWE_HOST, "")
        current_protocol = self._get_option(CONF_GOODWE_PROTOCOL, "udp")
        current_port = resolve_goodwe_port(
            current_protocol,
            self._get_option(CONF_GOODWE_PORT, DEFAULT_GOODWE_PORT_UDP),
        )
        current_ems_prefix = self._get_option(CONF_GOODWE_EMS_ENTITY_PREFIX, "")
        current_ems_control_mode = resolve_goodwe_ems_control_mode(
            self._get_option(CONF_GOODWE_EMS_CONTROL_MODE, None),
            current_ems_prefix,
        )

        goodwe_protocols = {
            "udp": "UDP direct control (port 8899)",
            "tcp": "TCP / LAN Kit-20 (port 502)",
        }

        return self.async_show_form(
            step_id="goodwe_connection_options",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_GOODWE_HOST,
                        default=current_host,
                    ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
                    vol.Required(
                        CONF_GOODWE_PROTOCOL,
                        default=current_protocol,
                    ): SelectSelector(SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=k, label=v)
                            for k, v in goodwe_protocols.items()
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )),
                    vol.Optional(
                        CONF_GOODWE_PORT,
                        default=current_port,
                    ): NumberSelector(NumberSelectorConfig(
                        min=1, max=65535, step=1, mode=NumberSelectorMode.BOX,
                    )),
                    vol.Required(
                        CONF_GOODWE_EMS_CONTROL_MODE,
                        default=current_ems_control_mode,
                    ): SelectSelector(SelectSelectorConfig(
                        options=goodwe_ems_control_options(),
                        mode=SelectSelectorMode.DROPDOWN,
                    )),
                    vol.Optional(
                        CONF_GOODWE_EMS_ENTITY_PREFIX,
                        default=current_ems_prefix or "goodwe",
                        description={
                            "suggested_value": current_ems_prefix or "goodwe"
                        },
                    ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
                }
            ),
            errors=errors,
        )

    async def async_step_esy_sunhome_connection(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Menu handler: re-select the upstream esy_sunhome integration entry."""
        esy_entries = self.hass.config_entries.async_entries("esy_sunhome")
        if not esy_entries:
            return self.async_abort(reason="esy_sunhome_not_installed")

        errors: dict[str, str] = {}

        if user_input is not None:
            selected_entry_id = user_input.get(CONF_ESY_CONFIG_ENTRY_ID, "")
            esy_entry = self.hass.config_entries.async_get_entry(selected_entry_id)
            if not esy_entry or not esy_entry.data.get("device_id"):
                errors["base"] = "esy_sunhome_no_device"
            else:
                new_data = dict(self.config_entry.data)
                new_data[CONF_ESY_CONFIG_ENTRY_ID] = selected_entry_id
                self.hass.config_entries.async_update_entry(
                    self.config_entry, data=new_data
                )
                self._schedule_entry_reload()
                return self.async_create_entry(
                    title="", data=dict(self.config_entry.options)
                )

        current_entry_id = self._get_option(
            CONF_ESY_CONFIG_ENTRY_ID,
            self.config_entry.data.get(CONF_ESY_CONFIG_ENTRY_ID, ""),
        )
        entry_options = {e.entry_id: e.title or e.entry_id for e in esy_entries}

        return self.async_show_form(
            step_id="esy_sunhome_connection",
            data_schema=vol.Schema({
                vol.Required(
                    CONF_ESY_CONFIG_ENTRY_ID,
                    default=current_entry_id,
                ): SelectSelector(SelectSelectorConfig(
                    options=[
                        SelectOptionDict(value=k, label=v)
                        for k, v in entry_options.items()
                    ],
                    mode=SelectSelectorMode.DROPDOWN,
                )),
            }),
            errors=errors,
        )

    async def async_step_solax_battery_options(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Menu handler: Solax connection settings."""
        from ..inverters.solax_battery import SolaxBatteryController

        solax_entries = self.hass.config_entries.async_entries("solax_modbus")
        if not solax_entries:
            return self.async_abort(reason="solax_not_installed")

        errors: dict[str, str] = {}
        description_placeholders: dict[str, str] = {}

        if user_input is not None:
            if len(solax_entries) == 1:
                selected_entry_id = solax_entries[0].entry_id
            else:
                selected_entry_id = user_input.get(CONF_SOLAX_CONFIG_ENTRY_ID, "")
            entity_prefix = (user_input.get(CONF_SOLAX_ENTITY_PREFIX) or "").strip()
            try:
                ctrl = SolaxBatteryController(
                    self.hass,
                    entity_prefix=entity_prefix,
                    solax_entry_id=selected_entry_id,
                    battery_nominal_v=float(user_input.get(CONF_SOLAX_BATTERY_NOMINAL_V, DEFAULT_SOLAX_BATTERY_NOMINAL_V)),
                    max_charge_current_a=float(user_input.get(CONF_SOLAX_MAX_CHARGE_CURRENT_A, DEFAULT_SOLAX_MAX_CHARGE_CURRENT_A)),
                    max_discharge_current_a=float(user_input.get(CONF_SOLAX_MAX_DISCHARGE_CURRENT_A, DEFAULT_SOLAX_MAX_DISCHARGE_CURRENT_A)),
                )
                await ctrl.connect()
                new_data = dict(self.config_entry.data)
                new_data[CONF_SOLAX_CONFIG_ENTRY_ID] = selected_entry_id
                if entity_prefix:
                    new_data[CONF_SOLAX_ENTITY_PREFIX] = entity_prefix
                elif CONF_SOLAX_ENTITY_PREFIX in new_data:
                    del new_data[CONF_SOLAX_ENTITY_PREFIX]
                new_data[CONF_SOLAX_BATTERY_CAPACITY_KWH] = float(user_input.get(CONF_SOLAX_BATTERY_CAPACITY_KWH, DEFAULT_SOLAX_BATTERY_CAPACITY_KWH))
                new_data[CONF_SOLAX_BATTERY_NOMINAL_V] = float(user_input.get(CONF_SOLAX_BATTERY_NOMINAL_V, DEFAULT_SOLAX_BATTERY_NOMINAL_V))
                new_data[CONF_SOLAX_MAX_CHARGE_CURRENT_A] = float(user_input.get(CONF_SOLAX_MAX_CHARGE_CURRENT_A, DEFAULT_SOLAX_MAX_CHARGE_CURRENT_A))
                new_data[CONF_SOLAX_MAX_DISCHARGE_CURRENT_A] = float(user_input.get(CONF_SOLAX_MAX_DISCHARGE_CURRENT_A, DEFAULT_SOLAX_MAX_DISCHARGE_CURRENT_A))
                self.hass.config_entries.async_update_entry(self.config_entry, data=new_data)
                self._schedule_entry_reload()
                return self.async_create_entry(title="", data=dict(self.config_entry.options))
            except ValueError as exc:
                msg = str(exc)
                if "solax_missing_entities:" in msg:
                    missing_list = msg.split(":", 1)[1]
                    _LOGGER.warning("Solax options: missing entities: %s", missing_list)
                    errors["base"] = "solax_missing_entities"
                    first_missing = missing_list.split(",")[0].strip()
                    description_placeholders["first_missing"] = first_missing
                else:
                    errors["base"] = "solax_connect_failed"
            except Exception as exc:
                _LOGGER.error("Solax options error: %s", exc)
                errors["base"] = "solax_connect_failed"

        current_entry_id = self._get_option(
            CONF_SOLAX_CONFIG_ENTRY_ID,
            self.config_entry.data.get(CONF_SOLAX_CONFIG_ENTRY_ID, ""),
        )
        current_capacity = self._get_option(CONF_SOLAX_BATTERY_CAPACITY_KWH, self.config_entry.data.get(CONF_SOLAX_BATTERY_CAPACITY_KWH, DEFAULT_SOLAX_BATTERY_CAPACITY_KWH))
        current_nominal_v = self._get_option(CONF_SOLAX_BATTERY_NOMINAL_V, self.config_entry.data.get(CONF_SOLAX_BATTERY_NOMINAL_V, DEFAULT_SOLAX_BATTERY_NOMINAL_V))
        current_charge_a = self._get_option(CONF_SOLAX_MAX_CHARGE_CURRENT_A, self.config_entry.data.get(CONF_SOLAX_MAX_CHARGE_CURRENT_A, DEFAULT_SOLAX_MAX_CHARGE_CURRENT_A))
        current_discharge_a = self._get_option(CONF_SOLAX_MAX_DISCHARGE_CURRENT_A, self.config_entry.data.get(CONF_SOLAX_MAX_DISCHARGE_CURRENT_A, DEFAULT_SOLAX_MAX_DISCHARGE_CURRENT_A))
        current_entity_prefix = self._get_option(
            CONF_SOLAX_ENTITY_PREFIX,
            self.config_entry.data.get(CONF_SOLAX_ENTITY_PREFIX, ""),
        )

        entry_options = {e.entry_id: e.title or e.entry_id for e in solax_entries}
        if not current_entry_id and entry_options:
            current_entry_id = next(iter(entry_options))

        return self.async_show_form(
            step_id="solax_battery_options",
            data_schema=vol.Schema({
                vol.Required(CONF_SOLAX_CONFIG_ENTRY_ID, default=current_entry_id): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=k, label=v)
                            for k, v in entry_options.items()
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(CONF_SOLAX_BATTERY_CAPACITY_KWH, default=current_capacity): NumberSelector(
                    NumberSelectorConfig(min=1, max=100, step=0.1, mode=NumberSelectorMode.BOX, unit_of_measurement="kWh")
                ),
                vol.Required(CONF_SOLAX_BATTERY_NOMINAL_V, default=current_nominal_v): NumberSelector(
                    NumberSelectorConfig(min=24, max=500, step=0.1, mode=NumberSelectorMode.BOX, unit_of_measurement="V")
                ),
                vol.Required(CONF_SOLAX_MAX_CHARGE_CURRENT_A, default=current_charge_a): NumberSelector(
                    NumberSelectorConfig(min=1, max=200, step=1, mode=NumberSelectorMode.BOX, unit_of_measurement="A")
                ),
                vol.Required(CONF_SOLAX_MAX_DISCHARGE_CURRENT_A, default=current_discharge_a): NumberSelector(
                    NumberSelectorConfig(min=1, max=200, step=1, mode=NumberSelectorMode.BOX, unit_of_measurement="A")
                ),
                vol.Optional(CONF_SOLAX_ENTITY_PREFIX, default=current_entity_prefix): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEXT)
                ),
            }),
            errors=errors,
            description_placeholders=description_placeholders or None,
        )

    async def async_step_saj_h2_connection(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Menu handler: SAJ H2 bridge settings."""
        from ..inverters.saj_h2 import SajH2BatteryController

        saj_entries = self.hass.config_entries.async_entries("saj_h2_modbus")
        if not saj_entries:
            return self.async_abort(reason="saj_h2_not_installed")

        errors: dict[str, str] = {}

        if user_input is not None:
            selected_entry_id = user_input.get(CONF_SAJ_CONFIG_ENTRY_ID, "")
            capacity_kwh = user_input.get(
                CONF_SAJ_BATTERY_CAPACITY_KWH,
                DEFAULT_SAJ_BATTERY_CAPACITY_KWH,
            )
            inverter_rated_kw = user_input.get(
                CONF_SAJ_INVERTER_RATED_KW,
                DEFAULT_SAJ_INVERTER_RATED_KW,
            )
            try:
                ctrl = SajH2BatteryController(
                    self.hass,
                    saj_entry_id=selected_entry_id,
                    battery_capacity_kwh=float(capacity_kwh),
                    inverter_rated_kw=float(inverter_rated_kw),
                )
                await ctrl.connect()
                new_data = dict(self.config_entry.data)
                new_data[CONF_SAJ_CONFIG_ENTRY_ID] = selected_entry_id
                new_data[CONF_SAJ_BATTERY_CAPACITY_KWH] = float(capacity_kwh)
                new_data[CONF_SAJ_INVERTER_RATED_KW] = float(inverter_rated_kw)
                self.hass.config_entries.async_update_entry(self.config_entry, data=new_data)
                self._schedule_entry_reload()
                return self.async_create_entry(title="", data=dict(self.config_entry.options))
            except ValueError as exc:
                if "saj_missing_entities:" in str(exc):
                    errors["base"] = "saj_missing_entities"
                else:
                    errors["base"] = "saj_connect_failed"
            except Exception as exc:
                _LOGGER.error("SAJ H2 options error: %s", exc)
                errors["base"] = "saj_connect_failed"

        entry_options = {e.entry_id: e.title or e.entry_id for e in saj_entries}
        current_entry_id = self._get_option(
            CONF_SAJ_CONFIG_ENTRY_ID,
            self.config_entry.data.get(CONF_SAJ_CONFIG_ENTRY_ID, ""),
        )
        current_capacity = self._get_option(
            CONF_SAJ_BATTERY_CAPACITY_KWH,
            self.config_entry.data.get(
                CONF_SAJ_BATTERY_CAPACITY_KWH,
                DEFAULT_SAJ_BATTERY_CAPACITY_KWH,
            ),
        )
        current_rated_kw = self._get_option(
            CONF_SAJ_INVERTER_RATED_KW,
            self.config_entry.data.get(
                CONF_SAJ_INVERTER_RATED_KW,
                DEFAULT_SAJ_INVERTER_RATED_KW,
            ),
        )

        return self.async_show_form(
            step_id="saj_h2_connection",
            data_schema=vol.Schema({
                vol.Required(
                    CONF_SAJ_CONFIG_ENTRY_ID,
                    default=current_entry_id,
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=k, label=v)
                            for k, v in entry_options.items()
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_SAJ_BATTERY_CAPACITY_KWH,
                    default=current_capacity,
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=1,
                        max=100,
                        step=0.1,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="kWh",
                    )
                ),
                vol.Required(
                    CONF_SAJ_INVERTER_RATED_KW,
                    default=current_rated_kw,
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=1,
                        max=50,
                        step=0.5,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="kW",
                    )
                ),
            }),
            errors=errors,
        )

    async def async_step_fronius_reserva_connection(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Menu handler: Fronius GEN24 storage bridge settings."""
        from ..inverters.fronius_reserva import FroniusReservaBatteryController

        fronius_entries = self.hass.config_entries.async_entries("fronius_modbus")
        if not fronius_entries:
            return self.async_abort(reason="fronius_reserva_not_installed")

        errors: dict[str, str] = {}
        description_placeholders: dict[str, str] = {}

        if user_input is not None:
            selected_entry_id = user_input.get(CONF_FRONIUS_RESERVA_CONFIG_ENTRY_ID, "")
            capacity_kwh = user_input.get(
                CONF_FRONIUS_RESERVA_BATTERY_CAPACITY_KWH,
                DEFAULT_FRONIUS_RESERVA_BATTERY_CAPACITY_KWH,
            )
            max_charge_kw = user_input.get(
                CONF_FRONIUS_RESERVA_MAX_CHARGE_KW,
                DEFAULT_FRONIUS_RESERVA_MAX_CHARGE_KW,
            )
            max_discharge_kw = user_input.get(
                CONF_FRONIUS_RESERVA_MAX_DISCHARGE_KW,
                DEFAULT_FRONIUS_RESERVA_MAX_DISCHARGE_KW,
            )
            try:
                ctrl = FroniusReservaBatteryController(
                    self.hass,
                    fronius_entry_id=selected_entry_id,
                    battery_capacity_kwh=float(capacity_kwh),
                    max_charge_kw=float(max_charge_kw),
                    max_discharge_kw=float(max_discharge_kw),
                )
                await ctrl.connect()
                new_data = dict(self.config_entry.data)
                new_data[CONF_FRONIUS_RESERVA_CONFIG_ENTRY_ID] = selected_entry_id
                new_data[CONF_FRONIUS_RESERVA_BATTERY_CAPACITY_KWH] = float(capacity_kwh)
                new_data[CONF_FRONIUS_RESERVA_MAX_CHARGE_KW] = float(max_charge_kw)
                new_data[CONF_FRONIUS_RESERVA_MAX_DISCHARGE_KW] = float(max_discharge_kw)
                self.hass.config_entries.async_update_entry(self.config_entry, data=new_data)
                self._schedule_entry_reload()
                return self.async_create_entry(title="", data=dict(self.config_entry.options))
            except ValueError as exc:
                msg = str(exc)
                if "fronius_reserva_missing_entities:" in msg:
                    missing_list = msg.split(":", 1)[1]
                    errors["base"] = "fronius_reserva_missing_entities"
                    description_placeholders["first_missing"] = missing_list.split(",")[0].strip()
                else:
                    errors["base"] = "fronius_reserva_connect_failed"
            except Exception as exc:
                _LOGGER.error("Fronius GEN24 storage options error: %s", exc)
                errors["base"] = "fronius_reserva_connect_failed"

        entry_options = {e.entry_id: e.title or e.entry_id for e in fronius_entries}
        current_entry_id = self._get_option(
            CONF_FRONIUS_RESERVA_CONFIG_ENTRY_ID,
            self.config_entry.data.get(CONF_FRONIUS_RESERVA_CONFIG_ENTRY_ID, ""),
        )
        current_capacity = self._get_option(
            CONF_FRONIUS_RESERVA_BATTERY_CAPACITY_KWH,
            self.config_entry.data.get(
                CONF_FRONIUS_RESERVA_BATTERY_CAPACITY_KWH,
                DEFAULT_FRONIUS_RESERVA_BATTERY_CAPACITY_KWH,
            ),
        )
        current_max_charge = self._get_option(
            CONF_FRONIUS_RESERVA_MAX_CHARGE_KW,
            self.config_entry.data.get(
                CONF_FRONIUS_RESERVA_MAX_CHARGE_KW,
                DEFAULT_FRONIUS_RESERVA_MAX_CHARGE_KW,
            ),
        )
        current_max_discharge = self._get_option(
            CONF_FRONIUS_RESERVA_MAX_DISCHARGE_KW,
            self.config_entry.data.get(
                CONF_FRONIUS_RESERVA_MAX_DISCHARGE_KW,
                DEFAULT_FRONIUS_RESERVA_MAX_DISCHARGE_KW,
            ),
        )

        return self.async_show_form(
            step_id="fronius_reserva_connection",
            data_schema=vol.Schema({
                vol.Required(
                    CONF_FRONIUS_RESERVA_CONFIG_ENTRY_ID,
                    default=current_entry_id,
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=k, label=v)
                            for k, v in entry_options.items()
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_FRONIUS_RESERVA_BATTERY_CAPACITY_KWH,
                    default=current_capacity,
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=1,
                        max=100,
                        step=0.1,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="kWh",
                    )
                ),
                vol.Required(
                    CONF_FRONIUS_RESERVA_MAX_CHARGE_KW,
                    default=current_max_charge,
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0.1,
                        max=50,
                        step=0.1,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="kW",
                    )
                ),
                vol.Required(
                    CONF_FRONIUS_RESERVA_MAX_DISCHARGE_KW,
                    default=current_max_discharge,
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0.1,
                        max=50,
                        step=0.1,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="kW",
                    )
                ),
            }),
            errors=errors,
            description_placeholders=description_placeholders or None,
        )

    async def async_step_neovolt_connection(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Menu handler: Neovolt bridge settings."""
        from ..inverters.neovolt import NeovoltFleetBatteryController

        neovolt_entries = self.hass.config_entries.async_entries("neovolt")
        if not neovolt_entries:
            return self.async_abort(reason="neovolt_not_installed")

        errors: dict[str, str] = {}

        if user_input is not None:
            if len(neovolt_entries) == 1:
                selected_entry_ids = [neovolt_entries[0].entry_id]
            else:
                selected_entry_ids = _normalize_neovolt_entry_ids(
                    user_input.get(CONF_NEOVOLT_CONFIG_ENTRY_IDS),
                    user_input.get(CONF_NEOVOLT_CONFIG_ENTRY_ID),
                )
            max_charge_kw = user_input.get(
                CONF_NEOVOLT_MAX_CHARGE_KW,
                DEFAULT_NEOVOLT_MAX_CHARGE_KW,
            )
            max_discharge_kw = user_input.get(
                CONF_NEOVOLT_MAX_DISCHARGE_KW,
                DEFAULT_NEOVOLT_MAX_DISCHARGE_KW,
            )
            surplus_balancer_mode = user_input.get(
                CONF_NEOVOLT_SURPLUS_BALANCER_MODE,
                DEFAULT_NEOVOLT_SURPLUS_BALANCER_MODE,
            )
            soc_balance_tolerance = user_input.get(
                CONF_NEOVOLT_SOC_BALANCE_TOLERANCE,
                DEFAULT_NEOVOLT_SOC_BALANCE_TOLERANCE,
            )
            try:
                battery_capacities_text = _normalize_neovolt_capacities_text(
                    user_input.get(CONF_NEOVOLT_BATTERY_CAPACITIES_KWH)
                )
                battery_capacities_kwh = _parse_neovolt_capacities_kwh(
                    battery_capacities_text,
                    len(selected_entry_ids),
                )
                ctrl = NeovoltFleetBatteryController(
                    self.hass,
                    neovolt_entry_ids=selected_entry_ids,
                    max_charge_kw=float(max_charge_kw),
                    max_discharge_kw=float(max_discharge_kw),
                    surplus_balancer_mode=str(surplus_balancer_mode),
                    soc_balance_tolerance_pct=float(soc_balance_tolerance),
                    battery_capacities_kwh=battery_capacities_kwh,
                )
                await ctrl.connect()
                new_data = dict(self.config_entry.data)
                new_data[CONF_NEOVOLT_CONFIG_ENTRY_ID] = selected_entry_ids[0]
                new_data[CONF_NEOVOLT_CONFIG_ENTRY_IDS] = selected_entry_ids
                new_data[CONF_NEOVOLT_MAX_CHARGE_KW] = float(max_charge_kw)
                new_data[CONF_NEOVOLT_MAX_DISCHARGE_KW] = float(max_discharge_kw)
                new_data[CONF_NEOVOLT_BATTERY_CAPACITIES_KWH] = battery_capacities_kwh
                if battery_capacities_text:
                    new_data[CONF_NEOVOLT_BATTERY_CAPACITIES_KWH_RAW] = (
                        battery_capacities_text
                    )
                else:
                    new_data.pop(CONF_NEOVOLT_BATTERY_CAPACITIES_KWH_RAW, None)
                new_data[CONF_NEOVOLT_SURPLUS_BALANCER_MODE] = str(surplus_balancer_mode)
                new_data[CONF_NEOVOLT_SOC_BALANCE_TOLERANCE] = float(soc_balance_tolerance)
                self.hass.config_entries.async_update_entry(self.config_entry, data=new_data)
                new_options = dict(self.config_entry.options)
                new_options[CONF_NEOVOLT_CONFIG_ENTRY_ID] = selected_entry_ids[0]
                new_options[CONF_NEOVOLT_CONFIG_ENTRY_IDS] = selected_entry_ids
                new_options[CONF_NEOVOLT_MAX_CHARGE_KW] = float(max_charge_kw)
                new_options[CONF_NEOVOLT_MAX_DISCHARGE_KW] = float(max_discharge_kw)
                new_options[CONF_NEOVOLT_BATTERY_CAPACITIES_KWH] = battery_capacities_kwh
                if battery_capacities_text:
                    new_options[CONF_NEOVOLT_BATTERY_CAPACITIES_KWH_RAW] = (
                        battery_capacities_text
                    )
                else:
                    new_options.pop(CONF_NEOVOLT_BATTERY_CAPACITIES_KWH_RAW, None)
                new_options[CONF_NEOVOLT_SURPLUS_BALANCER_MODE] = str(
                    surplus_balancer_mode
                )
                new_options[CONF_NEOVOLT_SOC_BALANCE_TOLERANCE] = float(
                    soc_balance_tolerance
                )
                self._schedule_entry_reload()
                return self.async_create_entry(title="", data=new_options)
            except ValueError as exc:
                if "capacity_" in str(exc):
                    errors["base"] = "neovolt_capacity_invalid"
                elif "neovolt_missing_entities:" in str(exc):
                    errors["base"] = "neovolt_missing_entities"
                else:
                    errors["base"] = "neovolt_connect_failed"
            except Exception as exc:
                _LOGGER.error("Neovolt options error: %s", exc)
                errors["base"] = "neovolt_connect_failed"

        entry_options = {e.entry_id: e.title or e.entry_id for e in neovolt_entries}
        current_entry_ids = _normalize_neovolt_entry_ids(
            self._get_option(
                CONF_NEOVOLT_CONFIG_ENTRY_IDS,
                self.config_entry.data.get(CONF_NEOVOLT_CONFIG_ENTRY_IDS),
            ),
            self._get_option(
                CONF_NEOVOLT_CONFIG_ENTRY_ID,
                self.config_entry.data.get(CONF_NEOVOLT_CONFIG_ENTRY_ID, ""),
            ),
        )
        if not current_entry_ids and entry_options:
            current_entry_ids = [next(iter(entry_options))]
        current_max_charge_kw = self._get_option(
            CONF_NEOVOLT_MAX_CHARGE_KW,
            self.config_entry.data.get(
                CONF_NEOVOLT_MAX_CHARGE_KW,
                DEFAULT_NEOVOLT_MAX_CHARGE_KW,
            ),
        )
        current_max_discharge_kw = self._get_option(
            CONF_NEOVOLT_MAX_DISCHARGE_KW,
            self.config_entry.data.get(
                CONF_NEOVOLT_MAX_DISCHARGE_KW,
                DEFAULT_NEOVOLT_MAX_DISCHARGE_KW,
            ),
        )
        current_surplus_balancer_mode = self._get_option(
            CONF_NEOVOLT_SURPLUS_BALANCER_MODE,
            self.config_entry.data.get(
                CONF_NEOVOLT_SURPLUS_BALANCER_MODE,
                DEFAULT_NEOVOLT_SURPLUS_BALANCER_MODE,
            ),
        )
        current_soc_balance_tolerance = self._get_option(
            CONF_NEOVOLT_SOC_BALANCE_TOLERANCE,
            self.config_entry.data.get(
                CONF_NEOVOLT_SOC_BALANCE_TOLERANCE,
                DEFAULT_NEOVOLT_SOC_BALANCE_TOLERANCE,
            ),
        )
        current_battery_capacities = _format_neovolt_capacities_kwh(
            self._get_option(
                CONF_NEOVOLT_BATTERY_CAPACITIES_KWH_RAW,
                self.config_entry.data.get(
                    CONF_NEOVOLT_BATTERY_CAPACITIES_KWH_RAW,
                    self.config_entry.data.get(CONF_NEOVOLT_BATTERY_CAPACITIES_KWH, []),
                ),
            )
        )

        return self.async_show_form(
            step_id="neovolt_connection",
            data_schema=vol.Schema({
                vol.Required(
                    CONF_NEOVOLT_CONFIG_ENTRY_IDS,
                    default=current_entry_ids,
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=k, label=v)
                            for k, v in entry_options.items()
                        ],
                        multiple=True,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_NEOVOLT_MAX_CHARGE_KW,
                    default=current_max_charge_kw,
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0.5,
                        max=50,
                        step=0.1,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="kW",
                    )
                ),
                vol.Required(
                    CONF_NEOVOLT_MAX_DISCHARGE_KW,
                    default=current_max_discharge_kw,
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0.5,
                        max=50,
                        step=0.1,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="kW",
                    )
                ),
                vol.Optional(
                    CONF_NEOVOLT_BATTERY_CAPACITIES_KWH,
                    default=current_battery_capacities,
                ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
                vol.Required(
                    CONF_NEOVOLT_SURPLUS_BALANCER_MODE,
                    default=current_surplus_balancer_mode,
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=mode, label=mode.title())
                            for mode in NEOVOLT_SURPLUS_BALANCER_MODES
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_NEOVOLT_SOC_BALANCE_TOLERANCE,
                    default=current_soc_balance_tolerance,
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=1,
                        max=30,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="%",
                    )
                ),
            }),
            errors=errors,
        )

    async def async_step_solaredge_connection(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Menu handler: SolarEdge Modbus curtailment settings."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = (user_input.get(CONF_SOLAREDGE_HOST) or "").strip()
            port = int(user_input.get(CONF_SOLAREDGE_PORT, DEFAULT_SOLAREDGE_PORT))
            slave_id = int(
                user_input.get(CONF_SOLAREDGE_SLAVE_ID, DEFAULT_SOLAREDGE_SLAVE_ID)
            )
            rated_power_w = float(
                user_input.get(
                    CONF_SOLAREDGE_RATED_POWER_W, DEFAULT_SOLAREDGE_RATED_POWER_W
                )
            )
            entity_prefix = (
                user_input.get(CONF_SOLAREDGE_ENTITY_PREFIX) or ""
            ).strip()

            if rated_power_w <= 0:
                errors["base"] = "solaredge_rated_power_required"
            elif not host and not entity_prefix:
                errors["base"] = "solaredge_host_required"
            else:
                from ..inverters.solaredge import SolarEdgeController

                controller = SolarEdgeController(
                    host=host,
                    port=port,
                    slave_id=slave_id,
                    rated_power_w=rated_power_w,
                    entity_prefix=entity_prefix,
                    hass=self.hass,
                )
                try:
                    if not await controller.connect():
                        errors["base"] = "solaredge_connect_failed"
                finally:
                    try:
                        await controller.disconnect()
                    except Exception:
                        pass

                if not errors:
                    updates = {
                        CONF_SOLAREDGE_HOST: host,
                        CONF_SOLAREDGE_PORT: port,
                        CONF_SOLAREDGE_SLAVE_ID: slave_id,
                        CONF_SOLAREDGE_RATED_POWER_W: rated_power_w,
                        CONF_SOLAREDGE_ENTITY_PREFIX: entity_prefix,
                        CONF_SOLAREDGE_DC_CURTAILMENT_ENABLED: user_input.get(
                            CONF_SOLAREDGE_DC_CURTAILMENT_ENABLED, False
                        ),
                    }
                    new_data = {**self.config_entry.data, **updates}
                    self.hass.config_entries.async_update_entry(
                        self.config_entry, data=new_data
                    )
                    new_options = {**self.config_entry.options, **updates}
                    self._schedule_entry_reload()
                    return self.async_create_entry(title="", data=new_options)

        return self.async_show_form(
            step_id="solaredge_connection",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_SOLAREDGE_HOST,
                        default=self._get_option(CONF_SOLAREDGE_HOST, ""),
                    ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
                    vol.Optional(
                        CONF_SOLAREDGE_PORT,
                        default=self._get_option(
                            CONF_SOLAREDGE_PORT, DEFAULT_SOLAREDGE_PORT
                        ),
                    ): NumberSelector(NumberSelectorConfig(
                        min=1, max=65535, step=1, mode=NumberSelectorMode.BOX,
                    )),
                    vol.Optional(
                        CONF_SOLAREDGE_SLAVE_ID,
                        default=self._get_option(
                            CONF_SOLAREDGE_SLAVE_ID, DEFAULT_SOLAREDGE_SLAVE_ID
                        ),
                    ): NumberSelector(NumberSelectorConfig(
                        min=1, max=247, step=1, mode=NumberSelectorMode.BOX,
                    )),
                    vol.Required(
                        CONF_SOLAREDGE_RATED_POWER_W,
                        default=self._get_option(
                            CONF_SOLAREDGE_RATED_POWER_W,
                            DEFAULT_SOLAREDGE_RATED_POWER_W,
                        ),
                    ): NumberSelector(NumberSelectorConfig(
                        min=1, max=100000, step=1, unit_of_measurement="W",
                        mode=NumberSelectorMode.BOX,
                    )),
                    vol.Optional(
                        CONF_SOLAREDGE_ENTITY_PREFIX,
                        default=self._get_option(CONF_SOLAREDGE_ENTITY_PREFIX, ""),
                    ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
                    vol.Optional(
                        CONF_SOLAREDGE_DC_CURTAILMENT_ENABLED,
                        default=self._get_option(
                            CONF_SOLAREDGE_DC_CURTAILMENT_ENABLED, False
                        ),
                    ): BooleanSelector(),
                }
            ),
            errors=errors,
        )

    async def async_step_optimization(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Menu handler: optimization provider and backup reserve settings."""
        battery_system = self._effective_battery_system()
        is_tesla = battery_system == BATTERY_SYSTEM_TESLA
        is_custom = battery_system == BATTERY_SYSTEM_CUSTOM
        current_provider = self._get_option(
            CONF_ELECTRICITY_PROVIDER,
            self.config_entry.data.get(CONF_ELECTRICITY_PROVIDER, "amber"),
        )
        supports_no_idle_mode = supports_no_idle_mode_provider(current_provider)
        if user_input is not None:
            optimization_provider = user_input.get(
                CONF_OPTIMIZATION_PROVIDER, OPT_PROVIDER_NATIVE
            )
            if is_custom:
                optimization_provider = OPT_PROVIDER_POWERSYNC
            optimization_enabled = bool(
                user_input.get(
                    CONF_OPTIMIZATION_ENABLED,
                    optimization_provider == OPT_PROVIDER_POWERSYNC,
                )
            )
            auto_apply_reserve_enabled = bool(
                user_input.get(CONF_OPTIMIZATION_AUTO_APPLY_RESERVE, False)
            )
            previous_auto_apply_reserve_enabled = bool(
                self._get_option(
                    CONF_OPTIMIZATION_AUTO_APPLY_RESERVE,
                    self.config_entry.data.get(
                        CONF_OPTIMIZATION_AUTO_APPLY_RESERVE, False
                    ),
                )
            )
            if optimization_provider != OPT_PROVIDER_POWERSYNC:
                optimization_enabled = False
                auto_apply_reserve_enabled = False
            if is_custom:
                optimization_enabled = True
            new_data = dict(self.config_entry.data)
            new_options = dict(self.config_entry.options)
            new_data[CONF_OPTIMIZATION_PROVIDER] = optimization_provider
            new_options[CONF_OPTIMIZATION_ENABLED] = optimization_enabled
            new_data[CONF_OPTIMIZATION_AUTO_APPLY_RESERVE] = auto_apply_reserve_enabled
            new_options[CONF_OPTIMIZATION_AUTO_APPLY_RESERVE] = auto_apply_reserve_enabled
            monitoring_mode = bool(user_input.get(CONF_MONITORING_MODE, False))
            if is_custom:
                monitoring_mode = True
            new_data[CONF_MONITORING_MODE] = monitoring_mode
            new_options[CONF_MONITORING_MODE] = monitoring_mode
            if optimization_provider != OPT_PROVIDER_POWERSYNC:
                new_data[CONF_OPTIMIZATION_DISABLE_IDLE] = False
                new_options[CONF_OPTIMIZATION_DISABLE_IDLE] = False
            if battery_system == BATTERY_SYSTEM_NEOVOLT:
                surplus_balancer_mode = user_input.get(
                    CONF_NEOVOLT_SURPLUS_BALANCER_MODE,
                    self._get_option(
                        CONF_NEOVOLT_SURPLUS_BALANCER_MODE,
                        self.config_entry.data.get(
                            CONF_NEOVOLT_SURPLUS_BALANCER_MODE,
                            DEFAULT_NEOVOLT_SURPLUS_BALANCER_MODE,
                        ),
                    ),
                )
                new_data[CONF_NEOVOLT_SURPLUS_BALANCER_MODE] = str(surplus_balancer_mode)
                new_options[CONF_NEOVOLT_SURPLUS_BALANCER_MODE] = str(
                    surplus_balancer_mode
                )
            if optimization_provider == OPT_PROVIDER_POWERSYNC:
                default_capacity_wh, default_charge_w, default_discharge_w = (
                    _default_optimizer_specs_for(
                        battery_system
                    )
                )
                default_capacity_kwh = default_capacity_wh / 1000
                default_charge_kw = default_charge_w / 1000
                default_discharge_kw = default_discharge_w / 1000
                backup_reserve = (
                    user_input.get(
                        CONF_OPTIMIZATION_BACKUP_RESERVE,
                        int(DEFAULT_OPTIMIZATION_BACKUP_RESERVE * 100),
                    )
                    / 100.0
                )
                current_manual_reserve = self._get_option(
                    CONF_OPTIMIZATION_MANUAL_RESERVE,
                    self.config_entry.data.get(CONF_OPTIMIZATION_MANUAL_RESERVE),
                )
                if current_manual_reserve is None:
                    current_manual_reserve = backup_reserve
                elif current_manual_reserve > 1:
                    current_manual_reserve = current_manual_reserve / 100.0
                if (
                    not auto_apply_reserve_enabled
                    and previous_auto_apply_reserve_enabled
                ):
                    backup_reserve = current_manual_reserve
                manual_reserve = (
                    backup_reserve
                    if auto_apply_reserve_enabled
                    else current_manual_reserve
                )
                hardware_backup_reserve = (
                    user_input.get(
                        CONF_HARDWARE_BACKUP_RESERVE,
                        int(DEFAULT_OPTIMIZATION_BACKUP_RESERVE * 100),
                    )
                    / 100.0
                )
                capacity_wh = _form_kwh_to_wh(
                    user_input.get(CONF_OPTIMIZATION_BATTERY_CAPACITY_WH),
                    default_capacity_kwh,
                )
                charge_w = _form_kw_to_w(
                    user_input.get(CONF_OPTIMIZATION_MAX_CHARGE_W),
                    default_charge_kw,
                )
                discharge_w = _form_kw_to_w(
                    user_input.get(CONF_OPTIMIZATION_MAX_DISCHARGE_W),
                    default_discharge_kw,
                )
                max_grid_export_w = _form_optional_kw_to_w(
                    user_input.get(CONF_OPTIMIZATION_MAX_GRID_EXPORT_W)
                )
                max_grid_import_w = _form_kw_to_w(
                    user_input.get(CONF_OPTIMIZATION_MAX_GRID_IMPORT_W),
                    0,
                )
                max_grid_charge_price = _form_optional_cents_to_price(
                    user_input.get(CONF_OPTIMIZATION_MAX_GRID_CHARGE_PRICE)
                )
                grid_charge_soc_cap = _form_percent_to_ratio(
                    user_input.get(CONF_OPTIMIZATION_GRID_CHARGE_SOC_CAP),
                    1.0,
                )
                allow_grid_charge = user_input.get(
                    CONF_OPTIMIZATION_ALLOW_GRID_CHARGE,
                    True,
                )
                profit_max_enabled = bool(
                    user_input.get(CONF_PROFIT_MAX_ENABLED, False)
                )
                charge_by_time_enabled = bool(
                    user_input.get(CONF_CHARGE_BY_TIME_ENABLED, False)
                )
                charge_by_time_target_time = user_input.get(
                    CONF_CHARGE_BY_TIME_TARGET_TIME,
                    DEFAULT_CHARGE_BY_TIME_TARGET_TIME,
                )
                charge_by_time_target_soc = _form_percent_to_ratio(
                    user_input.get(CONF_CHARGE_BY_TIME_TARGET_SOC),
                    DEFAULT_CHARGE_BY_TIME_TARGET_SOC,
                )
                disable_idle = (
                    bool(user_input.get(CONF_OPTIMIZATION_DISABLE_IDLE, False))
                    if supports_no_idle_mode
                    else False
                )
                new_data[CONF_OPTIMIZATION_COST_FUNCTION] = COST_FUNCTION_COST
                new_options[CONF_OPTIMIZATION_COST_FUNCTION] = COST_FUNCTION_COST
                new_data[CONF_OPTIMIZATION_BACKUP_RESERVE] = backup_reserve
                new_options[CONF_OPTIMIZATION_BACKUP_RESERVE] = backup_reserve
                new_data[CONF_OPTIMIZATION_MANUAL_RESERVE] = manual_reserve
                new_options[CONF_OPTIMIZATION_MANUAL_RESERVE] = manual_reserve
                new_data[CONF_HARDWARE_BACKUP_RESERVE] = hardware_backup_reserve
                new_options[CONF_HARDWARE_BACKUP_RESERVE] = hardware_backup_reserve
                new_options.pop("_user_backup_reserve", None)
                new_data[CONF_OPTIMIZATION_BATTERY_CAPACITY_WH] = capacity_wh
                new_options[CONF_OPTIMIZATION_BATTERY_CAPACITY_WH] = capacity_wh
                new_data[CONF_OPTIMIZATION_MAX_CHARGE_W] = charge_w
                new_options[CONF_OPTIMIZATION_MAX_CHARGE_W] = charge_w
                new_data[CONF_OPTIMIZATION_MAX_DISCHARGE_W] = discharge_w
                new_options[CONF_OPTIMIZATION_MAX_DISCHARGE_W] = discharge_w
                if max_grid_export_w is None:
                    new_data.pop(CONF_OPTIMIZATION_MAX_GRID_EXPORT_W, None)
                    new_options.pop(CONF_OPTIMIZATION_MAX_GRID_EXPORT_W, None)
                else:
                    new_data[CONF_OPTIMIZATION_MAX_GRID_EXPORT_W] = max_grid_export_w
                    new_options[CONF_OPTIMIZATION_MAX_GRID_EXPORT_W] = max_grid_export_w
                new_data[CONF_OPTIMIZATION_MAX_GRID_IMPORT_W] = max_grid_import_w
                new_options[CONF_OPTIMIZATION_MAX_GRID_IMPORT_W] = max_grid_import_w
                if max_grid_charge_price is None:
                    new_data.pop(CONF_OPTIMIZATION_MAX_GRID_CHARGE_PRICE, None)
                    new_options.pop(CONF_OPTIMIZATION_MAX_GRID_CHARGE_PRICE, None)
                else:
                    new_data[CONF_OPTIMIZATION_MAX_GRID_CHARGE_PRICE] = (
                        max_grid_charge_price
                    )
                    new_options[CONF_OPTIMIZATION_MAX_GRID_CHARGE_PRICE] = (
                        max_grid_charge_price
                    )
                new_data[CONF_OPTIMIZATION_GRID_CHARGE_SOC_CAP] = grid_charge_soc_cap
                new_options[CONF_OPTIMIZATION_GRID_CHARGE_SOC_CAP] = grid_charge_soc_cap
                new_data[CONF_OPTIMIZATION_ALLOW_GRID_CHARGE] = allow_grid_charge
                new_options[CONF_OPTIMIZATION_ALLOW_GRID_CHARGE] = allow_grid_charge
                spread_export_enabled = (
                    False
                    if is_tesla
                    else bool(
                        user_input.get(CONF_OPTIMIZATION_SPREAD_EXPORT_ENABLED, False)
                    )
                )
                spread_import_enabled = (
                    False
                    if is_tesla
                    else bool(
                        user_input.get(CONF_OPTIMIZATION_SPREAD_IMPORT_ENABLED, False)
                    )
                )
                ev_integration_enabled = bool(
                    user_input.get(CONF_OPTIMIZATION_EV_INTEGRATION, False)
                )
                load_entity = _normalize_optional_entity(
                    user_input.get(CONF_OPTIMIZATION_LOAD_ENTITY)
                )
                planned_ev_load_entity = _normalize_optional_entity(
                    user_input.get(CONF_OPTIMIZATION_PLANNED_EV_LOAD_ENTITY)
                )
                new_data[CONF_OPTIMIZATION_SPREAD_EXPORT_ENABLED] = spread_export_enabled
                new_options[CONF_OPTIMIZATION_SPREAD_EXPORT_ENABLED] = spread_export_enabled
                new_data[CONF_OPTIMIZATION_SPREAD_IMPORT_ENABLED] = spread_import_enabled
                new_options[CONF_OPTIMIZATION_SPREAD_IMPORT_ENABLED] = spread_import_enabled
                new_data[CONF_OPTIMIZATION_DISABLE_IDLE] = disable_idle
                new_options[CONF_OPTIMIZATION_DISABLE_IDLE] = disable_idle
                new_data[CONF_OPTIMIZATION_LOAD_ENTITY] = load_entity
                new_options[CONF_OPTIMIZATION_LOAD_ENTITY] = load_entity
                new_data[CONF_OPTIMIZATION_EV_INTEGRATION] = ev_integration_enabled
                new_options[CONF_OPTIMIZATION_EV_INTEGRATION] = ev_integration_enabled
                new_data[CONF_OPTIMIZATION_PLANNED_EV_LOAD_ENTITY] = planned_ev_load_entity
                new_options[CONF_OPTIMIZATION_PLANNED_EV_LOAD_ENTITY] = planned_ev_load_entity
                new_data[CONF_PROFIT_MAX_ENABLED] = profit_max_enabled
                new_options[CONF_PROFIT_MAX_ENABLED] = profit_max_enabled
                new_data[CONF_CHARGE_BY_TIME_ENABLED] = charge_by_time_enabled
                new_options[CONF_CHARGE_BY_TIME_ENABLED] = charge_by_time_enabled
                new_data[CONF_CHARGE_BY_TIME_TARGET_TIME] = charge_by_time_target_time
                new_options[CONF_CHARGE_BY_TIME_TARGET_TIME] = charge_by_time_target_time
                new_data[CONF_CHARGE_BY_TIME_TARGET_SOC] = charge_by_time_target_soc
                new_options[CONF_CHARGE_BY_TIME_TARGET_SOC] = charge_by_time_target_soc
                new_data[CONF_PROFIT_MAX_TARGET_TIME] = charge_by_time_target_time
                new_options[CONF_PROFIT_MAX_TARGET_TIME] = charge_by_time_target_time
                new_data[CONF_PROFIT_MAX_TARGET_SOC] = charge_by_time_target_soc
                new_options[CONF_PROFIT_MAX_TARGET_SOC] = charge_by_time_target_soc

            entry_data = self.hass.data.get(DOMAIN, {}).get(
                self.config_entry.entry_id
            )
            coordinator = (
                entry_data.get("optimization_coordinator")
                if isinstance(entry_data, dict)
                else None
            )

            # Decide — before persisting — whether anything STRUCTURAL changed.
            # Pure optimiser tunables are pushed into the running coordinator in
            # place (the same path the mobile app uses via set_settings), so the
            # change applies in well under a second. Structural changes —
            # provider, enable/disable, auto-apply toggle, monitoring mode, the
            # No Idle toggle, or the Neovolt surplus mode — still rebuild
            # the integration with a full reload.
            def _opt_changed(key: str, default: Any = None) -> bool:
                current = self._get_option(
                    key, self.config_entry.data.get(key, default)
                )
                updated = new_options.get(key, new_data.get(key, default))
                return current != updated

            structural_change = (
                coordinator is None
                or not hasattr(coordinator, "set_settings")
                or _opt_changed(CONF_OPTIMIZATION_PROVIDER, OPT_PROVIDER_NATIVE)
                or _opt_changed(CONF_OPTIMIZATION_ENABLED, False)
                or _opt_changed(CONF_OPTIMIZATION_AUTO_APPLY_RESERVE, False)
                or _opt_changed(CONF_MONITORING_MODE, False)
                or _opt_changed(CONF_OPTIMIZATION_DISABLE_IDLE, False)
                # EV integration must reload: set_settings only flips the
                # load-overlay flag, it does NOT start/stop the EV coordinator
                # that schedules charging — that happens during setup/enable.
                or _opt_changed(CONF_OPTIMIZATION_EV_INTEGRATION, False)
                or _opt_changed(CONF_NEOVOLT_SURPLUS_BALANCER_MODE)
            )

            # Only skip the reload listener when this write actually changes
            # persisted state. HA does not fire the update listener for a
            # no-op write (e.g. resubmitting the options flow unchanged), so
            # an unconditional flag here would be left stuck and later
            # silently swallow the reload for the next genuine structural
            # change.
            persisted_changed = (
                new_data != dict(self.config_entry.data)
                or new_options != dict(self.config_entry.options)
            )
            if isinstance(entry_data, dict) and persisted_changed:
                entry_data["_skip_reload"] = True
            self.hass.config_entries.async_update_entry(
                self.config_entry, data=new_data, options=new_options
            )
            if battery_system == BATTERY_SYSTEM_SIGENERGY and monitoring_mode:
                try:
                    await self.hass.services.async_call(
                        DOMAIN,
                        SERVICE_RESTORE_NORMAL,
                        {"source": "manual", "_force_restore": True},
                        blocking=True,
                    )
                except Exception as err:
                    _LOGGER.warning(
                        "Monitoring mode enabled but Sigenergy restore failed: %s",
                        err,
                    )

            if structural_change:
                self.hass.async_create_task(
                    self.hass.config_entries.async_reload(self.config_entry.entry_id)
                )
            elif optimization_provider == OPT_PROVIDER_POWERSYNC:
                live_settings = {
                    "auto_apply_reserve_enabled": auto_apply_reserve_enabled,
                    "backup_reserve": backup_reserve,
                    "hardware_backup_reserve": hardware_backup_reserve,
                    "battery_capacity_wh": capacity_wh,
                    "max_charge_w": charge_w,
                    "max_discharge_w": discharge_w,
                    "max_grid_export_w": max_grid_export_w,
                    "max_grid_import_w": max_grid_import_w,
                    "max_grid_charge_price": max_grid_charge_price,
                    "grid_charge_soc_cap": grid_charge_soc_cap,
                    "allow_grid_charge": allow_grid_charge,
                    "cost_function": COST_FUNCTION_COST,
                    "profit_max_enabled": profit_max_enabled,
                    "charge_by_time_enabled": charge_by_time_enabled,
                    "charge_by_time_target_time": charge_by_time_target_time,
                    "charge_by_time_target_soc": charge_by_time_target_soc,
                    "spread_export_enabled": spread_export_enabled,
                    "spread_import_enabled": spread_import_enabled,
                    "ev_integration": ev_integration_enabled,
                    "load_entity": load_entity,
                    "planned_ev_load_entity": planned_ev_load_entity,
                }
                try:
                    await coordinator.set_settings(live_settings)
                except Exception as err:  # never leave settings half-applied
                    _LOGGER.warning(
                        "Live optimiser settings apply failed (%s) — reloading entry",
                        err,
                    )
                    self.hass.async_create_task(
                        self.hass.config_entries.async_reload(
                            self.config_entry.entry_id
                        )
                    )

            return self.async_create_entry(
                title="", data=dict(self.config_entry.options)
            )

        current_opt_provider = self.config_entry.data.get(
            CONF_OPTIMIZATION_PROVIDER, OPT_PROVIDER_NATIVE
        )
        current_optimization_enabled = self.config_entry.options.get(
            CONF_OPTIMIZATION_ENABLED,
            current_opt_provider == OPT_PROVIDER_POWERSYNC,
        )
        current_auto_apply_reserve = self._get_option(
            CONF_OPTIMIZATION_AUTO_APPLY_RESERVE,
            self.config_entry.data.get(CONF_OPTIMIZATION_AUTO_APPLY_RESERVE, False),
        )
        current_monitoring_mode = self._get_option(
            CONF_MONITORING_MODE,
            self.config_entry.data.get(CONF_MONITORING_MODE, False),
        )
        current_surplus_balancer_mode = self._get_option(
            CONF_NEOVOLT_SURPLUS_BALANCER_MODE,
            self.config_entry.data.get(
                CONF_NEOVOLT_SURPLUS_BALANCER_MODE,
                DEFAULT_NEOVOLT_SURPLUS_BALANCER_MODE,
            ),
        )
        current_backup_reserve = self._get_option(
            CONF_OPTIMIZATION_BACKUP_RESERVE,
            self.config_entry.data.get(
                CONF_OPTIMIZATION_BACKUP_RESERVE,
                DEFAULT_OPTIMIZATION_BACKUP_RESERVE,
            ),
        )
        current_manual_reserve = self._get_option(
            CONF_OPTIMIZATION_MANUAL_RESERVE,
            self.config_entry.data.get(CONF_OPTIMIZATION_MANUAL_RESERVE),
        )
        if current_manual_reserve is not None and current_manual_reserve > 1:
            current_manual_reserve = current_manual_reserve / 100.0
        display_backup_reserve = (
            current_manual_reserve
            if current_auto_apply_reserve and current_manual_reserve is not None
            else current_backup_reserve
        )
        current_hardware_backup_reserve = self._get_option(
            CONF_HARDWARE_BACKUP_RESERVE,
            current_backup_reserve,
        )
        default_capacity_wh, default_charge_w, default_discharge_w = (
            _default_optimizer_specs_for(battery_system)
        )
        current_capacity_kwh = _stored_wh_to_kwh(
            self._get_option(
                CONF_OPTIMIZATION_BATTERY_CAPACITY_WH,
                self.config_entry.data.get(
                    CONF_OPTIMIZATION_BATTERY_CAPACITY_WH,
                    default_capacity_wh,
                ),
            ),
            default_capacity_wh,
        )
        current_charge_kw = _stored_w_to_kw(
            self._get_option(
                CONF_OPTIMIZATION_MAX_CHARGE_W,
                self.config_entry.data.get(
                    CONF_OPTIMIZATION_MAX_CHARGE_W,
                    default_charge_w,
                ),
            ),
            default_charge_w,
        )
        current_discharge_kw = _stored_w_to_kw(
            self._get_option(
                CONF_OPTIMIZATION_MAX_DISCHARGE_W,
                self.config_entry.data.get(
                    CONF_OPTIMIZATION_MAX_DISCHARGE_W,
                    default_discharge_w,
                ),
            ),
            default_discharge_w,
        )
        current_max_grid_export_kw = _stored_optional_w_to_kw(
            self._get_option(
                CONF_OPTIMIZATION_MAX_GRID_EXPORT_W,
                self.config_entry.data.get(CONF_OPTIMIZATION_MAX_GRID_EXPORT_W),
            )
        )
        current_max_grid_import_kw = _stored_w_to_kw(
            self._get_option(
                CONF_OPTIMIZATION_MAX_GRID_IMPORT_W,
                self.config_entry.data.get(
                    CONF_OPTIMIZATION_MAX_GRID_IMPORT_W,
                    0,
                ),
            ),
            0,
        )
        current_allow_grid_charge = self._get_option(
            CONF_OPTIMIZATION_ALLOW_GRID_CHARGE,
            self.config_entry.data.get(CONF_OPTIMIZATION_ALLOW_GRID_CHARGE, True),
        )
        current_max_grid_charge_price = _stored_optional_price_to_cents(
            self._get_option(
                CONF_OPTIMIZATION_MAX_GRID_CHARGE_PRICE,
                self.config_entry.data.get(CONF_OPTIMIZATION_MAX_GRID_CHARGE_PRICE),
            )
        )
        current_grid_charge_soc_cap = _stored_ratio_to_percent(
            self._get_option(
                CONF_OPTIMIZATION_GRID_CHARGE_SOC_CAP,
                self.config_entry.data.get(CONF_OPTIMIZATION_GRID_CHARGE_SOC_CAP, 1.0),
            ),
            1.0,
        )
        current_spread_export_enabled = self._get_option(
            CONF_OPTIMIZATION_SPREAD_EXPORT_ENABLED,
            self.config_entry.data.get(CONF_OPTIMIZATION_SPREAD_EXPORT_ENABLED, False),
        )
        current_spread_import_enabled = self._get_option(
            CONF_OPTIMIZATION_SPREAD_IMPORT_ENABLED,
            self.config_entry.data.get(CONF_OPTIMIZATION_SPREAD_IMPORT_ENABLED, False),
        )
        current_disable_idle = self._get_option(
            CONF_OPTIMIZATION_DISABLE_IDLE,
            self.config_entry.data.get(CONF_OPTIMIZATION_DISABLE_IDLE, False),
        )
        current_ev_integration_enabled = self._get_option(
            CONF_OPTIMIZATION_EV_INTEGRATION,
            self.config_entry.data.get(CONF_OPTIMIZATION_EV_INTEGRATION, False),
        )
        current_load_entity = _normalize_optional_entity(
            self._get_option(
                CONF_OPTIMIZATION_LOAD_ENTITY,
                self.config_entry.data.get(CONF_OPTIMIZATION_LOAD_ENTITY),
            )
        )
        current_planned_ev_load_entity = _normalize_optional_entity(
            self._get_option(
                CONF_OPTIMIZATION_PLANNED_EV_LOAD_ENTITY,
                self.config_entry.data.get(CONF_OPTIMIZATION_PLANNED_EV_LOAD_ENTITY),
            )
        )
        current_profit_max_enabled = self._get_option(
            CONF_PROFIT_MAX_ENABLED,
            self.config_entry.data.get(CONF_PROFIT_MAX_ENABLED, False),
        )
        current_charge_by_time_enabled = self._get_option(
            CONF_CHARGE_BY_TIME_ENABLED,
            self.config_entry.data.get(
                CONF_CHARGE_BY_TIME_ENABLED,
                bool(current_profit_max_enabled),
            ),
        )
        current_charge_by_time_target_time = self._get_option(
            CONF_CHARGE_BY_TIME_TARGET_TIME,
            self.config_entry.data.get(
                CONF_CHARGE_BY_TIME_TARGET_TIME,
                self.config_entry.data.get(
                    CONF_PROFIT_MAX_TARGET_TIME,
                    DEFAULT_CHARGE_BY_TIME_TARGET_TIME,
                ),
            ),
        )
        current_charge_by_time_target_soc = _stored_ratio_to_percent(
            self._get_option(
                CONF_CHARGE_BY_TIME_TARGET_SOC,
                self.config_entry.data.get(
                    CONF_CHARGE_BY_TIME_TARGET_SOC,
                    self.config_entry.data.get(
                        CONF_PROFIT_MAX_TARGET_SOC,
                        DEFAULT_CHARGE_BY_TIME_TARGET_SOC,
                    ),
                ),
            ),
            DEFAULT_CHARGE_BY_TIME_TARGET_SOC,
        )

        opt_providers = _optimization_provider_options_for_battery(battery_system)
        schema_fields: dict[Any, Any] = {
            vol.Required(
                CONF_OPTIMIZATION_PROVIDER,
                default=current_opt_provider,
            ): SelectSelector(SelectSelectorConfig(
                options=[
                    SelectOptionDict(value=k, label=v)
                    for k, v in opt_providers.items()
                ],
                mode=SelectSelectorMode.DROPDOWN,
            )),
            vol.Required(
                CONF_OPTIMIZATION_ENABLED,
                default=bool(current_optimization_enabled),
            ): BooleanSelector(),
            vol.Required(
                CONF_OPTIMIZATION_AUTO_APPLY_RESERVE,
                default=bool(current_auto_apply_reserve),
            ): BooleanSelector(),
            vol.Required(
                CONF_OPTIMIZATION_EV_INTEGRATION,
                default=bool(current_ev_integration_enabled),
            ): BooleanSelector(),
            vol.Optional(
                CONF_OPTIMIZATION_LOAD_ENTITY,
                description=(
                    {"suggested_value": current_load_entity}
                    if current_load_entity
                    else None
                ),
            ): EntitySelector(EntitySelectorConfig(domain="sensor")),
            vol.Optional(
                CONF_OPTIMIZATION_PLANNED_EV_LOAD_ENTITY,
                description=(
                    {"suggested_value": current_planned_ev_load_entity}
                    if current_planned_ev_load_entity
                    else None
                ),
            ): EntitySelector(EntitySelectorConfig(domain="sensor")),
            vol.Required(
                CONF_MONITORING_MODE,
                default=bool(current_monitoring_mode),
            ): BooleanSelector(),
        }
        if battery_system == BATTERY_SYSTEM_NEOVOLT:
            schema_fields[
                vol.Required(
                    CONF_NEOVOLT_SURPLUS_BALANCER_MODE,
                    default=current_surplus_balancer_mode,
                )
            ] = SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(value=mode, label=mode.title())
                        for mode in NEOVOLT_SURPLUS_BALANCER_MODES
                    ],
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
        schema_fields.update(
            {
                vol.Required(
                    CONF_OPTIMIZATION_BACKUP_RESERVE,
                    default=int(display_backup_reserve * 100)
                    if display_backup_reserve < 1
                    else int(display_backup_reserve),
                ): NumberSelector(NumberSelectorConfig(
                    min=0, max=100, step=1, unit_of_measurement="%",
                    mode=NumberSelectorMode.SLIDER,
                )),
                vol.Required(
                    CONF_HARDWARE_BACKUP_RESERVE,
                    default=int(current_hardware_backup_reserve * 100)
                    if current_hardware_backup_reserve < 1
                    else int(current_hardware_backup_reserve),
                ): NumberSelector(NumberSelectorConfig(
                    min=0, max=100, step=1, unit_of_measurement="%",
                    mode=NumberSelectorMode.SLIDER,
                )),
                vol.Required(
                    CONF_OPTIMIZATION_BATTERY_CAPACITY_WH,
                    default=current_capacity_kwh,
                ): NumberSelector(NumberSelectorConfig(
                    min=1, max=200, step=0.1, unit_of_measurement="kWh",
                    mode=NumberSelectorMode.BOX,
                )),
                vol.Required(
                    CONF_OPTIMIZATION_MAX_CHARGE_W,
                    default=current_charge_kw,
                ): NumberSelector(NumberSelectorConfig(
                    min=0.1, max=50, step=0.1, unit_of_measurement="kW",
                    mode=NumberSelectorMode.BOX,
                )),
                vol.Required(
                    CONF_OPTIMIZATION_MAX_DISCHARGE_W,
                    default=current_discharge_kw,
                ): NumberSelector(NumberSelectorConfig(
                    min=0.1, max=50, step=0.1, unit_of_measurement="kW",
                    mode=NumberSelectorMode.BOX,
                )),
                vol.Optional(
                    CONF_OPTIMIZATION_MAX_GRID_EXPORT_W,
                    description=(
                        {"suggested_value": current_max_grid_export_kw}
                        if current_max_grid_export_kw is not None
                        else None
                    ),
                ): NumberSelector(NumberSelectorConfig(
                    min=0, max=100, step=0.1, unit_of_measurement="kW",
                    mode=NumberSelectorMode.BOX,
                )),
                vol.Required(
                    CONF_OPTIMIZATION_MAX_GRID_IMPORT_W,
                    default=current_max_grid_import_kw,
                ): NumberSelector(NumberSelectorConfig(
                    min=0, max=100, step=0.1, unit_of_measurement="kW",
                    mode=NumberSelectorMode.BOX,
                )),
                vol.Required(
                    CONF_OPTIMIZATION_ALLOW_GRID_CHARGE,
                    default=bool(current_allow_grid_charge),
                ): BooleanSelector(),
                vol.Required(
                    CONF_OPTIMIZATION_MAX_GRID_CHARGE_PRICE,
                    default=current_max_grid_charge_price,
                ): NumberSelector(NumberSelectorConfig(
                    min=0, max=200, step=0.1, unit_of_measurement="c/kWh",
                    mode=NumberSelectorMode.BOX,
                )),
                vol.Required(
                    CONF_OPTIMIZATION_GRID_CHARGE_SOC_CAP,
                    default=current_grid_charge_soc_cap,
                ): NumberSelector(NumberSelectorConfig(
                    min=0, max=100, step=1, unit_of_measurement="%",
                    mode=NumberSelectorMode.SLIDER,
                )),
            }
        )
        if not is_tesla:
            schema_fields.update({
                vol.Required(
                    CONF_OPTIMIZATION_SPREAD_EXPORT_ENABLED,
                    default=bool(current_spread_export_enabled),
                ): BooleanSelector(),
                vol.Required(
                    CONF_OPTIMIZATION_SPREAD_IMPORT_ENABLED,
                    default=bool(current_spread_import_enabled),
                ): BooleanSelector(),
            })
        if supports_no_idle_mode:
            schema_fields[
                vol.Required(
                    CONF_OPTIMIZATION_DISABLE_IDLE,
                    default=bool(current_disable_idle),
                )
            ] = BooleanSelector()
        schema_fields.update(
            {
                vol.Required(
                    CONF_PROFIT_MAX_ENABLED,
                    default=bool(current_profit_max_enabled),
                ): BooleanSelector(),
                vol.Required(
                    CONF_CHARGE_BY_TIME_ENABLED,
                    default=bool(current_charge_by_time_enabled),
                ): BooleanSelector(),
                vol.Required(
                    CONF_CHARGE_BY_TIME_TARGET_TIME,
                    default=current_charge_by_time_target_time,
                ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
                vol.Required(
                    CONF_CHARGE_BY_TIME_TARGET_SOC,
                    default=current_charge_by_time_target_soc,
                ): NumberSelector(NumberSelectorConfig(
                    min=0, max=100, step=1, unit_of_measurement="%",
                    mode=NumberSelectorMode.SLIDER,
                )),
            }
        )

        return self.async_show_form(
            step_id="optimization",
            data_schema=vol.Schema(schema_fields),
        )

    async def async_step_inverter(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Menu handler: configure AC-coupled inverter for curtailment."""
        self._from_menu = True
        return await self.async_step_inverter_brand(user_input)

    async def async_step_curtailment(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Menu handler: curtailment settings only."""
        self._from_menu = True
        return await self.async_step_curtailment_options(user_input)

    async def async_step_demand_charges(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Menu handler: demand charge settings only."""
        self._from_menu = True
        return await self.async_step_demand_charge_options(user_input)

    async def async_step_weather(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Menu handler: weather settings only."""
        self._from_menu = True
        return await self.async_step_weather_options(user_input)

    async def async_step_init_tesla(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 1 for Tesla users: select electricity provider and Tesla API providers."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Store provider selections
            self._provider = user_input.get(CONF_ELECTRICITY_PROVIDER, "amber")
            self._tesla_provider = user_input.get(
                CONF_TESLA_API_PROVIDER, TESLA_PROVIDER_TESLEMETRY
            )

            # Tesla EV provider — independent from energy provider
            ev_choice = user_input.get(
                CONF_TESLA_EV_API_PROVIDER, TESLA_EV_API_PROVIDER_NONE
            )
            self._tesla_ev_provider_choice = ev_choice
            detected = _detect_tesla_ev_integrations(self.hass)
            if (
                ev_choice == TESLA_EV_API_PROVIDER_FLEET_API
                and not detected["tesla_fleet"]
            ):
                errors[CONF_TESLA_EV_API_PROVIDER] = "tesla_fleet_not_installed"
            elif (
                ev_choice == TESLA_EV_API_PROVIDER_TESLEMETRY
                and not detected["teslemetry"]
                and not self.config_entry.data.get(CONF_TESLA_EV_TELEMETRY_TOKEN)
            ):
                # No detected integration AND no stored EV-specific token —
                # need the user to enter one. Stash the partial selection
                # before routing to the token step.
                self._pending_init_tesla_input = dict(user_input)
                return await self.async_step_options_tesla_ev_token()

            current_tesla_provider = self.config_entry.data.get(
                CONF_TESLA_API_PROVIDER, TESLA_PROVIDER_TESLEMETRY
            )

            if not errors:
                # Persist the EV provider choice now (before any sub-step
                # branches), since it doesn't depend on the energy provider.
                new_data = dict(self.config_entry.data)
                new_data[CONF_TESLA_EV_API_PROVIDER] = ev_choice
                self.hass.config_entries.async_update_entry(
                    self.config_entry, data=new_data
                )

                # If user picked PowerSync, route to the token entry step.
                # The step itself accepts an empty submission to keep the current
                # token if it's already a valid psync_ token.
                if self._tesla_provider == TESLA_PROVIDER_POWERSYNC:
                    return await self.async_step_powersync_token()

                # Same for Teslemetry — always show the token step so the user can
                # optionally rotate it. Empty submission keeps the current token.
                if self._tesla_provider == TESLA_PROVIDER_TESLEMETRY:
                    return await self.async_step_teslemetry_token()

                # Fleet API — no token entry needed
                new_data = dict(self.config_entry.data)
                if self._tesla_provider != current_tesla_provider:
                    new_data[CONF_TESLA_API_PROVIDER] = self._tesla_provider
                new_data[CONF_TESLA_EV_API_PROVIDER] = ev_choice

                self.hass.config_entries.async_update_entry(
                    self.config_entry, data=new_data
                )

                # Route to provider-specific step
                if self._provider == "amber":
                    return await self.async_step_amber_options()
                elif self._provider == "flow_power":
                    return await self.async_step_flow_power_options()
                elif self._provider in CUSTOM_TOU_PROVIDER_OPTIONS:
                    return await self._async_route_custom_tou_options(self._provider)
                elif self._provider == "localvolts":
                    return await self.async_step_localvolts_options()
                elif self._provider == "octopus":
                    return await self.async_step_octopus_options()
                elif self._provider == "epex":
                    return await self.async_step_epex_options()
                elif self._provider == "nz":
                    return await self.async_step_nz_options()

        current_provider = self._get_option(CONF_ELECTRICITY_PROVIDER, "amber")
        current_tesla_provider = self.config_entry.data.get(
            CONF_TESLA_API_PROVIDER, TESLA_PROVIDER_TESLEMETRY
        )
        current_ev_provider = self.config_entry.data.get(
            CONF_TESLA_EV_API_PROVIDER, TESLA_EV_API_PROVIDER_NONE
        )

        # Build Tesla provider choices
        tesla_providers = {
            TESLA_PROVIDER_POWERSYNC: "PowerSync (Free - sign in with Tesla, recommended)",
            TESLA_PROVIDER_FLEET_API: "Tesla Fleet API (Free - requires Tesla Fleet integration)",
            TESLA_PROVIDER_TESLEMETRY: "Teslemetry (~$4/month)",
        }

        # Tesla EV provider choices (with detection annotations)
        tesla_ev_providers = _build_tesla_ev_provider_choices(self.hass)

        return self.async_show_form(
            step_id="init_tesla",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_ELECTRICITY_PROVIDER,
                        default=current_provider,
                    ): SelectSelector(SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=k, label=v)
                            for k, v in ELECTRICITY_PROVIDERS.items()
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )),
                    vol.Required(
                        CONF_TESLA_API_PROVIDER,
                        default=current_tesla_provider,
                    ): SelectSelector(SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=k, label=v)
                            for k, v in tesla_providers.items()
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )),
                    vol.Required(
                        CONF_TESLA_EV_API_PROVIDER,
                        default=current_ev_provider,
                    ): SelectSelector(SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=k, label=v)
                            for k, v in tesla_ev_providers.items()
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )),
                }
            ),
            errors=errors,
        )

    async def async_step_options_tesla_ev_token(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Collect a Teslemetry token for vehicle commands (options flow)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input.get(CONF_TESLA_EV_TELEMETRY_TOKEN, "").strip()
            if token:
                validation_result = await validate_teslemetry_token(self.hass, token)
                if validation_result["success"]:
                    new_data = dict(self.config_entry.data)
                    new_data[CONF_TESLA_EV_API_PROVIDER] = (
                        TESLA_EV_API_PROVIDER_TESLEMETRY
                    )
                    new_data[CONF_TESLA_EV_TELEMETRY_TOKEN] = token
                    self.hass.config_entries.async_update_entry(
                        self.config_entry, data=new_data
                    )
                    # Resume the original init step now that the token is saved
                    pending = getattr(self, "_pending_init_tesla_input", None)
                    if pending is not None:
                        self._pending_init_tesla_input = None
                        return await self.async_step_init_tesla(pending)
                    return await self.async_step_init_tesla()
                errors["base"] = validation_result.get("error", "unknown")
            else:
                errors["base"] = "no_token_provided"

        return self.async_show_form(
            step_id="options_tesla_ev_token",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_TESLA_EV_TELEMETRY_TOKEN): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
            description_placeholders={
                "teslemetry_url": "https://teslemetry.com",
            },
        )

    async def async_step_init_sigenergy(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 1 for Sigenergy users: Configure Modbus connection and DC curtailment.

        Supports both plain password (recommended) and pre-encoded pass_enc (advanced).
        """
        from ..sigenergy_api import encode_sigenergy_password

        errors: dict[str, str] = {}

        if user_input is not None:
            # Validate Modbus host
            modbus_host = user_input.get(CONF_SIGENERGY_MODBUS_HOST, "").strip()
            if not modbus_host:
                errors["base"] = "modbus_host_required"
            else:
                # Store provider and Sigenergy settings
                self._provider = user_input.get(CONF_ELECTRICITY_PROVIDER, "amber")

                # Update config entry data with Sigenergy Modbus settings
                new_data = dict(self.config_entry.data)
                new_data[CONF_SIGENERGY_MODBUS_HOST] = modbus_host
                new_data[CONF_SIGENERGY_MODBUS_PORT] = user_input.get(
                    CONF_SIGENERGY_MODBUS_PORT, DEFAULT_SIGENERGY_MODBUS_PORT
                )
                new_data[CONF_SIGENERGY_MODBUS_SLAVE_ID] = user_input.get(
                    CONF_SIGENERGY_MODBUS_SLAVE_ID, DEFAULT_SIGENERGY_MODBUS_SLAVE_ID
                )
                new_data[CONF_SIGENERGY_DC_CURTAILMENT_ENABLED] = user_input.get(
                    CONF_SIGENERGY_DC_CURTAILMENT_ENABLED, False
                )
                export_limit = user_input.get(CONF_SIGENERGY_EXPORT_LIMIT_KW)
                if export_limit is not None:
                    new_data[CONF_SIGENERGY_EXPORT_LIMIT_KW] = export_limit
                elif CONF_SIGENERGY_EXPORT_LIMIT_KW in new_data:
                    del new_data[CONF_SIGENERGY_EXPORT_LIMIT_KW]

                # Update Sigenergy Cloud API credentials if provided
                sigen_username = user_input.get(CONF_SIGENERGY_USERNAME, "").strip()
                sigen_password = user_input.get(CONF_SIGENERGY_PASSWORD, "").strip()
                sigen_pass_enc = user_input.get(CONF_SIGENERGY_PASS_ENC, "").strip()
                sigen_device_id = user_input.get(CONF_SIGENERGY_DEVICE_ID, "").strip()
                sigen_cloud_region = user_input.get(
                    CONF_SIGENERGY_CLOUD_REGION,
                    DEFAULT_SIGENERGY_CLOUD_REGION,
                )
                sigen_station_id = user_input.get(CONF_SIGENERGY_STATION_ID, "").strip()

                # Determine final pass_enc: explicit pass_enc > encoded password
                if sigen_pass_enc:
                    final_pass_enc = sigen_pass_enc
                elif sigen_password:
                    final_pass_enc = encode_sigenergy_password(sigen_password)
                else:
                    final_pass_enc = ""

                if sigen_username:
                    new_data[CONF_SIGENERGY_USERNAME] = sigen_username
                if final_pass_enc:
                    new_data[CONF_SIGENERGY_PASS_ENC] = final_pass_enc
                if sigen_device_id:
                    new_data[CONF_SIGENERGY_DEVICE_ID] = sigen_device_id
                previous_cloud_region = new_data.get(
                    CONF_SIGENERGY_CLOUD_REGION,
                    DEFAULT_SIGENERGY_CLOUD_REGION,
                )
                new_data[CONF_SIGENERGY_CLOUD_REGION] = sigen_cloud_region
                if previous_cloud_region != sigen_cloud_region:
                    new_data.pop(CONF_SIGENERGY_ACCESS_TOKEN, None)
                    new_data.pop(CONF_SIGENERGY_REFRESH_TOKEN, None)
                    new_data.pop(CONF_SIGENERGY_TOKEN_EXPIRES_AT, None)
                if sigen_station_id:
                    previous_station_id = new_data.get(CONF_SIGENERGY_STATION_ID)
                    new_data[CONF_SIGENERGY_STATION_ID] = sigen_station_id
                    if previous_station_id != sigen_station_id:
                        new_data.pop(CONF_SIGENERGY_TARIFF_STATION_ID, None)
                        new_data.pop(CONF_SIGENERGY_TARIFF_STATION_SOURCE_ID, None)

                self.hass.config_entries.async_update_entry(
                    self.config_entry, data=new_data
                )

                # Route to provider-specific step
                if self._provider == "amber":
                    return await self.async_step_amber_options()
                elif self._provider == "flow_power":
                    return await self.async_step_flow_power_options()
                elif self._provider in CUSTOM_TOU_PROVIDER_OPTIONS:
                    return await self._async_route_custom_tou_options(self._provider)
                elif self._provider == "octopus":
                    return await self.async_step_octopus_options()
                elif self._provider == "epex":
                    return await self.async_step_epex_options()
                elif self._provider == "nz":
                    return await self.async_step_nz_options()

        current_provider = self._get_option(CONF_ELECTRICITY_PROVIDER, "amber")
        current_modbus_host = self._get_option(CONF_SIGENERGY_MODBUS_HOST, "")
        current_modbus_port = self._get_option(
            CONF_SIGENERGY_MODBUS_PORT, DEFAULT_SIGENERGY_MODBUS_PORT
        )
        current_modbus_slave_id = self._get_option(
            CONF_SIGENERGY_MODBUS_SLAVE_ID, DEFAULT_SIGENERGY_MODBUS_SLAVE_ID
        )
        current_dc_curtailment = self._get_option(
            CONF_SIGENERGY_DC_CURTAILMENT_ENABLED, False
        )
        current_export_limit = self.config_entry.data.get(
            CONF_SIGENERGY_EXPORT_LIMIT_KW
        )
        # Get current Sigenergy Cloud credentials (for display, show empty if not set)
        current_sigen_username = self.config_entry.data.get(CONF_SIGENERGY_USERNAME, "")
        current_sigen_device_id = self.config_entry.data.get(
            CONF_SIGENERGY_DEVICE_ID, ""
        )
        current_sigen_cloud_region = self.config_entry.data.get(
            CONF_SIGENERGY_CLOUD_REGION, DEFAULT_SIGENERGY_CLOUD_REGION
        )
        current_sigen_station_id = self.config_entry.data.get(
            CONF_SIGENERGY_STATION_ID, ""
        )
        # Don't show current password for security - user must re-enter if changing

        return self.async_show_form(
            step_id="init_sigenergy",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_ELECTRICITY_PROVIDER,
                        default=current_provider,
                    ): SelectSelector(SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=k, label=v)
                            for k, v in ELECTRICITY_PROVIDERS.items()
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )),
                    vol.Required(
                        CONF_SIGENERGY_MODBUS_HOST,
                        default=current_modbus_host,
                    ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
                    vol.Optional(
                        CONF_SIGENERGY_MODBUS_PORT,
                        default=current_modbus_port,
                    ): NumberSelector(NumberSelectorConfig(
                        min=1, max=65535, step=1, mode=NumberSelectorMode.BOX,
                    )),
                    vol.Optional(
                        CONF_SIGENERGY_MODBUS_SLAVE_ID,
                        default=current_modbus_slave_id,
                    ): NumberSelector(NumberSelectorConfig(
                        min=0, max=247, step=1, mode=NumberSelectorMode.BOX,
                    )),
                    vol.Optional(
                        CONF_SIGENERGY_DC_CURTAILMENT_ENABLED,
                        default=current_dc_curtailment,
                    ): BooleanSelector(),
                    vol.Optional(
                        CONF_SIGENERGY_EXPORT_LIMIT_KW,
                        description={"suggested_value": current_export_limit},
                    ): NumberSelector(NumberSelectorConfig(
                        min=0, max=100, step=0.1, unit_of_measurement="kW",
                        mode=NumberSelectorMode.BOX,
                    )),
                    # Sigenergy Cloud API credentials for tariff sync
                    vol.Optional(
                        CONF_SIGENERGY_USERNAME,
                        default=current_sigen_username,
                        description={"suggested_value": current_sigen_username},
                    ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
                    vol.Optional(
                        CONF_SIGENERGY_PASSWORD,  # Plain password (recommended)
                        description={"suggested_value": ""},
                    ): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
                    vol.Optional(
                        CONF_SIGENERGY_PASS_ENC,  # Advanced: pre-encoded
                        description={"suggested_value": ""},
                    ): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
                    vol.Optional(
                        CONF_SIGENERGY_DEVICE_ID,
                        default=current_sigen_device_id,
                        description={"suggested_value": current_sigen_device_id},
                    ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
                    vol.Required(
                        CONF_SIGENERGY_CLOUD_REGION,
                        default=current_sigen_cloud_region,
                    ): SelectSelector(SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=k, label=v)
                            for k, v in SIGENERGY_CLOUD_REGIONS.items()
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )),
                    vol.Optional(
                        CONF_SIGENERGY_STATION_ID,
                        default=current_sigen_station_id,
                        description={"suggested_value": current_sigen_station_id},
                    ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
                }
            ),
            errors=errors,
        )

    async def async_step_init_sungrow(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 1 for Sungrow users: Configure Modbus connection settings."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Validate Modbus host
            modbus_host = user_input.get(CONF_SUNGROW_HOST, "").strip()
            if not modbus_host:
                errors["base"] = "sungrow_host_required"
            else:
                # Store provider and Sungrow settings
                self._provider = user_input.get(CONF_ELECTRICITY_PROVIDER, "amber")

                # Update config entry data with Sungrow Modbus settings
                new_data = dict(self.config_entry.data)
                new_options = dict(self.config_entry.options)
                sungrow_port = user_input.get(
                    CONF_SUNGROW_PORT, DEFAULT_SUNGROW_PORT
                )
                sungrow_slave_id = user_input.get(
                    CONF_SUNGROW_SLAVE_ID, DEFAULT_SUNGROW_SLAVE_ID
                )
                new_data[CONF_SUNGROW_HOST] = modbus_host
                new_data[CONF_SUNGROW_PORT] = sungrow_port
                new_data[CONF_SUNGROW_SLAVE_ID] = sungrow_slave_id
                new_options[CONF_SUNGROW_HOST] = modbus_host
                new_options[CONF_SUNGROW_PORT] = sungrow_port
                new_options[CONF_SUNGROW_SLAVE_ID] = sungrow_slave_id
                self._remove_legacy_sungrow_dual_options(new_data, new_options)

                if not errors:
                    self.hass.config_entries.async_update_entry(
                        self.config_entry, data=new_data, options=new_options
                    )

                    # Route to provider-specific step
                    if self._provider == "amber":
                        return await self.async_step_amber_options()
                    elif self._provider == "flow_power":
                        return await self.async_step_flow_power_options()
                    elif self._provider in CUSTOM_TOU_PROVIDER_OPTIONS:
                        return await self._async_route_custom_tou_options(self._provider)
                    elif self._provider == "octopus":
                        return await self.async_step_octopus_options()
                    elif self._provider == "epex":
                        return await self.async_step_epex_options()
                    elif self._provider == "nz":
                        return await self.async_step_nz_options()

        current_provider = self._get_option(CONF_ELECTRICITY_PROVIDER, "amber")
        current_host = self._get_option(CONF_SUNGROW_HOST, "")
        current_port = self._get_option(CONF_SUNGROW_PORT, DEFAULT_SUNGROW_PORT)
        current_slave_id = self._get_option(
            CONF_SUNGROW_SLAVE_ID, DEFAULT_SUNGROW_SLAVE_ID
        )
        return self.async_show_form(
            step_id="init_sungrow",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_ELECTRICITY_PROVIDER,
                        default=current_provider,
                    ): SelectSelector(SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=k, label=v)
                            for k, v in ELECTRICITY_PROVIDERS.items()
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )),
                    vol.Required(
                        CONF_SUNGROW_HOST,
                        default=current_host,
                    ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
                    vol.Optional(
                        CONF_SUNGROW_PORT,
                        default=current_port,
                    ): NumberSelector(NumberSelectorConfig(
                        min=1, max=65535, step=1, mode=NumberSelectorMode.BOX,
                    )),
                    vol.Optional(
                        CONF_SUNGROW_SLAVE_ID,
                        default=current_slave_id,
                    ): NumberSelector(NumberSelectorConfig(
                        min=1, max=247, step=1, mode=NumberSelectorMode.BOX,
                    )),
                }
            ),
            errors=errors,
        )

    async def async_step_init_foxess(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 1 for FoxESS users: configure connection settings."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Validate connection
            conn_type = user_input.get(
                CONF_FOXESS_CONNECTION_TYPE, FOXESS_CONNECTION_TCP
            )
            modbus_host = user_input.get(CONF_FOXESS_HOST, "").strip()
            serial_port = user_input.get(CONF_FOXESS_SERIAL_PORT, "").strip()
            cloud_api_key = user_input.get(CONF_FOXESS_CLOUD_API_KEY, "").strip()
            cloud_device_sn = user_input.get(CONF_FOXESS_CLOUD_DEVICE_SN, "").strip()
            entity_entry_id = user_input.get(CONF_FOXESS_ENTITY_CONFIG_ENTRY_ID, "").strip()
            entity_prefix = user_input.get(CONF_FOXESS_ENTITY_PREFIX, "").strip()
            entity_entries = _foxess_modbus_entry_options(self.hass)
            if conn_type == FOXESS_CONNECTION_ENTITY and not entity_entry_id and len(entity_entries) == 1:
                entity_entry_id = entity_entries[0]["value"]

            if conn_type == FOXESS_CONNECTION_TCP and not modbus_host:
                errors["base"] = "foxess_host_required"
            elif conn_type == FOXESS_CONNECTION_SERIAL and not serial_port:
                errors["base"] = "foxess_serial_required"
            elif conn_type == FOXESS_CONNECTION_CLOUD and (not cloud_api_key or not cloud_device_sn):
                errors["base"] = "foxess_cloud_required"
            elif conn_type == FOXESS_CONNECTION_ENTITY:
                valid, error = await _validate_foxess_entity_bridge(
                    self.hass,
                    entity_entry_id,
                    entity_prefix,
                )
                if not valid:
                    errors["base"] = error or "foxess_entity_connect_failed"

            if not errors:
                self._provider = user_input.get(CONF_ELECTRICITY_PROVIDER, "amber")

                # Update config entry data with FoxESS settings
                new_data = dict(self.config_entry.data)
                new_data[CONF_FOXESS_CONNECTION_TYPE] = conn_type
                if conn_type == FOXESS_CONNECTION_TCP:
                    new_data[CONF_FOXESS_HOST] = modbus_host
                    new_data[CONF_FOXESS_PORT] = user_input.get(
                        CONF_FOXESS_PORT, DEFAULT_FOXESS_PORT
                    )
                elif conn_type == FOXESS_CONNECTION_SERIAL:
                    new_data[CONF_FOXESS_SERIAL_PORT] = serial_port
                    new_data[CONF_FOXESS_SERIAL_BAUDRATE] = user_input.get(
                        CONF_FOXESS_SERIAL_BAUDRATE, DEFAULT_FOXESS_SERIAL_BAUDRATE
                    )
                elif conn_type == FOXESS_CONNECTION_ENTITY:
                    if entity_entry_id:
                        new_data[CONF_FOXESS_ENTITY_CONFIG_ENTRY_ID] = entity_entry_id
                    else:
                        new_data.pop(CONF_FOXESS_ENTITY_CONFIG_ENTRY_ID, None)
                    if entity_prefix:
                        new_data[CONF_FOXESS_ENTITY_PREFIX] = entity_prefix
                    else:
                        new_data.pop(CONF_FOXESS_ENTITY_PREFIX, None)
                else:
                    new_data[CONF_FOXESS_CLOUD_API_KEY] = cloud_api_key
                    new_data[CONF_FOXESS_CLOUD_DEVICE_SN] = cloud_device_sn
                new_data[CONF_FOXESS_SLAVE_ID] = user_input.get(
                    CONF_FOXESS_SLAVE_ID, DEFAULT_FOXESS_SLAVE_ID
                )

                self.hass.config_entries.async_update_entry(
                    self.config_entry, data=new_data
                )

                # Route to provider-specific step
                if self._provider == "amber":
                    return await self.async_step_amber_options()
                elif self._provider == "flow_power":
                    return await self.async_step_flow_power_options()
                elif self._provider in CUSTOM_TOU_PROVIDER_OPTIONS:
                    return await self._async_route_custom_tou_options(self._provider)
                elif self._provider == "octopus":
                    return await self.async_step_octopus_options()
                elif self._provider == "epex":
                    return await self.async_step_epex_options()
                elif self._provider == "nz":
                    return await self.async_step_nz_options()

        current_provider = self._get_option(CONF_ELECTRICITY_PROVIDER, "amber")
        current_conn_type = self._get_option(
            CONF_FOXESS_CONNECTION_TYPE, FOXESS_CONNECTION_TCP
        )
        current_host = self._get_option(CONF_FOXESS_HOST, "")
        current_port = self._get_option(CONF_FOXESS_PORT, DEFAULT_FOXESS_PORT)
        current_slave_id = self._get_option(
            CONF_FOXESS_SLAVE_ID, DEFAULT_FOXESS_SLAVE_ID
        )
        current_serial_port = self._get_option(CONF_FOXESS_SERIAL_PORT, "")
        current_baudrate = self._get_option(
            CONF_FOXESS_SERIAL_BAUDRATE, DEFAULT_FOXESS_SERIAL_BAUDRATE
        )
        current_cloud_api_key = self._get_option(CONF_FOXESS_CLOUD_API_KEY, "")
        current_cloud_device_sn = self._get_option(CONF_FOXESS_CLOUD_DEVICE_SN, "")
        current_entity_entry_id = self._get_option(CONF_FOXESS_ENTITY_CONFIG_ENTRY_ID, "")
        current_entity_prefix = self._get_option(CONF_FOXESS_ENTITY_PREFIX, "")
        foxess_conn_types_legacy = {
            FOXESS_CONNECTION_TCP: "Modbus TCP",
            FOXESS_CONNECTION_SERIAL: "RS485 Serial",
            FOXESS_CONNECTION_CLOUD: "FoxESS Cloud API",
            FOXESS_CONNECTION_ENTITY: "Entity bridge (foxess_modbus)",
        }

        schema_fields: dict[Any, Any] = {
            vol.Required(
                CONF_ELECTRICITY_PROVIDER,
                default=current_provider,
            ): SelectSelector(SelectSelectorConfig(
                options=[
                    SelectOptionDict(value=k, label=v)
                    for k, v in ELECTRICITY_PROVIDERS.items()
                ],
                mode=SelectSelectorMode.DROPDOWN,
            )),
            vol.Required(
                CONF_FOXESS_CONNECTION_TYPE,
                default=current_conn_type,
            ): SelectSelector(SelectSelectorConfig(
                options=[
                    SelectOptionDict(value=k, label=v)
                    for k, v in foxess_conn_types_legacy.items()
                ],
                mode=SelectSelectorMode.DROPDOWN,
            )),
            vol.Optional(
                CONF_FOXESS_HOST,
                default=current_host,
            ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
            vol.Optional(
                CONF_FOXESS_PORT,
                default=current_port,
            ): NumberSelector(NumberSelectorConfig(
                min=1, max=65535, step=1, mode=NumberSelectorMode.BOX,
            )),
            vol.Optional(
                CONF_FOXESS_SLAVE_ID,
                default=current_slave_id,
            ): NumberSelector(NumberSelectorConfig(
                min=1, max=247, step=1, mode=NumberSelectorMode.BOX,
            )),
            vol.Optional(
                CONF_FOXESS_SERIAL_PORT,
                default=current_serial_port,
            ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
            vol.Optional(
                CONF_FOXESS_SERIAL_BAUDRATE,
                default=current_baudrate,
            ): NumberSelector(NumberSelectorConfig(
                min=300, max=115200, step=1, mode=NumberSelectorMode.BOX,
            )),
            vol.Optional(
                CONF_FOXESS_CLOUD_API_KEY,
                default=current_cloud_api_key,
            ): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
            vol.Optional(
                CONF_FOXESS_CLOUD_DEVICE_SN,
                default=current_cloud_device_sn,
            ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
        }
        entity_entry_options = _foxess_modbus_entry_options(self.hass)
        if entity_entry_options:
            schema_fields[
                vol.Optional(
                    CONF_FOXESS_ENTITY_CONFIG_ENTRY_ID,
                    default=current_entity_entry_id or entity_entry_options[0]["value"],
                )
            ] = SelectSelector(
                SelectSelectorConfig(
                    options=entity_entry_options,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
        schema_fields[
            vol.Optional(CONF_FOXESS_ENTITY_PREFIX, default=current_entity_prefix)
        ] = TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT))

        return self.async_show_form(
            step_id="init_foxess",
            data_schema=vol.Schema(schema_fields),
            errors=errors,
        )

    async def async_step_init_goodwe(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 1 for GoodWe users: configure connection settings."""
        errors: dict[str, str] = {}

        if user_input is not None:
            goodwe_host = user_input.get(CONF_GOODWE_HOST, "").strip()

            if not goodwe_host:
                errors["base"] = "goodwe_connect_failed"
            else:
                ems_prefix = user_input.get(CONF_GOODWE_EMS_ENTITY_PREFIX, "").strip()
                protocol = user_input.get(CONF_GOODWE_PROTOCOL, "udp")
                ems_control_mode = resolve_goodwe_ems_control_mode_for_protocol(
                    self.hass,
                    user_input.get(CONF_GOODWE_EMS_CONTROL_MODE),
                    ems_prefix,
                    protocol,
                )
                resolved_ems_prefix = (
                    resolve_goodwe_ems_entity_prefix(self.hass, ems_prefix)
                    if ems_control_mode == GOODWE_EMS_CONTROL_ENTITY
                    else ems_prefix
                )
                ems_error = validate_goodwe_ems_control_mode(
                    self.hass,
                    ems_control_mode,
                    resolved_ems_prefix,
                )
                if ems_error:
                    errors["base"] = ems_error
                else:
                    self._provider = user_input.get(CONF_ELECTRICITY_PROVIDER, "amber")

                    # Update config entry data with GoodWe settings
                    new_data = dict(self.config_entry.data)
                    new_data[CONF_GOODWE_HOST] = goodwe_host
                    new_data[CONF_GOODWE_PORT] = resolve_goodwe_port(
                        protocol, user_input.get(CONF_GOODWE_PORT)
                    )
                    new_data[CONF_GOODWE_PROTOCOL] = protocol
                    new_data[CONF_GOODWE_EMS_CONTROL_MODE] = ems_control_mode
                    if ems_control_mode == GOODWE_EMS_CONTROL_ENTITY:
                        new_data[CONF_GOODWE_EMS_ENTITY_PREFIX] = resolved_ems_prefix
                    else:
                        new_data.pop(CONF_GOODWE_EMS_ENTITY_PREFIX, None)

                    self.hass.config_entries.async_update_entry(
                        self.config_entry, data=new_data
                    )

                    # Route to provider-specific step
                    if self._provider == "amber":
                        return await self.async_step_amber_options()
                    elif self._provider == "flow_power":
                        return await self.async_step_flow_power_options()
                    elif self._provider in CUSTOM_TOU_PROVIDER_OPTIONS:
                        return await self._async_route_custom_tou_options(self._provider)
                    elif self._provider == "octopus":
                        return await self.async_step_octopus_options()
                    elif self._provider == "epex":
                        return await self.async_step_epex_options()
                    elif self._provider == "nz":
                        return await self.async_step_nz_options()

        current_provider = self._get_option(CONF_ELECTRICITY_PROVIDER, "amber")
        current_host = self._get_option(CONF_GOODWE_HOST, "")
        current_protocol = self._get_option(CONF_GOODWE_PROTOCOL, "udp")
        current_port = resolve_goodwe_port(
            current_protocol,
            self._get_option(CONF_GOODWE_PORT, DEFAULT_GOODWE_PORT_UDP),
        )
        current_ems_prefix_init = self._get_option(CONF_GOODWE_EMS_ENTITY_PREFIX, "")
        current_ems_control_mode_init = resolve_goodwe_ems_control_mode(
            self._get_option(CONF_GOODWE_EMS_CONTROL_MODE, None),
            current_ems_prefix_init,
        )
        goodwe_protocols_legacy = {
            "udp": "UDP direct control (port 8899)",
            "tcp": "TCP / LAN Kit-20 (port 502)",
        }

        return self.async_show_form(
            step_id="init_goodwe",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_ELECTRICITY_PROVIDER,
                        default=current_provider,
                    ): SelectSelector(SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=k, label=v)
                            for k, v in ELECTRICITY_PROVIDERS.items()
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )),
                    vol.Required(
                        CONF_GOODWE_HOST,
                        default=current_host,
                    ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
                    vol.Required(
                        CONF_GOODWE_PROTOCOL,
                        default=current_protocol,
                    ): SelectSelector(SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=k, label=v)
                            for k, v in goodwe_protocols_legacy.items()
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )),
                    vol.Optional(
                        CONF_GOODWE_PORT,
                        default=current_port,
                    ): NumberSelector(NumberSelectorConfig(
                        min=1, max=65535, step=1, mode=NumberSelectorMode.BOX,
                    )),
                    vol.Required(
                        CONF_GOODWE_EMS_CONTROL_MODE,
                        default=current_ems_control_mode_init,
                    ): SelectSelector(SelectSelectorConfig(
                        options=goodwe_ems_control_options(),
                        mode=SelectSelectorMode.DROPDOWN,
                    )),
                    vol.Optional(
                        CONF_GOODWE_EMS_ENTITY_PREFIX,
                        default=current_ems_prefix_init or "goodwe",
                        description={
                            "suggested_value": current_ems_prefix_init or "goodwe"
                        },
                    ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
                }
            ),
            errors=errors,
        )

    async def _async_route_custom_tou_options(self, provider: str) -> FlowResult:
        """Route custom/static TOU providers to their relevant options step."""
        if provider in ("other", "tou_only"):
            return await self.async_step_custom_tariff_options()
        return await self.async_step_globird_options()

    async def _async_route_to_provider_options(self) -> FlowResult:
        """Continue the options flow into the electricity-provider-specific step.

        `self._provider` is only set when the user entered this router via
        async_step_pricing (which explicitly assigns it). Other entry points —
        notably async_step_powersync_token after a Tesla sign-in — also call
        this router but never touch `_provider`, which used to raise
        AttributeError and surface to the user as "Unknown error" in the
        Tesla sign-in dialog. Fall back to the persisted provider on the
        config entry so every path into this function is valid.
        """
        provider = getattr(self, "_provider", None) or self._get_option(
            CONF_ELECTRICITY_PROVIDER, "amber"
        )
        self._provider = provider
        if provider == "amber":
            return await self.async_step_amber_options()
        if provider == "flow_power":
            return await self.async_step_flow_power_options()
        if provider in CUSTOM_TOU_PROVIDER_OPTIONS:
            return await self._async_route_custom_tou_options(provider)
        if provider == "localvolts":
            return await self.async_step_localvolts_options()
        if provider == "octopus":
            return await self.async_step_octopus_options()
        if provider == "epex":
            return await self.async_step_epex_options()
        if provider == "nz":
            return await self.async_step_nz_options()
        return await self.async_step_amber_options()

    async def async_step_powersync_token(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step to enter a PowerSync.cc proxy token (options flow path).

        Shown whenever the user picks PowerSync as the Tesla provider in the
        options flow. If a valid psync_ token is already saved, the user can
        submit empty to keep it. Otherwise they must paste a new one.
        """
        errors: dict[str, str] = {}
        current_token = self.config_entry.data.get(CONF_TESLEMETRY_API_TOKEN, "") or ""
        has_current_powersync_token = current_token.startswith("psync_")

        if user_input is not None:
            token = user_input.get(CONF_TESLEMETRY_API_TOKEN, "").strip()

            if not token and has_current_powersync_token:
                # Empty submission + existing token → keep the current one
                new_data = dict(self.config_entry.data)
                new_data[CONF_TESLA_API_PROVIDER] = TESLA_PROVIDER_POWERSYNC
                self.hass.config_entries.async_update_entry(
                    self.config_entry, data=new_data
                )
                return await self._async_route_to_provider_options()

            if not token:
                errors["base"] = "no_token_provided"
            else:
                validation_result = await validate_powersync_token(self.hass, token)
                if validation_result["success"]:
                    new_data = dict(self.config_entry.data)
                    new_data[CONF_TESLA_API_PROVIDER] = TESLA_PROVIDER_POWERSYNC
                    new_data[CONF_TESLEMETRY_API_TOKEN] = token
                    self.hass.config_entries.async_update_entry(
                        self.config_entry, data=new_data
                    )
                    return await self._async_route_to_provider_options()
                errors["base"] = validation_result.get("error", "unknown")

        if has_current_powersync_token:
            instructions = (
                "A PowerSync token is already saved.\n\n"
                "Leave the field blank and press **Submit** to keep the current token, "
                "or paste a new `psync_` token below to update it.\n\n"
                f"To get a new token, sign in again at:\n\n**{POWERSYNC_AUTH_START_URL}**"
            )
        else:
            instructions = (
                "1. Open this URL in a browser and sign in with your Tesla account:\n\n"
                f"**{POWERSYNC_AUTH_START_URL}**\n\n"
                "2. After signing in, you'll get a token starting with `psync_`.\n\n"
                "3. Paste it below to connect.\n\n"
                "Your Tesla credentials are never seen by PowerSync — Tesla handles "
                "authentication and we only receive a token to call the Fleet API on your behalf."
            )

        return self.async_show_form(
            step_id="powersync_token",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_TESLEMETRY_API_TOKEN,
                        default="",
                    ): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
                }
            ),
            errors=errors,
            description_placeholders={
                "auth_url": POWERSYNC_AUTH_START_URL,
                "instructions": instructions,
            },
        )

    async def async_step_teslemetry_token(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step to enter a Teslemetry API token (options flow path).

        Shown whenever the user picks Teslemetry as the Tesla provider in the
        options flow. If a valid Teslemetry token is already saved, the user
        can submit empty to keep it. Otherwise they must paste a new one.
        """
        errors: dict[str, str] = {}
        current_token = self.config_entry.data.get(CONF_TESLEMETRY_API_TOKEN, "") or ""
        # A "current Teslemetry token" is one that's set and isn't a psync_ token
        has_current_teslemetry_token = bool(
            current_token
        ) and not current_token.startswith("psync_")

        if user_input is not None:
            token = user_input.get(CONF_TESLEMETRY_API_TOKEN, "").strip()

            if not token and has_current_teslemetry_token:
                # Keep current token, just update provider
                new_data = dict(self.config_entry.data)
                new_data[CONF_TESLA_API_PROVIDER] = TESLA_PROVIDER_TESLEMETRY
                self.hass.config_entries.async_update_entry(
                    self.config_entry, data=new_data
                )
                return await self._async_route_to_provider_options()

            if not token:
                errors["base"] = "no_token_provided"
            else:
                validation_result = await validate_teslemetry_token(self.hass, token)
                if validation_result["success"]:
                    new_data = dict(self.config_entry.data)
                    new_data[CONF_TESLA_API_PROVIDER] = TESLA_PROVIDER_TESLEMETRY
                    new_data[CONF_TESLEMETRY_API_TOKEN] = token
                    self.hass.config_entries.async_update_entry(
                        self.config_entry, data=new_data
                    )
                    return await self._async_route_to_provider_options()
                errors["base"] = validation_result.get("error", "unknown")

        if has_current_teslemetry_token:
            instructions = (
                "A Teslemetry API token is already saved.\n\n"
                "Leave the field blank and press **Submit** to keep the current token, "
                "or paste a new token from teslemetry.com to update it."
            )
        else:
            instructions = (
                "Enter your Teslemetry API token. You can get this from teslemetry.com."
            )

        return self.async_show_form(
            step_id="teslemetry_token",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_TESLEMETRY_API_TOKEN,
                        default="",
                    ): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
                }
            ),
            errors=errors,
            description_placeholders={
                "instructions": instructions,
            },
        )

    async def async_step_amber_options(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 2a: Amber Electric specific options."""
        battery_system = self.config_entry.data.get(
            CONF_BATTERY_SYSTEM, BATTERY_SYSTEM_TESLA
        )
        is_tesla = battery_system == BATTERY_SYSTEM_TESLA
        errors: dict[str, str] = {}

        if user_input is not None:
            new_amber_token = user_input.get("update_amber_token", "").strip()
            # Validate new token immediately and re-render so the site picker appears
            if new_amber_token:
                try:
                    result = await validate_amber_token(self.hass, new_amber_token)
                    if result["success"]:
                        new_data = dict(self.config_entry.data)
                        new_data[CONF_AMBER_API_TOKEN] = new_amber_token
                        self.hass.config_entries.async_update_entry(
                            self.config_entry, data=new_data
                        )
                        _LOGGER.info("Amber API token updated via options flow")
                        self._opt_amber_sites = result.get("sites", [])
                        user_input = None  # Re-render with site dropdown now visible
                    else:
                        errors["base"] = "invalid_auth"
                        user_input = None
                except Exception:
                    errors["base"] = "cannot_connect"
                    user_input = None

        if user_input is not None:
            # Handle site selection before popping other fields
            new_site_id = user_input.pop(CONF_AMBER_SITE_ID, None)
            user_input.pop("update_amber_token", None)

            new_data = dict(self.config_entry.data)
            if new_site_id:
                new_data[CONF_AMBER_SITE_ID] = new_site_id
                _LOGGER.info("Amber site ID updated to %s via options flow", new_site_id)
                self.hass.config_entries.async_update_entry(
                    self.config_entry, data=new_data
                )

            # Store amber options temporarily
            self._amber_options = user_input
            self._amber_options[CONF_ELECTRICITY_PROVIDER] = "amber"

            # Force tariff mode toggle only applies to Tesla - set to False for Sigenergy
            if not is_tesla:
                self._amber_options[CONF_FORCE_TARIFF_MODE_TOGGLE] = False

            # If entered from menu, save this section only and finish
            if getattr(self, "_from_menu", False):
                return self._save_and_finish(self._amber_options)

            # Route to demand charge options page
            return await self.async_step_demand_charge_options()

        # Fetch sites from current stored token (skipped if already cached or just refreshed)
        if not hasattr(self, "_opt_amber_sites"):
            existing_token = self.config_entry.data.get(CONF_AMBER_API_TOKEN, "")
            if existing_token:
                try:
                    result = await validate_amber_token(self.hass, existing_token)
                    self._opt_amber_sites = result.get("sites", []) if result["success"] else []
                except Exception:
                    self._opt_amber_sites = []
            else:
                self._opt_amber_sites = []

        # Build schema dict - conditionally include force mode toggle for Tesla only
        amber_forecast_types = {
            "predicted": "Predicted (Default)",
            "low": "Low (Lower prices expected)",
            "high": "High (Higher prices expected)",
        }

        schema_dict = {
            vol.Optional(
                "update_amber_token",
                default="",
            ): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
        }

        # Show site selector whenever we have at least one site (confirms selection for all users)
        opt_sites = getattr(self, "_opt_amber_sites", [])
        if len(opt_sites) >= 1:
            current_site_id = self.config_entry.data.get(CONF_AMBER_SITE_ID, "")
            amber_site_list: list[SelectOptionDict] = []
            for site in opt_sites:
                sid = site["id"]
                nmi = site.get("nmi", sid)
                status = site.get("status", "unknown")
                label = f"{nmi} ({'Active' if status == 'active' else status})"
                amber_site_list.append(SelectOptionDict(value=sid, label=label))
            schema_dict[
                vol.Optional(CONF_AMBER_SITE_ID, default=current_site_id or amber_site_list[0]["value"])
            ] = SelectSelector(SelectSelectorConfig(
                options=amber_site_list,
                mode=SelectSelectorMode.DROPDOWN,
            ))

        schema_dict.update({
            vol.Optional(
                CONF_AUTO_SYNC_ENABLED,
                default=self._get_option(CONF_AUTO_SYNC_ENABLED, True),
            ): BooleanSelector(),
            vol.Optional(
                CONF_AMBER_FORECAST_TYPE,
                default=self._get_option(CONF_AMBER_FORECAST_TYPE, "predicted"),
            ): SelectSelector(SelectSelectorConfig(
                options=[
                    SelectOptionDict(value=k, label=v)
                    for k, v in amber_forecast_types.items()
                ],
                mode=SelectSelectorMode.DROPDOWN,
            )),
            vol.Optional(
                CONF_SPIKE_PROTECTION_ENABLED,
                default=self._get_option(CONF_SPIKE_PROTECTION_ENABLED, False),
            ): BooleanSelector(),
            vol.Optional(
                CONF_FORECAST_DISCREPANCY_ALERT,
                default=self._get_option(CONF_FORECAST_DISCREPANCY_ALERT, False),
            ): BooleanSelector(),
            vol.Optional(
                CONF_FORECAST_DISCREPANCY_THRESHOLD,
                default=self._get_option(
                    CONF_FORECAST_DISCREPANCY_THRESHOLD,
                    DEFAULT_FORECAST_DISCREPANCY_THRESHOLD,
                ),
            ): NumberSelector(NumberSelectorConfig(
                min=0.0, max=100.0, step=0.1, unit_of_measurement=self._selector_unit(),
                mode=NumberSelectorMode.BOX,
            )),
        })

        # Only show force mode toggle for Tesla (it's a Tesla-specific feature)
        if is_tesla:
            schema_dict[
                vol.Optional(
                    CONF_FORCE_TARIFF_MODE_TOGGLE,
                    default=self._get_option(CONF_FORCE_TARIFF_MODE_TOGGLE, False),
                )
            ] = BooleanSelector()

        schema_dict.update(
            {
                vol.Optional(
                    CONF_EXPORT_BOOST_ENABLED,
                    default=self._get_option(CONF_EXPORT_BOOST_ENABLED, False),
                ): BooleanSelector(),
                vol.Optional(
                    CONF_EXPORT_PRICE_OFFSET,
                    default=self._get_option(CONF_EXPORT_PRICE_OFFSET, 0.0),
                ): NumberSelector(NumberSelectorConfig(
                    min=0.0, max=50.0, step=0.1, unit_of_measurement=self._selector_unit(),
                    mode=NumberSelectorMode.BOX,
                )),
                vol.Optional(
                    CONF_EXPORT_MIN_PRICE,
                    default=self._get_option(CONF_EXPORT_MIN_PRICE, 0.0),
                ): NumberSelector(NumberSelectorConfig(
                    min=0.0, max=100.0, step=0.1, unit_of_measurement=self._selector_unit(),
                    mode=NumberSelectorMode.BOX,
                )),
                vol.Optional(
                    CONF_EXPORT_BOOST_START,
                    default=self._get_option(
                        CONF_EXPORT_BOOST_START, DEFAULT_EXPORT_BOOST_START
                    ),
                ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
                vol.Optional(
                    CONF_EXPORT_BOOST_END,
                    default=self._get_option(
                        CONF_EXPORT_BOOST_END, DEFAULT_EXPORT_BOOST_END
                    ),
                ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
                vol.Optional(
                    CONF_EXPORT_BOOST_THRESHOLD,
                    default=self._get_option(
                        CONF_EXPORT_BOOST_THRESHOLD, DEFAULT_EXPORT_BOOST_THRESHOLD
                    ),
                ): NumberSelector(NumberSelectorConfig(
                    min=0.0, max=50.0, step=0.1, unit_of_measurement=self._selector_unit(),
                    mode=NumberSelectorMode.BOX,
                )),
                # Chip Mode (inverse of export boost)
                vol.Optional(
                    CONF_CHIP_MODE_ENABLED,
                    default=self._get_option(CONF_CHIP_MODE_ENABLED, False),
                ): BooleanSelector(),
                vol.Optional(
                    CONF_CHIP_MODE_START,
                    default=self._get_option(
                        CONF_CHIP_MODE_START, DEFAULT_CHIP_MODE_START
                    ),
                ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
                vol.Optional(
                    CONF_CHIP_MODE_END,
                    default=self._get_option(CONF_CHIP_MODE_END, DEFAULT_CHIP_MODE_END),
                ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
                vol.Optional(
                    CONF_CHIP_MODE_THRESHOLD,
                    default=self._get_option(
                        CONF_CHIP_MODE_THRESHOLD, DEFAULT_CHIP_MODE_THRESHOLD
                    ),
                ): NumberSelector(NumberSelectorConfig(
                    min=0.0, max=200.0, step=0.1, unit_of_measurement=self._selector_unit(),
                    mode=NumberSelectorMode.BOX,
                )),
            }
        )

        return self.async_show_form(
            step_id="amber_options",
            data_schema=vol.Schema(schema_dict),
            errors=errors,
        )

    async def async_step_demand_charge_options(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Dedicated step for Network Demand Charge configuration."""
        # If a pricing step chains here but we came from the menu,
        # save the pricing data and finish — don't show demand charge form.
        if (
            user_input is None
            and getattr(self, "_from_menu", False)
            and hasattr(self, "_amber_options")
            and self._amber_options
        ):
            return self._save_and_finish(self._amber_options)

        if user_input is not None:
            # Store demand charge options
            self._demand_options = user_input

            # If entered from menu, save this section only and finish
            if getattr(self, "_from_menu", False):
                return self._save_and_finish(self._demand_options)

            # Route to curtailment options
            return await self.async_step_curtailment_options()

        battery_system = self.config_entry.data.get(
            CONF_BATTERY_SYSTEM, BATTERY_SYSTEM_TESLA
        )

        demand_days_options = [
            SelectOptionDict(value="All Days", label="All Days"),
            SelectOptionDict(value="Weekdays Only", label="Weekdays Only"),
            SelectOptionDict(value="Weekends Only", label="Weekends Only"),
        ]
        demand_apply_options = [
            SelectOptionDict(value="Buy Only", label="Buy Only"),
            SelectOptionDict(value="Sell Only", label="Sell Only"),
            SelectOptionDict(value="Both", label="Both"),
        ]

        schema_dict = {
            vol.Optional(
                CONF_DEMAND_CHARGE_ENABLED,
                default=self._get_option(CONF_DEMAND_CHARGE_ENABLED, False),
            ): BooleanSelector(),
            vol.Optional(
                CONF_DEMAND_CHARGE_RATE,
                default=self._get_option(CONF_DEMAND_CHARGE_RATE, 10.0),
            ): NumberSelector(NumberSelectorConfig(
                min=0.0, max=100.0, step=0.1, unit_of_measurement=self._selector_unit("demand_rate"),
                mode=NumberSelectorMode.BOX,
            )),
            vol.Optional(
                CONF_DEMAND_CHARGE_START_TIME,
                default=self._get_option(CONF_DEMAND_CHARGE_START_TIME, "14:00"),
            ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
            vol.Optional(
                CONF_DEMAND_CHARGE_END_TIME,
                default=self._get_option(CONF_DEMAND_CHARGE_END_TIME, "20:00"),
            ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
            vol.Optional(
                CONF_DEMAND_CHARGE_DAYS,
                default=self._get_option(CONF_DEMAND_CHARGE_DAYS, "All Days"),
            ): SelectSelector(SelectSelectorConfig(
                options=demand_days_options,
                mode=SelectSelectorMode.DROPDOWN,
            )),
            vol.Optional(
                CONF_DEMAND_CHARGE_BILLING_DAY,
                default=self._get_option(CONF_DEMAND_CHARGE_BILLING_DAY, 1),
            ): NumberSelector(NumberSelectorConfig(
                min=1, max=31, step=1, mode=NumberSelectorMode.BOX,
            )),
            vol.Optional(
                CONF_DEMAND_CHARGE_APPLY_TO,
                default=self._get_option(CONF_DEMAND_CHARGE_APPLY_TO, "Buy Only"),
            ): SelectSelector(SelectSelectorConfig(
                options=demand_apply_options,
                mode=SelectSelectorMode.DROPDOWN,
            )),
        }

        # Only show artificial price increase for Tesla (Tesla-specific TOU feature)
        if battery_system == BATTERY_SYSTEM_TESLA:
            schema_dict[
                vol.Optional(
                    CONF_DEMAND_ARTIFICIAL_PRICE,
                    default=self._get_option(CONF_DEMAND_ARTIFICIAL_PRICE, False),
                )
            ] = BooleanSelector()

        schema_dict.update(
            {
                vol.Optional(
                    CONF_DEMAND_ALLOW_GRID_CHARGING,
                    default=self._get_option(CONF_DEMAND_ALLOW_GRID_CHARGING, False),
                ): BooleanSelector(),
                vol.Optional(
                    CONF_DAILY_SUPPLY_CHARGE,
                    default=self._get_option(CONF_DAILY_SUPPLY_CHARGE, 0.0),
                ): NumberSelector(NumberSelectorConfig(
                    min=0.0, max=500.0, step=0.01, unit_of_measurement=self._selector_unit("daily"),
                    mode=NumberSelectorMode.BOX,
                )),
                vol.Optional(
                    CONF_MONTHLY_SUPPLY_CHARGE,
                    default=self._get_option(CONF_MONTHLY_SUPPLY_CHARGE, 0.0),
                ): NumberSelector(NumberSelectorConfig(
                    min=0.0, max=500.0, step=0.01, unit_of_measurement=self._selector_unit("monthly"),
                    mode=NumberSelectorMode.BOX,
                )),
            }
        )

        return self.async_show_form(
            step_id="demand_charge_options",
            data_schema=vol.Schema(schema_dict),
        )

    async def async_step_curtailment_options(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Dedicated step for Solar Curtailment configuration."""
        battery_system = self._get_option(
            CONF_BATTERY_SYSTEM, BATTERY_SYSTEM_TESLA
        )
        is_sigenergy = battery_system == BATTERY_SYSTEM_SIGENERGY
        is_sungrow = battery_system == BATTERY_SYSTEM_SUNGROW
        is_tesla = battery_system == BATTERY_SYSTEM_TESLA

        if user_input is not None:
            # Check if solar curtailment is being disabled
            was_curtailment_enabled = self._get_option(
                CONF_BATTERY_CURTAILMENT_ENABLED, False
            )
            new_curtailment_enabled = user_input.get(
                CONF_BATTERY_CURTAILMENT_ENABLED, False
            )

            if was_curtailment_enabled and not new_curtailment_enabled:
                await self._restore_export_rule()

            # Store curtailment settings (no weather options here)
            self._curtailment_options = {
                CONF_BATTERY_CURTAILMENT_ENABLED: new_curtailment_enabled,
            }

            # If entered from menu, save curtailment settings and finish
            if getattr(self, "_from_menu", False):
                if is_sigenergy:
                    dc_enabled = user_input.get(
                        CONF_SIGENERGY_DC_CURTAILMENT_ENABLED, False
                    )
                    new_data = dict(self.config_entry.data)
                    new_data[CONF_SIGENERGY_DC_CURTAILMENT_ENABLED] = dc_enabled
                    self.hass.config_entries.async_update_entry(
                        self.config_entry, data=new_data
                    )
                ac_enabled = user_input.get(
                    CONF_AC_INVERTER_CURTAILMENT_ENABLED, False
                )
                self._curtailment_options[CONF_AC_INVERTER_CURTAILMENT_ENABLED] = (
                    ac_enabled
                )
                if is_tesla:
                    self._curtailment_options[CONF_POWERWALL_OFFGRID_AS_CURTAILMENT] = (
                        user_input.get(CONF_POWERWALL_OFFGRID_AS_CURTAILMENT, False)
                    )
                else:
                    self._curtailment_options[CONF_POWERWALL_OFFGRID_AS_CURTAILMENT] = False
                if ac_enabled:
                    return await self.async_step_inverter_brand()
                return self._save_and_finish(self._curtailment_options)

            if is_sigenergy:
                # Sigenergy DC curtailment - save DC settings to config entry data
                dc_enabled = user_input.get(
                    CONF_SIGENERGY_DC_CURTAILMENT_ENABLED, False
                )
                new_data = dict(self.config_entry.data)
                new_data[CONF_SIGENERGY_DC_CURTAILMENT_ENABLED] = dc_enabled
                self.hass.config_entries.async_update_entry(
                    self.config_entry, data=new_data
                )
                # Check if AC inverter curtailment needs configuration
                ac_enabled = user_input.get(
                    CONF_AC_INVERTER_CURTAILMENT_ENABLED, False
                )
                self._curtailment_options[CONF_AC_INVERTER_CURTAILMENT_ENABLED] = (
                    ac_enabled
                )
                if ac_enabled:
                    return await self.async_step_inverter_brand()
                return await self.async_step_weather_options()
            elif is_sungrow:
                # Sungrow battery systems can still have a separate SG-series
                # solar-only inverter that needs its own AC inverter polling path.
                ac_enabled = user_input.get(
                    CONF_AC_INVERTER_CURTAILMENT_ENABLED, False
                )
                self._curtailment_options[CONF_AC_INVERTER_CURTAILMENT_ENABLED] = (
                    ac_enabled
                )
                if ac_enabled:
                    return await self.async_step_inverter_brand()
                return await self.async_step_weather_options()
            elif is_tesla:
                # Tesla - check if AC inverter curtailment needs configuration
                ac_enabled = user_input.get(
                    CONF_AC_INVERTER_CURTAILMENT_ENABLED, False
                )
                self._curtailment_options[CONF_AC_INVERTER_CURTAILMENT_ENABLED] = (
                    ac_enabled
                )
                self._curtailment_options[CONF_POWERWALL_OFFGRID_AS_CURTAILMENT] = (
                    user_input.get(CONF_POWERWALL_OFFGRID_AS_CURTAILMENT, False)
                )

                if ac_enabled:
                    # Route to AC inverter brand selection
                    return await self.async_step_inverter_brand()

                # No AC inverter - route to weather options
                return await self.async_step_weather_options()
            else:
                ac_enabled = user_input.get(
                    CONF_AC_INVERTER_CURTAILMENT_ENABLED, False
                )
                self._curtailment_options[CONF_AC_INVERTER_CURTAILMENT_ENABLED] = (
                    ac_enabled
                )
                self._curtailment_options[CONF_POWERWALL_OFFGRID_AS_CURTAILMENT] = False
                if ac_enabled:
                    return await self.async_step_inverter_brand()
                return await self.async_step_weather_options()

        # Build schema based on battery system
        schema_dict: dict[vol.Marker, Any] = {
            vol.Optional(
                CONF_BATTERY_CURTAILMENT_ENABLED,
                default=self._get_option(CONF_BATTERY_CURTAILMENT_ENABLED, False),
            ): BooleanSelector(),
            vol.Optional(
                CONF_AC_INVERTER_CURTAILMENT_ENABLED,
                default=self._get_option(
                    CONF_AC_INVERTER_CURTAILMENT_ENABLED, False
                ),
            ): BooleanSelector(),
        }

        if is_sigenergy:
            # Sigenergy DC curtailment option
            schema_dict[
                vol.Optional(
                    CONF_SIGENERGY_DC_CURTAILMENT_ENABLED,
                    default=self.config_entry.data.get(
                        CONF_SIGENERGY_DC_CURTAILMENT_ENABLED, False
                    ),
                )
            ] = BooleanSelector()
        if is_tesla:
            # Tesla Powerwall off-grid fallback option
            schema_dict[
                vol.Optional(
                    CONF_POWERWALL_OFFGRID_AS_CURTAILMENT,
                    default=self._get_option(
                        CONF_POWERWALL_OFFGRID_AS_CURTAILMENT, False
                    ),
                )
            ] = BooleanSelector()

        return self.async_show_form(
            step_id="curtailment_options",
            data_schema=vol.Schema(schema_dict),
        )

    async def async_step_weather_options(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Weather and solar forecast configuration in options flow."""
        if user_input is not None:
            solar_forecast_provider = user_input.get(
                CONF_SOLAR_FORECAST_PROVIDER,
                DEFAULT_SOLAR_FORECAST_PROVIDER,
            )
            if solar_forecast_provider not in SOLAR_FORECAST_PROVIDERS:
                solar_forecast_provider = DEFAULT_SOLAR_FORECAST_PROVIDER

            # Store weather and Solcast settings
            weather_options = {
                CONF_WEATHER_LOCATION: user_input.get(CONF_WEATHER_LOCATION, ""),
                CONF_OPENWEATHERMAP_API_KEY: user_input.get(
                    CONF_OPENWEATHERMAP_API_KEY, ""
                ),
                CONF_WEATHER_ENTITY: _normalize_optional_entity(
                    user_input.get(CONF_WEATHER_ENTITY)
                ),
                CONF_SOLAR_FORECAST_PROVIDER: solar_forecast_provider,
                CONF_SOLCAST_ENABLED: user_input.get(CONF_SOLCAST_ENABLED, False),
                CONF_SOLCAST_API_KEY: (
                    user_input.get(CONF_SOLCAST_API_KEY) or ""
                ).strip(),
                CONF_SOLCAST_RESOURCE_ID: (
                    user_input.get(CONF_SOLCAST_RESOURCE_ID) or ""
                ).strip(),
                CONF_SOLCAST_ESTIMATE_TYPE: user_input.get(
                    CONF_SOLCAST_ESTIMATE_TYPE, DEFAULT_SOLCAST_ESTIMATE_TYPE
                ),
            }
            self._remove_legacy_data_keys(
                (
                    CONF_SOLCAST_ENABLED,
                    CONF_SOLCAST_API_KEY,
                    CONF_SOLCAST_RESOURCE_ID,
                    CONF_SOLCAST_ESTIMATE_TYPE,
                    CONF_SOLAR_FORECAST_PROVIDER,
                )
            )

            # If entered from menu, save weather settings only and finish
            if getattr(self, "_from_menu", False):
                return self._save_and_finish(weather_options)

            # Combine with previous options - check if came from inverter_config or curtailment_options
            if hasattr(self, "_inverter_options") and self._inverter_options:
                # Came from inverter_config - _inverter_options already has everything except weather
                final_data = {
                    **self._inverter_options,
                    **getattr(self, "_demand_options", {}),
                    **weather_options,
                }
            else:
                # Came directly from curtailment_options
                final_data = {
                    **getattr(self, "_amber_options", {}),
                    **getattr(self, "_demand_options", {}),
                    **getattr(self, "_curtailment_options", {}),
                    **weather_options,
                }

            self._final_options = final_data
            return await self.async_step_ev_charging()

        schema_dict: dict[vol.Marker, Any] = {
            vol.Optional(
                CONF_WEATHER_LOCATION,
                default=self._get_option(CONF_WEATHER_LOCATION, ""),
            ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
            vol.Optional(
                CONF_OPENWEATHERMAP_API_KEY,
                default=self._get_option(CONF_OPENWEATHERMAP_API_KEY, ""),
            ): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
        }
        self._add_weather_entity_selector(schema_dict)
        schema_dict.update(
            {
                vol.Optional(
                    CONF_SOLAR_FORECAST_PROVIDER,
                    default=self._get_option(
                        CONF_SOLAR_FORECAST_PROVIDER,
                        DEFAULT_SOLAR_FORECAST_PROVIDER,
                    ),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=value, label=label)
                            for value, label in SOLAR_FORECAST_PROVIDERS.items()
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(
                    CONF_SOLCAST_ENABLED,
                    default=self._get_option(CONF_SOLCAST_ENABLED, False),
                ): BooleanSelector(),
                vol.Optional(
                    CONF_SOLCAST_API_KEY,
                    default=self._get_option(CONF_SOLCAST_API_KEY, ""),
                ): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
                vol.Optional(
                    CONF_SOLCAST_RESOURCE_ID,
                    default=self._get_option(CONF_SOLCAST_RESOURCE_ID, ""),
                ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
                vol.Optional(
                    CONF_SOLCAST_ESTIMATE_TYPE,
                    default=self._get_option(
                        CONF_SOLCAST_ESTIMATE_TYPE, DEFAULT_SOLCAST_ESTIMATE_TYPE
                    ),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=value, label=label)
                            for value, label in SOLCAST_ESTIMATE_TYPES.items()
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="weather_options",
            data_schema=vol.Schema(schema_dict),
        )

    async def async_step_inverter_brand(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step for selecting inverter brand for AC-coupled curtailment."""
        if user_input is not None:
            # Store selected brand and proceed to brand-specific config
            self._inverter_brand = user_input.get(CONF_INVERTER_BRAND, "sungrow")
            return await self.async_step_inverter_config()

        # Get current brand from existing config
        current_brand = self._get_option(CONF_INVERTER_BRAND, "sungrow")

        return self.async_show_form(
            step_id="inverter_brand",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_INVERTER_BRAND,
                        default=current_brand,
                    ): SelectSelector(SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=k, label=v)
                            for k, v in INVERTER_BRANDS.items()
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )),
                }
            ),
        )

    async def async_step_inverter_config(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step for configuring inverter-specific settings."""
        errors = {}

        if user_input is not None:
            # Get brand and configuration values
            inverter_brand = getattr(self, "_inverter_brand", None) or self._get_option(
                CONF_INVERTER_BRAND, "sungrow"
            )
            inverter_host = user_input.get(CONF_INVERTER_HOST, "")
            inverter_port = user_input.get(CONF_INVERTER_PORT, DEFAULT_INVERTER_PORT)
            inverter_slave_id = user_input.get(
                CONF_INVERTER_SLAVE_ID, DEFAULT_INVERTER_SLAVE_ID
            )
            inverter_model = user_input.get(CONF_INVERTER_MODEL)
            inverter_rated_power_w = user_input.get(CONF_INVERTER_RATED_POWER_W)

            # Validate: if battery is Sungrow and AC inverter is also Sungrow,
            # check for IP/port/slave_id conflicts
            battery_system = self.config_entry.data.get(
                CONF_BATTERY_SYSTEM, BATTERY_SYSTEM_TESLA
            )
            if battery_system == BATTERY_SYSTEM_SUNGROW and inverter_brand == "sungrow":
                sungrow_host = self.config_entry.data.get(CONF_SUNGROW_HOST, "")
                sungrow_port = self.config_entry.data.get(
                    CONF_SUNGROW_PORT, DEFAULT_SUNGROW_PORT
                )
                sungrow_slave_id = self.config_entry.data.get(
                    CONF_SUNGROW_SLAVE_ID, DEFAULT_SUNGROW_SLAVE_ID
                )

                # AC inverter curtailment uses its own polling/controller path.
                # Pointing it at the same Sungrow hybrid endpoint as the battery
                # coordinator creates a second Modbus client against the SH.
                if (
                    inverter_host == sungrow_host
                    and inverter_port == sungrow_port
                    and inverter_slave_id == sungrow_slave_id
                ):
                    errors["base"] = "sungrow_modbus_conflict"

            if not errors:
                # Combine amber options, curtailment options, and inverter config
                final_data = {**getattr(self, "_amber_options", {})}
                final_data.update(getattr(self, "_curtailment_options", {}))
                if getattr(self, "_from_menu", False):
                    # Opening the AC inverter menu means the user is enabling
                    # the separate inverter polling/curtailment path.
                    final_data[CONF_AC_INVERTER_CURTAILMENT_ENABLED] = True
                final_data[CONF_INVERTER_BRAND] = inverter_brand
                final_data[CONF_INVERTER_MODEL] = inverter_model
                final_data[CONF_INVERTER_HOST] = inverter_host
                final_data[CONF_INVERTER_PORT] = inverter_port
                if inverter_rated_power_w is not None:
                    final_data[CONF_INVERTER_RATED_POWER_W] = float(
                        inverter_rated_power_w
                    )

                # Only include slave ID for Modbus brands (not Enphase/Zeversolar which use HTTP)
                if inverter_brand not in ("enphase", "zeversolar"):
                    final_data[CONF_INVERTER_SLAVE_ID] = inverter_slave_id
                else:
                    final_data[CONF_INVERTER_SLAVE_ID] = (
                        1  # Default for HTTP-based inverters
                    )

                # Include JWT token, Enlighten credentials, and grid profiles for Enphase (firmware 7.x+)
                if inverter_brand == "enphase":
                    final_data[CONF_INVERTER_TOKEN] = user_input.get(
                        CONF_INVERTER_TOKEN, ""
                    )
                    final_data[CONF_ENPHASE_USERNAME] = user_input.get(
                        CONF_ENPHASE_USERNAME, ""
                    )
                    final_data[CONF_ENPHASE_PASSWORD] = user_input.get(
                        CONF_ENPHASE_PASSWORD, ""
                    )
                    final_data[CONF_ENPHASE_SERIAL] = user_input.get(
                        CONF_ENPHASE_SERIAL, ""
                    )
                    # Grid profile names for profile switching fallback
                    final_data[CONF_ENPHASE_NORMAL_PROFILE] = user_input.get(
                        CONF_ENPHASE_NORMAL_PROFILE, ""
                    )
                    final_data[CONF_ENPHASE_ZERO_EXPORT_PROFILE] = user_input.get(
                        CONF_ENPHASE_ZERO_EXPORT_PROFILE, ""
                    )
                    # Installer mode for grid profile access
                    final_data[CONF_ENPHASE_IS_INSTALLER] = user_input.get(
                        CONF_ENPHASE_IS_INSTALLER, False
                    )

                # Fronius-specific: load following mode (for users without 0W export profile)
                if inverter_brand == "fronius":
                    final_data[CONF_FRONIUS_LOAD_FOLLOWING] = user_input.get(
                        CONF_FRONIUS_LOAD_FOLLOWING, False
                    )

                # Restore SOC threshold for AC inverter curtailment
                final_data[CONF_INVERTER_RESTORE_SOC] = user_input.get(
                    CONF_INVERTER_RESTORE_SOC, DEFAULT_INVERTER_RESTORE_SOC
                )

                # Store inverter config
                self._inverter_options = final_data

                # If entered from menu, save inverter settings only and finish
                if getattr(self, "_from_menu", False):
                    return self._save_and_finish(final_data)

                return await self.async_step_weather_options()

        # Get brand-specific models and defaults
        # Fall back to existing config if _inverter_brand not set (options flow)
        brand = getattr(self, "_inverter_brand", None) or self._get_option(
            CONF_INVERTER_BRAND, "sungrow"
        )
        # Pass battery system to filter out conflicting models (e.g., SH-series when battery is Sungrow)
        battery_system = self.config_entry.data.get(
            CONF_BATTERY_SYSTEM, BATTERY_SYSTEM_TESLA
        )
        models = get_models_for_brand(brand, battery_system)
        defaults = get_brand_defaults(brand)

        # Get current values from existing config (for editing)
        current_model = self._get_option(CONF_INVERTER_MODEL)
        # If current model doesn't belong to selected brand, use first model from brand
        if current_model not in models:
            current_model = next(iter(models.keys())) if models else ""

        current_host = self._get_option(CONF_INVERTER_HOST, "")
        current_port = self._get_option(CONF_INVERTER_PORT, defaults["port"])
        current_slave_id = self._get_option(
            CONF_INVERTER_SLAVE_ID, defaults["slave_id"]
        )

        # Build brand-specific schema
        schema_dict: dict[vol.Marker, Any] = {
            vol.Required(
                CONF_INVERTER_MODEL,
                default=current_model,
            ): SelectSelector(SelectSelectorConfig(
                options=[
                    SelectOptionDict(value=k, label=v)
                    for k, v in models.items()
                ],
                mode=SelectSelectorMode.DROPDOWN,
            )),
            vol.Required(
                CONF_INVERTER_HOST,
                default=current_host,
            ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
            vol.Required(
                CONF_INVERTER_PORT,
                default=current_port,
            ): NumberSelector(NumberSelectorConfig(
                min=1, max=65535, step=1, mode=NumberSelectorMode.BOX,
            )),
        }

        # Only show Slave ID for Modbus brands (not Enphase/Zeversolar which use HTTP)
        if brand not in ("enphase", "zeversolar"):
            schema_dict[
                vol.Required(
                    CONF_INVERTER_SLAVE_ID,
                    default=current_slave_id,
                )
            ] = NumberSelector(NumberSelectorConfig(
                min=1, max=247, step=1, mode=NumberSelectorMode.BOX,
            ))

        # Show JWT token and Enlighten credentials for Enphase (firmware 7.x+)
        if brand == "enphase":
            current_token = self._get_option(CONF_INVERTER_TOKEN, "")
            schema_dict[
                vol.Optional(
                    CONF_INVERTER_TOKEN,
                    default=current_token,
                )
            ] = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))

            # Enlighten credentials for automatic JWT token refresh (recommended)
            current_enphase_username = self._get_option(CONF_ENPHASE_USERNAME, "")
            schema_dict[
                vol.Optional(
                    CONF_ENPHASE_USERNAME,
                    default=current_enphase_username,
                )
            ] = TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT))

            current_enphase_password = self._get_option(CONF_ENPHASE_PASSWORD, "")
            schema_dict[
                vol.Optional(
                    CONF_ENPHASE_PASSWORD,
                    default=current_enphase_password,
                )
            ] = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))

            current_enphase_serial = self._get_option(CONF_ENPHASE_SERIAL, "")
            schema_dict[
                vol.Optional(
                    CONF_ENPHASE_SERIAL,
                    default=current_enphase_serial,
                )
            ] = TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT))

            # Grid profile names for profile switching fallback (when DPEL/DER unavailable)
            current_normal_profile = self._get_option(CONF_ENPHASE_NORMAL_PROFILE, "")
            schema_dict[
                vol.Optional(
                    CONF_ENPHASE_NORMAL_PROFILE,
                    default=current_normal_profile,
                )
            ] = TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT))

            current_zero_export_profile = self._get_option(
                CONF_ENPHASE_ZERO_EXPORT_PROFILE, ""
            )
            schema_dict[
                vol.Optional(
                    CONF_ENPHASE_ZERO_EXPORT_PROFILE,
                    default=current_zero_export_profile,
                )
            ] = TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT))

            # Installer mode for grid profile access
            current_is_installer = self._get_option(CONF_ENPHASE_IS_INSTALLER, False)
            schema_dict[
                vol.Optional(
                    CONF_ENPHASE_IS_INSTALLER,
                    default=current_is_installer,
                )
            ] = BooleanSelector()

        # Fronius-specific: load following mode (for users without 0W export profile)
        if brand == "fronius":
            current_load_following = self._get_option(
                CONF_FRONIUS_LOAD_FOLLOWING, False
            )
            schema_dict[
                vol.Optional(
                    CONF_FRONIUS_LOAD_FOLLOWING,
                    default=current_load_following,
                    description={"suggested_value": current_load_following},
                )
            ] = BooleanSelector()

        if brand == "solaredge":
            current_rated_power_w = self._get_option(
                CONF_INVERTER_RATED_POWER_W,
                DEFAULT_SOLAREDGE_RATED_POWER_W,
            )
            schema_dict[
                vol.Required(
                    CONF_INVERTER_RATED_POWER_W,
                    default=current_rated_power_w,
                )
            ] = NumberSelector(NumberSelectorConfig(
                min=1, max=100000, step=1, unit_of_measurement="W",
                mode=NumberSelectorMode.BOX,
            ))

        # Restore SOC threshold - restore inverter when battery drops below this %
        current_restore_soc = self._get_option(
            CONF_INVERTER_RESTORE_SOC, DEFAULT_INVERTER_RESTORE_SOC
        )
        schema_dict[
            vol.Optional(
                CONF_INVERTER_RESTORE_SOC,
                default=current_restore_soc,
            )
        ] = NumberSelector(NumberSelectorConfig(
            min=50, max=100, step=1, unit_of_measurement="%",
            mode=NumberSelectorMode.SLIDER,
        ))

        return self.async_show_form(
            step_id="inverter_config",
            data_schema=vol.Schema(schema_dict),
            errors=errors,
            description_placeholders={
                "brand": INVERTER_BRANDS.get(brand, brand),
            },
        )

    async def async_step_ev_charging(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step for EV Charging and OCPP configuration."""
        if user_input is not None:
            # Store EV data for potential zaptec_cloud step
            self._ev_options_data = dict(user_input)

            # If Zaptec standalone is being newly enabled, go to credentials step
            was_standalone = self._get_option(CONF_ZAPTEC_STANDALONE_ENABLED, False)
            now_standalone = user_input.get(CONF_ZAPTEC_STANDALONE_ENABLED, False)
            has_credentials = bool(self._get_option(CONF_ZAPTEC_USERNAME, ""))

            if now_standalone and (not was_standalone or not has_credentials):
                return await self.async_step_zaptec_cloud_options()

            return self._save_ev_options(user_input)

        # Build schema for EV and OCPP options
        current_ev_enabled = self._get_option(CONF_EV_CHARGING_ENABLED, False)
        current_ev_provider = self._get_option(CONF_EV_PROVIDER, EV_PROVIDER_FLEET_API)
        current_generic_capacity = self._get_option(
            CONF_GENERIC_CHARGER_BATTERY_CAPACITY_KWH, None
        )

        schema_dict: dict[vol.Marker, Any] = {
            # EV Charging settings
            vol.Optional(
                CONF_EV_CHARGING_ENABLED,
                default=current_ev_enabled,
            ): BooleanSelector(),
            vol.Optional(
                CONF_EV_PROVIDER,
                default=current_ev_provider,
            ): SelectSelector(SelectSelectorConfig(
                options=[
                    SelectOptionDict(value=k, label=v)
                    for k, v in EV_PROVIDERS.items()
                ],
                mode=SelectSelectorMode.DROPDOWN,
            )),
            vol.Optional(
                CONF_TESLA_BLE_ENTITY_PREFIX,
                default=self._get_option(
                    CONF_TESLA_BLE_ENTITY_PREFIX, DEFAULT_TESLA_BLE_ENTITY_PREFIX
                ),
            ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
            # OCPP settings
            vol.Optional(
                CONF_OCPP_ENABLED,
                default=self._get_option(CONF_OCPP_ENABLED, False),
            ): BooleanSelector(),
            vol.Optional(
                CONF_OCPP_PORT,
                default=self._get_option(CONF_OCPP_PORT, DEFAULT_OCPP_PORT),
            ): NumberSelector(NumberSelectorConfig(
                min=1, max=65535, step=1, mode=NumberSelectorMode.BOX,
            )),
            # Zaptec settings
            vol.Optional(
                CONF_ZAPTEC_CHARGER_ENTITY,
                default=self._get_option(CONF_ZAPTEC_CHARGER_ENTITY, ""),
            ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
            vol.Optional(
                CONF_ZAPTEC_INSTALLATION_ID,
                default=self._get_option(CONF_ZAPTEC_INSTALLATION_ID, ""),
            ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
            # Zaptec standalone (direct API)
            vol.Optional(
                CONF_ZAPTEC_STANDALONE_ENABLED,
                default=self._get_option(CONF_ZAPTEC_STANDALONE_ENABLED, False),
            ): BooleanSelector(),
            # Generic charger
            vol.Optional(
                CONF_GENERIC_CHARGER_ENABLED,
                default=self._get_option(CONF_GENERIC_CHARGER_ENABLED, False),
            ): BooleanSelector(),
            vol.Optional(
                CONF_GENERIC_CHARGER_SWITCH_ENTITY,
                default=self._get_option(CONF_GENERIC_CHARGER_SWITCH_ENTITY, ""),
            ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
            vol.Optional(
                CONF_GENERIC_CHARGER_AMPS_ENTITY,
                default=self._get_option(CONF_GENERIC_CHARGER_AMPS_ENTITY, ""),
            ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
            vol.Optional(
                CONF_GENERIC_CHARGER_STATUS_ENTITY,
                default=self._get_option(CONF_GENERIC_CHARGER_STATUS_ENTITY, ""),
            ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
            vol.Optional(
                CONF_GENERIC_CHARGER_POWER_ENTITY,
                default=self._get_option(CONF_GENERIC_CHARGER_POWER_ENTITY, ""),
            ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
            vol.Optional(
                CONF_GENERIC_CHARGER_SOC_ENTITY,
                default=self._get_option(CONF_GENERIC_CHARGER_SOC_ENTITY, ""),
            ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
            vol.Optional(
                CONF_GENERIC_CHARGER_SOC_ENTITY_2,
                default=self._get_option(CONF_GENERIC_CHARGER_SOC_ENTITY_2, ""),
            ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
            vol.Optional(
                CONF_GENERIC_CHARGER_BATTERY_CAPACITY_KWH,
                description=(
                    {"suggested_value": current_generic_capacity}
                    if current_generic_capacity is not None
                    else None
                ),
            ): NumberSelector(NumberSelectorConfig(
                min=1.0,
                max=250.0,
                step=0.1,
                mode=NumberSelectorMode.BOX,
                unit_of_measurement="kWh",
            )),
            # Sigenergy EVAC/EVDC charger direct Modbus control
            vol.Optional(
                CONF_SIGENERGY_CHARGER_ENABLED,
                default=self._get_option(CONF_SIGENERGY_CHARGER_ENABLED, False),
            ): BooleanSelector(),
            vol.Optional(
                CONF_SIGENERGY_CHARGER_TYPE,
                default=self._get_option(CONF_SIGENERGY_CHARGER_TYPE, SIGENERGY_CHARGER_EVAC),
            ): SelectSelector(SelectSelectorConfig(
                options=[
                    SelectOptionDict(value=k, label=v)
                    for k, v in SIGENERGY_CHARGER_TYPES.items()
                ],
                mode=SelectSelectorMode.DROPDOWN,
            )),
            vol.Optional(
                CONF_SIGENERGY_CHARGER_HOST,
                default=self._get_option(
                    CONF_SIGENERGY_CHARGER_HOST,
                    self._get_option(CONF_SIGENERGY_MODBUS_HOST, ""),
                ),
            ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
            vol.Optional(
                CONF_SIGENERGY_CHARGER_PORT,
                default=self._get_option(
                    CONF_SIGENERGY_CHARGER_PORT,
                    DEFAULT_SIGENERGY_CHARGER_PORT,
                ),
            ): NumberSelector(NumberSelectorConfig(
                min=1, max=65535, step=1, mode=NumberSelectorMode.BOX,
            )),
            vol.Optional(
                CONF_SIGENERGY_CHARGER_SLAVE_ID,
                default=self._get_option(
                    CONF_SIGENERGY_CHARGER_SLAVE_ID,
                    DEFAULT_SIGENERGY_CHARGER_SLAVE_ID,
                ),
            ): NumberSelector(NumberSelectorConfig(
                min=1, max=247, step=1, mode=NumberSelectorMode.BOX,
            )),
            vol.Optional(
                CONF_SIGENERGY_CHARGER_CHARGE_POWER_LIMIT_ENTITY,
                default=self._get_option(
                    CONF_SIGENERGY_CHARGER_CHARGE_POWER_LIMIT_ENTITY,
                    "",
                ),
            ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
            vol.Optional(
                CONF_SIGENERGY_CHARGER_DISCHARGE_POWER_LIMIT_ENTITY,
                default=self._get_option(
                    CONF_SIGENERGY_CHARGER_DISCHARGE_POWER_LIMIT_ENTITY,
                    "",
                ),
            ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
        }

        return self.async_show_form(
            step_id="ev_charging",
            data_schema=vol.Schema(schema_dict),
        )

    def _save_ev_options(self, ev_input: dict[str, Any]) -> FlowResult:
        """Save EV charging options and create entry."""
        # Start with existing options to preserve any settings not in this flow
        final_data = dict(self.config_entry.options)

        # Update with options collected from earlier steps in this flow
        flow_options = getattr(self, "_final_options", {})
        final_data.update(flow_options)

        # Add EV settings
        final_data[CONF_EV_CHARGING_ENABLED] = ev_input.get(
            CONF_EV_CHARGING_ENABLED, False
        )
        final_data[CONF_EV_PROVIDER] = ev_input.get(
            CONF_EV_PROVIDER, EV_PROVIDER_FLEET_API
        )
        final_data[CONF_TESLA_BLE_ENTITY_PREFIX] = ev_input.get(
            CONF_TESLA_BLE_ENTITY_PREFIX, DEFAULT_TESLA_BLE_ENTITY_PREFIX
        )

        # Add OCPP settings
        final_data[CONF_OCPP_ENABLED] = ev_input.get(CONF_OCPP_ENABLED, False)
        final_data[CONF_OCPP_PORT] = ev_input.get(CONF_OCPP_PORT, DEFAULT_OCPP_PORT)

        # Add Zaptec settings
        zaptec_entity = ev_input.get(CONF_ZAPTEC_CHARGER_ENTITY, "")
        if zaptec_entity:
            final_data[CONF_ZAPTEC_CHARGER_ENTITY] = zaptec_entity
        final_data[CONF_ZAPTEC_INSTALLATION_ID] = ev_input.get(
            CONF_ZAPTEC_INSTALLATION_ID, ""
        )

        # Add Zaptec standalone settings
        final_data[CONF_ZAPTEC_STANDALONE_ENABLED] = ev_input.get(
            CONF_ZAPTEC_STANDALONE_ENABLED, False
        )
        for key in (
            CONF_ZAPTEC_USERNAME,
            CONF_ZAPTEC_PASSWORD,
            CONF_ZAPTEC_CHARGER_ID,
            CONF_ZAPTEC_INSTALLATION_ID_CLOUD,
        ):
            if key in ev_input:
                final_data[key] = ev_input[key]

        # Add Generic charger settings
        final_data[CONF_GENERIC_CHARGER_ENABLED] = ev_input.get(
            CONF_GENERIC_CHARGER_ENABLED, False
        )
        final_data[CONF_GENERIC_CHARGER_SWITCH_ENTITY] = ev_input.get(
            CONF_GENERIC_CHARGER_SWITCH_ENTITY, ""
        ).strip()
        final_data[CONF_GENERIC_CHARGER_AMPS_ENTITY] = ev_input.get(
            CONF_GENERIC_CHARGER_AMPS_ENTITY, ""
        ).strip()
        final_data[CONF_GENERIC_CHARGER_STATUS_ENTITY] = ev_input.get(
            CONF_GENERIC_CHARGER_STATUS_ENTITY, ""
        ).strip()
        final_data[CONF_GENERIC_CHARGER_POWER_ENTITY] = ev_input.get(
            CONF_GENERIC_CHARGER_POWER_ENTITY, ""
        ).strip()
        final_data[CONF_GENERIC_CHARGER_SOC_ENTITY] = ev_input.get(
            CONF_GENERIC_CHARGER_SOC_ENTITY, ""
        ).strip()
        final_data[CONF_GENERIC_CHARGER_SOC_ENTITY_2] = ev_input.get(
            CONF_GENERIC_CHARGER_SOC_ENTITY_2, ""
        ).strip()
        generic_capacity = ev_input.get(CONF_GENERIC_CHARGER_BATTERY_CAPACITY_KWH)
        if generic_capacity is None:
            final_data.pop(CONF_GENERIC_CHARGER_BATTERY_CAPACITY_KWH, None)
        else:
            final_data[CONF_GENERIC_CHARGER_BATTERY_CAPACITY_KWH] = float(
                generic_capacity
            )

        # Add Sigenergy EV charger settings
        final_data[CONF_SIGENERGY_CHARGER_ENABLED] = ev_input.get(
            CONF_SIGENERGY_CHARGER_ENABLED, False
        )
        final_data[CONF_SIGENERGY_CHARGER_TYPE] = ev_input.get(
            CONF_SIGENERGY_CHARGER_TYPE, SIGENERGY_CHARGER_EVAC
        )
        sigenergy_charger_host = ev_input.get(CONF_SIGENERGY_CHARGER_HOST, "").strip()
        if sigenergy_charger_host:
            final_data[CONF_SIGENERGY_CHARGER_HOST] = sigenergy_charger_host
        final_data[CONF_SIGENERGY_CHARGER_PORT] = ev_input.get(
            CONF_SIGENERGY_CHARGER_PORT, DEFAULT_SIGENERGY_CHARGER_PORT
        )
        final_data[CONF_SIGENERGY_CHARGER_SLAVE_ID] = ev_input.get(
            CONF_SIGENERGY_CHARGER_SLAVE_ID, DEFAULT_SIGENERGY_CHARGER_SLAVE_ID
        )
        final_data[CONF_SIGENERGY_CHARGER_CHARGE_POWER_LIMIT_ENTITY] = ev_input.get(
            CONF_SIGENERGY_CHARGER_CHARGE_POWER_LIMIT_ENTITY, ""
        ).strip()
        final_data[CONF_SIGENERGY_CHARGER_DISCHARGE_POWER_LIMIT_ENTITY] = ev_input.get(
            CONF_SIGENERGY_CHARGER_DISCHARGE_POWER_LIMIT_ENTITY, ""
        ).strip()

        self._apply_legacy_data_key_removals()
        return self.async_create_entry(title="", data=final_data)

    async def async_step_zaptec_cloud_options(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle Zaptec Cloud API credentials in options flow."""
        errors = {}

        if user_input is not None:
            username = user_input.get(CONF_ZAPTEC_USERNAME, "")
            password = user_input.get(CONF_ZAPTEC_PASSWORD, "")

            if username and password:
                from ..zaptec_api import ZaptecCloudClient

                client = ZaptecCloudClient(username, password)
                try:
                    success, message = await client.test_connection()
                    if success:
                        chargers = await client.get_chargers()
                        installations = await client.get_installations()

                        ev_data = getattr(self, "_ev_options_data", {})
                        ev_data[CONF_ZAPTEC_USERNAME] = username
                        ev_data[CONF_ZAPTEC_PASSWORD] = password

                        if chargers:
                            ev_data[CONF_ZAPTEC_CHARGER_ID] = chargers[0].get("Id", "")
                        if installations:
                            ev_data[CONF_ZAPTEC_INSTALLATION_ID_CLOUD] = installations[
                                0
                            ].get("Id", "")

                        return self._save_ev_options(ev_data)
                    else:
                        errors["base"] = "zaptec_auth_failed"
                        _LOGGER.error("Zaptec Cloud auth failed: %s", message)
                except Exception as e:
                    errors["base"] = "zaptec_auth_failed"
                    _LOGGER.error("Zaptec Cloud connection error: %s", e)
                finally:
                    await client.close()
            else:
                errors["base"] = "zaptec_missing_credentials"

        return self.async_show_form(
            step_id="zaptec_cloud_options",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_ZAPTEC_USERNAME,
                        default=self._get_option(CONF_ZAPTEC_USERNAME, ""),
                    ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
                    vol.Required(CONF_ZAPTEC_PASSWORD): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_flow_power_options(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 2b: Flow Power main settings (region, base rate, PEA, sync)."""
        if user_input is not None:
            # Store main options temporarily
            self._flow_power_main_options = user_input

            # If switching to Amber and no token exists, collect it first
            price_source = user_input.get(CONF_FLOW_POWER_PRICE_SOURCE, "aemo")
            if price_source == "amber" and not self.config_entry.data.get(
                CONF_AMBER_API_TOKEN
            ):
                return await self.async_step_flow_power_amber_token()
            if price_source == "kwatch" and not self._get_option(CONF_FLOWPOWER_API_KEY):
                return await self.async_step_flow_power_api_key_options()

            return await self.async_step_flow_power_network_options()

        schema = {
            vol.Required(
                CONF_FLOW_POWER_STATE,
                default=self._get_option(CONF_FLOW_POWER_STATE, "NSW1"),
            ): SelectSelector(SelectSelectorConfig(
                options=[
                    SelectOptionDict(value=k, label=v)
                    for k, v in FLOW_POWER_STATES.items()
                ],
                mode=SelectSelectorMode.DROPDOWN,
            )),
            vol.Required(
                CONF_FLOW_POWER_PRICE_SOURCE,
                default=self._get_option(CONF_FLOW_POWER_PRICE_SOURCE, "aemo"),
            ): SelectSelector(SelectSelectorConfig(
                options=[
                    SelectOptionDict(value=k, label=v)
                    for k, v in FLOW_POWER_PRICE_SOURCES.items()
                ],
                mode=SelectSelectorMode.DROPDOWN,
            )),
            vol.Required(
                CONF_FLOW_POWER_BASE_RATE,
                default=self._get_option(
                    CONF_FLOW_POWER_BASE_RATE, FLOW_POWER_DEFAULT_BASE_RATE
                ),
            ): NumberSelector(NumberSelectorConfig(
                min=0.0, max=100.0, step=0.01, unit_of_measurement=self._selector_unit(),
                mode=NumberSelectorMode.BOX,
            )),
            vol.Required(
                CONF_FLOW_POWER_EXPORT_RATE,
                default=self._get_option(
                    CONF_FLOW_POWER_EXPORT_RATE,
                    FLOW_POWER_EXPORT_RATES.get(
                        self._get_option(CONF_FLOW_POWER_STATE, "NSW1"), 0.0
                    )
                    * 100,
                ),
            ): NumberSelector(NumberSelectorConfig(
                min=0.0, max=100.0, step=0.01, unit_of_measurement=self._selector_unit(),
                mode=NumberSelectorMode.BOX,
            )),
            vol.Optional(
                CONF_PEA_ENABLED,
                default=self._get_option(CONF_PEA_ENABLED, True),
            ): BooleanSelector(),
            vol.Optional(
                CONF_AUTO_SYNC_ENABLED,
                default=self._get_option(CONF_AUTO_SYNC_ENABLED, True),
            ): BooleanSelector(),
        }

        return self.async_show_form(
            step_id="flow_power_options",
            data_schema=vol.Schema(schema),
        )

    async def async_step_flow_power_portal_options(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Dedicated Flow Power portal account section (options flow)."""
        if user_input is not None:
            if user_input.get("connect_portal", True):
                return await self.async_step_flow_power_portal_reauth()
            return self.async_create_entry(
                title="", data=dict(self.config_entry.options)
            )

        return self.async_show_form(
            step_id="flow_power_portal_options",
            data_schema=vol.Schema(
                {
                    vol.Optional("connect_portal", default=True): BooleanSelector(),
                }
            ),
        )

    async def async_step_flow_power_portal_reauth(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Re-authenticate with Flow Power portal (options flow)."""
        from ..const import CONF_FLOWPOWER_EMAIL, CONF_FLOWPOWER_PASSWORD

        errors: dict[str, str] = {}
        current = {**self.config_entry.data, **self.config_entry.options}

        # Auto-submit stored credentials on first visit
        if (
            user_input is None
            and current.get(CONF_FLOWPOWER_EMAIL)
            and current.get(CONF_FLOWPOWER_PASSWORD)
        ):
            email = current[CONF_FLOWPOWER_EMAIL]
            password = current[CONF_FLOWPOWER_PASSWORD]
            try:
                from ..flow_power_portal import FlowPowerPortalClient

                self._fp_client = FlowPowerPortalClient()
                result = await self._fp_client.authenticate(email, password)
                if result.get("status") == "mfa_required":
                    self._fp_email = email
                    self._fp_password = password
                    return await self.async_step_flow_power_portal_mfa_options()
            except ValueError:
                errors["base"] = "invalid_credentials"
                self._fp_client = None
            except Exception as err:
                _LOGGER.exception(
                    "Flow Power portal stored-credential login failed: %s", err
                )
                errors["base"] = "cannot_connect"
                if getattr(self, "_fp_client", None) is not None:
                    await self._fp_client.close()
                self._fp_client = None

        if user_input is not None:
            email = user_input.get(CONF_FLOWPOWER_EMAIL, "")
            password = user_input.get(CONF_FLOWPOWER_PASSWORD, "")
            if email and password:
                try:
                    from ..flow_power_portal import FlowPowerPortalClient

                    self._fp_client = FlowPowerPortalClient()
                    result = await self._fp_client.authenticate(email, password)
                    if result.get("status") == "mfa_required":
                        self._fp_email = email
                        self._fp_password = password
                        return await self.async_step_flow_power_portal_mfa_options()
                except ValueError:
                    errors["base"] = "invalid_credentials"
                except Exception as err:
                    _LOGGER.exception(
                        "Flow Power portal login failed from options: %s", err
                    )
                    errors["base"] = "cannot_connect"
                    if getattr(self, "_fp_client", None) is not None:
                        await self._fp_client.close()
                    self._fp_client = None
            else:
                errors["base"] = "invalid_credentials"

        return self.async_show_form(
            step_id="flow_power_portal_reauth",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_FLOWPOWER_EMAIL,
                        default=current.get(CONF_FLOWPOWER_EMAIL, ""),
                    ): TextSelector(TextSelectorConfig(type=TextSelectorType.EMAIL)),
                    vol.Required(
                        CONF_FLOWPOWER_PASSWORD,
                        default=current.get(CONF_FLOWPOWER_PASSWORD, ""),
                    ): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
                }
            ),
            errors=errors,
        )

    async def async_step_flow_power_portal_mfa_options(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """SMS MFA verification in options flow."""
        from ..const import CONF_FLOWPOWER_EMAIL, CONF_FLOWPOWER_PASSWORD

        errors: dict[str, str] = {}

        if user_input is not None:
            code = user_input.get("mfa_code", "")
            if code and hasattr(self, "_fp_client"):
                success = await self._fp_client.verify_mfa(code)
                if success:
                    # Store credentials and stash client
                    self.hass.data.setdefault(DOMAIN, {})
                    self.hass.data[DOMAIN]["_pending_fp_client"] = self._fp_client
                    return self._save_and_finish(
                        {
                            CONF_FLOWPOWER_EMAIL: self._fp_email,
                            CONF_FLOWPOWER_PASSWORD: self._fp_password,
                        }
                    )
                else:
                    errors["base"] = "invalid_mfa_code"
            else:
                errors["base"] = "invalid_mfa_code"

        return self.async_show_form(
            step_id="flow_power_portal_mfa_options",
            data_schema=vol.Schema(
                {
                    vol.Required("mfa_code"): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
                }
            ),
            errors=errors,
        )

    async def async_step_flow_power_amber_token(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Collect Amber API token when Flow Power user switches to Amber pricing."""
        errors: dict[str, str] = {}

        if user_input is not None:
            validation_result = await validate_amber_token(
                self.hass, user_input[CONF_AMBER_API_TOKEN]
            )

            if validation_result["success"]:
                # Store token in config entry data
                new_data = {**self.config_entry.data}
                new_data[CONF_AMBER_API_TOKEN] = user_input[CONF_AMBER_API_TOKEN]
                self.hass.config_entries.async_update_entry(
                    self.config_entry, data=new_data
                )
                return await self.async_step_flow_power_network_options()
            else:
                errors["base"] = validation_result.get("error", "unknown")

        return self.async_show_form(
            step_id="flow_power_amber_token",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_AMBER_API_TOKEN): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
            description_placeholders={
                "amber_url": "https://app.amber.com.au/developers",
            },
        )

    async def async_step_flow_power_api_key_options(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Collect Flow Power KWatch API key from the options flow."""
        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = user_input.get(CONF_FLOWPOWER_API_KEY, "")
            region = self._flow_power_main_options.get(
                CONF_FLOW_POWER_STATE,
                self._get_option(CONF_FLOW_POWER_STATE, "NSW1"),
            )
            validation_result = await validate_flow_power_api_key(
                self.hass,
                api_key,
                region,
            )
            if validation_result["success"]:
                self._flow_power_main_options[CONF_FLOWPOWER_API_KEY] = api_key
                self._flow_power_sites = validation_result.get("sites", [])
                if len(self._flow_power_sites) == 1:
                    site = self._flow_power_sites[0]
                    self._flow_power_main_options[CONF_FLOWPOWER_NMI] = site["nmi"]
                    await _prefill_flow_power_network_tariff(
                        self.hass,
                        self._flow_power_main_options,
                        site,
                    )
                    return await self.async_step_flow_power_network_options()
                if self._flow_power_sites:
                    return await self.async_step_flow_power_site_options()
                return await self.async_step_flow_power_network_options()
            errors["base"] = validation_result.get("error", "cannot_connect")

        return self.async_show_form(
            step_id="flow_power_api_key_options",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_FLOWPOWER_API_KEY): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_flow_power_site_options(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Select Flow Power KWatch site in the options flow."""
        sites = getattr(self, "_flow_power_sites", [])
        errors: dict[str, str] = {}

        if user_input is not None:
            selected_nmi = user_input.get(CONF_FLOWPOWER_NMI)
            site = next((item for item in sites if item.get("nmi") == selected_nmi), None)
            if site:
                self._flow_power_main_options[CONF_FLOWPOWER_NMI] = selected_nmi
                await _prefill_flow_power_network_tariff(
                    self.hass,
                    self._flow_power_main_options,
                    site,
                )
                return await self.async_step_flow_power_network_options()
            errors["base"] = "invalid_site"

        return self.async_show_form(
            step_id="flow_power_site_options",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_FLOWPOWER_NMI): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(
                                    value=site["nmi"],
                                    label=_flow_power_site_label(site),
                                )
                                for site in sites
                            ],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_flow_power_network_options(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 2b-2: Flow Power network tariff & advanced settings."""
        if user_input is not None:
            # Parse combined network:tariff_code selection
            combined = user_input.pop("fp_network_tariff_combined", "")
            if combined and ":" in combined:
                fp_network, fp_tariff_code = combined.split(":", 1)
                user_input[CONF_FP_NETWORK] = fp_network
                user_input[CONF_FP_TARIFF_CODE] = fp_tariff_code
                api_name = NETWORK_API_NAME.get(fp_network, fp_network.lower())
                user_input[CONF_NETWORK_DISTRIBUTOR] = api_name
                user_input[CONF_NETWORK_TARIFF_CODE] = fp_tariff_code
            else:
                user_input[CONF_FP_NETWORK] = ""
                user_input[CONF_FP_TARIFF_CODE] = ""
                user_input.pop(CONF_NETWORK_DISTRIBUTOR, None)
                user_input.pop(CONF_NETWORK_TARIFF_CODE, None)

            # 0 is sentinel for "not set" — store None so auto-calculation kicks in
            if not user_input.get(CONF_FP_TWAP_OVERRIDE):
                user_input[CONF_FP_TWAP_OVERRIDE] = None
            if not user_input.get(CONF_PEA_CUSTOM_VALUE):
                user_input[CONF_PEA_CUSTOM_VALUE] = None

            # Merge main options from previous step with network/advanced options
            merged = {**self._flow_power_main_options, **user_input}
            self._flow_power_main_options = {}

            merged[CONF_ELECTRICITY_PROVIDER] = "flow_power"
            self._amber_options = merged
            return await self.async_step_demand_charge_options()

        pending_main = getattr(self, "_flow_power_main_options", {})
        current_region = pending_main.get(
            CONF_FLOW_POWER_STATE,
            self._get_option(CONF_FLOW_POWER_STATE, "NSW1"),
        )
        default_markup = DEFAULT_FP_AMBER_MARKUP.get(current_region, 4.0)

        # Build combined network+tariff dropdown for the region — all options in one pass
        from ..tariff_utils import get_tariff_codes_for_network
        region_network_names = REGION_NETWORKS.get(current_region, [])
        fp_combined_options: dict[str, str] = {"": "None (use simple formula)"}
        for network_name in region_network_names:
            codes = await self.hass.async_add_executor_job(
                get_tariff_codes_for_network, network_name
            )
            for code, desc in codes.items():
                fp_combined_options[f"{network_name}:{code}"] = f"{network_name} — {desc}"

        # Reconstruct current stored selection as combined key
        stored_network = pending_main.get(
            CONF_FP_NETWORK,
            self._get_option(CONF_FP_NETWORK, ""),
        )
        stored_tariff = pending_main.get(
            CONF_FP_TARIFF_CODE,
            self._get_option(CONF_FP_TARIFF_CODE, ""),
        )
        current_combined = f"{stored_network}:{stored_tariff}" if (stored_network and stored_tariff) else ""
        if current_combined not in fp_combined_options:
            current_combined = ""

        valid_hours = {f"{h:02d}:00" for h in range(24)}
        hour_options = [SelectOptionDict(value="", label="—")] + [
            SelectOptionDict(value=f"{h:02d}:00", label=f"{h:02d}:00")
            for h in range(24)
        ]

        return self.async_show_form(
            step_id="flow_power_network_options",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        "fp_network_tariff_combined",
                        default=current_combined,
                    ): SelectSelector(SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=k, label=v)
                            for k, v in fp_combined_options.items()
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )),
                    vol.Optional(
                        CONF_FP_TWAP_OVERRIDE,
                        default=self._get_option(CONF_FP_TWAP_OVERRIDE, None) or 0.0,
                    ): NumberSelector(NumberSelectorConfig(
                        min=0.0, max=50.0, step=0.01, unit_of_measurement=self._selector_unit(),
                        mode=NumberSelectorMode.BOX,
                    )),
                    vol.Optional(
                        CONF_FP_BILLING_DAY,
                        default=self._get_option(CONF_FP_BILLING_DAY, 1) or 1,
                    ): NumberSelector(NumberSelectorConfig(
                        min=1, max=28, step=1, unit_of_measurement="day",
                        mode=NumberSelectorMode.BOX,
                    )),
                    vol.Optional(
                        CONF_FP_AMBER_MARKUP,
                        default=self._get_option(CONF_FP_AMBER_MARKUP, None) or default_markup,
                    ): NumberSelector(NumberSelectorConfig(
                        min=0.0, max=20.0, step=0.01, unit_of_measurement=self._selector_unit(),
                        mode=NumberSelectorMode.BOX,
                    )),
                    vol.Optional(
                        CONF_PEA_CUSTOM_VALUE,
                        default=self._get_option(CONF_PEA_CUSTOM_VALUE, None) or 0.0,
                    ): NumberSelector(NumberSelectorConfig(
                        min=-50.0, max=50.0, step=0.01, unit_of_measurement=self._selector_unit(),
                        mode=NumberSelectorMode.BOX,
                    )),
                    vol.Optional(
                        CONF_NETWORK_USE_MANUAL_RATES,
                        default=self._get_option(CONF_NETWORK_USE_MANUAL_RATES, False),
                    ): BooleanSelector(),
                    vol.Optional(
                        CONF_NETWORK_TARIFF_TYPE,
                        default=self._get_option(CONF_NETWORK_TARIFF_TYPE, "flat"),
                    ): SelectSelector(SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=k, label=v)
                            for k, v in NETWORK_TARIFF_TYPES.items()
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )),
                    vol.Optional(
                        CONF_NETWORK_FLAT_RATE,
                        default=self._get_option(CONF_NETWORK_FLAT_RATE, 8.0),
                    ): NumberSelector(NumberSelectorConfig(
                        min=0.0, max=50.0, step=0.01, unit_of_measurement=self._selector_unit(),
                        mode=NumberSelectorMode.BOX,
                    )),
                    vol.Optional(
                        CONF_NETWORK_PEAK_RATE,
                        default=self._get_option(CONF_NETWORK_PEAK_RATE, 15.0),
                    ): NumberSelector(NumberSelectorConfig(
                        min=0.0, max=50.0, step=0.01, unit_of_measurement=self._selector_unit(),
                        mode=NumberSelectorMode.BOX,
                    )),
                    vol.Optional(
                        CONF_NETWORK_SHOULDER_RATE,
                        default=self._get_option(CONF_NETWORK_SHOULDER_RATE, 5.0),
                    ): NumberSelector(NumberSelectorConfig(
                        min=0.0, max=50.0, step=0.01, unit_of_measurement=self._selector_unit(),
                        mode=NumberSelectorMode.BOX,
                    )),
                    vol.Optional(
                        CONF_NETWORK_OFFPEAK_RATE,
                        default=self._get_option(CONF_NETWORK_OFFPEAK_RATE, 2.0),
                    ): NumberSelector(NumberSelectorConfig(
                        min=0.0, max=50.0, step=0.01, unit_of_measurement=self._selector_unit(),
                        mode=NumberSelectorMode.BOX,
                    )),
                    vol.Optional(
                        CONF_NETWORK_PEAK_START,
                        default=self._get_option(CONF_NETWORK_PEAK_START, "16:00") if self._get_option(CONF_NETWORK_PEAK_START, "16:00") in valid_hours else "16:00",
                    ): SelectSelector(SelectSelectorConfig(
                        options=hour_options,
                        mode=SelectSelectorMode.DROPDOWN,
                    )),
                    vol.Optional(
                        CONF_NETWORK_PEAK_END,
                        default=self._get_option(CONF_NETWORK_PEAK_END, "21:00") if self._get_option(CONF_NETWORK_PEAK_END, "21:00") in valid_hours else "21:00",
                    ): SelectSelector(SelectSelectorConfig(
                        options=hour_options,
                        mode=SelectSelectorMode.DROPDOWN,
                    )),
                    vol.Optional(
                        CONF_NETWORK_OFFPEAK_START,
                        default=self._get_option(CONF_NETWORK_OFFPEAK_START, "10:00") if self._get_option(CONF_NETWORK_OFFPEAK_START, "10:00") in valid_hours else "10:00",
                    ): SelectSelector(SelectSelectorConfig(
                        options=hour_options,
                        mode=SelectSelectorMode.DROPDOWN,
                    )),
                    vol.Optional(
                        CONF_NETWORK_OFFPEAK_END,
                        default=self._get_option(CONF_NETWORK_OFFPEAK_END, "15:00") if self._get_option(CONF_NETWORK_OFFPEAK_END, "15:00") in valid_hours else "15:00",
                    ): SelectSelector(SelectSelectorConfig(
                        options=hour_options,
                        mode=SelectSelectorMode.DROPDOWN,
                    )),
                    vol.Optional(
                        CONF_NETWORK_OTHER_FEES,
                        default=self._get_option(CONF_NETWORK_OTHER_FEES, 1.5),
                    ): NumberSelector(NumberSelectorConfig(
                        min=0.0, max=20.0, step=0.01, unit_of_measurement=self._selector_unit(),
                        mode=NumberSelectorMode.BOX,
                    )),
                    vol.Optional(
                        CONF_NETWORK_INCLUDE_GST,
                        default=self._get_option(CONF_NETWORK_INCLUDE_GST, True),
                    ): BooleanSelector(),
                }
            ),
        )

    async def async_step_globird_options(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 2c: Globird specific options."""
        errors: dict[str, str] = {}

        if user_input is not None:
            if not errors:
                # Add provider to the data
                user_input[CONF_ELECTRICITY_PROVIDER] = getattr(
                    self,
                    "_provider",
                    self._get_option(CONF_ELECTRICITY_PROVIDER, "globird"),
                )

                # If spike not enabled, ensure region/threshold don't cause issues
                if not user_input.get(CONF_AEMO_SPIKE_ENABLED, False):
                    user_input.pop(CONF_AEMO_REGION, None)
                    user_input.pop(CONF_AEMO_SPIKE_THRESHOLD, None)

                if user_input.get(CONF_GLOBIRD_PLAN) != GLOBIRD_PLAN_ZEROHERO_CUSTOM:
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
                        user_input.pop(key, None)

                # Check if user wants to configure custom tariff
                self._globird_configure_custom_tariff = user_input.pop(
                    "configure_custom_tariff", False
                )

                # Store options and route accordingly
                self._amber_options = user_input

                return await self._route_after_globird_portal_options()

        # Build region choices for AEMO
        region_choices = {"": "Select Region..."}
        region_choices.update(AEMO_REGIONS)

        is_tesla = (
            self.config_entry.data.get(CONF_BATTERY_SYSTEM, BATTERY_SYSTEM_TESLA)
            == BATTERY_SYSTEM_TESLA
        )

        current_globird_settings = dict(self.config_entry.data or {})
        current_globird_settings.update(self.config_entry.options or {})
        schema_fields: dict[Any, Any] = dict(
            self._globird_plan_schema(current_globird_settings).schema
        )
        schema_fields.update({
            vol.Optional(
                CONF_AEMO_SPIKE_ENABLED,
                default=self._get_option(CONF_AEMO_SPIKE_ENABLED, False),
            ): BooleanSelector(),
            vol.Optional(
                CONF_AEMO_REGION,
                default=self._get_option(CONF_AEMO_REGION, ""),
            ): SelectSelector(SelectSelectorConfig(
                options=[
                    SelectOptionDict(value=k, label=v)
                    for k, v in region_choices.items()
                ],
                mode=SelectSelectorMode.DROPDOWN,
            )),
            vol.Optional(
                CONF_AEMO_SPIKE_THRESHOLD,
                default=self._get_option(CONF_AEMO_SPIKE_THRESHOLD, 3000.0),
            ): NumberSelector(NumberSelectorConfig(
                min=0.0, max=20000.0, step=1.0, unit_of_measurement=self._selector_unit("market_rate"),
                mode=NumberSelectorMode.BOX,
            )),
        })

        # Tesla users get tariff from the Tesla API — no need for manual configuration
        if not is_tesla:
            schema_fields[vol.Optional("configure_custom_tariff", default=False)] = BooleanSelector()
            tariff_hint = (
                "**Custom Tariff (recommended):** Non-Tesla systems, including "
                "Sigenergy and FoxESS cloud, should configure the Globird/TOU rates "
                "inside PowerSync. These rates are needed for accurate price sensors, "
                "battery optimisation, and EV charging. For ZeroHero, enter the base "
                "feed-in tariff here; PowerSync models the capped Super Export bonus "
                "separately from your TOU tariff."
            )
        else:
            tariff_hint = (
                "Tesla Powerwall detected: PowerSync reads the TOU schedule from "
                "the tariff already stored on your Powerwall. Set the correct "
                "Globird/TOU tariff in the Tesla app before saving these settings. "
                "After changing the Tesla tariff, restart Home Assistant or reload "
                "PowerSync so the scheduler refreshes its cached baseline. Select "
                "your ZeroHero plan here so PowerSync can model the export cap and "
                "no-import credit on top of the Tesla tariff."
            )

        return self.async_show_form(
            step_id="globird_options",
            data_schema=vol.Schema(schema_fields),
            errors=errors,
            description_placeholders={
                "tariff_hint": tariff_hint,
            },
        )

    async def _route_after_globird_portal_options(self) -> FlowResult:
        """Continue the GloBird options flow after the portal section."""
        configure_tariff = getattr(self, "_globird_configure_custom_tariff", False)
        self._globird_configure_custom_tariff = False
        if configure_tariff:
            return await self.async_step_custom_tariff_options()
        return await self.async_step_demand_charge_options()

    async def async_step_globird_portal_options(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Dedicated GloBird portal account section (options flow)."""
        if user_input is not None:
            if user_input.get("connect_globird_portal", True):
                return await self.async_step_globird_portal_login_options()
            return self.async_create_entry(
                title="", data=dict(self.config_entry.options)
            )

        return self.async_show_form(
            step_id="globird_portal_options",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        "connect_globird_portal", default=True
                    ): BooleanSelector(),
                }
            ),
        )

    async def async_step_globird_portal_login_options(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Authenticate with the GloBird portal from options."""
        errors: dict[str, str] = {}
        current = {**self.config_entry.data, **self.config_entry.options}

        if user_input is not None:
            email = (user_input.get(CONF_GLOBIRD_EMAIL) or "").strip()
            password = user_input.get(CONF_GLOBIRD_PASSWORD) or ""
            password_for_validation = password or current.get(CONF_GLOBIRD_PASSWORD, "")
            if email and password_for_validation:
                error = await _validate_globird_credentials(
                    email, password_for_validation
                )
                if error is None:
                    return self._save_and_finish(
                        {
                            CONF_GLOBIRD_EMAIL: email,
                            CONF_GLOBIRD_PASSWORD: password_for_validation,
                        }
                    )
                errors["base"] = error
            else:
                errors["base"] = "invalid_globird_auth"

        return self.async_show_form(
            step_id="globird_portal_login_options",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_GLOBIRD_EMAIL,
                        default=current.get(CONF_GLOBIRD_EMAIL, ""),
                    ): TextSelector(TextSelectorConfig(type=TextSelectorType.EMAIL)),
                    vol.Optional(CONF_GLOBIRD_PASSWORD): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_localvolts_options(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 2e: Localvolts specific options."""
        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = user_input.get(CONF_LOCALVOLTS_API_KEY, "")
            partner_id = user_input.get(CONF_LOCALVOLTS_PARTNER_ID, "")
            nmi = user_input.get(CONF_LOCALVOLTS_NMI, "")

            # Validate if credentials changed
            if api_key and partner_id and nmi:
                validation = await validate_localvolts_credentials(
                    self.hass, api_key, partner_id, nmi
                )
                if not validation["success"]:
                    errors["base"] = validation.get("error", "cannot_connect")

            if not errors:
                self._amber_options = {
                    CONF_ELECTRICITY_PROVIDER: "localvolts",
                    CONF_LOCALVOLTS_API_KEY: api_key,
                    CONF_LOCALVOLTS_PARTNER_ID: partner_id,
                    CONF_LOCALVOLTS_NMI: nmi,
                    CONF_AUTO_SYNC_ENABLED: user_input.get(
                        CONF_AUTO_SYNC_ENABLED, True
                    ),
                    CONF_BATTERY_CURTAILMENT_ENABLED: user_input.get(
                        CONF_BATTERY_CURTAILMENT_ENABLED, False
                    ),
                }
                return await self.async_step_demand_charge_options()

        current_api_key = self.config_entry.data.get(CONF_LOCALVOLTS_API_KEY, "")
        current_partner_id = self.config_entry.data.get(CONF_LOCALVOLTS_PARTNER_ID, "")
        current_nmi = self.config_entry.data.get(CONF_LOCALVOLTS_NMI, "")
        current_auto_sync = self._get_option(CONF_AUTO_SYNC_ENABLED, True)
        current_curtailment = self._get_option(CONF_BATTERY_CURTAILMENT_ENABLED, False)

        return self.async_show_form(
            step_id="localvolts_options",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_LOCALVOLTS_API_KEY, default=current_api_key
                    ): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
                    vol.Required(
                        CONF_LOCALVOLTS_PARTNER_ID, default=current_partner_id
                    ): TextSelector(),
                    vol.Required(
                        CONF_LOCALVOLTS_NMI, default=current_nmi
                    ): TextSelector(),
                    vol.Optional(
                        CONF_AUTO_SYNC_ENABLED, default=current_auto_sync
                    ): BooleanSelector(),
                    vol.Optional(
                        CONF_BATTERY_CURTAILMENT_ENABLED, default=current_curtailment
                    ): BooleanSelector(),
                }
            ),
            errors=errors,
        )

    async def async_step_epex_options(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 2f: EPEX Day-Ahead (EU) specific options."""
        errors: dict[str, str] = {}

        if user_input is not None:
            region = user_input.get(CONF_EPEX_REGION, "DE")
            surcharge = user_input.get(CONF_EPEX_SURCHARGE, 0.0)
            tax_percent = user_input.get(CONF_EPEX_TAX_PERCENT, 0.0)
            export_rate = user_input.get(CONF_EPEX_EXPORT_RATE, 0.0)
            import_price_entity = _normalize_optional_entity(
                user_input.get(CONF_EPEX_IMPORT_PRICE_ENTITY)
            )
            export_price_entity = _normalize_optional_entity(
                user_input.get(CONF_EPEX_EXPORT_PRICE_ENTITY)
            )

            # Validate by fetching prices
            try:
                from ..epex_api import EPEXAPIClient

                client = EPEXAPIClient(async_get_clientsession(self.hass))
                valid = await client.validate_region(region)

                if not valid:
                    errors["base"] = "no_prices"
            except Exception as err:
                errors["base"] = "cannot_connect"
                _LOGGER.exception("Error validating EPEX region: %s", err)

            if not errors:
                self._amber_options = {
                    CONF_ELECTRICITY_PROVIDER: "epex",
                    CONF_EPEX_REGION: region,
                    CONF_EPEX_SURCHARGE: surcharge,
                    CONF_EPEX_TAX_PERCENT: tax_percent,
                    CONF_EPEX_EXPORT_RATE: export_rate,
                    CONF_AUTO_SYNC_ENABLED: user_input.get(
                        CONF_AUTO_SYNC_ENABLED, True
                    ),
                    CONF_BATTERY_CURTAILMENT_ENABLED: user_input.get(
                        CONF_BATTERY_CURTAILMENT_ENABLED, False
                    ),
                }
                if import_price_entity:
                    self._amber_options[CONF_EPEX_IMPORT_PRICE_ENTITY] = (
                        import_price_entity
                    )
                else:
                    self._amber_options[CONF_EPEX_IMPORT_PRICE_ENTITY] = None
                if export_price_entity:
                    self._amber_options[CONF_EPEX_EXPORT_PRICE_ENTITY] = export_price_entity
                else:
                    self._amber_options[CONF_EPEX_EXPORT_PRICE_ENTITY] = None
                return await self.async_step_demand_charge_options()

        current_region = self._get_option(CONF_EPEX_REGION, "DE")
        current_surcharge = self._get_option(CONF_EPEX_SURCHARGE, 0.0)
        current_tax = self._get_option(CONF_EPEX_TAX_PERCENT, 0.0)
        current_export = self._get_option(CONF_EPEX_EXPORT_RATE, 0.0)
        current_import_price_entity = _normalize_optional_entity(
            self._get_option(CONF_EPEX_IMPORT_PRICE_ENTITY, None)
        )
        current_export_price_entity = _normalize_optional_entity(
            self._get_option(CONF_EPEX_EXPORT_PRICE_ENTITY, None)
        )

        return self.async_show_form(
            step_id="epex_options",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_EPEX_REGION, default=current_region): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(value=k, label=v)
                                for k, v in EPEX_REGIONS.items()
                            ],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Optional(
                        CONF_EPEX_SURCHARGE, default=current_surcharge
                    ): NumberSelector(NumberSelectorConfig(
                        min=0.0, max=100.0, step=0.01, unit_of_measurement="ct/kWh",
                        mode=NumberSelectorMode.BOX,
                    )),
                    vol.Optional(
                        CONF_EPEX_TAX_PERCENT, default=current_tax
                    ): NumberSelector(NumberSelectorConfig(
                        min=0.0, max=100.0, step=0.1, unit_of_measurement="%",
                        mode=NumberSelectorMode.BOX,
                    )),
                    vol.Optional(
                        CONF_EPEX_EXPORT_RATE, default=current_export
                    ): NumberSelector(NumberSelectorConfig(
                        min=0.0, max=100.0, step=0.01, unit_of_measurement="ct/kWh",
                        mode=NumberSelectorMode.BOX,
                    )),
                    vol.Optional(
                        CONF_EPEX_IMPORT_PRICE_ENTITY,
                        description=(
                            {"suggested_value": current_import_price_entity}
                            if current_import_price_entity
                            else None
                        ),
                    ): EntitySelector(EntitySelectorConfig(domain="sensor")),
                    vol.Optional(
                        CONF_EPEX_EXPORT_PRICE_ENTITY,
                        description=(
                            {"suggested_value": current_export_price_entity}
                            if current_export_price_entity
                            else None
                        ),
                    ): EntitySelector(EntitySelectorConfig(domain="sensor")),
                    vol.Optional(
                        CONF_AUTO_SYNC_ENABLED,
                        default=self._get_option(CONF_AUTO_SYNC_ENABLED, True),
                    ): BooleanSelector(),
                    vol.Optional(
                        CONF_BATTERY_CURTAILMENT_ENABLED,
                        default=self._get_option(
                            CONF_BATTERY_CURTAILMENT_ENABLED, False
                        ),
                    ): BooleanSelector(),
                }
            ),
            errors=errors,
        )

    async def async_step_octopus_options(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 2d: Octopus Energy UK specific options."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Build tariff code from product + region
            product_key = user_input.get(CONF_OCTOPUS_PRODUCT, "agile")
            region = user_input.get(CONF_OCTOPUS_REGION, "C")

            # Map product selection to actual product codes
            product_code = OCTOPUS_PRODUCT_CODES.get(
                product_key, OCTOPUS_PRODUCT_CODES["agile"]
            )

            # Validate by fetching current prices
            try:
                from ..octopus_api import OctopusAPIClient

                client = OctopusAPIClient(async_get_clientsession(self.hass))

                # Dynamically discover current Tracker product code
                if product_key == "tracker":
                    try:
                        discovered = await client.discover_tracker_product()
                        if discovered:
                            product_code = discovered
                    except Exception:
                        pass  # Fall back to hardcoded

                tariff_code = f"E-1R-{product_code}-{region}"

                # Get export product/tariff codes if available
                export_product_code = OCTOPUS_EXPORT_PRODUCT_CODES.get(product_key)
                export_tariff_code = (
                    f"E-1R-{export_product_code}-{region}"
                    if export_product_code
                    else None
                )

                rates = await client.get_current_rates(
                    product_code, tariff_code, page_size=5
                )

                if not rates:
                    errors["base"] = "no_prices"
                    _LOGGER.error(
                        "No Octopus prices found for tariff %s in region %s",
                        tariff_code,
                        region,
                    )
            except Exception as err:
                errors["base"] = "cannot_connect"
                _LOGGER.exception("Error validating Octopus tariff: %s", err)

            if not errors:
                # Store Octopus options
                self._amber_options = {
                    CONF_ELECTRICITY_PROVIDER: "octopus",
                    CONF_OCTOPUS_PRODUCT: product_key,
                    CONF_OCTOPUS_REGION: region,
                    CONF_OCTOPUS_PRODUCT_CODE: product_code,
                    CONF_OCTOPUS_TARIFF_CODE: tariff_code,
                    CONF_OCTOPUS_EXPORT_PRODUCT_CODE: export_product_code,
                    CONF_OCTOPUS_EXPORT_TARIFF_CODE: export_tariff_code,
                    CONF_AUTO_SYNC_ENABLED: user_input.get(
                        CONF_AUTO_SYNC_ENABLED, True
                    ),
                    CONF_BATTERY_CURTAILMENT_ENABLED: user_input.get(
                        CONF_BATTERY_CURTAILMENT_ENABLED, False
                    ),
                }

                _LOGGER.info(
                    "Octopus options validated: product=%s, tariff=%s, region=%s",
                    product_code,
                    tariff_code,
                    region,
                )

                # Continue to saving sessions options
                return await self.async_step_octopus_saving_sessions_options()

        # Get current values
        current_product = self._get_option(CONF_OCTOPUS_PRODUCT, "agile")
        current_region = self._get_option(CONF_OCTOPUS_REGION, "C")

        return self.async_show_form(
            step_id="octopus_options",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_OCTOPUS_PRODUCT, default=current_product): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(value=k, label=v)
                                for k, v in OCTOPUS_PRODUCTS.items()
                            ],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Required(CONF_OCTOPUS_REGION, default=current_region): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(value=k, label=v)
                                for k, v in OCTOPUS_GSP_REGIONS.items()
                            ],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Optional(
                        CONF_AUTO_SYNC_ENABLED,
                        default=self._get_option(CONF_AUTO_SYNC_ENABLED, True),
                    ): BooleanSelector(),
                    vol.Optional(
                        CONF_BATTERY_CURTAILMENT_ENABLED,
                        default=self._get_option(
                            CONF_BATTERY_CURTAILMENT_ENABLED, False
                        ),
                    ): BooleanSelector(),
                }
            ),
            errors=errors,
            description_placeholders={
                "octopus_url": "https://octopus.energy/smart/agile/",
            },
        )

    async def async_step_octopus_saving_sessions_options(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure Octopus Saving Sessions options."""
        errors: dict[str, str] = {}

        if user_input is not None:
            if user_input.get(CONF_OCTOPUS_SAVING_SESSIONS_ENABLED):
                source = user_input.get(CONF_OCTOPUS_SAVING_SESSIONS_SOURCE, "direct")

                if source == "direct":
                    api_key = user_input.get(CONF_OCTOPUS_API_KEY, "")
                    account = user_input.get(CONF_OCTOPUS_ACCOUNT_NUMBER, "")
                    if not api_key or not account:
                        errors["base"] = "missing_credentials"
                    else:
                        try:
                            from ..octopus_sessions import OctopusSavingSessionsClient

                            session = async_get_clientsession(self.hass)
                            client = OctopusSavingSessionsClient(
                                session, api_key, account
                            )
                            authed = await client.authenticate()
                            if not authed:
                                errors["base"] = "invalid_auth"
                        except Exception:
                            errors["base"] = "cannot_connect"

            if not errors:
                # Store saving session options in _amber_options (merged with Octopus tariff)
                self._amber_options.update(
                    {
                        CONF_OCTOPUS_SAVING_SESSIONS_ENABLED: user_input.get(
                            CONF_OCTOPUS_SAVING_SESSIONS_ENABLED, False
                        ),
                        CONF_OCTOPUS_SAVING_SESSIONS_SOURCE: user_input.get(
                            CONF_OCTOPUS_SAVING_SESSIONS_SOURCE, "direct"
                        ),
                        CONF_OCTOPUS_API_KEY: user_input.get(CONF_OCTOPUS_API_KEY, ""),
                        CONF_OCTOPUS_ACCOUNT_NUMBER: user_input.get(
                            CONF_OCTOPUS_ACCOUNT_NUMBER, ""
                        ),
                        CONF_OCTOPUS_SAVING_SESSIONS_ENTITY: user_input.get(
                            CONF_OCTOPUS_SAVING_SESSIONS_ENTITY, ""
                        ),
                        CONF_OCTOPUS_SAVING_SESSIONS_AUTO_JOIN: user_input.get(
                            CONF_OCTOPUS_SAVING_SESSIONS_AUTO_JOIN, True
                        ),
                    }
                )
                return await self.async_step_demand_charge_options()

        # Get current values
        current_enabled = self._get_option(CONF_OCTOPUS_SAVING_SESSIONS_ENABLED, False)
        current_source = self._get_option(CONF_OCTOPUS_SAVING_SESSIONS_SOURCE, "direct")
        current_api_key = self._get_option(CONF_OCTOPUS_API_KEY, "")
        current_account = self._get_option(CONF_OCTOPUS_ACCOUNT_NUMBER, "")
        current_entity = self._get_option(CONF_OCTOPUS_SAVING_SESSIONS_ENTITY, "")
        current_auto_join = self._get_option(
            CONF_OCTOPUS_SAVING_SESSIONS_AUTO_JOIN, True
        )

        saving_session_sources = {
            "direct": "Direct (Octopus API key)",
            "entity": "Bottlecap Dave integration",
        }

        data_schema = vol.Schema(
            {
                vol.Optional(
                    CONF_OCTOPUS_SAVING_SESSIONS_ENABLED, default=current_enabled
                ): BooleanSelector(),
                vol.Optional(
                    CONF_OCTOPUS_SAVING_SESSIONS_SOURCE, default=current_source
                ): SelectSelector(SelectSelectorConfig(
                    options=[
                        SelectOptionDict(value=k, label=v)
                        for k, v in saving_session_sources.items()
                    ],
                    mode=SelectSelectorMode.DROPDOWN,
                )),
                vol.Optional(CONF_OCTOPUS_API_KEY, default=current_api_key): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
                vol.Optional(CONF_OCTOPUS_ACCOUNT_NUMBER, default=current_account): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEXT)
                ),
                vol.Optional(
                    CONF_OCTOPUS_SAVING_SESSIONS_ENTITY, default=current_entity
                ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
                vol.Optional(
                    CONF_OCTOPUS_SAVING_SESSIONS_AUTO_JOIN, default=current_auto_join
                ): BooleanSelector(),
            }
        )

        return self.async_show_form(
            step_id="octopus_saving_sessions_options",
            data_schema=data_schema,
            errors=errors,
        )

    async def async_step_nz_options(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 2e: NZ electricity provider options."""
        if user_input is not None:
            # Add provider to the data
            user_input[CONF_ELECTRICITY_PROVIDER] = "nz"

            retailer = user_input.get(
                CONF_NZ_RETAILER, self._get_option(CONF_NZ_RETAILER, "nz_custom")
            )
            peak_rate = user_input.get(CONF_NZ_PEAK_RATE, 40.0)
            shoulder_rate = user_input.get(CONF_NZ_SHOULDER_RATE, 25.0)
            offpeak_rate = user_input.get(CONF_NZ_OFFPEAK_RATE, 15.0)
            peak_export = user_input.get(CONF_NZ_PEAK_EXPORT, 8.0)
            offpeak_export = user_input.get(CONF_NZ_OFFPEAK_EXPORT, 8.0)

            # Load TOU periods from the selected retailer template
            from ..tariff_templates import (
                get_nz_template,
                NZ_WEEKDAY_PEAK,
                NZ_WEEKDAY_SHOULDER,
                NZ_WEEKEND_SHOULDER,
                NZ_OFFPEAK_OVERNIGHT,
            )

            template = get_nz_template(retailer)

            if template:
                tou_periods = template["seasons"]["All Year"]["tou_periods"]
            else:
                tou_periods = {
                    "PEAK": NZ_WEEKDAY_PEAK,
                    "SHOULDER": [*NZ_WEEKDAY_SHOULDER, *NZ_WEEKEND_SHOULDER],
                    "OFF_PEAK": NZ_OFFPEAK_OVERNIGHT,
                }

            retailer_name = NZ_RETAILERS.get(retailer, "Custom NZ Provider")

            # Build energy charges from user input (convert cents to $/kWh)
            energy_charges = {}
            sell_charges = {}
            for period_name in tou_periods:
                if period_name == "PEAK":
                    energy_charges[period_name] = peak_rate / 100
                    sell_charges[period_name] = peak_export / 100
                elif period_name == "SHOULDER":
                    energy_charges[period_name] = shoulder_rate / 100
                    sell_charges[period_name] = (peak_export + offpeak_export) / 200
                elif period_name == "SUPER_OFF_PEAK":
                    energy_charges[period_name] = offpeak_rate / 100
                    sell_charges[period_name] = offpeak_export / 100
                else:
                    energy_charges[period_name] = offpeak_rate / 100
                    sell_charges[period_name] = offpeak_export / 100

            # Build custom tariff and save to automation_store
            custom_tariff = {
                "name": f"{retailer_name} TOU",
                "utility": retailer_name,
                "currency": self._currency(),
                "seasons": {
                    "All Year": {
                        "fromMonth": 1,
                        "toMonth": 12,
                        "tou_periods": tou_periods,
                    }
                },
                "energy_charges": {
                    "All Year": energy_charges,
                },
                "sell_tariff": {
                    "energy_charges": {
                        "All Year": sell_charges,
                    }
                },
            }

            # Save custom tariff to automation_store (same pattern as custom_tariff_options)
            if DOMAIN in self.hass.data:
                for entry_id, entry_data in self.hass.data.get(DOMAIN, {}).items():
                    if (
                        isinstance(entry_data, dict)
                        and "automation_store" in entry_data
                    ):
                        store = entry_data["automation_store"]
                        store.set_custom_tariff(custom_tariff)
                        await store.async_save()

                        # Also update tariff_schedule for immediate use
                        from .. import convert_custom_tariff_to_schedule

                        tariff_schedule = convert_custom_tariff_to_schedule(
                            custom_tariff,
                            currency=self._currency(),
                        )
                        entry_data["tariff_schedule"] = tariff_schedule
                        _LOGGER.info(
                            "NZ custom tariff saved via options flow: %s", retailer_name
                        )
                        break

            self._amber_options = user_input
            return await self.async_step_demand_charge_options()

        return self.async_show_form(
            step_id="nz_options",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_NZ_RETAILER,
                        default=self._get_option(CONF_NZ_RETAILER, "octopus_nz"),
                    ): SelectSelector(SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=k, label=v)
                            for k, v in NZ_RETAILERS.items()
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )),
                    vol.Required(
                        CONF_NZ_DISTRIBUTION_ZONE,
                        default=self._get_option(CONF_NZ_DISTRIBUTION_ZONE, "vector"),
                    ): SelectSelector(SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=k, label=v)
                            for k, v in NZ_DISTRIBUTION_ZONES.items()
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )),
                    vol.Required(
                        CONF_NZ_PEAK_RATE,
                        default=self._get_option(CONF_NZ_PEAK_RATE, 40.0),
                    ): NumberSelector(NumberSelectorConfig(
                        min=0, max=200, step=0.1, unit_of_measurement=self._selector_unit(),
                        mode=NumberSelectorMode.BOX,
                    )),
                    vol.Required(
                        CONF_NZ_SHOULDER_RATE,
                        default=self._get_option(CONF_NZ_SHOULDER_RATE, 25.0),
                    ): NumberSelector(NumberSelectorConfig(
                        min=0, max=200, step=0.1, unit_of_measurement=self._selector_unit(),
                        mode=NumberSelectorMode.BOX,
                    )),
                    vol.Required(
                        CONF_NZ_OFFPEAK_RATE,
                        default=self._get_option(CONF_NZ_OFFPEAK_RATE, 15.0),
                    ): NumberSelector(NumberSelectorConfig(
                        min=0, max=200, step=0.1, unit_of_measurement=self._selector_unit(),
                        mode=NumberSelectorMode.BOX,
                    )),
                    vol.Required(
                        CONF_NZ_PEAK_EXPORT,
                        default=self._get_option(CONF_NZ_PEAK_EXPORT, 8.0),
                    ): NumberSelector(NumberSelectorConfig(
                        min=0, max=100, step=0.1, unit_of_measurement=self._selector_unit(),
                        mode=NumberSelectorMode.BOX,
                    )),
                    vol.Required(
                        CONF_NZ_OFFPEAK_EXPORT,
                        default=self._get_option(CONF_NZ_OFFPEAK_EXPORT, 8.0),
                    ): NumberSelector(NumberSelectorConfig(
                        min=0, max=100, step=0.1, unit_of_measurement=self._selector_unit(),
                        mode=NumberSelectorMode.BOX,
                    )),
                    vol.Required(
                        CONF_NZ_DAILY_SUPPLY,
                        default=self._get_option(CONF_NZ_DAILY_SUPPLY, 200.0),
                    ): NumberSelector(NumberSelectorConfig(
                        min=0, max=1000, step=0.1, unit_of_measurement=self._selector_unit("minor_daily"),
                        mode=NumberSelectorMode.BOX,
                    )),
                }
            ),
        )

    async def async_step_custom_tariff_options(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure custom tariff — step 1: basic setup (options flow).

        Same multi-step period builder as the initial config flow.
        """
        from ..const import DOMAIN

        errors: dict[str, str] = {}

        if user_input is not None:
            tariff_type = user_input.get("tariff_type", "tou")
            self._tariff_plan_name = user_input.get("plan_name", "")
            self._tariff_offpeak_rate = user_input.get("offpeak_rate", 15) / 100
            self._tariff_fit_rate = user_input.get("fit_rate", 5) / 100

            if tariff_type == "flat":
                flat_rate = user_input.get("flat_rate", 30) / 100
                custom_tariff = self._build_tariff_from_periods_compat(
                    [
                        {
                            "name": "ALL",
                            "start": 0,
                            "end": 24,
                            "days": "all_days",
                            "import_rate": flat_rate,
                            "export_rate": self._tariff_fit_rate,
                        }
                    ],
                )
                await self._save_custom_tariff(custom_tariff)
                return await self.async_step_demand_charge_options()

            # TOU — start the period builder
            self._tariff_periods = []
            return await self.async_step_tariff_period_options()

        # Get current custom tariff defaults
        current_tariff = None
        if DOMAIN in self.hass.data:
            for entry_id, entry_data in self.hass.data.get(DOMAIN, {}).items():
                if isinstance(entry_data, dict) and "automation_store" in entry_data:
                    store = entry_data["automation_store"]
                    current_tariff = store.get_custom_tariff()
                    break

        default_offpeak = 15
        default_fit = 5
        if current_tariff:
            charges = current_tariff.get("energy_charges", {}).get("All Year", {})
            # Find off-peak rate from any OFF_PEAK* key
            for k, v in charges.items():
                if k.startswith("OFF_PEAK") and isinstance(v, (int, float)):
                    default_offpeak = int(v * 100)
                    break
            sell_charges = (
                current_tariff.get("sell_tariff", {})
                .get("energy_charges", {})
                .get("All Year", {})
            )
            for k, v in sell_charges.items():
                if k.startswith("OFF_PEAK") or k == "ALL":
                    if isinstance(v, (int, float)):
                        default_fit = round(v * 100, 1)
                        break

        tariff_type_options = {
            "flat": "Flat Rate (single rate all day)",
            "tou": "Time of Use (multiple periods)",
        }

        return self.async_show_form(
            step_id="custom_tariff_options",
            data_schema=vol.Schema(
                {
                    vol.Optional("plan_name", default=""): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.TEXT)
                    ),
                    vol.Required("tariff_type", default="tou"): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(value=k, label=v)
                                for k, v in tariff_type_options.items()
                            ],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Optional("flat_rate", default=30): NumberSelector(
                        NumberSelectorConfig(
                            min=0, max=200, step=0.1, unit_of_measurement=self._selector_unit(),
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required("offpeak_rate", default=default_offpeak): NumberSelector(
                        NumberSelectorConfig(
                            min=0, max=200, step=0.1, unit_of_measurement=self._selector_unit(),
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required("fit_rate", default=default_fit): NumberSelector(
                        NumberSelectorConfig(
                            min=-100, max=100, step=0.1, unit_of_measurement=self._selector_unit(),
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                }
            ),
            errors=errors,
            description_placeholders={
                "info": f"Configure your electricity tariff. All rates in {self._selector_unit()}.\nFor TOU, you'll add time periods in the next step.",
            },
        )

    async def async_step_tariff_period_options(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Add a tariff time period (options flow). Same as config flow version."""
        from ..const import DOMAIN

        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                start_hour = int(user_input.get("period_start", "15:00").split(":")[0])
                end_hour = int(user_input.get("period_end", "21:00").split(":")[0])
            except (ValueError, IndexError):
                start_hour = 15
                end_hour = 21

            period = {
                "name": user_input.get("period_type", "PEAK"),
                "start": start_hour,
                "end": end_hour,
                "days": user_input.get("period_days", "weekdays"),
                "import_rate": user_input.get("import_rate", 45) / 100,
                "export_rate": user_input.get("export_rate", 5) / 100,
            }
            self._tariff_periods.append(period)

            if user_input.get("add_another", False):
                return await self.async_step_tariff_period_options()

            # Done — build and save tariff
            custom_tariff = self._build_tariff_from_periods_compat(
                self._tariff_periods,
            )
            await self._save_custom_tariff(custom_tariff)
            return await self.async_step_demand_charge_options()

        tariff_hour_options = [
            SelectOptionDict(value=f"{h:02d}:00", label=f"{h:02d}:00")
            for h in range(24)
        ]
        day_options = {
            "weekdays": "Weekdays only (Mon-Fri)",
            "weekends": "Weekends only (Sat-Sun)",
            "all_days": "All days (Mon-Sun)",
        }
        period_types = {
            "PEAK": "Peak",
            "SHOULDER": "Shoulder",
            "OFF_PEAK": "Off-Peak",
            "SUPER_OFF_PEAK": "Super Off-Peak",
        }

        count = len(self._tariff_periods)
        added_desc = ""
        if count > 0:
            lines = []
            minor_unit = self._selector_unit()
            day_labels = {
                "weekdays": "Mon-Fri",
                "weekends": "Sat-Sun",
                "all_days": "Mon-Sun",
            }
            for i, p in enumerate(self._tariff_periods, 1):
                label = {
                    "PEAK": "Peak",
                    "SHOULDER": "Shoulder",
                    "OFF_PEAK": "Off-Peak",
                    "SUPER_OFF_PEAK": "Super Off-Peak",
                }.get(p["name"], p["name"])
                lines.append(
                    f"{i}. {label} {p['start']:02d}:00-{p['end']:02d}:00 "
                    f"{day_labels.get(p.get('days'), 'Mon-Sun')} "
                    f"({p['import_rate'] * 100:.0f}{minor_unit} import, "
                    f"{p['export_rate'] * 100:.0f}{minor_unit} export)"
                )
            added_desc = "Periods added:\n" + "\n".join(lines)

        return self.async_show_form(
            step_id="tariff_period_options",
            data_schema=vol.Schema(
                {
                    vol.Required("period_type", default="PEAK"): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(value=k, label=v)
                                for k, v in period_types.items()
                            ],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Required("period_start", default="15:00"): SelectSelector(
                        SelectSelectorConfig(
                            options=tariff_hour_options,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Required("period_end", default="21:00"): SelectSelector(
                        SelectSelectorConfig(
                            options=tariff_hour_options,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Optional("period_days", default="all_days"): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(value=k, label=v)
                                for k, v in day_options.items()
                            ],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Required("import_rate", default=45): NumberSelector(
                        NumberSelectorConfig(
                            min=0, max=200, step=0.1, unit_of_measurement=self._selector_unit(),
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required("export_rate", default=5): NumberSelector(
                        NumberSelectorConfig(
                            min=-100, max=200, step=0.1, unit_of_measurement=self._selector_unit(),
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Optional("add_another", default=False): BooleanSelector(),
                }
            ),
            errors=errors,
            description_placeholders={
                "period_info": added_desc
                if added_desc
                else "Add your first tariff period. Remaining hours will be off-peak.",
            },
        )

    def _build_tariff_from_periods_compat(self, periods: list[dict]) -> dict:
        """Delegate to ConfigFlow's tariff builder using options flow state."""

        # Create a temporary object with the needed attributes
        class _Ctx:
            pass

        ctx = _Ctx()
        ctx._tariff_offpeak_rate = getattr(self, "_tariff_offpeak_rate", 0.15)
        ctx._tariff_fit_rate = getattr(self, "_tariff_fit_rate", 0.05)
        ctx._tariff_plan_name = getattr(self, "_tariff_plan_name", "")
        ctx._selected_electricity_provider = self.config_entry.data.get(
            CONF_ELECTRICITY_PROVIDER, "other"
        )
        ctx._tariff_currency = self._currency()
        ctx.hass = self.hass
        return PowerSyncConfigFlow._build_tariff_from_periods(ctx, periods)

    async def _save_custom_tariff(self, custom_tariff: dict) -> None:
        """Save custom tariff to automation_store and update live tariff_schedule."""
        from ..const import DOMAIN

        if DOMAIN in self.hass.data:
            for entry_id, entry_data in self.hass.data.get(DOMAIN, {}).items():
                if isinstance(entry_data, dict) and "automation_store" in entry_data:
                    store = entry_data["automation_store"]
                    tariff_currency = normalize_currency(
                        custom_tariff.get("currency"),
                        self._currency(),
                    )
                    custom_tariff["currency"] = tariff_currency
                    store.set_custom_tariff(custom_tariff)
                    await store.async_save()

                    from .. import convert_custom_tariff_to_schedule

                    tariff_schedule = convert_custom_tariff_to_schedule(
                        custom_tariff,
                        currency=tariff_currency,
                    )
                    entry_data["tariff_schedule"] = tariff_schedule
                    _LOGGER.info("Custom tariff saved via options flow")
                    break

