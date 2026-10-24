"""The venue agent's loop, with a scripted model and the fake `qloo mcp` server.

The model is `ScriptedModel` (tests/agent/scripted_model.py); the Qloo tools are the real
`QlooMCP` tools over `tests/qloo/fake_mcp_server.py`, so every cap and branch below runs
through Strands' own loop and hooks.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from scripted_model import OUTPUT_TOKENS, Results, ScriptedModel, Stop, Turn
from tenacity import wait_none

from draw.agent.agent import (
    BROADENING_LIMIT_MESSAGE,
    MAX_STEPS,
    STEP_LIMIT_MESSAGE,
    AgentResult,
    FinalAnswer,
    run_venue_agent,
)
from draw.qloo.budget import LLM_PREFIX, MemoryCounter
from draw.qloo.config import QlooConfig
from draw.qloo.envelope import BudgetExceeded
from draw.qloo.mcp_client import QlooMCP
from draw.schemas import Constraints, EntityCandidate, PastShow, ResolvedEntity, VenueInput

FAKE_SERVER = Path(__file__).resolve().parents[1] / "qloo" / "fake_mcp_server.py"
FAKE_KEY = "-".join(["fake", "qloo", "key", "0" * 12])
TODAY = dt.date(2026, 10, 20)
TURN_TOKENS = 1000 + OUTPUT_TOKENS
CONSTRAINTS = Constraints(
    include_tags=[],
    exclude_tags=[],
    size_tier="emerging",
    exclude_recent_months=0,
    max_candidates=20,
)


def show(act: str, sold: int) -> PastShow:
    return PastShow(act=act, date=dt.date(2026, 6, 1), tickets_sold=sold, capacity=150)


def act(name: str, entity_id: str | None, *, confirmed: bool = True) -> ResolvedEntity:
    candidates = (
        []
        if confirmed
        else [
            EntityCandidate(entity_id=f"ID-{i}", name=name, type="urn:entity:artist") for i in "AB"
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
ACTS = [act("Act Full", "ID-FULL"), act("Act Mid", "ID-MID"), act("Act Dup", None, confirmed=False)]


@pytest.fixture(autouse=True)
def fake_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QLOO_API_KEY", FAKE_KEY)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("LLM_DAILY_TOKEN_CAP", raising=False)


@pytest.fixture
def qloo() -> Iterator[QlooMCP]:
    gateway = QlooMCP(
        QlooConfig(qloo_bin="/fake/qloo"),
        command=[sys.executable, str(FAKE_SERVER)],
        retry_wait=wait_none(),
    )
    with gateway:
        yield gateway


def counter(cap: int = 10_000_000) -> MemoryCounter:
    return MemoryCounter(cap, prefix=LLM_PREFIX, what="LLM token", charge_over=True)


async def run(
    qloo: QlooMCP, script: list[Any], *, token_counter: MemoryCounter | None = None, **kwargs: Any
) -> tuple[AgentResult, ScriptedModel]:
    model = ScriptedModel(script)
    result = await run_venue_agent(
        VENUE,
        CONSTRAINTS,
        "Find emerging acts for a Thursday; the crowd leans jazz.",
        qloo=qloo,
        acts=ACTS,
        model=model,
        token_counter=token_counter or counter(),
        today=TODAY,
        **kwargs,
    )
    return result, model


# --- script pieces: the pipeline threaded by ids ---------------------------------------


def generate(results: Results) -> Turn:
    profile_id = next(r["profile_id"] for r in results if "profile_id" in r)
    return [
        (
            "generate_candidates",
            {"profile_id": profile_id, "constraints_json": CONSTRAINTS.model_dump_json()},
        )
    ]


def latest(results: Results, key: str) -> Any:
    """The newest tool result carrying `key` (a cancelled call carries only its message)."""
    return next(r[key] for r in reversed(results) if key in r)


def gather(results: Results) -> Turn:
    return [("gather_signals", {"run_id": latest(results, "run_id")})]


def rank(results: Results) -> Turn:
    return [("score_and_rank", {"run_id": latest(results, "run_id")})]


def answer(results: Results) -> Turn:
    ranked = results[-1]
    return json.dumps(
        {
            "shortlist": ranked["shortlist"],
            "notes": ["Model note."],
            "data_quality": ranked["data_quality"],
        }
    )


def build_from_prompt(model: ScriptedModel) -> Turn:
    """Read the venue id from the prompt the agent sent (the first user message)."""
    text = "".join(str(b.get("text", "")) for b in model.requests[0][0]["content"])
    venue_id = next(w.rstrip(".") for w in text.split() if w.startswith("ven_"))
    return [("build_crowd_profile", {"venue_id": venue_id})]


class Pipeline:
    """A script that reads the venue id off the prompt, then threads ids tool to tool."""

    def __init__(self, *tail: Any, qloo_first: bool = True) -> None:
        self.model: ScriptedModel | None = None
        self.tail = list(tail)
        self.qloo_first = qloo_first

    def turns(self) -> list[Any]:
        first: list[Any] = (
            [[("find_tags", {"query": "jazz", "limit": 5})]] if self.qloo_first else []
        )
        return [*first, self._build, *self.tail]

    def _build(self, results: Results) -> Turn:
        assert self.model is not None
        return build_from_prompt(self.model)


async def run_pipeline(
    qloo: QlooMCP, *tail: Any, qloo_first: bool = True, **kwargs: Any
) -> tuple[AgentResult, ScriptedModel]:
    pipeline = Pipeline(*tail, qloo_first=qloo_first)
    model = ScriptedModel(pipeline.turns())
    pipeline.model = model
    result = await run_venue_agent(
        VENUE,
        CONSTRAINTS,
        "Find emerging acts for a Thursday; the crowd leans jazz.",
        qloo=qloo,
        acts=ACTS,
        model=model,
        token_counter=kwargs.pop("token_counter", None) or counter(),
        today=TODAY,
        **kwargs,
    )
    return result, model


# --- the happy path ---------------------------------------------------------------------


async def test_full_run_ranks_deterministically_and_validates(qloo: QlooMCP) -> None:
    result, model = await run_pipeline(qloo, generate, gather, rank, answer)
    assert result.status == "ok" and result.error is None, result
    assert result.stop_reason == "end_turn"
    assert [t.tool for t in result.trace] == [
        "find_tags",
        "build_crowd_profile",
        "generate_candidates",
        "gather_signals",
        "score_and_rank",
    ]
    assert result.steps == 5 and all(t.status == "success" for t in result.trace)
    assert result.trace[0].qloo_tool and not result.trace[1].qloo_tool
    assert result.trace[0].outcome is not None and result.trace[0].outcome.startswith("ok")

    # The shortlist is the store's ranking, highest total first, with candidate ids.
    assert result.store is not None and result.run_id is not None
    run = result.store.run(result.run_id)
    assert run.ranked is not None and result.shortlist
    assert [e["entity_id"] for e in result.shortlist] == [
        r.candidate.entity_id for r in run.ranked[: len(result.shortlist)]
    ]
    totals = [e["total"] for e in result.shortlist]
    assert totals == sorted(totals, reverse=True)
    assert "Model note." in result.notes
    assert any("Act Dup" in n for n in result.notes), "the unconfirmed act is noted"
    assert result.data_quality in ("ok", "partial", "degraded")

    # The final JSON validates, and the model's answer was JSON too.
    final = FinalAnswer.model_validate(result.to_json())
    assert [e.candidate_id for e in final.shortlist] == [
        e["candidate_id"] for e in result.shortlist
    ]
    assert final.ask is None
    assert json.loads(result.model_answer)["shortlist"]

    # Usage: six model calls, each metered, the system prompt and tools cached by config.
    assert result.usage["model_calls"] == 6
    assert result.usage["tokens_counted"] == 6 * TURN_TOKENS
    assert (
        result.usage["input_tokens"] == 6 * 1000
        and result.usage["output_tokens"] == 6 * OUTPUT_TOKENS
    )
    assert model.system_prompt and "never rank" in model.system_prompt
    names = {spec["name"] for spec in model.tool_specs}
    assert {"find_tags", "recommend", "score_and_rank", "draft_brief"} <= names
    assert "QLOO_API_KEY" not in model.system_prompt and FAKE_KEY not in str(model.requests)


async def test_prompt_carries_ids_and_sell_through_not_envelopes(qloo: QlooMCP) -> None:
    result, model = await run_pipeline(qloo, generate, rank, answer)
    assert result.status == "ok"
    prompt = "".join(str(b.get("text", "")) for b in model.requests[0][0]["content"])
    assert "Synthetic Room" in prompt and "ven_" in prompt and "res_" in prompt
    assert "sold 100%" in prompt and "sold 67%" in prompt
    assert "UNCONFIRMED" in prompt and "ID-A" in prompt
    assert "none (profile from location and past acts)" in prompt
    assert "provenance" not in prompt and "affinity" not in prompt
    assert any("were not checked this time" in n for n in result.notes)
    assert result.data_quality in ("partial", "degraded")


# --- caps ------------------------------------------------------------------------------


async def test_stops_at_eight_tool_steps_then_forces_the_answer(qloo: QlooMCP) -> None:
    """A model that never stops calling tools gets exactly MAX_STEPS executed."""
    runaway: list[Any] = [[("find_tags", {"query": "jazz", "limit": 3})]]
    result, model = await run(qloo, runaway)
    assert result.steps == MAX_STEPS
    executed = [t for t in result.trace if t.status != "cancelled"]
    cancelled = [t for t in result.trace if t.status == "cancelled"]
    assert len(executed) == MAX_STEPS and len(cancelled) == 2
    assert all(t.outcome and t.outcome.startswith("Step limit reached") for t in cancelled)
    # The model was told to answer (the cancel message came back as a tool result)...
    flat = json.dumps(model.requests[-1])
    assert STEP_LIMIT_MESSAGE[:40] in flat
    # ...ignored it once, and the second over-limit batch ended the turn without a model call.
    assert result.stop_reason == "step_limit" and model.turn == MAX_STEPS + 2
    assert result.status == "error" and "score_and_rank" in (result.error or "")
    assert result.shortlist == []


async def test_parallel_tool_calls_count_each(qloo: QlooMCP) -> None:
    three: list[Any] = [[("find_tags", {"query": "jazz"})] * 3]
    result, model = await run(qloo, three)
    assert result.steps == MAX_STEPS
    assert [t.status for t in result.trace][:9] == ["success"] * 8 + ["cancelled"]


async def test_tools_in_one_turn_run_in_order(qloo: QlooMCP) -> None:
    """gather_signals and score_and_rank in one turn: ranking must see the signals.

    Strands runs a turn's tools concurrently by default, and score_and_rank (no awaits)
    used to finish before gather_signals (Qloo calls), ranking on neutral signals.
    """

    def gather_and_rank(results: Results) -> Turn:
        run_id = latest(results, "run_id")
        return [("gather_signals", {"run_id": run_id}), ("score_and_rank", {"run_id": run_id})]

    result, _ = await run_pipeline(qloo, generate, gather_and_rank, answer, qloo_first=False)
    assert result.status == "ok", result
    assert [t.tool for t in result.trace] == [
        "build_crowd_profile",
        "generate_candidates",
        "gather_signals",
        "score_and_rank",
    ]
    assert result.store is not None and result.run_id is not None
    run = result.store.run(result.run_id)
    assert run.signals is not None and run.signals.by_id
    assert run.ranked is not None
    with_signals = [r for r in run.ranked if r.candidate.entity_id in run.signals.by_id]
    assert with_signals and any("past_fit" not in (r.score.neutral or []) for r in with_signals)
    assert not any("were not checked this time" in n for n in result.notes)


async def test_broadening_happens_at_most_once(qloo: QlooMCP) -> None:
    result, _ = await run_pipeline(
        qloo, generate, generate, generate, rank, answer, qloo_first=False
    )
    assert result.status == "ok", result.error
    generated = [t for t in result.trace if t.tool == "generate_candidates"]
    assert [t.status for t in generated] == ["success", "success", "cancelled"]
    assert generated[2].outcome and generated[2].outcome.startswith(BROADENING_LIMIT_MESSAGE[:30])
    assert result.steps == 4  # build, generate x2, rank; the cancelled call is not a step


async def test_budget_guard_raises_before_the_run(qloo: QlooMCP) -> None:
    spent = counter(cap=5000)
    await spent.reserve(5000)
    model = ScriptedModel(["{}"])
    with pytest.raises(BudgetExceeded, match="LLM token daily budget exceeded: 5000/5000"):
        await run_venue_agent(VENUE, CONSTRAINTS, "hi", qloo=qloo, model=model, token_counter=spent)
    assert model.turn == 0, "no model call is made once the cap is spent"


async def test_budget_guard_stops_the_run_above_cap(qloo: QlooMCP) -> None:
    """Each model call is charged; the call that passes the cap ends the run with an error."""
    tight = counter(cap=TURN_TOKENS + 10)
    result, model = await run_pipeline(qloo, generate, rank, answer, token_counter=tight)
    assert result.status == "error" and result.stop_reason == "budget"
    assert "LLM token daily budget exceeded" in (result.error or "")
    assert model.turn == 2 and await tight.used() == 2 * TURN_TOKENS
    assert [t.tool for t in result.trace] == ["find_tags"], "the trace so far is kept"


async def test_token_cap_from_env_and_default_counter(
    qloo: QlooMCP, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_DAILY_TOKEN_CAP", "123")
    result, _ = await run_pipeline(qloo, generate, rank, answer, token_counter=None)
    # No REDIS_URL: the default counter is a no-op, so even a tiny cap never trips.
    assert result.status == "ok"


# --- asks and the validator -------------------------------------------------------------


async def test_needs_input_returns_an_ask_payload(qloo: QlooMCP) -> None:
    script: list[Any] = [
        [("describe", {"entity": "Blue Note Jazz Club", "type": "place"})],
        "should never be asked",
    ]
    result, model = await run(qloo, script)
    assert result.status == "ask" and result.ask is not None
    assert result.stop_reason == "needs_input" and model.turn == 1
    assert result.ask.tool == "describe" and len(result.ask.candidates) == 2
    assert {"id", "name", "type"} <= set(result.ask.candidates[0])
    assert result.ask.question
    final = result.to_json()
    assert set(final["ask"]) == {"question", "candidates"} and final["data_quality"] == "partial"
    FinalAnswer.model_validate(final)
    assert result.shortlist == [] and result.error is None


async def test_skipping_score_and_rank_is_rejected(qloo: QlooMCP) -> None:
    def fake_answer(results: Results) -> Turn:
        return json.dumps(
            {
                "shortlist": [
                    {"candidate_id": "cand_x", "entity_id": "E1", "name": "Made Up", "total": 0.9}
                ],
                "notes": [],
                "data_quality": "ok",
            }
        )

    result, _ = await run_pipeline(qloo, generate, fake_answer)
    assert result.status == "error" and result.shortlist == []
    assert "score_and_rank was never called" in (result.error or "")
    assert result.to_json()["error"] and result.to_json()["shortlist"] == []


async def test_reordered_shortlist_is_restored(qloo: QlooMCP) -> None:
    def reversed_answer(results: Results) -> Turn:
        ranked = results[-1]
        return json.dumps(
            {"shortlist": list(reversed(ranked["shortlist"])), "notes": [], "data_quality": "ok"}
        )

    result, _ = await run_pipeline(qloo, generate, rank, reversed_answer)
    assert result.status == "ok"
    totals = [e["total"] for e in result.shortlist]
    assert totals == sorted(totals, reverse=True)
    assert any("keeps Outdraw's score" in n for n in result.notes)


async def test_prose_answer_still_yields_the_ranked_shortlist(qloo: QlooMCP) -> None:
    result, _ = await run_pipeline(qloo, generate, rank, "Here is my shortlist: lots of prose.")
    assert result.status == "ok" and result.shortlist
    assert any("not the expected JSON" in n for n in result.notes)


async def test_max_tokens_and_refusal_are_checked_before_content(qloo: QlooMCP) -> None:
    cut, _ = await run(qloo, [Stop("max_tokens")])
    assert cut.status == "error" and cut.stop_reason == "max_tokens"
    assert "max_tokens" in (cut.error or "")

    refused, _ = await run(qloo, [Stop("refusal")])
    assert refused.status == "error" and refused.stop_reason == "refusal"
    assert "refused" in (refused.error or "")
