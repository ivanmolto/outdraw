"""T32: the budget guard over a fake Redis, a fake Telegram endpoint and an injected clock."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import cast

import httpx
import pytest
import redis.asyncio as redis_asyncio

from draw.guard import BudgetGuard, RedisLatch, TelegramAlerter, alerter_from_env
from draw.qloo.budget import LLM_PREFIX, MemoryCounter, RedisCounter
from draw.qloo.envelope import BudgetExceeded

QLOO_CAP = 10
LLM_CAP = 1000


class FakeRedis:
    """The few commands the counters and the latch use, with Redis semantics."""

    def __init__(self) -> None:
        self.data: dict[str, int | str] = {}
        self.ttl: dict[str, int] = {}

    async def incrby(self, key: str, amount: int) -> int:
        value = int(self.data.get(key, 0)) + amount
        self.data[key] = value
        return value

    async def decrby(self, key: str, amount: int) -> int:
        return await self.incrby(key, -amount)

    async def expire(self, key: str, seconds: int) -> bool:
        self.ttl[key] = seconds
        return True

    async def get(self, key: str) -> str | None:
        value = self.data.get(key)
        return None if value is None else str(value)

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self.data:
            return False
        self.data[key] = value
        if ex is not None:
            self.ttl[key] = ex
        return True

    async def aclose(self) -> None:
        return None


class Clock:
    """The time source every counter and the guard share, so a test can roll the day over."""

    def __init__(self) -> None:
        self.current = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.current


class FakeTelegram(httpx.AsyncBaseTransport):
    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, json={"ok": self.status == 200})

    def texts(self) -> list[str]:
        return [json.loads(r.content)["text"] for r in self.requests]


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def telegram() -> FakeTelegram:
    return FakeTelegram()


def _client(redis: FakeRedis) -> redis_asyncio.Redis:
    return cast(redis_asyncio.Redis, redis)


@pytest.fixture
def qloo(redis: FakeRedis, clock: Clock) -> RedisCounter:
    return RedisCounter("redis://unused", QLOO_CAP, clock=clock, client=_client(redis))


@pytest.fixture
def llm(redis: FakeRedis, clock: Clock) -> RedisCounter:
    return RedisCounter(
        "redis://unused",
        LLM_CAP,
        prefix=LLM_PREFIX,
        what="LLM token",
        charge_over=True,
        clock=clock,
        client=_client(redis),
    )


def make_guard(
    qloo: RedisCounter,
    llm: RedisCounter,
    redis: FakeRedis,
    clock: Clock,
    telegram: FakeTelegram | None,
) -> BudgetGuard:
    return BudgetGuard(
        qloo=qloo,
        qloo_cap=QLOO_CAP,
        llm=llm,
        llm_cap=LLM_CAP,
        latch=RedisLatch("redis://unused", client=_client(redis)),
        alerter=TelegramAlerter("123:secret", "42", transport=telegram) if telegram else None,
        clock=clock,
    )


@pytest.fixture
def guard(
    redis: FakeRedis,
    telegram: FakeTelegram,
    qloo: RedisCounter,
    llm: RedisCounter,
    clock: Clock,
) -> BudgetGuard:
    return make_guard(qloo, llm, redis, clock, telegram)


async def test_quiet_below_70_percent(
    guard: BudgetGuard, qloo: RedisCounter, telegram: FakeTelegram
) -> None:
    await qloo.reserve(6)
    await guard.check()
    assert telegram.requests == []


async def test_alert_once_per_day_at_70_percent(
    guard: BudgetGuard,
    qloo: RedisCounter,
    telegram: FakeTelegram,
    redis: FakeRedis,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="draw.guard"):
        await qloo.reserve(7)  # the spend itself alerts: no run needed
    assert len(telegram.requests) == 1
    request = telegram.requests[0]
    assert request.url.path == "/bot123:secret/sendMessage"
    assert json.loads(request.content)["chat_id"] == "42"
    assert "Qloo daily budget at 70% (7/10) on 2026-10-26" in telegram.texts()[0]
    assert any("budget alert" in r.getMessage() for r in caplog.records)
    assert redis.ttl["draw:alert:qloo:2026-10-26"] > 0

    await guard.check()
    await qloo.reserve(1)
    await guard.check()
    assert len(telegram.requests) == 1, "one alert per counter per day"


async def test_check_alerts_on_spend_made_elsewhere(
    guard: BudgetGuard, redis: FakeRedis, telegram: FakeTelegram
) -> None:
    """Another instance filled the counter: the pre-run check still alerts."""
    await redis.incrby("draw:qloo:2026-10-26", 8)
    await guard.check()
    assert len(telegram.requests) == 1


async def test_latch_is_shared_across_guards(
    qloo: RedisCounter, llm: RedisCounter, redis: FakeRedis, clock: Clock
) -> None:
    """A second instance (or a restart) sees the Redis latch and stays quiet."""
    first = FakeTelegram()
    make_guard(qloo, llm, redis, clock, first)
    await qloo.reserve(8)
    assert len(first.requests) == 1

    other_telegram = FakeTelegram()
    other_qloo = RedisCounter("redis://unused", QLOO_CAP, clock=clock, client=_client(redis))
    other = make_guard(other_qloo, llm, redis, clock, other_telegram)
    await other.check()
    await other_qloo.reserve(1)
    assert other_telegram.requests == []


async def test_each_counter_alerts_on_its_own(
    guard: BudgetGuard, qloo: RedisCounter, llm: RedisCounter, telegram: FakeTelegram
) -> None:
    await qloo.reserve(7)
    await llm.reserve(750)
    texts = telegram.texts()
    assert len(texts) == 2
    assert "Qloo daily budget" in texts[0]
    assert "LLM token daily budget at 75% (750/1000)" in texts[1]


async def test_block_at_qloo_cap(
    guard: BudgetGuard, qloo: RedisCounter, telegram: FakeTelegram
) -> None:
    await qloo.reserve(QLOO_CAP)
    assert len(telegram.requests) == 1, "a jump straight past 70% still alerts"
    with pytest.raises(BudgetExceeded) as info:
        await guard.check()
    assert (info.value.used, info.value.cap, info.value.what) == (QLOO_CAP, QLOO_CAP, "Qloo")
    with pytest.raises(BudgetExceeded):
        await qloo.reserve(1)
    with pytest.raises(BudgetExceeded):
        await guard.check()
    assert len(telegram.requests) == 1


async def test_block_at_llm_cap_after_charge_over(
    guard: BudgetGuard, llm: RedisCounter, telegram: FakeTelegram
) -> None:
    with pytest.raises(BudgetExceeded):
        await llm.reserve(LLM_CAP + 50)  # a model call that crossed the cap mid-run
    assert len(telegram.requests) == 1, "the over-cap charge still reports"
    with pytest.raises(BudgetExceeded) as info:
        await guard.check()
    assert info.value.what == "LLM token"
    assert info.value.used == LLM_CAP + 50


async def test_below_cap_runs(guard: BudgetGuard, qloo: RedisCounter) -> None:
    await qloo.reserve(QLOO_CAP - 1)
    await guard.check()


async def test_date_rollover_resets_counters_and_alert(
    guard: BudgetGuard, qloo: RedisCounter, telegram: FakeTelegram, clock: Clock
) -> None:
    await qloo.reserve(QLOO_CAP)
    with pytest.raises(BudgetExceeded):
        await guard.check()
    assert len(telegram.requests) == 1

    clock.current = datetime(2026, 10, 27, 0, 0, 1, tzinfo=UTC)
    assert await qloo.used() == 0
    await guard.check()  # a new day: not blocked, no alert yet
    assert len(telegram.requests) == 1

    await qloo.reserve(7)
    assert len(telegram.requests) == 2
    assert "on 2026-10-27" in telegram.texts()[1]


async def test_telegram_failure_never_fails_the_spend(
    qloo: RedisCounter,
    llm: RedisCounter,
    redis: FakeRedis,
    clock: Clock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    broken = FakeTelegram(status=500)
    make_guard(qloo, llm, redis, clock, broken)
    with caplog.at_level(logging.WARNING, logger="draw.guard"):
        assert await qloo.reserve(8) == 8
    assert len(broken.requests) == 1
    assert "not delivered" in caplog.text
    assert "secret" not in caplog.text, "the bot token is never logged"


async def test_memory_counter_and_latch_without_redis(clock: Clock) -> None:
    telegram = FakeTelegram()
    qloo = MemoryCounter(QLOO_CAP, clock=clock)
    llm = MemoryCounter(LLM_CAP, prefix=LLM_PREFIX, what="LLM token", charge_over=True, clock=clock)
    guard = BudgetGuard(
        qloo=qloo,
        qloo_cap=QLOO_CAP,
        llm=llm,
        llm_cap=LLM_CAP,
        alerter=TelegramAlerter("t", "c", transport=telegram),
        clock=clock,
    )
    await qloo.reserve(7)
    await guard.check()
    assert len(telegram.requests) == 1
    clock.current = datetime(2026, 10, 27, tzinfo=UTC)
    await qloo.reserve(7)
    assert len(telegram.requests) == 2


def test_alerter_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    assert not isinstance(alerter_from_env(), TelegramAlerter)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    assert isinstance(alerter_from_env(), TelegramAlerter)


def test_guard_attaches_to_both_counters(
    guard: BudgetGuard, qloo: RedisCounter, llm: RedisCounter
) -> None:
    assert qloo.on_spend is not None and llm.on_spend is not None
