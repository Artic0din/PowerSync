"""Lifecycle helpers extracted from ``async_setup_entry`` dual-write wiring.

These helpers host the Phase 0/1 EntryRuntime + BatteryPort + LoadpointArbiter
attachment so ``__init__.py`` stays thinner as Phase 7 progresses. Full
``async_setup`` / ``async_unload_entry`` / ``async_migrate_entry`` bodies remain
in ``__init__.py`` for now; ``setup.py`` documents the eventual move.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant


def build_entry_runtime(
    hass: HomeAssistant,
    domain: str,
    entry: ConfigEntry,
    bag: dict[str, Any],
) -> Any:
    """Wrap the legacy hass.data bag as a typed EntryRuntime (dual-write)."""
    from ..runtime import store_entry_runtime

    return store_entry_runtime(hass, domain, entry, bag)


def register_battery_port(
    hass: HomeAssistant,
    battery_system: str,
    runtime: Any,
) -> Any:
    """Resolve brand capabilities + BatteryPort and store on ``runtime``."""
    from ..batteries import get_battery_port
    from ..capabilities import get_brand_capabilities

    brand_caps = get_brand_capabilities(str(battery_system))
    battery_port = get_battery_port(hass, str(battery_system))
    runtime["brand_capabilities"] = brand_caps
    runtime["battery_port"] = battery_port
    return battery_port


def attach_runtime(
    hass: HomeAssistant,
    domain: str,
    entry: ConfigEntry,
    bag: dict[str, Any],
    *,
    battery_system: str,
) -> Any:
    """Dual-write EntryRuntime and attach BatteryPort + LoadpointArbiter.

    Replaces the inline Phase 0/1 block formerly in ``async_setup_entry``.
    """
    from ..ev import LoadpointArbiter

    runtime = build_entry_runtime(hass, domain, entry, bag)
    register_battery_port(hass, battery_system, runtime)
    loadpoint_arbiter = LoadpointArbiter()
    runtime["loadpoint_arbiter"] = loadpoint_arbiter
    return runtime
