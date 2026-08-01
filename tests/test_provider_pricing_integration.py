"""Regression tests for provider pricing/account sensor integration."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parent.parent
COMPONENT_ROOT = ROOT / "custom_components" / "power_sync"


FLOW_POWER_EXCLUSIVE_SENSOR_KEYS = {
    "flow_power_price",
    "flow_power_export_price",
    "flow_power_twap",
    "flow_power_network_tariff",
    "flow_power_amber_comparison",
    "fp_account_pea",
    "fp_account_pea_30d",
    "fp_account_bpea",
    "fp_account_cpea",
    "fp_account_pea_import",
    "fp_account_lwap",
    "fp_account_lwap_actual",
    "fp_account_twap",
    "fp_account_avg_rrp",
    "fp_account_dlf",
    "fp_account_avg_usage",
    "fp_account_max_usage",
}


def _load_flow_power_cleanup(entity_registry, device_registry):
    """Load the registry cleanup without importing the full HA sensor platform."""
    source = (COMPONENT_ROOT / "sensor.py").read_text()
    tree = ast.parse(source)
    function = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_cleanup_inactive_flow_power_registry"
        ),
        None,
    )
    assert function is not None, "Flow Power inactive-provider cleanup is missing"

    namespace = {
        "HomeAssistant": object,
        "ConfigEntry": object,
        "DOMAIN": "power_sync",
        "SENSOR_FAMILY_FLOW_POWER": "flow_power",
        "_FLOW_POWER_EXCLUSIVE_SENSOR_KEYS": FLOW_POWER_EXCLUSIVE_SENSOR_KEYS,
        "_LOGGER": SimpleNamespace(
            debug=lambda *_args, **_kwargs: None,
            info=lambda *_args, **_kwargs: None,
        ),
        "er": SimpleNamespace(async_get=lambda _hass: entity_registry),
        "dr": SimpleNamespace(async_get=lambda _hass: device_registry),
    }
    module = ast.Module(body=[function], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, "sensor.py", "exec"), namespace)
    return namespace["_cleanup_inactive_flow_power_registry"]


class _FakeEntityRegistry:
    def __init__(self, entries):
        self.entities = {entry.entity_id: entry for entry in entries}
        self.removed: list[str] = []

    def async_remove(self, entity_id: str) -> None:
        self.removed.append(entity_id)
        self.entities.pop(entity_id, None)


class _FakeDeviceRegistry:
    def __init__(self, devices):
        self.devices = {device.id: device for device in devices}
        self.detached: list[tuple[str, str]] = []

    def async_update_device(self, device_id: str, *, remove_config_entry_id: str):
        self.detached.append((device_id, remove_config_entry_id))
        self.devices[device_id].config_entries.discard(remove_config_entry_id)


def test_provider_pricing_device_helper_links_to_entry_device():
    """Provider pricing devices should be linked under the PowerSync entry."""
    source = (COMPONENT_ROOT / "const.py").read_text()

    assert "def provider_pricing_device_info(entry_id: str, provider: str) -> dict:" in source
    assert '"name": name' in source
    assert '"model": "Electricity Pricing"' in source
    assert '"via_device": (DOMAIN, entry_id)' in source
    assert 'f"{entry_id}_{provider_key}_pricing"' in source
    assert 'name = "GloBird Pricing"' in source
    assert 'name = "Flow Power Pricing"' in source


def test_globird_setup_creates_coordinator_and_gated_sensors():
    """GloBird portal sensors should only be added when credentials are configured."""
    init_source = (COMPONENT_ROOT / "__init__.py").read_text()
    sensor_source = (COMPONENT_ROOT / "sensor.py").read_text()

    assert 'if electricity_provider == "globird":' in init_source
    assert "CONF_GLOBIRD_EMAIL" in init_source
    assert "CONF_GLOBIRD_PASSWORD" in init_source
    assert "GloBirdCoordinator(hass, entry)" in init_source
    assert "async_config_entry_first_refresh()" in init_source
    assert '"globird_coordinator": globird_coordinator' in init_source
    assert "await globird_coordinator.async_shutdown()" in init_source

    assert 'if electricity_provider == "globird" and globird_coordinator:' in sensor_source
    assert "build_globird_entities(globird_coordinator, entry)" in sensor_source


def test_globird_sensors_use_linked_device_and_stable_object_ids():
    """Ported GloBird sensors should use PowerSync-native IDs and device info."""
    source = (COMPONENT_ROOT / "globird_sensors.py").read_text()

    assert "def build_globird_entities(" in source
    assert "GloBirdLatestDayCostSensor" in source
    assert 'sensor_key = "latest_day_cost"' in source
    assert "provider_pricing_device_info(" in source
    assert "SENSOR_FAMILY_GLOBIRD" in source
    assert 'return "_".join(["power_sync", SENSOR_FAMILY_GLOBIRD, *safe_parts])' in source
    assert "usage_attributes(" in source
    assert "cost_attributes(" in source


def test_flow_power_api_account_sensors_use_provider_device_and_object_ids():
    """Flow Power API account sensors should retain their stable entity IDs."""
    source = (COMPONENT_ROOT / "sensor.py").read_text()
    const_source = (COMPONENT_ROOT / "const.py").read_text()

    assert "FLOW_POWER_ACCOUNT_SENSORS = [" in const_source
    assert '"fp_account_pea"' in const_source
    assert '"fp_account_lwap"' in const_source
    assert '"fp_account_avg_usage"' in const_source
    assert '"fp_account_max_usage"' in const_source
    assert "CONF_FLOWPOWER_API_KEY" in source
    assert "if fp_api_key:" in source
    assert "FlowPowerAccountSensor(" in source
    assert "return provider_pricing_device_info(self._entry.entry_id, SENSOR_FAMILY_FLOW_POWER)" in source
    assert 'self._attr_suggested_object_id = f"power_sync_{sensor_type}"' in source
    assert "network_tariff_raw" in source


def test_inactive_flow_power_registry_cleanup_is_exact_and_idempotent():
    """Switching to AGL removes only Flow-exclusive registry rows and device."""
    entry_id = "entry-1"
    flow_entries = [
        SimpleNamespace(
            entity_id=f"sensor.power_sync_{sensor_key}",
            platform="power_sync",
            config_entry_id=entry_id,
            unique_id=(
                f"{entry_id}_{sensor_key}"
                if index % 2 == 0
                else f"power_sync_{entry_id}_{sensor_key}"
            ),
        )
        for index, sensor_key in enumerate(sorted(FLOW_POWER_EXCLUSIVE_SENSOR_KEYS))
    ]
    shared_and_unrelated = [
        SimpleNamespace(
            entity_id="sensor.power_sync_current_import_price",
            platform="power_sync",
            config_entry_id=entry_id,
            unique_id=f"{entry_id}_current_import_price",
        ),
        SimpleNamespace(
            entity_id="sensor.other_flow_power_price",
            platform="other_integration",
            config_entry_id="other-entry",
            unique_id="other-entry_flow_power_price",
        ),
    ]
    entity_registry = _FakeEntityRegistry(flow_entries + shared_and_unrelated)
    flow_device = SimpleNamespace(
        id="flow-device",
        identifiers={("power_sync", f"{entry_id}_flow_power_pricing")},
        config_entries={entry_id},
    )
    parent_device = SimpleNamespace(
        id="parent-device",
        identifiers={("power_sync", entry_id)},
        config_entries={entry_id},
    )
    device_registry = _FakeDeviceRegistry([flow_device, parent_device])
    cleanup = _load_flow_power_cleanup(entity_registry, device_registry)
    entry = SimpleNamespace(entry_id=entry_id)

    assert cleanup(object(), entry, "agl") == len(FLOW_POWER_EXCLUSIVE_SENSOR_KEYS)
    assert set(entity_registry.removed) == {
        f"sensor.power_sync_{sensor_key}"
        for sensor_key in FLOW_POWER_EXCLUSIVE_SENSOR_KEYS
    }
    assert "sensor.power_sync_current_import_price" in entity_registry.entities
    assert "sensor.other_flow_power_price" in entity_registry.entities
    assert device_registry.detached == [("flow-device", entry_id)]

    assert cleanup(object(), entry, "agl") == 0
    assert device_registry.detached == [("flow-device", entry_id)]


def test_active_flow_power_registry_cleanup_is_a_noop():
    """An active Flow configuration must retain its provider entities/device."""
    entry_id = "entry-1"
    entity_registry = _FakeEntityRegistry(
        [
            SimpleNamespace(
                entity_id="sensor.power_sync_flow_power_price",
                platform="power_sync",
                config_entry_id=entry_id,
                unique_id=f"{entry_id}_flow_power_price",
            )
        ]
    )
    device_registry = _FakeDeviceRegistry(
        [
            SimpleNamespace(
                id="flow-device",
                identifiers={("power_sync", f"{entry_id}_flow_power_pricing")},
                config_entries={entry_id},
            )
        ]
    )
    cleanup = _load_flow_power_cleanup(entity_registry, device_registry)

    assert cleanup(object(), SimpleNamespace(entry_id=entry_id), "flow_power") == 0
    assert entity_registry.removed == []
    assert device_registry.detached == []
