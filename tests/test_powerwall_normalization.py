"""Unit tests for powerwall_local.normalization.

Covers the four public functions that translate between raw Powerwall local
hardware values and the user-facing (Tesla app / cloud API) representation:

    normalize_local_soc_percent        — raw full-pack SOE → app SOC
    detect_local_backup_reserve_offset — infer hidden hardware reserve offset
    normalize_local_backup_reserve_percent — local reserve readback → user %
    local_backup_reserve_write_percent — user % → local write value

The convention:
    - Local hardware includes the DEFAULT_LOW_SOE_RESERVE_PCT (5 %) hidden
      reserve. So local 5 % == user 0 %, local 100 % == user 100 %.
    - Battery sign convention: negative = charge, positive = discharge.
      These functions operate on SOC/reserve percentages, not power values.

Run with: pytest tests/test_powerwall_normalization.py
"""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest

# --- stub the package namespace so we can import the module standalone ---
ROOT = Path(__file__).resolve().parent.parent / "custom_components" / "power_sync"
_PKG = "power_sync_norm_test"
_LPKG = f"{_PKG}.powerwall_local"


def _ensure_namespace() -> None:
    if _PKG in sys.modules:
        return
    pkg = types.ModuleType(_PKG)
    pkg.__path__ = [str(ROOT)]
    sys.modules[_PKG] = pkg
    local = types.ModuleType(_LPKG)
    local.__path__ = [str(ROOT / "powerwall_local")]
    sys.modules[_LPKG] = local


def _load_normalization():
    _ensure_namespace()
    mod_name = f"{_LPKG}.normalization"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(
        mod_name, ROOT / "powerwall_local" / "normalization.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def norm():
    return _load_normalization()


# Constants from the module under test
DEFAULT_LOW_SOE_RESERVE_PCT = 5.0


# ---------------------------------------------------------------------------
# normalize_local_soc_percent
# ---------------------------------------------------------------------------

class TestNormalizeLocalSocPercent:
    """normalize_local_soc_percent maps raw full-pack SOE → app SOC %."""

    def test_five_percent_raw_maps_to_zero(self, norm):
        # ARRANGE: local hardware floor (5 %) == 0 % in Tesla app
        # ACT
        result = norm.normalize_local_soc_percent(5.0)
        # ASSERT
        assert result == pytest.approx(0.0, abs=0.001)

    def test_100_percent_raw_maps_to_100(self, norm):
        # ARRANGE: full battery
        # ACT
        result = norm.normalize_local_soc_percent(100.0)
        # ASSERT
        assert result == pytest.approx(100.0, abs=0.001)

    def test_midpoint_value_is_correct(self, norm):
        # ARRANGE: raw 52.5 % lies halfway between 5 % and 100 %
        # (52.5 - 5) / (100 - 5) * 100 = 50.0
        # ACT
        result = norm.normalize_local_soc_percent(52.5)
        # ASSERT
        assert result == pytest.approx(50.0, abs=0.01)

    def test_below_floor_clamps_to_zero(self, norm):
        # ARRANGE: hardware may briefly read below the reserve floor
        # ACT
        result = norm.normalize_local_soc_percent(2.0)
        # ASSERT: must not return a negative SOC
        assert result == pytest.approx(0.0, abs=0.001)

    def test_above_100_clamps_to_100(self, norm):
        # ARRANGE: defensive — malformed sensor reading
        # ACT
        result = norm.normalize_local_soc_percent(110.0)
        # ASSERT
        assert result == pytest.approx(100.0, abs=0.001)

    def test_zero_raw_clamps_to_zero(self, norm):
        # ARRANGE
        # ACT
        result = norm.normalize_local_soc_percent(0.0)
        # ASSERT
        assert result == pytest.approx(0.0, abs=0.001)

    def test_invalid_string_returns_none(self, norm):
        # ARRANGE
        # ACT
        result = norm.normalize_local_soc_percent("not-a-number")
        # ASSERT
        assert result is None

    def test_none_returns_none(self, norm):
        # ARRANGE / ACT
        result = norm.normalize_local_soc_percent(None)
        # ASSERT
        assert result is None

    def test_string_numeric_is_accepted(self, norm):
        # ARRANGE: HA state values come through as strings
        # ACT
        result = norm.normalize_local_soc_percent("52.5")
        # ASSERT
        assert result == pytest.approx(50.0, abs=0.01)

    def test_known_real_world_reading(self, norm):
        # ARRANGE: local hardware reads 85.0 %, hidden reserve 5 %
        # Expected: (85 - 5) / 95 * 100 ≈ 84.21 %
        # ACT
        result = norm.normalize_local_soc_percent(85.0)
        # ASSERT
        assert result == pytest.approx(84.21, abs=0.1)


# ---------------------------------------------------------------------------
# detect_local_backup_reserve_offset
# ---------------------------------------------------------------------------

class TestDetectLocalBackupReserveOffset:
    """detect_local_backup_reserve_offset infers the hidden hardware reserve."""

    def test_standard_five_percent_offset(self, norm):
        # ARRANGE: local reads 25 %, user-facing = 20 % → offset = 5 %
        # ACT
        result = norm.detect_local_backup_reserve_offset(25.0, 20.0)
        # ASSERT
        assert result == pytest.approx(5.0, abs=0.01)

    def test_zero_offset(self, norm):
        # ARRANGE: local and user-facing are the same (e.g. PW2 without hidden reserve)
        # ACT
        result = norm.detect_local_backup_reserve_offset(20.0, 20.0)
        # ASSERT
        assert result == pytest.approx(0.0, abs=0.001)

    def test_returns_none_when_local_is_none(self, norm):
        # ARRANGE
        # ACT
        result = norm.detect_local_backup_reserve_offset(None, 20.0)
        # ASSERT
        assert result is None

    def test_returns_none_when_user_facing_is_none(self, norm):
        # ARRANGE
        # ACT
        result = norm.detect_local_backup_reserve_offset(25.0, None)
        # ASSERT
        assert result is None

    def test_returns_none_when_offset_is_negative(self, norm):
        # ARRANGE: local < user-facing — physically impossible, bad readback
        # ACT
        result = norm.detect_local_backup_reserve_offset(15.0, 20.0)
        # ASSERT
        assert result is None

    def test_returns_none_when_offset_exceeds_max(self, norm):
        # ARRANGE: 25 % offset is above MAX_AUTO_DETECTED_LOW_SOE_RESERVE_PCT (20 %)
        # ACT
        result = norm.detect_local_backup_reserve_offset(45.0, 20.0)
        # ASSERT
        assert result is None

    def test_returns_none_when_local_is_100(self, norm):
        # ARRANGE: 100 % local reserve makes computation meaningless
        # ACT
        result = norm.detect_local_backup_reserve_offset(100.0, 95.0)
        # ASSERT
        assert result is None

    def test_returns_none_when_user_facing_is_100(self, norm):
        # ARRANGE
        # ACT
        result = norm.detect_local_backup_reserve_offset(100.0, 100.0)
        # ASSERT
        assert result is None

    def test_max_valid_offset(self, norm):
        # ARRANGE: MAX_AUTO_DETECTED_LOW_SOE_RESERVE_PCT is 20 %
        # local = 40 %, user = 20 % → offset = 20 % — should be accepted
        # ACT
        result = norm.detect_local_backup_reserve_offset(40.0, 20.0)
        # ASSERT
        assert result == pytest.approx(20.0, abs=0.01)

    def test_invalid_string_local_returns_none(self, norm):
        # ARRANGE
        # ACT
        result = norm.detect_local_backup_reserve_offset("bad", 20.0)
        # ASSERT
        assert result is None


# ---------------------------------------------------------------------------
# normalize_local_backup_reserve_percent
# ---------------------------------------------------------------------------

class TestNormalizeLocalBackupReservePercent:
    """normalize_local_backup_reserve_percent maps local readback → user-facing %."""

    def test_user_zero_when_local_equals_low_soe_reserve(self, norm):
        # ARRANGE: local = 5 % == low SOE reserve → user sees 0 %
        # ACT
        result = norm.normalize_local_backup_reserve_percent(5.0)
        # ASSERT
        assert result == 0

    def test_user_reserve_matches_local_minus_offset(self, norm):
        # ARRANGE: local = 25 %, default offset = 5 % → user = 20 %
        # ACT
        result = norm.normalize_local_backup_reserve_percent(25.0)
        # ASSERT
        assert result == 20

    def test_local_100_maps_to_100(self, norm):
        # ARRANGE
        # ACT
        result = norm.normalize_local_backup_reserve_percent(100.0)
        # ASSERT
        assert result == 100

    def test_below_low_soe_reserve_maps_to_zero(self, norm):
        # ARRANGE: local reads 3 % (below hidden reserve floor)
        # ACT
        result = norm.normalize_local_backup_reserve_percent(3.0)
        # ASSERT
        assert result == 0

    def test_none_returns_none(self, norm):
        # ARRANGE / ACT
        result = norm.normalize_local_backup_reserve_percent(None)
        # ASSERT
        assert result is None

    def test_custom_low_soe_reserve_offset(self, norm):
        # ARRANGE: site with 7 % hardware reserve
        # local = 27 % → user = 20 %
        # ACT
        result = norm.normalize_local_backup_reserve_percent(27.0, low_soe_reserve_pct=7.0)
        # ASSERT
        assert result == 20

    def test_custom_offset_above_max_is_clamped(self, norm):
        # ARRANGE: low_soe_reserve_pct > MAX_AUTO_DETECTED (20 %) gets clamped
        # so only the max (20 %) is used; local = 25 % → user = 5 %
        # ACT
        result = norm.normalize_local_backup_reserve_percent(25.0, low_soe_reserve_pct=25.0)
        # ASSERT: offset clamped to 20; 25 - 20 = 5
        assert result == 5

    def test_integer_result_type(self, norm):
        # ARRANGE / ACT
        result = norm.normalize_local_backup_reserve_percent(25.0)
        # ASSERT: function contract returns int
        assert isinstance(result, int)


# ---------------------------------------------------------------------------
# local_backup_reserve_write_percent
# ---------------------------------------------------------------------------

class TestLocalBackupReserveWritePercent:
    """local_backup_reserve_write_percent maps user-facing target → local write value."""

    def test_user_zero_writes_low_soe_reserve(self, norm):
        # ARRANGE: user sets 0 % → write 5 % (the hidden floor)
        # ACT
        result = norm.local_backup_reserve_write_percent(0)
        # ASSERT
        assert result == 5

    def test_user_20_writes_25(self, norm):
        # ARRANGE: user sets 20 % → write 25 % (20 + 5 offset)
        # ACT
        result = norm.local_backup_reserve_write_percent(20)
        # ASSERT
        assert result == 25

    def test_user_100_writes_100(self, norm):
        # ARRANGE: full reserve — no offset applied at ceiling
        # ACT
        result = norm.local_backup_reserve_write_percent(100)
        # ASSERT
        assert result == 100

    def test_none_returns_none(self, norm):
        # ARRANGE / ACT
        result = norm.local_backup_reserve_write_percent(None)
        # ASSERT
        assert result is None

    def test_custom_offset(self, norm):
        # ARRANGE: site with 7 % hardware reserve; user = 20 % → write 27 %
        # ACT
        result = norm.local_backup_reserve_write_percent(20, low_soe_reserve_pct=7.0)
        # ASSERT
        assert result == 27

    def test_over_100_clamps_to_100(self, norm):
        # ARRANGE: rounding could push result above 100
        # ACT
        result = norm.local_backup_reserve_write_percent(95, low_soe_reserve_pct=10.0)
        # ASSERT: 95 + 10 = 105, clamped to 100
        assert result == 100

    def test_integer_result_type(self, norm):
        # ARRANGE / ACT
        result = norm.local_backup_reserve_write_percent(20)
        # ASSERT: function contract returns int
        assert isinstance(result, int)

    def test_roundtrip_write_then_read(self, norm):
        # ARRANGE: write a user value then read it back — should recover exact value
        for user_pct in (0, 10, 20, 50, 80, 100):
            local_write = norm.local_backup_reserve_write_percent(user_pct)
            # ACT
            recovered = norm.normalize_local_backup_reserve_percent(local_write)
            # ASSERT
            assert recovered == user_pct, (
                f"round-trip failed for user={user_pct}%: "
                f"write={local_write}%, read_back={recovered}%"
            )
