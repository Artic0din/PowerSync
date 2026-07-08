"""Periodic Tesla Powerwall BMS health polling."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Awaitable, Callable

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval

_LOGGER = logging.getLogger(__name__)

POWERWALL_BMS_HEALTH_POLL_INTERVAL = timedelta(minutes=5)

BatteryHealthFetcher = Callable[[], Awaitable[dict[str, Any] | None]]
BatteryHealthSyncer = Callable[[dict[str, Any]], Awaitable[None]]


def async_start_powerwall_bms_health_polling(
    hass: HomeAssistant,
    entry_id: str,
    fetch: BatteryHealthFetcher,
    sync: BatteryHealthSyncer,
    *,
    interval: timedelta = POWERWALL_BMS_HEALTH_POLL_INTERVAL,
) -> Callable[[], None]:
    """Poll BMS health periodically and publish successful samples to sensors."""
    in_progress = False

    async def _poll(now=None) -> None:
        nonlocal in_progress
        if in_progress:
            _LOGGER.debug("Skipping overlapping Powerwall BMS health poll for %s", entry_id)
            return

        in_progress = True
        try:
            payload = await fetch()
            if payload:
                await sync(payload)
        except Exception as err:
            _LOGGER.debug("Powerwall BMS health poll failed for %s: %s", entry_id, err)
        finally:
            in_progress = False

    return async_track_time_interval(hass, _poll, interval)
