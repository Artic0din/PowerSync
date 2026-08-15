"""Regression tests for Sigenergy EV charger API view wiring."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
INIT_PATH = ROOT / "custom_components" / "power_sync" / "__init__.py"


def _class_method(tree: ast.AST, class_name: str, method_name: str) -> ast.AsyncFunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for child in node.body:
                if isinstance(child, ast.AsyncFunctionDef) and child.name == method_name:
                    return child
    raise AssertionError(f"{class_name}.{method_name} not found")


def _sigenergy_hass_args(method: ast.AsyncFunctionDef, func_name: str) -> list[ast.AST]:
    """Positional hass-like arguments passed to a module-level sigenergy helper.

    Both ``_configured_sigenergy_charger_capabilities(entry, hass)`` and
    ``_read_sigenergy_charger_state_for_entry(entry, hass)`` take the hass
    reference as their second positional argument.
    """
    args: list[ast.AST] = []
    for node in ast.walk(method):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == func_name
        ):
            assert len(node.args) >= 2
            args.append(node.args[1])
    return args


def _sigenergy_capability_hass_args(method: ast.AsyncFunctionDef) -> list[ast.AST]:
    return _sigenergy_hass_args(method, "_configured_sigenergy_charger_capabilities")


def test_sigenergy_ev_api_views_use_stored_hass_reference_for_capabilities():
    """Regression coverage for EVWidgetDataView/EVLoadpointStatusView.

    EVVehiclesView is checked separately in
    test_sigenergy_ev_api_views_use_stored_hass_reference_for_vehicle_discovery
    below since it calls both sigenergy helpers, not just this one.
    """
    source = INIT_PATH.read_text()
    tree = ast.parse(source)

    for class_name in ("EVWidgetDataView", "EVLoadpointStatusView"):
        method = _class_method(tree, class_name, "get")
        hass_args = _sigenergy_capability_hass_args(method)

        assert hass_args
        assert all(
            isinstance(arg, ast.Attribute)
            and isinstance(arg.value, ast.Name)
            and arg.value.id == "self"
            and arg.attr == "_hass"
            for arg in hass_args
        )


def test_ev_vehicles_view_uses_stored_hass_reference_for_sigenergy_charger_lookup():
    """EVVehiclesView.get() must use self._hass, not the module-level `hass`
    from _get_available_ev_vehicles() -- EVVehiclesView is a top-level class,
    not nested inside that function, so a bare `hass` reference here is a
    NameError at request time once a Sigenergy charger is configured.
    """
    source = INIT_PATH.read_text()
    tree = ast.parse(source)

    method = _class_method(tree, "EVVehiclesView", "get")
    hass_args = _sigenergy_hass_args(
        method, "_read_sigenergy_charger_state_for_entry"
    ) + _sigenergy_hass_args(method, "_configured_sigenergy_charger_capabilities")

    assert hass_args
    assert all(
        isinstance(arg, ast.Attribute)
        and isinstance(arg.value, ast.Name)
        and arg.value.id == "self"
        and arg.attr == "_hass"
        for arg in hass_args
    )
