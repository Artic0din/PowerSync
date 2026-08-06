"""Regression coverage for trustworthy FoxESS grid-power telemetry."""

from __future__ import annotations

import ast
import math
from pathlib import Path
from typing import Any


COORDINATOR_PATH = (
    Path(__file__).resolve().parent.parent
    / "custom_components"
    / "power_sync"
    / "coordinator.py"
)


def _load_grid_helpers() -> dict[str, Any]:
    """Extract the pure telemetry helpers without importing Home Assistant."""
    names = {
        "_finite_float",
        "_foxess_direct_grid_power",
        "_foxess_entity_grid_power_valid",
        "_foxess_cloud_grid_power",
    }
    tree = ast.parse(COORDINATOR_PATH.read_text())
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    assert {node.name for node in functions} == names
    module = ast.Module(body=functions, type_ignores=[])
    ast.fix_missing_locations(module)
    namespace: dict[str, Any] = {"Any": Any, "math": math}
    exec(compile(module, str(COORDINATOR_PATH), "exec"), namespace)
    return namespace


def _class_update_source(class_name: str) -> str:
    source = COORDINATOR_PATH.read_text()
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != class_name:
            continue
        for member in node.body:
            if (
                isinstance(member, ast.AsyncFunctionDef)
                and member.name == "_async_update_data"
            ):
                segment = ast.get_source_segment(source, member)
                assert segment is not None
                return segment
    raise AssertionError(f"{class_name}._async_update_data not found")


HELPERS = _load_grid_helpers()


def test_direct_grid_power_preserves_missing_and_nonfinite_invalidity():
    normalize = HELPERS["_foxess_direct_grid_power"]

    assert normalize(-0.02) == (-0.02, True)
    assert normalize("0.4") == (0.4, True)
    assert normalize(None) == (0.0, False)
    assert normalize("bad") == (0.0, False)
    assert normalize(float("nan")) == (0.0, False)
    assert normalize(float("inf")) == (0.0, False)
    assert normalize(False) == (0.0, False)


def test_entity_grid_power_requires_ready_current_reading():
    normalize = HELPERS["_foxess_entity_grid_power_valid"]

    assert normalize(True, -0.02) == (-0.02, True)
    assert normalize(False, -0.02) == (-0.02, False)
    assert normalize(True, None) == (0.0, False)
    assert normalize(True, "bad") == (0.0, False)
    assert normalize(True, float("nan")) == (0.0, False)


def test_cloud_grid_power_requires_meter_or_complete_import_export_pair():
    normalize = HELPERS["_foxess_cloud_grid_power"]

    assert normalize(-20, None, None) == (-0.02, True)
    assert normalize(None, 400, 100) == (0.3, True)
    assert normalize("bad", 400, 100) == (0.3, True)
    assert normalize(None, None, None) == (0.0, False)
    assert normalize(None, 400, None) == (0.0, False)
    assert normalize(None, float("inf"), 100) == (0.0, False)


def test_all_foxess_coordinators_publish_explicit_grid_validity():
    expected_helpers = {
        "FoxESSEnergyCoordinator": "_foxess_direct_grid_power",
        "FoxESSEntityEnergyCoordinator": "_foxess_entity_grid_power_valid",
        "FoxESSCloudEnergyCoordinator": "_foxess_cloud_grid_power",
    }

    for class_name, helper_name in expected_helpers.items():
        source = _class_update_source(class_name)
        assert helper_name in source
        assert '"grid_power_valid": grid_power_valid' in source


def test_entity_stale_snapshot_clears_grid_validity():
    source = COORDINATOR_PATH.read_text()
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != "NativeBatteryIntegrationReadinessMixin":
            continue
        for member in node.body:
            if isinstance(member, ast.FunctionDef) and member.name == "_native_stale_data":
                segment = ast.get_source_segment(source, member)
                assert segment is not None
                assert 'stale["telemetry_ready"] = False' in segment
                assert 'stale["grid_power_valid"] = False' in segment
                return
    raise AssertionError("NativeBatteryIntegrationReadinessMixin._native_stale_data not found")


def test_cloud_missing_soc_snapshot_is_control_invalid():
    source = _class_update_source("FoxESSCloudEnergyCoordinator")
    missing_soc_block = source[
        source.index("if soc is None:") : source.index(
            "buy, sell = _get_current_prices",
        )
    ]

    assert "stale_data = dict(self.data)" in missing_soc_block
    assert 'stale_data["telemetry_ready"] = False' in missing_soc_block
    assert 'stale_data["grid_power_valid"] = False' in missing_soc_block
    assert "return stale_data" in missing_soc_block
