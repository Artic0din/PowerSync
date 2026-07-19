"""BatteryPort Protocol and service-backed implementation.

Normalized units at the port boundary:
- power: watts
- duration: minutes
- reserve: percent 0–100
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


@runtime_checkable
class BatteryPort(Protocol):
    """Unified battery control contract used by optimizer, services, and EV."""

    async def force_charge(
        self,
        power_w: float = 5000,
        duration_minutes: int = 60,
        *,
        source: str = "user",
        extend_hardware: bool = False,
    ) -> bool: ...

    async def force_discharge(
        self,
        power_w: float = 5000,
        duration_minutes: int = 60,
        *,
        source: str = "user",
        extend_hardware: bool = False,
        tariff_duration: int | None = None,
    ) -> bool: ...

    async def restore_normal(
        self,
        *,
        source: str = "user",
        allow_monitoring_restore: bool = False,
    ) -> bool: ...

    async def set_backup_reserve(self, percent: int) -> bool: ...

    async def set_self_consumption_mode(self) -> bool: ...

    async def read_backup_reserve(self) -> Any: ...


class ServiceBatteryPort:
    """BatteryPort that delegates to the legacy power_sync HA services.

    Used during the strangler transition while brand handlers still live in
    ``async_setup_entry``. Optimizer and wrappers should depend on BatteryPort,
    not call ``hass.services`` directly.
    """

    def __init__(self, hass: HomeAssistant, battery_system: str = "") -> None:
        self.hass = hass
        self.battery_system = battery_system

    async def force_charge(
        self,
        power_w: float = 5000,
        duration_minutes: int = 60,
        *,
        source: str = "user",
        extend_hardware: bool = False,
    ) -> bool:
        try:
            service_data: dict[str, Any] = {
                "duration": duration_minutes,
                "power_w": power_w,
                "source": source,
            }
            if extend_hardware:
                service_data["_extend_hardware"] = True
            await self.hass.services.async_call(
                "power_sync",
                "force_charge",
                service_data,
                blocking=True,
            )
            return True
        except Exception as err:  # noqa: BLE001 — port returns False on failure
            _LOGGER.error("BatteryPort.force_charge failed: %s", err, exc_info=True)
            return False

    async def force_discharge(
        self,
        power_w: float = 5000,
        duration_minutes: int = 60,
        *,
        source: str = "user",
        extend_hardware: bool = False,
        tariff_duration: int | None = None,
    ) -> bool:
        try:
            service_data: dict[str, Any] = {
                "duration": duration_minutes,
                "power_w": power_w,
                "source": source,
            }
            if extend_hardware:
                service_data["_extend_hardware"] = True
            if tariff_duration is not None:
                service_data["_tariff_duration"] = tariff_duration
            await self.hass.services.async_call(
                "power_sync",
                "force_discharge",
                service_data,
                blocking=True,
            )
            return True
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("BatteryPort.force_discharge failed: %s", err, exc_info=True)
            return False

    async def restore_normal(
        self,
        *,
        source: str = "user",
        allow_monitoring_restore: bool = False,
    ) -> bool:
        try:
            service_data: dict[str, Any] = {"source": source}
            if allow_monitoring_restore:
                service_data["_allow_monitoring_restore"] = True
            await self.hass.services.async_call(
                "power_sync",
                "restore_normal",
                service_data,
                blocking=True,
            )
            return True
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("BatteryPort.restore_normal failed: %s", err, exc_info=True)
            return False

    async def set_backup_reserve(self, percent: int) -> bool:
        try:
            pct = max(0, min(100, int(round(percent))))
            await self.hass.services.async_call(
                "power_sync",
                "set_backup_reserve",
                {"percent": pct, "source": "optimizer"},
                blocking=True,
            )
            return True
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("BatteryPort.set_backup_reserve failed: %s", err, exc_info=True)
            return False

    async def set_self_consumption_mode(self) -> bool:
        try:
            await self.hass.services.async_call(
                "power_sync",
                "set_self_consumption",
                {"source": "optimizer"},
                blocking=True,
            )
            return True
        except Exception as err:  # noqa: BLE001
            _LOGGER.error(
                "BatteryPort.set_self_consumption_mode failed: %s", err, exc_info=True
            )
            return False

    async def read_backup_reserve(self) -> Any:
        """Legacy service port has no read path; return None."""
        return None
