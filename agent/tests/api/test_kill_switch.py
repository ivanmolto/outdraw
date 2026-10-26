"""T33: the `LIVE_MODE` kill switch and the fallback seed a refused call names: runs and
refinements, and every other route that would spend (resolve, profile, the first brief or
compare), while what is stored is still served.

The parsing and the closest-seed choice run offline; the route tests use the real app over
a migrated Postgres (the `test_api` fixtures, Docker), with `LIVE_MODE` flipped per test.
"""

# Its fixtures are imported, so the tests taking them as arguments "redefine" them.
# ruff: noqa: F811

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from test_api import (  # noqa: F401  (fixtures)
    Scripts,
    _sql,
    client,
    confirm_all,
    create_venue,
    db_url,
    harness,
    post_run,
    profiled_venue,
    scripts,
)
from test_brief_route import answer as brief_answer
from test_compare import ANSWER as COMPARE_ANSWER

from draw import main
from draw.api.live import closest_seed, live_mode
from draw.db.engine import async_url
from draw.qloo.harness import RuntimeVersions
from draw.ranking.evidence import REF_TOP_REASON
from draw.seeds import load_seeds

SEEDS = load_seeds()
FIRST = SEEDS[0].slug


@pytest.fixture(autouse=True)
def live_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test starts with the switch unset (on), whatever the shell has."""
    monkeypatch.delenv("LIVE_MODE", raising=False)


# --- LIVE_MODE ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "on"), [(None, True), ("", True), ("on", True), ("ON", True), (" off ", False)]
)
def test_live_mode_values(monkeypatch: pytest.MonkeyPatch, value: str | None, on: bool) -> None:
    if value is not None:
        monkeypatch.setenv("LIVE_MODE", value)
    assert live_mode() is on


@pytest.mark.parametrize("value", ["of", "false", "0", "maybe"])
def test_invalid_live_mode_fails_the_start(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("LIVE_MODE", value)
    with pytest.raises(ValueError, match="LIVE_MODE must be"):
        live_mode()
    monkeypatch.setattr(main, "probe_versions", lambda: RuntimeVersions("0.0.0", "v22.19.0"))

    async def no_deps() -> None:
        return None

    with pytest.raises(ValueError), TestClient(main.create_app(no_deps)):
        pass


# --- the closest seed --------------------------------------------------------------------


def test_a_seed_venue_falls_back_to_itself() -> None:
    for seed in SEEDS:
        assert closest_seed(SEEDS, venue_id=f"ven_seed_{seed.slug}", city="Elsewhere") == seed.slug


@pytest.mark.parametrize(
    ("venue", "expected"),
    [
        ({"city": "New York", "country": "United States", "capacity": 150}, "brooklyn-indie"),
        ({"city": "manhattan", "country": "US", "capacity": 900}, "brooklyn-indie"),
        ({"city": "New Orleans", "country": "United States", "capacity": 1200}, "nola-jazz"),
        ({"city": "London", "country": "United Kingdom", "capacity": 80}, "london-comedy"),
        ({"city": "Manchester", "country": "United Kingdom", "capacity": 2000}, "london-comedy"),
        # Same country, no city match: shared tags first, then the nearest capacity.
        (
            {"city": "Chicago", "country": "United States", "capacity": 400, "tags": ["jazz"]},
            "nola-jazz",
        ),
        ({"city": "Chicago", "country": "United States", "capacity": 420}, "brooklyn-indie"),
        ({"city": "Chicago", "country": "United States", "capacity": 140}, "nola-jazz"),
    ],
)
def test_closest_seed(venue: dict[str, Any], expected: str) -> None:
    got = closest_seed(
        SEEDS,
        venue_id="ven_x",
        city=venue["city"],
        country=venue["country"],
        capacity=venue["capacity"],
        include_tags=venue.get("tags"),
    )
    assert got == expected


def test_nothing_known_is_the_first_seed() -> None:
    assert closest_seed(SEEDS) == FIRST
    assert closest_seed(SEEDS, venue_id="ven_missing") == FIRST


# --- the routes --------------------------------------------------------------------------


def _count(db_url: str, table: str) -> int:
    """Rows in `table`, over an engine of its own (the app's is bound to its event loop)."""

    async def count() -> int:
        engine = create_async_engine(async_url(db_url))
        try:
            async with engine.connect() as conn:
                return int((await conn.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one())
        finally:
            await engine.dispose()

    return asyncio.run(count())


def _ready_seed(db_url: str, slug: str) -> None:
    """A precomputed seed as `scripts/precompute.py` leaves it, as far as readiness goes."""
    _sql(
        db_url,
        "INSERT INTO venues (id, name, city, country, capacity) VALUES "
        f"('ven_seed_{slug}', 'Synthetic Seed', 'Nowhere', 'Nowhere', 100)",
    )
    _sql(
        db_url,
        "INSERT INTO runs (id, venue_id, constraints, status, is_seed, seed_slug) VALUES "
        f"('run_seed_{slug}', 'ven_seed_{slug}', '{{}}'::jsonb, 'ok', true, '{slug}')",
    )


def test_run_refused_when_off(
    client: TestClient, db_url: str, scripts: Scripts, monkeypatch: pytest.MonkeyPatch
) -> None:
    venue_id = profiled_venue(client)  # New York, 150
    calls = _count(db_url, "qloo_calls")
    monkeypatch.setenv("LIVE_MODE", "off")

    res = post_run(client, venue_id)

    assert res.status_code == 503
    body = res.json()
    assert body["reason"] == "live_mode_off" and body["fallback_seed"] == "brooklyn-indie"
    assert _count(db_url, "runs") == 0, "nothing stored"
    assert _count(db_url, "qloo_calls") == calls and scripts.models == [], "nothing spent"
    assert client.get("/limits").status_code == 200, "reads still answer"


def test_refused_before_the_venue_is_checked(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unknown venue or run is a 503 with the first seed, not a 404: off means off."""
    monkeypatch.setenv("LIVE_MODE", "off")
    res = post_run(client, "ven_missing")
    assert res.status_code == 503 and res.json()["fallback_seed"] == FIRST
    res = client.post("/runs/run_missing/refine", json={"message": "bigger acts"})
    assert res.status_code == 503 and res.json()["fallback_seed"] == FIRST


def test_refine_refused_when_off(
    client: TestClient, db_url: str, scripts: Scripts, monkeypatch: pytest.MonkeyPatch
) -> None:
    venue_id = profiled_venue(client)
    scripts.pipeline()
    parent = post_run(client, venue_id)
    assert parent.status_code == 200, parent.text
    built = len(scripts.models)
    monkeypatch.setenv("LIVE_MODE", "off")

    res = client.post(f"/runs/{parent.json()['run_id']}/refine", json={"message": "bigger acts"})

    assert res.status_code == 503
    assert res.json() == {
        "reason": "live_mode_off",
        "detail": "Live analysis is switched off (LIVE_MODE=off).",
        "fallback_seed": "brooklyn-indie",
    }
    assert _count(db_url, "runs") == 1 and len(scripts.models) == built, "nothing run"
    # The stored run still reads, and flipping back on runs again.
    assert client.get(f"/runs/{parent.json()['run_id']}").status_code == 200
    monkeypatch.setenv("LIVE_MODE", "on")
    assert post_run(client, venue_id).status_code == 200


def test_only_precomputed_seeds_are_fallbacks(
    client: TestClient, db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    venue_id = profiled_venue(client)
    _ready_seed(db_url, "london-comedy")
    monkeypatch.setenv("LIVE_MODE", "off")
    res = post_run(client, venue_id)
    assert res.status_code == 503 and res.json()["fallback_seed"] == "london-comedy"


def test_budget_429_names_the_seed(client: TestClient) -> None:
    venue_id = profiled_venue(client)
    counter = client.app.state.deps.gateways.counter  # type: ignore[attr-defined]
    asyncio.run(counter.reserve(300 - asyncio.run(counter.used())))
    res = post_run(client, venue_id)
    assert res.status_code == 429
    assert res.json()["reason"] == "budget" and res.json()["fallback_seed"] == "brooklyn-indie"


# --- the other routes that spend -----------------------------------------------------------


def _refused(res: Any, seed: str = "brooklyn-indie") -> None:
    assert res.status_code == 503, res.text
    assert res.json()["reason"] == "live_mode_off" and res.json()["fallback_seed"] == seed


def test_resolve_refused_only_with_acts_to_match(
    client: TestClient, db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    venue_id = create_venue(client)
    monkeypatch.setenv("LIVE_MODE", "off")
    _refused(client.post(f"/venues/{venue_id}/resolve"))
    assert _count(db_url, "qloo_calls") == 0 and _count(db_url, "resolutions") == 0

    monkeypatch.setenv("LIVE_MODE", "on")
    assert client.post(f"/venues/{venue_id}/resolve").status_code == 200
    calls = _count(db_url, "qloo_calls")
    # Everything stored: answered from the database, nothing to refuse.
    monkeypatch.setenv("LIVE_MODE", "off")
    assert client.post(f"/venues/{venue_id}/resolve").status_code == 200
    assert _count(db_url, "qloo_calls") == calls
    # Saving the venue and confirming matches never spend, so they stay open.
    confirm_all(client, venue_id)


def test_profile_refused_after_the_confirm_gate(
    client: TestClient, db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    venue_id = create_venue(client)
    assert client.post(f"/venues/{venue_id}/resolve").status_code == 200
    monkeypatch.setenv("LIVE_MODE", "off")
    assert client.post(f"/venues/{venue_id}/profile").status_code == 409, "confirm first"

    confirm_all(client, venue_id)
    calls = _count(db_url, "qloo_calls")
    _refused(client.post(f"/venues/{venue_id}/profile"))
    assert _count(db_url, "qloo_calls") == calls and _count(db_url, "crowd_profiles") == 0


def _live_run(client: TestClient, scripts: Scripts) -> tuple[str, str, str]:
    """A ranked live run: its id, its top act's entity id and name."""
    scripts.pipeline()
    res = post_run(client, profiled_venue(client))
    assert res.status_code == 200, res.text
    top = res.json()["result"]["shortlist"][0]
    return res.json()["run_id"], top["entity_id"], top["name"]


def test_first_compare_refused_cached_compare_served(
    client: TestClient, scripts: Scripts, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id, _, _ = _live_run(client, scripts)
    built = len(scripts.models)
    monkeypatch.setenv("LIVE_MODE", "off")
    _refused(client.get(f"/runs/{run_id}/compare"))
    assert len(scripts.models) == built, "no model call"

    monkeypatch.setenv("LIVE_MODE", "on")
    scripts.turns = [COMPARE_ANSWER]
    assert client.get(f"/runs/{run_id}/compare").json()["cached"] is False
    monkeypatch.setenv("LIVE_MODE", "off")
    cached = client.get(f"/runs/{run_id}/compare")
    assert cached.status_code == 200 and cached.json()["cached"] is True


def test_first_brief_refused_cached_brief_served(
    client: TestClient, scripts: Scripts, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id, entity_id, name = _live_run(client, scripts)
    path = f"/runs/{run_id}/candidates/{entity_id}/brief"
    built = len(scripts.models)
    monkeypatch.setenv("LIVE_MODE", "off")
    _refused(client.get(path))
    assert len(scripts.models) == built, "no model call"
    # The rest of the Soundcheck panel is reads: still served.
    assert client.get(f"/runs/{run_id}/candidates/{entity_id}/evidence").status_code == 200

    monkeypatch.setenv("LIVE_MODE", "on")
    scripts.turns = [brief_answer(name, [REF_TOP_REASON])]
    assert client.get(path).json()["cached"] is False
    monkeypatch.setenv("LIVE_MODE", "off")
    cached = client.get(path)
    assert cached.status_code == 200 and cached.json()["cached"] is True
