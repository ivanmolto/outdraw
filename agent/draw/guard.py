"""Daily budget guard: today's Qloo calls and LLM tokens against their caps, with one alert.

`BudgetGuard.check()` runs before every agent run (`POST /runs`, `POST /runs/{id}/refine`):
at or above the cap of `draw:qloo:YYYY-MM-DD` or `draw:llm:YYYY-MM-DD` it raises
`BudgetExceeded`, which the API answers with 429 `{reason: "budget"}`.

The 70% alert is not tied to runs. The guard attaches itself to both counters' `on_spend`,
so every count (resolve, profile, briefs, compare, the precompute script) reports its new
value; `check()` looks too, for spend made by another instance. At or above 70% of a cap it
logs a warning and sends one Telegram message per counter per UTC day: the latch key
`draw:alert:<counter>:YYYY-MM-DD` is set with NX, so several instances and restarts still
send one. Every key carries the date, so the counters and the latch reset together at
00:00 UTC. An alert never fails the call it rides on.

Spending stays with the gateways (`reserve()` per Qloo call, `MeteredModel` per model call),
which still stop a run that crosses the cap midway. Without `REDIS_URL` the counters read 0
and the guard never fires.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Final, Protocol

import httpx
import redis.asyncio as redis_asyncio

from draw.qloo.budget import KEY_TTL_S, Clock, DailyCounter, SpendHook, today_key, utc_now
from draw.qloo.envelope import BudgetExceeded

__all__ = [
    "ALERT_PREFIX",
    "ALERT_RATIO",
    "AlertLatch",
    "Alerter",
    "BudgetGuard",
    "MemoryLatch",
    "NullAlerter",
    "RedisLatch",
    "TelegramAlerter",
    "alerter_from_env",
]

log = logging.getLogger("draw.guard")

ALERT_RATIO: Final = 0.7
ALERT_PREFIX: Final = "draw:alert:"
TELEGRAM_TIMEOUT_S: Final = 5.0


class AlertLatch(Protocol):
    async def first_today(self, name: str, now_key: str) -> bool:
        """True exactly once per `name` per UTC day (`now_key` is that day, YYYY-MM-DD)."""
        ...


class MemoryLatch:
    """In-process latch: enough without Redis, where the counters never move anyway."""

    def __init__(self) -> None:
        self._seen: set[str] = set()

    async def first_today(self, name: str, now_key: str) -> bool:
        key = f"{ALERT_PREFIX}{name}:{now_key}"
        if key in self._seen:
            return False
        self._seen.add(key)
        return True


class RedisLatch:
    """`SET draw:alert:<name>:<date> 1 NX EX 2d`: one winner across instances and restarts."""

    def __init__(self, url: str, *, client: redis_asyncio.Redis | None = None) -> None:
        self._redis = client or redis_asyncio.from_url(url, decode_responses=True)

    async def first_today(self, name: str, now_key: str) -> bool:
        key = f"{ALERT_PREFIX}{name}:{now_key}"
        return bool(await self._redis.set(key, "1", nx=True, ex=KEY_TTL_S))

    async def aclose(self) -> None:
        await self._redis.aclose()


class Alerter(Protocol):
    async def send(self, text: str) -> None: ...


class NullAlerter:
    """No Telegram configured: the warning is only logged."""

    async def send(self, text: str) -> None:
        return None


class TelegramAlerter:
    """Bot API `sendMessage`. The token is in the URL, so it is never logged."""

    def __init__(
        self, token: str, chat_id: str, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._url = f"https://api.telegram.org/bot{token}/sendMessage"
        self._chat_id = chat_id
        self._transport = transport

    async def send(self, text: str) -> None:
        async with httpx.AsyncClient(
            timeout=TELEGRAM_TIMEOUT_S, transport=self._transport
        ) as client:
            res = await client.post(self._url, json={"chat_id": self._chat_id, "text": text})
            res.raise_for_status()


def alerter_from_env() -> Alerter:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    return TelegramAlerter(token, chat_id) if token and chat_id else NullAlerter()


@dataclass(frozen=True)
class _Budget:
    name: str
    """Latch and log name: `qloo` or `llm`."""
    what: str
    """`BudgetExceeded.what`, as the gateways raise it."""
    counter: DailyCounter
    cap: int


class BudgetGuard:
    def __init__(
        self,
        *,
        qloo: DailyCounter,
        qloo_cap: int,
        llm: DailyCounter,
        llm_cap: int,
        latch: AlertLatch | None = None,
        alerter: Alerter | None = None,
        ratio: float = ALERT_RATIO,
        clock: Clock = utc_now,
    ) -> None:
        self._budgets = (
            _Budget("qloo", "Qloo", qloo, qloo_cap),
            _Budget("llm", "LLM token", llm, llm_cap),
        )
        self.latch: AlertLatch = latch or MemoryLatch()
        self.alerter: Alerter = alerter or NullAlerter()
        self.ratio = ratio
        self.clock = clock
        for budget in self._budgets:
            budget.counter.on_spend = self._spend_hook(budget)

    async def check(self) -> None:
        """Raise at either cap; alert on the way (once a day per counter).

        Both counters are read before raising, so a blocked day still alerts for the other
        counter if it is high too.
        """
        exceeded: BudgetExceeded | None = None
        for budget in self._budgets:
            used = await budget.counter.used()
            await self._maybe_alert(budget, used)
            if used >= budget.cap and exceeded is None:
                exceeded = BudgetExceeded(used, budget.cap, budget.what)
        if exceeded is not None:
            raise exceeded

    def _spend_hook(self, budget: _Budget) -> SpendHook:
        async def hook(value: int) -> None:
            await self._maybe_alert(budget, value)

        return hook

    async def _maybe_alert(self, budget: _Budget, used: int) -> None:
        if used < budget.cap * self.ratio:
            return
        try:
            await self._alert(budget, used)
        except Exception as exc:  # an alert must never fail the call it rides on
            log.warning("budget alert not delivered: %s", type(exc).__name__)

    async def _alert(self, budget: _Budget, used: int) -> None:
        day = today_key(self.clock(), prefix="")
        if not await self.latch.first_today(budget.name, day):
            return
        pct = round(100 * used / budget.cap) if budget.cap else 100
        text = (
            f"Outdraw: {budget.what} daily budget at {pct}% ({used}/{budget.cap}) on {day} UTC."
            " Runs stop at 100%."
        )
        log.warning("budget alert: %s", text)
        await self.alerter.send(text)

    async def aclose(self) -> None:
        if isinstance(self.latch, RedisLatch):
            await self.latch.aclose()
