"""Daily counters in Redis: Qloo calls (`draw:qloo:YYYY-MM-DD`) and LLM tokens (`draw:llm:...`).

All four harness surfaces share one 10,000/month key quota, so the Qloo cap is a hard stop:
`reserve()` raises `BudgetExceeded` and nothing is spawned. Cache hits are never counted.
The agent (T13) uses the same counters with `prefix=LLM_PREFIX` and `amount=` the tokens a
model call used; there the spend has already happened, so `charge_over=True` keeps it on
the counter and raises afterwards. Without `REDIS_URL` the counter is a no-op (local
development, CI).

`on_spend` is called with the counter's new value after every count, including the one that
crosses the cap with `charge_over`; the budget guard (`draw.guard`, T32) attaches its 70%
alert there so every spend path reports, not only runs. `clock` is the time source the
date keys come from; tests pass their own to roll the day over.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Protocol

import redis.asyncio as redis_asyncio

from draw.qloo.envelope import BudgetExceeded

KEY_PREFIX = "draw:qloo:"
LLM_PREFIX = "draw:llm:"
KEY_TTL_S = 2 * 24 * 3600

Clock = Callable[[], datetime]
SpendHook = Callable[[int], Awaitable[None]]


def utc_now() -> datetime:
    return datetime.now(UTC)


def today_key(now: datetime | None = None, prefix: str = KEY_PREFIX) -> str:
    return prefix + (now or utc_now()).strftime("%Y-%m-%d")


class DailyCounter(Protocol):
    on_spend: SpendHook | None

    async def reserve(self, amount: int = 1) -> int:
        """Count `amount`; raise `BudgetExceeded` if that would pass the cap."""
        ...

    async def used(self) -> int: ...


class NullCounter:
    """No Redis configured: never blocks, never counts."""

    on_spend: SpendHook | None = None

    async def reserve(self, amount: int = 1) -> int:
        return 0

    async def used(self) -> int:
        return 0


class MemoryCounter:
    """In-process counter with the Redis semantics, for tests and single-process runs."""

    def __init__(
        self,
        cap: int,
        *,
        prefix: str = KEY_PREFIX,
        what: str = "Qloo",
        charge_over: bool = False,
        clock: Clock = utc_now,
    ) -> None:
        self.cap = cap
        self.prefix = prefix
        self.what = what
        self.charge_over = charge_over
        self.clock = clock
        self.on_spend: SpendHook | None = None
        self._counts: dict[str, int] = {}

    def _key(self) -> str:
        return today_key(self.clock(), prefix=self.prefix)

    async def reserve(self, amount: int = 1) -> int:
        key = self._key()
        before = self._counts.get(key, 0)
        value = before + amount
        if value > self.cap:
            if self.charge_over:
                self._counts[key] = value
                await _spent(self.on_spend, value)
            raise BudgetExceeded(value if self.charge_over else before, self.cap, self.what)
        self._counts[key] = value
        await _spent(self.on_spend, value)
        return value

    async def used(self) -> int:
        return self._counts.get(self._key(), 0)


class RedisCounter:
    def __init__(
        self,
        url: str,
        cap: int,
        *,
        prefix: str = KEY_PREFIX,
        what: str = "Qloo",
        charge_over: bool = False,
        clock: Clock = utc_now,
        client: redis_asyncio.Redis | None = None,
    ) -> None:
        self.cap = cap
        self.prefix = prefix
        self.what = what
        self.charge_over = charge_over
        self.clock = clock
        self.on_spend: SpendHook | None = None
        # `client` lets tests pass a fake; the service always builds one from `url`.
        self._redis = client or redis_asyncio.from_url(url, decode_responses=True)

    def _key(self) -> str:
        return today_key(self.clock(), prefix=self.prefix)

    async def reserve(self, amount: int = 1) -> int:
        key = self._key()
        value = int(await self._redis.incrby(key, amount))
        if value == amount:
            await self._redis.expire(key, KEY_TTL_S)
        if value > self.cap:
            if self.charge_over:
                await _spent(self.on_spend, value)
                raise BudgetExceeded(value, self.cap, self.what)
            # Give the slot back so /limits shows real spend, not refused attempts.
            await self._redis.decrby(key, amount)
            raise BudgetExceeded(value - amount, self.cap, self.what)
        await _spent(self.on_spend, value)
        return value

    async def used(self) -> int:
        raw = await self._redis.get(self._key())
        return int(raw) if raw else 0

    async def aclose(self) -> None:
        await self._redis.aclose()


async def _spent(hook: SpendHook | None, value: int) -> None:
    if hook is not None:
        await hook(value)
