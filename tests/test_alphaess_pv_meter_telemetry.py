"""Regression coverage for AlphaESS PV source selection."""

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ALPHAESS = ROOT / "custom_components" / "power_sync" / "inverters" / "alphaess.py"
INIT = ROOT / "custom_components" / "power_sync" / "__init__.py"


def test_alphaess_uses_positive_pv_meter_when_inverter_total_is_zero():
    source = ALPHAESS.read_text()

    assert "REG_PV_METER_TOTAL_ACTIVE_POWER, 2" in source
    assert "self._to_signed32(pv_meter_regs[0], pv_meter_regs[1])" in source
    assert "(pv_w is None or pv_w == 0) and pv_meter_w is not None and pv_meter_w > 0" in source
    assert 'attrs["pv_power_source"] = "pv_meter"' in source


def test_tesla_optimizer_retry_is_not_reported_as_a_terminal_failure():
    source = INIT.read_text()
    start = source.index('"Force charge failed before tariff upload: Tesla grid charging did not verify"')
    retry_branch = source[start : start + 1800]

    assert 'if source == "optimizer":' in retry_branch
    assert '"Force Charge Retrying"' in retry_branch
    assert '"Force Charge Failed"' in retry_branch
