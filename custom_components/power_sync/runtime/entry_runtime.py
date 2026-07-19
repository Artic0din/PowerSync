"""EntryRuntime — typed replacement for the hass.data[DOMAIN][entry_id] bag.

Phase 0 dual-writes: the legacy dict remains the source of truth for most
callers, while EntryRuntime mirrors key fields and provides typed accessors.
Later phases migrate consumers onto EntryRuntime and drop the string-key bag.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

# Sentinel key stored alongside the legacy bag.
RUNTIME_KEY = "_entry_runtime"


@dataclass
class EntryRuntime:
    """Typed runtime state for one PowerSync config entry."""

    hass: HomeAssistant
    entry: ConfigEntry
    entry_id: str

    # Legacy mutable bag (dual-write target). Prefer typed fields below when set.
    data: dict[str, Any] = field(default_factory=dict)

    # Subsystem handles (populated as phases land).
    battery_port: Any | None = None
    price_provider: Any | None = None
    energy_telemetry: Any | None = None
    optimization_coordinator: Any | None = None
    loadpoint_arbiter: Any | None = None
    brand_capabilities: Any | None = None

    def get(self, key: str, default: Any = None) -> Any:
        """Dict-compatible getter over the legacy bag."""
        return self.data.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.data[key] = value
        # Keep typed aliases in sync when known keys change.
        if key == "optimization_coordinator":
            self.optimization_coordinator = value
        elif key == "battery_port":
            self.battery_port = value
        elif key == "price_provider":
            self.price_provider = value
        elif key == "energy_telemetry":
            self.energy_telemetry = value
        elif key == "loadpoint_arbiter":
            self.loadpoint_arbiter = value
        elif key == "brand_capabilities":
            self.brand_capabilities = value

    def __contains__(self, key: object) -> bool:
        return key in self.data

    def setdefault(self, key: str, default: Any = None) -> Any:
        return self.data.setdefault(key, default)

    def update(self, mapping: dict[str, Any]) -> None:
        for key, value in mapping.items():
            self[key] = value

    @classmethod
    def from_legacy_bag(
        cls,
        hass: HomeAssistant,
        entry: ConfigEntry,
        bag: dict[str, Any],
    ) -> EntryRuntime:
        """Wrap an existing hass.data bag without copying payload identity."""
        runtime = cls(hass=hass, entry=entry, entry_id=entry.entry_id, data=bag)
        runtime.optimization_coordinator = bag.get("optimization_coordinator")
        runtime.battery_port = bag.get("battery_port")
        runtime.price_provider = bag.get("price_provider")
        runtime.energy_telemetry = bag.get("energy_telemetry")
        runtime.loadpoint_arbiter = bag.get("loadpoint_arbiter")
        runtime.brand_capabilities = bag.get("brand_capabilities")
        return runtime


def store_entry_runtime(
    hass: HomeAssistant,
    domain: str,
    entry: ConfigEntry,
    bag: dict[str, Any],
) -> EntryRuntime:
    """Dual-write EntryRuntime into the legacy bag and domain registry."""
    runtime = EntryRuntime.from_legacy_bag(hass, entry, bag)
    bag[RUNTIME_KEY] = runtime
    domain_data = hass.data.setdefault(domain, {})
    domain_data[entry.entry_id] = bag
    domain_data.setdefault("_runtimes", {})[entry.entry_id] = runtime
    return runtime


def get_entry_runtime(
    hass: HomeAssistant,
    domain: str,
    entry_id: str,
) -> EntryRuntime | None:
    """Return EntryRuntime for an entry_id if present."""
    domain_data = hass.data.get(domain) or {}
    runtimes = domain_data.get("_runtimes") or {}
    runtime = runtimes.get(entry_id)
    if runtime is not None:
        return runtime
    bag = domain_data.get(entry_id)
    if isinstance(bag, dict):
        stored = bag.get(RUNTIME_KEY)
        if isinstance(stored, EntryRuntime):
            return stored
    return None
