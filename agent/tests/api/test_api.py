"""The HTTP surface end to end: venues → resolve → confirm → profile → runs.

Real FastAPI app and migrated Postgres (testcontainers); Qloo is the fake harness
(`RouterHarness` for `qloo exec`, `fake_mcp_server.py` for the agent's `qloo mcp`) and the
model is `ScriptedModel`, so every route runs its real code path offline.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from conftest import AGENT_DIR, FIXTURES_DIR, RouterHarness
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from tenacity import wait_none

from draw import main
from draw.api.auth import HEADER
from draw.api.deps import Deps
from draw.db.engine import async_url
from draw.qloo import exec_client
from draw.qloo.budget import LLM_PREFIX, MemoryCounter
from draw.qloo.config import QlooConfig
from draw.qloo.harness import RuntimeVersions
from draw.schemas import Constraints, CrowdProfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from scripted_model import Results, ScriptedModel, Turn  # noqa: E402

FAKE_SERVER = Path(__file__).resolve().parents[1] / "qloo" / "fake_mcp_server.py"
FAKE_QLOO_KEY = "-".join(["fake", "qloo", "key", "0" * 12])
INTERNAL_KEY = "test-internal-key"
TABLES = "venues, crowd_profiles, runs, candidates, qloo_cache, qloo_calls, resolutions, embeddings"

VENUE: dict[str, Any] = {
    "name": "Blue Note Jazz Club",
    "city": "New York",
    "country": "United States",
    "capacity": 150,
    "past_shows": [
        {"act": "Synthetic Entity 3", "date": "2026-06-12", "tickets_sold": 150, "capacity": 150},
        {"act": "Unknown Act", "date": "2026-07-03", "tickets_sold": 90, "capacity": 150},
    ],
}
VENUE_KEY = "venue:Blue Note Jazz Club"
# The describe fixture's only entity: an exact match for "Synthetic Entity 3", a
# non-matching candidate for "Unknown Act".
ENTITY_ID = "746CD02C-8299-5FBF-9C15-615CBD354946"
CONSTRAINTS = Constraints(
    include_tags=[],
    exclude_tags=[],
    size_tier="emerging",
    exclude_recent_months=0,
    max_candidates=20,
)
PROTECTED = [
    ("post", "/venues"),
    ("get", "/venues/ven_x"),
    ("post", "/venues/ven_x/resolve"),
    ("post", "/venues/ven_x/confirm"),
    ("post", "/venues/ven_x/profile"),
    ("post", "/runs"),
    ("get", "/runs/run_x"),
    ("get", "/runs/run_x/candidates/E/evidence"),
    ("get", "/runs/run_x/candidates/E/brief"),
    ("patch", "/runs/run_x/candidates/E"),
    ("get", "/runs/run_x/calls"),
    ("get", "/runs/run_x/compare"),
    ("post", "/runs/run_x/refine"),
    ("get", "/limits"),
]


def fixture(workflow: str, name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES_DIR / workflow / f"{name}.json").read_text())
    return data


def route(workflow: str, payload: dict[str, Any]) -> dict[str, Any]:
    """`qloo exec` answers for resolve (describe) and the crowd profile."""
    if workflow == "describe":
        name = str(payload.get("entity", ""))
        return fixture(
            "describe", "blue_note_jazz_club" if name.startswith("Blue Note") else "radiohead"
        )
    if workflow == "recommend":
        target = {"artist": "artists", "place": "places", "movie": "movies"}
        return fixture("recommend", f"crowd_{target.get(payload['target_type'], 'brands')}")
    if workflow == "compare_audiences":
        return fixture("compare_audiences", "crowd_genres")
    if workflow == "audience_demographics":
        return fixture("audience_demographics", "crowd_demographics_1")
    raise AssertionError(f"unexpected qloo exec {workflow}")


# --- the scripted model ----------------------------------------------------------------


class Scripts:
    """`Deps.model_factory`: a fresh `ScriptedModel` per run, playing `self.turns`."""

    def __init__(self) -> None:
        self.turns: list[Any] = []
        self.models: list[ScriptedModel] = []

    def __call__(self) -> ScriptedModel:
        model = ScriptedModel(self.turns)
        self.models.append(model)
        return model

    def prompt(self) -> str:
        return "".join(str(b.get("text", "")) for b in self.models[-1].requests[0][0]["content"])

    def pipeline(self) -> None:
        """generate (on the stored profile) → score_and_rank → the final JSON."""
        self.turns = [self._generate, _rank, _answer]

    def _generate(self, results: Results) -> Turn:
        match = re.search(r"profile_id (prof_\w+)", self.prompt())
        assert match, "the stored profile's id is in the prompt"
        constraints = CONSTRAINTS.model_dump_json()
        return [
            (
                "generate_candidates",
                {"profile_id": match.group(1), "constraints_json": constraints},
            )
        ]


def _latest(results: Results, key: str) -> Any:
    return next(r[key] for r in reversed(results) if key in r)


def _rank(results: Results) -> Turn:
    return [("score_and_rank", {"run_id": _latest(results, "run_id")})]


def _answer(results: Results) -> Turn:
    ranked = results[-1]
    payload = {"shortlist": ranked["shortlist"], "notes": [], "data_quality": "ok"}
    return json.dumps(payload)


# --- fixtures --------------------------------------------------------------------------


def _sql(url: str, statement: str) -> None:
    async def run() -> None:
        engine = create_async_engine(async_url(url))
        try:
            async with engine.begin() as conn:
                await conn.execute(text(statement))
        finally:
            await engine.dispose()

    asyncio.run(run())


@pytest.fixture(scope="module")
def db_url(pg_url: str) -> str:
    cfg = Config(str(AGENT_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(AGENT_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", pg_url)
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")
    return pg_url


@pytest.fixture
def scripts() -> Scripts:
    return Scripts()


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> RouterHarness:
    fake = RouterHarness(route)
    monkeypatch.setattr(exec_client, "create_subprocess_exec", fake)
    return fake


@pytest.fixture
def client(
    db_url: str, scripts: Scripts, harness: RouterHarness, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    _sql(db_url, f"TRUNCATE {TABLES} CASCADE")
    monkeypatch.setenv("INTERNAL_API_KEY", INTERNAL_KEY)
    monkeypatch.setenv("QLOO_API_KEY", FAKE_QLOO_KEY)
    for name in ("DATABASE_URL", "REDIS_URL", "QLOO_RECORD", "LLM_DAILY_TOKEN_CAP", "LIVE_MODE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(main, "probe_versions", lambda: RuntimeVersions("0.0.0", "v22.19.0"))

    async def deps() -> Deps:
        return await Deps.create(
            db_url,
            config=QlooConfig(qloo_bin="/fake/qloo", api_key=FAKE_QLOO_KEY, daily_cap=300),
            qloo_counter=MemoryCounter(300),
            llm_counter=MemoryCounter(
                10_000_000, prefix=LLM_PREFIX, what="LLM token", charge_over=True
            ),
            model_factory=scripts,
            mcp_command=[sys.executable, str(FAKE_SERVER)],
            retry_wait=wait_none(),
        )

    with TestClient(main.create_app(deps), headers={HEADER: INTERNAL_KEY}) as test_client:
        yield test_client


def create_venue(client: TestClient, **overrides: Any) -> str:
    res = client.post("/venues", json={**VENUE, **overrides})
    assert res.status_code == 201, res.text
    venue_id: str = res.json()["id"]
    return venue_id


def items_by_query(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {i["query"]: i for i in body["items"]}


def confirm_all(client: TestClient, venue_id: str) -> None:
    for query, chosen in ((VENUE_KEY, None), ("Unknown Act", ENTITY_ID)):
        res = client.post(
            f"/venues/{venue_id}/confirm", json={"query": query, "chosen_entity_id": chosen}
        )
        assert res.status_code == 200, res.text


def profiled_venue(client: TestClient) -> str:
    venue_id = create_venue(client)
    assert client.post(f"/venues/{venue_id}/resolve").status_code == 200
    confirm_all(client, venue_id)
    res = client.post(f"/venues/{venue_id}/profile")
    assert res.status_code == 200, res.text
    return venue_id


def post_run(client: TestClient, venue_id: str, message: str = "Thursday jazz.") -> Any:
    body = {"venue_id": venue_id, "constraints": CONSTRAINTS.model_dump(), "message": message}
    return client.post("/runs", json=body)


# --- auth --------------------------------------------------------------------------------


@pytest.mark.parametrize(("method", "path"), PROTECTED)
def test_401_without_or_with_wrong_header(client: TestClient, method: str, path: str) -> None:
    missing = client.request(method, path, headers={HEADER: ""}, json={})
    wrong = client.request(method, path, headers={HEADER: "nope"}, json={})
    assert missing.status_code == 401 and wrong.status_code == 401


def test_health_needs_no_header(client: TestClient) -> None:
    assert client.get("/health", headers={HEADER: ""}).status_code == 200


def test_401_when_internal_key_unset(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INTERNAL_API_KEY")
    assert client.get("/limits").status_code == 401


# --- venues ------------------------------------------------------------------------------


def test_venue_create_read_update(client: TestClient) -> None:
    venue_id = create_venue(client)
    assert venue_id.startswith("ven_")

    got = client.get(f"/venues/{venue_id}").json()
    assert got["venue"]["name"] == "Blue Note Jazz Club"
    assert [s["act"] for s in got["venue"]["past_shows"]] == ["Synthetic Entity 3", "Unknown Act"]
    assert got["resolutions"] == [] and got["profile"] is None

    client.post(f"/venues/{venue_id}/resolve")
    update = {**VENUE, "id": venue_id, "capacity": 200, "past_shows": VENUE["past_shows"][:1]}
    res = client.post("/venues", json=update)
    assert res.status_code == 201 and res.json() == {"id": venue_id}
    got = client.get(f"/venues/{venue_id}").json()
    assert got["venue"]["capacity"] == 200
    # The dropped act's resolution goes with it; the kept ones stay.
    assert {i["query"] for i in got["resolutions"]} == {VENUE_KEY, "Synthetic Entity 3"}


def test_venue_errors(client: TestClient) -> None:
    assert client.get("/venues/ven_missing").status_code == 404
    assert client.post("/venues", json={**VENUE, "id": "ven_missing"}).status_code == 404
    res = client.post("/venues", json={**VENUE, "capacity": 0})
    assert res.status_code == 400
    assert res.json() == {"reason": "invalid_input", "field": "capacity"}


def test_resolve_returns_candidates(client: TestClient, harness: RouterHarness) -> None:
    venue_id = create_venue(client)
    res = client.post(f"/venues/{venue_id}/resolve")
    assert res.status_code == 200, res.text
    body = res.json()
    assert [i["query"] for i in body["items"]] == [VENUE_KEY, "Synthetic Entity 3", "Unknown Act"]
    items = items_by_query(body)

    venue = items[VENUE_KEY]
    assert venue["kind"] == "venue" and not venue["confirmed"] and len(venue["candidates"]) == 2
    exact = items["Synthetic Entity 3"]
    assert exact["confirmed"] and exact["entity_id"] == ENTITY_ID
    fuzzy = items["Unknown Act"]
    assert not fuzzy["confirmed"] and fuzzy["entity_id"] is None
    assert ENTITY_ID in [c["entity_id"] for c in fuzzy["candidates"]]

    # Stored: resolving again spends no Qloo call.
    spawned = harness.spawns
    assert client.post(f"/venues/{venue_id}/resolve").json() == body
    assert harness.spawns == spawned == 3


def test_confirm_stores_the_choice(client: TestClient) -> None:
    venue_id = create_venue(client)
    client.post(f"/venues/{venue_id}/resolve")

    res = client.post(
        f"/venues/{venue_id}/confirm",
        json={"query": "Unknown Act", "chosen_entity_id": ENTITY_ID},
    )
    assert res.status_code == 200, res.text
    assert res.json()["confirmed"] and res.json()["entity_id"] == ENTITY_ID
    skipped = client.post(
        f"/venues/{venue_id}/confirm", json={"query": VENUE_KEY, "chosen_entity_id": None}
    )
    assert skipped.json()["confirmed"] and skipped.json()["entity_id"] is None

    stored = {i["query"]: i for i in client.get(f"/venues/{venue_id}").json()["resolutions"]}
    assert stored["Unknown Act"]["confirmed"] and stored["Unknown Act"]["entity_id"] == ENTITY_ID
    assert stored[VENUE_KEY]["confirmed"]


def test_confirm_rejects_unknown_choices(client: TestClient) -> None:
    venue_id = create_venue(client)
    client.post(f"/venues/{venue_id}/resolve")
    not_a_candidate = client.post(
        f"/venues/{venue_id}/confirm", json={"query": "Unknown Act", "chosen_entity_id": "X"}
    )
    assert not_a_candidate.status_code == 422
    unresolved = client.post(
        f"/venues/{venue_id}/confirm", json={"query": "Nobody", "chosen_entity_id": ENTITY_ID}
    )
    assert unresolved.status_code == 404


def test_profile_409_while_unconfirmed(client: TestClient, harness: RouterHarness) -> None:
    venue_id = create_venue(client)
    before = client.post(f"/venues/{venue_id}/profile")
    assert before.status_code == 409

    client.post(f"/venues/{venue_id}/resolve")
    res = client.post(f"/venues/{venue_id}/profile")
    assert res.status_code == 409
    assert res.json()["detail"]["pending"] == [VENUE_KEY, "Unknown Act"]
    assert "recommend" not in harness.workflows(), "no taste workflow before confirmation"


def test_profile_builds_and_stores(client: TestClient, harness: RouterHarness) -> None:
    venue_id = profiled_venue(client)
    profile = client.get(f"/venues/{venue_id}").json()["profile"]
    assert profile is not None
    built = CrowdProfile.model_validate(profile)
    assert built.venue_id == venue_id
    assert [s.entity_id for s in built.signals] == [ENTITY_ID]
    assert built.related_artists
    assert len(harness.inputs_for("recommend")) == 4


# --- runs --------------------------------------------------------------------------------


def test_run_returns_ranked_candidates(client: TestClient, scripts: Scripts) -> None:
    venue_id = profiled_venue(client)
    scripts.pipeline()
    res = post_run(client, venue_id)
    assert res.status_code == 200, res.text
    run = res.json()
    assert run["status"] == "ok" and run["venue_id"] == venue_id
    shortlist = run["result"]["shortlist"]
    assert shortlist
    totals = [e["total"] for e in shortlist]
    assert totals == sorted(totals, reverse=True)
    for entry in shortlist:
        assert entry["top_reason"] and set(entry["neutral"]) <= set(entry["sub_scores"])
        assert {"crowd_affinity", "past_fit", "local_heat"} <= set(entry["sub_scores"])
    assert sum(run["result"]["weights"].values()) == pytest.approx(1.0)
    assert run["result"]["steps"] == 2, "the stored profile saves build_crowd_profile"
    assert run["token_usage"]["model_calls"] == 3

    assert client.get(f"/runs/{run['run_id']}").json() == run


def test_run_needs_a_profile(client: TestClient) -> None:
    venue_id = create_venue(client)
    assert post_run(client, venue_id).status_code == 409
    assert post_run(client, "ven_missing").status_code == 404


def test_run_blocked_at_the_daily_cap(client: TestClient) -> None:
    """The before-run guard (T32): 429 {reason: "budget"} before anything is stored."""
    venue_id = profiled_venue(client)
    deps = client.app.state.deps  # type: ignore[attr-defined]
    counter = deps.gateways.counter
    asyncio.run(counter.reserve(300 - asyncio.run(counter.used())))
    res = post_run(client, venue_id)
    assert res.status_code == 429
    assert res.json()["reason"] == "budget" and res.json()["what"] == "Qloo"
    assert client.get("/limits").json()["qloo"] == {"used": 300, "cap": 300, "remaining": 0}


def test_run_returns_ask_on_needs_input(client: TestClient, scripts: Scripts) -> None:
    venue_id = profiled_venue(client)
    scripts.turns = [
        [("describe", {"entity": "Blue Note Jazz Club", "type": "place"})],
        "should never be asked",
    ]
    res = post_run(client, venue_id)
    assert res.status_code == 200, res.text
    run = res.json()
    assert run["status"] == "ask"
    ask = run["result"]["ask"]
    assert ask["question"] and len(ask["candidates"]) == 2
    assert run["result"]["shortlist"] == [], "RunResult always carries the key; an ask has none"
    assert client.get(f"/runs/{run['run_id']}").json()["status"] == "ask"


def test_evidence_and_calls(client: TestClient, scripts: Scripts) -> None:
    venue_id = profiled_venue(client)
    scripts.pipeline()
    run = post_run(client, venue_id).json()
    run_id, top = run["run_id"], run["result"]["shortlist"][0]

    res = client.get(f"/runs/{run_id}/candidates/{top['entity_id']}/evidence")
    assert res.status_code == 200, res.text
    evidence = res.json()
    assert evidence["score"]["total"] == pytest.approx(top["total"], abs=1e-4)
    trail = evidence["evidence"]
    assert trail["entity_id"] == top["entity_id"] and trail["sub_scores"]
    assert set(trail["source_labels"].values()) <= {"qloo", "draw"}
    missing = client.get(f"/runs/{run_id}/candidates/NOPE/evidence")
    assert missing.status_code == 404

    calls = client.get(f"/runs/{run_id}/calls")
    assert calls.status_code == 200
    rows = calls.json()
    assert rows and {r["workflow"] for r in rows} == {"recommend"}
    assert {"call_id", "workflow", "status", "cache_hit", "request", "envelope"} <= set(rows[0])
    call_ids = {r["call_id"] for r in rows}
    assert {c["call_id"] for c in trail["qloo_calls"]} <= call_ids
    assert FAKE_QLOO_KEY not in calls.text
    assert client.get("/runs/run_missing/calls").status_code == 404
    assert client.get("/runs/run_missing").status_code == 404


def test_limits_shape(client: TestClient) -> None:
    venue_id = create_venue(client)
    client.post(f"/venues/{venue_id}/resolve")
    res = client.get("/limits")
    assert res.status_code == 200
    body = res.json()
    assert set(body) == {"date", "qloo", "llm"}
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", body["date"])
    for name in ("qloo", "llm"):
        assert set(body[name]) == {"used", "cap", "remaining"}
        assert body[name]["remaining"] == body[name]["cap"] - body[name]["used"]
    assert body["qloo"] == {"used": 3, "cap": 300, "remaining": 297}
    assert "key" not in res.text.lower()
