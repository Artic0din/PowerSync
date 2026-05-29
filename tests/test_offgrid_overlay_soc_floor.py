"""Regression test for the off-grid overlay logging bug.

`OptimizationCoordinator._apply_offgrid_overlay` logged a summary line
referencing a name `soc_floor` that was never defined in scope. Whenever the
overlay actually marked slots (`offgrid_count > 0`) — i.e. the real off-grid
dispatch path — the `_LOGGER.info(...)` call raised `NameError`, crashing the
LP post-processing step. The fix logs the SOC eligibility threshold that the
overlay actually uses (`self._OFFGRID_FULL_SOC_THRESHOLD`).

This is an AST/source-level guard: exercising the method behaviourally would
require importing the Home Assistant-heavy coordinator module (and its eight
sibling modules), which this repo's test harness deliberately avoids. The
assertions below pin the exact regression — the undefined name must not return,
and the real threshold must be the logged value.
"""

from __future__ import annotations

import ast
from pathlib import Path

COORDINATOR_PATH = (
    Path(__file__).resolve().parent.parent
    / "custom_components"
    / "power_sync"
    / "optimization"
    / "coordinator.py"
)


def _find_method(tree: ast.AST, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"method '{name}' not found in {COORDINATOR_PATH}")


def test_offgrid_overlay_does_not_reference_undefined_soc_floor():
    source = COORDINATOR_PATH.read_text()
    method = _find_method(ast.parse(source), "_apply_offgrid_overlay")

    referenced_names = {
        node.id
        for node in ast.walk(method)
        if isinstance(node, ast.Name)
    }
    assert "soc_floor" not in referenced_names, (
        "_apply_offgrid_overlay references undefined name 'soc_floor' — this "
        "raises NameError whenever the overlay marks slots (offgrid_count > 0)"
    )


def test_offgrid_overlay_logs_the_soc_eligibility_threshold():
    source = COORDINATOR_PATH.read_text()
    method = _find_method(ast.parse(source), "_apply_offgrid_overlay")
    method_source = ast.get_source_segment(source, method)

    assert method_source is not None
    # The summary log must report the threshold the overlay actually gates on.
    assert "self._OFFGRID_FULL_SOC_THRESHOLD" in method_source
    assert "SOC floor=" in method_source


def test_offgrid_full_soc_threshold_is_defined_as_percent():
    # Guards the contract the log line relies on: the threshold is a percentage
    # value (formatted with %d%%), not a 0-1 fraction.
    source = COORDINATOR_PATH.read_text()
    tree = ast.parse(source)
    found = False
    for node in ast.walk(tree):
        # Handle both plain assignment and annotated assignment (e.g. x: float = 98.0)
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if (
            any(
                isinstance(t, ast.Name) and t.id == "_OFFGRID_FULL_SOC_THRESHOLD"
                for t in targets
            )
            and isinstance(value, ast.Constant)
        ):
            assert value.value >= 1, "_OFFGRID_FULL_SOC_THRESHOLD should be a percent (e.g. 98.0)"
            found = True
    assert found, "_OFFGRID_FULL_SOC_THRESHOLD class attribute not found"
