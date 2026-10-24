"""ORM models. The schema itself is owned by the Alembic migrations in `/agent/migrations`;
`test_migrations.py` fails if these models and the migrated schema drift apart."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

EMBEDDING_DIM = 256


class Base(DeclarativeBase):
    pass


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class VenueRow(Base):
    __tablename__ = "venues"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    city: Mapped[str] = mapped_column(Text, nullable=False)
    country: Mapped[str] = mapped_column(Text, nullable=False)
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    past_shows: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    qloo_venue_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _created_at()

    __table_args__ = (CheckConstraint("capacity >= 1", name="venues_capacity_positive"),)


class CrowdProfileRow(Base):
    __tablename__ = "crowd_profiles"

    venue_id: Mapped[str] = mapped_column(
        Text, ForeignKey("venues.id", ondelete="CASCADE"), primary_key=True
    )
    profile: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _created_at()


class RunRow(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    venue_id: Mapped[str] = mapped_column(
        Text, ForeignKey("venues.id", ondelete="CASCADE"), nullable=False
    )
    parent_run_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("runs.id", ondelete="SET NULL")
    )
    constraints: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # {"input": n, "output": n} as counted by the agent loop.
    token_usage: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # The LLM-only shortlist for the same venue and constraints, cached by GET /runs/{id}/compare.
    compare: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    is_seed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    seed_slug: Mapped[str | None] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        # pending/running while the agent works; then AgentResult.status (ok | ask | error).
        CheckConstraint(
            "status IN ('pending', 'running', 'ok', 'ask', 'error')", name="runs_status_valid"
        ),
        Index("ix_runs_venue_id_created_at", "venue_id", "created_at"),
        Index("ix_runs_parent_run_id", "parent_run_id"),
    )


class CandidateRow(Base):
    __tablename__ = "candidates"

    run_id: Mapped[str] = mapped_column(
        Text, ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True
    )
    entity_id: Mapped[str] = mapped_column(Text, primary_key=True)
    score: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    pencilled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))


class QlooCacheRow(Base):
    """Same table `draw.qloo.cache.PostgresCache` reads and writes."""

    __tablename__ = "qloo_cache"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    envelope: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = _created_at()


class QlooCallRow(Base):
    """Redacted request/envelope pairs written by `draw.qloo.recording.Recorder`.

    `run_id` has no foreign key: precompute and smoke scripts record calls outside any run.
    """

    __tablename__ = "qloo_calls"

    call_id: Mapped[str] = mapped_column(Text, primary_key=True)
    run_id: Mapped[str | None] = mapped_column(Text, index=True)
    workflow: Mapped[str] = mapped_column(Text, nullable=False)
    request: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    envelope: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    cache_hit: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = _created_at()


class ResolutionRow(Base):
    __tablename__ = "resolutions"

    venue_id: Mapped[str] = mapped_column(
        Text, ForeignKey("venues.id", ondelete="CASCADE"), primary_key=True
    )
    query: Mapped[str] = mapped_column(Text, primary_key=True)
    candidates: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    chosen_entity_id: Mapped[str | None] = mapped_column(Text)
    confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    updated_at: Mapped[datetime] = _created_at()


class SessionRow(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    plan: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'free'"))
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (CheckConstraint("plan IN ('free', 'pro')", name="sessions_plan_valid"),)


class EmbeddingRow(Base):
    __tablename__ = "embeddings"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    ref_id: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        UniqueConstraint("kind", "ref_id", name="uq_embeddings_kind_ref_id"),
        Index(
            "ix_embeddings_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )
