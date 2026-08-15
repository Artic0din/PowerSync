"""Regression test: the Octopus export-tariff fallback in async_setup_entry
must not reference constants that aren't imported.

``CONF_OCTOPUS_PRODUCT`` and ``OCTOPUS_EXPORT_PRODUCT_CODES`` are both
defined in const.py, but were missing from __init__.py's `from .const
import (...)` block. The fallback path that derives an export tariff code
from the configured product key (used whenever no export tariff was
discovered from the account or stored directly) referenced both names
without importing them, so it raised NameError instead of falling back --
every account relying on that fallback would fail to set up export tariff
pricing.

async_setup_entry() is a full Home Assistant config-entry setup function
that isn't practical to exercise directly in this stub-based suite, so this
checks import completeness statically instead: every name the fallback
block reads must actually be bound at module scope.
"""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
INIT_PATH = ROOT / "custom_components" / "power_sync" / "__init__.py"


def _module_level_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


def test_octopus_export_tariff_fallback_names_are_imported():
    source = INIT_PATH.read_text()
    tree = ast.parse(source)
    module_names = _module_level_names(tree)

    # These are both real constants defined in const.py -- this asserts
    # they're actually imported into __init__.py, not just spelled
    # correctly somewhere in the file.
    assert "CONF_OCTOPUS_PRODUCT" in module_names
    assert "OCTOPUS_EXPORT_PRODUCT_CODES" in module_names
