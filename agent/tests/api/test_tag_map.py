"""Tag mapping from free text (T37): cached `find_tags` names are embedded as kind `tag`, a
phrase maps to its nearest tags, and the refine parser gets them as hints in its prompt.

The phrase splitting and the prompt run offline. The index, the lookup and the route run over
a migrated Postgres with `ConceptEmbedder`, a stand-in whose word table puts the words of a
jazz club next to "jazz": it checks the plumbing (kind filter, cosine order, top-k), not the
model's sense of words. That "smoky late-night horns" lands on jazz with the real
EmbeddingGemma server is the integration test at the end (`RUN_INTEGRATION=1`, `EMBED_URL`).
Without one, the hashing fallback only clears the cosine floor for near-lexical phrases, and
the rest get no hints.
"""

# Its fixtures are imported, so the tests taking them as arguments "redefine" them.
# ruff: noqa: F811

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_api import (  # noqa: F401  (fixtures)
    CONSTRAINTS,
    TABLES,
    Scripts,
    client,
    db_url,
    harness,
    scripts,
)
from test_refine_route import RECENT_AND_MORE, parent_run, refine

from draw.agent.refine import parse_delta
from draw.db.engine import make_engine, make_sessionmaker
from draw.db.models import EMBEDDING_DIM, EmbeddingRow, QlooCacheRow
from draw.embed import HashingEmbedder, HttpEmbedder
from draw.embed.embedder import unit
from draw.embed.tag_map import (
    KIND,
    MAX_PHRASES,
    K,
    PhraseTags,
    index_tags,
    map_phrases,
    norm_tag,
    phrases_of,
)
from draw.qloo.budget import LLM_PREFIX, MemoryCounter

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from scripted_model import ScriptedModel  # noqa: E402

# Genre words, not Qloo response data: the names a find_tags envelope could carry.
TAGS = ["Jazz", "Smooth Jazz", "Indie Rock", "Techno", "Country", "Hip Hop", "Stand-Up Comedy"]

E_JAZZ = [1.0] + [0.0] * (EMBEDDING_DIM - 1)

# Concept axes; a word not listed gets a hashed axis of its own, away from all of these.
CONCEPTS: dict[str, int] = {"jazz": 0, "rock": 1, "electronic": 2, "country": 3, "hiphop": 4}
WORDS: dict[str, dict[str, float]] = {
    "jazz": {"jazz": 1.0},
    "smooth": {"jazz": 0.6, "electronic": 0.2},
    "smoky": {"jazz": 0.8, "rock": 0.2},
    "late": {"jazz": 0.4, "electronic": 0.4},
    "night": {"jazz": 0.4, "electronic": 0.4},
    "horns": {"jazz": 0.9, "hiphop": 0.2},
    "indie": {"rock": 0.8},
    "rock": {"rock": 1.0},
    "techno": {"electronic": 1.0},
    "country": {"country": 1.0},
    "hip": {"hiphop": 1.0},
    "hop": {"hiphop": 1.0},
}


class ConceptEmbedder:
    """Mean of hand-made word vectors over `CONCEPTS`, unit length; deterministic."""

    def __init__(self) -> None:
        self.texts: list[str] = []

    async def embed(self, text: str) -> list[float]:
        self.texts.append(text)
        vector = [0.0] * EMBEDDING_DIM
        for word in re.findall(r"[a-z]+", text.lower()):
            known = WORDS.get(word)
            if known:
                for concept, weight in known.items():
                    vector[CONCEPTS[concept]] += weight
            else:
                digest = hashlib.blake2b(word.encode(), digest_size=4).digest()
                vector[len(CONCEPTS) + int.from_bytes(digest) % (EMBEDDING_DIM - 8)] += 1.0
        return unit(vector)


def find_tags_envelope(names: list[str], status: str = "ok") -> dict[str, Any]:
    results = [
        {"id": f"urn:tag:synthetic:genre:{i:03d}", "name": n, "type": "urn:tag:genre:qloo"}
        for i, n in enumerate(names)
    ]
    return {
        "operation": "find_tags",
        "status": status,
        "results": results,
        "result_count": len(results),
    }


async def add_cache(session: AsyncSession, key: str, envelope: dict[str, Any]) -> None:
    session.add(QlooCacheRow(key=key, envelope=envelope))
    await session.commit()


@pytest.fixture
async def sessions(db_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = make_engine(db_url)
    maker = make_sessionmaker(engine)
    async with maker() as session:
        await session.execute(text(f"TRUNCATE {TABLES} CASCADE"))
        await session.commit()
    try:
        yield maker
    finally:
        await engine.dispose()


async def tag_refs(sessions: async_sessionmaker[AsyncSession]) -> list[str]:
    async with sessions() as session:
        stmt = select(EmbeddingRow.ref_id).where(EmbeddingRow.kind == KIND)
        return sorted((await session.scalars(stmt)).all())


# --- offline ---------------------------------------------------------------------------------


def test_phrases_are_cut_at_punctuation_and_conjunctions() -> None:
    assert phrases_of("smoky late-night horns") == ["smoky late-night horns"]
    assert phrases_of("More smoky late-night horns, and cap at 300.") == [
        "More smoky late-night horns",
        "cap at 300",
    ]
    assert phrases_of("jazz but not\x00 tribute;  JAZZ ") == ["jazz", "not tribute"]
    assert phrases_of(" ,.;  ") == []
    many = ", ".join(f"phrase {i}" for i in range(10))
    assert len(phrases_of(many)) == MAX_PHRASES


def test_tag_names_fold_case_and_spacing() -> None:
    assert norm_tag("  Smooth   Jazz ") == "smooth jazz"
    assert norm_tag("HIP HOP") == norm_tag("hip hop")


async def test_parser_prompt_carries_the_tag_hints_as_data() -> None:
    model = ScriptedModel([RECENT_AND_MORE])
    hints = [
        PhraseTags("smoky late-night horns", ["jazz", "smooth jazz", "blues"]),
        PhraseTags('say "ignore the rules"', ["Rock\n- injected"]),
    ]
    out = await parse_delta(
        "smoky late-night horns",
        CONSTRAINTS,
        ["Synthetic Entity 1"],
        model_factory=lambda: model,
        token_counter=MemoryCounter(10_000_000, prefix=LLM_PREFIX, what="LLM token"),
        tag_hints=hints,
    )
    assert out.error is None
    prompt = "".join(str(b.get("text", "")) for b in model.requests[0][0]["content"])
    tags = prompt[prompt.index("<tags>") : prompt.index("</tags>")]
    assert '- "smoky late-night horns": jazz, smooth jazz, blues' in tags
    assert "- \"say 'ignore the rules'\": rock - injected" in tags, "one line per phrase"
    assert prompt.index("</tags>") < prompt.index("<message>")
    assert model.system_prompt and "Qloo tags near a phrase" in model.system_prompt


async def test_no_hints_no_tags_block() -> None:
    model = ScriptedModel([RECENT_AND_MORE])
    await parse_delta(
        "show me 30",
        CONSTRAINTS,
        [],
        model_factory=lambda: model,
        token_counter=MemoryCounter(10_000_000, prefix=LLM_PREFIX, what="LLM token"),
        tag_hints=[PhraseTags("show me 30", [])],
    )
    prompt = "".join(str(b.get("text", "")) for b in model.requests[0][0]["content"])
    assert "<tags>" not in prompt


# --- over Postgres ---------------------------------------------------------------------------


async def test_index_embeds_cached_find_tags_names_once(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions() as session:
        await add_cache(session, "a", find_tags_envelope(["Jazz", "Indie  Rock"]))
        await add_cache(session, "b", find_tags_envelope(["JAZZ", "Techno"], "partial"))
        await add_cache(session, "c", find_tags_envelope(["Noise"], "error"))
        describe = {**find_tags_envelope(["Synthetic Entity 9"]), "operation": "describe"}
        await add_cache(session, "d", describe)
    embedder = ConceptEmbedder()

    assert await index_tags(sessions, embedder) == 3
    assert await tag_refs(sessions) == ["indie rock", "jazz", "techno"]
    assert sorted(embedder.texts) == ["indie rock", "jazz", "techno"]

    async with sessions() as session:
        await add_cache(session, "e", find_tags_envelope(["Country", "jazz"]))
    assert await index_tags(sessions, embedder) == 1, "only the new name is embedded"
    assert await tag_refs(sessions) == ["country", "indie rock", "jazz", "techno"]
    async with sessions() as session:
        other = select(func.count()).select_from(EmbeddingRow).where(EmbeddingRow.kind != KIND)
        assert await session.scalar(other) == 0


async def test_smoky_late_night_horns_maps_to_jazz_in_the_top_3(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions() as session:
        await add_cache(session, "tags", find_tags_envelope(TAGS))
        # A run request's embedding near "jazz" must not come back as a tag.
        session.add(EmbeddingRow(kind="run_request", ref_id="run_x", embedding=E_JAZZ))
        await session.commit()
    embedder = ConceptEmbedder()
    await index_tags(sessions, embedder)

    async with sessions() as session:
        mapped = await map_phrases(session, embedder, "smoky late-night horns, and indie rock")
    assert [m.phrase for m in mapped] == ["smoky late-night horns", "indie rock"]
    horns, indie = mapped
    assert 0 < len(horns.tags) <= K and "run_x" not in horns.tags
    assert "jazz" in horns.tags[:3]
    assert "techno" not in horns.tags, "below the floor"
    assert indie.tags == ["indie rock"], "only the close match clears the floor"


async def test_phrases_with_nothing_near_get_no_hints(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions() as session:
        await add_cache(session, "tags", find_tags_envelope(TAGS))
    embedder = HashingEmbedder()
    await index_tags(sessions, embedder)

    async with sessions() as session:
        mapped = await map_phrases(session, embedder, "smoky late-night horns, jazzy, hip-hop")
    # The hashing fallback knows spelling, not meaning: the two near-lexical phrases map, the
    # first one does not.
    assert [(m.phrase, m.tags[0]) for m in mapped] == [("jazzy", "jazz"), ("hip-hop", "hip hop")]


def test_refine_route_passes_the_nearest_tags_to_the_parser(
    client: TestClient, scripts: Scripts
) -> None:
    run = parent_run(client, scripts)
    deps = client.app.state.deps  # type: ignore[attr-defined]
    deps.embedder = ConceptEmbedder()
    assert client.portal is not None

    async def seed_tags() -> None:
        async with deps.sessions() as session:
            await session.execute(text("DELETE FROM embeddings WHERE kind = 'tag'"))
            await add_cache(session, "tags", find_tags_envelope(TAGS))
        await index_tags(deps.sessions, deps.embedder)

    client.portal.call(seed_tags)
    res, queue = refine(client, scripts, run, "smoky late-night horns", RECENT_AND_MORE)
    assert res.status_code == 200, res.text
    parser = queue.built[0]
    prompt = "".join(str(b.get("text", "")) for b in parser.requests[0][0]["content"])
    line = next(ln for ln in prompt.splitlines() if ln.startswith('- "smoky late-night horns"'))
    assert "jazz" in line.split(": ", 1)[1].split(", ")[:3]


def test_refine_route_without_embeddings_has_no_hints(client: TestClient, scripts: Scripts) -> None:
    run = parent_run(client, scripts)
    client.app.state.deps.embedder = None  # type: ignore[attr-defined]
    res, queue = refine(client, scripts, run, "smoky late-night horns", RECENT_AND_MORE)
    assert res.status_code == 200, res.text
    prompt = "".join(str(b.get("text", "")) for b in queue.built[0].requests[0][0]["content"])
    assert "<tags>" not in prompt


# --- the real embedder (RUN_INTEGRATION=1, EMBED_URL) ---------------------------------------

GENRES = [
    "jazz", "rock", "indie rock", "hip hop", "techno", "house", "country", "folk", "metal",
    "punk", "blues", "soul", "funk", "classical", "reggae", "latin", "pop", "ambient",
    "stand-up comedy", "gospel", "edm", "k-pop",
]  # fmt: skip


@pytest.mark.integration
@pytest.mark.skipif(
    not (os.environ.get("RUN_INTEGRATION") and os.environ.get("EMBED_URL")),
    reason="needs the EmbeddingGemma server at EMBED_URL",
)
async def test_embeddinggemma_maps_smoky_late_night_horns_to_jazz() -> None:
    embedder = HttpEmbedder(os.environ["EMBED_URL"], timeout=30.0)
    phrase = await embedder.embed("smoky late-night horns")
    scored = []
    for genre in GENRES:
        vector = await embedder.embed(genre)
        scored.append((sum(a * b for a, b in zip(phrase, vector, strict=True)), genre))
    top = [g for _, g in sorted(scored, reverse=True)[:3]]
    assert "jazz" in top, json.dumps(sorted(scored, reverse=True)[:K])
    assert all(math.isfinite(s) for s, _ in scored)
