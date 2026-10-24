"""The evidence trail (T22): complete for every candidate of the NYC fixture run, every item
labelled qloo or draw, stored per candidate at ranking time and retrievable by id."""

from __future__ import annotations

import math
from typing import Any

import pytest
from conftest import load_script

from draw.agent.store import RunStore
from draw.agent.tools import DrawTools
from draw.ranking.evidence import (
    REF_AFFINITY,
    REF_CALL,
    REF_INPUT,
    REF_TOTAL,
    REF_WEIGHT,
    TrailError,
    affinities_by_sub_score,
    assert_complete,
    params_summary,
    validate_trail,
)
from draw.ranking.score import QLOO_SIGNALS, WEIGHTS
from draw.schemas import EvidenceTrail, Score

walkthrough = load_script("walkthrough")


@pytest.fixture(scope="module")
def nyc_run() -> tuple[RunStore, str]:
    """The NYC jazz fixture run: 20 ranked acts, signals for the top 15, trails for all."""
    import asyncio

    store, run_id = asyncio.run(walkthrough.run_nyc_jazz())
    return store, run_id


def scored(store: RunStore, run_id: str) -> list[tuple[str, str, Score, EvidenceTrail]]:
    """(candidate_id, name, score, trail) for every ranked act, best first."""
    run = store.run(run_id)
    out = []
    for item in run.ranked or []:
        candidate_id = run.candidate_ids[item.candidate.entity_id]
        out.append((candidate_id, item.candidate.name, item.score, store.trail(candidate_id)))
    return out


def numbers_of(score: Score) -> dict[str, float]:
    out: dict[str, float] = {REF_TOTAL: score.total}
    for name in WEIGHTS:
        value = getattr(score.breakdown, name)
        if value is not None:
            out[name] = value
            out[f"{REF_WEIGHT}{name}"] = getattr(score.weights, name)
    return out


# --- completeness ---------------------------------------------------------------------


def test_validator_passes_for_every_candidate(nyc_run: tuple[RunStore, str]) -> None:
    rows = scored(*nyc_run)
    assert len(rows) == 20
    for _, name, score, trail in rows:
        assert validate_trail(score, trail) == [], name
        assert_complete(score, trail)


def test_every_number_in_the_score_is_in_the_trail_with_a_label(
    nyc_run: tuple[RunStore, str],
) -> None:
    for _, name, score, trail in scored(*nyc_run):
        found: dict[str, float | None] = {
            REF_TOTAL: trail.total,
            **{n: getattr(trail.sub_scores, n) for n in WEIGHTS},
            **{f"{REF_WEIGHT}{n}": getattr(trail.weights, n) for n in WEIGHTS},
        }
        for ref, value in numbers_of(score).items():
            in_trail = found[ref]
            assert in_trail is not None and math.isclose(in_trail, value, abs_tol=1e-9), (name, ref)
            assert trail.source_labels[ref] in ("qloo", "draw"), (name, ref)


def test_every_item_is_labelled_qloo_or_draw_and_nothing_else(
    nyc_run: tuple[RunStore, str],
) -> None:
    for _, name, _score, trail in scored(*nyc_run):
        labels = trail.source_labels
        assert set(labels.values()) <= {"qloo", "draw"}, name
        items = {REF_TOTAL, *WEIGHTS, *(f"{REF_WEIGHT}{n}" for n in WEIGHTS)}
        items |= {f"{REF_INPUT}{i.ref}" for i in trail.inputs}
        items |= {f"{REF_CALL}{c.call_id}" for c in trail.qloo_calls}
        items |= {f"{REF_AFFINITY}{n}" for n in affinities_by_sub_score(trail)}
        assert set(labels) == items, name
        # Draw's own arithmetic and inputs are never passed off as Qloo data.
        assert labels[REF_TOTAL] == "draw" and labels["novelty"] == "draw"
        assert labels["size_fit"] == "draw"
        assert all(labels[f"{REF_WEIGHT}{n}"] == "draw" for n in WEIGHTS)
        assert all(labels[f"{REF_CALL}{c.call_id}"] == "qloo" for c in trail.qloo_calls)
        assert labels[f"{REF_INPUT}popularity"] == "qloo"
        assert labels[f"{REF_INPUT}region"] == "draw"


def test_measured_acts_are_qloo_and_fallbacks_are_draw(nyc_run: tuple[RunStore, str]) -> None:
    rows = scored(*nyc_run)
    measured, unmeasured = rows[:15], rows[15:]
    assert unmeasured, "the run has acts past the signals budget"
    for _, name, score, trail in measured:
        by_sub = affinities_by_sub_score(trail)
        for sub in ("crowd_affinity", "past_fit", "local_heat"):
            assert trail.source_labels[sub] == "qloo", (name, sub)
            assert math.isclose(by_sub[sub].value, getattr(score.breakdown, sub)), (name, sub)
            assert by_sub[sub].call_id in {c.call_id for c in trail.qloo_calls}
        # The trends fixture carries no series for these acts: momentum fell back, honestly.
        assert "momentum" in score.neutral and trail.source_labels["momentum"] == "draw"
    for _, name, score, trail in unmeasured:
        assert set(score.neutral) == set(QLOO_SIGNALS), name
        assert all(trail.source_labels[sub] == "draw" for sub in QLOO_SIGNALS), name
        assert set(affinities_by_sub_score(trail)) == {"crowd_affinity"}, name
        assert {c.workflow for c in trail.qloo_calls} == {"recommend"}, name


def test_inputs_carry_the_weights(nyc_run: tuple[RunStore, str]) -> None:
    _, _, _, trail = scored(*nyc_run)[0]
    signals = [i for i in trail.inputs if i.ref.startswith("signal:")]
    assert [i.label.split("weight ")[1][:4] for i in signals] == ["1.00", "0.67", "0.30"]
    assert {i.ref for i in trail.inputs} >= {
        "region",
        "size_tier",
        "popularity",
        "months_since_booked",
    }


# --- the validator catches orphans -----------------------------------------------------


def broken(trail: EvidenceTrail, **changes: Any) -> EvidenceTrail:
    return trail.model_copy(update=changes, deep=True)


def test_validator_reports_orphan_numbers_and_labels(nyc_run: tuple[RunStore, str]) -> None:
    _, _, score, trail = scored(*nyc_run)[0]

    wrong_sub = broken(
        trail, sub_scores=trail.sub_scores.model_copy(update={"crowd_affinity": 0.1})
    )
    assert any(p.startswith("crowd_affinity: score has") for p in validate_trail(score, wrong_sub))

    wrong_total = broken(trail, total=0.123)
    problems = validate_trail(score, wrong_total)
    assert any(p.startswith("total: score has") for p in problems)
    assert any("weighted sum" in p for p in problems)

    labels = dict(trail.source_labels)
    del labels["past_fit"]
    assert "past_fit: no source label" in validate_trail(score, broken(trail, source_labels=labels))

    labels = {**trail.source_labels, "affinity:novelty": "qloo"}
    assert "affinity:novelty: label without an item" in validate_trail(
        score, broken(trail, source_labels=labels)
    )

    labels = {**trail.source_labels, "momentum": "qloo"}  # a neutral fallback passed off as Qloo
    problems = validate_trail(score, broken(trail, source_labels=labels))
    assert "momentum: neutral fallback labelled qloo" in problems
    assert any(p.startswith("momentum: labelled qloo but no affinity") for p in problems)

    labels = {**trail.source_labels, "crowd_affinity": "draw"}
    assert "crowd_affinity: has a Qloo affinity but is labelled draw" in validate_trail(
        score, broken(trail, source_labels=labels)
    )

    no_calls = broken(
        trail,
        qloo_calls=[],
        source_labels={k: v for k, v in trail.source_labels.items() if not k.startswith(REF_CALL)},
    )
    problems = validate_trail(score, no_calls)
    assert any("not in trail" in p for p in problems)
    assert any("in Score.evidence but not in the trail" in p for p in problems)

    with pytest.raises(TrailError, match="crowd_affinity"):
        assert_complete(score, wrong_sub)


# --- stored and retrievable --------------------------------------------------------------


async def test_trail_is_stored_at_ranking_and_retrievable(nyc_run: tuple[RunStore, str]) -> None:
    store, run_id = nyc_run
    run = store.run(run_id)
    assert set(run.trails) == set(run.candidate_ids.values())
    tools = DrawTools(store, walkthrough.FixtureExec(walkthrough.QlooConfig(qloo_bin="/x")))
    candidate_id, name, score, trail = scored(store, run_id)[0]

    full = tools.evidence_trail(candidate_id)
    assert full["candidate_id"] == candidate_id and full["name"] == name
    round_trip = EvidenceTrail.model_validate({k: full[k] for k in EvidenceTrail.model_fields})
    assert round_trip == trail
    assert validate_trail(score, round_trip) == []

    view = await tools.get_evidence(candidate_id)
    assert view["total"] == round(trail.total, 4)
    assert {c["call_id"] for c in view["qloo_calls"]} == {c.call_id for c in trail.qloo_calls}
    assert {a["sub_score"] for a in view["affinities"]} == set(affinities_by_sub_score(trail))
    assert view["source_labels"] == {
        k: v for k, v in trail.source_labels.items() if k in WEIGHTS or k == REF_TOTAL
    }
    with pytest.raises(ValueError, match="Unknown candidate id"):
        store.trail("cand_nope")


def test_params_summary_shows_shape_not_ids() -> None:
    request: dict[str, Any] = {
        "target_type": "artist",
        "signals": ["746CD02C-8299-5FBF-9C15-615CBD354946", "EE4EDBA1-C8A0-5039-B5E2-147F264D4C81"],
        "signal_location": "New York, United States",
        "include_tags": ["urn:tag:genre:music:jazz"],
        "limit": 20,
        "semantic": True,
    }
    summary = params_summary("recommend", request)
    assert summary == (
        'recommend(target_type=artist, signals[2], signal_location="New York, United States", '
        "include_tags[1], limit=20, semantic=true)"
    )
    assert params_summary("where_popular", {"entity": request["signals"][0]}) == (
        "where_popular(entity=<id>)"
    )
    assert "746CD02C" not in summary and "urn:tag" not in summary
