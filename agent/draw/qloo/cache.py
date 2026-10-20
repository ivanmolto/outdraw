"""Envelope cache: in-memory for tests and local runs, Postgres `qloo_cache` in production.

Key: sha256 over the workflow name and the canonical JSON of the input. Only `ok` and
`partial` envelopes are stored (see `envelope.CACHEABLE_STATUSES`); entries expire after
seven days. Cached envelopes stay server-side: Qloo's terms forbid response data in the
public repo.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from typing import Any, Protocol

import asyncpg

from draw.qloo.config import CACHE_TTL_S

# Owned by the T14 migration once it exists; `PostgresCache.ensure_schema` is idempotent.
QLOO_CACHE_DDL = """
CREATE TABLE IF NOT EXISTS qloo_cache (
    key        TEXT PRIMARY KEY,
    envelope   JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def cache_key(workflow: str, input_: dict[str, Any]) -> str:
    return hashlib.sha256((workflow + canonical_json(input_)).encode("utf-8")).hexdigest()


class CacheBackend(Protocol):
    async def get(self, key: str) -> dict[str, Any] | None: ...

    async def set(self, key: str, envelope: dict[str, Any]) -> None: ...


class MemoryCache:
    """Dict-backed cache with the same TTL semantics as the Postgres table."""

    def __init__(
        self, ttl_s: float = CACHE_TTL_S, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._ttl = ttl_s
        self._clock = clock
        self._items: dict[str, tuple[float, str]] = {}

    async def get(self, key: str) -> dict[str, Any] | None:
        item = self._items.get(key)
        if item is None:
            return None
        stored_at, payload = item
        if self._clock() - stored_at > self._ttl:
            del self._items[key]
            return None
        loaded: dict[str, Any] = json.loads(payload)
        return loaded

    async def set(self, key: str, envelope: dict[str, Any]) -> None:
        self._items[key] = (self._clock(), canonical_json(envelope))

    def __len__(self) -> int:
        return len(self._items)


class PostgresCache:
    """`qloo_cache(key pk, envelope jsonb, created_at)` over an asyncpg pool."""

    def __init__(self, pool: asyncpg.Pool, ttl_s: float = CACHE_TTL_S) -> None:
        self._pool = pool
        self._ttl = ttl_s

    async def ensure_schema(self) -> None:
        await self._pool.execute(QLOO_CACHE_DDL)

    async def get(self, key: str) -> dict[str, Any] | None:
        row = await self._pool.fetchrow(
            "SELECT envelope::text FROM qloo_cache"
            " WHERE key = $1 AND created_at > now() - ($2::float8 * interval '1 second')",
            key,
            float(self._ttl),
        )
        if row is None:
            return None
        loaded: dict[str, Any] = json.loads(row[0])
        return loaded

    async def set(self, key: str, envelope: dict[str, Any]) -> None:
        await self._pool.execute(
            "INSERT INTO qloo_cache (key, envelope, created_at) VALUES ($1, $2::jsonb, now())"
            " ON CONFLICT (key) DO UPDATE SET envelope = EXCLUDED.envelope,"
            " created_at = EXCLUDED.created_at",
            key,
            canonical_json(envelope),
        )
