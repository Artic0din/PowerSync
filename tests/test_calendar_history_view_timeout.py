"""Regression coverage for slow mobile calendar-history requests."""

from __future__ import annotations

import ast
import asyncio
import logging
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable


INIT_PATH = (
    Path(__file__).resolve().parent.parent
    / "custom_components"
    / "power_sync"
    / "__init__.py"
)


class _Response:
    def __init__(self, body: dict[str, Any], status: int = 200):
        self.body = body
        self.status = status


class _Request:
    def __init__(self, query: dict[str, str]):
        self.query = query


def _load_calendar_view(history_builder: Callable[..., Any]):
    tree = ast.parse(INIT_PATH.read_text())
    class_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "CalendarHistoryView"
    )
    module = ast.fix_missing_locations(
        ast.Module(body=[class_node], type_ignores=[])
    )
    web = SimpleNamespace(
        Request=object,
        Response=object,
        json_response=lambda body, status=200: _Response(body, status),
    )
    summary_coordinator = object()
    namespace = {
        "Any": Any,
        "Callable": Callable,
        "DOMAIN": "power_sync",
        "HomeAssistant": object,
        "HomeAssistantView": object,
        "TimeoutError": TimeoutError,
        "_LOGGER": logging.getLogger(__name__),
        "_calculate_cost_from_statistics": None,
        "_calculate_cost_from_tariff": None,
        "_calendar_result_from_energy_summary": history_builder,
        "_find_calendar_energy_summary_source": lambda hass: (
            "Sigenergy",
            summary_coordinator,
            "entry-1",
        ),
        "_find_calendar_tariff_schedule": lambda hass: None,
        "asyncio": asyncio,
        "time": time,
        "web": web,
    }
    exec(compile(module, str(INIT_PATH), "exec"), namespace)
    return namespace["CalendarHistoryView"]


class _Hass:
    def __init__(self):
        self.data = {"power_sync": {"entry-1": {}}}

    def async_create_task(self, coroutine, *, name: str):
        return asyncio.create_task(coroutine, name=name)


def test_slow_non_tesla_history_survives_timeout_and_is_cached():
    async def scenario():
        release = asyncio.Event()
        build_count = 0

        async def build_history(*_args):
            nonlocal build_count
            build_count += 1
            await release.wait()
            return {
                "success": True,
                "period": "month",
                "time_series": [
                    {
                        "timestamp": "2026-07-01T00:00:00+10:00",
                        "solar_generation": 12_000,
                    }
                ],
            }

        view_class = _load_calendar_view(build_history)
        view = view_class(_Hass())
        view._REQUEST_TIMEOUT_SECONDS = 0.01
        request = _Request({"period": "month"})

        loading_response = await view.get(request)

        assert loading_response.status == 200
        assert loading_response.body == {
            "success": False,
            "error": "Calendar history is still loading, please retry",
            "reason": "loading",
            "refresh_pending": True,
        }
        assert build_count == 1
        assert ("summary:entry-1", "month", "") in view._inflight

        release.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert not view._inflight
        assert ("summary:entry-1", "month", "") in view._cache

        completed_response = await view.get(request)

        assert completed_response.status == 200
        assert completed_response.body["success"] is True
        assert completed_response.body["cached"] is True
        assert len(completed_response.body["time_series"]) == 1
        assert build_count == 1
        assert not view._inflight

        cached_response = await view.get(request)

        assert cached_response.status == 200
        assert cached_response.body["success"] is True
        assert cached_response.body["cached"] is True
        assert build_count == 1

    asyncio.run(scenario())
