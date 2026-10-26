"""Semantic run cache (T36): a run whose request reads like an earlier one reuses its result.

The request is a canonical text (city, sorted past acts, constraints), embedded and looked up
by cosine similarity among the stored request embeddings (`embeddings`, kind `run_request`,
`ref_id` the run). At `THRESHOLD` or above the earlier run's result is served again, marked
`cache_source="semantic"`, and nothing is spent on Qloo or the model.

The embedding only absorbs wording. One more or one fewer tag, or another number, barely
moves the cosine of a text this long (adding a genre still scores ~0.98), so a hit must also
be an ok run of the same venue with the same normalised include and exclude tags, size tier,
recency window and candidate count, made after the venue and its crowd profile were last
changed (edited past shows move `past_fit` and `novelty`; a rebuilt profile means new Qloo
data).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from draw.db.models import CrowdProfileRow, EmbeddingRow, RunRow, VenueRow
from draw.schemas import Constraints, VenueInput

__all__ = ["KIND", "THRESHOLD", "Hit", "canonical_text", "is_hit", "lookup", "store"]

KIND: Final = "run_request"
THRESHOLD: Final = 0.92
# Vectors are stored as float32, so an exact 0.92 can read back a hair under it.
_TOLERANCE: Final = 1e-6
# Nearest requests checked for the exact guards before giving up.
_NEAREST: Final = 5


@dataclass(frozen=True)
class Hit:
    run_id: str
    similarity: float


def _norm(text: str) -> str:
    """Case, spacing and punctuation folded: "Hip-Hop " and "hip hop" are one tag."""
    return " ".join(re.sub(r"[\W_]+", " ", text.lower()).split())


def _set(items: list[str]) -> frozenset[str]:
    return frozenset(n for n in map(_norm, items) if n)


def _sorted(items: list[str]) -> str:
    return "; ".join(sorted(_set(items)))


def _tags(constraints: Constraints) -> tuple[frozenset[str], frozenset[str]]:
    return _set(constraints.include_tags), _set(constraints.exclude_tags)


def canonical_text(venue: VenueInput, constraints: Constraints) -> str:
    """The request as one normalised text: case, spacing, order and repeats do not count."""
    return "\n".join(
        [
            f"city: {_norm(venue.city)}",
            f"past acts: {_sorted([s.act for s in venue.past_shows])}",
            f"include tags: {_sorted(constraints.include_tags)}",
            f"exclude tags: {_sorted(constraints.exclude_tags)}",
            f"size tier: {constraints.size_tier}",
            f"exclude recent months: {constraints.exclude_recent_months}",
            f"max candidates: {constraints.max_candidates}",
        ]
    )


def is_hit(similarity: float) -> bool:
    return similarity >= THRESHOLD - _TOLERANCE


async def lookup(
    session: AsyncSession, vector: list[float], venue_id: str, constraints: Constraints
) -> Hit | None:
    """The most similar earlier request that may stand in for this one, if it is a hit."""
    exact = constraints.model_dump(
        mode="json", include={"size_tier", "exclude_recent_months", "max_candidates"}
    )
    similarity = (1 - EmbeddingRow.embedding.cosine_distance(vector)).label("similarity")
    stmt = (
        select(RunRow.id, RunRow.constraints, similarity)
        .join(EmbeddingRow, (EmbeddingRow.ref_id == RunRow.id) & (EmbeddingRow.kind == KIND))
        .join(CrowdProfileRow, CrowdProfileRow.venue_id == RunRow.venue_id)
        .join(VenueRow, VenueRow.id == RunRow.venue_id)
        .where(
            RunRow.venue_id == venue_id,
            RunRow.status == "ok",
            RunRow.constraints.contains(exact),
            RunRow.created_at >= CrowdProfileRow.updated_at,
            RunRow.created_at >= VenueRow.updated_at,
        )
        .order_by(EmbeddingRow.embedding.cosine_distance(vector))
        .limit(_NEAREST)
    )
    tags = _tags(constraints)
    for row in await session.execute(stmt):
        if not is_hit(float(row.similarity)):
            break  # nearest first: the rest are further still
        if _tags(Constraints.model_validate(row.constraints)) == tags:
            return Hit(run_id=row.id, similarity=float(row.similarity))
    return None


def store(session: AsyncSession, run_id: str, vector: list[float]) -> None:
    """Add the run's request embedding to the session (committed with the run's result)."""
    session.add(EmbeddingRow(kind=KIND, ref_id=run_id, embedding=vector))
