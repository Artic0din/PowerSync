"""Regression coverage for provider-config Auto Sync responses."""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
import textwrap


INIT_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "power_sync"
    / "__init__.py"
)


def _handler_globals() -> dict:
    """Load real handler globals in an isolated package for relative imports."""
    package_name = "_ps_provider_config_helpers"
    package = ModuleType(package_name)
    package.__path__ = [str(INIT_PATH.parent)]
    previous = {
        name: value for name, value in sys.modules.items()
        if name == package_name or name.startswith(package_name + ".")
    }
    sys.modules[package_name] = package
    globals_: dict = {}
    try:
        for module_name in ("const", "currency", "zerohero"):
            spec = importlib.util.spec_from_file_location(
                f"{package_name}.{module_name}",
                INIT_PATH.parent / f"{module_name}.py",
            )
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            globals_.update({
                name: value for name, value in vars(module).items()
                if not name.startswith("__")
            })
    finally:
        for name in list(sys.modules):
            if name == package_name or name.startswith(package_name + "."):
                sys.modules.pop(name, None)
        sys.modules.update(previous)
    return globals_


def _provider_config_get():
    source = INIT_PATH.read_text()
    module = ast.parse(source)
    for node in module.body:
        if isinstance(node, ast.ClassDef) and node.name == "ProviderConfigView":
            for child in node.body:
                if isinstance(child, ast.AsyncFunctionDef) and child.name == "get":
                    method_source = ast.get_source_segment(source, child)
                    assert method_source is not None
                    break
            else:
                raise AssertionError("ProviderConfigView.get not found")
            break
    else:
        raise AssertionError("ProviderConfigView not found")

    class _Logger:
        def info(self, *_args, **_kwargs):
            pass

        warning = info
        error = info

    def _json_response(payload, status=200):
        return SimpleNamespace(payload=payload, status=status)

    web = SimpleNamespace(Request=object, Response=object, json_response=_json_response)
    namespace = {
        "web": web,
        "_LOGGER": _Logger(),
        **_handler_globals(),
    }
    exec(textwrap.dedent(method_source), namespace)
    return namespace["get"]


def test_octopus_provider_config_returns_persisted_auto_sync_false():
    """Every provider exposing Auto Sync must return its persisted false value."""

    entry = SimpleNamespace(
        entry_id="entry-1",
        data={"battery_system": "tesla"},
        options={
            "electricity_provider": "octopus",
            "auto_sync_enabled": False,
        },
    )
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_entries=lambda _domain: [entry]),
        data={"power_sync": {"entry-1": {}}},
    )

    response = asyncio.run(_provider_config_get()(SimpleNamespace(_hass=hass), None))

    assert response.status == 200
    assert response.payload["success"] is True
    assert response.payload["electricity_provider"] == "octopus"
    assert response.payload["config"]["auto_sync"] is False


def test_provider_config_demand_charge_updates_use_the_reload_lifecycle():
    """Demand Charge changes must rebuild its coordinator and protection timers."""

    source = INIT_PATH.read_text()
    module = ast.parse(source)
    post_source = None
    for node in module.body:
        if isinstance(node, ast.ClassDef) and node.name == "ProviderConfigView":
            post = next(
                child
                for child in node.body
                if isinstance(child, ast.AsyncFunctionDef) and child.name == "post"
            )
            post_source = ast.get_source_segment(source, post)
            break

    assert post_source is not None
    assert '"demand_charge_enabled"' in post_source
    assert '"demand_charge_billing_day"' in post_source
    assert "demand_charge_changed = bool(" in post_source
    assert "and not demand_charge_changed" in post_source
