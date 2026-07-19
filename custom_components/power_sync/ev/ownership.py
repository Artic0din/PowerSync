"""EV ownership helpers — compatibility re-export toward ``ev/``.

Canonical implementation remains in ``automations.ev_ownership`` until the
strangler migration completes. Prefer importing from ``ev.ownership`` for new
arbiter / loadpoint code.
"""

from __future__ import annotations

from ..automations.ev_ownership import (
    DEFAULT_VEHICLE_ID,
    MANUAL_STOP_HOLD_SECONDS,
    can_claim_ev_ownership,
    can_take_over_ev_ownership,
    claim_ev_ownership,
    clear_ev_ownerships,
    get_active_ev_owner_mode,
    get_ev_last_commands,
    get_ev_ownership,
    get_ev_ownerships,
    is_solar_surplus_owner_mode,
    manual_stop_hold_reason,
    normalize_vehicle_id,
    owner_family,
    persist_ev_runtime_state,
    record_ev_command,
    record_manual_stop_hold,
    release_ev_ownership,
    restore_ev_runtime_state,
)

__all__ = [
    "DEFAULT_VEHICLE_ID",
    "MANUAL_STOP_HOLD_SECONDS",
    "can_claim_ev_ownership",
    "can_take_over_ev_ownership",
    "claim_ev_ownership",
    "clear_ev_ownerships",
    "get_active_ev_owner_mode",
    "get_ev_last_commands",
    "get_ev_ownership",
    "get_ev_ownerships",
    "is_solar_surplus_owner_mode",
    "manual_stop_hold_reason",
    "normalize_vehicle_id",
    "owner_family",
    "persist_ev_runtime_state",
    "record_ev_command",
    "record_manual_stop_hold",
    "release_ev_ownership",
    "restore_ev_runtime_state",
]
