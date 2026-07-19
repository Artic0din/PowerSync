"""Restore-contract helpers shared across brand adapters.

Codifies the OB-registry invariants:
- clear force/idle flags only after a successful hardware write
- monitoring-mode symmetry (set and restore gated the same way)
- persist-or-startup-heal for reserve targets
- respect ``_restore_superseded`` when a newer command landed during await
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable


@dataclass(frozen=True)
class RestoreContract:
    """Policy knobs for a restore_normal / idle-exit path."""

    clear_flags_only_after_success: bool = True
    honor_monitoring_gate: bool = True
    honor_superseded: bool = True
    persist_reserve_target: bool = True


async def apply_restore_success_gate(
    *,
    write: Callable[[], Awaitable[bool]],
    clear_state: Callable[[], None],
    is_superseded: Callable[[], bool] | None = None,
    monitoring_blocked: bool = False,
    contract: RestoreContract | None = None,
) -> bool:
    """Run a restore write and clear local flags only on success.

    Returns True when the hardware write reported success and state was cleared.
    """
    policy = contract or RestoreContract()

    if policy.honor_monitoring_gate and monitoring_blocked:
        return False

    if policy.honor_superseded and is_superseded is not None and is_superseded():
        return False

    ok = await write()
    if not ok:
        return False

    if policy.honor_superseded and is_superseded is not None and is_superseded():
        # A newer command won the race; do not clear ownership flags.
        return False

    if policy.clear_flags_only_after_success:
        clear_state()
    return True


def monitoring_blocks_control(entry_data: dict[str, Any], *, allow_restore: bool = False) -> bool:
    """Return True when monitoring mode should block a control write."""
    if not isinstance(entry_data, dict):
        return False
    monitoring = bool(entry_data.get("monitoring_mode") or entry_data.get("is_monitoring"))
    if not monitoring:
        return False
    if allow_restore and entry_data.get("_allow_monitoring_restore"):
        return False
    return True
