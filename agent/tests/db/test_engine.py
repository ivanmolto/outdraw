from __future__ import annotations

import pytest

from draw.db.engine import async_url


def test_neon_url_becomes_asyncpg_url() -> None:
    url = "postgresql://u:p@ep-x-pooler.us-east-1.aws.neon.tech/outdraw?sslmode=require&channel_binding=require"
    assert async_url(url) == (
        "postgresql+asyncpg://u:p@ep-x-pooler.us-east-1.aws.neon.tech/outdraw?ssl=require"
    )


def test_plain_and_driver_urls() -> None:
    assert async_url("postgres://u:p@h:5432/db") == "postgresql+asyncpg://u:p@h:5432/db"
    assert async_url("postgresql+psycopg2://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"


def test_rejects_non_postgres() -> None:
    with pytest.raises(ValueError):
        async_url("mysql://u:p@h/db")
