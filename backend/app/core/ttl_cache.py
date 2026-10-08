"""Tiny in-process TTL cache for read endpoints whose answer is expensive to
compute on a large table (dashboard totals, filter choice lists) and fine to
serve a minute or two stale. Per worker process; no cross-process coordination."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any


class TTLCache:
    def __init__(self, ttl_seconds: float, max_entries: int = 512) -> None:
        self._ttl = ttl_seconds
        self._max = max_entries
        self._data: dict[Any, tuple[float, Any]] = {}

    async def get_or_compute(self, key: Any, compute: Callable[[], Awaitable[Any]]) -> Any:
        now = time.monotonic()
        hit = self._data.get(key)
        if hit is not None and hit[0] > now:
            return hit[1]
        value = await compute()
        if len(self._data) >= self._max:
            self._data = {k: v for k, v in self._data.items() if v[0] > now}
            if len(self._data) >= self._max:
                self._data.clear()
        self._data[key] = (now + self._ttl, value)
        return value

    def clear(self) -> None:
        self._data.clear()
