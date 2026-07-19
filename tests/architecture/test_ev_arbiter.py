"""Contract tests for LoadpointArbiter ownership rules."""

from __future__ import annotations

import asyncio

from custom_components.power_sync.ev import (
    EVLoadpointState,
    LoadpointArbiter,
    LoadpointCommand,
)


def test_arbiter_does_not_stop_unowned_external_session():
    arbiter = LoadpointArbiter(stop_external_sessions=False)
    arbiter.upsert_state(
        EVLoadpointState(
            loadpoint_id="lp1",
            connected=True,
            actual_charging=True,
            owner=None,
        )
    )
    winner = arbiter.select_command(
        "lp1",
        [LoadpointCommand(action="stop", mode="solar_surplus", priority=10)],
    )
    assert winner.action == "noop"
    assert winner.reason == "unowned_external_session"


def test_arbiter_runs_one_winning_command_per_cycle():
    calls: list[tuple[str, dict]] = []

    async def handler(action: str, payload: dict) -> bool:
        calls.append((action, payload))
        return True

    arbiter = LoadpointArbiter(command_handler=handler)
    arbiter.upsert_state(EVLoadpointState(loadpoint_id="lp1", connected=True))

    async def _run():
        return await arbiter.run_cycle(
            "lp1",
            [
                LoadpointCommand(action="start", mode="price_level", priority=1, amps=8),
                LoadpointCommand(action="start", mode="smart_schedule", priority=5, amps=16),
            ],
        )

    winner = asyncio.get_event_loop().run_until_complete(_run())
    assert winner.mode == "smart_schedule"
    assert len(calls) == 1
    assert calls[0][0] == "start"
    assert calls[0][1]["amps"] == 16
    state = arbiter.get_state("lp1")
    assert state is not None
    assert state.owner_mode == "smart_schedule"
