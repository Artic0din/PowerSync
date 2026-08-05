"""Tests for provider-neutral solar forecast accuracy learning."""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parent.parent
    / "custom_components"
    / "power_sync"
    / "optimization"
    / "solar_forecast_learning.py"
)
SPEC = importlib.util.spec_from_file_location("solar_forecast_learning_test", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
SolarForecastLearner = MODULE.SolarForecastLearner


def _record_day(
    learner,
    *,
    source: str,
    day: int,
    forecast_kw: float,
    actual_kw: float,
) -> None:
    start = datetime(2026, 7, day, 8, 0, tzinfo=timezone.utc)
    for index in range(49):
        learner.observe(
            timestamp=start + timedelta(minutes=5 * index),
            source=source,
            forecast_kw=forecast_kw,
            actual_kw=actual_kw,
            valid=True,
        )


def _finalize_through_day(learner, source: str, last_day: int) -> None:
    learner.observe(
        timestamp=datetime(2026, 7, last_day + 1, 8, 0, tzinfo=timezone.utc),
        source=source,
        forecast_kw=1.0,
        actual_kw=1.0,
        valid=True,
    )


def test_cold_start_preserves_legacy_mode_and_deduplicates_timestamp():
    learner = SolarForecastLearner()
    timestamp = datetime(2026, 7, 1, 8, 0, tzinfo=timezone.utc)

    assert learner.observe(
        timestamp=timestamp,
        source="solcast",
        forecast_kw=4.0,
        actual_kw=4.0,
        valid=True,
    ) is True
    assert learner.observe(
        timestamp=timestamp,
        source="solcast",
        forecast_kw=4.0,
        actual_kw=4.0,
        valid=True,
    ) is False
    assert learner.allowance_kwh("solcast") is None
    assert learner.confidence("solcast") == 0.0
    assert learner.diagnostics("solcast")["mode"] == "legacy"


def test_learns_one_sided_provider_specific_shortfall_and_blends_confidence():
    learner = SolarForecastLearner()
    for day in range(1, 4):
        # Four forecast hours at 4 kW versus 3.5 kW actual = 2 kWh shortfall.
        _record_day(
            learner,
            source="solcast",
            day=day,
            forecast_kw=4.0,
            actual_kw=3.5,
        )
    _finalize_through_day(learner, "solcast", 3)

    assert learner.valid_days("solcast") == 3
    assert learner.allowance_kwh("solcast") == 2.0
    assert 0.0 < learner.confidence("solcast") < 1.0
    assert learner.allowance_kwh("open_meteo") is None


def test_overproduction_is_zero_shortfall_not_negative_allowance():
    learner = SolarForecastLearner()
    for day in range(1, 4):
        _record_day(
            learner,
            source="open_meteo",
            day=day,
            forecast_kw=3.0,
            actual_kw=4.0,
        )
    _finalize_through_day(learner, "open_meteo", 3)

    assert learner.allowance_kwh("open_meteo") == 0.0


def test_invalid_window_breaks_energy_integration_and_records_reason():
    learner = SolarForecastLearner()
    start = datetime(2026, 7, 1, 8, 0, tzinfo=timezone.utc)

    learner.observe(
        timestamp=start,
        source="volcast",
        forecast_kw=4.0,
        actual_kw=4.0,
        valid=True,
    )
    learner.observe(
        timestamp=start + timedelta(minutes=5),
        source="volcast",
        forecast_kw=4.0,
        actual_kw=0.0,
        valid=False,
        skip_reason="curtailment",
    )
    learner.observe(
        timestamp=start + timedelta(minutes=10),
        source="volcast",
        forecast_kw=4.0,
        actual_kw=4.0,
        valid=True,
    )

    active = learner.active_days["volcast"]
    assert active.valid_hours == 0.0
    assert learner.skipped["curtailment"] == 1


def test_store_round_trip_retains_bounded_learning_state():
    learner = SolarForecastLearner()
    for day in range(1, 4):
        _record_day(
            learner,
            source="solcast",
            day=day,
            forecast_kw=4.0,
            actual_kw=3.75,
        )
    _finalize_through_day(learner, "solcast", 3)

    restored = SolarForecastLearner.from_dict(learner.to_dict())

    assert restored.allowance_kwh("solcast") == 1.0
    assert restored.valid_days("solcast") == 3
    assert restored.to_dict()["version"] == 1
    assert SolarForecastLearner.from_dict({"version": 999}).histories == {}
