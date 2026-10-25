"""Shared service resources: the database, the Qloo gateways and the daily counters.

`Deps` is built once in the app's lifespan (`Deps.from_env`) and read by every route
through `get_deps`. Without `DATABASE_URL` there is no `Deps` and every route except
/health answers 503. One cache, one Qloo counter and one recorder are shared by every
gateway, so per-run gateways differ only in their `run_id` (the `qloo_calls` tag behind
`GET /runs/{id}/calls`).
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import asyncpg
from fastapi import HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from strands.models import Model
from tenacity.wait import wait_base

from draw.agent.agent import AgentSettings, token_counter_from_env
from draw.db.engine import make_engine, make_sessionmaker
from draw.guard import BudgetGuard, MemoryLatch, RedisLatch, alerter_from_env
from draw.qloo.budget import DailyCounter, NullCounter, RedisCounter
from draw.qloo.cache import CacheBackend, PostgresCache
from draw.qloo.config import QlooConfig
from draw.qloo.exec_client import QlooExec
from draw.qloo.mcp_client import QlooMCP
from draw.qloo.recording import Recorder

__all__ = ["Deps", "Gateways", "get_deps"]


@dataclass
class Gateways:
    """Makes gateways over the shared cache, counter and recorder."""

    config: QlooConfig
    cache: CacheBackend
    counter: DailyCounter
    recorder: Recorder | None
    mcp_command: Sequence[str] | None = None
    """Overrides `<qloo_bin> mcp` (tests point it at the fake server)."""
    retry_wait: wait_base | None = None

    def scripted(self, run_id: str | None = None) -> QlooExec:
        """`qloo exec` for resolve and profile calls; nothing to start or stop."""
        return QlooExec(
            self.config,
            cache=self.cache,
            counter=self.counter,
            recorder=self.recorder,
            retry_wait=self.retry_wait,
            run_id=run_id,
        )

    @asynccontextmanager
    async def agent(self, run_id: str) -> AsyncIterator[QlooMCP]:
        """A `qloo mcp` server for one agent run, stopped when the run ends."""
        gateway = QlooMCP(
            self.config,
            command=self.mcp_command,
            cache=self.cache,
            counter=self.counter,
            recorder=self.recorder,
            retry_wait=self.retry_wait,
            run_id=run_id,
        )
        # `start` blocks until the server is ready; keep the event loop free meanwhile.
        await asyncio.to_thread(gateway.start)
        try:
            yield gateway
        finally:
            await asyncio.to_thread(gateway.stop)


@dataclass
class Deps:
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    gateways: Gateways
    llm_counter: DailyCounter
    llm_cap: int
    guard: BudgetGuard
    """Checked before every run; its 70% alert rides on both counters' `on_spend`."""
    model_factory: Callable[[], Model] | None = None
    """None: the agent builds Haiku 5.5 from the environment."""
    pool: asyncpg.Pool | None = field(default=None, repr=False)
    """asyncpg pool behind the Qloo cache and recorder (they take asyncpg, not SQLAlchemy)."""

    @classmethod
    async def create(
        cls,
        database_url: str,
        *,
        config: QlooConfig | None = None,
        qloo_counter: DailyCounter | None = None,
        llm_counter: DailyCounter | None = None,
        model_factory: Callable[[], Model] | None = None,
        mcp_command: Sequence[str] | None = None,
        retry_wait: wait_base | None = None,
    ) -> Deps:
        config = config or QlooConfig.from_env()
        settings = AgentSettings.from_env()
        pool = await asyncpg.create_pool(database_url, min_size=1, max_size=4)
        assert pool is not None
        cache = PostgresCache(pool)
        await cache.ensure_schema()
        recorder = Recorder(
            config.recordings_dir if config.record else None, secret=config.api_key, pool=pool
        )
        await recorder.ensure_schema()
        if qloo_counter is None:
            qloo_counter = (
                RedisCounter(config.redis_url, config.daily_cap)
                if config.redis_url
                else NullCounter()
            )
        llm_counter = llm_counter or token_counter_from_env(settings)
        guard = BudgetGuard(
            qloo=qloo_counter,
            qloo_cap=config.daily_cap,
            llm=llm_counter,
            llm_cap=settings.token_cap,
            latch=RedisLatch(config.redis_url) if config.redis_url else MemoryLatch(),
            alerter=alerter_from_env(),
        )
        engine = make_engine(database_url)
        return cls(
            engine=engine,
            sessions=make_sessionmaker(engine),
            gateways=Gateways(
                config,
                cache,
                qloo_counter,
                recorder,
                mcp_command=mcp_command,
                retry_wait=retry_wait,
            ),
            llm_counter=llm_counter,
            llm_cap=settings.token_cap,
            guard=guard,
            model_factory=model_factory,
            pool=pool,
        )

    @classmethod
    async def from_env(cls) -> Deps | None:
        url = os.environ.get("DATABASE_URL")
        return await cls.create(url) if url else None

    async def aclose(self) -> None:
        for counter in (self.gateways.counter, self.llm_counter):
            if isinstance(counter, RedisCounter):
                await counter.aclose()
        await self.guard.aclose()
        await self.engine.dispose()
        if self.pool is not None:
            await self.pool.close()


def get_deps(request: Request) -> Deps:
    deps: Deps | None = getattr(request.app.state, "deps", None)
    if deps is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "DATABASE_URL is not set.")
    return deps
