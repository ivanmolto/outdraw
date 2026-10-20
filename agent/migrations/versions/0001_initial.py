"""Initial schema: venues, profiles, runs, candidates, Qloo cache/calls, resolutions,
sessions and pgvector embeddings.

Revision ID: 0001
Revises:
Create Date: 2026-10-21

`qloo_cache` and `qloo_calls` use `IF NOT EXISTS` because `PostgresCache.ensure_schema` and
`Recorder.ensure_schema` (T06) may already have created them; their DDL is identical.
"""

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPDATED_AT_TABLES = ("venues", "crowd_profiles", "resolutions")


def _created_at(name: str = "created_at") -> sa.Column[datetime]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def upgrade() -> None:
    # Left in place on downgrade: other schemas may use it, and dropping it needs ownership.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "venues",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("city", sa.Text(), nullable=False),
        sa.Column("country", sa.Text(), nullable=False),
        sa.Column("lat", sa.Float()),
        sa.Column("lng", sa.Float()),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column("past_shows", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("qloo_venue_id", sa.Text()),
        _created_at(),
        _created_at("updated_at"),
        sa.CheckConstraint("capacity >= 1", name="venues_capacity_positive"),
    )

    op.create_table(
        "crowd_profiles",
        sa.Column(
            "venue_id",
            sa.Text(),
            sa.ForeignKey("venues.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("profile", JSONB(), nullable=False),
        _created_at(),
        _created_at("updated_at"),
    )

    op.create_table(
        "runs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "venue_id", sa.Text(), sa.ForeignKey("venues.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("parent_run_id", sa.Text(), sa.ForeignKey("runs.id", ondelete="SET NULL")),
        sa.Column("constraints", JSONB(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("result", JSONB()),
        sa.Column("token_usage", JSONB()),
        sa.Column("is_seed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("seed_slug", sa.Text(), unique=True),
        _created_at(),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'ok', 'ask', 'error')", name="runs_status_valid"
        ),
    )
    op.create_index("ix_runs_venue_id_created_at", "runs", ["venue_id", "created_at"])
    op.create_index("ix_runs_parent_run_id", "runs", ["parent_run_id"])

    op.create_table(
        "candidates",
        sa.Column(
            "run_id", sa.Text(), sa.ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("entity_id", sa.Text(), primary_key=True),
        sa.Column("score", JSONB(), nullable=False),
        sa.Column("evidence", JSONB(), nullable=False),
        sa.Column("pencilled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS qloo_cache (
            key        TEXT PRIMARY KEY,
            envelope   JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS qloo_calls (
            call_id    TEXT PRIMARY KEY,
            run_id     TEXT NULL,
            workflow   TEXT NOT NULL,
            request    JSONB NOT NULL,
            envelope   JSONB NOT NULL,
            status     TEXT NOT NULL,
            cache_hit  BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_qloo_calls_run_id ON qloo_calls (run_id)")

    op.create_table(
        "resolutions",
        sa.Column(
            "venue_id",
            sa.Text(),
            sa.ForeignKey("venues.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("query", sa.Text(), primary_key=True),
        sa.Column("candidates", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("chosen_entity_id", sa.Text()),
        sa.Column("confirmed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        _created_at("updated_at"),
    )

    op.create_table(
        "sessions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("plan", sa.Text(), nullable=False, server_default=sa.text("'free'")),
        _created_at(),
        sa.CheckConstraint("plan IN ('free', 'pro')", name="sessions_plan_valid"),
    )

    op.create_table(
        "embeddings",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("ref_id", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(256), nullable=False),
        _created_at(),
        sa.UniqueConstraint("kind", "ref_id", name="uq_embeddings_kind_ref_id"),
    )
    op.create_index(
        "ix_embeddings_embedding_hnsw",
        "embeddings",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )

    # A trigger rather than an ORM onupdate, so raw asyncpg writes bump updated_at too.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END
        $$ LANGUAGE plpgsql
        """
    )
    for table in UPDATED_AT_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_set_updated_at BEFORE UPDATE ON {table}"
            " FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )


def downgrade() -> None:
    op.drop_table("embeddings")
    op.drop_table("sessions")
    op.drop_table("resolutions")
    op.drop_table("qloo_calls")
    op.drop_table("qloo_cache")
    op.drop_table("candidates")
    op.drop_table("runs")
    op.drop_table("crowd_profiles")
    op.drop_table("venues")
    # Triggers went with their tables.
    op.execute("DROP FUNCTION IF EXISTS set_updated_at()")
