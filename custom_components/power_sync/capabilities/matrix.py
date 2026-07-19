"""Static capability matrix per battery system.

Replaces scattered ``hasattr`` / brand if-ladders in the optimizer executor.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BrandCapabilities:
    """What a battery brand can do on the control/optimizer path."""

    battery_system: str
    supports_force_charge: bool = True
    supports_force_discharge: bool = True
    supports_backup_reserve_write: bool = True
    supports_self_consumption_mode: bool = True
    supports_no_discharge_mode: bool = False
    supports_target_charge_power: bool = False
    supports_target_export_power: bool = False
    supports_idle_hold_reserve: bool = True
    supports_self_heal: bool = False
    supports_offgrid_overlay: bool = False
    monitoring_only: bool = False


_MATRIX: dict[str, BrandCapabilities] = {
    "tesla": BrandCapabilities(
        battery_system="tesla",
        supports_no_discharge_mode=True,
        supports_target_charge_power=False,
        supports_target_export_power=False,
        supports_self_heal=True,
        supports_offgrid_overlay=True,
    ),
    "sungrow": BrandCapabilities(
        battery_system="sungrow",
        supports_target_charge_power=True,
        supports_target_export_power=True,
        supports_self_heal=True,
    ),
    "goodwe": BrandCapabilities(
        battery_system="goodwe",
        supports_backup_reserve_write=True,
        supports_self_heal=True,
        supports_idle_hold_reserve=False,
    ),
    "foxess": BrandCapabilities(
        battery_system="foxess",
        supports_target_charge_power=True,
        supports_target_export_power=True,
    ),
    "sigenergy": BrandCapabilities(
        battery_system="sigenergy",
        supports_target_charge_power=True,
        supports_target_export_power=True,
        supports_backup_reserve_write=True,
    ),
    "alphaess": BrandCapabilities(
        battery_system="alphaess",
        supports_target_charge_power=True,
        supports_target_export_power=True,
    ),
    "solax": BrandCapabilities(
        battery_system="solax",
        supports_target_charge_power=True,
    ),
    "saj_h2": BrandCapabilities(
        battery_system="saj_h2",
        supports_backup_reserve_write=False,
    ),
    "fronius_reserva": BrandCapabilities(
        battery_system="fronius_reserva",
    ),
    "neovolt": BrandCapabilities(
        battery_system="neovolt",
    ),
    "solaredge": BrandCapabilities(
        battery_system="solaredge",
        supports_target_export_power=True,
    ),
    "anker_solix": BrandCapabilities(
        battery_system="anker_solix",
    ),
    "esy_sunhome": BrandCapabilities(
        battery_system="esy_sunhome",
        supports_backup_reserve_write=False,
    ),
    "custom": BrandCapabilities(
        battery_system="custom",
        supports_force_charge=False,
        supports_force_discharge=False,
        supports_backup_reserve_write=False,
        supports_self_consumption_mode=False,
        monitoring_only=True,
    ),
}


def get_brand_capabilities(battery_system: str | None) -> BrandCapabilities:
    """Return capabilities for a battery system key (case-insensitive)."""
    key = (battery_system or "custom").strip().lower()
    # Normalize common aliases
    aliases = {
        "powerwall": "tesla",
        "tesla_powerwall": "tesla",
        "sungrow_sh": "sungrow",
        "fox": "foxess",
        "fronius": "fronius_reserva",
        "saj": "saj_h2",
        "anker": "anker_solix",
        "external": "custom",
        "custom_external": "custom",
    }
    key = aliases.get(key, key)
    return _MATRIX.get(key, BrandCapabilities(battery_system=key))
