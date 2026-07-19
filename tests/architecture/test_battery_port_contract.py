"""Contract tests for BatteryPort units and ServiceBatteryPort wiring."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from custom_components.power_sync.control import (
    BatteryPort,
    ServiceBatteryPort,
    apply_restore_success_gate,
)
from custom_components.power_sync.capabilities import get_brand_capabilities


def test_service_battery_port_satisfies_protocol():
    hass = MagicMock()
    hass.services.async_call = AsyncMock()
    port = ServiceBatteryPort(hass, "tesla")
    assert isinstance(port, BatteryPort)


def test_service_battery_port_force_charge_uses_watts_and_minutes():
    hass = MagicMock()
    hass.services.async_call = AsyncMock()
    port = ServiceBatteryPort(hass, "tesla")

    ok = asyncio.get_event_loop().run_until_complete(
        port.force_charge(power_w=3500, duration_minutes=45, source="optimizer")
    )

    assert ok is True
    hass.services.async_call.assert_awaited_once()
    args = hass.services.async_call.await_args
    assert args.args[0] == "power_sync"
    assert args.args[1] == "force_charge"
    assert args.args[2]["power_w"] == 3500
    assert args.args[2]["duration"] == 45
    assert args.args[2]["source"] == "optimizer"


def test_restore_success_gate_clears_only_after_success():
    cleared = {"done": False}

    async def _fail():
        return False

    async def _ok():
        return True

    async def _run():
        assert (
            await apply_restore_success_gate(
                write=_fail,
                clear_state=lambda: cleared.__setitem__("done", True),
            )
            is False
        )
        assert cleared["done"] is False

        assert (
            await apply_restore_success_gate(
                write=_ok,
                clear_state=lambda: cleared.__setitem__("done", True),
            )
            is True
        )
        assert cleared["done"] is True

    asyncio.get_event_loop().run_until_complete(_run())


def test_brand_capabilities_tesla_self_heal_and_custom_monitoring():
    tesla = get_brand_capabilities("tesla")
    assert tesla.supports_self_heal is True
    assert tesla.supports_offgrid_overlay is True
    custom = get_brand_capabilities("custom")
    assert custom.monitoring_only is True
    assert custom.supports_force_charge is False
