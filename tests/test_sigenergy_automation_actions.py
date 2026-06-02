"""Regression tests for Sigenergy automation actions."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ACTIONS_PATH = ROOT / "custom_components" / "power_sync" / "automations" / "actions.py"


def _find_function(tree: ast.AST, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"Function {name} not found")


def test_sigenergy_force_discharge_action_routes_through_service_for_timer():
    source = ACTIONS_PATH.read_text()
    tree = ast.parse(source)
    function = _find_function(tree, "_action_force_discharge")
    function_source = ast.get_source_segment(source, function)

    assert function_source is not None
    assert "controller.force_discharge(power_kw)" not in function_source
    assert "SERVICE_FORCE_DISCHARGE" in function_source
    assert (
        "service_data: Dict[str, Any] = {\"duration\": duration, \"source\": \"automation\"}"
        in function_source
    )
    assert "service_data[\"power_w\"] = int(power_w)" in function_source
    assert "restore_export_limit" not in function_source


def test_sigenergy_force_charge_action_routes_through_service_for_timer():
    source = ACTIONS_PATH.read_text()
    tree = ast.parse(source)
    function = _find_function(tree, "_action_force_charge")
    function_source = ast.get_source_segment(source, function)

    assert function_source is not None
    assert "controller.force_charge(power_kw)" not in function_source
    assert "SERVICE_FORCE_CHARGE" in function_source
    assert (
        "service_data: Dict[str, Any] = {\"duration\": duration, \"source\": \"automation\"}"
        in function_source
    )
    assert "service_data[\"power_w\"] = int(power_w)" in function_source
