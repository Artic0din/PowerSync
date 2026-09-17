"""Regression coverage for EPEX settings changed through the options flow."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parent.parent
INIT_PATH = ROOT / "custom_components" / "power_sync" / "__init__.py"

CONF_EPEX_REGION = "epex_region"
CONF_EPEX_SURCHARGE = "epex_surcharge"
CONF_EPEX_TAX_PERCENT = "epex_tax_percent"
CONF_EPEX_EXPORT_RATE = "epex_export_rate"
CONF_EPEX_EXPORT_SOURCE = "epex_export_source"


def _epex_setup_assignments() -> list[ast.Assign]:
    """Find the EPEX effective-settings assignments in real setup source."""
    module = ast.parse(INIT_PATH.read_text())
    setup = next(
        node
        for node in module.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "async_setup_entry"
    )
    names = {
        "has_epex",
        "epex_region",
        "epex_surcharge",
        "epex_tax_percent",
        "epex_export_rate",
        "epex_export_source",
    }
    found: dict[str, ast.Assign] = {}
    for node in ast.walk(setup):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id in names:
            found[target.id] = node
    assert found.keys() == names
    return [found[name] for name in names]


def _evaluate_epex_setup(entry: SimpleNamespace) -> dict[str, object]:
    """Execute the real EPEX setup assignments with an options/data fake."""
    assignments = _epex_setup_assignments()
    module = ast.Module(body=assignments, type_ignores=[])
    ast.fix_missing_locations(module)

    def entry_value(key: str, default: object = None) -> object:
        return entry.options.get(key, entry.data.get(key, default))

    namespace: dict[str, object] = {
        "electricity_provider": "epex",
        "_entry_value": entry_value,
        "CONF_EPEX_REGION": CONF_EPEX_REGION,
        "CONF_EPEX_SURCHARGE": CONF_EPEX_SURCHARGE,
        "CONF_EPEX_TAX_PERCENT": CONF_EPEX_TAX_PERCENT,
        "CONF_EPEX_EXPORT_RATE": CONF_EPEX_EXPORT_RATE,
        "CONF_EPEX_EXPORT_SOURCE": CONF_EPEX_EXPORT_SOURCE,
    }
    exec(compile(module, str(INIT_PATH), "exec"), namespace)
    return namespace


def test_epex_setup_prefers_options_over_legacy_entry_data():
    """An options-flow edit must drive the coordinator after its reload."""
    values = _evaluate_epex_setup(
        SimpleNamespace(
            data={
                CONF_EPEX_REGION: "DE",
                CONF_EPEX_SURCHARGE: 0.0,
                CONF_EPEX_TAX_PERCENT: 0.0,
                CONF_EPEX_EXPORT_RATE: 0.0,
            },
            options={
                CONF_EPEX_REGION: "SE4",
                CONF_EPEX_SURCHARGE: 5.66,
                CONF_EPEX_TAX_PERCENT: 25.0,
                CONF_EPEX_EXPORT_RATE: 8.5,
                CONF_EPEX_EXPORT_SOURCE: "raw_wholesale",
            },
        )
    )

    assert values["has_epex"] is True
    assert values["epex_region"] == "SE4"
    assert values["epex_surcharge"] == 5.66
    assert values["epex_tax_percent"] == 25.0
    assert values["epex_export_rate"] == 8.5
    assert values["epex_export_source"] == "raw_wholesale"


def test_epex_setup_accepts_epex_region_saved_only_in_options():
    """An EPEX region saved through options must enable its coordinator."""
    values = _evaluate_epex_setup(
        SimpleNamespace(
            data={"electricity_provider": "amber"},
            options={CONF_EPEX_REGION: "SE3"},
        )
    )

    assert values["has_epex"] is True
    assert values["epex_region"] == "SE3"
