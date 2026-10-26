"""`QlooExec`: the gateway over `qloo exec <workflow> --input-file <path>`.

Per call, in order: mint a `call_id`; look up the cache (a hit spawns nothing); reserve a
slot in the daily budget (`BudgetExceeded` before any spawn); take the concurrency
semaphore; spawn the harness with the full environment and a 20s timeout; normalise the
JSON on stdout; retry with backoff only when `error.retryable`; cache `ok`/`partial`;
record the call when `QLOO_RECORD=1`; log one structured line; then branch on status:

- `needs_input` raises `QlooNeedsInput(candidates)`;
- `empty`, or `ok` with nothing in it, raises `QlooEmpty`;
- `error` raises `QlooError(code, retryable, recovery)`;
- `ok`, `partial` and `degraded` are returned as a `QlooResult` (check `summary` and label
  the result when the status is not `ok`).

Input shapes come from docs/decisions/001-qloo-workflows.md and contain only entity IDs,
tag URNs, region names, dates and limits, never personal data.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
import uuid
from asyncio import create_subprocess_exec
from asyncio.subprocess import PIPE
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Literal

import asyncpg
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)
from tenacity.wait import wait_base

from draw.qloo.budget import DailyCounter, NullCounter, RedisCounter
from draw.qloo.cache import CacheBackend, MemoryCache, PostgresCache, cache_key
from draw.qloo.config import QlooConfig
from draw.qloo.envelope import (
    CACHEABLE_STATUSES,
    QlooEmpty,
    QlooEnvelope,
    QlooError,
    QlooNeedsInput,
    QlooResult,
    candidates_of,
    is_empty,
    normalise,
)
from draw.qloo.recording import Recorder

log = logging.getLogger("draw.qloo")

MAX_ATTEMPTS = 3
STDERR_TAIL = 400

Workflow = Literal[
    "find_tags",
    "describe",
    "recommend",
    "rank",
    "where_popular",
    "compare_audiences",
    "audience_demographics",
    "trends",
]
TargetType = Literal["artist", "place", "movie", "brand", "book", "tv_show", "podcast"]


class _RetryableEnvelope(Exception):  # noqa: N818
    """Internal: an `error` envelope with `retryable: true`, drives tenacity."""

    def __init__(self, envelope: QlooEnvelope) -> None:
        super().__init__(envelope.error.code if envelope.error else "retryable")
        self.envelope = envelope


def _input(**fields: Any) -> dict[str, Any]:
    """Drop unset fields so the cache key and the harness see the same minimal shape."""
    return {k: v for k, v in fields.items() if v is not None}


class QlooExec:
    def __init__(
        self,
        config: QlooConfig | None = None,
        *,
        cache: CacheBackend | None = None,
        counter: DailyCounter | None = None,
        recorder: Recorder | None = None,
        retry_wait: wait_base | None = None,
        run_id: str | None = None,
        observer: Callable[[QlooResult], None] | None = None,
    ) -> None:
        self.config = config or QlooConfig()
        self.cache: CacheBackend = cache if cache is not None else MemoryCache()
        self.counter: DailyCounter = counter if counter is not None else NullCounter()
        self.recorder = recorder
        self.run_id = run_id
        # Sees every result (cache hits and non-ok statuses included) before the status
        # branch raises: the agent's run store uses it to list the calls behind a score.
        self.observer = observer
        # Per gateway, so per run: the run's summary log line reads them (`draw.api.runs`).
        self.calls = 0
        self.cache_hits = 0
        self._wait: wait_base = retry_wait or wait_exponential_jitter(multiplier=0.5, max=8.0)
        self._semaphore = asyncio.Semaphore(self.config.concurrency)
        self._pool: asyncpg.Pool | None = None

    @classmethod
    async def from_env(cls, run_id: str | None = None) -> QlooExec:
        """Wire cache, counter and recorder from `DATABASE_URL`, `REDIS_URL`, `QLOO_RECORD`."""
        config = QlooConfig.from_env()
        pool: asyncpg.Pool | None = None
        cache: CacheBackend
        if config.database_url:
            pool = await asyncpg.create_pool(config.database_url, min_size=1, max_size=4)
            assert pool is not None
            pg_cache = PostgresCache(pool)
            await pg_cache.ensure_schema()
            cache = pg_cache
        else:
            cache = MemoryCache()
        counter: DailyCounter = (
            RedisCounter(config.redis_url, config.daily_cap) if config.redis_url else NullCounter()
        )
        recorder: Recorder | None = None
        if config.record:
            recorder = Recorder(config.recordings_dir, secret=config.api_key, pool=pool)
            await recorder.ensure_schema()
        client = cls(config, cache=cache, counter=counter, recorder=recorder, run_id=run_id)
        client._pool = pool
        return client

    async def aclose(self) -> None:
        if isinstance(self.counter, RedisCounter):
            await self.counter.aclose()
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    # --- workflows (shapes verified on the real key, see decision 001) ----------------

    async def find_tags(
        self, query: str, *, limit: int = 5, semantic: bool | None = None
    ) -> QlooResult:
        return await self.exec("find_tags", _input(query=query, limit=limit, semantic=semantic))

    async def describe(self, entity: str, *, type: str | None = None) -> QlooResult:  # noqa: A002
        return await self.exec("describe", _input(entity=entity, type=type))

    async def recommend(
        self,
        target_type: TargetType,
        *,
        signals: Sequence[str] | None = None,
        signal_tags: Sequence[str] | None = None,
        signal_location: str | None = None,
        filter_location: str | None = None,
        include_tags: Sequence[str] | None = None,
        include_tags_operator: Literal["union", "intersection"] | None = None,
        exclude_tags: Sequence[str] | None = None,
        exclude_tags_operator: Literal["union", "intersection"] | None = None,
        limit: int = 10,
    ) -> QlooResult:
        return await self.exec(
            "recommend",
            _input(
                target_type=target_type,
                signals=list(signals) if signals else None,
                signal_tags=list(signal_tags) if signal_tags else None,
                signal_location=signal_location,
                filter_location=filter_location,
                include_tags=list(include_tags) if include_tags else None,
                include_tags_operator=include_tags_operator,
                exclude_tags=list(exclude_tags) if exclude_tags else None,
                exclude_tags_operator=exclude_tags_operator,
                limit=limit,
            ),
        )

    async def rank(
        self, options: Sequence[str], *, option_type: TargetType, signals: Sequence[str]
    ) -> QlooResult:
        return await self.exec(
            "rank",
            _input(options=list(options), option_type=option_type, signals=list(signals)),
        )

    async def where_popular(
        self, entity: str, *, entity_type: TargetType, within: str, limit: int = 10
    ) -> QlooResult:
        return await self.exec(
            "where_popular",
            _input(entity=entity, entity_type=entity_type, within=within, limit=limit),
        )

    async def compare_audiences(
        self, group_a: Sequence[str], group_b: Sequence[str], *, limit: int = 10
    ) -> QlooResult:
        return await self.exec(
            "compare_audiences",
            _input(group_a=list(group_a), group_b=list(group_b), limit=limit),
        )

    async def audience_demographics(self, entity: str, *, entity_type: TargetType) -> QlooResult:
        return await self.exec(
            "audience_demographics", _input(entity=entity, entity_type=entity_type)
        )

    async def trends(
        self,
        entities: Sequence[str],
        *,
        entity_type: TargetType,
        start_date: str,
        end_date: str,
        limit: int = 10,
    ) -> QlooResult:
        return await self.exec(
            "trends",
            _input(
                entities=list(entities),
                entity_type=entity_type,
                start_date=start_date,
                end_date=end_date,
                limit=limit,
            ),
        )

    # --- the gateway ----------------------------------------------------------------

    async def exec(self, workflow: Workflow, input_: dict[str, Any]) -> QlooResult:
        call_id = str(uuid.uuid4())
        key = cache_key(workflow, input_)
        started = time.monotonic()
        attempts = 0

        cached = await self.cache.get(key)
        if cached is not None:
            envelope = QlooEnvelope.model_validate(cached)
            cache_hit = True
        else:
            cache_hit = False
            envelope, attempts = await self._run_with_retries(workflow, input_)
            if envelope.status in CACHEABLE_STATUSES:
                await self.cache.set(key, envelope.model_dump(mode="json"))

        latency_ms = int((time.monotonic() - started) * 1000)
        self.calls += 1
        self.cache_hits += int(cache_hit)
        result = QlooResult(
            **envelope.model_dump(mode="json"),
            call_id=call_id,
            workflow=workflow,
            cache_hit=cache_hit,
            latency_ms=latency_ms,
            request=dict(input_),
        )
        log.info(
            "qloo_call workflow=%s status=%s cache=%s latency_ms=%d attempts=%d call_id=%s",
            workflow,
            result.status,
            "hit" if cache_hit else "miss",
            latency_ms,
            attempts,
            call_id,
            extra={
                "workflow": workflow,
                "status": result.status,
                "cache_hit": cache_hit,
                "latency_ms": latency_ms,
                "attempts": attempts,
                "call_id": call_id,
            },
        )
        if self.recorder is not None:
            await self.recorder.record(
                call_id=call_id,
                workflow=workflow,
                request=input_,
                envelope=envelope.model_dump(mode="json"),
                status=result.status,
                cache_hit=cache_hit,
                run_id=self.run_id,
            )
        if self.observer is not None:
            self.observer(result)
        self._raise_for_status(result)
        return result

    @staticmethod
    def _raise_for_status(result: QlooResult) -> None:
        if result.status == "needs_input":
            raise QlooNeedsInput(candidates_of(result), envelope=result)
        if result.status == "error":
            err = result.error
            assert err is not None
            raise QlooError(
                err.code, retryable=err.retryable, recovery=err.recovery, envelope=result
            )
        if is_empty(result):
            raise QlooEmpty(result)

    async def _run_with_retries(
        self, workflow: str, input_: dict[str, Any]
    ) -> tuple[QlooEnvelope, int]:
        """Spawn up to MAX_ATTEMPTS times; only a `retryable` error envelope triggers a retry."""
        attempts = 0
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(MAX_ATTEMPTS),
                wait=self._wait,
                retry=retry_if_exception_type(_RetryableEnvelope),
                reraise=False,
            ):
                with attempt:
                    attempts += 1
                    envelope = await self._spawn_once(workflow, input_)
                    if envelope.status == "error" and envelope.error and envelope.error.retryable:
                        raise _RetryableEnvelope(envelope)
                    return envelope, attempts
        except RetryError as exc:
            last = exc.last_attempt.exception()
            assert isinstance(last, _RetryableEnvelope)
            return last.envelope, attempts
        raise AssertionError("unreachable")  # pragma: no cover

    async def _spawn_once(self, workflow: str, input_: dict[str, Any]) -> QlooEnvelope:
        await self.counter.reserve()  # BudgetExceeded here means nothing was spawned
        async with self._semaphore:
            return await self._spawn(workflow, input_)

    async def _spawn(self, workflow: str, input_: dict[str, Any]) -> QlooEnvelope:
        binary = self.config.qloo_bin
        if binary is None:
            raise QlooError(
                "harness_missing",
                recovery="Install the pinned harness with `npm ci` inside /agent.",
            )
        with tempfile.TemporaryDirectory(prefix="qloo-exec-") as tmp:
            input_file = Path(tmp) / "input.json"
            input_file.write_text(json.dumps(input_), encoding="utf-8")
            proc = await create_subprocess_exec(
                binary,
                "exec",
                workflow,
                "--input-file",
                str(input_file),
                stdout=PIPE,
                stderr=PIPE,
                # The full environment (the harness reads the key from it), as a plain dict:
                # uvloop, which uvicorn runs on, rejects `os.environ` itself.
                env=dict(os.environ),
            )
            try:
                out, err = await asyncio.wait_for(proc.communicate(), self.config.timeout_s)
            except TimeoutError:
                proc.kill()
                await proc.wait()
                raise QlooError(
                    "harness_timeout",
                    recovery=f"No answer from the harness in {self.config.timeout_s:.0f}s.",
                ) from None

        returncode = proc.returncode if proc.returncode is not None else -1
        stdout = out.decode("utf-8", errors="replace")
        stderr = err.decode("utf-8", errors="replace").strip()[-STDERR_TAIL:]
        try:
            raw = json.loads(stdout) if stdout.strip() else None
        except json.JSONDecodeError:
            raw = None
        if not isinstance(raw, dict):
            raise QlooError(
                "harness_failed",
                recovery=f"Harness exit {returncode} with no envelope: {stderr or 'no stderr'}",
            )
        raw.setdefault("operation", workflow)
        raw.setdefault("harness_exit_code", returncode)
        return normalise(raw)
