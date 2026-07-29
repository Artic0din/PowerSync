"""Regression tests for the Teslemetry Energy Site SSE transport."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = (
    ROOT
    / "custom_components"
    / "power_sync"
    / "teslemetry_sse.py"
)
SPEC = importlib.util.spec_from_file_location(
    "power_sync_teslemetry_sse_test",
    MODULE_PATH,
)
assert SPEC and SPEC.loader
teslemetry_sse = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(teslemetry_sse)


class _AsyncLines:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = iter(lines)

    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        try:
            return next(self._lines)
        except StopIteration as err:
            raise StopAsyncIteration from err


class _FakeResponse:
    def __init__(
        self,
        status: int,
        lines: list[bytes] | None = None,
    ) -> None:
        self.status = status
        self.content = _AsyncLines(lines or [])
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def close(self) -> None:
        self.closed = True


class _FakeSession:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append({"url": url, **kwargs})
        return self._responses.pop(0)


class _BlockingLines:
    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        await asyncio.Event().wait()
        raise StopAsyncIteration


def test_iter_sse_json_handles_keepalives_multiline_data_and_bad_frames():
    async def _collect():
        content = _AsyncLines(
            [
                b": keep-alive\n",
                b"\n",
                b"data: not-json\n",
                b"\n",
                b'data: {"site_id":"123",\n',
                b'data: "live_status":{"solar_power":2400}}\n',
                b"\n",
            ]
        )
        return [event async for event in teslemetry_sse.iter_sse_json(content)]

    assert asyncio.run(_collect()) == [
        {
            "site_id": "123",
            "live_status": {"solar_power": 2400},
        }
    ]


def test_client_requests_only_live_status_and_forwards_full_snapshot():
    event = (
        b'data: {"createdAt":"2026-07-29T10:16:00.000Z",'
        b'"site_id":"123","isCache":true,'
        b'"live_status":{"battery_power":1100}}\n\n'
    )
    session = _FakeSession([_FakeResponse(200, [event])])
    received: list[dict[str, Any]] = []
    connection_states: list[bool] = []

    async def _run() -> None:
        async def _token() -> str:
            return "test-token"

        async def _event_callback(payload: dict[str, Any]) -> None:
            received.append(payload)
            client._active = False

        client = teslemetry_sse.TeslemetryEnergySSEClient(
            session,
            base_url="https://api.teslemetry.com",
            site_id="123",
            token_getter=_token,
            event_callback=_event_callback,
            connection_callback=connection_states.append,
            user_agent="PowerSync/test",
        )
        client._active = True
        await client._async_listen_once()

    asyncio.run(_run())

    assert received == [
        {
            "createdAt": "2026-07-29T10:16:00.000Z",
            "site_id": "123",
            "isCache": True,
            "live_status": {"battery_power": 1100},
        }
    ]
    assert session.calls[0]["url"] == "https://api.teslemetry.com/sse/123"
    assert session.calls[0]["params"] == {"topics": "live_status"}
    assert session.calls[0]["headers"]["Authorization"] == "Bearer test-token"
    assert connection_states == [True, False]


def test_http_400_retries_without_topics_for_staged_server_rollout():
    event = (
        b'data: {"createdAt":"2026-07-29T10:16:00.000Z",'
        b'"site_id":"123","live_status":{"grid_power":300}}\n\n'
    )
    session = _FakeSession(
        [
            _FakeResponse(400),
            _FakeResponse(200, [event]),
        ]
    )
    received: list[dict[str, Any]] = []

    async def _run() -> None:
        async def _token() -> str:
            return "test-token"

        async def _event_callback(payload: dict[str, Any]) -> None:
            received.append(payload)
            client._active = False

        client = teslemetry_sse.TeslemetryEnergySSEClient(
            session,
            base_url="https://api.teslemetry.com",
            site_id="123",
            token_getter=_token,
            event_callback=_event_callback,
            connection_callback=lambda connected: None,
            user_agent="PowerSync/test",
        )
        client._active = True
        await client._async_run()

    asyncio.run(_run())

    assert len(received) == 1
    assert session.calls[0]["params"] == {"topics": "live_status"}
    assert session.calls[1]["params"] is None


def test_async_stop_closes_response_and_cancels_reconnect_task():
    response = _FakeResponse(200)
    response.content = _BlockingLines()
    session = _FakeSession([response])
    connection_states: list[bool] = []

    async def _run() -> None:
        connected = asyncio.Event()

        async def _token() -> str:
            return "test-token"

        async def _event_callback(payload: dict[str, Any]) -> None:
            raise AssertionError("The blocking response should carry no event")

        def _connection_callback(value: bool) -> None:
            connection_states.append(value)
            if value:
                connected.set()

        client = teslemetry_sse.TeslemetryEnergySSEClient(
            session,
            base_url="https://api.teslemetry.com",
            site_id="123",
            token_getter=_token,
            event_callback=_event_callback,
            connection_callback=_connection_callback,
            user_agent="PowerSync/test",
        )
        client.start(asyncio.create_task)
        await asyncio.wait_for(connected.wait(), timeout=1)
        await client.async_stop()
        assert client.connected is False

    asyncio.run(_run())

    assert response.closed is True
    assert connection_states == [True, False]
