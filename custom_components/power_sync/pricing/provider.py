"""PriceProvider Protocol and coordinator-backed adapter."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .amber_interval import AmberInterval, normalize_amber_intervals


@runtime_checkable
class PriceProvider(Protocol):
    """Fetch normalized Amber-format price intervals."""

    async def async_get_intervals(self) -> list[AmberInterval]: ...


class CoordinatorPriceProvider:
    """Adapt a legacy DataUpdateCoordinator that exposes Amber-like lists."""

    def __init__(self, coordinator: Any, data_key: str | None = None) -> None:
        self._coordinator = coordinator
        self._data_key = data_key

    async def async_get_intervals(self) -> list[AmberInterval]:
        data = getattr(self._coordinator, "data", None)
        if data is None:
            return []
        if self._data_key and isinstance(data, dict):
            intervals = data.get(self._data_key)
        elif isinstance(data, list):
            intervals = data
        elif isinstance(data, dict):
            intervals = (
                data.get("intervals")
                or data.get("forecast")
                or data.get("prices")
                or data.get("data")
            )
        else:
            intervals = None
        if isinstance(intervals, dict):
            # Some coordinators nest general/feedIn separately
            merged: list[Any] = []
            for key in ("general", "feedIn", "import", "export"):
                chunk = intervals.get(key)
                if isinstance(chunk, list):
                    merged.extend(chunk)
            intervals = merged
        return normalize_amber_intervals(intervals if isinstance(intervals, list) else None)
