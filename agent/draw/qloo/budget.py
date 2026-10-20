"""Daily Qloo call counter: Redis key `draw:qloo:YYYY-MM-DD`, checked before every spawn.

All four harness surfaces share one 10,000/month key quota, so the cap is a hard stop:
`reserve()` raises `BudgetExceeded` and nothing is spawned. Cache hits are never counted.
Without `REDIS_URL` the counter is a no-op (local development, CI).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

import redis.asyncio as redis_asyncio

from draw.qloo.envelope import BudgetExceeded

KEY_PREFIX = "draw:qloo:"
KEY_TTL_S = 2 * 24 * 3600


def today_key(now: datetime | None = None) -> str:
    return KEY_PREFIX + (now or datetime.now(UTC)).strftime("%Y-%m-%d")


class DailyCounter(Protocol):
    async def reserve(self) -> int:
        """Count one call; raise `BudgetExceeded` if that would pass the cap."""
        ...

    async def used(self) -> int: ...


class NullCounter:
    """No Redis configured: never blocks, never counts."""

    async def reserve(self) -> int:
        return 0

    async def used(self) -> int:
        return 0


class MemoryCounter:
    """In-process counter with the Redis semantics, for tests and single-process runs."""

    def __init__(self, cap: int) -> None:
        self.cap = cap
        self._counts: dict[str, int] = {}

    async def reserve(self) -> int:
        key = today_key()
        value = self._counts.get(key, 0) + 1
        if value > self.cap:
            raise BudgetExceeded(value - 1, self.cap)
        self._counts[key] = value
        return value

    async def used(self) -> int:
        return self._counts.get(today_key(), 0)


class RedisCounter:
    def __init__(self, url: str, cap: int) -> None:
        self.cap = cap
        self._redis = redis_asyncio.from_url(url, decode_responses=True)

    async def reserve(self) -> int:
        key = today_key()
        value = int(await self._redis.incr(key))
        if value == 1:
            await self._redis.expire(key, KEY_TTL_S)
        if value > self.cap:
            # Give the slot back so /limits shows real spend, not refused attempts.
            await self._redis.decr(key)
            raise BudgetExceeded(value - 1, self.cap)
        return value

    async def used(self) -> int:
        raw = await self._redis.get(today_key())
        return int(raw) if raw else 0

    async def aclose(self) -> None:
        await self._redis.aclose()
