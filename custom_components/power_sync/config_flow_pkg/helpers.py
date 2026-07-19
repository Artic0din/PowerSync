"""Unit conversion and validation helpers for PowerSync config flows."""

from __future__ import annotations

# Re-export shared imports/constants into this module namespace so call sites
# that historically lived beside these helpers keep working unchanged.
from ._shared import *  # noqa: F403
from ._shared import (  # noqa: F401
    BATTERY_SYSTEM_CONNECTION_KEYS,
    CONF_NETWORK_TARIFF_COMBINED,
    CUSTOM_TOU_PROVIDER_OPTIONS,
    SUNGROW_LEGACY_DUAL_KEYS,
    _LOGGER,
)

def _build_globird_plan_schema(
    current: dict[str, Any] | None = None,
    *,
    rate_unit: str,
    currency_unit: str,
) -> vol.Schema:
    """Build the shared GloBird plan selector schema."""
    current = current or {}
    hour_options = [
        SelectOptionDict(value=f"{h:02d}:00", label=f"{h:02d}:00")
        for h in range(24)
    ]
    return vol.Schema(
        {
            vol.Required(
                CONF_GLOBIRD_PLAN,
                default=current.get(CONF_GLOBIRD_PLAN, GLOBIRD_PLAN_NOT_ZEROHERO),
            ): SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(value=k, label=v)
                        for k, v in GLOBIRD_PLANS.items()
                    ],
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(
                CONF_GLOBIRD_ZEROHERO_START,
                default=current.get(
                    CONF_GLOBIRD_ZEROHERO_START,
                    DEFAULT_GLOBIRD_ZEROHERO_START,
                ),
            ): SelectSelector(
                SelectSelectorConfig(
                    options=hour_options,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(
                CONF_GLOBIRD_ZEROHERO_END,
                default=current.get(
                    CONF_GLOBIRD_ZEROHERO_END,
                    DEFAULT_GLOBIRD_ZEROHERO_END,
                ),
            ): SelectSelector(
                SelectSelectorConfig(
                    options=hour_options,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(
                CONF_GLOBIRD_ZEROHERO_EXPORT_CAP_KWH,
                default=current.get(
                    CONF_GLOBIRD_ZEROHERO_EXPORT_CAP_KWH,
                    DEFAULT_GLOBIRD_ZEROHERO_EXPORT_CAP_KWH,
                ),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0.0,
                    max=100.0,
                    step=0.1,
                    unit_of_measurement="kWh",
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(
                CONF_GLOBIRD_ZEROHERO_SUPER_EXPORT_RATE,
                default=current.get(
                    CONF_GLOBIRD_ZEROHERO_SUPER_EXPORT_RATE,
                    DEFAULT_GLOBIRD_ZEROHERO_SUPER_EXPORT_RATE,
                ),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0.0,
                    max=100.0,
                    step=0.1,
                    unit_of_measurement=rate_unit,
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(
                CONF_GLOBIRD_ZEROHERO_CREDIT_AMOUNT,
                default=current.get(
                    CONF_GLOBIRD_ZEROHERO_CREDIT_AMOUNT,
                    DEFAULT_GLOBIRD_ZEROHERO_CREDIT_AMOUNT,
                ),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0.0,
                    max=10.0,
                    step=0.01,
                    unit_of_measurement=currency_unit,
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(
                CONF_GLOBIRD_ZEROHERO_IMPORT_LIMIT_KW,
                default=current.get(
                    CONF_GLOBIRD_ZEROHERO_IMPORT_LIMIT_KW,
                    DEFAULT_GLOBIRD_ZEROHERO_IMPORT_LIMIT_KW,
                ),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0.0,
                    max=5.0,
                    step=0.001,
                    unit_of_measurement="kW",
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(
                CONF_GLOBIRD_ZEROCHARGE_START,
                default=current.get(
                    CONF_GLOBIRD_ZEROCHARGE_START,
                    DEFAULT_GLOBIRD_ZEROCHARGE_START,
                ),
            ): SelectSelector(
                SelectSelectorConfig(
                    options=hour_options,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(
                CONF_GLOBIRD_ZEROCHARGE_END,
                default=current.get(
                    CONF_GLOBIRD_ZEROCHARGE_END,
                    DEFAULT_GLOBIRD_ZEROCHARGE_END,
                ),
            ): SelectSelector(
                SelectSelectorConfig(
                    options=hour_options,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(
                CONF_GLOBIRD_ZEROCHARGE_IMPORT_CAP_KWH,
                default=current.get(
                    CONF_GLOBIRD_ZEROCHARGE_IMPORT_CAP_KWH,
                    DEFAULT_GLOBIRD_ZEROCHARGE_IMPORT_CAP_KWH,
                ),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0.0,
                    max=200.0,
                    step=0.1,
                    unit_of_measurement="kWh",
                    mode=NumberSelectorMode.BOX,
                )
            ),
        }
    )


async def _validate_globird_credentials(email: str, password: str) -> str | None:
    """Validate GloBird portal credentials and return a config-flow error key."""
    from ..globird_api import (
        GloBirdAuthError,
        GloBirdCaptchaRequired,
        GloBirdClient,
    )

    client = GloBirdClient()
    try:
        await client.authenticate(email, password)
    except GloBirdCaptchaRequired:
        return "captcha_required"
    except GloBirdAuthError:
        return "invalid_globird_auth"
    except Exception as err:
        _LOGGER.exception("GloBird portal credential validation failed: %s", err)
        return "cannot_connect"
    finally:
        await client.close()
    return None


def _normalize_neovolt_entry_ids(
    raw_entry_ids: Any,
    fallback_entry_id: str | None = None,
) -> list[str]:
    """Normalize Neovolt selector values to a list of entry ids."""
    if isinstance(raw_entry_ids, (list, tuple)):
        entry_ids = [entry_id for entry_id in raw_entry_ids if entry_id]
    elif isinstance(raw_entry_ids, str) and raw_entry_ids:
        entry_ids = [raw_entry_ids]
    else:
        entry_ids = []

    if not entry_ids and fallback_entry_id:
        entry_ids = [fallback_entry_id]
    return entry_ids


def _parse_neovolt_capacities_kwh(raw_value: Any, stack_count: int) -> list[float]:
    """Parse optional comma-separated Neovolt stack capacities in selected-entry order."""
    if raw_value in (None, "", []):
        return []
    if isinstance(raw_value, (list, tuple)):
        raw_parts = list(raw_value)
    else:
        raw_parts = [
            part.strip()
            for part in str(raw_value).replace(";", ",").split(",")
            if part.strip()
        ]

    capacities: list[float] = []
    for raw_part in raw_parts:
        raw_capacity = str(raw_part).strip().lower().removesuffix("kwh").strip()
        try:
            capacity = float(raw_capacity)
        except (TypeError, ValueError) as exc:
            raise ValueError("capacity_invalid") from exc
        if capacity <= 0:
            raise ValueError("capacity_must_be_positive")
        capacities.append(capacity)

    if stack_count <= 1 and len(capacities) > 1:
        capacities = [sum(capacities)]
    elif stack_count > 1 and len(capacities) == 1:
        capacities = capacities * stack_count
    return capacities


def _normalize_neovolt_capacities_text(raw_value: Any) -> str:
    """Normalize the user's Neovolt capacity text without changing its meaning."""
    if raw_value in (None, "", []):
        return ""
    if isinstance(raw_value, (list, tuple)):
        raw_parts = [str(part).strip() for part in raw_value if str(part).strip()]
    else:
        raw_parts = [
            part.strip()
            for part in str(raw_value).replace(";", ",").split(",")
            if part.strip()
        ]
    return ", ".join(raw_parts)


def _format_neovolt_capacities_kwh(raw_value: Any) -> str:
    """Format stored Neovolt capacities for the config/options form."""
    if raw_value in (None, "", []):
        return ""
    if not isinstance(raw_value, (list, tuple)):
        return str(raw_value)
    return ", ".join(f"{float(capacity):g}" for capacity in raw_value)


def _stored_wh_to_kwh(value: Any, default_wh: int) -> float:
    """Convert a stored Wh/kWh value to kWh for config flow display."""
    try:
        amount = float(value)
    except (TypeError, ValueError):
        amount = float(default_wh)
    return amount / 1000.0 if amount > 1000 else amount


def _stored_w_to_kw(value: Any, default_w: int) -> float:
    """Convert a stored W/kW value to kW for config flow display."""
    try:
        amount = float(value)
    except (TypeError, ValueError):
        amount = float(default_w)
    return amount / 1000.0 if amount > 100 else amount


def _stored_optional_w_to_kw(value: Any) -> float | None:
    """Convert an optional stored W/kW value to kW for config flow display."""
    if value in (None, "", []):
        return None
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    if amount < 0:
        return None
    return amount / 1000.0 if amount > 100 else amount


def _stored_ratio_to_percent(value: Any, default_ratio: float) -> int:
    """Convert a stored 0-1 ratio or 0-100 percent to a clamped whole percent."""
    try:
        amount = float(value)
    except (TypeError, ValueError):
        amount = float(default_ratio)
    if amount <= 1:
        amount *= 100
    return max(0, min(100, int(round(amount))))


def _stored_optional_price_to_cents(value: Any) -> float:
    """Convert optional stored $/kWh or c/kWh to c/kWh for form display."""
    if value in (None, "", []):
        return 0.0
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return 0.0
    if amount <= 0:
        return 0.0
    return amount * 100.0 if amount <= 1 else amount


def _normalize_optional_entity(value: Any) -> str | None:
    """Return a usable entity id, or None for unset optional entity fields."""
    if not isinstance(value, str):
        return None

    entity_id = value.strip()
    if not entity_id or entity_id.lower() == "none":
        return None
    return entity_id


def _foxess_modbus_entry_options(hass: HomeAssistant) -> list[SelectOptionDict]:
    """Return selectable Nathan Marlor foxess_modbus config entries."""
    return [
        SelectOptionDict(value=entry.entry_id, label=entry.title or entry.entry_id)
        for entry in hass.config_entries.async_entries("foxess_modbus")
    ]


async def _validate_foxess_entity_bridge(
    hass: HomeAssistant,
    entry_id: str,
    entity_prefix: str,
) -> tuple[bool, str | None]:
    """Validate foxess_modbus entity bridge setup."""
    if not entry_id and not entity_prefix:
        return False, "foxess_entity_required"
    try:
        from ..inverters.foxess_entity import FoxESSEntityController

        controller = FoxESSEntityController(
            hass,
            foxess_entry_id=entry_id or None,
            entity_prefix=entity_prefix,
        )
        await controller.connect()
        return True, None
    except ValueError as exc:
        _LOGGER.warning("FoxESS entity bridge validation failed: %s", exc)
        return False, "foxess_entity_missing_entities"
    except Exception as exc:
        _LOGGER.error("FoxESS entity bridge setup error: %s", exc)
        return False, "foxess_entity_connect_failed"


def _form_kwh_to_wh(value: Any, default_kwh: float) -> int:
    """Convert a config flow kWh field to Wh for persisted optimizer config."""
    try:
        amount = float(value)
    except (TypeError, ValueError):
        amount = default_kwh
    return int(round(amount * 1000))


def _form_kw_to_w(value: Any, default_kw: float) -> int:
    """Convert a config flow kW field to W for persisted optimizer config."""
    try:
        amount = float(value)
    except (TypeError, ValueError):
        amount = default_kw
    return int(round(amount * 1000))


def _form_optional_kw_to_w(value: Any) -> int | None:
    """Convert an optional config flow kW field to W, preserving explicit zero."""
    if value in (None, "", []):
        return None
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    if amount < 0:
        return None
    return int(round(amount * 1000))


def _form_optional_cents_to_price(value: Any) -> float | None:
    """Convert optional c/kWh form input to stored $/kWh."""
    if value in (None, "", []):
        return None
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    if amount <= 0:
        return None
    return amount / 100.0 if amount > 1 else amount


def _form_percent_to_ratio(value: Any, default_ratio: float) -> float:
    """Convert a config flow percent field to a stored 0-1 ratio."""
    try:
        amount = float(value)
    except (TypeError, ValueError):
        amount = default_ratio * 100
    return max(0.0, min(1.0, amount / 100.0))


def _default_optimizer_specs_for(battery_system: str) -> tuple[int, int, int]:
    capacity_wh = BATTERY_CAPACITY_DEFAULTS.get(
        battery_system,
        BATTERY_CAPACITY_DEFAULTS[BATTERY_SYSTEM_TESLA],
    )
    power_w = BATTERY_POWER_DEFAULTS.get(
        battery_system,
        BATTERY_POWER_DEFAULTS[BATTERY_SYSTEM_TESLA],
    )
    return capacity_wh, power_w, power_w


def _optimization_provider_options_for_battery(
    battery_system: str | None,
) -> dict[str, str]:
    """Return native and Smart Optimization labels for a battery system."""
    if battery_system == BATTERY_SYSTEM_CUSTOM:
        return {
            OPT_PROVIDER_POWERSYNC: "Smart Optimization planner (monitoring mode)",
        }
    native_name = OPTIMIZATION_PROVIDER_NATIVE_NAMES.get(
        battery_system or BATTERY_SYSTEM_TESLA,
        "Battery",
    )
    return {
        OPT_PROVIDER_NATIVE: f"{native_name} built-in optimization",
        OPT_PROVIDER_POWERSYNC: "Smart Optimization (Built-in LP)",
    }


async def validate_amber_token(hass: HomeAssistant, api_token: str) -> dict[str, Any]:
    """Validate the Amber API token."""
    session = async_get_clientsession(hass)
    headers = {"Authorization": f"Bearer {api_token}"}

    try:
        async with session.get(
            f"{AMBER_API_BASE_URL}/sites",
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as response:
            if response.status == 200:
                sites = await response.json()
                if sites and len(sites) > 0:
                    return {
                        "success": True,
                        "sites": sites,
                    }
                else:
                    return {"success": False, "error": "no_sites"}
            elif response.status == 401:
                return {"success": False, "error": "invalid_auth"}
            else:
                return {"success": False, "error": "cannot_connect"}
    except aiohttp.ClientError:
        return {"success": False, "error": "cannot_connect"}
    except Exception as err:
        _LOGGER.exception("Unexpected error validating Amber token: %s", err)
        return {"success": False, "error": "unknown"}


async def validate_flow_power_api_key(
    hass: HomeAssistant,
    api_key: str,
    region: str = "NSW1",
) -> dict[str, Any]:
    """Validate Flow Power KWatch API key and return residential sites when available."""
    if not api_key:
        return {"success": False, "error": "invalid_api_key"}

    site_lookup_error: str | None = None
    try:
        from ..flow_power_api import FlowPowerAPIClient, FlowPowerAPIError

        client = FlowPowerAPIClient(api_key, async_get_clientsession(hass))
        sites = await client.get_residential_sites()
    except FlowPowerAPIError as err:
        if str(err) == "invalid_api_key":
            return {"success": False, "error": "invalid_api_key"}
        site_lookup_error = str(err)
        sites = []
    except aiohttp.ClientError:
        site_lookup_error = "cannot_connect"
        sites = []
    except Exception as err:
        _LOGGER.exception("Flow Power API validation failed: %s", err)
        site_lookup_error = "cannot_connect"
        sites = []

    if sites:
        return {"success": True, "sites": sites}

    api_region = FLOW_POWER_KWATCH_REGIONS.get(region, str(region).lower())
    try:
        dispatch = await client.dispatch5mins(api_region, period=60)
        forecast = await client.predispatch30mins(api_region, period=1)
    except FlowPowerAPIError as err:
        if str(err) == "invalid_api_key":
            return {"success": False, "error": "invalid_api_key"}
        return {"success": False, "error": "cannot_connect"}
    except aiohttp.ClientError:
        return {"success": False, "error": "cannot_connect"}
    except Exception as err:
        _LOGGER.exception("Flow Power API price validation failed: %s", err)
        return {"success": False, "error": "cannot_connect"}

    if dispatch and forecast:
        return {
            "success": True,
            "sites": [],
            "site_lookup_error": site_lookup_error or "no_sites",
        }
    return {"success": False, "error": "cannot_connect" if site_lookup_error else "no_sites"}


def _flow_power_site_label(site: dict[str, Any]) -> str:
    """Return a display label for a Flow Power site."""
    nmi = site.get("nmi", "")
    tariff = site.get("networkTariff")
    return f"{nmi} — {tariff}" if tariff else str(nmi)


async def _prefill_flow_power_network_tariff(
    hass: HomeAssistant,
    flow_data: dict[str, Any],
    site: dict[str, Any] | None,
) -> None:
    """Prefill Flow Power network tariff from KWatch site metadata when unset."""
    if not site:
        return
    network_tariff = site.get("networkTariff")
    if network_tariff:
        flow_data[CONF_FLOWPOWER_NETWORK_TARIFF] = network_tariff
    if flow_data.get(CONF_FP_NETWORK) or flow_data.get(CONF_FP_TARIFF_CODE):
        return
    if not network_tariff:
        return

    wanted_codes = [
        part.strip()
        for part in str(network_tariff).replace(";", ",").split(",")
        if part.strip()
    ]
    if not wanted_codes:
        return

    from ..tariff_utils import get_tariff_codes_for_network

    region = flow_data.get(CONF_FLOW_POWER_STATE, "NSW1")
    for network_name in REGION_NETWORKS.get(region, []):
        codes = await hass.async_add_executor_job(
            get_tariff_codes_for_network,
            network_name,
        )
        for wanted in wanted_codes:
            if wanted in codes:
                api_name = NETWORK_API_NAME.get(network_name, network_name.lower())
                flow_data[CONF_FP_NETWORK] = network_name
                flow_data[CONF_FP_TARIFF_CODE] = wanted
                flow_data[CONF_NETWORK_DISTRIBUTOR] = api_name
                flow_data[CONF_NETWORK_TARIFF_CODE] = wanted
                return


async def validate_localvolts_credentials(
    hass: HomeAssistant, api_key: str, partner_id: str, nmi: str
) -> dict[str, Any]:
    """Validate Localvolts API credentials by fetching current interval."""
    from ..localvolts_api import LocalvoltsClient

    session = async_get_clientsession(hass)
    client = LocalvoltsClient(session, api_key, partner_id)
    try:
        return await client.validate_credentials(nmi)
    except Exception:
        return {"success": False, "error": "cannot_connect"}


async def validate_teslemetry_token(
    hass: HomeAssistant, api_token: str
) -> dict[str, Any]:
    """Validate the Teslemetry API token and get sites."""
    session = async_get_clientsession(hass)
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }

    try:
        async with session.get(
            f"{TESLEMETRY_API_BASE_URL}/api/1/products",
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as response:
            if response.status == 200:
                data = await response.json()
                products = data.get("response", [])

                # Filter for energy sites
                energy_sites = [p for p in products if "energy_site_id" in p]

                if energy_sites:
                    return {
                        "success": True,
                        "sites": energy_sites,
                    }
                else:
                    return {"success": False, "error": "no_energy_sites"}
            elif response.status == 401:
                return {"success": False, "error": "invalid_auth"}
            else:
                error_text = await response.text()
                _LOGGER.error(
                    "Teslemetry API error %s: %s", response.status, error_text[:200]
                )
                return {"success": False, "error": "cannot_connect"}
    except aiohttp.ClientError as err:
        _LOGGER.exception("Error connecting to Teslemetry API: %s", err)
        return {"success": False, "error": "cannot_connect"}
    except Exception as err:
        _LOGGER.exception("Unexpected error validating Teslemetry token: %s", err)
        return {"success": False, "error": "unknown"}


async def _validate_fleet_api_token_at(
    hass: HomeAssistant, api_token: str, base_url: str
) -> dict[str, Any]:
    """Validate a Fleet API token against a specific base URL."""
    session = async_get_clientsession(hass)
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }
    async with session.get(
        f"{base_url}/api/1/products",
        headers=headers,
        timeout=aiohttp.ClientTimeout(total=30),
    ) as response:
        if response.status == 200:
            data = await response.json()
            products = data.get("response", [])
            energy_sites = [p for p in products if "energy_site_id" in p]
            if energy_sites:
                return {"success": True, "sites": energy_sites, "base_url": base_url}
            return {"success": False, "error": "no_energy_sites"}
        if response.status == 401:
            return {"success": False, "error": "invalid_auth"}
        if response.status == 421:
            error_text = await response.text()
            return {"success": False, "error": "out_of_region", "error_text": error_text}
        error_text = await response.text()
        _LOGGER.error("Fleet API error %s: %s", response.status, error_text[:200])
        return {"success": False, "error": "cannot_connect"}


async def validate_fleet_api_token(
    hass: HomeAssistant, api_token: str
) -> dict[str, Any]:
    """Validate the Fleet API token and get sites.

    On a 421 "user out of region" response, Tesla returns the correct regional
    base URL in the error body.  We parse it out and retry automatically so EU
    and AP users don't hit a dead end during setup.
    """
    try:
        result = await _validate_fleet_api_token_at(hass, api_token, FLEET_API_BASE_URL)
        if result.get("error") == "out_of_region":
            import re
            error_text = result.get("error_text", "")
            match = re.search(r"use base URL:\s*(https://[^\s,]+)", error_text)
            if match:
                regional_url = match.group(1).rstrip("/")
                _LOGGER.info(
                    "Fleet API 421 — retrying with regional endpoint: %s", regional_url
                )
                return await _validate_fleet_api_token_at(hass, api_token, regional_url)
            _LOGGER.error("Fleet API 421 but could not parse regional URL from: %s", error_text[:300])
            return {"success": False, "error": "cannot_connect"}
        return result
    except aiohttp.ClientError as err:
        _LOGGER.exception("Error connecting to Fleet API: %s", err)
        return {"success": False, "error": "cannot_connect"}
    except Exception as err:
        _LOGGER.exception("Unexpected error validating Fleet API token: %s", err)
        return {"success": False, "error": "unknown"}


async def validate_powersync_token(
    hass: HomeAssistant, api_token: str
) -> dict[str, Any]:
    """Validate a PowerSync.cc proxy token and fetch the user's energy sites.

    PowerSync tokens look like `psync_<43 base64url chars>`. They authenticate
    against the PowerSync.cc cloud proxy which forwards to Tesla's Fleet API
    on the user's behalf, handling OAuth refresh transparently.
    """
    if not api_token or not api_token.startswith("psync_"):
        return {"success": False, "error": "invalid_token_format"}

    session = async_get_clientsession(hass)
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }

    try:
        async with session.get(
            f"{POWERSYNC_API_BASE_URL}/api/1/products",
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as response:
            if response.status == 200:
                data = await response.json()
                products = data.get("response", [])

                energy_sites = [p for p in products if "energy_site_id" in p]

                if energy_sites:
                    return {"success": True, "sites": energy_sites}
                return {"success": False, "error": "no_energy_sites"}
            if response.status == 401:
                return {"success": False, "error": "invalid_auth"}
            error_text = await response.text()
            _LOGGER.error(
                "PowerSync proxy error %s: %s", response.status, error_text[:200]
            )
            return {"success": False, "error": "cannot_connect"}
    except aiohttp.ClientError as err:
        _LOGGER.exception("Error connecting to PowerSync proxy: %s", err)
        return {"success": False, "error": "cannot_connect"}
    except Exception as err:
        _LOGGER.exception("Unexpected error validating PowerSync token: %s", err)
        return {"success": False, "error": "unknown"}


def _detect_tesla_ev_integrations(hass: HomeAssistant) -> dict[str, bool]:
    """Detect whether the Tesla Fleet and Teslemetry HA integrations are loaded.

    Returns a dict like ``{"tesla_fleet": True, "teslemetry": False}`` so the
    config flow can label EV provider options with their detection status.
    """
    result = {"tesla_fleet": False, "teslemetry": False}
    for integration in ("tesla_fleet", "teslemetry"):
        for entry in hass.config_entries.async_entries(integration):
            if entry.state == ConfigEntryState.LOADED:
                result[integration] = True
                break
    return result


def _build_tesla_ev_provider_choices(hass: HomeAssistant) -> dict[str, str]:
    """Build the Tesla EV API provider dropdown options with detection annotations."""
    detected = _detect_tesla_ev_integrations(hass)
    fleet_label = "Tesla Fleet API"
    if detected["tesla_fleet"]:
        fleet_label += " — detected in Home Assistant"
    else:
        fleet_label += " — requires Tesla Fleet integration (not installed)"

    teslemetry_label = "Teslemetry (~$4/month)"
    if detected["teslemetry"]:
        teslemetry_label += " — detected in Home Assistant"
    else:
        teslemetry_label += " — will ask for API token"

    return {
        TESLA_EV_API_PROVIDER_NONE: "None — BLE/OCPP, Powerwall only",
        TESLA_EV_API_PROVIDER_FLEET_API: fleet_label,
        TESLA_EV_API_PROVIDER_TESLEMETRY: teslemetry_label,
    }


async def validate_sigenergy_credentials(
    hass: HomeAssistant,
    username: str,
    pass_enc: str,
    device_id: str,
    cloud_region: str = DEFAULT_SIGENERGY_CLOUD_REGION,
) -> dict[str, Any]:
    """Validate Sigenergy credentials and get stations list."""
    from ..sigenergy_api import SigenergyAPIClient

    try:
        session = async_get_clientsession(hass)
        client = SigenergyAPIClient(
            username=username,
            pass_enc=pass_enc,
            device_id=device_id,
            cloud_region=cloud_region,
            session=session,
        )

        # Authenticate
        auth_result = await client.authenticate()
        if "error" in auth_result:
            _LOGGER.error(f"Sigenergy auth failed: {auth_result['error']}")
            return {"success": False, "error": "invalid_auth"}

        # Authentication succeeded - save tokens
        result = {
            "success": True,
            "auth_success": True,
            "access_token": auth_result.get("access_token"),
            "refresh_token": auth_result.get("refresh_token"),
            "expires_at": auth_result.get("expires_at"),
        }

        # Try to get stations (may fail with 404 on some accounts)
        stations_result = await client.get_stations()
        if "error" in stations_result:
            _LOGGER.warning(
                f"Sigenergy get stations failed: {stations_result['error']} - manual station ID required"
            )
            result["stations"] = []
            result["stations_error"] = stations_result["error"]
        else:
            stations = stations_result.get("stations", [])
            result["stations"] = stations

        return result

    except Exception as err:
        _LOGGER.exception("Unexpected error validating Sigenergy credentials: %s", err)
        return {"success": False, "error": "unknown"}


async def test_sungrow_connection(
    hass: HomeAssistant,
    host: str,
    port: int = 502,
    slave_id: int = 1,
) -> dict[str, Any]:
    """Test Sungrow Modbus connection by reading battery SOC."""
    from ..inverters.sungrow_sh import SungrowSHController

    try:
        controller = SungrowSHController(host=host, port=port, slave_id=slave_id)
        controller.TIMEOUT_SECONDS = 3.0
        async with controller:
            # The setup test only needs a core battery block read. Some
            # Sungrow/WiNet firmware times out on optional load/export
            # registers, which should not block creating the entry.
            data = await controller.get_setup_battery_data()
            if data and "battery_soc" in data:
                soc = data.get("battery_soc", 0)
                soh = data.get("battery_soh", 0)
                # Reject garbage Modbus reads (0xFFFF = 6553.5%)
                # Often caused by another integration holding the Modbus port
                if soc > 100 or soh > 100:
                    _LOGGER.warning(
                        "Sungrow connection test returned invalid SOC=%.1f%% SOH=%.1f%% "
                        "(possible Modbus conflict — check for other integrations using port %d)",
                        soc,
                        soh,
                        port,
                    )
                    return {"success": False, "error": "modbus_conflict"}
                return {
                    "success": True,
                    "battery_soc": soc,
                    "battery_soh": soh,
                }
            else:
                return {"success": False, "error": "cannot_connect"}
    except Exception as err:
        _LOGGER.error("Sungrow connection test failed: %s", err)
        return {"success": False, "error": "cannot_connect"}


async def test_foxess_connection(
    hass: HomeAssistant,
    host: str,
    port: int = 502,
    slave_id: int = 247,
    connection_type: str = "tcp",
    serial_port: str | None = None,
    baudrate: int = 9600,
) -> dict[str, Any]:
    """Test FoxESS Modbus connection by detecting model and reading battery SOC."""
    from ..inverters.foxess import FoxESSController

    try:
        controller = FoxESSController(
            host=host,
            port=port,
            slave_id=slave_id,
            connection_type=connection_type,
            serial_port=serial_port,
            baudrate=baudrate,
        )
        async with controller:
            # Auto-detect model family
            model_family = await controller.detect_model()

            # Try to read battery SOC
            data = await controller.get_battery_data()
            if data and "battery_soc" in data:
                return {
                    "success": True,
                    "battery_soc": data.get("battery_soc"),
                    "model_family": model_family.value,
                }
            else:
                return {"success": False, "error": "cannot_connect"}
    except Exception as err:
        _LOGGER.error("FoxESS connection test failed: %s", err)
        return {"success": False, "error": "cannot_connect"}


async def test_goodwe_connection(
    hass: HomeAssistant,
    host: str,
    port: int = 8899,
) -> dict[str, Any]:
    """Test GoodWe connection and return inverter info."""
    import goodwe

    try:
        inverter = await goodwe.connect(host=host, port=port, timeout=5, retries=2)
        await inverter.read_device_info()
        # Check if battery-capable (ET/ES family has set_ongrid_battery_dod)
        has_battery = hasattr(inverter, "set_ongrid_battery_dod")
        return {
            "success": True,
            "model_name": inverter.model_name,
            "serial_number": inverter.serial_number,
            "rated_power": inverter.rated_power,
            "has_battery": has_battery,
        }
    except Exception as err:
        _LOGGER.error("GoodWe connection test failed: %s", err)
        return {"success": False, "error": str(err)}


def validate_goodwe_ems_entity_prefix(
    hass: HomeAssistant,
    prefix: str | None,
) -> str | None:
    """Validate optional GoodWe EMS relay entities from the HA GoodWe integration."""
    if not prefix:
        return None

    prefix = prefix.strip()
    if not prefix:
        return None

    required_entities = (
        f"select.{prefix}_ems_mode",
        f"number.{prefix}_ems_power_limit",
    )
    missing = [
        entity_id
        for entity_id in required_entities
        if hass.states.get(entity_id) is None
    ]
    if missing:
        _LOGGER.warning(
            "GoodWe EMS entity prefix '%s' is missing required entities: %s",
            prefix,
            ", ".join(missing),
        )
        return "goodwe_ems_entities_missing"

    return None


async def resolve_goodwe_entity_telemetry_prefix(
    hass: HomeAssistant,
    prefix: str | None,
) -> str:
    """Return a validated GoodWe telemetry entity prefix, or empty string."""
    from ..inverters.goodwe_entity import GoodWeEntityTelemetryController

    controller = GoodWeEntityTelemetryController(hass, entity_prefix=prefix or "")
    try:
        await controller.connect()
        return controller.entity_prefix
    except Exception as err:
        _LOGGER.debug("GoodWe entity telemetry validation failed: %s", err)
        return ""


def _goodwe_ems_prefix_exists(hass: HomeAssistant, prefix: str) -> bool:
    """Return whether a GoodWe EMS prefix has the required HA entity pair."""
    return (
        hass.states.get(f"select.{prefix}_ems_mode") is not None
        and hass.states.get(f"number.{prefix}_ems_power_limit") is not None
    )


def _goodwe_ems_prefix_candidates(hass: HomeAssistant) -> list[str]:
    """Return GoodWe EMS prefixes with both required HA entities loaded."""
    try:
        mode_entity_ids = hass.states.async_entity_ids("select")
    except TypeError:
        mode_entity_ids = [
            entity_id
            for entity_id in hass.states.async_entity_ids()
            if entity_id.startswith("select.")
        ]

    candidates: list[str] = []
    for entity_id in mode_entity_ids:
        if not entity_id.startswith("select.") or not entity_id.endswith("_ems_mode"):
            continue
        prefix = entity_id.removeprefix("select.").removesuffix("_ems_mode")
        if hass.states.get(f"number.{prefix}_ems_power_limit") is not None:
            candidates.append(prefix)

    return sorted(set(candidates))


def resolve_goodwe_ems_entity_prefix(
    hass: HomeAssistant,
    prefix: str | None,
) -> str:
    """Resolve a typed GoodWe EMS prefix, auto-detecting when needed."""
    typed_prefix = (prefix or "").strip()
    if typed_prefix and _goodwe_ems_prefix_exists(hass, typed_prefix):
        return typed_prefix

    candidates = _goodwe_ems_prefix_candidates(hass)
    if typed_prefix in candidates:
        return typed_prefix
    if "goodwe" in candidates:
        return "goodwe"
    if len(candidates) == 1:
        return candidates[0]

    return typed_prefix


def resolve_goodwe_ems_control_mode(mode: str | None, prefix: str | None) -> str:
    """Return the GoodWe EMS control mode, preserving legacy prefix configs."""
    if mode in (GOODWE_EMS_CONTROL_DIRECT, GOODWE_EMS_CONTROL_ENTITY):
        return mode
    return (
        GOODWE_EMS_CONTROL_ENTITY
        if (prefix or "").strip()
        else GOODWE_EMS_CONTROL_DIRECT
    )


def resolve_goodwe_ems_control_mode_for_protocol(
    hass: HomeAssistant,
    mode: str | None,
    prefix: str | None,
    protocol: str | None,
) -> str:
    """Prefer EMS entity control for GoodWe TCP setups when entities exist."""
    resolved_mode = resolve_goodwe_ems_control_mode(mode, prefix)
    if (
        resolved_mode == GOODWE_EMS_CONTROL_DIRECT
        and protocol == "tcp"
        and resolve_goodwe_ems_entity_prefix(hass, prefix)
    ):
        return GOODWE_EMS_CONTROL_ENTITY
    return resolved_mode


def validate_goodwe_ems_control_mode(
    hass: HomeAssistant,
    mode: str | None,
    prefix: str | None,
) -> str | None:
    """Validate the selected GoodWe EMS command path."""
    mode = resolve_goodwe_ems_control_mode(mode, prefix)
    if mode == GOODWE_EMS_CONTROL_DIRECT:
        return None
    if not (prefix or "").strip():
        return "goodwe_ems_prefix_required"
    return validate_goodwe_ems_entity_prefix(hass, prefix)


def goodwe_ems_control_options() -> list[SelectOptionDict]:
    """Return labels for the GoodWe EMS command-path selector."""
    return [
        SelectOptionDict(
            value=GOODWE_EMS_CONTROL_DIRECT,
            label="Direct IP control",
        ),
        SelectOptionDict(
            value=GOODWE_EMS_CONTROL_ENTITY,
            label="Home Assistant entity control",
        ),
    ]


def resolve_goodwe_port(protocol: str, port: int | None) -> int:
    """Resolve GoodWe port defaults when the user switches protocol."""
    if protocol == "tcp" and (port is None or port == DEFAULT_GOODWE_PORT_UDP):
        return DEFAULT_GOODWE_PORT_TCP
    if protocol == "udp" and port is None:
        return DEFAULT_GOODWE_PORT_UDP
    return port if port is not None else DEFAULT_GOODWE_PORT_UDP

__all__ = [
    '_build_globird_plan_schema',
    '_validate_globird_credentials',
    '_normalize_neovolt_entry_ids',
    '_parse_neovolt_capacities_kwh',
    '_normalize_neovolt_capacities_text',
    '_format_neovolt_capacities_kwh',
    '_stored_wh_to_kwh',
    '_stored_w_to_kw',
    '_stored_optional_w_to_kw',
    '_stored_ratio_to_percent',
    '_stored_optional_price_to_cents',
    '_normalize_optional_entity',
    '_foxess_modbus_entry_options',
    '_validate_foxess_entity_bridge',
    '_form_kwh_to_wh',
    '_form_kw_to_w',
    '_form_optional_kw_to_w',
    '_form_optional_cents_to_price',
    '_form_percent_to_ratio',
    '_default_optimizer_specs_for',
    '_optimization_provider_options_for_battery',
    'validate_amber_token',
    'validate_flow_power_api_key',
    '_flow_power_site_label',
    '_prefill_flow_power_network_tariff',
    'validate_localvolts_credentials',
    'validate_teslemetry_token',
    '_validate_fleet_api_token_at',
    'validate_fleet_api_token',
    'validate_powersync_token',
    '_detect_tesla_ev_integrations',
    '_build_tesla_ev_provider_choices',
    'validate_sigenergy_credentials',
    'test_sungrow_connection',
    'test_foxess_connection',
    'test_goodwe_connection',
    'validate_goodwe_ems_entity_prefix',
    'resolve_goodwe_entity_telemetry_prefix',
    '_goodwe_ems_prefix_exists',
    '_goodwe_ems_prefix_candidates',
    'resolve_goodwe_ems_entity_prefix',
    'resolve_goodwe_ems_control_mode',
    'resolve_goodwe_ems_control_mode_for_protocol',
    'validate_goodwe_ems_control_mode',
    'goodwe_ems_control_options',
    'resolve_goodwe_port',
]
