"""Tag mapping from free text (T37): a booker's phrase to the nearest Qloo tag names.

`index_tags` runs once at startup (in the background, `draw.main`): it reads the tag names in
the cached `find_tags` envelopes (`qloo_cache`, filled by T02's precompute and every later
run) and embeds each name not yet indexed into `embeddings` with kind `tag` and `ref_id` the
normalised name. Nothing is spent on Qloo; tag names and vectors stay server-side.

At refine time `map_phrases` splits the message into phrases and returns the `K` nearest tag
names for each, which `draw.agent.refine.parse_delta` passes to the constraint parser as
hints. The hints only suggest words for the parser's tags; they never rank acts, and the
parser is free to ignore them.

Only tags at `FLOOR` cosine or above count as near. Without an embedding model (`EMBED_URL`,
T35) the `HashingEmbedder` stands in, and it only knows spelling: "jazzy" and "hip-hop" reach
"jazz" and "hip hop", a phrase like "smoky late-night horns" reaches nothing and the parser
works from the booker's words alone. The meaning-level mapping the task asks for needs T35.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from draw.db.models import EmbeddingRow, QlooCacheRow
from draw.embed.embedder import Embedder

__all__ = [
    "FLOOR",
    "K",
    "KIND",
    "MAX_PHRASES",
    "PhraseTags",
    "index_tags",
    "map_phrases",
    "nearest_tags",
    "norm_tag",
    "phrases_of",
    "tag_names",
]

log = logging.getLogger("draw.embed.tag_map")

KIND: Final = "tag"
K: Final = 5
"""Nearest tags per phrase."""
FLOOR: Final = 0.5
"""Lowest cosine that counts as near: about what "jazzy" scores against "jazz" under the
hashing fallback, well below any meaning-level match of a real model."""
MAX_PHRASES: Final = 4
MAX_PHRASE_CHARS: Final = 80
MAX_NAME_CHARS: Final = 60
MAX_INDEXED: Final = 5000
"""Tag names embedded per start, at most (the cache holds far fewer)."""


def norm_tag(name: str) -> str:
    """Case and spacing folded; the `ref_id` of a tag's embedding and the name in a hint."""
    return " ".join(name.casefold().split())


def _names_in(envelope: dict[str, Any]) -> Iterable[str]:
    results = envelope.get("results")
    for item in results if isinstance(results, list) else []:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            yield item["name"]


async def tag_names(session: AsyncSession) -> list[str]:
    """Distinct normalised tag names in the cached `find_tags` envelopes, in first-seen order."""
    stmt = select(QlooCacheRow.envelope).where(
        QlooCacheRow.envelope["operation"].astext == "find_tags",
        QlooCacheRow.envelope["status"].astext.in_(("ok", "partial")),
    )
    seen: dict[str, None] = {}
    for envelope in (await session.scalars(stmt)).all():
        for name in _names_in(envelope):
            tag = norm_tag(name)
            if tag and len(tag) <= MAX_NAME_CHARS:
                seen.setdefault(tag)
    return list(seen)


async def index_tags(sessions: async_sessionmaker[AsyncSession], embedder: Embedder) -> int:
    """Embed the cached tag names not yet indexed; returns how many were added.

    A name whose embedding fails is skipped (logged) and tried again on the next start.
    """
    async with sessions() as session:
        names = await tag_names(session)
        done = set(
            await session.scalars(select(EmbeddingRow.ref_id).where(EmbeddingRow.kind == KIND))
        )
    todo = [n for n in names if n not in done][:MAX_INDEXED]
    added = 0
    for name in todo:
        try:
            vector = await embedder.embed(name)
        except Exception:
            log.warning("tag index: embedding %r failed", name, exc_info=True)
            continue
        async with sessions() as session:
            await session.execute(
                insert(EmbeddingRow)
                .values(kind=KIND, ref_id=name, embedding=vector)
                .on_conflict_do_nothing(constraint="uq_embeddings_kind_ref_id")
            )
            await session.commit()
        added += 1
    log.info("tag index: %d names cached, %d added", len(names), added)
    return added


async def nearest_tags(
    session: AsyncSession, vector: list[float], k: int = K, floor: float = FLOOR
) -> list[str]:
    """Up to `k` indexed tag names at `floor` cosine or above, nearest first."""
    distance = EmbeddingRow.embedding.cosine_distance(vector)
    stmt = (
        select(EmbeddingRow.ref_id)
        .where(EmbeddingRow.kind == KIND, 1 - distance >= floor)
        .order_by(distance)
        .limit(k)
    )
    return list((await session.scalars(stmt)).all())


_SPLIT = re.compile(r"[.,;:!?\n]+|\s+(?:and|but|or|plus)\s+", re.IGNORECASE)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def phrases_of(message: str) -> list[str]:
    """The message cut at punctuation and conjunctions: up to `MAX_PHRASES` distinct phrases."""
    out: list[str] = []
    for part in _SPLIT.split(_CONTROL.sub(" ", message)):
        phrase = " ".join(part.split())[:MAX_PHRASE_CHARS].strip()
        if phrase and phrase.casefold() not in {p.casefold() for p in out}:
            out.append(phrase)
        if len(out) >= MAX_PHRASES:
            break
    return out


@dataclass(frozen=True)
class PhraseTags:
    phrase: str
    tags: list[str]
    """Nearest first."""


async def map_phrases(
    session: AsyncSession, embedder: Embedder, message: str, k: int = K
) -> list[PhraseTags]:
    """Each phrase of `message` with up to `k` tag names at `FLOOR` or above; phrases with none
    near are dropped."""
    out: list[PhraseTags] = []
    for phrase in phrases_of(message):
        tags = await nearest_tags(session, await embedder.embed(phrase), k)
        if tags:
            out.append(PhraseTags(phrase, tags))
    return out
