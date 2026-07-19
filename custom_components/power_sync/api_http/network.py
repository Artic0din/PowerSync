"""HTTP views for PowerSync."""
from __future__ import annotations

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from ..const import DOMAIN

class NetworkEnvelopeView(HomeAssistantView):
    """Read-only network export envelope endpoint."""

    url = "/api/power_sync/network_envelope"
    name = "api:power_sync:network_envelope"
    requires_auth = True

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._hass = hass
        self._entry = entry

    async def get(self, request: web.Request) -> web.Response:
        """Return the entry's current atomic network envelope snapshot."""
        entry_data = self._hass.data.get(DOMAIN, {}).get(self._entry.entry_id, {})
        manager = entry_data.get("network_envelope_manager")
        if manager is None:
            return web.json_response(
                {
                    "success": False,
                    "error": "Network export envelope is not initialized",
                },
                status=503,
            )
        return web.json_response(
            {
                "success": True,
                "network_envelope": manager.snapshot.to_dict(),
            }
        )

