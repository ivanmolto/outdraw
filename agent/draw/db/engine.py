"""Async engine and session factory over `DATABASE_URL`."""

from __future__ import annotations

import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# libpq-only query params that asyncpg rejects; `sslmode` is renamed to asyncpg's `ssl`.
_LIBPQ_ONLY = {"channel_binding"}


def async_url(url: str) -> str:
    """Turn a libpq URL (as Neon prints it) into a `postgresql+asyncpg://` SQLAlchemy URL."""
    parts = urlsplit(url)
    scheme = parts.scheme.split("+", 1)[0]
    if scheme not in {"postgres", "postgresql"}:
        raise ValueError(f"not a Postgres URL: scheme {parts.scheme!r}")
    query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key in _LIBPQ_ONLY:
            continue
        query.append(("ssl" if key == "sslmode" else key, value))
    return urlunsplit(("postgresql+asyncpg", parts.netloc, parts.path, urlencode(query), ""))


def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return async_url(url)


def make_engine(url: str | None = None) -> AsyncEngine:
    return create_async_engine(async_url(url) if url else database_url(), pool_pre_ping=True)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
