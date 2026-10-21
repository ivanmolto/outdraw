"""`_with_scores` and `RunResult`: the stored run result carries each shortlist entry's
sub-scores and validates into the typed shape the contract exposes."""

from __future__ import annotations

from typing import Any

import pytest

from draw.api.runs import RunResult, _with_scores
from draw.db.models import CandidateRow
from draw.ranking.score import WEIGHTS

BREAKDOWN = {
    "crowd_affinity": 0.9,
    "past_fit": 0.5,
    "local_heat": 0.7,
    "novelty": 1.0,
    "size_fit": 1.0,
    "momentum": 0.5,
}


def row(entity_id: str) -> CandidateRow:
    score: dict[str, Any] = {
        "total": 0.8,
        "breakdown": BREAKDOWN,
        "weights": dict(WEIGHTS),
        "top_reason": f"Reason for {entity_id}.",
        "neutral": ["past_fit", "momentum"],
        "evidence": [],
    }
    return CandidateRow(run_id="run_x", entity_id=entity_id, score=score, evidence={})


def test_entries_gain_scores_and_result_gains_weights() -> None:
    result = {
        "shortlist": [
            {"candidate_id": "c1", "entity_id": "E1", "name": "One", "total": 0.8, "reason": "r"},
            {"candidate_id": "c2", "entity_id": "E2", "name": "Two", "total": 0.7, "reason": None},
        ],
        "notes": [],
        "data_quality": "ok",
    }

    out = _with_scores(result, [row("E1")])

    first, second = out["shortlist"]
    assert first["top_reason"] == "Reason for E1."
    assert first["sub_scores"] == BREAKDOWN
    assert first["neutral"] == ["past_fit", "momentum"]
    assert first["source_labels"] == {
        "crowd_affinity": "qloo",
        "past_fit": "draw",
        "local_heat": "qloo",
        "novelty": "draw",
        "size_fit": "draw",
        "momentum": "draw",
    }
    assert first["reason"] == "r", "the agent's own fields stay"
    assert "sub_scores" not in second, "no stored candidate, no invented scores"
    assert out["weights"] == dict(WEIGHTS)
    assert sum(out["weights"].values()) == pytest.approx(1.0)
    assert "top_reason" not in result["shortlist"][0], "the input is not mutated"


def test_ask_results_pass_through() -> None:
    ask = {"ask": {"question": "Which one?", "candidates": []}, "notes": [], "data_quality": "ok"}

    assert _with_scores(ask, [row("E1")]) is ask


def test_run_result_validates_every_stored_shape() -> None:
    ok = RunResult.model_validate(
        _with_scores(
            {
                "shortlist": [
                    {"candidate_id": "c1", "entity_id": "E1", "name": "One", "total": 0.8},
                ],
                "notes": [],
                "data_quality": "ok",
                "stop_reason": "end_turn",
                "steps": 4,
                "profile_id": "prof_1",
            },
            [row("E1")],
        )
    )
    assert ok.shortlist[0].sub_scores is not None
    assert ok.shortlist[0].sub_scores.crowd_affinity == 0.9
    assert ok.weights is not None and ok.weights.momentum == WEIGHTS["momentum"]

    ask = RunResult.model_validate(
        {"ask": {"question": "Which one?", "candidates": [{"name": "A"}]}, "data_quality": "ok"}
    )
    assert ask.ask is not None and ask.shortlist == []

    failed = RunResult.model_validate({"error": "boom"})
    assert failed.error == "boom" and failed.data_quality == "degraded"
