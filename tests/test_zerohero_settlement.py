"""Regression tests for GloBird ZeroHero settlement rules."""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parent.parent
ZEROHERO_PATH = ROOT / "custom_components" / "power_sync" / "zerohero.py"


def _load_zerohero_module():
    spec = importlib.util.spec_from_file_location("powersync_zerohero_test", ZEROHERO_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


zerohero = _load_zerohero_module()


def _ts(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 5, 3, hour, minute, tzinfo=timezone.utc)


def test_current_plan_topup_applies_only_to_first_15kwh_in_window():
    config = zerohero.zerohero_config_from_settings(
        {"globird_plan": "zerohero_current"}
    )

    result = zerohero.settle_zerohero_series(
        config,
        [_ts(18, 0), _ts(19, 0), _ts(20, 0), _ts(21, 0)],
        [0.0, 0.0, 0.0, 0.0],
        [6.0, 6.0, 6.0, 6.0],
        [0.05, 0.05, 0.05, 0.05],
    )

    assert result.total_export_kwh == pytest.approx(24.0)
    assert result.bonus_export_kwh == pytest.approx(15.0)
    assert result.base_export_earnings == pytest.approx(1.20)
    assert result.bonus_export_earnings == pytest.approx(1.50)
    assert result.export_earnings == pytest.approx(2.70)


def test_jul_2026_plan_uses_10c_super_export_and_zerocharge_window():
    config = zerohero.zerohero_config_from_settings(
        {"globird_plan": "zerohero_jul_2026"}
    )

    assert config.start == "18:00"
    assert config.end == "21:00"
    assert config.export_cap_kwh == pytest.approx(15.0)
    assert config.super_export_rate == pytest.approx(0.10)
    assert config.import_allowance_kwh == pytest.approx(0.09)
    assert config.zerocharge_start == "12:00"
    assert config.zerocharge_end == "15:00"
    assert config.zerocharge_import_cap_kwh == pytest.approx(50.0)
    assert zerohero.zerocharge_monthly_cap_kwh(config, _ts(12, 0)) == pytest.approx(
        1550.0
    )
    assert zerohero.zerocharge_is_in_window(_ts(12, 0), config)
    assert not zerohero.zerocharge_is_in_window(_ts(15, 0), config)


def test_pre_jul_2026_plan_keeps_grandfathered_free_window():
    config = zerohero.zerohero_config_from_settings(
        {"globird_plan": "zerohero_pre_jul_2026"}
    )

    assert config.start == "18:00"
    assert config.end == "21:00"
    assert config.export_cap_kwh == pytest.approx(15.0)
    assert config.super_export_rate == pytest.approx(0.10)
    assert config.zerocharge_start == "11:00"
    assert config.zerocharge_end == "14:00"
    assert config.zerocharge_import_cap_kwh == pytest.approx(50.0)


def _tesla_zerohero_tariff(free_start: int, free_end: int) -> dict:
    return {
        "plan_name": "Zerohero",
        "buy_rates": {
            "ON_PEAK": 0.53,
            "SUPER_OFF_PEAK": 0.0,
        },
        "sell_rates": {
            "ON_PEAK": 0.10,
            "SUPER_OFF_PEAK": 0.0,
        },
        "tou_periods": {
            "ON_PEAK": [
                {
                    "fromHour": 18,
                    "fromMinute": 0,
                    "toHour": 21,
                    "toMinute": 0,
                }
            ],
            "SUPER_OFF_PEAK": [
                {
                    "fromHour": free_start,
                    "fromMinute": 0,
                    "toHour": free_end,
                    "toMinute": 0,
                }
            ],
        },
    }


@pytest.mark.parametrize(
    ("free_start", "free_end", "expected_plan"),
    [
        (11, 14, "zerohero_pre_jul_2026"),
        (12, 15, "zerohero_jul_2026"),
    ],
)
def test_tesla_tariff_distinguishes_pre_and_post_july_contracts(
    free_start,
    free_end,
    expected_plan,
):
    tariff = _tesla_zerohero_tariff(free_start, free_end)

    assert zerohero.infer_zerohero_plan_from_tariff(tariff) == expected_plan


def test_definitive_tesla_zerohero_tariff_repairs_not_zerohero_default():
    entry = SimpleNamespace(
        data={"electricity_provider": "globird"},
        options={"globird_plan": "not_zerohero"},
    )
    tariff = _tesla_zerohero_tariff(11, 14)

    config = zerohero.zerohero_config_from_entry(entry, tariff)

    assert config is not None
    assert config.plan == "zerohero_pre_jul_2026"
    assert config.zerocharge_start == "11:00"


def test_non_zerohero_tariff_name_never_overrides_not_zerohero_choice():
    entry = SimpleNamespace(data={}, options={"globird_plan": "not_zerohero"})
    tariff = _tesla_zerohero_tariff(11, 14)
    tariff["plan_name"] = "Standard TOU"

    assert zerohero.zerohero_config_from_entry(entry, tariff) is None


def test_definitive_tariff_never_overrides_an_explicit_plan():
    entry = SimpleNamespace(
        data={},
        options={
            "globird_plan": "zerohero_custom",
            "globird_zerohero_start": "17:30",
            "globird_zerohero_end": "20:30",
        },
    )

    config = zerohero.zerohero_config_from_entry(
        entry,
        _tesla_zerohero_tariff(11, 14),
    )

    assert config is not None
    assert config.plan == "zerohero_custom"
    assert config.start == "17:30"
    assert config.end == "20:30"


def test_zerohero_name_and_rates_without_export_window_do_not_activate_plan():
    tariff = _tesla_zerohero_tariff(11, 14)
    tariff["tou_periods"]["ON_PEAK"][0].update(
        {"fromHour": 17, "toHour": 20}
    )

    assert zerohero.infer_zerohero_plan_from_tariff(tariff) is None


def test_legacy_plan_topup_applies_only_to_first_10kwh_before_8pm():
    config = zerohero.zerohero_config_from_settings(
        {"globird_plan": "zerohero_legacy"}
    )

    result = zerohero.settle_zerohero_series(
        config,
        [_ts(18, 0), _ts(19, 0), _ts(20, 0)],
        [0.0, 0.0, 0.0],
        [6.0, 6.0, 6.0],
        [0.04, 0.04, 0.04],
    )

    assert result.total_export_kwh == pytest.approx(18.0)
    assert result.bonus_export_kwh == pytest.approx(10.0)
    assert result.base_export_earnings == pytest.approx(0.72)
    assert result.bonus_export_earnings == pytest.approx(1.10)


def test_import_above_hourly_allowance_loses_credit():
    config = zerohero.zerohero_config_from_settings(
        {"globird_plan": "zerohero_current"}
    )

    result = zerohero.settle_zerohero_series(
        config,
        [_ts(18, 0), _ts(19, 0), _ts(20, 0), _ts(21, 0)],
        [0.02, 0.02, 0.06, 0.0],
        [1.0, 1.0, 1.0, 0.0],
        [0.05, 0.05, 0.05, 0.05],
        include_credit=True,
    )

    assert config.import_allowance_kwh == pytest.approx(0.09)
    assert result.import_window_kwh == pytest.approx(0.10)
    assert result.credit_status == "lost"
    assert result.credit_value == 0.0
    assert result.bonus_export_kwh == pytest.approx(3.0)
    assert result.bonus_export_earnings == pytest.approx(0.30)


def test_credit_is_included_when_window_stays_under_threshold():
    config = zerohero.zerohero_config_from_settings(
        {"globird_plan": "zerohero_current"}
    )

    result = zerohero.settle_zerohero_series(
        config,
        [_ts(18, 0), _ts(19, 0), _ts(20, 0), _ts(21, 0)],
        [0.01, 0.01, 0.01, 0.0],
        [1.0, 1.0, 1.0, 0.0],
        [0.05, 0.05, 0.05, 0.05],
        include_credit=True,
    )

    assert result.import_window_kwh == pytest.approx(0.03)
    assert result.credit_status == "earned"
    assert result.credit_value == pytest.approx(1.0)


def test_zerocharge_import_credit_applies_only_to_capped_window_imports():
    config = zerohero.zerohero_config_from_settings(
        {"globird_plan": "zerohero_jul_2026"}
    )

    used, credit = zerohero.settle_zerocharge_imports(
        config,
        [_ts(11, 55), _ts(12, 0), _ts(13, 0), _ts(15, 0)],
        [10.0, 30.0, 30.0, 10.0],
        [0.40, 0.50, 0.60, 0.70],
        initial_import_kwh=1540.0,
        initial_period_key="2026-05",
    )

    assert used == pytest.approx(1600.0)
    assert credit == pytest.approx(5.0)


def test_zerocharge_monthly_pool_allows_50kwh_early_in_month():
    config = zerohero.zerohero_config_from_settings(
        {"globird_plan": "zerohero_jul_2026"}
    )
    timestamp = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)

    used, credit = zerohero.settle_zerocharge_imports(
        config,
        [timestamp],
        [5.0],
        [0.50],
        initial_import_kwh=50.0,
        initial_period_key="2026-08",
    )

    assert used == pytest.approx(55.0)
    assert credit == pytest.approx(2.5)


def test_zerocharge_empty_series_preserves_initial_month_usage():
    config = zerohero.zerohero_config_from_settings(
        {"globird_plan": "zerohero_jul_2026"}
    )

    used, credit = zerohero.settle_zerocharge_imports(
        config,
        [],
        [],
        [],
        initial_import_kwh=5.0,
    )

    assert used == pytest.approx(5.0)
    assert credit == 0.0


def test_zerocharge_invalid_period_returns_no_pool():
    config = zerohero.zerohero_config_from_settings(
        {"globird_plan": "zerohero_jul_2026"}
    )

    assert zerohero.zerocharge_monthly_cap_kwh(config, "2026-13") == 0.0


def test_zerocharge_month_caps_are_separate_at_august_september_boundary():
    config = zerohero.zerohero_config_from_settings(
        {"globird_plan": "zerohero_jul_2026"}
    )
    timestamps = [
        datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc),
        datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
    ]

    used, credit = zerohero.settle_zerocharge_imports(
        config,
        timestamps,
        [1550.0, 5.0],
        [0.10, 0.50],
    )

    assert used == pytest.approx(1555.0)
    assert credit == pytest.approx(155.0 + 2.5)


def test_existing_custom_zerohero_does_not_enable_zerocharge_by_default():
    config = zerohero.zerohero_config_from_settings(
        {
            "globird_plan": "zerohero_custom",
            "globird_zerohero_start": "18:00",
            "globird_zerohero_end": "21:00",
            "globird_zerohero_export_cap_kwh": 15,
            "globird_zerohero_super_export_rate": 10,
            "globird_zerohero_credit_amount": 1,
            "globird_zerohero_import_limit_kw": 0.03,
        }
    )

    assert not config.zerocharge_enabled
