"""Shared user-facing settings metadata for PowerSync clients."""

from __future__ import annotations

from typing import Any


def split_optimizer_reserve_values(
    *,
    auto_apply_enabled: bool,
    configured_reserve: float,
    manual_reserve: float | None,
) -> tuple[float, float | None]:
    """Return the editable manual reserve and read-only applied reserve."""
    displayed_reserve = (
        manual_reserve
        if auto_apply_enabled and manual_reserve is not None
        else configured_reserve
    )
    applied_reserve = configured_reserve if auto_apply_enabled else None
    return displayed_reserve, applied_reserve


def merge_optimization_section_input(
    live_values: dict[str, Any],
    visible_fields: set[str],
    submitted: dict[str, Any],
) -> dict[str, Any]:
    """Merge a section with current hidden values, never a rendered snapshot."""
    return {
        **{
            key: value
            for key, value in live_values.items()
            if key not in visible_fields
        },
        **submitted,
    }


def submitted_live_settings(
    settings: dict[str, Any],
    submitted_fields: set[str],
    form_field_by_setting: dict[str, str],
) -> dict[str, Any]:
    """Return only live optimizer settings owned by the submitted form section."""
    return {
        key: value
        for key, value in settings.items()
        if form_field_by_setting.get(key) in submitted_fields
    }


def optimizer_settings_schema() -> dict[str, Any]:
    """Return cross-client ownership metadata for PowerSync settings.

    ``category`` is retained for version-1 mobile clients. New clients should
    use ``owner`` and ``section`` so a field is rendered by the surface that
    owns it instead of treating every value used by the solver as an optimizer
    setting.

    Monitoring and Away use separate control endpoints and remain intentionally
    absent from this writable optimization/settings contract.
    """
    return {
        "version": 2,
        "fields": {
            "enabled": {
                "category": "core",
                "owner": "optimizer",
                "section": "overview",
                "order": 1,
            },
            "profit_max_enabled": {
                "category": "core",
                "owner": "optimizer",
                "section": "goals",
                "order": 10,
            },
            "charge_by_time_enabled": {
                "category": "core",
                "owner": "optimizer",
                "section": "goals",
                "order": 11,
            },
            "charge_by_time_target_soc": {
                "category": "core",
                "owner": "optimizer",
                "section": "goals",
                "order": 12,
            },
            "charge_by_time_target_time": {
                "category": "core",
                "owner": "optimizer",
                "section": "goals",
                "order": 13,
            },
            "backup_reserve": {
                "category": "core",
                "owner": "optimizer",
                "section": "reserve_strategy",
                "order": 20,
            },
            "auto_apply_reserve_enabled": {
                "category": "behaviour",
                "owner": "optimizer",
                "section": "reserve_strategy",
                "order": 21,
            },
            "ev_integration": {
                "category": "behaviour",
                "owner": "ev_charging",
                "section": "battery_planning",
                "order": 30,
                "capability": "ev_integration",
            },
            "allow_grid_charge": {
                "category": "behaviour",
                "owner": "optimizer",
                "section": "grid_charging",
                "order": 40,
                "capability": "grid_charge",
            },
            "hardware_backup_reserve": {
                "category": "system",
                "owner": "controls",
                "section": "battery",
                "order": 50,
            },
            "battery_capacity_wh": {
                "category": "system",
                "owner": "battery",
                "section": "specifications",
                "order": 60,
            },
            "max_charge_w": {
                "category": "system",
                "owner": "battery",
                "section": "specifications",
                "order": 61,
            },
            "max_discharge_w": {
                "category": "system",
                "owner": "battery",
                "section": "specifications",
                "order": 62,
            },
            "max_grid_import_w": {
                "category": "system",
                "owner": "site",
                "section": "limits",
                "order": 70,
            },
            "max_grid_export_w": {
                "category": "system",
                "owner": "site",
                "section": "limits",
                "order": 71,
            },
            "max_grid_charge_price": {
                "category": "advanced",
                "owner": "optimizer",
                "section": "grid_charging",
                "order": 41,
                "capability": "grid_charge",
            },
            "grid_charge_soc_cap": {
                "category": "advanced",
                "owner": "optimizer",
                "section": "grid_charging",
                "order": 42,
                "capability": "grid_charge",
            },
            "spread_import_enabled": {
                "category": "advanced",
                "owner": "optimizer",
                "section": "dispatch",
                "order": 80,
                "visible_if": {"battery_system_not": "tesla"},
            },
            "spread_export_enabled": {
                "category": "advanced",
                "owner": "optimizer",
                "section": "dispatch",
                "order": 81,
                "visible_if": {"battery_system_not": "tesla"},
            },
            "disable_idle_enabled": {
                "category": "advanced",
                "owner": "optimizer",
                "section": "dispatch",
                "order": 82,
                "capability": "disable_idle",
            },
        },
    }


def optimizer_settings_groups() -> dict[str, Any]:
    """Return the unchanged legacy grouping for existing mobile clients."""
    return {
        "optimizer": {
            "title": "Smart Optimization",
            "collapsed": False,
            "fields": [
                "enabled",
                "backup_reserve",
                "hardware_backup_reserve",
                "profit_max_enabled",
                "charge_by_time_enabled",
                "charge_by_time_target_time",
                "charge_by_time_target_soc",
                "load_entity",
                "planned_ev_load_entity",
                "battery_capacity_wh",
                "max_charge_w",
                "max_discharge_w",
            ],
        },
        "advanced_optimizer": {
            "title": "Advanced optimizer controls",
            "collapsed": True,
            "fields": [
                "allow_grid_charge",
                "max_grid_charge_price",
                "grid_charge_soc_cap",
                "max_grid_import_w",
                "max_grid_export_w",
                "spread_import_enabled",
                "spread_export_enabled",
                "disable_idle_enabled",
                "auto_apply_reserve_enabled",
            ],
        },
    }
