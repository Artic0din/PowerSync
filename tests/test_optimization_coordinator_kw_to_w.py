"""Regression coverage for OptimizationCoordinator._kw_to_w.

``optimization/coordinator.py`` used to define ``_kw_to_w`` twice as a
``@staticmethod`` on ``OptimizationCoordinator`` (once around line 2311,
once around line 7515). Since both definitions lived directly in the same
class body, the second silently shadowed the first in the class namespace --
every ``self._kw_to_w(...)`` call anywhere in the class (including call
sites textually *above* the first definition) always resolved to the
second, unrounded, negative-value-permissive implementation. The first
definition was fully dead code, but its differing docstring/type hint
(clamping negative values to ``None``, rounding to ``int``) misdescribed
the behavior callers actually got.

This test extracts the real ``OptimizationCoordinator`` class body via
``ast`` (matching this repo's established pattern for exercising methods on
its few very large classes without needing a full Home Assistant object
graph -- see tests/test_calendar_history_view_timeout.py and
tests/test_force_mode_controls.py) and asserts:

  1. Exactly one ``_kw_to_w`` definition exists directly in the class body
     (guards against the shadowing bug being reintroduced).
  2. The surviving implementation's actual behavior: passthrough float
     conversion, ``None`` on invalid input, and no clamping of negative
     values (needed for signed power readings like grid/battery power).
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest


COORDINATOR_PATH = (
    Path(__file__).resolve().parent.parent
    / "custom_components"
    / "power_sync"
    / "optimization"
    / "coordinator.py"
)


def _optimization_coordinator_class_node() -> ast.ClassDef:
    tree = ast.parse(COORDINATOR_PATH.read_text())
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "OptimizationCoordinator"
    )


def test_kw_to_w_is_defined_exactly_once_on_optimization_coordinator():
    """Guards against reintroducing a silently-shadowing duplicate method."""
    class_node = _optimization_coordinator_class_node()
    kw_to_w_defs = [
        node
        for node in class_node.body
        if isinstance(node, ast.FunctionDef) and node.name == "_kw_to_w"
    ]
    assert len(kw_to_w_defs) == 1, (
        "OptimizationCoordinator must define _kw_to_w exactly once -- a second "
        "definition would silently shadow the first in the class namespace "
        "with no error or warning."
    )


def _load_kw_to_w():
    class_node = _optimization_coordinator_class_node()
    func_node = next(
        node
        for node in class_node.body
        if isinstance(node, ast.FunctionDef) and node.name == "_kw_to_w"
    )
    module = ast.fix_missing_locations(ast.Module(body=[func_node], type_ignores=[]))
    namespace: dict[str, Any] = {"Any": Any}
    exec(compile(module, str(COORDINATOR_PATH), "exec"), namespace)
    return namespace["_kw_to_w"]


@pytest.mark.parametrize(
    "value,expected",
    [
        (1, 1000.0),
        (1.5, 1500.0),
        (0, 0.0),
        ("2.5", 2500.0),
        # Negative values pass through unclamped -- callers rely on this for
        # signed readings (e.g. battery/grid power where negative means
        # export/discharge).
        (-1.2, -1200.0),
        (None, None),
        ("not-a-number", None),
    ],
)
def test_kw_to_w_behavior(value, expected):
    kw_to_w = _load_kw_to_w()
    assert kw_to_w(value) == expected
