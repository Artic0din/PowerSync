"""Identity-safe Tesla Fleet and ESPHome BLE bridge pairing."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from .const import (
    CONF_EV_PROVIDER,
    CONF_TESLA_BLE_ENTITY_PREFIX,
    CONF_TESLA_BLE_VEHICLE_MAPPING,
    DEFAULT_TESLA_BLE_ENTITY_PREFIX,
    EV_PROVIDER_BOTH,
)

_BLE_PREFIX_PATTERN = re.compile(r"^[a-z0-9_]+$")


class TeslaBleMappingError(ValueError):
    """Raised when a Tesla VIN-to-BLE mapping is malformed or ambiguous."""


def configured_ble_prefixes(config: Mapping[str, Any]) -> list[str]:
    """Return configured ESPHome BLE prefixes in user-defined display order."""
    raw = str(
        config.get(
            CONF_TESLA_BLE_ENTITY_PREFIX,
            DEFAULT_TESLA_BLE_ENTITY_PREFIX,
        )
        or ""
    )
    prefixes = [prefix.strip() for prefix in raw.split(",") if prefix.strip()]
    return prefixes or [DEFAULT_TESLA_BLE_ENTITY_PREFIX]


def parse_tesla_ble_vehicle_mapping(raw_mapping: Any) -> dict[str, str]:
    """Parse comma/newline-separated ``VIN=prefix`` associations."""
    if raw_mapping is None or not str(raw_mapping).strip():
        return {}

    mapping: dict[str, str] = {}
    assigned_prefixes: set[str] = set()
    for item in re.split(r"[\n,]+", str(raw_mapping)):
        item = item.strip()
        if not item:
            continue
        if item.count("=") != 1:
            raise TeslaBleMappingError("Each mapping must use VIN=prefix")
        vin, prefix = (part.strip() for part in item.split("=", 1))
        normalized_vin = vin.upper()
        if (
            len(normalized_vin) != 17
            or not normalized_vin.isalnum()
            or normalized_vin.isdigit()
        ):
            raise TeslaBleMappingError("Mappings require a valid 17-character VIN")
        if not _BLE_PREFIX_PATTERN.fullmatch(prefix):
            raise TeslaBleMappingError(
                "BLE prefixes may contain only lowercase letters, numbers, and underscores"
            )
        if normalized_vin in mapping:
            raise TeslaBleMappingError("Each VIN may be mapped only once")
        if prefix in assigned_prefixes:
            raise TeslaBleMappingError("Each BLE prefix may be mapped only once")
        mapping[normalized_vin] = prefix
        assigned_prefixes.add(prefix)
    return mapping


def vehicle_ble_prefix(
    config: Mapping[str, Any],
    vehicle_vin: str | None,
    fleet_vins: Sequence[str] = (),
) -> str | None:
    """Resolve one vehicle's BLE prefix without relying on discovery order."""
    if vehicle_vin and vehicle_vin.startswith("ble_"):
        return vehicle_vin[4:]

    prefixes = configured_ble_prefixes(config)
    if not (
        vehicle_vin
        and len(vehicle_vin) == 17
        and vehicle_vin.isalnum()
        and config.get(CONF_EV_PROVIDER) == EV_PROVIDER_BOTH
    ):
        return prefixes[0] if prefixes else None

    try:
        explicit_mapping = parse_tesla_ble_vehicle_mapping(
            config.get(CONF_TESLA_BLE_VEHICLE_MAPPING)
        )
    except TeslaBleMappingError:
        return None

    mapped_prefix = explicit_mapping.get(vehicle_vin.upper())
    if mapped_prefix is not None:
        return mapped_prefix if mapped_prefix in prefixes else None

    unique_fleet_vins = list(dict.fromkeys(vin.upper() for vin in fleet_vins if vin))
    if len(unique_fleet_vins) == 1 and len(prefixes) == 1:
        return prefixes[0] if vehicle_vin.upper() == unique_fleet_vins[0] else None
    return None


def ble_prefix_vehicle_pairs(
    config: Mapping[str, Any],
    fleet_vins: Sequence[str],
) -> dict[str, str]:
    """Return BLE-prefix-to-VIN pairs that are explicit or unambiguous."""
    pairs: dict[str, str] = {}
    for vin in dict.fromkeys(str(candidate) for candidate in fleet_vins if candidate):
        prefix = vehicle_ble_prefix(config, vin, fleet_vins)
        if prefix:
            pairs[prefix] = vin
    return pairs
