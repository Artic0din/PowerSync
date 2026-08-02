"""Disposable production-code regression target for PowerBot workflow verification."""

from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "power_sync"
    / "currency.py"
)
SPEC = importlib.util.spec_from_file_location("power_sync_task17_currency", MODULE_PATH)
assert SPEC is not None
CURRENCY_MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(CURRENCY_MODULE)
normalize_currency = CURRENCY_MODULE.normalize_currency


def test_normalizes_currency_whitespace_and_case() -> None:
    assert normalize_currency(" nzd ") == "NZD"
