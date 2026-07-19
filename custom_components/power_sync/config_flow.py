"""Config flow for PowerSync integration.

Thin compatibility facade. Home Assistant discovers this module via
``manifest.json`` ``config_flow: true``. Implementation lives in
``config_flow_pkg/``.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry

from .config_flow_pkg import PowerSyncConfigFlow, PowerSyncOptionsFlow

# Re-export for HA entry points and any external importers.
__all__ = [
    "PowerSyncConfigFlow",
    "PowerSyncOptionsFlow",
    "async_get_options_flow",
]


async def async_get_options_flow(config_entry: ConfigEntry) -> PowerSyncOptionsFlow:
    """Return the options flow handler (module-level HA entry point)."""
    return PowerSyncConfigFlow.async_get_options_flow(config_entry)
