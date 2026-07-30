"""Regression coverage for cumulative grid-import automation triggers."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
TRIGGERS_PATH = (
    ROOT / "custom_components" / "power_sync" / "automations" / "triggers.py"
)
AUTOMATIONS_PATH = (
    ROOT / "custom_components" / "power_sync" / "automations" / "__init__.py"
)


@dataclass
class TriggerResult:
    triggered: bool
    reason: str = ""


class _Store:
    def __init__(self) -> None:
        self.runtime = None
        self.last_value = None

    def get_trigger_runtime(self, automation_id):
        return dict(self.runtime) if isinstance(self.runtime, dict) else None

    def update_trigger_runtime(self, automation_id, runtime):
        self.runtime = dict(runtime) if isinstance(runtime, dict) else None

    def update_trigger_state(self, automation_id, value):
        self.last_value = value


def _trigger_namespace() -> dict[str, Any]:
    source = TRIGGERS_PATH.read_text()
    module = ast.parse(source)
    wanted = {
        "_grid_import_window_key",
        "_finite_non_negative",
        "_evaluate_grid_import_energy_trigger",
    }
    namespace: dict[str, Any] = {
        "Any": Any,
        "Dict": dict,
        "datetime": datetime,
        "timedelta": timedelta,
        "math": math,
        "TriggerResult": TriggerResult,
        "dt_util": SimpleNamespace(
            now=lambda: datetime(2026, 7, 30, 11, 0, tzinfo=timezone.utc)
        ),
    }
    for node in module.body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            segment = ast.get_source_segment(source, node)
            assert segment is not None
            exec(segment, namespace)
    return namespace


def _evaluate(namespace, store, trigger, when, total, power=0.0):
    return namespace["_evaluate_grid_import_energy_trigger"](
        trigger,
        {
            "current_time": when,
            "grid_import_today_kwh": total,
            "grid_import_kw": power,
            "grid_import_energy_power_kw": power,
        },
        store,
        1,
    )


def test_cumulative_grid_import_triggers_once_when_window_quota_is_reached():
    namespace = _trigger_namespace()
    store = _Store()
    trigger = {
        "time_window_start": "11:00",
        "time_window_end": "14:00",
        "grid_import_energy_threshold_kwh": 50,
    }

    first = _evaluate(
        namespace,
        store,
        trigger,
        datetime(2026, 7, 30, 11, 0, tzinfo=timezone.utc),
        100,
    )
    below = _evaluate(
        namespace,
        store,
        trigger,
        datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc),
        125,
    )
    crossed = _evaluate(
        namespace,
        store,
        trigger,
        datetime(2026, 7, 30, 13, 0, tzinfo=timezone.utc),
        151,
    )
    repeated = _evaluate(
        namespace,
        store,
        trigger,
        datetime(2026, 7, 30, 13, 1, tzinfo=timezone.utc),
        152,
    )

    assert first.triggered is False
    assert below.triggered is False
    assert store.last_value == 52
    assert crossed.triggered is True
    assert "51.00 kWh" in crossed.reason
    assert repeated.triggered is False
    assert "already triggered" in repeated.reason


def test_cumulative_grid_import_resets_for_the_next_local_window():
    namespace = _trigger_namespace()
    store = _Store()
    trigger = {
        "time_window_start": "11:00",
        "time_window_end": "14:00",
        "grid_import_energy_threshold_kwh": 50,
    }

    _evaluate(
        namespace,
        store,
        trigger,
        datetime(2026, 7, 30, 13, 0, tzinfo=timezone.utc),
        100,
    )
    result = _evaluate(
        namespace,
        store,
        trigger,
        datetime(2026, 7, 31, 11, 0, tzinfo=timezone.utc),
        20,
    )

    assert result.triggered is False
    assert store.runtime["accumulated_kwh"] == 0
    assert store.runtime["last_total_kwh"] == 20


def test_overnight_window_handles_daily_import_counter_reset():
    namespace = _trigger_namespace()
    store = _Store()
    trigger = {
        "time_window_start": "23:00",
        "time_window_end": "01:00",
        "grid_import_energy_threshold_kwh": 5,
    }

    _evaluate(
        namespace,
        store,
        trigger,
        datetime(2026, 7, 30, 23, 59, tzinfo=timezone.utc),
        100,
    )
    result = _evaluate(
        namespace,
        store,
        trigger,
        datetime(2026, 7, 31, 0, 10, tzinfo=timezone.utc),
        2,
    )

    assert result.triggered is False
    assert store.runtime["accumulated_kwh"] == 2


def test_grid_import_power_is_integrated_when_energy_counter_is_unavailable():
    namespace = _trigger_namespace()
    store = _Store()
    trigger = {
        "time_window_start": "11:00",
        "time_window_end": "14:00",
        "grid_import_energy_threshold_kwh": 1,
    }

    _evaluate(
        namespace,
        store,
        trigger,
        datetime(2026, 7, 30, 11, 0, tzinfo=timezone.utc),
        None,
        power=6,
    )
    result = _evaluate(
        namespace,
        store,
        trigger,
        datetime(2026, 7, 30, 11, 1, tzinfo=timezone.utc),
        None,
        power=6,
    )

    assert result.triggered is False
    assert store.runtime["accumulated_kwh"] == 0.1


def test_automation_engine_exposes_and_persists_grid_import_energy_state():
    source = AUTOMATIONS_PATH.read_text()

    assert '"grid_import_today_kwh": None' in source
    assert '"grid_import_energy_power_kw": None' in source
    assert 'energy_summary.get(' in source
    assert '"grid_import_today_kwh"' in source
    assert '"trigger_runtime": None' in source
    assert "def update_trigger_runtime(" in source
    assert "if self._store.runtime_dirty:" in source
    assert "total_seconds() >= 60" in source
