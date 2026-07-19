"""HTTP views for PowerSync."""
from __future__ import annotations

import logging
from aiohttp import web
from datetime import timezone
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from typing import Any
from ..const import (
    DOMAIN,
    CONF_SOLAR_FORECAST_PROVIDER,
    CONF_SOLCAST_ENABLED,
    CONF_SOLCAST_API_KEY,
    CONF_SOLCAST_RESOURCE_ID,
    CONF_SOLCAST_ESTIMATE_TYPE,
    DEFAULT_SOLCAST_ESTIMATE_TYPE,
    SOLCAST_ESTIMATE_TYPES,
    CONF_OPENWEATHERMAP_API_KEY,
)
from .. import (
    _LOGGER,
    _SOLCAST_SETTINGS_KEYS,
    _get_available_ev_vehicles,
    _has_external_solcast_integration,
    _normalize_solar_forecast_provider,
    _solcast_builtin_configured,
)

class AutomationsView(HomeAssistantView):
    """HTTP view to manage automations for mobile app."""

    url = "/api/power_sync/automations"
    name = "api:power_sync:automations"
    requires_auth = True

    def __init__(self, hass: HomeAssistant):
        """Initialize the view."""
        self._hass = hass

    def _get_store(self):
        """Get the automation store from hass.data."""
        if DOMAIN not in self._hass.data:
            return None
        return self._hass.data[DOMAIN].get("automation_store")

    async def get(self, request: web.Request) -> web.Response:
        """Handle GET request - list all automations."""
        _LOGGER.info("📱 Automations HTTP GET request")

        store = self._get_store()
        if not store:
            return web.json_response(
                {"success": False, "error": "Automation store not initialized"},
                status=503
            )

        try:
            automations = store.get_all()
            available_vehicles = _get_available_ev_vehicles(self._hass)
            return web.json_response({
                "success": True,
                "automations": automations,
                "available_vehicles": available_vehicles,
            })
        except Exception as e:
            _LOGGER.error(f"Error fetching automations: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500
            )

    async def post(self, request: web.Request) -> web.Response:
        """Handle POST request - create new automation."""
        _LOGGER.info("📱 Automations HTTP POST request")

        store = self._get_store()
        if not store:
            return web.json_response(
                {"success": False, "error": "Automation store not initialized"},
                status=503
            )

        try:
            data = await request.json()
            _LOGGER.debug(f"📱 Creating automation with data: name={data.get('name')}, actions={data.get('actions')}")
            # Ensure store._data has required keys (recovery from corrupted state)
            if not hasattr(store, '_data') or store._data is None:
                store._data = {}
            if "automations" not in store._data:
                store._data["automations"] = []
            if "next_id" not in store._data:
                store._data["next_id"] = 1
            automation = store.create(data)
            await store.async_save()
            return web.json_response({
                "success": True,
                "automation": automation
            })
        except Exception as e:
            _LOGGER.error(f"Error creating automation: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500
            )

class AutomationDetailView(HomeAssistantView):
    """HTTP view to manage a single automation."""

    url = "/api/power_sync/automations/{automation_id}"
    name = "api:power_sync:automation_detail"
    requires_auth = True

    def __init__(self, hass: HomeAssistant):
        """Initialize the view."""
        self._hass = hass

    def _get_store(self):
        """Get the automation store from hass.data."""
        if DOMAIN not in self._hass.data:
            return None
        return self._hass.data[DOMAIN].get("automation_store")

    async def get(self, request: web.Request, automation_id: str) -> web.Response:
        """Handle GET request - get single automation."""
        store = self._get_store()
        if not store:
            return web.json_response(
                {"success": False, "error": "Automation store not initialized"},
                status=503
            )

        try:
            automation = store.get(int(automation_id))
            if not automation:
                return web.json_response(
                    {"success": False, "error": "Automation not found"},
                    status=404
                )
            return web.json_response({
                "success": True,
                "automation": automation
            })
        except Exception as e:
            _LOGGER.error(f"Error fetching automation: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500
            )

    async def put(self, request: web.Request, automation_id: str) -> web.Response:
        """Handle PUT request - update automation."""
        _LOGGER.info(f"📱 Automations HTTP PUT request for id={automation_id}")

        store = self._get_store()
        if not store:
            return web.json_response(
                {"success": False, "error": "Automation store not initialized"},
                status=503
            )

        try:
            data = await request.json()
            trigger = data.get('trigger', {})
            _LOGGER.debug(f"📱 Updating automation {automation_id} with data: name={data.get('name')}, trigger={trigger}, actions={data.get('actions')}, conditions={data.get('conditions')}")
            automation = store.update(int(automation_id), data)
            if not automation:
                return web.json_response(
                    {"success": False, "error": "Automation not found"},
                    status=404
                )
            await store.async_save()
            return web.json_response({
                "success": True,
                "automation": automation
            })
        except Exception as e:
            _LOGGER.error(f"Error updating automation: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500
            )

    async def delete(self, request: web.Request, automation_id: str) -> web.Response:
        """Handle DELETE request - delete automation."""
        store = self._get_store()
        if not store:
            return web.json_response(
                {"success": False, "error": "Automation store not initialized"},
                status=503
            )

        try:
            success = store.delete(int(automation_id))
            if not success:
                return web.json_response(
                    {"success": False, "error": "Automation not found"},
                    status=404
                )
            await store.async_save()
            return web.json_response({"success": True})
        except Exception as e:
            _LOGGER.error(f"Error deleting automation: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500
            )

class AutomationToggleView(HomeAssistantView):
    """HTTP view to toggle automation enabled state."""

    url = "/api/power_sync/automations/{automation_id}/toggle"
    name = "api:power_sync:automation_toggle"
    requires_auth = True

    def __init__(self, hass: HomeAssistant):
        """Initialize the view."""
        self._hass = hass

    def _get_store(self):
        """Get the automation store from hass.data."""
        if DOMAIN not in self._hass.data:
            return None
        return self._hass.data[DOMAIN].get("automation_store")

    async def post(self, request: web.Request, automation_id: str) -> web.Response:
        """Handle POST request - toggle automation."""
        store = self._get_store()
        if not store:
            return web.json_response(
                {"success": False, "error": "Automation store not initialized"},
                status=503
            )

        try:
            result = store.toggle(int(automation_id))
            if result is None:
                return web.json_response(
                    {"success": False, "error": "Automation not found"},
                    status=404
                )
            await store.async_save()
            return web.json_response({
                "success": True,
                "enabled": result
            })
        except Exception as e:
            _LOGGER.error(f"Error toggling automation: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500
            )

class AutomationPauseView(HomeAssistantView):
    """HTTP view to pause an automation."""

    url = "/api/power_sync/automations/{automation_id}/pause"
    name = "api:power_sync:automation_pause"
    requires_auth = True

    def __init__(self, hass: HomeAssistant):
        """Initialize the view."""
        self._hass = hass

    def _get_store(self):
        """Get the automation store from hass.data."""
        if DOMAIN not in self._hass.data:
            return None
        return self._hass.data[DOMAIN].get("automation_store")

    async def post(self, request: web.Request, automation_id: str) -> web.Response:
        """Handle POST request - pause automation."""
        store = self._get_store()
        if not store:
            return web.json_response(
                {"success": False, "error": "Automation store not initialized"},
                status=503
            )

        try:
            success = store.pause(int(automation_id))
            if not success:
                return web.json_response(
                    {"success": False, "error": "Automation not found"},
                    status=404
                )
            await store.async_save()
            return web.json_response({"success": True})
        except Exception as e:
            _LOGGER.error(f"Error pausing automation: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500
            )

class AutomationResumeView(HomeAssistantView):
    """HTTP view to resume a paused automation."""

    url = "/api/power_sync/automations/{automation_id}/resume"
    name = "api:power_sync:automation_resume"
    requires_auth = True

    def __init__(self, hass: HomeAssistant):
        """Initialize the view."""
        self._hass = hass

    def _get_store(self):
        """Get the automation store from hass.data."""
        if DOMAIN not in self._hass.data:
            return None
        return self._hass.data[DOMAIN].get("automation_store")

    async def post(self, request: web.Request, automation_id: str) -> web.Response:
        """Handle POST request - resume automation."""
        store = self._get_store()
        if not store:
            return web.json_response(
                {"success": False, "error": "Automation store not initialized"},
                status=503
            )

        try:
            success = store.resume(int(automation_id))
            if not success:
                return web.json_response(
                    {"success": False, "error": "Automation not found"},
                    status=404
                )
            await store.async_save()
            return web.json_response({"success": True})
        except Exception as e:
            _LOGGER.error(f"Error resuming automation: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500
            )

class AutomationGroupsView(HomeAssistantView):
    """HTTP view to get automation groups."""

    url = "/api/power_sync/automations/groups"
    name = "api:power_sync:automation_groups"
    requires_auth = True

    def __init__(self, hass: HomeAssistant):
        """Initialize the view."""
        self._hass = hass

    def _get_store(self):
        """Get the automation store from hass.data."""
        if DOMAIN not in self._hass.data:
            return None
        return self._hass.data[DOMAIN].get("automation_store")

    async def get(self, request: web.Request) -> web.Response:
        """Handle GET request - get all group names."""
        store = self._get_store()
        if not store:
            return web.json_response({
                "success": True,
                "groups": ["Default Group"]
            })

        try:
            automations = store.get_all()
            groups = set()
            for auto in automations:
                group = auto.get("group_name", "Default Group")
                if group:
                    groups.add(group)
            if not groups:
                groups.add("Default Group")
            return web.json_response({
                "success": True,
                "groups": sorted(list(groups))
            })
        except Exception as e:
            _LOGGER.error(f"Error fetching groups: {e}", exc_info=True)
            return web.json_response({
                "success": True,
                "groups": ["Default Group"]
            })

class PushTokenRegisterView(HomeAssistantView):
    """HTTP view to register push notification tokens."""

    url = "/api/power_sync/push/register"
    name = "api:power_sync:push_register"
    requires_auth = True

    def __init__(self, hass: HomeAssistant):
        """Initialize the view."""
        self._hass = hass

    async def post(self, request: web.Request) -> web.Response:
        """Handle POST request - register push token."""
        _LOGGER.info("📱 PUSH REGISTER: Request received from mobile app")

        try:
            data = await request.json()
            push_token = data.get("push_token")
            platform = data.get("platform", "unknown")
            device_name = data.get("device_name", "Unknown device")

            _LOGGER.info(f"📱 PUSH REGISTER: platform={platform}, device={device_name}")
            _LOGGER.debug(f"📱 PUSH REGISTER: token={'[%d chars]' % len(push_token) if push_token else 'None'}")

            if not push_token:
                _LOGGER.error("📱 PUSH REGISTER: No push_token in request body")
                return web.json_response(
                    {"success": False, "error": "push_token is required"},
                    status=400
                )

            # Store push token in hass.data for quick access
            if DOMAIN not in self._hass.data:
                self._hass.data[DOMAIN] = {}

            if "push_tokens" not in self._hass.data[DOMAIN]:
                self._hass.data[DOMAIN]["push_tokens"] = {}

            self._hass.data[DOMAIN]["push_tokens"][push_token] = {
                "token": push_token,
                "platform": platform,
                "device_name": device_name,
                "registered_at": dt_util.now().isoformat(),
            }

            # Also persist to AutomationStore for survival across restarts
            persisted = False
            for entry_id, entry_data in self._hass.data.get(DOMAIN, {}).items():
                if isinstance(entry_data, dict) and "automation_store" in entry_data:
                    store = entry_data["automation_store"]
                    store.register_push_token(push_token, platform, device_name)
                    await store.async_save()
                    persisted = True
                    _LOGGER.info(f"📱 PUSH REGISTER: Token persisted to storage for {device_name} ({platform})")
                    break

            if not persisted:
                _LOGGER.warning("📱 PUSH REGISTER: Could not persist token - no automation_store found")

            # Log token type for debugging
            token_type = "Expo" if push_token.startswith("ExponentPushToken") else "Unknown/FCM"
            _LOGGER.info(f"📱 PUSH REGISTER: ✅ Success - {device_name} ({platform}) - Token type: {token_type}")

            if not push_token.startswith("ExponentPushToken"):
                _LOGGER.warning(f"📱 PUSH REGISTER: ⚠️ Token does not start with 'ExponentPushToken' - notifications may not work!")

            # Log current registered tokens count
            token_count = len(self._hass.data[DOMAIN].get("push_tokens", {}))
            _LOGGER.info(f"📱 PUSH REGISTER: Total registered tokens now: {token_count}")

            return web.json_response({"success": True})

        except Exception as e:
            _LOGGER.error(f"📱 PUSH REGISTER: Error - {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500
            )

class PushTestView(HomeAssistantView):
    """HTTP view to send a test push notification."""

    url = "/api/power_sync/push/test"
    name = "api:power_sync:push_test"
    requires_auth = True

    def __init__(self, hass: HomeAssistant):
        """Initialize the view."""
        self._hass = hass

    async def post(self, request: web.Request) -> web.Response:
        """Handle POST request - send test notification."""
        from ..automations.actions import _send_expo_push

        _LOGGER.info("📱 PUSH TEST: Test notification requested")

        # List currently registered tokens
        push_tokens = self._hass.data.get(DOMAIN, {}).get("push_tokens", {})
        _LOGGER.info(f"📱 PUSH TEST: Currently registered tokens: {len(push_tokens)}")
        for token_key, token_data in push_tokens.items():
            _LOGGER.info(f"📱 PUSH TEST:   - {token_data.get('device_name')} ({token_data.get('platform')}) registered at {token_data.get('registered_at')}")

        if not push_tokens:
            return web.json_response({
                "success": False,
                "error": "No push tokens registered",
                "registered_tokens": 0
            })

        try:
            # Send test notification
            await _send_expo_push(
                self._hass,
                "PowerSync Test",
                f"Test notification sent at {dt_util.now().strftime('%H:%M:%S')}"
            )

            return web.json_response({
                "success": True,
                "message": "Test notification sent - check HA logs for Expo API response",
                "registered_tokens": len(push_tokens),
                "tokens": [
                    {
                        "device": t.get("device_name"),
                        "platform": t.get("platform"),
                        "registered_at": t.get("registered_at"),
                    }
                    for t in push_tokens.values()
                ]
            })

        except Exception as e:
            _LOGGER.error(f"📱 PUSH TEST: Error - {e}", exc_info=True)
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

class CurrentWeatherView(HomeAssistantView):
    """HTTP view to get current weather for mobile app dashboard."""

    url = "/api/power_sync/weather"
    name = "api:power_sync:weather"
    requires_auth = True

    def __init__(self, hass: HomeAssistant):
        """Initialize the view."""
        self._hass = hass

    async def get(self, request: web.Request) -> web.Response:
        """Handle GET request — fetch current weather.

        Uses the shared async_resolve_weather() helper so OWM and HA
        inbuilt weather + sun.sun day/night are treated interchangeably.
        Both the mobile app and the automation trigger layer share this
        resolver, so weather behaviour is consistent across surfaces.
        """
        from ..automations.weather import async_resolve_weather
        from ..const import CONF_OPENWEATHERMAP_API_KEY, CONF_WEATHER_LOCATION

        try:
            entries = self._hass.config_entries.async_entries(DOMAIN)
            if not entries:
                return web.json_response({
                    "success": False,
                    "error": "PowerSync not configured",
                }, status=400)

            entry = entries[0]

            api_key = entry.options.get(
                CONF_OPENWEATHERMAP_API_KEY,
                entry.data.get(CONF_OPENWEATHERMAP_API_KEY),
            )
            weather_location = entry.options.get(
                CONF_WEATHER_LOCATION,
                entry.data.get(CONF_WEATHER_LOCATION),
            )
            timezone = entry.options.get(
                "timezone",
                entry.data.get("timezone", "Australia/Brisbane"),
            )

            weather_data = await async_resolve_weather(
                self._hass, api_key, timezone, weather_location
            )
            if not weather_data:
                return web.json_response({
                    "success": False,
                    "error": "Failed to resolve weather (no OWM / HA entity / sun.sun)",
                }, status=500)

            return web.json_response({
                "success": True,
                "condition": weather_data.get("condition"),
                "description": weather_data.get("description", ""),
                "temperature_c": weather_data.get("temperature_c"),
                "humidity": weather_data.get("humidity"),
                "cloud_cover": weather_data.get("cloud_cover"),
                "is_night": weather_data.get("is_night", False),
                "source": weather_data.get("source"),
            })

        except Exception as e:
            _LOGGER.error(f"Error fetching weather: {e}", exc_info=True)
            return web.json_response({
                "success": False,
                "error": str(e),
            }, status=500)

class WeatherSolcastSettingsView(HomeAssistantView):
    """HTTP view to get/set weather and Solcast settings from mobile app."""

    url = "/api/power_sync/weather/settings"
    name = "api:power_sync:weather:settings"
    requires_auth = True

    def __init__(self, hass: HomeAssistant):
        self._hass = hass

    async def get(self, request: web.Request) -> web.Response:
        """Get current weather/solcast settings."""
        from ..const import CONF_WEATHER_LOCATION, CONF_OPENWEATHERMAP_API_KEY
        entry = None
        for config_entry in self._hass.config_entries.async_entries(DOMAIN):
            entry = config_entry
            break

        if not entry:
            return web.json_response({"success": False, "error": "Not configured"}, status=503)

        opts = {**entry.data, **entry.options}
        builtin_configured = _solcast_builtin_configured(opts)

        # Detect external Solcast HA integration (solcast_solar)
        external_solcast = _has_external_solcast_integration(self._hass)
        solcast_source = "none"
        if external_solcast:
            solcast_source = "integration"
        elif builtin_configured:
            solcast_source = "builtin"

        # Pick up any init error the coordinator cached at setup so the app
        # can display the real failure reason (e.g. "API key does not match
        # an active account") instead of silently reporting success while
        # Solcast isn't actually working.
        domain_data = self._hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
        solcast_init_error = domain_data.get("solcast_init_error") if isinstance(domain_data, dict) else None
        solcast_coord = domain_data.get("solcast_coordinator") if isinstance(domain_data, dict) else None
        solcast_active = bool(solcast_coord is not None or external_solcast)

        _LOGGER.info(
            "Weather/Solcast GET: source=%s, builtin_configured=%s, external_detected=%s, api_key=%s, active=%s, init_error=%s",
            solcast_source, builtin_configured, external_solcast,
            "set" if opts.get(CONF_SOLCAST_API_KEY) else "empty",
            solcast_active, bool(solcast_init_error),
        )
        return web.json_response({
            "success": True,
            "weather_location": opts.get(CONF_WEATHER_LOCATION, ""),
            "openweathermap_api_key": opts.get(CONF_OPENWEATHERMAP_API_KEY, ""),
            "solar_forecast_provider": _normalize_solar_forecast_provider(
                opts.get(CONF_SOLAR_FORECAST_PROVIDER)
            ),
            "solcast_enabled": builtin_configured or external_solcast,
            "solcast_source": solcast_source,
            "solcast_api_key": opts.get(CONF_SOLCAST_API_KEY, ""),
            "solcast_resource_id": opts.get(CONF_SOLCAST_RESOURCE_ID, ""),
            "solcast_estimate_type": opts.get(
                CONF_SOLCAST_ESTIMATE_TYPE, DEFAULT_SOLCAST_ESTIMATE_TYPE
            ),
            "solcast_active": solcast_active,
            "solcast_error": solcast_init_error,
        })

    async def post(self, request: web.Request) -> web.Response:
        """Update weather/solcast settings."""
        from ..const import CONF_WEATHER_LOCATION, CONF_OPENWEATHERMAP_API_KEY
        entry = None
        for config_entry in self._hass.config_entries.async_entries(DOMAIN):
            entry = config_entry
            break

        if not entry:
            return web.json_response({"success": False, "error": "Not configured"}, status=503)

        try:
            data = await request.json()
            new_options = dict(entry.options)
            new_data = dict(entry.data)
            old_effective = {**entry.data, **entry.options}

            if "weather_location" in data:
                new_options[CONF_WEATHER_LOCATION] = data["weather_location"]
            if "openweathermap_api_key" in data:
                new_options[CONF_OPENWEATHERMAP_API_KEY] = data["openweathermap_api_key"]
            if "solar_forecast_provider" in data:
                new_options[CONF_SOLAR_FORECAST_PROVIDER] = _normalize_solar_forecast_provider(
                    data["solar_forecast_provider"]
                )
                new_data.pop(CONF_SOLAR_FORECAST_PROVIDER, None)
            if "solcast_enabled" in data:
                new_options[CONF_SOLCAST_ENABLED] = bool(data["solcast_enabled"])
                new_data.pop(CONF_SOLCAST_ENABLED, None)
            if "solcast_api_key" in data:
                new_options[CONF_SOLCAST_API_KEY] = (data["solcast_api_key"] or "").strip()
                new_data.pop(CONF_SOLCAST_API_KEY, None)
            if "solcast_resource_id" in data:
                new_options[CONF_SOLCAST_RESOURCE_ID] = (data["solcast_resource_id"] or "").strip()
                new_data.pop(CONF_SOLCAST_RESOURCE_ID, None)
            if "solcast_estimate_type" in data:
                estimate_type = data["solcast_estimate_type"] or DEFAULT_SOLCAST_ESTIMATE_TYPE
                if estimate_type not in SOLCAST_ESTIMATE_TYPES:
                    estimate_type = DEFAULT_SOLCAST_ESTIMATE_TYPE
                new_options[CONF_SOLCAST_ESTIMATE_TYPE] = estimate_type
                new_data.pop(CONF_SOLCAST_ESTIMATE_TYPE, None)

            # Determine whether any solcast-related setting changed OR whether the
            # coordinator is missing despite valid config. Either case requires a
            # reload to (re)initialize SolcastForecastCoordinator, which is only
            # created inside async_setup_entry.
            new_effective = {**new_data, **new_options}
            solcast_changed = any(
                old_effective.get(k) != new_effective.get(k)
                or entry.data.get(k) != new_data.get(k)
                for k in _SOLCAST_SETTINGS_KEYS
            )

            entry_data = self._hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
            coordinator_missing = (
                _solcast_builtin_configured(new_effective)
                and entry_data.get("solcast_coordinator") is None
                and "solcast_solar" not in getattr(self._hass.config, "components", set())
            )

            update_kwargs: dict[str, Any] = {"options": new_options}
            if new_data != entry.data:
                update_kwargs["data"] = new_data
            self._hass.config_entries.async_update_entry(entry, **update_kwargs)
            _LOGGER.info(
                "Weather/Solcast settings updated from mobile app "
                "(solcast_changed=%s, coordinator_missing=%s, api_key=%s, resource_id=%s, enabled=%s)",
                solcast_changed,
                coordinator_missing,
                "set" if new_options.get(CONF_SOLCAST_API_KEY) else "empty",
                "set" if new_options.get(CONF_SOLCAST_RESOURCE_ID) else "empty",
                bool(new_options.get(CONF_SOLCAST_ENABLED)),
            )

            # Force reload if solcast config changed or coordinator wasn't created
            # at startup. async_update_entry only fires the update listener when
            # HA detects a diff, so re-submitting identical values wouldn't reload
            # even when the coordinator is missing.
            if solcast_changed or coordinator_missing:
                _LOGGER.info(
                    "Reloading PowerSync to (re)initialize Solcast coordinator "
                    "(changed=%s, missing=%s)",
                    solcast_changed, coordinator_missing,
                )
                self._hass.async_create_task(
                    self._hass.config_entries.async_reload(entry.entry_id)
                )

            return web.json_response({"success": True})
        except Exception as e:
            _LOGGER.error("Error updating weather/solcast settings: %s", e, exc_info=True)
            return web.json_response({"success": False, "error": "Settings update failed"}, status=500)

