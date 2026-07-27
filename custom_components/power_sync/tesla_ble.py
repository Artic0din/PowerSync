"""Shared Tesla BLE entity resolution helpers."""

from __future__ import annotations

from typing import Any

from .const import (
    TESLA_BLE_BINARY_CONNECTION_STATUS,
    TESLA_BLE_BINARY_STATUS,
)


def tesla_ble_status_entity_ids(prefix: str) -> tuple[str, str]:
    """Return supported bridge-status entities in compatibility order."""
    return (
        TESLA_BLE_BINARY_STATUS.format(prefix=prefix),
        TESLA_BLE_BINARY_CONNECTION_STATUS.format(prefix=prefix),
    )


def get_tesla_ble_status_state(hass: Any, prefix: str) -> Any | None:
    """Return the first available BLE bridge status state.

    Older/example ESPHome bridge YAML exposes a generic node ``Status`` entity,
    while the Tesla BLE component itself exposes ``BLE Status``. Prefer the
    existing node-status signal for compatibility, but do not hide a configured
    vehicle when only the component connection status is present.
    """
    for entity_id in tesla_ble_status_entity_ids(prefix):
        state = hass.states.get(entity_id)
        if state is not None:
            return state
    return None
