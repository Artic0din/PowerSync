"""Regression coverage for Fronius curtailment live-status freshness."""

from __future__ import annotations

import ast
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace


INIT_PATH = Path(__file__).resolve().parent.parent / "custom_components" / "power_sync" / "__init__.py"


def _fronius_snapshot_is_fresh():
    """Load the nested Fronius guard without importing the integration."""
    source = INIT_PATH.read_text()
    module = ast.parse(source)
    node = next(
        candidate
        for candidate in ast.walk(module)
        if isinstance(candidate, ast.FunctionDef)
        and candidate.name == "_fronius_snapshot_is_fresh"
    )
    namespace = {
        "timedelta": timedelta,
        "dt_util": SimpleNamespace(utcnow=lambda: datetime.now(timezone.utc)),
    }
    exec(textwrap.dedent(ast.get_source_segment(source, node) or ""), namespace)
    return namespace["_fronius_snapshot_is_fresh"]


def test_fronius_successful_ready_snapshot_without_optional_timestamp_is_usable():
    """Ticket #24: do not defer curtailment on HA runtimes without that field."""
    coordinator = SimpleNamespace(
        last_update_success=True,
        data={"telemetry_ready": True},
        update_interval=timedelta(seconds=30),
    )

    assert _fronius_snapshot_is_fresh()(coordinator, coordinator.data) is True


def test_fronius_unsuccessful_or_not_ready_snapshot_remains_rejected():
    is_fresh = _fronius_snapshot_is_fresh()

    assert is_fresh(SimpleNamespace(last_update_success=False), {"telemetry_ready": True}) is False
    assert is_fresh(SimpleNamespace(last_update_success=True), {"telemetry_ready": False}) is False
