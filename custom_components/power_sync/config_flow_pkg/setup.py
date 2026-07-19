"""PowerSync initial config flow (setup)."""

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


class PowerSyncConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for PowerSync."""

    VERSION = 7

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._amber_data: dict[str, Any] = {}
        self._amber_sites: list[dict[str, Any]] = []
        self._teslemetry_data: dict[str, Any] = {}
        self._tesla_sites: list[dict[str, Any]] = []
        self._site_data: dict[str, Any] = {}
        self._tesla_fleet_available: bool = False
        self._tesla_fleet_token: str | None = None
        self._selected_provider: str | None = None
        self._reauth_entry: ConfigEntry | None = None
        # Battery system selection
        self._selected_battery_system: str = BATTERY_SYSTEM_TESLA
        self._sigenergy_data: dict[str, Any] = {}
        self._sigenergy_stations: list[dict[str, Any]] = []
        self._sungrow_data: dict[str, Any] = {}  # Sungrow Modbus configuration
        self._foxess_data: dict[str, Any] = {}  # FoxESS Modbus configuration
        self._goodwe_data: dict[str, Any] = {}  # GoodWe configuration
        self._neovolt_data: dict[str, Any] = {}  # Neovolt bridge configuration
        self._solaredge_data: dict[str, Any] = {}  # SolarEdge curtailment configuration
        self._aemo_only_mode: bool = False  # True if using AEMO spike only (no Amber)
        self._aemo_data: dict[str, Any] = {}
        self._globird_data: dict[str, Any] = {}
        self._covau_data: dict[str, Any] = {}
        self._flow_power_data: dict[str, Any] = {}
        self._flow_power_sites: list[dict[str, Any]] = []
        self._flow_power_main_options: dict[str, Any] = {}
        self._octopus_data: dict[str, Any] = {}  # Octopus Energy UK configuration
        self._localvolts_data: dict[str, Any] = {}  # Localvolts configuration
        self._epex_data: dict[str, Any] = {}  # EPEX Day-Ahead (EU) configuration
        self._selected_electricity_provider: str = "amber"
        self._custom_tariff_data: dict[
            str, Any
        ] = {}  # Custom tariff for non-Amber users
        # Optimization provider selection (for Tesla/Sigenergy)
        self._optimization_provider: str = OPT_PROVIDER_NATIVE
        self._ml_options: dict[str, Any] = {}  # Smart Optimization options

    def _currency(self) -> str:
        """Return the currency for the currently selected provider."""
        return currency_for_provider(self._selected_electricity_provider, self.hass)

    def _selector_unit(self, unit_kind: str = "minor_rate") -> str:
        """Return a provider-aware unit label for setup selectors."""
        return selector_unit_for_provider(
            self._selected_electricity_provider,
            self.hass,
            unit_kind,
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step - choose battery system first."""
        # Check if already configured
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        # Electricity provider selection is the first step
        return await self.async_step_provider_selection()

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        """Handle reauthentication when the stored token is no longer valid.

        Triggered by ConfigEntryAuthFailed from the coordinator. We jump
        straight to the relevant token entry step based on which provider
        the user originally configured.
        """
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Show the reauth flow for the configured Tesla provider."""
        if self._reauth_entry is None:
            return self.async_abort(reason="reauth_failed")

        provider = self._reauth_entry.data.get(
            CONF_TESLA_API_PROVIDER, TESLA_PROVIDER_TESLEMETRY
        )

        # Route to the right token entry step based on the existing provider
        if provider == TESLA_PROVIDER_POWERSYNC:
            return await self.async_step_powersync_reauth()
        if provider == TESLA_PROVIDER_TESLEMETRY:
            return await self.async_step_teslemetry_reauth()
        # Fleet API uses the existing tesla_fleet integration's tokens — no
        # token entry needed; abort and let the user fix tesla_fleet directly
        return self.async_abort(reason="reauth_fleet_api_use_tesla_fleet")

    async def async_step_powersync_reauth(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Re-enter a PowerSync token after the existing one was invalidated."""
        errors: dict[str, str] = {}

        if user_input is not None and self._reauth_entry is not None:
            powersync_token = user_input.get(CONF_TESLEMETRY_API_TOKEN, "").strip()
            if not powersync_token:
                errors["base"] = "no_token_provided"
            else:
                validation_result = await validate_powersync_token(
                    self.hass, powersync_token
                )
                if validation_result["success"]:
                    new_data = {
                        **self._reauth_entry.data,
                        CONF_TESLEMETRY_API_TOKEN: powersync_token,
                        CONF_TESLA_API_PROVIDER: TESLA_PROVIDER_POWERSYNC,
                    }
                    self.hass.config_entries.async_update_entry(
                        self._reauth_entry, data=new_data
                    )
                    await self.hass.config_entries.async_reload(
                        self._reauth_entry.entry_id
                    )
                    return self.async_abort(reason="reauth_successful")
                errors["base"] = validation_result.get("error", "unknown")

        return self.async_show_form(
            step_id="powersync_reauth",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_TESLEMETRY_API_TOKEN): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
            description_placeholders={
                "auth_url": POWERSYNC_AUTH_START_URL,
            },
        )

    async def async_step_teslemetry_reauth(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Re-enter a Teslemetry token after the existing one was invalidated."""
        errors: dict[str, str] = {}

        if user_input is not None and self._reauth_entry is not None:
            teslemetry_token = user_input.get(CONF_TESLEMETRY_API_TOKEN, "").strip()
            if not teslemetry_token:
                errors["base"] = "no_token_provided"
            else:
                validation_result = await validate_teslemetry_token(
                    self.hass, teslemetry_token
                )
                if validation_result["success"]:
                    new_data = {
                        **self._reauth_entry.data,
                        CONF_TESLEMETRY_API_TOKEN: teslemetry_token,
                        CONF_TESLA_API_PROVIDER: TESLA_PROVIDER_TESLEMETRY,
                    }
                    self.hass.config_entries.async_update_entry(
                        self._reauth_entry, data=new_data
                    )
                    await self.hass.config_entries.async_reload(
                        self._reauth_entry.entry_id
                    )
                    return self.async_abort(reason="reauth_successful")
                errors["base"] = validation_result.get("error", "unknown")

        return self.async_show_form(
            step_id="teslemetry_reauth",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_TESLEMETRY_API_TOKEN): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_provider_selection(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle provider selection - first step in setup."""
        if user_input is not None:
            provider = user_input.get(CONF_ELECTRICITY_PROVIDER, "amber")
            self._selected_electricity_provider = provider

            if provider == "amber":
                # Amber: Need Amber API token
                self._aemo_only_mode = False
                return await self.async_step_amber()
            elif provider == "flow_power":
                # Flow Power: Configure region and price source first
                self._aemo_only_mode = False
                return await self.async_step_flow_power_setup()
            elif provider in ("globird", "aemo_vpp"):
                # Globird/AEMO VPP: AEMO spike only mode (static tariff)
                self._aemo_only_mode = True
                self._amber_data = {}
                if provider == "globird":
                    return await self.async_step_globird_plan()
                return await self.async_step_aemo_config()
            elif provider == "covau":
                self._aemo_only_mode = False
                self._amber_data = {}
                self._aemo_data = {CONF_AEMO_SPIKE_ENABLED: False}
                return await self.async_step_covau_postcode()
            elif provider == "localvolts":
                # Localvolts: Real-time wholesale pricing (Australia)
                self._aemo_only_mode = False
                self._amber_data = {}
                return await self.async_step_localvolts()
            elif provider == "octopus":
                # Octopus Energy UK: Dynamic pricing
                self._aemo_only_mode = False
                self._amber_data = {}  # No Amber API needed
                return await self.async_step_octopus()
            elif provider == "epex":
                # EPEX Day-Ahead: European dynamic pricing
                self._aemo_only_mode = False
                self._amber_data = {}
                return await self.async_step_epex()
            elif provider == "nz":
                # New Zealand TOU: Static tariff with retailer templates
                self._aemo_only_mode = True
                self._amber_data = {}
                return await self.async_step_nz_retailer()
            elif provider == "other":
                # Other/Custom TOU: collect custom rates directly.
                self._aemo_only_mode = False
                self._amber_data = {}
                self._aemo_data = {CONF_AEMO_SPIKE_ENABLED: False}
                return await self.async_step_custom_tariff()
            else:
                # Default to Amber
                self._aemo_only_mode = False
                return await self.async_step_amber()

        return self.async_show_form(
            step_id="provider_selection",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ELECTRICITY_PROVIDER, default="amber"): SelectSelector(
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

    def _covau_energy_entity_valid(self, entity_id: str | None) -> bool:
        """Return whether an entity is a monotonic cumulative energy meter."""
        if not entity_id:
            return True
        state = self.hass.states.get(entity_id)
        if state is None:
            return False
        attributes = state.attributes or {}
        state_class = str(attributes.get("state_class") or "").lower()
        device_class = str(attributes.get("device_class") or "").lower()
        unit = str(attributes.get("unit_of_measurement") or "").lower()
        return (
            state_class == "total_increasing"
            and device_class == "energy"
            and unit in {"wh", "kwh", "mwh"}
        )

    def _auto_detect_covau_energy_entity(self, direction: str) -> str:
        """Best-effort PCC meter suggestion; the user still confirms it."""
        tokens = ("import", "consumption") if direction == "import" else ("export", "feed_in", "feedin")
        candidates = []
        states = getattr(self.hass.states, "async_all", lambda: [])()
        for state in states:
            entity_id = str(getattr(state, "entity_id", "") or "")
            if self._covau_energy_entity_valid(entity_id) and any(
                token in entity_id.lower() for token in tokens
            ):
                candidates.append(entity_id)
        return sorted(candidates)[0] if candidates else ""

    async def async_step_covau_postcode(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Filter the current SolarMax family by postcode/state."""
        errors: dict[str, str] = {}
        if user_input is not None:
            postcode = str(user_input.get(CONF_COVAU_POSTCODE) or "").strip()
            candidates = covau_plan_candidates(postcode)
            if not postcode.isdigit() or len(postcode) != 4:
                errors[CONF_COVAU_POSTCODE] = "invalid_postcode"
            elif not candidates:
                errors["base"] = "covau_no_supported_plans"
            else:
                self._covau_postcode = postcode
                self._covau_candidates = candidates
                return await self.async_step_covau_plan()
        return self.async_show_form(
            step_id="covau_postcode",
            data_schema=vol.Schema({
                vol.Required(CONF_COVAU_POSTCODE): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEXT)
                )
            }),
            errors=errors,
        )

    async def async_step_covau_plan(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm distributor, exact immutable plan and settlement meters."""
        errors: dict[str, str] = {}
        candidates = getattr(self, "_covau_candidates", covau_plan_candidates(None))
        by_id = {item["plan_id"]: item for item in candidates}
        if user_input is not None:
            plan_id = str(user_input.get(CONF_COVAU_PLAN_ID) or "")
            if plan_id == "manual":
                self._covau_import_entity = user_input.get(CONF_COVAU_IMPORT_ENERGY_ENTITY) or ""
                self._covau_export_entity = user_input.get(CONF_COVAU_EXPORT_ENERGY_ENTITY) or ""
                return await self.async_step_covau_manual_tariff()
            metadata = by_id.get(plan_id)
            distributor = str(user_input.get(CONF_COVAU_DISTRIBUTOR) or "")
            import_entity = user_input.get(CONF_COVAU_IMPORT_ENERGY_ENTITY) or ""
            export_entity = user_input.get(CONF_COVAU_EXPORT_ENERGY_ENTITY) or ""
            if metadata is None:
                errors[CONF_COVAU_PLAN_ID] = "covau_unsupported_plan"
            elif distributor != metadata["distributor"]:
                errors[CONF_COVAU_DISTRIBUTOR] = "covau_distributor_mismatch"
            elif import_entity and not self._covau_energy_entity_valid(import_entity):
                errors[CONF_COVAU_IMPORT_ENERGY_ENTITY] = "covau_energy_meter_invalid"
            elif export_entity and not self._covau_energy_entity_valid(export_entity):
                errors[CONF_COVAU_EXPORT_ENERGY_ENTITY] = "covau_energy_meter_invalid"
            else:
                try:
                    raw = await async_fetch_covau_plan(self.hass, plan_id)
                    snapshot = normalize_covau_plan(raw, plan_id)
                except Exception as err:
                    _LOGGER.warning("CovaU public plan fetch failed for %s: %s", plan_id, err)
                    errors["base"] = "cannot_connect"
                else:
                    self._covau_data = {
                        CONF_COVAU_POSTCODE: getattr(self, "_covau_postcode", ""),
                        CONF_COVAU_PLAN_ID: plan_id,
                        CONF_COVAU_DISTRIBUTOR: distributor,
                        CONF_COVAU_PLAN_RAW: raw,
                        CONF_COVAU_PLAN_SNAPSHOT: snapshot.to_dict(),
                        CONF_COVAU_IMPORT_ENERGY_ENTITY: import_entity,
                        CONF_COVAU_EXPORT_ENERGY_ENTITY: export_entity,
                    }
                    return await self.async_step_battery_system()

        import_default = self._auto_detect_covau_energy_entity("import")
        export_default = self._auto_detect_covau_energy_entity("export")
        plan_options = [
            SelectOptionDict(
                value=item["plan_id"],
                label=f"{item['display_name']} — {item['distributor']}",
            )
            for item in candidates
        ] + [SelectOptionDict(value="manual", label="Manual stepped SolarMax tariff")]
        distributor_options = sorted({item["distributor"] for item in candidates})
        return self.async_show_form(
            step_id="covau_plan",
            data_schema=vol.Schema({
                vol.Required(CONF_COVAU_PLAN_ID): SelectSelector(
                    SelectSelectorConfig(options=plan_options, mode=SelectSelectorMode.DROPDOWN)
                ),
                vol.Required(CONF_COVAU_DISTRIBUTOR): SelectSelector(
                    SelectSelectorConfig(
                        options=[SelectOptionDict(value=value, label=value) for value in distributor_options],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(
                    CONF_COVAU_IMPORT_ENERGY_ENTITY,
                    description={"suggested_value": import_default} if import_default else None,
                ): EntitySelector(EntitySelectorConfig(domain="sensor")),
                vol.Optional(
                    CONF_COVAU_EXPORT_ENERGY_ENTITY,
                    description={"suggested_value": export_default} if export_default else None,
                ): EntitySelector(EntitySelectorConfig(domain="sensor")),
            }),
            errors=errors,
        )

    async def async_step_covau_manual_tariff(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Validated manual fallback for withdrawn/account-specific SolarMax plans."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                day_rate = float(user_input["day_rate_c_per_kwh"])
                snapshot = validate_manual_covau_snapshot({
                    "plan_id": user_input.get("plan_id") or "manual_covau_solarmax",
                    "display_name": user_input.get("display_name") or "Manual CovaU SolarMax",
                    "distributor": user_input.get(CONF_COVAU_DISTRIBUTOR) or "Manual",
                    "effective_date": user_input.get("effective_date") or "",
                    "supply_c_per_day": user_input["supply_c_per_day"],
                    "import_periods": [
                        {"start": "00:00", "end": "06:00", "c_per_kwh": user_input["overnight_rate_c_per_kwh"]},
                        {"start": "06:00", "end": "11:00", "c_per_kwh": day_rate},
                        {"start": "11:00", "end": "14:00", "c_per_kwh": day_rate},
                        {"start": "14:00", "end": "15:00", "c_per_kwh": day_rate},
                        {"start": "15:00", "end": "21:00", "c_per_kwh": user_input["peak_rate_c_per_kwh"]},
                        {"start": "21:00", "end": "24:00", "c_per_kwh": day_rate},
                    ],
                    "export_base_c_per_kwh": user_input["export_base_c_per_kwh"],
                    "free_import_start": "11:00",
                    "free_import_end": "14:00",
                    "free_import_cap_kwh": user_input["free_import_cap_kwh"],
                    "premium_export_start": "18:00",
                    "premium_export_end": "21:00",
                    "premium_export_cap_kwh": user_input["premium_export_cap_kwh"],
                    "premium_export_total_c_per_kwh": user_input["premium_export_total_c_per_kwh"],
                })
            except (KeyError, TypeError, ValueError) as err:
                _LOGGER.debug("Manual CovaU tariff validation failed: %s", err)
                errors["base"] = "covau_manual_tariff_invalid"
            else:
                self._covau_data = {
                    CONF_COVAU_POSTCODE: getattr(self, "_covau_postcode", ""),
                    CONF_COVAU_PLAN_ID: snapshot.plan_id,
                    CONF_COVAU_DISTRIBUTOR: snapshot.distributor,
                    CONF_COVAU_PLAN_SNAPSHOT: snapshot.to_dict(),
                    CONF_COVAU_MANUAL_TARIFF: True,
                    CONF_COVAU_IMPORT_ENERGY_ENTITY: getattr(self, "_covau_import_entity", ""),
                    CONF_COVAU_EXPORT_ENERGY_ENTITY: getattr(self, "_covau_export_entity", ""),
                }
                return await self.async_step_battery_system()

        fields: dict[Any, Any] = {
            vol.Required("plan_id", default="manual_covau_solarmax"): TextSelector(),
            vol.Required("display_name", default="Manual CovaU SolarMax"): TextSelector(),
            vol.Required(CONF_COVAU_DISTRIBUTOR): TextSelector(),
            vol.Optional("effective_date", default=""): TextSelector(),
        }
        defaults = {
            "overnight_rate_c_per_kwh": 16.5,
            "day_rate_c_per_kwh": 35.17,
            "peak_rate_c_per_kwh": 58.78,
            "supply_c_per_day": 171.996,
            "export_base_c_per_kwh": 5.0,
            "free_import_cap_kwh": 50.0,
            "premium_export_total_c_per_kwh": 15.0,
            "premium_export_cap_kwh": 30.0,
        }
        for key, default in defaults.items():
            fields[vol.Required(key, default=default)] = NumberSelector(
                NumberSelectorConfig(min=0, max=1000, step=0.001, mode=NumberSelectorMode.BOX)
            )
        return self.async_show_form(
            step_id="covau_manual_tariff",
            data_schema=vol.Schema(fields),
            errors=errors,
        )

    async def async_step_flow_power_setup(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle Flow Power setup - region and base rate only."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Apply sensible defaults for fields not shown during initial setup
            api_key = user_input.get(CONF_FLOWPOWER_API_KEY)
            user_input[CONF_FLOW_POWER_PRICE_SOURCE] = "kwatch" if api_key else "aemo"
            user_input[CONF_PEA_ENABLED] = True
            user_input[CONF_PEA_CUSTOM_VALUE] = None
            user_input[CONF_NETWORK_USE_MANUAL_RATES] = False
            user_input[CONF_AUTO_SYNC_ENABLED] = True
            user_input[CONF_BATTERY_CURTAILMENT_ENABLED] = False

            # Store Flow Power configuration — tariff collected in next step
            self._flow_power_data = user_input

            # AEMO Direct is the default - no Amber API needed
            self._amber_data = {}
            self._aemo_only_mode = False

            if api_key:
                validation_result = await validate_flow_power_api_key(
                    self.hass,
                    api_key,
                    user_input.get(CONF_FLOW_POWER_STATE, "NSW1"),
                )
                if not validation_result["success"]:
                    errors["base"] = validation_result.get("error", "cannot_connect")
                else:
                    self._flow_power_sites = validation_result.get("sites", [])
                    if len(self._flow_power_sites) == 1:
                        site = self._flow_power_sites[0]
                        self._flow_power_data[CONF_FLOWPOWER_NMI] = site["nmi"]
                        await _prefill_flow_power_network_tariff(
                            self.hass,
                            self._flow_power_data,
                            site,
                        )
                        return await self.async_step_flow_power_tariff()
                    if self._flow_power_sites:
                        return await self.async_step_flow_power_site()

            # Route to tariff selection (region-filtered)
            if not errors:
                return await self.async_step_flow_power_tariff()

        return self.async_show_form(
            step_id="flow_power_setup",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_FLOW_POWER_STATE, default="NSW1"): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(value=k, label=v)
                                for k, v in FLOW_POWER_STATES.items()
                            ],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Required(
                        CONF_FLOW_POWER_BASE_RATE, default=FLOW_POWER_DEFAULT_BASE_RATE
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0.0,
                            max=100.0,
                            step=0.01,
                            unit_of_measurement=self._selector_unit(),
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Optional(CONF_FLOWPOWER_API_KEY): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_flow_power_site(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Select the Flow Power residential site for a KWatch API key."""
        sites = getattr(self, "_flow_power_sites", [])
        errors: dict[str, str] = {}

        if user_input is not None:
            selected_nmi = user_input.get(CONF_FLOWPOWER_NMI)
            site = next((item for item in sites if item.get("nmi") == selected_nmi), None)
            if site:
                self._flow_power_data[CONF_FLOWPOWER_NMI] = selected_nmi
                await _prefill_flow_power_network_tariff(
                    self.hass,
                    self._flow_power_data,
                    site,
                )
                return await self.async_step_flow_power_tariff()
            errors["base"] = "invalid_site"

        return self.async_show_form(
            step_id="flow_power_site",
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

    async def async_step_flow_power_tariff(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Select network tariff (region-filtered) for the v2 PEA formula."""
        errors: dict[str, str] = {}
        region = self._flow_power_data.get(CONF_FLOW_POWER_STATE, "NSW1")

        if user_input is not None:
            combined = user_input.get("fp_network_tariff_combined", "")
            if combined and ":" in combined:
                fp_network, fp_tariff_code = combined.split(":", 1)
                self._flow_power_data[CONF_FP_NETWORK] = fp_network
                self._flow_power_data[CONF_FP_TARIFF_CODE] = fp_tariff_code
                api_name = NETWORK_API_NAME.get(fp_network, fp_network.lower())
                self._flow_power_data[CONF_NETWORK_DISTRIBUTOR] = api_name
                self._flow_power_data[CONF_NETWORK_TARIFF_CODE] = fp_tariff_code
            else:
                self._flow_power_data[CONF_FP_NETWORK] = ""
                self._flow_power_data[CONF_FP_TARIFF_CODE] = ""
                self._flow_power_data.pop(CONF_NETWORK_DISTRIBUTOR, None)
                self._flow_power_data.pop(CONF_NETWORK_TARIFF_CODE, None)

            if self._flow_power_data.get(CONF_FLOWPOWER_API_KEY):
                return await self.async_step_battery_system()
            return await self.async_step_flow_power_portal()

        # Build combined network+tariff dropdown for the region — all options loaded at render time
        from ..tariff_utils import get_tariff_codes_for_network
        region_network_names = REGION_NETWORKS.get(region, [])
        fp_combined_options: dict[str, str] = {"": "None (use simple formula)"}
        for network_name in region_network_names:
            codes = await self.hass.async_add_executor_job(
                get_tariff_codes_for_network, network_name
            )
            for code, desc in codes.items():
                fp_combined_options[f"{network_name}:{code}"] = f"{network_name} — {desc}"

        stored_network = self._flow_power_data.get(CONF_FP_NETWORK, "")
        stored_tariff = self._flow_power_data.get(CONF_FP_TARIFF_CODE, "")
        current_combined = f"{stored_network}:{stored_tariff}" if (stored_network and stored_tariff) else ""
        if current_combined not in fp_combined_options:
            current_combined = ""

        return self.async_show_form(
            step_id="flow_power_tariff",
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
                }
            ),
            errors=errors,
        )

    async def async_step_flow_power_portal(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Offer Flow Power portal connection during initial setup."""
        if user_input is not None:
            if user_input.get("connect_portal", True):
                return await self.async_step_flow_power_portal_login()
            return await self.async_step_battery_system()

        return self.async_show_form(
            step_id="flow_power_portal",
            data_schema=vol.Schema(
                {
                    vol.Optional("connect_portal", default=True): BooleanSelector(),
                }
            ),
        )

    async def async_step_flow_power_portal_login(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Authenticate with the Flow Power portal during initial setup."""
        errors: dict[str, str] = {}

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
                        return await self.async_step_flow_power_portal_mfa()
                    errors["base"] = "cannot_connect"
                except ValueError:
                    errors["base"] = "invalid_credentials"
                except Exception as err:
                    _LOGGER.exception("Flow Power portal login failed during setup: %s", err)
                    errors["base"] = "cannot_connect"
                    if getattr(self, "_fp_client", None) is not None:
                        await self._fp_client.close()
                    self._fp_client = None
            else:
                errors["base"] = "invalid_credentials"

        return self.async_show_form(
            step_id="flow_power_portal_login",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_FLOWPOWER_EMAIL): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.EMAIL)
                    ),
                    vol.Required(CONF_FLOWPOWER_PASSWORD): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_flow_power_portal_mfa(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Verify Flow Power SMS MFA during initial setup."""
        errors: dict[str, str] = {}

        if user_input is not None:
            code = user_input.get("mfa_code", "")
            if code and hasattr(self, "_fp_client"):
                success = await self._fp_client.verify_mfa(code)
                if success:
                    self._flow_power_data[CONF_FLOWPOWER_EMAIL] = self._fp_email
                    self._flow_power_data[CONF_FLOWPOWER_PASSWORD] = self._fp_password
                    self.hass.data.setdefault(DOMAIN, {})
                    self.hass.data[DOMAIN]["_pending_fp_client"] = self._fp_client
                    return await self.async_step_battery_system()
                errors["base"] = "invalid_mfa_code"
            else:
                errors["base"] = "invalid_mfa_code"

        return self.async_show_form(
            step_id="flow_power_portal_mfa",
            data_schema=vol.Schema(
                {
                    vol.Required("mfa_code"): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.TEXT)
                    ),
                }
            ),
            errors=errors,
        )

    async def _route_to_battery_setup(self) -> FlowResult:
        """Route to battery system setup based on selection."""
        if self._selected_battery_system == BATTERY_SYSTEM_SIGENERGY:
            return await self.async_step_sigenergy_credentials()
        elif self._selected_battery_system == BATTERY_SYSTEM_SUNGROW:
            return await self.async_step_sungrow()
        elif self._selected_battery_system == BATTERY_SYSTEM_FOXESS:
            return await self.async_step_foxess_connection()
        elif self._selected_battery_system == BATTERY_SYSTEM_GOODWE:
            return await self.async_step_goodwe_connection()
        elif self._selected_battery_system == BATTERY_SYSTEM_ALPHAESS:
            return await self.async_step_alphaess_modbus()
        elif self._selected_battery_system == BATTERY_SYSTEM_ESY_SUNHOME:
            return await self.async_step_esy_sunhome()
        elif self._selected_battery_system == BATTERY_SYSTEM_SOLAX:
            return await self.async_step_solax_battery()
        elif self._selected_battery_system == BATTERY_SYSTEM_SAJ_H2:
            return await self.async_step_saj_h2_battery()
        elif self._selected_battery_system == BATTERY_SYSTEM_FRONIUS_RESERVA:
            return await self.async_step_fronius_reserva_battery()
        elif self._selected_battery_system == BATTERY_SYSTEM_NEOVOLT:
            return await self.async_step_neovolt_battery()
        elif self._selected_battery_system == BATTERY_SYSTEM_SOLAREDGE:
            return await self.async_step_solaredge()
        elif self._selected_battery_system == BATTERY_SYSTEM_ANKER_SOLIX:
            return await self.async_step_anker_solix()
        elif self._selected_battery_system == BATTERY_SYSTEM_CUSTOM:
            return await self.async_step_custom_battery()
        else:
            return await self.async_step_tesla_provider()

    def _create_final_entry(self) -> FlowResult:
        """Create final config entry after battery connection is established.

        Merges all collected data and creates the entry. Fine-tuning
        (curtailment, weather, demand charges, EV, inverter config, etc.)
        is done via the options flow or mobile app.
        """
        data = {
            **self._amber_data,
            **self._teslemetry_data,
            **self._site_data,
            **self._aemo_data,
            **self._globird_data,
            **self._covau_data,
            **self._flow_power_data,
            **self._octopus_data,
            **self._localvolts_data,
            **self._epex_data,
            **getattr(self, "_sigenergy_data", {}),
            **getattr(self, "_sungrow_data", {}),
            **getattr(self, "_foxess_data", {}),
            **getattr(self, "_goodwe_data", {}),
            **getattr(self, "_alphaess_data", {}),
            **getattr(self, "_esy_sunhome_data", {}),
            **getattr(self, "_solax_data", {}),
            **getattr(self, "_saj_h2_data", {}),
            **getattr(self, "_fronius_reserva_data", {}),
            **getattr(self, "_neovolt_data", {}),
            **getattr(self, "_solaredge_data", {}),
            **getattr(self, "_anker_solix_data", {}),
            **getattr(self, "_custom_battery_data", {}),
            CONF_ELECTRICITY_PROVIDER: self._selected_electricity_provider,
        }

        # Set battery system type
        if self._selected_battery_system:
            data[CONF_BATTERY_SYSTEM] = self._selected_battery_system

        # Include custom tariff data if configured
        if self._custom_tariff_data:
            data["initial_custom_tariff"] = self._custom_tariff_data

        # Include NZ config if set
        if hasattr(self, "_nz_config"):
            data.update(self._nz_config)

        # Include optimization provider selection
        data[CONF_OPTIMIZATION_PROVIDER] = self._optimization_provider
        if self._ml_options:
            data.update(self._ml_options)

        # Tesla EV API provider (chosen during async_step_tesla_provider).
        # Defaults to "none" so non-Tesla setups stay clean.
        ev_provider_choice = getattr(
            self, "_tesla_ev_provider", TESLA_EV_API_PROVIDER_NONE
        )
        data[CONF_TESLA_EV_API_PROVIDER] = ev_provider_choice
        ev_token = getattr(self, "_tesla_ev_teslemetry_token", None)
        if ev_token:
            data[CONF_TESLA_EV_TELEMETRY_TOKEN] = ev_token

        # Set appropriate title based on battery system and provider
        battery_label = {
            BATTERY_SYSTEM_SIGENERGY: "Sigenergy",
            BATTERY_SYSTEM_SUNGROW: "Sungrow",
            BATTERY_SYSTEM_FOXESS: "FoxESS",
            BATTERY_SYSTEM_GOODWE: "GoodWe",
            BATTERY_SYSTEM_ALPHAESS: "AlphaESS",
            BATTERY_SYSTEM_ESY_SUNHOME: "ESY Sunhome",
            BATTERY_SYSTEM_SOLAX: "Solax",
            BATTERY_SYSTEM_SAJ_H2: "SAJ H2",
            BATTERY_SYSTEM_FRONIUS_RESERVA: "Fronius GEN24 storage",
            BATTERY_SYSTEM_NEOVOLT: "Neovolt",
            BATTERY_SYSTEM_SOLAREDGE: "SolarEdge",
            BATTERY_SYSTEM_ANKER_SOLIX: "Anker Solix",
            BATTERY_SYSTEM_CUSTOM: "Custom",
        }.get(self._selected_battery_system, "")

        if battery_label:
            title = f"PowerSync - {battery_label}"
        elif self._aemo_only_mode:
            title = "PowerSync Globird"
        elif self._selected_electricity_provider == "flow_power":
            title = "PowerSync Flow Power"
        elif self._selected_electricity_provider == "covau":
            title = "PowerSync CovaU SolarMax"
        elif self._selected_electricity_provider == "localvolts":
            title = "PowerSync Localvolts"
        elif self._selected_electricity_provider == "octopus":
            title = "PowerSync Octopus"
        elif self._selected_electricity_provider == "other":
            title = "PowerSync Custom TOU"
        else:
            title = "PowerSync Amber"

        return self.async_create_entry(title=title, data=data)

    async def async_step_octopus(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle Octopus Energy UK configuration."""
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
                # Store Octopus data
                self._octopus_data = {
                    CONF_OCTOPUS_PRODUCT: product_key,
                    CONF_OCTOPUS_REGION: region,
                    CONF_OCTOPUS_PRODUCT_CODE: product_code,
                    CONF_OCTOPUS_TARIFF_CODE: tariff_code,
                    CONF_OCTOPUS_EXPORT_PRODUCT_CODE: export_product_code,
                    CONF_OCTOPUS_EXPORT_TARIFF_CODE: export_tariff_code,
                }

                _LOGGER.info(
                    "Octopus tariff validated: product=%s, tariff=%s, region=%s",
                    product_code,
                    tariff_code,
                    region,
                )

                # Route to battery system selection
                return await self.async_step_battery_system()

        # Build form schema
        data_schema = vol.Schema(
            {
                vol.Required(CONF_OCTOPUS_PRODUCT, default="agile"): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=k, label=v)
                            for k, v in OCTOPUS_PRODUCTS.items()
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(CONF_OCTOPUS_REGION, default="C"): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=k, label=v)
                            for k, v in OCTOPUS_GSP_REGIONS.items()
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="octopus",
            data_schema=data_schema,
            errors=errors,
            description_placeholders={
                "octopus_url": "https://octopus.energy/smart/agile/",
            },
        )

    async def async_step_amber(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle Amber API token entry."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Validate Amber API token
            validation_result = await validate_amber_token(
                self.hass, user_input[CONF_AMBER_API_TOKEN]
            )

            if validation_result["success"]:
                self._amber_data = user_input
                self._amber_sites = validation_result.get("sites", [])
                # For non-Tesla batteries, select the Amber site
                if (
                    self._selected_battery_system != BATTERY_SYSTEM_TESLA
                    and self._amber_sites
                ):
                    active_sites = [
                        s for s in self._amber_sites if s.get("status") == "active"
                    ]
                    # Always show site picker so user can confirm/change the NMI
                    return await self.async_step_amber_site_selection()
                # Route to battery system selection
                return await self.async_step_battery_system()
            else:
                errors["base"] = validation_result.get("error", "unknown")

        data_schema = vol.Schema(
            {
                vol.Required(CONF_AMBER_API_TOKEN): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
            }
        )

        return self.async_show_form(
            step_id="amber",
            data_schema=data_schema,
            errors=errors,
            description_placeholders={
                "amber_url": "https://app.amber.com.au/developers",
            },
        )

    async def async_step_amber_site_selection(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle Amber site selection for non-Tesla users with multiple sites."""
        errors: dict[str, str] = {}

        if user_input is not None:
            amber_site_id = user_input.get(CONF_AMBER_SITE_ID)
            if not amber_site_id:
                errors["base"] = "no_site_selected"
            else:
                self._site_data[CONF_AMBER_SITE_ID] = amber_site_id
                self._site_data.setdefault(CONF_AUTO_SYNC_ENABLED, True)
                self._site_data.setdefault(CONF_AMBER_FORECAST_TYPE, "predicted")
                return await self.async_step_battery_system()

        amber_site_list: list[SelectOptionDict] = []
        default_amber_site = None
        for site in self._amber_sites:
            site_id = site["id"]
            site_nmi = site.get("nmi", site_id)
            site_status = site.get("status", "unknown")
            if site_status == "active":
                label = f"{site_nmi} (Active)"
                if default_amber_site is None:
                    default_amber_site = site_id
            elif site_status == "closed":
                label = f"{site_nmi} (Closed)"
            else:
                label = f"{site_nmi} ({site_status})"
            amber_site_list.append(SelectOptionDict(value=site_id, label=label))

        data_schema = vol.Schema({
            vol.Required(CONF_AMBER_SITE_ID, default=default_amber_site): SelectSelector(
                SelectSelectorConfig(
                    options=amber_site_list,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
        })

        return self.async_show_form(
            step_id="amber_site_selection",
            data_schema=data_schema,
            errors=errors,
        )

    async def async_step_epex(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle EPEX Day-Ahead (EU) configuration."""
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

            # Validate by fetching prices from EPEX API
            try:
                from ..epex_api import EPEXAPIClient

                client = EPEXAPIClient(async_get_clientsession(self.hass))
                valid = await client.validate_region(region)

                if not valid:
                    errors["base"] = "no_prices"
                    _LOGGER.error("No EPEX prices found for region %s", region)
            except Exception as err:
                errors["base"] = "cannot_connect"
                _LOGGER.exception("Error validating EPEX region: %s", err)

            if not errors:
                self._epex_data = {
                    CONF_EPEX_REGION: region,
                    CONF_EPEX_SURCHARGE: surcharge,
                    CONF_EPEX_TAX_PERCENT: tax_percent,
                    CONF_EPEX_EXPORT_RATE: export_rate,
                }
                if import_price_entity:
                    self._epex_data[CONF_EPEX_IMPORT_PRICE_ENTITY] = (
                        import_price_entity
                    )
                if export_price_entity:
                    self._epex_data[CONF_EPEX_EXPORT_PRICE_ENTITY] = export_price_entity

                _LOGGER.info(
                    "EPEX config validated: region=%s, surcharge=%.1f ct, tax=%.1f%%, export=%.1f ct, import_entity=%s, export_entity=%s",
                    region,
                    surcharge,
                    tax_percent,
                    export_rate,
                    import_price_entity or "none",
                    export_price_entity or "none",
                )

                # Route to battery system selection
                return await self.async_step_battery_system()

        data_schema = vol.Schema(
            {
                vol.Required(CONF_EPEX_REGION, default="DE"): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=k, label=v)
                            for k, v in EPEX_REGIONS.items()
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(CONF_EPEX_SURCHARGE, default=0.0): NumberSelector(
                    NumberSelectorConfig(
                        min=0, max=50, step=0.1, unit_of_measurement="ct/kWh",
                    )
                ),
                vol.Optional(CONF_EPEX_TAX_PERCENT, default=0.0): NumberSelector(
                    NumberSelectorConfig(
                        min=0, max=50, step=0.5, unit_of_measurement="%",
                    )
                ),
                vol.Optional(CONF_EPEX_EXPORT_RATE, default=0.0): NumberSelector(
                    NumberSelectorConfig(
                        min=0, max=50, step=0.1, unit_of_measurement="ct/kWh",
                    )
                ),
                vol.Optional(CONF_EPEX_IMPORT_PRICE_ENTITY): EntitySelector(
                    EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(CONF_EPEX_EXPORT_PRICE_ENTITY): EntitySelector(
                    EntitySelectorConfig(domain="sensor")
                ),
            }
        )

        return self.async_show_form(
            step_id="epex",
            data_schema=data_schema,
            errors=errors,
        )

    async def async_step_localvolts(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle Localvolts API configuration."""
        errors: dict[str, str] = {}

        if user_input is not None:
            validation = await validate_localvolts_credentials(
                self.hass,
                user_input[CONF_LOCALVOLTS_API_KEY],
                user_input[CONF_LOCALVOLTS_PARTNER_ID],
                user_input[CONF_LOCALVOLTS_NMI],
            )
            if validation["success"]:
                self._localvolts_data = user_input
                # Route to battery system selection
                return await self.async_step_battery_system()
            else:
                errors["base"] = validation.get("error", "cannot_connect")

        data_schema = vol.Schema(
            {
                vol.Required(CONF_LOCALVOLTS_API_KEY): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
                vol.Required(CONF_LOCALVOLTS_PARTNER_ID): TextSelector(),
                vol.Required(CONF_LOCALVOLTS_NMI): TextSelector(),
            }
        )

        return self.async_show_form(
            step_id="localvolts",
            data_schema=data_schema,
            errors=errors,
        )

    async def async_step_battery_system(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Let user choose battery system - Tesla or Sigenergy (first step)."""
        if user_input is not None:
            self._selected_battery_system = user_input.get(
                CONF_BATTERY_SYSTEM, BATTERY_SYSTEM_TESLA
            )

            if self._selected_battery_system == BATTERY_SYSTEM_CUSTOM:
                self._optimization_provider = OPT_PROVIDER_POWERSYNC
                return await self.async_step_custom_battery()

            # Keep setup and post-setup optimization pages aligned.
            return await self.async_step_ml_options()

        return self.async_show_form(
            step_id="battery_system",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_BATTERY_SYSTEM, default=BATTERY_SYSTEM_TESLA
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(value=k, label=v)
                                for k, v in BATTERY_SYSTEMS.items()
                            ],
                            # Keep this as a dropdown so newer battery systems
                            # do not get pushed below the fold in the setup UI.
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
        )

    async def async_step_custom_battery(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure a planner-only custom battery system using HA entities."""
        default_capacity_wh, default_charge_w, default_discharge_w = (
            _default_optimizer_specs_for(BATTERY_SYSTEM_CUSTOM)
        )
        default_capacity_kwh = default_capacity_wh / 1000
        default_charge_kw = default_charge_w / 1000
        default_discharge_kw = default_discharge_w / 1000

        if user_input is not None:
            self._custom_battery_data = {
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
            }
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
            self._optimization_provider = OPT_PROVIDER_POWERSYNC
            self._ml_options.update(
                {
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
            )
            if max_grid_export_w is not None:
                self._ml_options[CONF_OPTIMIZATION_MAX_GRID_EXPORT_W] = (
                    max_grid_export_w
                )
            return self._create_final_entry()

        return self.async_show_form(
            step_id="custom_battery",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CUSTOM_BATTERY_LEVEL_ENTITY
                    ): EntitySelector(EntitySelectorConfig(domain="sensor")),
                    vol.Required(
                        CONF_CUSTOM_BATTERY_POWER_ENTITY
                    ): EntitySelector(EntitySelectorConfig(domain="sensor")),
                    vol.Required(
                        CONF_CUSTOM_GRID_POWER_ENTITY
                    ): EntitySelector(EntitySelectorConfig(domain="sensor")),
                    vol.Required(
                        CONF_CUSTOM_SOLAR_POWER_ENTITY
                    ): EntitySelector(EntitySelectorConfig(domain="sensor")),
                    vol.Required(
                        CONF_CUSTOM_LOAD_POWER_ENTITY
                    ): EntitySelector(EntitySelectorConfig(domain="sensor")),
                    vol.Required(
                        CONF_OPTIMIZATION_BACKUP_RESERVE,
                        default=int(DEFAULT_OPTIMIZATION_BACKUP_RESERVE * 100),
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
                        default=default_capacity_kwh,
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
                        default=default_charge_kw,
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
                        default=default_discharge_kw,
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
                        default=0,
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
                        default=True,
                    ): BooleanSelector(),
                }
            ),
        )

    async def async_step_optimization_provider(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Let user choose optimization provider - native battery or Smart Optimization."""
        errors: dict[str, str] = {}

        if user_input is not None:
            provider = user_input.get(CONF_OPTIMIZATION_PROVIDER, OPT_PROVIDER_NATIVE)
            self._optimization_provider = provider

            if provider == OPT_PROVIDER_POWERSYNC:
                return await self.async_step_ml_options()
            else:
                # User wants native battery optimization - proceed to battery connection
                return await self._route_to_battery_setup()

        # Get the native optimization name based on battery system
        native_name = OPTIMIZATION_PROVIDER_NATIVE_NAMES.get(
            self._selected_battery_system, "Battery"
        )

        providers = _optimization_provider_options_for_battery(
            self._selected_battery_system
        )

        return self.async_show_form(
            step_id="optimization_provider",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_OPTIMIZATION_PROVIDER, default=OPT_PROVIDER_POWERSYNC
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(value=k, label=v)
                                for k, v in providers.items()
                            ],
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                }
            ),
            errors=errors,
            description_placeholders={
                "battery_name": native_name,
            },
        )

    async def async_step_ml_options(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure Smart Optimization options."""
        battery_system = self._selected_battery_system or BATTERY_SYSTEM_TESLA
        is_tesla = battery_system == BATTERY_SYSTEM_TESLA
        supports_no_idle_mode = supports_no_idle_mode_provider(
            self._selected_electricity_provider
        )
        default_capacity_wh, default_charge_w, default_discharge_w = (
            _default_optimizer_specs_for(battery_system)
        )
        default_capacity_kwh = default_capacity_wh / 1000
        default_charge_kw = default_charge_w / 1000
        default_discharge_kw = default_discharge_w / 1000

        if user_input is not None:
            optimization_provider = user_input.get(
                CONF_OPTIMIZATION_PROVIDER,
                OPT_PROVIDER_POWERSYNC,
            )
            self._optimization_provider = optimization_provider
            self._ml_options = {
                CONF_MONITORING_MODE: bool(
                    user_input.get(CONF_MONITORING_MODE, False)
                )
            }
            if optimization_provider == OPT_PROVIDER_POWERSYNC:
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
                auto_apply_reserve_enabled = bool(
                    user_input.get(CONF_OPTIMIZATION_AUTO_APPLY_RESERVE, False)
                )
                disable_idle = (
                    bool(user_input.get(CONF_OPTIMIZATION_DISABLE_IDLE, False))
                    if supports_no_idle_mode
                    else False
                )
                backup_reserve = (
                    user_input.get(
                        CONF_OPTIMIZATION_BACKUP_RESERVE,
                        int(DEFAULT_OPTIMIZATION_BACKUP_RESERVE * 100),
                    )
                    / 100.0
                )
                self._ml_options.update({
                    CONF_OPTIMIZATION_ENABLED: bool(
                        user_input.get(CONF_OPTIMIZATION_ENABLED, True)
                    ),
                    CONF_OPTIMIZATION_AUTO_APPLY_RESERVE: auto_apply_reserve_enabled,
                    CONF_OPTIMIZATION_MANUAL_RESERVE: backup_reserve,
                    CONF_OPTIMIZATION_EV_INTEGRATION: bool(
                        user_input.get(CONF_OPTIMIZATION_EV_INTEGRATION, False)
                    ),
                    CONF_OPTIMIZATION_LOAD_ENTITY: (
                        _normalize_optional_entity(
                            user_input.get(CONF_OPTIMIZATION_LOAD_ENTITY)
                        )
                    ),
                    CONF_OPTIMIZATION_PLANNED_EV_LOAD_ENTITY: (
                        _normalize_optional_entity(
                            user_input.get(CONF_OPTIMIZATION_PLANNED_EV_LOAD_ENTITY)
                        )
                    ),
                    CONF_OPTIMIZATION_COST_FUNCTION: COST_FUNCTION_COST,
                    CONF_OPTIMIZATION_BACKUP_RESERVE: backup_reserve,
                    CONF_HARDWARE_BACKUP_RESERVE: user_input.get(
                        CONF_HARDWARE_BACKUP_RESERVE,
                        int(DEFAULT_OPTIMIZATION_BACKUP_RESERVE * 100),
                    )
                    / 100.0,
                    CONF_OPTIMIZATION_BATTERY_CAPACITY_WH: _form_kwh_to_wh(
                        user_input.get(CONF_OPTIMIZATION_BATTERY_CAPACITY_WH),
                        default_capacity_kwh,
                    ),
                    CONF_OPTIMIZATION_MAX_CHARGE_W: _form_kw_to_w(
                        user_input.get(CONF_OPTIMIZATION_MAX_CHARGE_W),
                        default_charge_kw,
                    ),
                    CONF_OPTIMIZATION_MAX_DISCHARGE_W: _form_kw_to_w(
                        user_input.get(CONF_OPTIMIZATION_MAX_DISCHARGE_W),
                        default_discharge_kw,
                    ),
                    CONF_OPTIMIZATION_MAX_GRID_IMPORT_W: _form_kw_to_w(
                        user_input.get(CONF_OPTIMIZATION_MAX_GRID_IMPORT_W),
                        0,
                    ),
                    CONF_OPTIMIZATION_MAX_GRID_CHARGE_PRICE: (
                        _form_optional_cents_to_price(
                            user_input.get(CONF_OPTIMIZATION_MAX_GRID_CHARGE_PRICE)
                        )
                    ),
                    CONF_OPTIMIZATION_GRID_CHARGE_SOC_CAP: _form_percent_to_ratio(
                        user_input.get(CONF_OPTIMIZATION_GRID_CHARGE_SOC_CAP),
                        1.0,
                    ),
                    CONF_OPTIMIZATION_ALLOW_GRID_CHARGE: user_input.get(
                        CONF_OPTIMIZATION_ALLOW_GRID_CHARGE,
                        True,
                    ),
                    CONF_OPTIMIZATION_SPREAD_EXPORT_ENABLED: spread_export_enabled,
                    CONF_OPTIMIZATION_SPREAD_IMPORT_ENABLED: spread_import_enabled,
                    CONF_OPTIMIZATION_DISABLE_IDLE: disable_idle,
                    CONF_PROFIT_MAX_ENABLED: bool(
                        user_input.get(CONF_PROFIT_MAX_ENABLED, False)
                    ),
                    CONF_CHARGE_BY_TIME_ENABLED: bool(
                        user_input.get(CONF_CHARGE_BY_TIME_ENABLED, False)
                    ),
                    CONF_CHARGE_BY_TIME_TARGET_TIME: user_input.get(
                        CONF_CHARGE_BY_TIME_TARGET_TIME,
                        DEFAULT_CHARGE_BY_TIME_TARGET_TIME,
                    ),
                    CONF_CHARGE_BY_TIME_TARGET_SOC: _form_percent_to_ratio(
                        user_input.get(CONF_CHARGE_BY_TIME_TARGET_SOC),
                        DEFAULT_CHARGE_BY_TIME_TARGET_SOC,
                    ),
                })
                self._ml_options[CONF_PROFIT_MAX_TARGET_TIME] = self._ml_options[
                    CONF_CHARGE_BY_TIME_TARGET_TIME
                ]
                self._ml_options[CONF_PROFIT_MAX_TARGET_SOC] = self._ml_options[
                    CONF_CHARGE_BY_TIME_TARGET_SOC
                ]
                max_grid_export_w = _form_optional_kw_to_w(
                    user_input.get(CONF_OPTIMIZATION_MAX_GRID_EXPORT_W)
                )
                if max_grid_export_w is not None:
                    self._ml_options[CONF_OPTIMIZATION_MAX_GRID_EXPORT_W] = (
                        max_grid_export_w
                    )
            # Proceed to battery connection setup
            return await self._route_to_battery_setup()

        opt_providers = _optimization_provider_options_for_battery(battery_system)
        schema_fields: dict[Any, Any] = {
            vol.Required(
                CONF_OPTIMIZATION_PROVIDER,
                default=OPT_PROVIDER_POWERSYNC,
            ): SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(value=k, label=v)
                        for k, v in opt_providers.items()
                    ],
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(
                CONF_OPTIMIZATION_ENABLED,
                default=True,
            ): BooleanSelector(),
            vol.Required(
                CONF_OPTIMIZATION_AUTO_APPLY_RESERVE,
                default=False,
            ): BooleanSelector(),
            vol.Required(
                CONF_OPTIMIZATION_EV_INTEGRATION,
                default=False,
            ): BooleanSelector(),
            vol.Optional(
                CONF_OPTIMIZATION_LOAD_ENTITY,
            ): EntitySelector(EntitySelectorConfig(domain="sensor")),
            vol.Optional(
                CONF_OPTIMIZATION_PLANNED_EV_LOAD_ENTITY,
            ): EntitySelector(EntitySelectorConfig(domain="sensor")),
            vol.Required(
                CONF_MONITORING_MODE,
                default=False,
            ): BooleanSelector(),
            vol.Required(
                CONF_OPTIMIZATION_BACKUP_RESERVE,
                default=int(DEFAULT_OPTIMIZATION_BACKUP_RESERVE * 100),
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
                CONF_HARDWARE_BACKUP_RESERVE,
                default=int(DEFAULT_OPTIMIZATION_BACKUP_RESERVE * 100),
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
                default=default_capacity_kwh,
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
                default=default_charge_kw,
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
                default=default_discharge_kw,
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
                default=0,
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
                default=True,
            ): BooleanSelector(),
            vol.Required(
                CONF_OPTIMIZATION_MAX_GRID_CHARGE_PRICE,
                default=0,
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0,
                    max=200,
                    step=0.1,
                    unit_of_measurement="c/kWh",
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_OPTIMIZATION_GRID_CHARGE_SOC_CAP,
                default=100,
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0,
                    max=100,
                    step=1,
                    unit_of_measurement="%",
                    mode=NumberSelectorMode.SLIDER,
                )
            ),
        }
        if not is_tesla:
            schema_fields.update({
                vol.Required(
                    CONF_OPTIMIZATION_SPREAD_EXPORT_ENABLED,
                    default=False,
                ): BooleanSelector(),
                vol.Required(
                    CONF_OPTIMIZATION_SPREAD_IMPORT_ENABLED,
                    default=False,
                ): BooleanSelector(),
            })
        if supports_no_idle_mode:
            schema_fields[
                vol.Required(
                    CONF_OPTIMIZATION_DISABLE_IDLE,
                    default=False,
                )
            ] = BooleanSelector()
        schema_fields.update({
            vol.Required(
                CONF_PROFIT_MAX_ENABLED,
                default=False,
            ): BooleanSelector(),
            vol.Required(
                CONF_CHARGE_BY_TIME_ENABLED,
                default=False,
            ): BooleanSelector(),
            vol.Required(
                CONF_CHARGE_BY_TIME_TARGET_TIME,
                default=DEFAULT_CHARGE_BY_TIME_TARGET_TIME,
            ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
            vol.Required(
                CONF_CHARGE_BY_TIME_TARGET_SOC,
                default=int(DEFAULT_CHARGE_BY_TIME_TARGET_SOC * 100),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0,
                    max=100,
                    step=1,
                    unit_of_measurement="%",
                    mode=NumberSelectorMode.SLIDER,
                )
            ),
        })

        return self.async_show_form(
            step_id="ml_options",
            data_schema=vol.Schema(schema_fields),
            description_placeholders={},
        )

    async def async_step_sigenergy_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle Sigenergy credential entry.

        Supports both plain password (recommended) and pre-encoded pass_enc (advanced).
        If plain password is provided, it's encoded automatically.
        """
        from ..sigenergy_api import encode_sigenergy_password

        errors: dict[str, str] = {}

        if user_input is not None:
            username = user_input.get(CONF_SIGENERGY_USERNAME, "").strip()
            plain_password = user_input.get(CONF_SIGENERGY_PASSWORD, "").strip()
            pass_enc = user_input.get(CONF_SIGENERGY_PASS_ENC, "").strip()
            device_id = user_input.get(CONF_SIGENERGY_DEVICE_ID, "").strip()
            cloud_region = user_input.get(
                CONF_SIGENERGY_CLOUD_REGION,
                DEFAULT_SIGENERGY_CLOUD_REGION,
            )

            # Determine which password to use
            # Priority: pass_enc (explicit override) > password (encode it)
            if pass_enc:
                # Advanced user provided pre-encoded password
                final_pass_enc = pass_enc
            elif plain_password:
                # Normal user provided plain password - encode it
                final_pass_enc = encode_sigenergy_password(plain_password)
            else:
                final_pass_enc = ""

            if not username or not final_pass_enc:
                errors["base"] = "missing_credentials"
            elif device_id and (len(device_id) != 13 or not device_id.isdigit()):
                errors["base"] = "invalid_device_id"
            else:
                # Validate credentials
                validation_result = await validate_sigenergy_credentials(
                    self.hass, username, final_pass_enc, device_id, cloud_region
                )

                if validation_result["success"]:
                    self._sigenergy_data = {
                        CONF_SIGENERGY_USERNAME: username,
                        CONF_SIGENERGY_PASS_ENC: final_pass_enc,  # Always store encoded
                        CONF_SIGENERGY_DEVICE_ID: device_id,
                        CONF_SIGENERGY_CLOUD_REGION: cloud_region,
                        CONF_SIGENERGY_ACCESS_TOKEN: validation_result.get(
                            "access_token"
                        ),
                        CONF_SIGENERGY_REFRESH_TOKEN: validation_result.get(
                            "refresh_token"
                        ),
                        CONF_SIGENERGY_TOKEN_EXPIRES_AT: validation_result.get(
                            "expires_at"
                        ),
                    }
                    self._sigenergy_stations = validation_result.get("stations", [])
                    return await self.async_step_sigenergy_station()
                else:
                    errors["base"] = validation_result.get("error", "unknown")

        return self.async_show_form(
            step_id="sigenergy_credentials",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SIGENERGY_USERNAME): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.TEXT)
                    ),
                    vol.Required(CONF_SIGENERGY_PASSWORD): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                    vol.Optional(CONF_SIGENERGY_DEVICE_ID, default=""): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.TEXT)
                    ),
                    vol.Required(
                        CONF_SIGENERGY_CLOUD_REGION,
                        default=DEFAULT_SIGENERGY_CLOUD_REGION,
                    ): SelectSelector(SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=k, label=v)
                            for k, v in SIGENERGY_CLOUD_REGIONS.items()
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )),
                    vol.Optional(CONF_SIGENERGY_PASS_ENC): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
            description_placeholders={
                "credentials_help": "Enter your Sigenergy account password. Device ID is from browser dev tools.",
            },
        )

    async def async_step_sigenergy_station(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle Sigenergy station selection or manual entry."""
        errors: dict[str, str] = {}

        if user_input is not None:
            station_id = user_input.get(CONF_SIGENERGY_STATION_ID)
            if station_id:
                # Strip any whitespace
                station_id = str(station_id).strip()
                self._sigenergy_data[CONF_SIGENERGY_STATION_ID] = station_id
                tariff_station_id = getattr(
                    self, "_sigenergy_tariff_station_options", {}
                ).get(station_id)
                if tariff_station_id:
                    self._sigenergy_data[CONF_SIGENERGY_TARIFF_STATION_ID] = (
                        tariff_station_id
                    )
                    self._sigenergy_data[CONF_SIGENERGY_TARIFF_STATION_SOURCE_ID] = (
                        station_id
                    )
                # Go to Modbus connection configuration (required for energy data)
                return await self.async_step_sigenergy_modbus()
            else:
                errors["base"] = "no_station_selected"

        # Build station options from validated stations
        station_options = {}
        station_tariff_ids = {}
        try:
            from ..sigenergy_api import extract_tariff_station_id
        except Exception:
            extract_tariff_station_id = None

        for station in self._sigenergy_stations:
            tariff_station_id = (
                extract_tariff_station_id(station)
                if extract_tariff_station_id
                else None
            )
            station_identifiers = [
                str(station.get(key) or "").strip()
                for key in (
                    "id",
                    "plantId",
                    "systemId",
                    "stationSn",
                    "stationSN",
                    "stationCode",
                    "stationId",
                    "station_id",
                    "stationID",
                )
            ]
            station_id = next(
                (
                    value
                    for value in station_identifiers
                    if value and not value.isdigit()
                ),
                None,
            )
            if not station_id:
                station_id = next((value for value in station_identifiers if value), "")
            if not station_id:
                continue
            station_name = (
                station.get("stationName")
                or station.get("name")
                or f"Station {station_id}"
            )
            station_options[station_id] = station_name
            if tariff_station_id:
                station_tariff_ids[station_id] = tariff_station_id
        self._sigenergy_tariff_station_options = station_tariff_ids

        # If no stations found via API, show manual entry form
        if not station_options:
            return self.async_show_form(
                step_id="sigenergy_station",
                data_schema=vol.Schema(
                    {
                        vol.Required(CONF_SIGENERGY_STATION_ID): TextSelector(
                            TextSelectorConfig(type=TextSelectorType.TEXT)
                        ),
                    }
                ),
                errors=errors,
                description_placeholders={
                    "station_help": "Station list unavailable. Enter your Station ID manually. "
                    "To find it, ask SigenAI 'Tell me my StationID' in the Sigenergy app.",
                },
            )

        return self.async_show_form(
            step_id="sigenergy_station",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SIGENERGY_STATION_ID): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(value=k, label=v)
                                for k, v in station_options.items()
                            ],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_sigenergy_modbus(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure Sigenergy Modbus connection (required for energy data)."""
        errors: dict[str, str] = {}

        if user_input is not None:
            modbus_host = user_input.get(CONF_SIGENERGY_MODBUS_HOST, "").strip()
            if not modbus_host:
                errors["base"] = "modbus_host_required"
            else:
                self._sigenergy_data[CONF_SIGENERGY_MODBUS_HOST] = modbus_host
                self._sigenergy_data[CONF_SIGENERGY_MODBUS_PORT] = user_input.get(
                    CONF_SIGENERGY_MODBUS_PORT, DEFAULT_SIGENERGY_MODBUS_PORT
                )
                self._sigenergy_data[CONF_SIGENERGY_MODBUS_SLAVE_ID] = user_input.get(
                    CONF_SIGENERGY_MODBUS_SLAVE_ID, DEFAULT_SIGENERGY_MODBUS_SLAVE_ID
                )
                # Go directly to creating the entry (skip dc_curtailment)
                return self._create_final_entry()

        return self.async_show_form(
            step_id="sigenergy_modbus",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SIGENERGY_MODBUS_HOST): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.TEXT)
                    ),
                    vol.Optional(
                        CONF_SIGENERGY_MODBUS_PORT,
                        default=DEFAULT_SIGENERGY_MODBUS_PORT,
                    ): NumberSelector(
                        NumberSelectorConfig(min=1, max=65535, step=1, mode=NumberSelectorMode.BOX)
                    ),
                    vol.Optional(
                        CONF_SIGENERGY_MODBUS_SLAVE_ID,
                        default=DEFAULT_SIGENERGY_MODBUS_SLAVE_ID,
                    ): NumberSelector(
                        NumberSelectorConfig(min=0, max=247, step=1, mode=NumberSelectorMode.BOX)
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_alphaess_modbus(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure AlphaESS Modbus TCP connection (primary control path).

        Default slave ID is 85 (0x55) — the AlphaESS factory default. We
        sanity-probe the connection by reading the battery SOC register (0102H)
        before accepting. Cloud credentials are optional and collected next.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input.get(CONF_ALPHAESS_MODBUS_HOST, "").strip()
            port = user_input.get(CONF_ALPHAESS_MODBUS_PORT, DEFAULT_ALPHAESS_MODBUS_PORT)
            slave_id = user_input.get(
                CONF_ALPHAESS_MODBUS_SLAVE_ID, DEFAULT_ALPHAESS_MODBUS_SLAVE_ID
            )
            export_limit_kw = user_input.get(CONF_ALPHAESS_EXPORT_LIMIT_KW)
            dc_curtailment = user_input.get(CONF_ALPHAESS_DC_CURTAILMENT_ENABLED, False)

            if not host:
                errors["base"] = "alphaess_host_required"
            else:
                # Sanity-probe: try to read battery SOC (register 0x0102)
                from ..inverters.alphaess import AlphaESSController
                controller = AlphaESSController(
                    host=host,
                    port=int(port),
                    slave_id=int(slave_id),
                    max_export_limit_kw=export_limit_kw,
                )
                try:
                    connected = await controller.connect()
                    if not connected:
                        errors["base"] = "alphaess_connection_failed"
                    else:
                        state = await controller.get_status()
                        if state.attributes is None or "battery_soc" not in state.attributes:
                            errors["base"] = "alphaess_no_data"
                finally:
                    try:
                        await controller.disconnect()
                    except Exception:
                        pass

                if not errors:
                    self._alphaess_data = {
                        CONF_ALPHAESS_MODBUS_HOST: host,
                        CONF_ALPHAESS_MODBUS_PORT: int(port),
                        CONF_ALPHAESS_MODBUS_SLAVE_ID: int(slave_id),
                        CONF_ALPHAESS_DC_CURTAILMENT_ENABLED: dc_curtailment,
                    }
                    if export_limit_kw is not None:
                        self._alphaess_data[CONF_ALPHAESS_EXPORT_LIMIT_KW] = float(
                            export_limit_kw
                        )
                    return await self.async_step_alphaess_cloud()

        return self.async_show_form(
            step_id="alphaess_modbus",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ALPHAESS_MODBUS_HOST): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.TEXT)
                    ),
                    vol.Optional(
                        CONF_ALPHAESS_MODBUS_PORT,
                        default=DEFAULT_ALPHAESS_MODBUS_PORT,
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=1, max=65535, step=1, mode=NumberSelectorMode.BOX
                        )
                    ),
                    vol.Optional(
                        CONF_ALPHAESS_MODBUS_SLAVE_ID,
                        default=DEFAULT_ALPHAESS_MODBUS_SLAVE_ID,
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=1, max=255, step=1, mode=NumberSelectorMode.BOX
                        )
                    ),
                    vol.Optional(CONF_ALPHAESS_EXPORT_LIMIT_KW): NumberSelector(
                        NumberSelectorConfig(
                            min=0.0, max=100.0, step=0.1, mode=NumberSelectorMode.BOX,
                            unit_of_measurement="kW",
                        )
                    ),
                    vol.Optional(
                        CONF_ALPHAESS_DC_CURTAILMENT_ENABLED,
                        default=False,
                    ): BooleanSelector(),
                }
            ),
            errors=errors,
            description_placeholders={
                "alphaess_help": (
                    "Connect to your AlphaESS inverter. Default slave ID is 85 "
                    "(0x55) — the AlphaESS factory default. Export limit is "
                    "optional; leave blank for unlimited."
                ),
                "alphaess_curtailment_warning": (
                    "⚠️ DC Curtailment requires Modbus curtailment to be enabled in "
                    "your AlphaESS firmware settings first. Without this, PowerSync "
                    "can write the export-limit register but the inverter will not "
                    "physically curtail PV. Enable it in the AlphaESS app under "
                    "Settings → Grid → Export Limit (or equivalent for your firmware "
                    "version) before turning this on. See the PowerSync wiki for details."
                ),
            },
        )

    async def async_step_alphaess_cloud(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure optional AlphaESS Cloud API (fallback when Modbus is down).

        Credentials are issued at https://open.alphaess.com. Leave blank to
        skip — Modbus alone is sufficient for full control.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            app_id = (user_input.get(CONF_ALPHAESS_CLOUD_APP_ID) or "").strip()
            app_secret = (user_input.get(CONF_ALPHAESS_CLOUD_APP_SECRET) or "").strip()
            serial = (user_input.get(CONF_ALPHAESS_CLOUD_SERIAL) or "").strip()

            # Both empty = skip cloud entirely
            if not app_id and not app_secret:
                self._alphaess_data[CONF_ALPHAESS_CLOUD_ENABLED] = False
                return self._create_final_entry()

            if not app_id or not app_secret:
                errors["base"] = "alphaess_cloud_partial"
            else:
                from ..alphaess_api import AlphaESSCloudClient
                client = AlphaESSCloudClient(
                    app_id=app_id, app_secret=app_secret, serial=serial
                )
                try:
                    ok, msg = await client.test_connection()
                    if not ok:
                        errors["base"] = "alphaess_cloud_invalid"
                        _LOGGER.warning("AlphaESS cloud validation failed: %s", msg)
                finally:
                    try:
                        await client.close()
                    except Exception:
                        pass

                if not errors:
                    self._alphaess_data.update({
                        CONF_ALPHAESS_CLOUD_ENABLED: True,
                        CONF_ALPHAESS_CLOUD_APP_ID: app_id,
                        CONF_ALPHAESS_CLOUD_APP_SECRET: app_secret,
                        CONF_ALPHAESS_CLOUD_SERIAL: serial,
                    })
                    return self._create_final_entry()

        return self.async_show_form(
            step_id="alphaess_cloud",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_ALPHAESS_CLOUD_APP_ID): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.TEXT)
                    ),
                    vol.Optional(CONF_ALPHAESS_CLOUD_APP_SECRET): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                    vol.Optional(CONF_ALPHAESS_CLOUD_SERIAL): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.TEXT)
                    ),
                }
            ),
            errors=errors,
            description_placeholders={
                "alphaess_cloud_help": (
                    "Optional. Get App ID + App Secret from https://open.alphaess.com. "
                    "Cloud is a fallback only — Modbus is the primary control path. "
                    "Leave blank to skip."
                ),
            },
        )

    async def async_step_esy_sunhome(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Select the upstream esy_sunhome companion integration entry.

        PowerSync bridges ESY Sunhome via the esy_sunhome integration which
        handles the ESY cloud MQTT connection. Install esy_sunhome from HACS
        first, configure it with your ESY app credentials, then return here.
        """
        esy_entries = self.hass.config_entries.async_entries("esy_sunhome")
        if not esy_entries:
            return self.async_abort(reason="esy_sunhome_not_installed")

        errors: dict[str, str] = {}

        if user_input is not None or len(esy_entries) == 1:
            if len(esy_entries) == 1:
                selected_entry_id = esy_entries[0].entry_id
            else:
                selected_entry_id = user_input.get(CONF_ESY_CONFIG_ENTRY_ID, "")

            esy_entry = self.hass.config_entries.async_get_entry(selected_entry_id)
            if not esy_entry or not esy_entry.data.get("device_id"):
                errors["base"] = "esy_sunhome_no_device"
            else:
                self._esy_sunhome_data = {CONF_ESY_CONFIG_ENTRY_ID: selected_entry_id}
                return self._create_final_entry()

        if not errors and len(esy_entries) > 1:
            entry_options = {e.entry_id: e.title or e.entry_id for e in esy_entries}
            return self.async_show_form(
                step_id="esy_sunhome",
                data_schema=vol.Schema({
                    vol.Required(CONF_ESY_CONFIG_ENTRY_ID): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(value=k, label=v)
                                for k, v in entry_options.items()
                            ],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }),
                errors=errors,
            )

        return self.async_show_form(
            step_id="esy_sunhome",
            data_schema=vol.Schema({}),
            errors=errors,
        )

    async def async_step_solaredge(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure SolarEdge telemetry, battery dispatch, and curtailment.

        PowerSync reads SolarEdge Home battery telemetry from HA entities and
        uses writable HA storage-control entities for battery dispatch. Direct
        Modbus or entity fallback is used for active-power curtailment.
        """
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
                    connected = await controller.connect()
                    if not connected:
                        errors["base"] = "solaredge_connect_failed"
                finally:
                    try:
                        await controller.disconnect()
                    except Exception:
                        pass

                if not errors:
                    self._solaredge_data = {
                        CONF_SOLAREDGE_HOST: host,
                        CONF_SOLAREDGE_PORT: port,
                        CONF_SOLAREDGE_SLAVE_ID: slave_id,
                        CONF_SOLAREDGE_RATED_POWER_W: rated_power_w,
                        CONF_SOLAREDGE_ENTITY_PREFIX: entity_prefix,
                        CONF_SOLAREDGE_DC_CURTAILMENT_ENABLED: user_input.get(
                            CONF_SOLAREDGE_DC_CURTAILMENT_ENABLED, False
                        ),
                    }
                    return self._create_final_entry()

        current_solaredge = user_input or self._solaredge_data

        return self.async_show_form(
            step_id="solaredge",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_SOLAREDGE_HOST,
                        default=current_solaredge.get(CONF_SOLAREDGE_HOST, ""),
                    ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
                    vol.Optional(
                        CONF_SOLAREDGE_PORT,
                        default=current_solaredge.get(
                            CONF_SOLAREDGE_PORT, DEFAULT_SOLAREDGE_PORT
                        ),
                    ): NumberSelector(NumberSelectorConfig(
                        min=1, max=65535, step=1, mode=NumberSelectorMode.BOX,
                    )),
                    vol.Optional(
                        CONF_SOLAREDGE_SLAVE_ID,
                        default=current_solaredge.get(
                            CONF_SOLAREDGE_SLAVE_ID, DEFAULT_SOLAREDGE_SLAVE_ID
                        ),
                    ): NumberSelector(NumberSelectorConfig(
                        min=1, max=247, step=1, mode=NumberSelectorMode.BOX,
                    )),
                    vol.Required(
                        CONF_SOLAREDGE_RATED_POWER_W,
                        default=current_solaredge.get(
                            CONF_SOLAREDGE_RATED_POWER_W,
                            DEFAULT_SOLAREDGE_RATED_POWER_W,
                        ),
                    ): NumberSelector(NumberSelectorConfig(
                        min=1, max=100000, step=1, unit_of_measurement="W",
                        mode=NumberSelectorMode.BOX,
                    )),
                    vol.Optional(
                        CONF_SOLAREDGE_ENTITY_PREFIX,
                        default=current_solaredge.get(
                            CONF_SOLAREDGE_ENTITY_PREFIX, ""
                        ),
                    ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
                    vol.Optional(
                        CONF_SOLAREDGE_DC_CURTAILMENT_ENABLED,
                        default=current_solaredge.get(
                            CONF_SOLAREDGE_DC_CURTAILMENT_ENABLED, False
                        ),
                    ): BooleanSelector(),
                }
            ),
            errors=errors,
        )

    async def async_step_anker_solix(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure Anker Solix direct Modbus or HA integration bridge."""
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
            data = {
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
                        data.update(
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
                        data.update(
                            {
                                CONF_ANKER_SOLIX_CONFIG_ENTRY_ID: selected_entry_id,
                                CONF_ANKER_SOLIX_ENTITY_PREFIX: entity_prefix,
                            }
                        )
            except Exception as exc:
                _LOGGER.debug("Anker Solix setup validation failed: %s", exc)
                errors["base"] = "cannot_connect"

            if not errors:
                self._anker_solix_data = data
                return self._create_final_entry()

        current = user_input or getattr(self, "_anker_solix_data", {})
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
                    default=current.get(CONF_ANKER_SOLIX_MODBUS_HOST, ""),
                )
            ] = TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT))
            schema_fields[
                vol.Required(
                    CONF_ANKER_SOLIX_MODBUS_PORT,
                    default=current.get(
                        CONF_ANKER_SOLIX_MODBUS_PORT,
                        DEFAULT_ANKER_SOLIX_MODBUS_PORT,
                    ),
                )
            ] = NumberSelector(
                NumberSelectorConfig(min=1, max=65535, step=1, mode=NumberSelectorMode.BOX)
            )
            schema_fields[
                vol.Required(
                    CONF_ANKER_SOLIX_MODBUS_SLAVE_ID,
                    default=current.get(
                        CONF_ANKER_SOLIX_MODBUS_SLAVE_ID,
                        DEFAULT_ANKER_SOLIX_MODBUS_SLAVE_ID,
                    ),
                )
            ] = NumberSelector(
                NumberSelectorConfig(min=1, max=247, step=1, mode=NumberSelectorMode.BOX)
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
                        default=current.get(CONF_ANKER_SOLIX_CONFIG_ENTRY_ID, ""),
                    )
                ] = SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=e.entry_id, label=e.title or e.entry_id)
                            for e in anker_entries
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                )
            schema_fields[
                vol.Optional(
                    CONF_ANKER_SOLIX_ENTITY_PREFIX,
                    default=current.get(CONF_ANKER_SOLIX_ENTITY_PREFIX, ""),
                )
            ] = TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT))

        schema_fields[
            vol.Required(
                CONF_ANKER_SOLIX_BATTERY_CAPACITY_KWH,
                default=current.get(
                    CONF_ANKER_SOLIX_BATTERY_CAPACITY_KWH,
                    DEFAULT_ANKER_SOLIX_BATTERY_CAPACITY_KWH,
                ),
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
                default=current.get(
                    CONF_ANKER_SOLIX_MAX_CHARGE_KW,
                    DEFAULT_ANKER_SOLIX_MAX_CHARGE_KW,
                ),
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
                default=current.get(
                    CONF_ANKER_SOLIX_MAX_DISCHARGE_KW,
                    DEFAULT_ANKER_SOLIX_MAX_DISCHARGE_KW,
                ),
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

    async def async_step_solax_battery(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure Solax Hybrid connection via the solax_modbus integration.

        PowerSync bridges through the wills106/homeassistant-solax-modbus entities.
        Install the solax_modbus integration from HACS first, then return here.
        """
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
            capacity_kwh = user_input.get(CONF_SOLAX_BATTERY_CAPACITY_KWH, DEFAULT_SOLAX_BATTERY_CAPACITY_KWH)
            nominal_v = user_input.get(CONF_SOLAX_BATTERY_NOMINAL_V, DEFAULT_SOLAX_BATTERY_NOMINAL_V)
            max_charge_a = user_input.get(CONF_SOLAX_MAX_CHARGE_CURRENT_A, DEFAULT_SOLAX_MAX_CHARGE_CURRENT_A)
            max_discharge_a = user_input.get(CONF_SOLAX_MAX_DISCHARGE_CURRENT_A, DEFAULT_SOLAX_MAX_DISCHARGE_CURRENT_A)
            entity_prefix = (user_input.get(CONF_SOLAX_ENTITY_PREFIX) or "").strip()

            try:
                ctrl = SolaxBatteryController(
                    self.hass,
                    entity_prefix=entity_prefix,
                    solax_entry_id=selected_entry_id,
                    battery_nominal_v=float(nominal_v),
                    max_charge_current_a=float(max_charge_a),
                    max_discharge_current_a=float(max_discharge_a),
                )
                await ctrl.connect()
                self._solax_data = {
                    CONF_SOLAX_CONFIG_ENTRY_ID: selected_entry_id,
                    CONF_SOLAX_BATTERY_CAPACITY_KWH: float(capacity_kwh),
                    CONF_SOLAX_BATTERY_NOMINAL_V: float(nominal_v),
                    CONF_SOLAX_MAX_CHARGE_CURRENT_A: float(max_charge_a),
                    CONF_SOLAX_MAX_DISCHARGE_CURRENT_A: float(max_discharge_a),
                }
                if entity_prefix:
                    self._solax_data[CONF_SOLAX_ENTITY_PREFIX] = entity_prefix
                return self._create_final_entry()
            except ValueError as exc:
                msg = str(exc)
                if "solax_missing_entities:" in msg:
                    missing_list = msg.split(":", 1)[1]
                    _LOGGER.warning("Solax setup: missing entities: %s", missing_list)
                    errors["base"] = "solax_missing_entities"
                    first_missing = missing_list.split(",")[0].strip()
                    description_placeholders["first_missing"] = first_missing
                else:
                    errors["base"] = "solax_connect_failed"
            except Exception as exc:
                _LOGGER.error("Solax setup error: %s", exc)
                errors["base"] = "solax_connect_failed"

        if len(solax_entries) == 1:
            data_schema = vol.Schema({
                vol.Required(CONF_SOLAX_BATTERY_CAPACITY_KWH, default=DEFAULT_SOLAX_BATTERY_CAPACITY_KWH): NumberSelector(
                    NumberSelectorConfig(min=1, max=100, step=0.1, mode=NumberSelectorMode.BOX, unit_of_measurement="kWh")
                ),
                vol.Required(CONF_SOLAX_BATTERY_NOMINAL_V, default=DEFAULT_SOLAX_BATTERY_NOMINAL_V): NumberSelector(
                    NumberSelectorConfig(min=24, max=500, step=0.1, mode=NumberSelectorMode.BOX, unit_of_measurement="V")
                ),
                vol.Required(CONF_SOLAX_MAX_CHARGE_CURRENT_A, default=DEFAULT_SOLAX_MAX_CHARGE_CURRENT_A): NumberSelector(
                    NumberSelectorConfig(min=1, max=200, step=1, mode=NumberSelectorMode.BOX, unit_of_measurement="A")
                ),
                vol.Required(CONF_SOLAX_MAX_DISCHARGE_CURRENT_A, default=DEFAULT_SOLAX_MAX_DISCHARGE_CURRENT_A): NumberSelector(
                    NumberSelectorConfig(min=1, max=200, step=1, mode=NumberSelectorMode.BOX, unit_of_measurement="A")
                ),
                vol.Optional(CONF_SOLAX_ENTITY_PREFIX, default=""): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEXT)
                ),
            })
        else:
            entry_options = {e.entry_id: e.title or e.entry_id for e in solax_entries}
            data_schema = vol.Schema({
                vol.Required(CONF_SOLAX_CONFIG_ENTRY_ID): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=k, label=v)
                            for k, v in entry_options.items()
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(CONF_SOLAX_BATTERY_CAPACITY_KWH, default=DEFAULT_SOLAX_BATTERY_CAPACITY_KWH): NumberSelector(
                    NumberSelectorConfig(min=1, max=100, step=0.1, mode=NumberSelectorMode.BOX, unit_of_measurement="kWh")
                ),
                vol.Required(CONF_SOLAX_BATTERY_NOMINAL_V, default=DEFAULT_SOLAX_BATTERY_NOMINAL_V): NumberSelector(
                    NumberSelectorConfig(min=24, max=500, step=0.1, mode=NumberSelectorMode.BOX, unit_of_measurement="V")
                ),
                vol.Required(CONF_SOLAX_MAX_CHARGE_CURRENT_A, default=DEFAULT_SOLAX_MAX_CHARGE_CURRENT_A): NumberSelector(
                    NumberSelectorConfig(min=1, max=200, step=1, mode=NumberSelectorMode.BOX, unit_of_measurement="A")
                ),
                vol.Required(CONF_SOLAX_MAX_DISCHARGE_CURRENT_A, default=DEFAULT_SOLAX_MAX_DISCHARGE_CURRENT_A): NumberSelector(
                    NumberSelectorConfig(min=1, max=200, step=1, mode=NumberSelectorMode.BOX, unit_of_measurement="A")
                ),
                vol.Optional(CONF_SOLAX_ENTITY_PREFIX, default=""): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEXT)
                ),
            })

        return self.async_show_form(
            step_id="solax_battery",
            data_schema=data_schema,
            errors=errors,
            description_placeholders=description_placeholders or None,
        )

    async def async_step_saj_h2_battery(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure SAJ H2 bridge via the saj_h2_modbus integration."""
        from ..inverters.saj_h2 import SajH2BatteryController

        saj_entries = self.hass.config_entries.async_entries("saj_h2_modbus")
        if not saj_entries:
            return self.async_abort(reason="saj_h2_not_installed")

        errors: dict[str, str] = {}

        if user_input is not None:
            if len(saj_entries) == 1:
                selected_entry_id = saj_entries[0].entry_id
            else:
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
                self._saj_h2_data = {
                    CONF_SAJ_CONFIG_ENTRY_ID: selected_entry_id,
                    CONF_SAJ_BATTERY_CAPACITY_KWH: float(capacity_kwh),
                    CONF_SAJ_INVERTER_RATED_KW: float(inverter_rated_kw),
                }
                return self._create_final_entry()
            except ValueError as exc:
                if "saj_missing_entities:" in str(exc):
                    errors["base"] = "saj_missing_entities"
                else:
                    errors["base"] = "saj_connect_failed"
            except Exception as exc:
                _LOGGER.error("SAJ H2 setup error: %s", exc)
                errors["base"] = "saj_connect_failed"

        if len(saj_entries) == 1:
            data_schema = vol.Schema(
                {
                    vol.Required(
                        CONF_SAJ_BATTERY_CAPACITY_KWH,
                        default=DEFAULT_SAJ_BATTERY_CAPACITY_KWH,
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
                        default=DEFAULT_SAJ_INVERTER_RATED_KW,
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=1,
                            max=50,
                            step=0.5,
                            mode=NumberSelectorMode.BOX,
                            unit_of_measurement="kW",
                        )
                    ),
                }
            )
        else:
            entry_options = {e.entry_id: e.title or e.entry_id for e in saj_entries}
            data_schema = vol.Schema(
                {
                    vol.Required(CONF_SAJ_CONFIG_ENTRY_ID): SelectSelector(
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
                        default=DEFAULT_SAJ_BATTERY_CAPACITY_KWH,
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
                        default=DEFAULT_SAJ_INVERTER_RATED_KW,
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=1,
                            max=50,
                            step=0.5,
                            mode=NumberSelectorMode.BOX,
                            unit_of_measurement="kW",
                        )
                    ),
                }
            )

        return self.async_show_form(
            step_id="saj_h2_battery",
            data_schema=data_schema,
            errors=errors,
        )

    async def async_step_fronius_reserva_battery(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure Fronius GEN24 storage bridge via the fronius_modbus integration."""
        from ..inverters.fronius_reserva import FroniusReservaBatteryController

        fronius_entries = self.hass.config_entries.async_entries("fronius_modbus")
        if not fronius_entries:
            return self.async_abort(reason="fronius_reserva_not_installed")

        errors: dict[str, str] = {}
        description_placeholders: dict[str, str] = {}

        if user_input is not None:
            if len(fronius_entries) == 1:
                selected_entry_id = fronius_entries[0].entry_id
            else:
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
                self._fronius_reserva_data = {
                    CONF_FRONIUS_RESERVA_CONFIG_ENTRY_ID: selected_entry_id,
                    CONF_FRONIUS_RESERVA_BATTERY_CAPACITY_KWH: float(capacity_kwh),
                    CONF_FRONIUS_RESERVA_MAX_CHARGE_KW: float(max_charge_kw),
                    CONF_FRONIUS_RESERVA_MAX_DISCHARGE_KW: float(max_discharge_kw),
                }
                return self._create_final_entry()
            except ValueError as exc:
                msg = str(exc)
                if "fronius_reserva_missing_entities:" in msg:
                    missing_list = msg.split(":", 1)[1]
                    errors["base"] = "fronius_reserva_missing_entities"
                    description_placeholders["first_missing"] = missing_list.split(",")[0].strip()
                else:
                    errors["base"] = "fronius_reserva_connect_failed"
            except Exception as exc:
                _LOGGER.error("Fronius GEN24 storage setup error: %s", exc)
                errors["base"] = "fronius_reserva_connect_failed"

        schema_fields: dict[Any, Any] = {}
        if len(fronius_entries) > 1:
            entry_options = {e.entry_id: e.title or e.entry_id for e in fronius_entries}
            schema_fields[
                vol.Required(CONF_FRONIUS_RESERVA_CONFIG_ENTRY_ID)
            ] = SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(value=k, label=v)
                        for k, v in entry_options.items()
                    ],
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )

        schema_fields[
            vol.Required(
                CONF_FRONIUS_RESERVA_BATTERY_CAPACITY_KWH,
                default=DEFAULT_FRONIUS_RESERVA_BATTERY_CAPACITY_KWH,
            )
        ] = NumberSelector(
            NumberSelectorConfig(
                min=1,
                max=100,
                step=0.1,
                mode=NumberSelectorMode.BOX,
                unit_of_measurement="kWh",
            )
        )
        schema_fields[
            vol.Required(
                CONF_FRONIUS_RESERVA_MAX_CHARGE_KW,
                default=DEFAULT_FRONIUS_RESERVA_MAX_CHARGE_KW,
            )
        ] = NumberSelector(
            NumberSelectorConfig(
                min=0.1,
                max=50,
                step=0.1,
                mode=NumberSelectorMode.BOX,
                unit_of_measurement="kW",
            )
        )
        schema_fields[
            vol.Required(
                CONF_FRONIUS_RESERVA_MAX_DISCHARGE_KW,
                default=DEFAULT_FRONIUS_RESERVA_MAX_DISCHARGE_KW,
            )
        ] = NumberSelector(
            NumberSelectorConfig(
                min=0.1,
                max=50,
                step=0.1,
                mode=NumberSelectorMode.BOX,
                unit_of_measurement="kW",
            )
        )

        return self.async_show_form(
            step_id="fronius_reserva_battery",
            data_schema=vol.Schema(schema_fields),
            errors=errors,
            description_placeholders=description_placeholders or None,
        )

    async def async_step_neovolt_battery(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure Neovolt bridge via the upstream neovolt integration."""
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
                self._neovolt_data = {
                    CONF_NEOVOLT_CONFIG_ENTRY_ID: selected_entry_ids[0],
                    CONF_NEOVOLT_CONFIG_ENTRY_IDS: selected_entry_ids,
                    CONF_NEOVOLT_MAX_CHARGE_KW: float(max_charge_kw),
                    CONF_NEOVOLT_MAX_DISCHARGE_KW: float(max_discharge_kw),
                    CONF_NEOVOLT_BATTERY_CAPACITIES_KWH: battery_capacities_kwh,
                    CONF_NEOVOLT_BATTERY_CAPACITIES_KWH_RAW: battery_capacities_text,
                    CONF_NEOVOLT_SURPLUS_BALANCER_MODE: str(surplus_balancer_mode),
                    CONF_NEOVOLT_SOC_BALANCE_TOLERANCE: float(soc_balance_tolerance),
                }
                return self._create_final_entry()
            except ValueError as exc:
                if "capacity_" in str(exc):
                    errors["base"] = "neovolt_capacity_invalid"
                elif "neovolt_missing_entities:" in str(exc):
                    errors["base"] = "neovolt_missing_entities"
                else:
                    errors["base"] = "neovolt_connect_failed"
            except Exception as exc:
                _LOGGER.error("Neovolt setup error: %s", exc)
                errors["base"] = "neovolt_connect_failed"

        schema_fields: dict[Any, Any] = {}
        if len(neovolt_entries) > 1:
            entry_options = {e.entry_id: e.title or e.entry_id for e in neovolt_entries}
            schema_fields[
                vol.Required(
                    CONF_NEOVOLT_CONFIG_ENTRY_IDS,
                    default=list(entry_options),
                )
            ] = SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(value=k, label=v)
                        for k, v in entry_options.items()
                    ],
                    multiple=True,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )

        schema_fields[
            vol.Required(
                CONF_NEOVOLT_MAX_CHARGE_KW,
                default=DEFAULT_NEOVOLT_MAX_CHARGE_KW,
            )
        ] = NumberSelector(
            NumberSelectorConfig(
                min=0.5,
                max=50,
                step=0.1,
                mode=NumberSelectorMode.BOX,
                unit_of_measurement="kW",
            )
        )
        schema_fields[
            vol.Required(
                CONF_NEOVOLT_MAX_DISCHARGE_KW,
                default=DEFAULT_NEOVOLT_MAX_DISCHARGE_KW,
            )
        ] = NumberSelector(
            NumberSelectorConfig(
                min=0.5,
                max=50,
                step=0.1,
                mode=NumberSelectorMode.BOX,
                unit_of_measurement="kW",
            )
        )
        schema_fields[
            vol.Optional(
                CONF_NEOVOLT_BATTERY_CAPACITIES_KWH,
                default="",
            )
        ] = TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT))
        schema_fields[
            vol.Required(
                CONF_NEOVOLT_SURPLUS_BALANCER_MODE,
                default=DEFAULT_NEOVOLT_SURPLUS_BALANCER_MODE,
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
        schema_fields[
            vol.Required(
                CONF_NEOVOLT_SOC_BALANCE_TOLERANCE,
                default=DEFAULT_NEOVOLT_SOC_BALANCE_TOLERANCE,
            )
        ] = NumberSelector(
            NumberSelectorConfig(
                min=1,
                max=30,
                step=1,
                mode=NumberSelectorMode.BOX,
                unit_of_measurement="%",
            )
        )

        return self.async_show_form(
            step_id="neovolt_battery",
            data_schema=vol.Schema(schema_fields),
            errors=errors,
        )

    async def async_step_sungrow(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure Sungrow Modbus TCP connection."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input.get(CONF_SUNGROW_HOST, "").strip()
            port = user_input.get(CONF_SUNGROW_PORT, DEFAULT_SUNGROW_PORT)
            slave_id = user_input.get(CONF_SUNGROW_SLAVE_ID, DEFAULT_SUNGROW_SLAVE_ID)

            if not host:
                errors["base"] = "sungrow_host_required"
            else:
                # Test Modbus connection
                test_result = await test_sungrow_connection(
                    self.hass, host, port, slave_id
                )

                if test_result["success"]:
                    # Store Sungrow configuration
                    self._sungrow_data = {
                        CONF_SUNGROW_HOST: host,
                        CONF_SUNGROW_PORT: port,
                        CONF_SUNGROW_SLAVE_ID: slave_id,
                    }
                    _LOGGER.info(
                        "Sungrow Modbus connection successful: host=%s, SOC=%.1f%%, SOH=%.1f%%",
                        host,
                        test_result.get("battery_soc", 0),
                        test_result.get("battery_soh", 0),
                    )
                    # Go directly to creating the entry (skip secondary)
                    return self._create_final_entry()
                else:
                    errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="sungrow",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SUNGROW_HOST): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.TEXT)
                    ),
                    vol.Optional(CONF_SUNGROW_PORT, default=DEFAULT_SUNGROW_PORT): NumberSelector(
                        NumberSelectorConfig(min=1, max=65535, step=1, mode=NumberSelectorMode.BOX)
                    ),
                    vol.Optional(
                        CONF_SUNGROW_SLAVE_ID, default=DEFAULT_SUNGROW_SLAVE_ID
                    ): NumberSelector(
                        NumberSelectorConfig(min=1, max=247, step=1, mode=NumberSelectorMode.BOX)
                    ),
                }
            ),
            errors=errors,
        )

    # ---- FoxESS Config Flow Steps ----

    async def async_step_foxess_connection(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Choose FoxESS connection type: TCP, Serial, Cloud, or entity bridge."""
        if user_input is not None:
            conn_type = user_input.get(
                CONF_FOXESS_CONNECTION_TYPE, FOXESS_CONNECTION_TCP
            )
            if conn_type == FOXESS_CONNECTION_SERIAL:
                return await self.async_step_foxess_serial()
            if conn_type == FOXESS_CONNECTION_CLOUD:
                self._foxess_data = {
                    CONF_FOXESS_CONNECTION_TYPE: FOXESS_CONNECTION_CLOUD,
                }
                return await self.async_step_foxess_cloud()
            if conn_type == FOXESS_CONNECTION_ENTITY:
                self._foxess_data = {
                    CONF_FOXESS_CONNECTION_TYPE: FOXESS_CONNECTION_ENTITY,
                }
                return await self.async_step_foxess_entity()
            else:
                return await self.async_step_foxess_tcp()

        return self.async_show_form(
            step_id="foxess_connection",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_FOXESS_CONNECTION_TYPE, default=FOXESS_CONNECTION_TCP
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(value=FOXESS_CONNECTION_TCP, label="Modbus TCP (LAN/Wi-Fi)"),
                                SelectOptionDict(value=FOXESS_CONNECTION_SERIAL, label="RS485 Serial"),
                                SelectOptionDict(value=FOXESS_CONNECTION_CLOUD, label="FoxESS Cloud API"),
                                SelectOptionDict(value=FOXESS_CONNECTION_ENTITY, label="Entity bridge (foxess_modbus)"),
                            ],
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                }
            ),
        )

    async def async_step_foxess_entity(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure FoxESS via nathanmarlor/foxess_modbus entities."""
        errors: dict[str, str] = {}
        entries = _foxess_modbus_entry_options(self.hass)

        if user_input is not None:
            selected_entry_id = ""
            if len(entries) == 1:
                selected_entry_id = entries[0]["value"]
            elif entries:
                selected_entry_id = user_input.get(CONF_FOXESS_ENTITY_CONFIG_ENTRY_ID, "")
            entity_prefix = (user_input.get(CONF_FOXESS_ENTITY_PREFIX) or "").strip()

            valid, error = await _validate_foxess_entity_bridge(
                self.hass,
                selected_entry_id,
                entity_prefix,
            )
            if valid:
                self._foxess_data = {
                    CONF_FOXESS_CONNECTION_TYPE: FOXESS_CONNECTION_ENTITY,
                }
                if selected_entry_id:
                    self._foxess_data[CONF_FOXESS_ENTITY_CONFIG_ENTRY_ID] = selected_entry_id
                if entity_prefix:
                    self._foxess_data[CONF_FOXESS_ENTITY_PREFIX] = entity_prefix
                return self._create_final_entry()
            errors["base"] = error or "foxess_entity_connect_failed"

        schema: dict[Any, Any] = {}
        if len(entries) > 1:
            schema[
                vol.Required(CONF_FOXESS_ENTITY_CONFIG_ENTRY_ID)
            ] = SelectSelector(
                SelectSelectorConfig(options=entries, mode=SelectSelectorMode.DROPDOWN)
            )
        elif not entries:
            schema[
                vol.Optional(CONF_FOXESS_ENTITY_PREFIX, default="")
            ] = TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT))
        else:
            schema[
                vol.Optional(CONF_FOXESS_ENTITY_PREFIX, default="")
            ] = TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT))

        if len(entries) > 1:
            schema[
                vol.Optional(CONF_FOXESS_ENTITY_PREFIX, default="")
            ] = TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT))

        return self.async_show_form(
            step_id="foxess_entity",
            data_schema=vol.Schema(schema),
            errors=errors,
        )

    async def async_step_foxess_tcp(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure FoxESS Modbus TCP connection."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input.get(CONF_FOXESS_HOST, "").strip()
            port = user_input.get(CONF_FOXESS_PORT, DEFAULT_FOXESS_PORT)
            slave_id = user_input.get(CONF_FOXESS_SLAVE_ID, DEFAULT_FOXESS_SLAVE_ID)

            if not host:
                errors["base"] = "foxess_host_required"
            else:
                # Test Modbus connection and auto-detect model
                test_result = await test_foxess_connection(
                    self.hass,
                    host,
                    port,
                    slave_id,
                    connection_type="tcp",
                )

                if test_result["success"]:
                    detected_model = test_result.get("model_family", "unknown")
                    self._foxess_data = {
                        CONF_FOXESS_HOST: host,
                        CONF_FOXESS_PORT: port,
                        CONF_FOXESS_SLAVE_ID: slave_id,
                        CONF_FOXESS_CONNECTION_TYPE: FOXESS_CONNECTION_TCP,
                        CONF_FOXESS_MODEL_FAMILY: detected_model,
                    }
                    _LOGGER.info(
                        "FoxESS Modbus TCP connection successful: host=%s, model=%s, SOC=%.1f%%",
                        host,
                        detected_model,
                        test_result.get("battery_soc", 0),
                    )
                    # Let user confirm/override model if in H3-Pro register family
                    if detected_model in ("H3-Pro", "H3-Smart"):
                        return await self.async_step_foxess_model()
                    return await self.async_step_foxess_cloud()
                else:
                    errors["base"] = "foxess_tcp_failed"

        return self.async_show_form(
            step_id="foxess_tcp",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_FOXESS_HOST): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.TEXT)
                    ),
                    vol.Optional(CONF_FOXESS_PORT, default=DEFAULT_FOXESS_PORT): NumberSelector(
                        NumberSelectorConfig(min=1, max=65535, step=1, mode=NumberSelectorMode.BOX)
                    ),
                    vol.Optional(
                        CONF_FOXESS_SLAVE_ID, default=DEFAULT_FOXESS_SLAVE_ID
                    ): NumberSelector(
                        NumberSelectorConfig(min=1, max=247, step=1, mode=NumberSelectorMode.BOX)
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_foxess_serial(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure FoxESS RS485 serial connection."""
        errors: dict[str, str] = {}

        if user_input is not None:
            serial_port = user_input.get(CONF_FOXESS_SERIAL_PORT, "").strip()
            baudrate = user_input.get(
                CONF_FOXESS_SERIAL_BAUDRATE, DEFAULT_FOXESS_SERIAL_BAUDRATE
            )
            slave_id = user_input.get(CONF_FOXESS_SLAVE_ID, DEFAULT_FOXESS_SLAVE_ID)

            if not serial_port:
                errors["base"] = "foxess_serial_required"
            else:
                # Test serial connection
                test_result = await test_foxess_connection(
                    self.hass,
                    "",
                    0,
                    slave_id,
                    connection_type="serial",
                    serial_port=serial_port,
                    baudrate=baudrate,
                )

                if test_result["success"]:
                    detected_model = test_result.get("model_family", "unknown")
                    self._foxess_data = {
                        CONF_FOXESS_SERIAL_PORT: serial_port,
                        CONF_FOXESS_SERIAL_BAUDRATE: baudrate,
                        CONF_FOXESS_SLAVE_ID: slave_id,
                        CONF_FOXESS_CONNECTION_TYPE: FOXESS_CONNECTION_SERIAL,
                        CONF_FOXESS_MODEL_FAMILY: detected_model,
                    }
                    _LOGGER.info(
                        "FoxESS RS485 connection successful: port=%s, model=%s, SOC=%.1f%%",
                        serial_port,
                        detected_model,
                        test_result.get("battery_soc", 0),
                    )
                    # Let user confirm/override model if in H3-Pro register family
                    if detected_model in ("H3-Pro", "H3-Smart"):
                        return await self.async_step_foxess_model()
                    return await self.async_step_foxess_cloud()
                else:
                    errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="foxess_serial",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_FOXESS_SERIAL_PORT, default="/dev/ttyUSB0"): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.TEXT)
                    ),
                    vol.Optional(
                        CONF_FOXESS_SERIAL_BAUDRATE,
                        default=DEFAULT_FOXESS_SERIAL_BAUDRATE,
                    ): NumberSelector(
                        NumberSelectorConfig(min=1200, max=115200, step=1, mode=NumberSelectorMode.BOX)
                    ),
                    vol.Optional(
                        CONF_FOXESS_SLAVE_ID, default=DEFAULT_FOXESS_SLAVE_ID
                    ): NumberSelector(
                        NumberSelectorConfig(min=1, max=247, step=1, mode=NumberSelectorMode.BOX)
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_foxess_model(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm or override detected FoxESS model family.

        Shown when auto-detection finds H3-Pro-class registers, since H3-Pro
        and H3 Smart share the same register address space.
        """
        if user_input is not None:
            selected = user_input.get(CONF_FOXESS_MODEL_FAMILY, FOXESS_MODEL_H3_PRO)
            self._foxess_data[CONF_FOXESS_MODEL_FAMILY] = selected
            _LOGGER.info("FoxESS model confirmed by user: %s", selected)
            return await self.async_step_foxess_cloud()

        detected = self._foxess_data.get(CONF_FOXESS_MODEL_FAMILY, FOXESS_MODEL_H3_PRO)

        return self.async_show_form(
            step_id="foxess_model",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_FOXESS_MODEL_FAMILY, default=detected): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(value=k, label=v)
                                for k, v in FOXESS_MODEL_FAMILIES.items()
                            ],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
        )

    async def async_step_foxess_cloud(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """FoxESS Cloud API key for cloud control or tariff schedule sync."""
        errors = {}
        cloud_required = (
            self._foxess_data.get(CONF_FOXESS_CONNECTION_TYPE) == FOXESS_CONNECTION_CLOUD
        )

        if user_input is not None:
            # Cloud is optional for Modbus setups, required for cloud-only setups.
            api_key = user_input.get(CONF_FOXESS_CLOUD_API_KEY, "").strip()
            if api_key:
                device_sn = user_input.get(CONF_FOXESS_CLOUD_DEVICE_SN, "").strip()
                # Validate connection
                try:
                    from ..foxess_api import FoxESSCloudClient, _extract_device_sn

                    client = FoxESSCloudClient(api_key=api_key, device_sn=device_sn)
                    try:
                        devices = await client.get_device_list()
                        self._foxess_cloud_devices = devices
                        if not device_sn and len(devices) == 1:
                            device_sn = _extract_device_sn(devices[0])
                        if device_sn and devices and not any(
                            _extract_device_sn(device) == device_sn
                            for device in devices
                        ):
                            errors["base"] = "foxess_cloud_auth_failed"
                            return self._show_foxess_cloud_form(
                                errors,
                                api_key=api_key,
                                cloud_required=cloud_required,
                            )
                        if cloud_required and not device_sn:
                            errors["base"] = "foxess_cloud_device_required"
                            return self._show_foxess_cloud_form(
                                errors,
                                api_key=api_key,
                                cloud_required=cloud_required,
                            )
                    finally:
                        await client.close()

                    self._foxess_data[CONF_FOXESS_CLOUD_API_KEY] = api_key
                    self._foxess_data[CONF_FOXESS_CLOUD_DEVICE_SN] = device_sn
                    return self._create_final_entry()
                except Exception as e:
                    _LOGGER.error("FoxESS Cloud connection error: %s", e)
                    errors["base"] = "foxess_cloud_connection_error"
            else:
                if cloud_required:
                    errors["base"] = "foxess_cloud_required"
                    return self._show_foxess_cloud_form(
                        errors,
                        cloud_required=cloud_required,
                    )
                # Blank API key — skip cloud setup
                return self._create_final_entry()

        return self._show_foxess_cloud_form(errors, cloud_required=cloud_required)

    def _show_foxess_cloud_form(
        self,
        errors: dict[str, str],
        *,
        api_key: str = "",
        cloud_required: bool = False,
    ) -> FlowResult:
        """Show FoxESS Cloud API setup with a selector when devices are known."""
        device_field = TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT))
        devices = getattr(self, "_foxess_cloud_devices", []) or []
        device_options = []
        if devices:
            try:
                from ..foxess_api import _extract_device_sn

                for device in devices:
                    sn = _extract_device_sn(device)
                    if sn:
                        label = device.get("stationName") or device.get("deviceName") or sn
                        device_options.append(SelectOptionDict(value=sn, label=f"{label} ({sn})"))
            except Exception:
                device_options = []
        if device_options:
            device_field = SelectSelector(
                SelectSelectorConfig(options=device_options, mode=SelectSelectorMode.DROPDOWN)
            )

        api_key_marker = vol.Required if cloud_required else vol.Optional
        device_marker = vol.Required if cloud_required else vol.Optional
        schema = {
            api_key_marker(CONF_FOXESS_CLOUD_API_KEY, default=api_key): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD)
            ),
        }
        device_key = (
            device_marker(CONF_FOXESS_CLOUD_DEVICE_SN)
            if cloud_required and device_options
            else device_marker(CONF_FOXESS_CLOUD_DEVICE_SN, default="")
        )
        schema[device_key] = device_field

        return self.async_show_form(
            step_id="foxess_cloud",
            data_schema=vol.Schema(schema),
            errors=errors,
        )

    async def async_step_goodwe_connection(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure GoodWe inverter connection."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input.get(CONF_GOODWE_HOST, "").strip()
            protocol = user_input.get(CONF_GOODWE_PROTOCOL, "udp")
            port = resolve_goodwe_port(protocol, user_input.get(CONF_GOODWE_PORT))
            ems_prefix = user_input.get(CONF_GOODWE_EMS_ENTITY_PREFIX, "").strip()
            ems_control_mode = resolve_goodwe_ems_control_mode_for_protocol(
                self.hass,
                user_input.get(CONF_GOODWE_EMS_CONTROL_MODE),
                ems_prefix,
                protocol,
            )

            if not host:
                errors["base"] = "goodwe_connect_failed"
            else:
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
                    entity_telemetry_prefix = ""
                    if protocol == "tcp" or port == DEFAULT_GOODWE_PORT_TCP:
                        entity_telemetry_prefix = await resolve_goodwe_entity_telemetry_prefix(
                            self.hass,
                            resolved_ems_prefix or ems_prefix,
                        )
                    result = (
                        {"success": True, "has_battery": True}
                        if entity_telemetry_prefix
                        else await test_goodwe_connection(self.hass, host, port)
                    )

                    if result.get("success"):
                        if not result.get("has_battery"):
                            errors["base"] = "goodwe_no_battery"
                        else:
                            self._goodwe_data = {
                                CONF_GOODWE_HOST: host,
                                CONF_GOODWE_PORT: port,
                                CONF_GOODWE_PROTOCOL: protocol,
                                CONF_GOODWE_EMS_CONTROL_MODE: ems_control_mode,
                            }
                            if ems_control_mode == GOODWE_EMS_CONTROL_ENTITY:
                                self._goodwe_data[
                                    CONF_GOODWE_EMS_ENTITY_PREFIX
                                ] = resolved_ems_prefix
                            _LOGGER.info(
                                "GoodWe connection successful%s: %s (SN: %s, %sW)",
                                (
                                    f" via telemetry entities '{entity_telemetry_prefix}'"
                                    if entity_telemetry_prefix
                                    else ""
                                ),
                                result.get("model_name"),
                                result.get("serial_number"),
                                result.get("rated_power"),
                            )
                            return self._create_final_entry()
                    else:
                        errors["base"] = "goodwe_connect_failed"

        current_host = user_input.get(CONF_GOODWE_HOST, "") if user_input else ""
        current_protocol = user_input.get(CONF_GOODWE_PROTOCOL, "udp") if user_input else "udp"
        current_port = (
            resolve_goodwe_port(current_protocol, user_input.get(CONF_GOODWE_PORT))
            if user_input
            else DEFAULT_GOODWE_PORT_UDP
        )
        current_ems_prefix = (
            user_input.get(CONF_GOODWE_EMS_ENTITY_PREFIX, "").strip()
            if user_input
            else ""
        )
        current_ems_control_mode = resolve_goodwe_ems_control_mode(
            user_input.get(CONF_GOODWE_EMS_CONTROL_MODE) if user_input else None,
            current_ems_prefix,
        )

        return self.async_show_form(
            step_id="goodwe_connection",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_GOODWE_HOST, default=current_host): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.TEXT)
                    ),
                    vol.Required(CONF_GOODWE_PROTOCOL, default=current_protocol): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(value="udp", label="UDP direct control (port 8899)"),
                                SelectOptionDict(value="tcp", label="TCP / LAN Kit-20 (port 502)"),
                            ],
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                    vol.Required(
                        CONF_GOODWE_PORT, default=current_port
                    ): NumberSelector(
                        NumberSelectorConfig(min=1, max=65535, step=1, mode=NumberSelectorMode.BOX)
                    ),
                    vol.Required(
                        CONF_GOODWE_EMS_CONTROL_MODE,
                        default=current_ems_control_mode,
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=goodwe_ems_control_options(),
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
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

    async def async_step_tesla_provider(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Let user choose between Tesla Fleet and Teslemetry."""
        # Check if Tesla Fleet integration is configured and loaded
        self._tesla_fleet_available = False
        self._tesla_fleet_token = None

        tesla_fleet_entries = self.hass.config_entries.async_entries("tesla_fleet")
        if tesla_fleet_entries:
            for tesla_entry in tesla_fleet_entries:
                if tesla_entry.state == ConfigEntryState.LOADED:
                    try:
                        if CONF_TOKEN in tesla_entry.data:
                            token_data = tesla_entry.data[CONF_TOKEN]
                            if CONF_ACCESS_TOKEN in token_data:
                                self._tesla_fleet_token = token_data[CONF_ACCESS_TOKEN]
                                self._tesla_fleet_available = True
                                _LOGGER.info(
                                    "Tesla Fleet integration detected and available"
                                )
                                break
                    except Exception as e:
                        _LOGGER.warning(
                            "Failed to extract tokens from Tesla Fleet integration: %s",
                            e,
                        )

        # Build the labelled EV provider choices once for reuse below
        ev_provider_choices = _build_tesla_ev_provider_choices(self.hass)

        def _build_schema(include_fleet: bool) -> vol.Schema:
            energy_options: list[SelectOptionDict] = [
                SelectOptionDict(
                    value=TESLA_PROVIDER_POWERSYNC,
                    label="PowerSync (Free - sign in with Tesla, recommended)",
                ),
            ]
            if include_fleet:
                energy_options.append(
                    SelectOptionDict(
                        value=TESLA_PROVIDER_FLEET_API,
                        label="Tesla Fleet API (Free - uses existing Tesla Fleet integration)",
                    )
                )
            energy_options.append(
                SelectOptionDict(
                    value=TESLA_PROVIDER_TESLEMETRY,
                    label="Teslemetry (~$4/month)",
                )
            )

            ev_options = [
                SelectOptionDict(value=k, label=v)
                for k, v in ev_provider_choices.items()
            ]

            return vol.Schema(
                {
                    vol.Required(
                        CONF_TESLA_API_PROVIDER, default=TESLA_PROVIDER_POWERSYNC
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=energy_options,
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                    vol.Required(
                        CONF_TESLA_EV_API_PROVIDER,
                        default=TESLA_EV_API_PROVIDER_NONE,
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=ev_options,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            )

        async def _handle_ev_provider_selection(
            user_input_local: dict[str, Any],
        ) -> FlowResult | None:
            """Stash and validate the EV provider choice. Returns a follow-up
            FlowResult when the user picked Teslemetry-without-detection (token
            entry), or None to indicate the caller should continue normally."""
            ev_choice = user_input_local.get(
                CONF_TESLA_EV_API_PROVIDER, TESLA_EV_API_PROVIDER_NONE
            )
            self._tesla_ev_provider = ev_choice
            detected = _detect_tesla_ev_integrations(self.hass)
            if (
                ev_choice == TESLA_EV_API_PROVIDER_FLEET_API
                and not detected["tesla_fleet"]
            ):
                # Hard fail — Tesla Fleet OAuth can't be entered manually here
                return self.async_show_form(
                    step_id="tesla_provider",
                    data_schema=_build_schema(self._tesla_fleet_available),
                    errors={CONF_TESLA_EV_API_PROVIDER: "tesla_fleet_not_installed"},
                )
            if (
                ev_choice == TESLA_EV_API_PROVIDER_TESLEMETRY
                and not detected["teslemetry"]
            ):
                # Will need a follow-up token entry step (handled after energy
                # provider validation succeeds, since both flows share that
                # step). Mark a flag so we know to route there.
                self._tesla_ev_needs_teslemetry_token = True
            else:
                self._tesla_ev_needs_teslemetry_token = False
            return None

        # If Tesla Fleet is not available, offer PowerSync (free) or Teslemetry (paid)
        if not self._tesla_fleet_available:
            if user_input is not None:
                ev_followup = await _handle_ev_provider_selection(user_input)
                if ev_followup is not None:
                    return ev_followup
                self._selected_provider = user_input[CONF_TESLA_API_PROVIDER]
                if self._selected_provider == TESLA_PROVIDER_POWERSYNC:
                    return await self.async_step_powersync()
                return await self.async_step_teslemetry()

            return self.async_show_form(
                step_id="tesla_provider",
                data_schema=_build_schema(include_fleet=False),
            )

        # Tesla Fleet is available - let user choose
        if user_input is not None:
            ev_followup = await _handle_ev_provider_selection(user_input)
            if ev_followup is not None:
                return ev_followup

            self._selected_provider = user_input[CONF_TESLA_API_PROVIDER]

            if self._selected_provider == TESLA_PROVIDER_POWERSYNC:
                _LOGGER.info("User selected PowerSync.cc cloud proxy")
                return await self.async_step_powersync()

            if self._selected_provider == TESLA_PROVIDER_FLEET_API:
                # User chose Fleet API - validate and get sites
                _LOGGER.info("User selected Tesla Fleet API")
                validation_result = await validate_fleet_api_token(
                    self.hass, self._tesla_fleet_token
                )

                if validation_result["success"]:
                    # Store empty Teslemetry token (we'll use Fleet API in __init__.py)
                    # AND persist the provider choice so that on HA restart the
                    # integration remembers we picked Fleet API instead of
                    # defaulting back to Teslemetry (which would then 401 on
                    # the empty token and break the Tesla coordinator).
                    # Also persist the regional base URL so EU/AP users don't hit
                    # the hardcoded NA endpoint on every subsequent API call.
                    self._teslemetry_data = {
                        CONF_TESLEMETRY_API_TOKEN: "",
                        CONF_TESLA_API_PROVIDER: TESLA_PROVIDER_FLEET_API,
                        CONF_FLEET_API_BASE_URL: validation_result.get("base_url", FLEET_API_BASE_URL),
                    }
                    self._tesla_sites = validation_result.get("sites", [])
                    return await self.async_step_site_selection()
                else:
                    # Fleet API validation failed - show error
                    errors = {"base": validation_result.get("error", "unknown")}
                    return self.async_show_form(
                        step_id="tesla_provider",
                        data_schema=_build_schema(include_fleet=True),
                        errors=errors,
                    )
            else:
                # User chose Teslemetry
                _LOGGER.info("User selected Teslemetry")
                return await self.async_step_teslemetry()

        # Show provider selection form — default to PowerSync (free, recommended)
        return self.async_show_form(
            step_id="tesla_provider",
            data_schema=_build_schema(include_fleet=True),
            description_placeholders={
                "fleet_detected": "✓ Tesla Fleet integration detected!",
            },
        )

    async def async_step_teslemetry(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle Teslemetry API token entry."""
        errors: dict[str, str] = {}

        if user_input is not None:
            teslemetry_token = user_input.get(CONF_TESLEMETRY_API_TOKEN, "").strip()

            if teslemetry_token:
                validation_result = await validate_teslemetry_token(
                    self.hass, teslemetry_token
                )

                if validation_result["success"]:
                    self._teslemetry_data = user_input
                    self._tesla_sites = validation_result.get("sites", [])
                    return await self.async_step_site_selection()
                else:
                    errors["base"] = validation_result.get("error", "unknown")
            else:
                errors["base"] = "no_token_provided"

        data_schema = vol.Schema(
            {
                vol.Required(CONF_TESLEMETRY_API_TOKEN): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
            }
        )

        return self.async_show_form(
            step_id="teslemetry",
            data_schema=data_schema,
            errors=errors,
            description_placeholders={
                "teslemetry_url": "https://teslemetry.com",
            },
        )

    async def async_step_tesla_ev_teslemetry_token(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Collect a Teslemetry API token used exclusively for vehicle commands.

        Reached when the user picked Teslemetry as the EV provider but the
        Teslemetry HA integration is not installed. The token entered here is
        stored under CONF_TESLA_EV_TELEMETRY_TOKEN and used by
        get_tesla_vehicle_api_token() at runtime — kept separate from the
        energy-site Teslemetry token so users can mix providers freely.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            token = user_input.get(CONF_TESLA_EV_TELEMETRY_TOKEN, "").strip()
            if token:
                validation_result = await validate_teslemetry_token(self.hass, token)
                if validation_result["success"]:
                    self._tesla_ev_teslemetry_token = token
                    return self._create_final_entry()
                errors["base"] = validation_result.get("error", "unknown")
            else:
                errors["base"] = "no_token_provided"

        return self.async_show_form(
            step_id="tesla_ev_teslemetry_token",
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

    async def async_step_powersync(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle PowerSync.cc cloud proxy token entry.

        Flow:
        1. Show a form with a button/link to https://api.powersync.cc/auth/start
        2. User signs in with Tesla in their browser, gets a `psync_xxx` token
        3. User pastes it back into HA, we validate it against the proxy
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            powersync_token = user_input.get(CONF_TESLEMETRY_API_TOKEN, "").strip()

            if powersync_token:
                validation_result = await validate_powersync_token(
                    self.hass, powersync_token
                )

                if validation_result["success"]:
                    # Reuse the teslemetry token slot — coordinator picks the right
                    # base URL based on CONF_TESLA_API_PROVIDER
                    self._teslemetry_data = {
                        CONF_TESLEMETRY_API_TOKEN: powersync_token,
                        CONF_TESLA_API_PROVIDER: TESLA_PROVIDER_POWERSYNC,
                    }
                    self._tesla_sites = validation_result.get("sites", [])
                    return await self.async_step_site_selection()
                errors["base"] = validation_result.get("error", "unknown")
            else:
                errors["base"] = "no_token_provided"

        data_schema = vol.Schema(
            {
                vol.Required(CONF_TESLEMETRY_API_TOKEN): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
            }
        )

        return self.async_show_form(
            step_id="powersync",
            data_schema=data_schema,
            errors=errors,
            description_placeholders={
                "auth_url": POWERSYNC_AUTH_START_URL,
            },
        )

    def _globird_plan_schema(self, current: dict[str, Any] | None = None) -> vol.Schema:
        """Build the GloBird plan selector schema."""
        return _build_globird_plan_schema(
            current,
            rate_unit=self._selector_unit(),
            currency_unit=self._currency(),
        )

    async def async_step_globird_plan(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Select the exact GloBird plan before AEMO spike setup."""
        if user_input is not None:
            plan = user_input.get(CONF_GLOBIRD_PLAN, GLOBIRD_PLAN_NOT_ZEROHERO)
            self._globird_data = {CONF_GLOBIRD_PLAN: plan}
            if plan == GLOBIRD_PLAN_ZEROHERO_CUSTOM:
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
                    self._globird_data[key] = user_input.get(key)
            return await self.async_step_globird_portal()

        return self.async_show_form(
            step_id="globird_plan",
            data_schema=self._globird_plan_schema(),
        )

    async def async_step_globird_portal(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Offer GloBird portal connection during initial setup."""
        if user_input is not None:
            if user_input.get("connect_globird_portal", True):
                return await self.async_step_globird_portal_login()
            return await self.async_step_aemo_config()

        return self.async_show_form(
            step_id="globird_portal",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        "connect_globird_portal", default=True
                    ): BooleanSelector(),
                }
            ),
        )

    async def async_step_globird_portal_login(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Authenticate with the GloBird portal during initial setup."""
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input.get(CONF_GLOBIRD_EMAIL, "")
            password = user_input.get(CONF_GLOBIRD_PASSWORD, "")
            if email and password:
                error = await _validate_globird_credentials(email, password)
                if error is None:
                    self._globird_data[CONF_GLOBIRD_EMAIL] = email
                    self._globird_data[CONF_GLOBIRD_PASSWORD] = password
                    return await self.async_step_aemo_config()
                errors["base"] = error
            else:
                errors["base"] = "invalid_globird_auth"

        return self.async_show_form(
            step_id="globird_portal_login",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_GLOBIRD_EMAIL): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.EMAIL)
                    ),
                    vol.Required(CONF_GLOBIRD_PASSWORD): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_aemo_config(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle AEMO spike detection configuration."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Validate AEMO region is selected if enabled
            aemo_enabled = user_input.get(CONF_AEMO_SPIKE_ENABLED, False)

            if aemo_enabled:
                region = user_input.get(CONF_AEMO_REGION)
                if not region:
                    errors["base"] = "aemo_region_required"
                else:
                    # Store AEMO config
                    self._aemo_data = {
                        CONF_AEMO_SPIKE_ENABLED: True,
                        CONF_AEMO_REGION: region,
                        CONF_AEMO_SPIKE_THRESHOLD: user_input.get(
                            CONF_AEMO_SPIKE_THRESHOLD, 3000.0
                        ),
                    }

                    # Route to battery system selection
                    return await self.async_step_battery_system()
            else:
                # AEMO disabled
                self._aemo_data = {CONF_AEMO_SPIKE_ENABLED: False}

                # Route to battery system selection
                return await self.async_step_battery_system()

        # Build region choices
        region_options = [
            SelectOptionDict(value=k, label=v)
            for k, v in AEMO_REGIONS.items()
        ]

        # Default to enabled if in AEMO-only mode
        default_enabled = self._aemo_only_mode

        data_schema = vol.Schema(
            {
                vol.Optional(CONF_AEMO_SPIKE_ENABLED, default=default_enabled): BooleanSelector(),
                vol.Optional(CONF_AEMO_REGION): SelectSelector(
                    SelectSelectorConfig(
                        options=region_options,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(CONF_AEMO_SPIKE_THRESHOLD, default=3000.0): NumberSelector(
                    NumberSelectorConfig(
                        min=0,
                        max=20000,
                        step=100,
                        unit_of_measurement=self._selector_unit("market_rate"),
                        mode=NumberSelectorMode.BOX,
                    )
                ),
            }
        )

        threshold_hint = (
            "Default: $3,000/MWh. GloBird spike exports use $3,000/MWh. "
            "Adjust only if your plan specifies a different threshold."
        )
        if self._selected_electricity_provider in ("globird", "aemo_vpp"):
            threshold_hint += (
                "\n\nTesla Powerwall users only: set the correct Globird/TOU tariff in "
                "the Tesla app before continuing. After changing the Tesla tariff, "
                "restart Home Assistant or reload PowerSync so the tariff scheduler "
                "fetches and caches the new baseline. Other battery systems, including "
                "Sigenergy and FoxESS cloud, configure the Globird/TOU custom tariff in "
                "PowerSync after selecting the battery system."
            )

        return self.async_show_form(
            step_id="aemo_config",
            data_schema=data_schema,
            errors=errors,
            description_placeholders={
                "threshold_hint": threshold_hint,
            },
        )

    async def async_step_custom_tariff(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure a custom tariff during initial setup."""
        errors: dict[str, str] = {}

        if user_input is not None:
            if user_input.get("skip_tariff", False):
                self._custom_tariff_data = {}
                return await self.async_step_battery_system()

            tariff_type = user_input.get("tariff_type", "tou")
            self._tariff_plan_name = user_input.get("plan_name", "")
            self._tariff_offpeak_rate = user_input.get("offpeak_rate", 15) / 100
            self._tariff_fit_rate = user_input.get("fit_rate", 5) / 100

            if tariff_type == "flat":
                flat_rate = user_input.get("flat_rate", 30) / 100
                self._custom_tariff_data = self._build_tariff_from_periods(
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
                return await self.async_step_battery_system()

            self._tariff_periods = []
            return await self.async_step_tariff_period()

        tariff_type_options = {
            "flat": "Flat Rate (single rate all day)",
            "tou": "Time of Use (multiple periods)",
        }

        return self.async_show_form(
            step_id="custom_tariff",
            data_schema=vol.Schema(
                {
                    vol.Optional("skip_tariff", default=False): BooleanSelector(),
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
                            min=0,
                            max=200,
                            step=0.1,
                            unit_of_measurement=self._selector_unit(),
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required("offpeak_rate", default=15): NumberSelector(
                        NumberSelectorConfig(
                            min=0,
                            max=200,
                            step=0.1,
                            unit_of_measurement=self._selector_unit(),
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required("fit_rate", default=5): NumberSelector(
                        NumberSelectorConfig(
                            min=-100,
                            max=100,
                            step=0.1,
                            unit_of_measurement=self._selector_unit(),
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                }
            ),
            errors=errors,
            description_placeholders={
                "info": (
                    f"Configure your electricity tariff. All rates in "
                    f"{self._selector_unit()}. For TOU, you'll add time periods "
                    "in the next step."
                ),
                "skip_hint": "You can skip this and configure rates later.",
            },
        )

    async def async_step_tariff_period(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Add a custom tariff period during initial setup."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                start_hour = int(user_input.get("period_start", "15:00").split(":")[0])
                end_hour = int(user_input.get("period_end", "21:00").split(":")[0])
            except (ValueError, IndexError):
                start_hour = 15
                end_hour = 21

            self._tariff_periods.append(
                {
                    "name": user_input.get("period_type", "PEAK"),
                    "start": start_hour,
                    "end": end_hour,
                    "days": user_input.get("period_days", "weekdays"),
                    "import_rate": user_input.get("import_rate", 45) / 100,
                    "export_rate": user_input.get("export_rate", 5) / 100,
                }
            )

            if user_input.get("add_another", False):
                return await self.async_step_tariff_period()

            self._custom_tariff_data = self._build_tariff_from_periods(
                self._tariff_periods,
            )
            return await self.async_step_battery_system()

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

        count = len(getattr(self, "_tariff_periods", []))
        added_desc = ""
        if count > 0:
            lines = []
            minor_unit = self._selector_unit()
            day_labels = {
                "weekdays": "Mon-Fri",
                "weekends": "Sat-Sun",
                "all_days": "Mon-Sun",
            }
            for idx, period in enumerate(self._tariff_periods, 1):
                lines.append(
                    f"{idx}. {period['name']} {period['start']:02d}:00-"
                    f"{period['end']:02d}:00 "
                    f"{day_labels.get(period.get('days'), 'Mon-Sun')}, import "
                    f"{period['import_rate'] * 100:.1f}{minor_unit}, export "
                    f"{period['export_rate'] * 100:.1f}{minor_unit}"
                )
            added_desc = "Added periods:\n" + "\n".join(lines) + "\n\n"

        return self.async_show_form(
            step_id="tariff_period",
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
                    vol.Required("period_days", default="weekdays"): SelectSelector(
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
                            min=0,
                            max=200,
                            step=0.1,
                            unit_of_measurement=self._selector_unit(),
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required("export_rate", default=5): NumberSelector(
                        NumberSelectorConfig(
                            min=-100,
                            max=200,
                            step=0.1,
                            unit_of_measurement=self._selector_unit(),
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

    def _build_tariff_from_periods(self, periods: list[dict]) -> dict:
        """Build a Tesla-format tariff from a list of user-defined time periods.

        Each period with different rates gets a unique internal name (e.g. PEAK_1,
        PEAK_2) so the optimizer sees distinct prices for each time block.
        Remaining hours not covered by any period become OFF_PEAK.
        """
        tou_periods: dict[str, list] = {}
        energy_charges: dict[str, float] = {}
        sell_charges: dict[str, float] = {}

        def _day_ranges(scope: str) -> list[tuple[int, int]]:
            if scope == "weekdays":
                return [(1, 5)]
            if scope == "weekends":
                return [(0, 0), (6, 6)]
            return [(0, 6)]

        # Assign unique names when the same period type has different rates
        name_counters: dict[str, int] = {}
        for period in periods:
            base_name = period["name"]

            # Check if an existing period with same name has the same rates
            existing_key = None
            for key in tou_periods:
                if key == base_name or key.startswith(base_name + "_"):
                    if (
                        energy_charges.get(key) == period["import_rate"]
                        and sell_charges.get(key) == period["export_rate"]
                    ):
                        existing_key = key
                        break

            if existing_key:
                # Same rates — add time range to existing period
                unique_name = existing_key
            else:
                # Different rates or new period — create unique name
                if base_name not in name_counters:
                    # First occurrence — use base name
                    if base_name in tou_periods:
                        # Base name taken with different rates — rename it
                        old_periods = tou_periods.pop(base_name)
                        old_import = energy_charges.pop(base_name)
                        old_export = sell_charges.pop(base_name)
                        new_name = f"{base_name}_1"
                        tou_periods[new_name] = old_periods
                        energy_charges[new_name] = old_import
                        sell_charges[new_name] = old_export
                        name_counters[base_name] = 2
                        unique_name = f"{base_name}_2"
                    else:
                        unique_name = base_name
                        name_counters[base_name] = 1
                else:
                    name_counters[base_name] += 1
                    unique_name = f"{base_name}_{name_counters[base_name]}"

            if unique_name not in tou_periods:
                tou_periods[unique_name] = []
            for from_day, to_day in _day_ranges(period.get("days", "weekdays")):
                tou_periods[unique_name].append(
                    {
                        "fromDayOfWeek": from_day,
                        "toDayOfWeek": to_day,
                        "fromHour": period["start"],
                        "toHour": period["end"],
                    }
                )
            energy_charges[unique_name] = period["import_rate"]
            sell_charges[unique_name] = period["export_rate"]

        # Auto-fill remaining hours as OFF_PEAK per day. This lets tariffs have
        # different weekday and weekend definitions while still covering gaps.
        defined_hours_by_day = {day: set() for day in range(7)}

        def _days_between(start: int, end: int) -> list[int]:
            start %= 7
            end %= 7
            if start <= end:
                return list(range(start, end + 1))
            return list(range(start, 7)) + list(range(0, end + 1))

        def _mark_hours(day: int, start: int, end: int) -> None:
            defined_hours_by_day[day % 7].update(range(start, end))

        for period_list in tou_periods.values():
            for p in period_list:
                start_hour = int(p["fromHour"])
                end_hour = int(p["toHour"])
                for day in _days_between(p["fromDayOfWeek"], p["toDayOfWeek"]):
                    if start_hour == end_hour:
                        _mark_hours(day, 0, 24)
                    elif start_hour < end_hour:
                        _mark_hours(day, start_hour, end_hour)
                    else:
                        _mark_hours(day, start_hour, 24)
                        _mark_hours(day + 1, 0, end_hour)

        offpeak_periods = []
        offpeak_gaps: dict[tuple[int, int], list[int]] = {}
        for day, defined_hours in defined_hours_by_day.items():
            gap_start = None
            for h in range(25):
                if h < 24 and h not in defined_hours:
                    if gap_start is None:
                        gap_start = h
                elif gap_start is not None:
                    offpeak_gaps.setdefault((gap_start, h), []).append(day)
                    gap_start = None

        for (from_hour, to_hour), days in offpeak_gaps.items():
            sorted_days = sorted(days)
            range_start = sorted_days[0]
            previous_day = range_start
            for day in sorted_days[1:] + [None]:
                if day is not None and day == previous_day + 1:
                    previous_day = day
                    continue
                offpeak_periods.append(
                    {
                        "fromDayOfWeek": range_start,
                        "toDayOfWeek": previous_day,
                        "fromHour": from_hour,
                        "toHour": to_hour,
                    }
                )
                if day is not None:
                    range_start = previous_day = day

        if not offpeak_periods and not tou_periods:
            offpeak_periods.append(
                {"fromDayOfWeek": 0, "toDayOfWeek": 6, "fromHour": 0, "toHour": 24}
            )

        offpeak_rate = getattr(self, "_tariff_offpeak_rate", 0.15)
        fit_rate = getattr(self, "_tariff_fit_rate", 0.05)

        if offpeak_periods:
            # Use a unique off-peak name if OFF_PEAK is already taken by a user period
            op_name = "OFF_PEAK"
            if op_name in tou_periods:
                op_name = "OFF_PEAK_AUTO"
            tou_periods[op_name] = offpeak_periods
            energy_charges[op_name] = offpeak_rate
            sell_charges[op_name] = fit_rate

        provider_name = {
            "globird": "Globird Energy",
            "aemo_vpp": "VPP Provider",
            "nz": "NZ Provider",
            "other": "Custom Provider",
        }.get(getattr(self, "_selected_electricity_provider", "other"), "Custom")

        plan_name = getattr(self, "_tariff_plan_name", "") or f"{provider_name} TOU"
        tariff_currency = normalize_currency(
            getattr(self, "_tariff_currency", None),
            currency_for_provider(
                getattr(self, "_selected_electricity_provider", "other"),
                getattr(self, "hass", None),
            ),
        )

        return {
            "name": plan_name,
            "utility": provider_name,
            "currency": tariff_currency,
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

    async def async_step_nz_retailer(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle NZ retailer selection."""
        if user_input is not None:
            retailer = user_input.get(CONF_NZ_RETAILER, "nz_custom")
            zone = user_input.get(CONF_NZ_DISTRIBUTION_ZONE, "other")

            # Store NZ config (retailer + zone) for options flow to pick up later
            self._nz_config = {
                CONF_NZ_RETAILER: retailer,
                CONF_NZ_DISTRIBUTION_ZONE: zone,
            }

            # Route to battery system selection
            return await self.async_step_battery_system()

        return self.async_show_form(
            step_id="nz_retailer",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NZ_RETAILER, default="octopus_nz"): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(value=k, label=v)
                                for k, v in NZ_RETAILERS.items()
                            ],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Required(CONF_NZ_DISTRIBUTION_ZONE, default="vector"): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(value=k, label=v)
                                for k, v in NZ_DISTRIBUTION_ZONES.items()
                            ],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
        )

    async def async_step_site_selection(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle site selection for both Amber and Tesla."""
        errors: dict[str, str] = {}

        # Determine if we should show Amber-specific options
        # Show only if: not AEMO-only mode AND we have Amber sites AND not Flow Power (which handles settings separately)
        has_amber_sites = bool(self._amber_sites)
        is_flow_power = self._selected_electricity_provider == "flow_power"
        show_amber_options = (
            not self._aemo_only_mode and has_amber_sites and not is_flow_power
        )

        if user_input is not None:
            gateway_ip = (user_input.get(CONF_POWERWALL_LOCAL_IP) or "").strip()
            # Handle Amber site selection (only if we have Amber sites)
            amber_site_id = None
            if has_amber_sites:
                amber_site_id = user_input.get(CONF_AMBER_SITE_ID)
                if not amber_site_id:
                    # Auto-select: prefer active site, or fall back to first site
                    active_sites = [
                        s for s in self._amber_sites if s.get("status") == "active"
                    ]
                    if len(active_sites) == 1:
                        amber_site_id = active_sites[0]["id"]
                        _LOGGER.info(
                            f"Auto-selected single active Amber site: {amber_site_id}"
                        )
                    elif len(self._amber_sites) == 1:
                        amber_site_id = self._amber_sites[0]["id"]
                        _LOGGER.info(
                            f"Auto-selected single Amber site: {amber_site_id}"
                        )

            # Store site selection data
            self._site_data = {
                CONF_TESLA_ENERGY_SITE_ID: user_input[CONF_TESLA_ENERGY_SITE_ID],
            }

            if gateway_ip:
                self._site_data[CONF_POWERWALL_LOCAL_IP] = gateway_ip

            # Add Amber site if we have one
            if amber_site_id:
                self._site_data[CONF_AMBER_SITE_ID] = amber_site_id

            # For Amber provider (not Flow Power), get settings from this form
            if show_amber_options:
                self._site_data[CONF_AUTO_SYNC_ENABLED] = user_input.get(
                    CONF_AUTO_SYNC_ENABLED, True
                )
                self._site_data[CONF_AMBER_FORECAST_TYPE] = user_input.get(
                    CONF_AMBER_FORECAST_TYPE, "predicted"
                )
                self._site_data[CONF_BATTERY_CURTAILMENT_ENABLED] = user_input.get(
                    CONF_BATTERY_CURTAILMENT_ENABLED, False
                )
            elif self._aemo_only_mode:
                # AEMO-only mode doesn't use Amber sync
                self._site_data[CONF_AUTO_SYNC_ENABLED] = False
            # For Flow Power, these settings are already in _flow_power_data

            # If the user picked Teslemetry as the EV provider but Teslemetry
            # isn't installed in HA, prompt for an API token before finalising.
            if getattr(self, "_tesla_ev_needs_teslemetry_token", False):
                return await self.async_step_tesla_ev_teslemetry_token()

            # Go directly to creating the entry (skip curtailment/weather/demand/EV steps)
            return self._create_final_entry()

        data_schema_dict: dict[vol.Marker, Any] = {}

        if self._tesla_sites:
            # Build Tesla site options from Teslemetry API response
            tesla_site_options = [
                SelectOptionDict(
                    value=str(site.get("energy_site_id")),
                    label=f"{site.get('site_name', 'Tesla Energy Site ' + str(site.get('energy_site_id')))} ({site.get('energy_site_id')})",
                )
                for site in self._tesla_sites
            ]

            data_schema_dict[vol.Required(CONF_TESLA_ENERGY_SITE_ID)] = SelectSelector(
                SelectSelectorConfig(
                    options=tesla_site_options,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )

            # Optional gateway LAN IP for direct local features (snapshot
            # polling, automated curtailment, fast operation-mode toggles).
            # Pairing itself is cloud-based (Fleet API key registration);
            # gateway control uses RSA signing — no password required.
            data_schema_dict[vol.Optional(CONF_POWERWALL_LOCAL_IP, default="")] = str
        else:
            # No sites found - should not happen if validation worked
            _LOGGER.error("No Tesla energy sites found in Teslemetry account")
            return self.async_abort(reason="no_energy_sites")

        # Only add Amber-specific options for Amber provider with Amber sites
        if show_amber_options:
            # Build Amber site options with status indicator
            amber_site_list: list[SelectOptionDict] = []
            default_amber_site = None
            for site in self._amber_sites:
                site_id = site["id"]
                site_nmi = site.get("nmi", site_id)
                site_status = site.get("status", "unknown")

                # Add status indicator to help users identify active vs closed sites
                if site_status == "active":
                    label = f"{site_nmi} (Active)"
                    if default_amber_site is None:
                        default_amber_site = site_id
                elif site_status == "closed":
                    label = f"{site_nmi} (Closed)"
                else:
                    label = f"{site_nmi} ({site_status})"

                amber_site_list.append(SelectOptionDict(value=site_id, label=label))

            # Always show Amber site selection dropdown (so user can see status)
            if amber_site_list:
                data_schema_dict[
                    vol.Required(CONF_AMBER_SITE_ID, default=default_amber_site)
                ] = SelectSelector(
                    SelectSelectorConfig(
                        options=amber_site_list,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                )

            data_schema_dict[vol.Optional(CONF_AUTO_SYNC_ENABLED, default=True)] = BooleanSelector()
            data_schema_dict[
                vol.Optional(CONF_AMBER_FORECAST_TYPE, default="predicted")
            ] = SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(value="predicted", label="Predicted (Default)"),
                        SelectOptionDict(value="low", label="Low (Lower prices expected)"),
                        SelectOptionDict(value="high", label="High (Higher prices expected)"),
                    ],
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
            data_schema_dict[
                vol.Optional(CONF_BATTERY_CURTAILMENT_ENABLED, default=False)
            ] = BooleanSelector()
        elif has_amber_sites and is_flow_power:
            # Flow Power with Amber pricing - show Amber site selection only
            amber_site_list_fp: list[SelectOptionDict] = []
            default_amber_site = None
            for site in self._amber_sites:
                site_id = site["id"]
                site_nmi = site.get("nmi", site_id)
                site_status = site.get("status", "unknown")
                if site_status == "active":
                    label = f"{site_nmi} (Active)"
                    if default_amber_site is None:
                        default_amber_site = site_id
                elif site_status == "closed":
                    label = f"{site_nmi} (Closed)"
                else:
                    label = f"{site_nmi} ({site_status})"
                amber_site_list_fp.append(SelectOptionDict(value=site_id, label=label))

            if amber_site_list_fp:
                data_schema_dict[
                    vol.Required(CONF_AMBER_SITE_ID, default=default_amber_site)
                ] = SelectSelector(
                    SelectSelectorConfig(
                        options=amber_site_list_fp,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                )

        data_schema = vol.Schema(data_schema_dict)

        return self.async_show_form(
            step_id="site_selection",
            data_schema=data_schema,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ):
        """Get the options flow for this handler."""
        from .options import PowerSyncOptionsFlow

        return PowerSyncOptionsFlow()



