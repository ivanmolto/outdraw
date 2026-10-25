"""Draw's tools over synthetic envelopes: each callable standalone, compact, id-driven."""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

import pytest
from conftest import FIXTURES_DIR, RouterHarness
from strands.types.tools import AgentTool, ToolUse

from draw.agent.store import RunStore
from draw.agent.tools import TOOL_NAMES, DrawTools, dumps
from draw.qloo import exec_client
from draw.qloo.config import QlooConfig
from draw.qloo.exec_client import QlooExec
from draw.ranking.score import WEIGHTS
from draw.schemas import EntityCandidate, PastShow, ResolvedEntity, VenueInput

MAX_OUTPUT = 2048
TODAY = dt.date(2026, 10, 20)
CONSTRAINTS = json.dumps(
    {
        "include_tags": [],
        "exclude_tags": [],
        "size_tier": "emerging",  # the synthetic candidates' popularity band
        "exclude_recent_months": 0,
        "max_candidates": 20,
    }
)


def fixture(workflow: str, name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES_DIR / workflow / f"{name}.json").read_text("utf-8"))
    return data


def router(workflow: str, payload: dict[str, Any]) -> dict[str, Any]:
    if workflow == "recommend":
        target = str(payload.get("target_type"))
        if target == "artist":
            name = "candidates_baseline" if payload.get("limit", 0) > 10 else "crowd_artists"
        else:
            name = {"place": "crowd_places", "movie": "crowd_movies"}.get(target, "crowd_brands")
        return fixture("recommend", name)
    if workflow == "compare_audiences":
        return fixture("compare_audiences", "crowd_genres")
    if workflow == "audience_demographics":
        return fixture("audience_demographics", "crowd_demographics_1")
    if workflow == "where_popular":
        return fixture("where_popular", "radiohead_new_york_ny")
    if workflow == "trends":
        return fixture("trends", "three_artists_last_12_months")
    if workflow == "find_tags":
        return fixture("find_tags", "jazz")
    raise AssertionError(f"unexpected workflow {workflow}")


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> RouterHarness:
    fake = RouterHarness(router)
    monkeypatch.setattr(exec_client, "create_subprocess_exec", fake)
    return fake


def show(act: str, sold: int) -> PastShow:
    return PastShow(act=act, date=dt.date(2026, 6, 1), tickets_sold=sold, capacity=150)


def act(name: str, entity_id: str | None, *, confirmed: bool = True) -> ResolvedEntity:
    candidates = (
        []
        if confirmed
        else [
            EntityCandidate(entity_id="ID-A", name=name, type="urn:entity:artist"),
            EntityCandidate(entity_id="ID-B", name=name, type="urn:entity:artist"),
        ]
    )
    return ResolvedEntity(
        query=name,
        entity_id=entity_id,
        canonical_name=name,
        confidence=1.0 if confirmed else 0.4,
        candidates=candidates,
        confirmed=confirmed,
    )


VENUE = VenueInput(
    name="Synthetic Room",
    city="New York",
    country="United States",
    capacity=150,
    past_shows=[show("Act Full", 150), show("Act Mid", 100), show("Act Dup", 60)],
)


@pytest.fixture
def seeded(harness: RouterHarness) -> tuple[DrawTools, RunStore, str, str]:
    """A store with one venue, two confirmed acts and one ambiguous act."""
    store = RunStore()
    venue_id = store.add_venue(VENUE, venue_id="ven_test")
    store.add_resolved(venue_id, act("Act Full", "ID-FULL"))
    store.add_resolved(venue_id, act("Act Mid", "ID-MID"))
    ambiguous_id = store.add_resolved(venue_id, act("Act Dup", None, confirmed=False))
    tools = DrawTools(store, QlooExec(QlooConfig(qloo_bin="/fake/qloo")), today=TODAY)
    return tools, store, venue_id, ambiguous_id


def check(payload: dict[str, Any]) -> str:
    """Every tool output is JSON-serialisable, under 2 KB and free of raw envelopes."""
    text = dumps(payload)
    assert len(text) < MAX_OUTPUT, len(text)
    assert json.loads(text) == payload
    assert "provenance" not in text and "explainability" not in text
    return text


# --- the pipeline, tool by tool ------------------------------------------------------


async def test_pipeline_end_to_end(seeded: tuple[DrawTools, RunStore, str, str]) -> None:
    tools, store, venue_id, ambiguous_id = seeded

    confirmed = await tools.confirm_entity(ambiguous_id, "ID-B")
    check(confirmed)
    assert confirmed == {
        "resolved_id": ambiguous_id,
        "query": "Act Dup",
        "entity_id": "ID-B",
        "canonical_name": "Act Dup",
        "confirmed": True,
    }
    assert store.resolved[ambiguous_id].confirmed is True

    profile = await tools.build_crowd_profile(venue_id)
    check(profile)
    assert profile["venue_id"] == venue_id and profile["profile_id"].startswith("prof_")
    assert profile["signals"] == 3 and profile["unconfirmed"] == []
    assert profile["data_quality"] == "ok" and len(profile["genres"]) <= 5
    assert store.profiles[profile["profile_id"]].venue_id == venue_id

    run = await tools.generate_candidates(profile["profile_id"], CONSTRAINTS)
    check(run)
    assert run["run_id"].startswith("run_") and run["count"] == 20
    assert run["candidates_quality"] == "ok" and run["reason"] is None
    assert len(run["top"]) == 5

    signals = await tools.gather_signals(run["run_id"])
    check(signals)
    assert 1 <= signals["candidates_with_signals"] <= 20 and signals["calls"] <= 33

    ranked = await tools.score_and_rank(run["run_id"])
    check(ranked)
    assert ranked["ranked"] == 20 and ranked["signals_gathered"] is True
    assert ranked["weights"] == dict(WEIGHTS) and ranked["data_quality"] == "ok"
    shortlist = ranked["shortlist"]
    assert len(shortlist) == 8 and all(e["candidate_id"].startswith("cand_") for e in shortlist)
    totals = [e["total"] for e in shortlist]
    assert totals == sorted(totals, reverse=True)
    assert all("reason" in e for e in shortlist[:5]) and "reason" not in shortlist[7]

    top = shortlist[0]["candidate_id"]
    evidence = await tools.get_evidence(top)
    check(evidence)
    assert evidence["candidate_id"] == top and evidence["run_id"] == run["run_id"]
    assert set(evidence["sub_scores"]) == set(WEIGHTS) and evidence["weights"] == dict(WEIGHTS)
    assert evidence["source_labels"]["novelty"] == "draw"
    assert evidence["source_labels"]["crowd_affinity"] == "qloo"
    assert evidence["affinities"], "a Qloo-backed sub-score points at a call"
    call_ids = {c["call_id"] for c in evidence["qloo_calls"]}
    assert {a["call_id"] for a in evidence["affinities"]} <= call_ids
    assert {a["sub_score"] for a in evidence["affinities"]} == {
        k for k, v in evidence["source_labels"].items() if v == "qloo"
    }
    assert all(c["workflow"] in {"recommend", "compare_audiences", "where_popular", "trends"}
               for c in evidence["qloo_calls"])  # fmt: skip
    assert call_ids <= set(store.calls), "the store observed every gateway call"
    assert len(evidence["inputs"]) == 3 + 4, (
        "three crowd signals, region, tier, popularity, history"
    )
    full = tools.evidence_trail(top)
    assert {c["call_id"] for c in full["qloo_calls"]} == call_ids
    assert set(full["source_labels"]) > set(evidence["source_labels"])
    assert full["popularity"] == store.run(run["run_id"]).ranked[0].candidate.popularity  # type: ignore[index]
    # draft_brief (the one tool with a model call of its own) is covered by test_brief.py.


async def test_score_and_rank_without_signals_is_neutral(
    seeded: tuple[DrawTools, RunStore, str, str],
) -> None:
    tools, _store, venue_id, _ = seeded
    profile = await tools.build_crowd_profile(venue_id)
    run = await tools.generate_candidates(profile["profile_id"], CONSTRAINTS)
    ranked = await tools.score_and_rank(run["run_id"])
    assert ranked["signals_gathered"] is False
    evidence = await tools.get_evidence(ranked["shortlist"][0]["candidate_id"])
    assert evidence["neutral"] == ["local_heat", "momentum", "past_fit"]
    assert evidence["source_labels"]["past_fit"] == "draw"
    assert evidence["sub_scores"]["past_fit"] == 0.5


async def test_unconfirmed_acts_are_skipped_and_listed(
    seeded: tuple[DrawTools, RunStore, str, str],
) -> None:
    tools, _store, venue_id, _ = seeded
    profile = await tools.build_crowd_profile(venue_id)
    assert profile["signals"] == 2 and profile["unconfirmed"] == ["Act Dup"]
    assert any("Act Dup" in n for n in profile["notes"])


# --- errors stay readable ------------------------------------------------------------


async def test_unknown_ids_and_bad_input_raise_value_errors(
    seeded: tuple[DrawTools, RunStore, str, str],
) -> None:
    tools, _store, venue_id, ambiguous_id = seeded
    with pytest.raises(ValueError, match="Unknown venue id"):
        await tools.build_crowd_profile("ven_nope")
    with pytest.raises(ValueError, match="not a candidate"):
        await tools.confirm_entity(ambiguous_id, "ID-Z")
    with pytest.raises(ValueError, match="Unknown profile id"):
        await tools.generate_candidates("prof_nope", CONSTRAINTS)
    profile = await tools.build_crowd_profile(venue_id)
    with pytest.raises(ValueError, match="constraints_json"):
        await tools.generate_candidates(profile["profile_id"], '{"size_tier": "huge"}')
    with pytest.raises(ValueError, match="Unknown run id"):
        await tools.gather_signals("run_nope")
    with pytest.raises(ValueError, match="Unknown candidate id"):
        await tools.get_evidence("cand_nope")


# --- as Strands tools ----------------------------------------------------------------


async def test_tools_are_strands_tools_with_docstrings(
    seeded: tuple[DrawTools, RunStore, str, str],
) -> None:
    tools, _store, venue_id, _ = seeded
    agent_tools = tools.tools()
    assert [t.tool_name for t in agent_tools] == list(TOOL_NAMES)
    for t in agent_tools:
        assert isinstance(t, AgentTool)
        spec = t.tool_spec
        assert len(spec["description"]) > 40, t.tool_name
        properties = spec["inputSchema"]["json"]["properties"]
        assert "self" not in properties and properties, t.tool_name

    build = agent_tools[1]
    tool_use = ToolUse(toolUseId="t1", name="build_crowd_profile", input={"venue_id": venue_id})
    events = [e async for e in build.stream(tool_use, {})]
    result = events[-1]["tool_result"]
    assert result["status"] == "success" and result["toolUseId"] == "t1"
    payload = json.loads(result["content"][0]["text"])
    assert payload["venue_id"] == venue_id and len(result["content"][0]["text"]) < MAX_OUTPUT

    bad_use = ToolUse(toolUseId="t2", name="build_crowd_profile", input={"venue_id": "nope"})
    events = [e async for e in build.stream(bad_use, {})]
    failed = events[-1]["tool_result"]
    assert failed["status"] == "error" and "Unknown venue id" in failed["content"][0]["text"]
