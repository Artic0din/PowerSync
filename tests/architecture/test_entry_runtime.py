"""Contract tests for EntryRuntime dual-write helpers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from custom_components.power_sync.runtime import (
    EntryRuntime,
    get_entry_runtime,
    store_entry_runtime,
)
from custom_components.power_sync.runtime.entry_runtime import RUNTIME_KEY


def test_store_entry_runtime_dual_writes_bag_and_registry():
    hass = MagicMock()
    hass.data = {}
    entry = SimpleNamespace(entry_id="abc123")
    bag = {"amber_coordinator": object()}

    runtime = store_entry_runtime(hass, "power_sync", entry, bag)

    assert isinstance(runtime, EntryRuntime)
    assert bag[RUNTIME_KEY] is runtime
    assert hass.data["power_sync"]["abc123"] is bag
    assert hass.data["power_sync"]["_runtimes"]["abc123"] is runtime
    assert get_entry_runtime(hass, "power_sync", "abc123") is runtime


def test_entry_runtime_setitem_syncs_typed_aliases():
    hass = MagicMock()
    entry = SimpleNamespace(entry_id="e1")
    runtime = EntryRuntime(hass=hass, entry=entry, entry_id="e1")
    marker = object()
    runtime["optimization_coordinator"] = marker
    assert runtime.optimization_coordinator is marker
    assert runtime.get("optimization_coordinator") is marker
