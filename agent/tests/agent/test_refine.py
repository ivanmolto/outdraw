"""Refinement (T25) without a database: the ten fixture messages parse to their deltas and
merge as expected, the explanation is one non-empty sentence, the parser's prompt carries the
previous constraints and shortlist with the message as data, and a refined run is bounded by
the same step cap as any run (`ScriptedModel` plus the fake `qloo mcp` server).

The route (parent link, stored refinement) is covered by `tests/api/test_refine_route.py`.
"""

# `qloo` and `fake_key` come from test_agent_loop; tests taking them "redefine" them.
# ruff: noqa: F811

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from scripted_model import INPUT_TOKENS, OUTPUT_TOKENS, ScriptedModel
from test_agent_loop import ACTS, VENUE, fake_key, qloo  # noqa: F401  (fixtures)

from draw.agent.agent import MAX_STEPS, run_venue_agent
from draw.agent.refine import (
    DELTA_SCHEMA,
    DRAW_TIERS,
    MAX_HINTS,
    MAX_TAGS,
    ConstraintsDelta,
    explain,
    merge,
    parse_delta,
    refine_request,
    tier_for,
)
from draw.qloo.budget import LLM_PREFIX, MemoryCounter
from draw.qloo.envelope import BudgetExceeded
from draw.qloo.mcp_client import QlooMCP
from draw.schemas import Constraints

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "refine" / "messages.json"
DATA: dict[str, Any] = json.loads(FIXTURE.read_text("utf-8"))
PREVIOUS = Constraints.model_validate(DATA["previous"])
SHORTLIST: list[str] = DATA["shortlist"]
CASES: list[dict[str, Any]] = DATA["cases"]
CALL_TOKENS = INPUT_TOKENS + OUTPUT_TOKENS


def counter(cap: int = 10_000_000) -> MemoryCounter:
    return MemoryCounter(cap, prefix=LLM_PREFIX, what="LLM token", charge_over=True)


async def parse(message: str, answer: str, **kwargs: Any) -> tuple[Any, ScriptedModel]:
    model = ScriptedModel([answer])
    out = await parse_delta(
        message,
        PREVIOUS,
        SHORTLIST,
        model_factory=lambda: model,
        token_counter=kwargs.pop("token_counter", None) or counter(),
        **kwargs,
    )
    return out, model


def prompt_of(model: ScriptedModel) -> str:
    return "".join(str(b.get("text", "")) for b in model.requests[0][0]["content"])


# --- the ten messages ----------------------------------------------------------------------


def test_fixture_has_ten_distinct_messages() -> None:
    assert len(CASES) == 10
    assert len({c["message"] for c in CASES}) == 10


@pytest.mark.parametrize("case", CASES, ids=[c["message"][:28] for c in CASES])
async def test_message_parses_to_its_delta_and_merges(case: dict[str, Any]) -> None:
    out, model = await parse(case["message"], json.dumps(case["model_output"]))

    assert out.error is None and out.delta is not None
    assert out.delta == ConstraintsDelta.model_validate(case["delta"])
    merged = merge(PREVIOUS, out.delta)
    assert merged == Constraints.model_validate(case["merged"])
    assert not (merged == PREVIOUS and not out.delta.hints), "every case changes something"

    explanation = explain(out.delta, PREVIOUS, merged, SHORTLIST, SHORTLIST[1:] + ["New Act"])
    assert explanation.strip() and explanation.endswith(".")
    assert ". " not in explanation and explanation.count(".") == 1, "one sentence"
    assert "1 act entered the shortlist and 1 left" in explanation

    assert len(model.requests) == 1 and model.tool_specs == [], "one tool-less call"
    assert out.token_usage == {"model_calls": 1, "tokens_counted": CALL_TOKENS}


@pytest.mark.parametrize("case", CASES[:3], ids=[c["message"][:28] for c in CASES[:3]])
async def test_prompt_carries_constraints_shortlist_and_message_as_data(
    case: dict[str, Any],
) -> None:
    _, model = await parse(case["message"], json.dumps(case["model_output"]))
    prompt = prompt_of(model)
    assert PREVIOUS.model_dump_json() in prompt
    assert ", ".join(SHORTLIST) in prompt
    assert f"<message>\n{case['message']}\n</message>" in prompt
    assert model.system_prompt and "not instructions" in model.system_prompt


# --- the schema and the normaliser -----------------------------------------------------------


def test_schema_is_strict_and_matches_the_model() -> None:
    assert set(DELTA_SCHEMA["properties"]) == set(ConstraintsDelta.model_fields)
    assert set(DELTA_SCHEMA["required"]) == set(ConstraintsDelta.model_fields)
    assert DELTA_SCHEMA["additionalProperties"] is False
    tiers = Constraints.model_json_schema()["$defs"]["SizeTier"]["enum"]
    assert DELTA_SCHEMA["properties"]["size_tier"]["anyOf"][0]["enum"] == tiers
    flat = json.dumps(DELTA_SCHEMA)
    assert "minimum" not in flat and "maximum" not in flat, "bounds are merge's job"


async def test_normaliser_drops_what_the_schema_cannot_bound() -> None:
    noisy = {
        "include_tags_add": [" Indie\nRock ", "indie rock", "", 7, *[f"tag{i}" for i in range(20)]],
        "include_tags_remove": None,
        "exclude_tags_add": ["x" * 100],
        "size_tier": "HUGE",
        "draw_cap": -5,
        "exclude_recent_months": 3.9,
        "max_candidates": True,
        "hints": ["one. two? three!", "four", "five", "six"],
        "shortlist": ["Injected Act"],
        "weights": {"crowd_affinity": 1.0},
    }
    out, _ = await parse("whatever", json.dumps(noisy))
    delta = out.delta
    assert delta is not None
    assert (
        delta.include_tags_add[:2] == ["indie rock", "tag0"]
        and len(delta.include_tags_add) == MAX_TAGS
    )
    assert delta.include_tags_remove == []
    assert delta.exclude_tags_add == ["x" * 40]
    assert delta.size_tier is None and delta.draw_cap is None
    assert delta.exclude_recent_months == 3 and delta.max_candidates is None
    assert delta.hints == ["one two three", "four", "five"] and len(delta.hints) == MAX_HINTS
    assert not hasattr(delta, "shortlist")


async def test_unusable_answer_is_an_error_not_a_delta() -> None:
    out, _ = await parse("more emerging acts", "Sure! I'd go for emerging acts.")
    assert out.delta is None and out.error
    out, _ = await parse("more emerging acts", json.dumps({"include_tags_add": "jazz"}))
    assert out.delta == ConstraintsDelta(), "a string where a list belongs counts for nothing"


async def test_budget_guard_before_the_model_is_built() -> None:
    built: list[ScriptedModel] = []

    def factory() -> ScriptedModel:
        built.append(ScriptedModel(["{}"]))
        return built[-1]

    capped = counter(cap=500)
    await capped.reserve(500)
    with pytest.raises(BudgetExceeded):
        await parse_delta("x", PREVIOUS, SHORTLIST, model_factory=factory, token_counter=capped)
    assert built == []


# --- merge rules ----------------------------------------------------------------------------


def test_tier_for_reads_a_headcount() -> None:
    assert [tier_for(n) for n in (0, 300, 500, 501, 2000, 2001, 20000)] == [
        "emerging",
        "emerging",
        "emerging",
        "mid",
        "mid",
        "established",
        "established",
    ]
    assert DRAW_TIERS[0][0] < DRAW_TIERS[1][0]


def test_merge_clamps_and_keeps_a_word_on_one_side() -> None:
    delta = ConstraintsDelta(
        include_tags_add=["Jazz", "funk"],
        exclude_tags_add=["funk", "tribute"],
        exclude_recent_months=999,
        max_candidates=300,
    )
    merged = merge(Constraints(**{**DATA["previous"], "exclude_tags": ["jazz"]}), delta)
    assert merged.include_tags == ["jazz"], "already there; 'funk' excluded in the same breath"
    assert merged.exclude_tags == ["tribute"], "'jazz' is wanted again, 'funk' kept out"
    assert merged.exclude_recent_months == 120 and merged.max_candidates == 50

    explicit = merge(PREVIOUS, ConstraintsDelta(size_tier="established", draw_cap=100))
    assert explicit.size_tier == "established", "an explicit tier beats the headcount"
    assert merge(PREVIOUS, ConstraintsDelta()) == PREVIOUS
    assert ConstraintsDelta().is_empty() and not ConstraintsDelta(hints=["x"]).is_empty()


def test_merge_caps_tags_keeping_the_new_ones() -> None:
    many = Constraints(**{**DATA["previous"], "include_tags": [f"g{i}" for i in range(10)]})
    merged = merge(many, ConstraintsDelta(include_tags_add=["new one", "new two"]))
    assert len(merged.include_tags) == MAX_TAGS
    assert merged.include_tags[-2:] == ["new one", "new two"]


# --- explanation ----------------------------------------------------------------------------


def test_explanation_covers_every_kind_of_change() -> None:
    delta = ConstraintsDelta(
        include_tags_add=["soul"],
        include_tags_remove=["jazz"],
        exclude_tags_add=["tribute"],
        exclude_recent_months=12,
        max_candidates=30,
        draw_cap=300,
        hints=["Thursday crowd skews younger"],
    )
    merged = merge(PREVIOUS, delta)
    text = explain(delta, PREVIOUS, merged, ["A", "B", "C"], ["C", "D", "E"])
    for piece in (
        "Looked for emerging acts (a draw of about 300)",
        "added 'soul' to the genres",
        "dropped 'jazz' from the genres",
        "excluded 'tribute'",
        "skipped acts booked in the last 12 months",
        "considered up to 30 candidates",
        "noting 'Thursday crowd skews younger'",
        "2 acts entered the shortlist and 2 left.",
    ):
        assert piece in text, piece
    assert text.count(".") == 1

    same = explain(ConstraintsDelta(hints=["weeknight"]), PREVIOUS, PREVIOUS, ["A"], ["A"])
    assert same == (
        "Kept the constraints and re-ran the search noting 'weeknight'; the shortlist is unchanged."
    )
    gone = explain(
        ConstraintsDelta(size_tier="emerging"),
        PREVIOUS,
        merge(PREVIOUS, ConstraintsDelta(size_tier="emerging")),
        ["A"],
        [],
    )
    assert gone.endswith("; no act survived the search.")
    back = explain(
        ConstraintsDelta(exclude_recent_months=0),
        PREVIOUS,
        merge(PREVIOUS, ConstraintsDelta(exclude_recent_months=0)),
        [],
        ["A"],
    )
    assert back.startswith("Allowed acts booked recently; 1 act entered")


def test_refine_request_names_the_earlier_shortlist_and_hints() -> None:
    delta = ConstraintsDelta(hints=["younger crowd"])
    text = refine_request("Thursday crowd skews younger", delta, SHORTLIST)
    assert ", ".join(SHORTLIST) in text
    assert "Booker's message: Thursday crowd skews younger" in text
    assert text.endswith("younger crowd.")
    assert "Notes" not in refine_request("x", ConstraintsDelta(), [])


# --- the re-run keeps the run's step cap ------------------------------------------------------


async def test_refined_run_stops_at_the_same_step_cap(qloo: QlooMCP) -> None:
    """The re-run is a normal agent run: a runaway model gets exactly MAX_STEPS tool calls."""
    delta = ConstraintsDelta(size_tier="emerging", hints=["younger crowd"])
    merged = merge(PREVIOUS, delta)
    runaway: list[Any] = [[("find_tags", {"query": "jazz", "limit": 3})]]
    model = ScriptedModel(runaway)
    result = await run_venue_agent(
        VENUE,
        merged,
        refine_request("more emerging acts", delta, SHORTLIST),
        qloo=qloo,
        acts=ACTS,
        model=model,
        token_counter=counter(),
    )
    assert result.steps == MAX_STEPS and result.stop_reason == "step_limit"
    assert result.status == "error" and result.shortlist == []
    prompt = "".join(str(b.get("text", "")) for b in model.requests[0][0]["content"])
    assert '"size_tier":"emerging"' in prompt and "Refinement of an earlier shortlist" in prompt


# --- the real model (RUN_INTEGRATION=1) ------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(not os.environ.get("RUN_INTEGRATION"), reason="needs the Anthropic key")
@pytest.mark.parametrize("case", CASES, ids=[c["message"][:28] for c in CASES])
async def test_haiku_parses_the_fixture_messages(case: dict[str, Any]) -> None:
    out = await parse_delta(case["message"], PREVIOUS, SHORTLIST, token_counter=counter())
    assert out.error is None and out.delta is not None, out.model_answer
    assert merge(PREVIOUS, out.delta) == Constraints.model_validate(case["merged"])
