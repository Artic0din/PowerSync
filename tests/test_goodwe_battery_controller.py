from __future__ import annotations

import asyncio
import importlib
import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
COMPONENT_ROOT = ROOT / "custom_components" / "power_sync"


def _load_goodwe_controller_module():
    saved = {
        name: sys.modules.get(name)
        for name in (
            "power_sync",
            "power_sync.inverters",
            "power_sync.inverters.goodwe_battery",
        )
    }

    power_sync = types.ModuleType("power_sync")
    power_sync.__path__ = [str(COMPONENT_ROOT)]
    sys.modules["power_sync"] = power_sync

    inverters = types.ModuleType("power_sync.inverters")
    inverters.__path__ = [str(COMPONENT_ROOT / "inverters")]
    sys.modules["power_sync.inverters"] = inverters
    sys.modules.pop("power_sync.inverters.goodwe_battery", None)

    module = importlib.import_module("power_sync.inverters.goodwe_battery")

    def restore() -> None:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    return module, restore


class _FakeGoodWeInverter:
    def __init__(self) -> None:
        self.export_limits: list[int] = []

    async def set_grid_export_limit(self, value: int) -> None:
        if value > 65535:
            raise OverflowError("int too big to convert")
        self.export_limits.append(value)


def test_restore_uses_goodwe_export_limit_register_maximum():
    module, restore_module = _load_goodwe_controller_module()
    try:
        inverter = _FakeGoodWeInverter()
        controller = module.GoodWeBatteryController("192.0.2.10")
        controller._inverter = inverter

        async def connect() -> bool:
            return True

        controller.connect = connect

        assert asyncio.run(controller.restore())
        assert inverter.export_limits == [65535]
    finally:
        restore_module()


def test_set_grid_export_limit_clamps_to_goodwe_register_range():
    module, restore_module = _load_goodwe_controller_module()
    try:
        inverter = _FakeGoodWeInverter()
        controller = module.GoodWeBatteryController("192.0.2.10")
        controller._inverter = inverter

        assert asyncio.run(controller.set_grid_export_limit(99999))
        assert inverter.export_limits == [65535]
    finally:
        restore_module()
