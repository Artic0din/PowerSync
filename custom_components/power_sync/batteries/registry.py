"""Factory for BatteryPort implementations by battery system key."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from ..control.battery_port import BatteryPort, ServiceBatteryPort


def get_battery_port(
    hass: HomeAssistant,
    battery_system: str,
    *,
    brand_controller: Any | None = None,
) -> BatteryPort:
    """Return a BatteryPort for the given battery system.

    When a brand_controller duck-types the normalized methods, wrap it;
    otherwise fall back to the HA service port (strangler default).
    """
    if brand_controller is not None and callable(
        getattr(brand_controller, "force_charge", None)
    ):
        return BrandControllerBatteryPort(brand_controller, battery_system)
    return ServiceBatteryPort(hass, battery_system)


class BrandControllerBatteryPort:
    """Adapt a duck-typed brand controller onto BatteryPort units (W / min / %)."""

    def __init__(self, controller: Any, battery_system: str) -> None:
        self._controller = controller
        self.battery_system = battery_system

    async def force_charge(
        self,
        power_w: float = 5000,
        duration_minutes: int = 60,
        *,
        source: str = "user",
        extend_hardware: bool = False,
    ) -> bool:
        fn = self._controller.force_charge
        return bool(
            await _call_flexible(
                fn,
                power_w=power_w,
                duration_minutes=duration_minutes,
                source=source,
                extend_hardware=extend_hardware,
            )
        )

    async def force_discharge(
        self,
        power_w: float = 5000,
        duration_minutes: int = 60,
        *,
        source: str = "user",
        extend_hardware: bool = False,
        tariff_duration: int | None = None,
    ) -> bool:
        fn = self._controller.force_discharge
        return bool(
            await _call_flexible(
                fn,
                power_w=power_w,
                duration_minutes=duration_minutes,
                source=source,
                extend_hardware=extend_hardware,
                tariff_duration=tariff_duration,
            )
        )

    async def restore_normal(
        self,
        *,
        source: str = "user",
        allow_monitoring_restore: bool = False,
    ) -> bool:
        fn = getattr(self._controller, "restore_normal", None)
        if fn is None:
            return False
        try:
            result = await fn()
            return bool(result) if result is not None else True
        except TypeError:
            result = await fn(source=source)
            return bool(result) if result is not None else True

    async def set_backup_reserve(self, percent: int) -> bool:
        fn = getattr(self._controller, "set_backup_reserve", None)
        if fn is None:
            return False
        pct = max(0, min(100, int(round(percent))))
        result = await fn(pct)
        return bool(result) if result is not None else True

    async def set_self_consumption_mode(self) -> bool:
        fn = getattr(self._controller, "set_self_consumption_mode", None) or getattr(
            self._controller, "set_self_consumption", None
        )
        if fn is None:
            return False
        result = await fn()
        return bool(result) if result is not None else True

    async def read_backup_reserve(self) -> Any:
        fn = getattr(self._controller, "read_backup_reserve", None)
        if fn is None:
            return None
        return await fn()


async def _call_flexible(fn: Any, **kwargs: Any) -> Any:
    """Call brand methods that disagree on parameter names/units."""
    power_w = float(kwargs.get("power_w") or 0)
    duration = int(kwargs.get("duration_minutes") or 60)
    # Try normalized signature first, then common brand variants.
    attempts = (
        lambda: fn(
            duration_minutes=duration,
            power_w=power_w,
        ),
        lambda: fn(power_w=power_w, duration_minutes=duration),
        lambda: fn(power_w),
        lambda: fn(power_w / 1000.0),  # kW brands (Sigenergy/AlphaESS)
        lambda: fn(duration, power_w),
        lambda: fn(),
    )
    last_err: Exception | None = None
    for attempt in attempts:
        try:
            result = await attempt()
            return result if result is not None else True
        except TypeError as err:
            last_err = err
            continue
    if last_err:
        raise last_err
    return False
