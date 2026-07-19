"""Single loadpoint arbiter: modes propose, arbiter issues one command/cycle."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .loadpoint import EVLoadpointState

_LOGGER = logging.getLogger(__name__)

CommandHandler = Callable[[str, dict[str, Any]], Awaitable[bool]]


@dataclass(frozen=True)
class LoadpointCommand:
    """A proposed or executed charger command."""

    action: str  # start | stop | set_amps | noop
    mode: str  # smart_schedule | solar_surplus | price_level | scheduled | manual | optimizer
    amps: int | None = None
    reason: str | None = None
    priority: int = 0  # higher wins within a cycle


class LoadpointArbiter:
    """Arbitrate EV mode proposals into at most one hardware command per cycle."""

    def __init__(
        self,
        *,
        command_handler: CommandHandler | None = None,
        stop_external_sessions: bool = False,
    ) -> None:
        self._command_handler = command_handler
        self.stop_external_sessions = stop_external_sessions
        self._loadpoints: dict[str, EVLoadpointState] = {}
        self._last_cycle_commands: dict[str, LoadpointCommand] = {}

    def upsert_state(self, state: EVLoadpointState) -> None:
        self._loadpoints[state.loadpoint_id] = state

    def get_state(self, loadpoint_id: str) -> EVLoadpointState | None:
        return self._loadpoints.get(loadpoint_id)

    def all_states(self) -> list[EVLoadpointState]:
        return list(self._loadpoints.values())

    def select_command(
        self,
        loadpoint_id: str,
        proposals: list[LoadpointCommand],
    ) -> LoadpointCommand:
        """Pick the winning proposal for one loadpoint this cycle."""
        state = self._loadpoints.get(loadpoint_id)
        if not proposals:
            return LoadpointCommand(action="noop", mode="none", reason="no_proposals")

        ranked = sorted(proposals, key=lambda p: p.priority, reverse=True)
        winner = ranked[0]

        # Never stop a session we do not own unless explicitly allowed.
        if winner.action == "stop" and state is not None:
            if state.owner is None and not self.stop_external_sessions:
                return LoadpointCommand(
                    action="noop",
                    mode=winner.mode,
                    reason="unowned_external_session",
                    priority=winner.priority,
                )
            if (
                state.owner is not None
                and state.owner_mode is not None
                and state.owner_mode != winner.mode
                and winner.mode != "manual"
            ):
                return LoadpointCommand(
                    action="noop",
                    mode=winner.mode,
                    reason=f"owned_by_{state.owner_mode}",
                    priority=winner.priority,
                )
        return winner

    async def run_cycle(
        self,
        loadpoint_id: str,
        proposals: list[LoadpointCommand],
    ) -> LoadpointCommand:
        """Select and optionally execute one command for the loadpoint."""
        winner = self.select_command(loadpoint_id, proposals)
        self._last_cycle_commands[loadpoint_id] = winner
        state = self._loadpoints.get(loadpoint_id)
        if state is not None:
            state.last_command = {
                "action": winner.action,
                "mode": winner.mode,
                "amps": winner.amps,
                "reason": winner.reason,
            }

        if winner.action == "noop" or self._command_handler is None:
            return winner

        ok = await self._command_handler(
            winner.action,
            {
                "loadpoint_id": loadpoint_id,
                "mode": winner.mode,
                "amps": winner.amps,
                "reason": winner.reason,
            },
        )
        if ok and state is not None:
            if winner.action == "start":
                state.owner = "powersync"
                state.owner_mode = winner.mode
                state.actual_charging = True
            elif winner.action == "stop":
                state.owner = None
                state.owner_mode = None
                state.actual_charging = False
            elif winner.action == "set_amps" and winner.amps is not None:
                state.target_amps = winner.amps
        elif not ok:
            _LOGGER.warning(
                "LoadpointArbiter command failed: %s on %s", winner.action, loadpoint_id
            )
        return winner
