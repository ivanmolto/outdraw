"""The semantic run cache (T36): a paraphrased `POST /runs` reuses the earlier run's result
(one set of Qloo calls), an unrelated request misses, and the 0.92 threshold is inclusive.

The canonical-text and threshold tests run offline; the route tests run the real app over a
migrated Postgres, with the default `HashingEmbedder` or a `QueueEmbedder` whose vectors set
the cosine exactly.
"""

# Its fixtures are imported, so the tests taking them as arguments "redefine" them.
# ruff: noqa: F811

from __future__ import annotations

import math
from typing import Any

import pytest
from conftest import RouterHarness
from fastapi.testclient import TestClient
from test_api import (  # noqa: F401  (fixtures)
    CONSTRAINTS,
    VENUE,
    Scripts,
    _sql,
    client,
    confirm_all,
    db_url,
    fixture,
    harness,
    profiled_venue,
    route,
    scripts,
)

from draw.db.models import EMBEDDING_DIM
from draw.embed import HashingEmbedder
from draw.embed.semantic_cache import THRESHOLD, canonical_text, is_hit
from draw.schemas import Constraints, VenueInput

PARAPHRASE_VENUE = {
    **VENUE,
    "city": "  new york ",
    "past_shows": list(reversed(VENUE["past_shows"])),
}


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def at_cosine(similarity: float, axis: int = 1) -> list[float]:
    """A unit vector whose cosine with `E0` (the first axis) is `similarity`, the rest of it
    along `axis`."""
    vector = [similarity] + [0.0] * (EMBEDDING_DIM - 1)
    vector[axis] = math.sqrt(1 - similarity**2)
    return vector


E0 = at_cosine(1.0)


class QueueEmbedder:
    """Hands out the given vectors in order, one per request."""

    def __init__(self, *vectors: list[float]) -> None:
        self.vectors = list(vectors)
        self.texts: list[str] = []

    async def embed(self, text: str) -> list[float]:
        self.texts.append(text)
        return self.vectors.pop(0)


def qloo_used(client: TestClient) -> int:
    used: int = client.get("/limits").json()["qloo"]["used"]
    return used


def run(client: TestClient, venue_id: str, message: str, **constraints: Any) -> dict[str, Any]:
    body = {
        "venue_id": venue_id,
        "constraints": {**CONSTRAINTS.model_dump(), **constraints},
        "message": message,
    }
    res = client.post("/runs", json=body)
    assert res.status_code == 200, res.text
    out: dict[str, Any] = res.json()
    return out


# --- offline ------------------------------------------------------------------------------


async def test_paraphrase_has_the_same_canonical_text() -> None:
    venue = VenueInput.model_validate(VENUE)
    paraphrase = VenueInput.model_validate(PARAPHRASE_VENUE)
    tags = CONSTRAINTS.model_copy(update={"include_tags": ["Jazz", "hip hop"]})
    respelled = CONSTRAINTS.model_copy(update={"include_tags": ["Hip-Hop", "jazz ", "JAZZ"]})

    text = canonical_text(venue, tags)
    assert text == canonical_text(paraphrase, respelled)
    assert "past acts: synthetic entity 3; unknown act" in text
    embedder = HashingEmbedder()
    first, second = await embedder.embed(text), await embedder.embed(text)
    assert len(first) == EMBEDDING_DIM and first == second, "deterministic"
    assert is_hit(cosine(first, await embedder.embed(canonical_text(paraphrase, respelled))))


async def test_unrelated_request_is_below_the_threshold() -> None:
    venue = VenueInput.model_validate(VENUE)
    other = VenueInput.model_validate(
        {**VENUE, "city": "Chicago", "past_shows": [{"act": "Heavy Band", "date": "2026-05-01"}]}
    )
    metal = Constraints.model_validate({**CONSTRAINTS.model_dump(), "include_tags": ["metal"]})
    embedder = HashingEmbedder()
    a = await embedder.embed(canonical_text(venue, CONSTRAINTS))
    b = await embedder.embed(canonical_text(other, metal))
    assert not is_hit(cosine(a, b))


def test_threshold_is_inclusive() -> None:
    assert THRESHOLD == 0.92
    assert is_hit(0.92) and is_hit(1.0)
    assert not is_hit(0.9199)
    assert is_hit(cosine(E0, at_cosine(0.92)))
    assert not is_hit(cosine(E0, at_cosine(0.919)))


# --- over the API -------------------------------------------------------------------------


def test_paraphrased_requests_share_one_set_of_qloo_calls(
    client: TestClient, scripts: Scripts
) -> None:
    venue_id = profiled_venue(client)
    scripts.pipeline()
    first = run(client, venue_id, "Thursday jazz.", include_tags=["Jazz", "hip hop"])
    used = qloo_used(client)
    models = len(scripts.models)
    assert first["result"]["cache_source"] is None
    assert client.get(f"/runs/{first['run_id']}/calls").json(), "the first run called Qloo"

    second = run(
        client, venue_id, "Jazz on a Thursday night, please", include_tags=["hip-hop", "jazz"]
    )
    assert qloo_used(client) == used, "no Qloo call for the paraphrase"
    assert len(scripts.models) == models, "no model built for the paraphrase"
    assert second["run_id"] != first["run_id"] and second["status"] == "ok"
    assert second["result"]["cache_source"] == "semantic"
    assert second["result"]["cached_from"] == first["run_id"]
    assert second["result"]["shortlist"] == first["result"]["shortlist"]
    assert second["constraints"]["include_tags"] == ["hip-hop", "jazz"], "its own constraints"
    assert set(second["token_usage"].values()) == {0}
    assert client.get(f"/runs/{second['run_id']}").json() == second

    entity_id = second["result"]["shortlist"][0]["entity_id"]
    evidence = client.get(f"/runs/{second['run_id']}/candidates/{entity_id}/evidence")
    assert evidence.status_code == 200 and evidence.json()["run_id"] == second["run_id"]


@pytest.mark.parametrize(
    "change",
    [
        {"include_tags": ["metal"]},
        {"exclude_tags": ["tribute"]},
        {"max_candidates": 10},
        # Not a tier change: the fixture pool is all emerging, so an established request
        # finds nothing now that the model is held to the booker's tier.
        {"exclude_recent_months": 24},
    ],
)
def test_unrelated_request_misses(
    client: TestClient, scripts: Scripts, change: dict[str, Any]
) -> None:
    venue_id = profiled_venue(client)
    scripts.pipeline()
    first = run(client, venue_id, "Thursday jazz.")
    models = len(scripts.models)

    second = run(client, venue_id, "Thursday jazz.", **change)
    assert second["result"]["cache_source"] is None, "a real change is a new run"
    assert len(scripts.models) == models + 1
    assert second["result"]["shortlist"] and second["run_id"] != first["run_id"]


def test_other_venue_misses(client: TestClient, scripts: Scripts) -> None:
    first_venue, second_venue = profiled_venue(client), profiled_venue(client)
    scripts.pipeline()
    run(client, first_venue, "Thursday jazz.")
    assert run(client, second_venue, "Thursday jazz.")["result"]["cache_source"] is None


def test_threshold_boundary(client: TestClient, scripts: Scripts) -> None:
    deps = client.app.state.deps  # type: ignore[attr-defined]
    deps.embedder = QueueEmbedder(E0, at_cosine(0.919, axis=2), at_cosine(0.92))
    venue_id = profiled_venue(client)
    scripts.pipeline()
    first = run(client, venue_id, "Thursday jazz.")

    below = run(client, venue_id, "Thursday jazz.")
    assert below["result"]["cache_source"] is None, "0.919 misses"
    at = run(client, venue_id, "Thursday jazz.")
    assert at["result"]["cache_source"] == "semantic", "0.92 hits"
    # The 0.919 run stored its own vector too, off at right angles to the 0.92 request.
    assert at["result"]["cached_from"] == first["run_id"]
    assert len(deps.embedder.texts) == 3


def test_edited_past_shows_miss(client: TestClient, scripts: Scripts) -> None:
    """An update drops the profile, so the rebuilt profile already misses; the venue guard
    also covers an edit that bypasses the route (precompute, a hand edit)."""
    venue_id = profiled_venue(client)
    scripts.pipeline()
    run(client, venue_id, "Thursday jazz.")
    shows = [*VENUE["past_shows"], {"act": "Synthetic Entity 3", "date": "2026-09-20"}]
    assert (
        client.post("/venues", json={**VENUE, "id": venue_id, "past_shows": shows}).status_code
        == 201
    )
    assert client.post(f"/venues/{venue_id}/resolve").status_code == 200
    confirm_all(client, venue_id)
    assert client.post(f"/venues/{venue_id}/profile").status_code == 200
    assert run(client, venue_id, "Thursday jazz.")["result"]["cache_source"] is None


def test_rebuilt_profile_from_the_same_data_still_hits(
    client: TestClient, scripts: Scripts, harness: RouterHarness
) -> None:
    """T39: a rebuild that reads the same envelopes (cache hits) keeps the stored profile."""
    venue_id = profiled_venue(client)
    scripts.pipeline()
    first = run(client, venue_id, "Thursday jazz.")
    spawned = harness.spawns
    before = client.get(f"/venues/{venue_id}").json()["profile"]
    assert client.post(f"/venues/{venue_id}/profile").status_code == 200
    assert client.get(f"/venues/{venue_id}").json()["profile"] == before, "the stored row stays"
    assert harness.spawns == spawned, "the rebuild was cache hits only"
    second = run(client, venue_id, "Thursday jazz.")
    assert second["result"]["cache_source"] == "semantic"
    assert second["result"]["cached_from"] == first["run_id"]


def test_rebuilt_profile_with_new_data_misses(
    client: TestClient, scripts: Scripts, harness: RouterHarness, db_url: str
) -> None:
    """The envelopes expired and Qloo answers differently (a partial related-artists list):
    the profile changes, so the run is new."""
    venue_id = profiled_venue(client)
    scripts.pipeline()
    run(client, venue_id, "Thursday jazz.")
    _sql(db_url, "TRUNCATE qloo_cache")
    harness.router = lambda workflow, payload: (
        fixture("recommend", "derived_partial")
        if workflow == "recommend" and payload["target_type"] == "artist"
        else route(workflow, payload)
    )
    assert client.post(f"/venues/{venue_id}/profile").status_code == 200
    assert client.get(f"/venues/{venue_id}").json()["profile"]["data_quality"] == "partial"
    assert run(client, venue_id, "Thursday jazz.")["result"]["cache_source"] is None


def test_repeating_the_venue_flow_spends_nothing(
    client: TestClient, scripts: Scripts, harness: RouterHarness
) -> None:
    """T39, as the web replays it: the venue saved again by id, its matches confirmed again,
    the profile rebuilt, then the run: no Qloo spawn, no model, the run from the cache."""
    venue_id = profiled_venue(client)
    scripts.pipeline()
    first = run(client, venue_id, "Thursday jazz.")
    spawned, models = harness.spawns, len(scripts.models)

    assert client.post("/venues", json={**VENUE, "id": venue_id}).status_code == 201
    assert client.post(f"/venues/{venue_id}/resolve").status_code == 200
    confirm_all(client, venue_id)
    assert client.post(f"/venues/{venue_id}/profile").status_code == 200
    second = run(client, venue_id, "Thursday jazz.")
    assert harness.spawns == spawned and len(scripts.models) == models
    assert second["result"]["cache_source"] == "semantic"
    assert second["result"]["cached_from"] == first["run_id"]


def test_failing_embedder_runs_uncached(client: TestClient, scripts: Scripts) -> None:
    class Broken:
        async def embed(self, text: str) -> list[float]:
            raise ConnectionError("embedder down")

    client.app.state.deps.embedder = Broken()  # type: ignore[attr-defined]
    venue_id = profiled_venue(client)
    scripts.pipeline()
    assert run(client, venue_id, "Thursday jazz.")["status"] == "ok"
    second = run(client, venue_id, "Thursday jazz.")
    assert second["status"] == "ok" and second["result"]["cache_source"] is None
