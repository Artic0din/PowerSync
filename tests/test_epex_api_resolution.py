"""Regression coverage for EPEX native Belgian quarter-hour requests."""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
EPEX_API_PATH = ROOT / "custom_components" / "power_sync" / "epex_api.py"


def _get_prices_source() -> str:
    source = EPEX_API_PATH.read_text()
    module = ast.parse(source)
    for node in module.body:
        if isinstance(node, ast.ClassDef) and node.name == "EPEXAPIClient":
            for item in node.body:
                if isinstance(item, ast.AsyncFunctionDef) and item.name == "get_prices":
                    segment = ast.get_source_segment(source, item)
                    assert segment is not None
                    return segment
    raise AssertionError("EPEXAPIClient.get_prices not found")


class _Response:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self) -> dict[str, Any]:
        return {"prices": [{"startsAt": "2026-07-30T14:00:00+02:00", "total": 8.2}]}


class _Session:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, url, *, params, timeout):
        self.calls.append((url, dict(params)))
        return _Response()


def _request_params(region: str) -> dict[str, Any]:
    namespace: dict[str, Any] = {
        "aiohttp": SimpleNamespace(
            ClientTimeout=lambda **kwargs: kwargs,
            ClientError=Exception,
        ),
        "EPEX_API_BASE_URL": "https://example.test",
        "_LOGGER": SimpleNamespace(
            debug=lambda *args, **kwargs: None,
            error=lambda *args, **kwargs: None,
        ),
    }
    exec(_get_prices_source(), namespace)
    session = _Session()
    client = SimpleNamespace(_session=session)

    asyncio.run(namespace["get_prices"](client, region=region))

    return session.calls[0][1]


def test_belgium_requests_native_quarter_hour_prices():
    assert _request_params("BE")["hourly"] == "false"


def test_other_epex_regions_keep_hourly_aggregation():
    assert _request_params("DE")["hourly"] == "true"
