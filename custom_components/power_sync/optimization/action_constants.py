"""Shared action-set constants for optimizer coordinator mixins."""

from __future__ import annotations

from datetime import timedelta

EXPORT_ACTIONS = {"discharge", "export"}
SELF_USE_ACTIONS = {"consume", "self_consumption"}
CHARGE_ACTIONS = {"charge"}
FORCED_ACTIONS = CHARGE_ACTIONS | EXPORT_ACTIONS
SUNGROW_INFERRED_RESTORE_COOLDOWN = timedelta(minutes=5)
