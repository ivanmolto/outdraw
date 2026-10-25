"""The booking brief (T27) without a database: the brief validates against the shared
BookingBrief schema, every fit reason cites an evidence ref that exists, the support act is
the smaller act with the highest compare_audiences overlap (one call), an unusable draft is
retried once and then replaced by Outdraw's template, and the email names the venue and the
act. The `draft_brief` tool runs the writer on a real pipeline and caches the result.

The route (`GET /runs/{id}/candidates/{entity_id}/brief`) is covered by
`tests/api/test_brief_route.py`.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest
from conftest import FakeHarness, fixture_text
from scripted_model import INPUT_TOKENS, OUTPUT_TOKENS, ScriptedModel
from test_tools import CONSTRAINTS, TODAY, VENUE, act, check, router

from draw.agent.brief import (
    BRIEF_SCHEMA,
    MAX_ATTEMPTS,
    MAX_REASONS,
    BriefAct,
    BriefContext,
    BriefOut,
    draft_brief,
    evidence_items,
    night_format_for,
    partners_for,
    pick_support,
    problems_of,
    support_pool,
)
from draw.agent.store import RunStore
from draw.agent.tools import DrawTools
from draw.qloo import exec_client
from draw.qloo.budget import LLM_PREFIX, MemoryCounter
from draw.qloo.config import QlooConfig
from draw.qloo.envelope import BudgetExceeded
from draw.qloo.exec_client import QlooExec
from draw.ranking.evidence import REF_TOP_REASON
from draw.ranking.score import WEIGHTS
from draw.schemas import (
    Affinity,
    BookingBrief,
    Constraints,
    CrowdProfile,
    EvidenceTrail,
    ProfileItem,
    QlooCallRecord,
    ScoreBreakdown,
    ScoreWeights,
    TrailInput,
)

CALL_TOKENS = INPUT_TOKENS + OUTPUT_TOKENS

# The support_pool fixture: one headliner against three smaller acts; act B2 carries 7 of the
# 10 shared tags, B1 five, B3 two.
HEADLINER = "00000000-0000-5000-8000-0000000000A0"
B1, B2, B3 = (
    "00000000-0000-5000-8000-0000000000B1",
    "00000000-0000-5000-8000-0000000000B2",
    "00000000-0000-5000-8000-0000000000B3",
)
CALL_ID = "11111111-1111-4111-8111-111111111111"
TOP_REASON = "Qloo's recommend puts Synthetic Headliner at 91% affinity with this crowd."


def counter(cap: int = 10_000_000) -> MemoryCounter:
    return MemoryCounter(cap, prefix=LLM_PREFIX, what="LLM token", charge_over=True)


def trail() -> EvidenceTrail:
    """A complete-looking trail: one recommend call, its affinity, the usual inputs."""
    breakdown = ScoreBreakdown(
        crowd_affinity=0.91, past_fit=0.5, local_heat=0.5, novelty=1.0, size_fit=1.0
    )
    weights = ScoreWeights(**{k: v for k, v in WEIGHTS.items() if k != "momentum"})
    total = sum(getattr(weights, k) * getattr(breakdown, k) for k in WEIGHTS if k != "momentum")
    labels: dict[str, Any] = {
        "crowd_affinity": "qloo",
        "past_fit": "draw",
        "local_heat": "draw",
        "novelty": "draw",
        "size_fit": "draw",
        "total": "draw",
        "top_reason": "draw",
        f"call:{CALL_ID}": "qloo",
        "affinity:crowd_affinity": "qloo",
        "input:region": "draw",
        "input:popularity": "qloo",
        "profile:places": "qloo",
        "profile:films": "qloo",
        "profile:brands": "qloo",
    }
    for name in ("crowd_affinity", "past_fit", "local_heat", "novelty", "size_fit"):
        labels[f"weight:{name}"] = "draw"
    return EvidenceTrail(
        inputs=[
            TrailInput(ref="region", label="Venue region: New York, United States"),
            TrailInput(ref="popularity", label="Qloo popularity 0.999 (recommend)"),
        ],
        qloo_calls=[
            QlooCallRecord(
                call_id=CALL_ID,
                workflow="recommend",
                params_summary="recommend(target_type=artist, signals[2], limit=20)",
                status="ok",
                cache_hit=False,
            )
        ],
        affinities=[Affinity(entity=HEADLINER, value=0.91, call_id=CALL_ID)],
        sub_scores=breakdown,
        weights=weights,
        total=total,
        source_labels=labels,
    )


def profile(demographics: dict[str, Any] | None = None) -> CrowdProfile:
    return CrowdProfile(
        venue_id="ven_test",
        signals=[],
        genres=[],
        related_artists=[],
        places=[
            ProfileItem(entity_id="P1", name="Synthetic Bar", affinity=0.9),
            ProfileItem(entity_id="P2", name="Synthetic Diner", affinity=0.8),
            ProfileItem(entity_id="P3", name="Synthetic Cafe", affinity=0.7),
        ],
        films=[],
        brands=[],
        demographics=demographics,
        evidence=[],
        data_quality="ok",
    )


def ranked_acts() -> list[BriefAct]:
    """The headliner (established) with mid-band and emerging acts around it."""
    return [
        BriefAct(entity_id=HEADLINER, name="Synthetic Headliner", popularity=0.9995, total=0.9),
        BriefAct(entity_id=B1, name="Synthetic Opener 1", popularity=0.998, total=0.85),
        BriefAct(entity_id="E-OTHER", name="Synthetic Big", popularity=0.9999, total=0.84),
        BriefAct(entity_id=B2, name="Synthetic Opener 2", popularity=0.997, total=0.8),
        BriefAct(entity_id="E-SMALL", name="Synthetic Small", popularity=0.9, total=0.7),
        BriefAct(entity_id=B3, name="Synthetic Opener 3", popularity=0.9965, total=0.6),
    ]


def context(act_id: str = HEADLINER, demographics: dict[str, Any] | None = None) -> BriefContext:
    acts = ranked_acts()
    return BriefContext(
        run_id="run_x",
        venue=VENUE,
        act=next(a for a in acts if a.entity_id == act_id),
        top_reason=TOP_REASON,
        trail=trail(),
        profile=profile(demographics),
        constraints=Constraints.model_validate_json(CONSTRAINTS),
        ranked=acts,
    )


def gateway() -> QlooExec:
    return QlooExec(QlooConfig(qloo_bin="/fake/qloo"))


def answer(
    refs: list[str],
    *,
    venue: str = VENUE.name,
    act_name: str = "Synthetic Headliner",
    support: str | None = "Wrong Support",
) -> str:
    """A model answer in the BookingBrief shape citing `refs`; the facts are deliberately
    wrong so the test can see Outdraw overwrite them."""
    return json.dumps(
        {
            "why_it_fits": [
                {"text": f"Reason {i + 1} for this room.", "evidence_ref": ref}
                for i, ref in enumerate(refs)
            ],
            "night_format": "Whatever the model says",
            "support_act": support,
            "partners": ["Model Partner"],
            "outreach_email": {
                "subject": f"{act_name} at {venue}",
                "body": f"Hello, we book {venue} and would love to host {act_name}. Best.",
            },
        }
    )


# --- the parts ---------------------------------------------------------------------------


def test_brief_schema_matches_the_shared_shape() -> None:
    assert set(BRIEF_SCHEMA["required"]) == set(BookingBrief.model_fields)
    assert BRIEF_SCHEMA["additionalProperties"] is False
    assert set(BRIEF_SCHEMA["properties"]["why_it_fits"]["items"]["required"]) == {
        "text",
        "evidence_ref",
    }
    assert json.dumps(BRIEF_SCHEMA), "serialisable for output_config.format"
    assert "minLength" not in json.dumps(BRIEF_SCHEMA), "no bounds the API would reject"


@pytest.mark.parametrize(
    ("ages", "expected"),
    [
        (None, "standard format"),
        ({"25_to_29": 0.99, "45_to_54": 0.9}, "Late show"),
        ({"35_to_44": 0.99, "25_to_29": 0.9}, "Evening show"),
        ({"55_and_older": 0.99, "35_to_44": 0.9}, "Early show"),
        ({"unknown_bucket": 0.99}, "standard format"),
    ],
)
def test_night_format_is_templated_from_the_age_skew(
    ages: dict[str, float] | None, expected: str
) -> None:
    demographics = {"age": ages, "gender": {"male": 0.9, "female": 0.9}} if ages else None
    text = night_format_for(demographics)
    assert expected in text
    if ages and "unknown_bucket" not in ages:
        assert "Qloo audience demographics" in text


def test_partners_are_the_top_two_places() -> None:
    assert partners_for(profile()) == ["Synthetic Bar", "Synthetic Diner"]
    assert partners_for(profile().model_copy(update={"places": []})) == []


def test_support_pool_is_one_tier_smaller_best_scored_first() -> None:
    pool = support_pool(context())
    assert [a.entity_id for a in pool] == [B1, B2, B3], "mid band only, by score"
    assert support_pool(context(act_id="E-SMALL")) == [], "an emerging act has no smaller tier"
    mid = context(act_id=B1)
    assert [a.entity_id for a in support_pool(mid)] == ["E-SMALL"]


async def test_support_act_is_the_smaller_act_with_the_highest_overlap(
    harness: Callable[..., FakeHarness],
) -> None:
    fake = harness((fixture_text("compare_audiences", "support_pool"), "", 0))
    pick = await pick_support(context(), gateway())

    assert fake.spawns == 1 and fake.workflows() == ["compare_audiences"]
    assert fake.inputs[0] == {"group_a": [HEADLINER], "group_b": [B1, B2, B3], "limit": 10}
    assert pick.entity_id == B2 and pick.name == "Synthetic Opener 2"
    assert pick.overlap == pytest.approx(0.7), "7 of 10 shared tags"
    assert pick.status == "ok" and pick.call_id and pick.pool == 3 and pick.note is None
    assert pick.entity_id != B1, "the best-scored smaller act is not the pick: overlap rules"


async def test_support_without_per_act_detail_is_the_best_scored_with_a_note(
    harness: Callable[..., FakeHarness],
) -> None:
    harness((fixture_text("compare_audiences", "two_artists"), "", 0))
    pick = await pick_support(context(), gateway())
    assert pick.entity_id == B1 and pick.overlap is None
    assert pick.note and "best-scored smaller act" in pick.note


async def test_support_without_a_smaller_act_or_without_data_is_none(
    harness: Callable[..., FakeHarness],
) -> None:
    fake = harness()
    none = await pick_support(context(act_id="E-SMALL"), gateway())
    assert none.entity_id is None and none.note and fake.spawns == 0, "no call without a pool"

    fake = harness((fixture_text("recommend", "derived_empty"), "", 0))
    empty = await pick_support(context(), gateway())
    assert empty.entity_id is None and empty.status == "empty" and empty.note
    assert fake.spawns == 1


def test_evidence_items_cover_every_trail_label_and_the_support_call() -> None:
    ctx = context()
    items = evidence_items(ctx, pick := _ok_pick())
    assert set(ctx.trail.source_labels) <= set(items)
    assert items[REF_TOP_REASON].label == TOP_REASON and items[REF_TOP_REASON].source == "draw"
    assert (
        items[f"call:{CALL_ID}"].source == "qloo" and "recommend(" in items[f"call:{CALL_ID}"].label
    )
    assert "0.91" in items["affinity:crowd_affinity"].label
    assert "Synthetic Bar" in items["profile:places"].label
    support_key = f"call:{pick.call_id}"
    assert items[support_key].source == "qloo" and "Synthetic Opener 2" in items[support_key].label


def _ok_pick() -> Any:
    from draw.agent.brief import SupportPick

    return SupportPick(
        entity_id=B2,
        name="Synthetic Opener 2",
        overlap=0.7,
        call_id="22222222-2222-4222-8222-222222222222",
        status="ok",
        pool=3,
    )


def test_problems_name_unknown_refs_and_missing_names() -> None:
    ctx = context()
    items = evidence_items(ctx, _ok_pick())
    good = BookingBrief.model_validate_json(answer([REF_TOP_REASON]))
    assert problems_of(good, items, ctx.venue, ctx.act) == []

    bad = BookingBrief.model_validate_json(
        answer(["call:not-a-call", REF_TOP_REASON], venue="Some Other Room", act_name="Nobody")
    )
    problems = problems_of(bad, items, ctx.venue, ctx.act)
    assert len(problems) == 3
    assert "call:not-a-call" in problems[0]
    assert "venue" in problems[1] and VENUE.name in problems[1]
    assert "act" in problems[2] and "Synthetic Headliner" in problems[2]


# --- the writer ----------------------------------------------------------------------------


async def test_one_call_validates_and_keeps_outdraws_facts(
    harness: Callable[..., FakeHarness],
) -> None:
    harness((fixture_text("compare_audiences", "support_pool"), "", 0))
    model = ScriptedModel([answer([REF_TOP_REASON, f"call:{CALL_ID}"])])
    count = counter()
    out = await draft_brief(
        context(demographics={"age": {"25_to_29": 0.99, "55_and_older": 0.5}}),
        qloo=gateway(),
        model_factory=lambda: model,
        token_counter=count,
    )

    assert isinstance(out, BriefOut) and out.error is None and out.attempts == 1
    BookingBrief.model_validate(out.brief.model_dump())  # the shared schema
    assert len(model.requests) == 1 and model.tool_specs == []
    assert out.token_usage == {"model_calls": 1, "tokens_counted": CALL_TOKENS}
    assert await count.used() == CALL_TOKENS

    brief = out.brief
    assert [r.evidence_ref for r in brief.why_it_fits] == [REF_TOP_REASON, f"call:{CALL_ID}"]
    assert all(r.evidence_ref in out.evidence for r in brief.why_it_fits)
    # The model's facts are overwritten with Outdraw's.
    assert brief.support_act == "Synthetic Opener 2" and out.support.entity_id == B2
    assert brief.partners == ["Synthetic Bar", "Synthetic Diner"]
    assert brief.night_format.startswith("Late show")
    assert (
        VENUE.name in brief.outreach_email.body
        and "Synthetic Headliner" in brief.outreach_email.body
    )
    assert out.source_labels == {
        "why_it_fits": "draw",
        "night_format": "draw",
        "support_act": "qloo",
        "partners": "qloo",
        "outreach_email": "draw",
    }
    assert out.notes == [] and not out.cached

    prompt = "".join(str(b.get("text", "")) for b in model.requests[0][0]["content"])
    assert "Synthetic Room" in prompt and f"call:{CALL_ID}" in prompt
    assert "Synthetic Opener 2" in prompt and "Synthetic Bar" in prompt
    assert "provenance" not in prompt and "fake" not in prompt.lower()


async def test_unknown_ref_is_retried_once_with_the_problems(
    harness: Callable[..., FakeHarness],
) -> None:
    harness((fixture_text("compare_audiences", "support_pool"), "", 0))
    model = ScriptedModel([answer(["call:ghost"]), answer([REF_TOP_REASON])])
    out = await draft_brief(
        context(), qloo=gateway(), model_factory=lambda: model, token_counter=counter()
    )

    assert out.attempts == 2 and out.error is None
    assert [r.evidence_ref for r in out.brief.why_it_fits] == [REF_TOP_REASON]
    assert len(model.requests) == 2
    retry = model.requests[1][-1]
    assert retry["role"] == "user"
    text = "".join(str(b.get("text", "")) for b in retry["content"])
    assert "call:ghost" in text and "not in the evidence list" in text
    assert out.token_usage["model_calls"] == 2


async def test_two_unusable_drafts_fall_back_to_the_template(
    harness: Callable[..., FakeHarness],
) -> None:
    harness((fixture_text("compare_audiences", "support_pool"), "", 0))
    bad = answer([REF_TOP_REASON], venue="Elsewhere", act_name="Nobody")
    model = ScriptedModel([bad, "not even json"])
    out = await draft_brief(
        context(), qloo=gateway(), model_factory=lambda: model, token_counter=counter()
    )

    assert out.attempts == MAX_ATTEMPTS == 2
    assert out.error and "expected JSON shape" in out.error
    brief = out.brief
    assert brief.why_it_fits[0].text == TOP_REASON
    assert brief.why_it_fits[0].evidence_ref == REF_TOP_REASON and REF_TOP_REASON in out.evidence
    assert brief.support_act == "Synthetic Opener 2" and brief.partners == [
        "Synthetic Bar",
        "Synthetic Diner",
    ]
    assert (
        VENUE.name in brief.outreach_email.body
        and "Synthetic Headliner" in brief.outreach_email.body
    )
    assert "Synthetic Headliner" in brief.outreach_email.subject
    assert any("template" in n for n in out.notes)


async def test_too_many_reasons_are_trimmed(harness: Callable[..., FakeHarness]) -> None:
    harness((fixture_text("compare_audiences", "support_pool"), "", 0))
    model = ScriptedModel([answer([REF_TOP_REASON] * (MAX_REASONS + 3))])
    out = await draft_brief(
        context(), qloo=gateway(), model_factory=lambda: model, token_counter=counter()
    )
    assert out.error is None and len(out.brief.why_it_fits) == MAX_REASONS


async def test_budget_guard_before_the_model_is_built(harness: Callable[..., FakeHarness]) -> None:
    fake = harness()
    built: list[ScriptedModel] = []

    def factory() -> ScriptedModel:
        built.append(ScriptedModel([answer([REF_TOP_REASON])]))
        return built[-1]

    count = counter(cap=500)
    await count.reserve(500)
    with pytest.raises(BudgetExceeded):
        await draft_brief(context(), qloo=gateway(), model_factory=factory, token_counter=count)
    assert built == [] and fake.spawns == 0, "neither the model nor Qloo is touched at the cap"


# --- the tool ------------------------------------------------------------------------------


async def test_draft_brief_tool_runs_the_pipeline_and_caches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from conftest import RouterHarness

    fake = RouterHarness(router)
    monkeypatch.setattr(exec_client, "create_subprocess_exec", fake)
    store = RunStore()
    venue_id = store.add_venue(VENUE, venue_id="ven_test")
    store.add_resolved(venue_id, act("Act Full", "ID-FULL"))
    store.add_resolved(venue_id, act("Act Mid", "ID-MID"))
    models: list[ScriptedModel] = []

    def factory() -> ScriptedModel:
        models.append(ScriptedModel([answer([REF_TOP_REASON], act_name=top_name)]))
        return models[-1]

    tools = DrawTools(
        store,
        QlooExec(QlooConfig(qloo_bin="/fake/qloo")),
        today=TODAY,
        model_factory=factory,
        token_counter=counter(),
    )
    profile_ = await tools.build_crowd_profile(venue_id)
    run = await tools.generate_candidates(profile_["profile_id"], CONSTRAINTS)
    ranked = await tools.score_and_rank(run["run_id"])
    top = ranked["shortlist"][0]["candidate_id"]
    top_name = ranked["shortlist"][0]["name"]
    spawns = fake.spawns

    brief = await tools.draft_brief(top)
    check(brief)
    assert brief["cached"] is False and brief["error"] is None and brief["name"] == top_name
    assert brief["why_it_fits"] == [
        {"text": "Reason 1 for this room.", "evidence_ref": REF_TOP_REASON}
    ]
    # Every synthetic candidate sits in the emerging band: nothing smaller to open, no call.
    assert brief["support_act"] is None and fake.spawns == spawns
    assert any("smaller" in n for n in brief["notes"])
    assert brief["partners"] and brief["outreach_subject"] and brief["outreach_preview"]
    assert len(models) == 1

    again = await tools.draft_brief(top)
    assert again == {**brief, "cached": True} and len(models) == 1, "no second model call"
    full = tools.brief(top)
    assert isinstance(full, BriefOut) and full.brief.why_it_fits[0].evidence_ref in full.evidence
    scored = store.run(run["run_id"]).ranked
    assert scored is not None
    assert tools.evidence_trail(top)["popularity"] == scored[0].candidate.popularity
    with pytest.raises(ValueError, match="No brief"):
        tools.brief(ranked["shortlist"][1]["candidate_id"])
