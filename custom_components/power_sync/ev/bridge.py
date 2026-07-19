"""Bridge helpers wiring LoadpointArbiter into entry runtime / EV modes."""

from __future__ import annotations

import logging
from typing import Any

from .arbiter import LoadpointArbiter, LoadpointCommand

_LOGGER = logging.getLogger(__name__)


def attach_arbiter_to_entry(
    hass: Any,
    entry_id: str,
    arbiter: LoadpointArbiter,
) -> LoadpointArbiter:
    """Store ``arbiter`` on the entry bag and EntryRuntime when present.

    Idempotent: overwrites any previous ``loadpoint_arbiter`` reference so the
    EV charging coordinator and optimizer share one arbiter instance.
    """
    from ..const import DOMAIN
    from ..runtime import get_entry_runtime

    domain_data = hass.data.setdefault(DOMAIN, {})
    bag = domain_data.get(entry_id)
    if isinstance(bag, dict):
        bag["loadpoint_arbiter"] = arbiter

    runtime = get_entry_runtime(hass, DOMAIN, entry_id)
    if runtime is not None:
        runtime["loadpoint_arbiter"] = arbiter
    else:
        _LOGGER.debug(
            "attach_arbiter_to_entry: no EntryRuntime for %s; bag-only write",
            entry_id,
        )
    return arbiter


def propose_from_mode(mode: str, **kwargs: Any) -> LoadpointCommand:
    """Build a LoadpointCommand proposal from an EV mode name and kwargs.

    Accepted kwargs: ``action`` (default ``noop``), ``amps``, ``reason``,
    ``priority`` (default ``0``).
    """
    action = str(kwargs.get("action") or "noop")
    amps = kwargs.get("amps")
    reason = kwargs.get("reason")
    priority = int(kwargs.get("priority") or 0)
    amps_int: int | None
    try:
        amps_int = int(amps) if amps is not None else None
    except (TypeError, ValueError):
        amps_int = None
    return LoadpointCommand(
        action=action,
        mode=str(mode),
        amps=amps_int,
        reason=str(reason) if reason is not None else None,
        priority=priority,
    )
