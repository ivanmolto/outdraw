"""Migrations against a throwaway pgvector Postgres (testcontainers): upgrade head, downgrade
base, upgrade head; the migrated schema matches the ORM models; triggers and checks hold."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from conftest import AGENT_DIR
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import create_async_engine

from draw.db.engine import async_url
from draw.db.models import Base
from draw.qloo.cache import QLOO_CACHE_DDL
from draw.qloo.recording import QLOO_CALLS_DDL

# Same major version as the Neon project.
IMAGE = "pgvector/pgvector:pg18"
TABLES = {
    "venues",
    "crowd_profiles",
    "runs",
    "candidates",
    "qloo_cache",
    "qloo_calls",
    "resolutions",
    "sessions",
    "embeddings",
}
VENUE = "INSERT INTO venues (id, name, city, country, capacity) VALUES ('v1', 'n', 'c', 'US', 10)"


def _docker_available() -> bool:
    try:
        import docker

        docker.from_env().ping()
    except Exception:
        return False
    return True


@pytest.fixture(scope="module")
def pg_url() -> Iterator[str]:
    if not _docker_available():
        if os.environ.get("CI"):
            pytest.fail("Docker is required for migration tests in CI")
        pytest.skip("Docker is not running")
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer(IMAGE, driver=None) as pg:
        yield pg.get_connection_url()


def _sync[T](url: str, fn: Callable[[Connection], T]) -> T:
    """Run `fn` on a sync-style connection inside one committed transaction."""

    async def run() -> T:
        engine = create_async_engine(async_url(url))
        try:
            async with engine.begin() as conn:
                return await conn.run_sync(fn)
        finally:
            await engine.dispose()

    return asyncio.run(run())


def _execute(url: str, *statements: str) -> None:
    def run(conn: Connection) -> None:
        for statement in statements:
            conn.execute(text(statement))

    _sync(url, run)


@pytest.fixture
def fresh_db(pg_url: str) -> str:
    """Empty `public` schema for each test; the shared container keeps the suite fast."""
    _execute(pg_url, "DROP SCHEMA public CASCADE", "CREATE SCHEMA public")
    return pg_url


def _config(url: str) -> Config:
    cfg = Config(str(AGENT_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(AGENT_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.attributes["configure_logger"] = False
    return cfg


def _tables(url: str) -> set[str]:
    names = _sync(url, lambda c: set(inspect(c).get_table_names()))
    return names - {"alembic_version"}


def _inspect(url: str) -> dict[str, Any]:
    def collect(conn: Connection) -> dict[str, Any]:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
        vector = conn.execute(
            text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        ).scalar_one_or_none()
        return {
            "tables": set(inspect(conn).get_table_names()) - {"alembic_version"},
            "version": version,
            "vector": vector == 1,
            "diff": compare_metadata(MigrationContext.configure(conn), Base.metadata),
        }

    return _sync(url, collect)


def test_upgrade_downgrade_upgrade(fresh_db: str) -> None:
    cfg = _config(fresh_db)

    command.upgrade(cfg, "head")
    state = _inspect(fresh_db)
    assert state["tables"] == TABLES
    assert state["vector"]
    assert state["version"] == "0001"

    command.downgrade(cfg, "base")
    assert _tables(fresh_db) == set()

    command.upgrade(cfg, "head")
    assert _inspect(fresh_db)["tables"] == TABLES


def test_schema_matches_models(fresh_db: str) -> None:
    command.upgrade(_config(fresh_db), "head")
    assert _inspect(fresh_db)["diff"] == []


def test_upgrade_adopts_tables_created_by_ensure_schema(fresh_db: str) -> None:
    """A database where the T06 gateway already created qloo_cache/qloo_calls still migrates."""
    _execute(
        fresh_db,
        QLOO_CACHE_DDL,
        QLOO_CALLS_DDL,
        "INSERT INTO qloo_cache (key, envelope) VALUES ('k', '{\"status\": \"ok\"}')",
    )
    command.upgrade(_config(fresh_db), "head")
    state = _inspect(fresh_db)
    assert state["tables"] == TABLES
    assert state["diff"] == []


def test_rerun_without_version_table_fails_loudly(fresh_db: str) -> None:
    """Idempotency comes from alembic_version, not from IF NOT EXISTS: a migrated database
    that lost its version marker must fail on the first owned table, not silently re-apply."""
    cfg = _config(fresh_db)
    command.upgrade(cfg, "head")
    _execute(fresh_db, "DROP TABLE alembic_version")
    with pytest.raises(ProgrammingError, match="already exists"):
        command.upgrade(cfg, "head")
    assert _tables(fresh_db) == TABLES


def test_updated_at_trigger_and_status_check(fresh_db: str) -> None:
    command.upgrade(_config(fresh_db), "head")
    _execute(
        fresh_db,
        VENUE,
        "UPDATE venues SET created_at = now() - interval '1 minute',"
        " updated_at = now() - interval '1 minute' WHERE id = 'v1'",
    )
    _execute(fresh_db, "UPDATE venues SET name = 'renamed' WHERE id = 'v1'")
    bumped = _sync(
        fresh_db,
        lambda c: c.execute(text("SELECT updated_at > created_at FROM venues")).scalar_one(),
    )
    assert bumped is True

    with pytest.raises(IntegrityError, match="runs_status_valid"):
        _execute(
            fresh_db,
            "INSERT INTO runs (id, venue_id, constraints, status)"
            " VALUES ('r1', 'v1', '{}', 'bogus')",
        )
