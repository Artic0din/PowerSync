"""Regression tests for Tesla Fleet-to-BLE bridge identity mapping."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent / "custom_components" / "power_sync"
package = sys.modules.setdefault("power_sync", types.ModuleType("power_sync"))
package.__path__ = [str(ROOT)]

from power_sync.tesla_ble_mapping import (  # noqa: E402
    TeslaBleMappingError,
    ble_prefix_vehicle_pairs,
    parse_tesla_ble_vehicle_mapping,
    vehicle_ble_prefix,
)


VIN_A = "5YJ3E1EA7NF0000A1"
VIN_B = "5YJ3E1EA7NF0000B2"


def _both_config(prefixes: str, mapping: str = "") -> dict[str, str]:
    return {
        "ev_provider": "both",
        "tesla_ble_entity_prefix": prefixes,
        "tesla_ble_vehicle_mapping": mapping,
    }


def test_mapping_accepts_comma_and_newline_separators():
    assert parse_tesla_ble_vehicle_mapping(
        f"{VIN_A.lower()}=bridge_alpha,\n{VIN_B}=bridge_beta"
    ) == {
        VIN_A: "bridge_alpha",
        VIN_B: "bridge_beta",
    }


@pytest.mark.parametrize(
    "mapping",
    (
        "not-a-vin=bridge_alpha",
        f"{VIN_A}=Bridge-Alpha",
        f"{VIN_A}=bridge_alpha,{VIN_A}=bridge_beta",
        f"{VIN_A}=bridge_alpha,{VIN_B}=bridge_alpha",
        f"{VIN_A}:bridge_alpha",
    ),
)
def test_mapping_rejects_malformed_or_ambiguous_entries(mapping: str):
    with pytest.raises(TeslaBleMappingError):
        parse_tesla_ble_vehicle_mapping(mapping)


def test_explicit_mapping_does_not_depend_on_prefix_or_vin_order():
    config = _both_config(
        "bridge_alpha,bridge_beta",
        f"{VIN_A}=bridge_beta,{VIN_B}=bridge_alpha",
    )

    assert vehicle_ble_prefix(config, VIN_A, [VIN_B, VIN_A]) == "bridge_beta"
    assert vehicle_ble_prefix(config, VIN_B, [VIN_A, VIN_B]) == "bridge_alpha"
    assert ble_prefix_vehicle_pairs(config, [VIN_B, VIN_A]) == {
        "bridge_alpha": VIN_B,
        "bridge_beta": VIN_A,
    }


def test_unmapped_multi_vehicle_configuration_fails_closed():
    config = _both_config("bridge_alpha,bridge_beta")

    assert vehicle_ble_prefix(config, VIN_A, [VIN_A, VIN_B]) is None
    assert vehicle_ble_prefix(config, VIN_B, [VIN_A, VIN_B]) is None


def test_single_vehicle_single_bridge_is_inferred_safely():
    config = _both_config("bridge_alpha")

    assert vehicle_ble_prefix(config, VIN_A, [VIN_A]) == "bridge_alpha"


def test_mapping_to_an_unconfigured_bridge_fails_closed():
    config = _both_config("bridge_alpha", f"{VIN_A}=bridge_beta")

    assert vehicle_ble_prefix(config, VIN_A, [VIN_A]) is None
