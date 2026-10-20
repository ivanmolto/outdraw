"""The deterministic score: pure, bounded, and honest about neutral fallbacks."""

from __future__ import annotations

from typing import Any

import pytest

from draw.ranking.candidates import POPULARITY_BANDS
from draw.ranking.score import (
    NEUTRAL,
    NOVELTY_HORIZON_MONTHS,
    WEIGHTS,
    novelty_of,
    score_candidate,
    size_fit_of,
)
from draw.ranking.signals import CandidateSignals, SignalValue
from draw.schemas import Candidate, Constraints, CrowdProfile, EvidenceRef, Score, SizeTier

CALL = "00000000-0000-0000-0000-00000000c000"
PROFILE = CrowdProfile(
    venue_id="synthetic-room-new-york",
    signals=[],
    genres=[],
    related_artists=[],
    places=[],
    films=[],
    brands=[],
    evidence=[],
    data_quality="ok",
)


def constraints(tier: SizeTier = "established") -> Constraints:
    return Constraints(
        include_tags=[], exclude_tags=[], size_tier=tier, exclude_recent_months=0, max_candidates=40
    )


def candidate(*, affinity: float = 0.8, popularity: float = 0.9995) -> Candidate:
    return Candidate(
        entity_id="ID-C0",
        name="Synthetic Act",
        popularity=popularity,
        affinity=affinity,
        tags=[],
        evidence=[EvidenceRef(item_ref="ID-C0", call_id=CALL)],
    )


def signals(
    *,
    past_fit: float | None = 0.6,
    local_heat: float | None = 0.7,
    momentum: float | None = 0.55,
    months: int | None = None,
) -> CandidateSignals:
    def sv(value: float | None, suffix: str) -> SignalValue:
        return SignalValue(value, f"00000000-0000-0000-0000-0000000000{suffix}", "ok")

    return CandidateSignals(
        entity_id="ID-C0",
        past_fit=sv(past_fit, "a1"),
        local_heat=sv(local_heat, "b2"),
        momentum=sv(momentum, "c3"),
        months_since_booked=months,
    )


_UNSET: Any = object()


def score(
    c: Candidate | None = None,
    s: CandidateSignals | None = _UNSET,
    *,
    tier: SizeTier = "established",
) -> Score:
    if s is _UNSET:
        s = signals()
    return score_candidate(c or candidate(), PROFILE, constraints(tier), s)


# --- shape ---------------------------------------------------------------------------


def test_weights_sum_to_one_and_are_exported() -> None:
    assert sum(WEIGHTS.values()) == pytest.approx(1.0)
    assert list(WEIGHTS) == [
        "crowd_affinity",
        "past_fit",
        "local_heat",
        "novelty",
        "size_fit",
        "momentum",
    ]
    result = score()
    assert result.weights.model_dump() == dict(WEIGHTS)


def test_every_sub_score_in_range_and_total_is_weighted_sum() -> None:
    for aff in (0.0, 0.37, 1.0):
        for months in (None, 0, 3, 30):
            result = score(candidate(affinity=aff), signals(months=months))
            breakdown = result.breakdown.model_dump()
            assert all(0.0 <= v <= 1.0 for v in breakdown.values())
            assert result.total == pytest.approx(
                sum(WEIGHTS[k] * breakdown[k] for k in WEIGHTS), abs=1e-9
            )
            assert 0.0 <= result.total <= 1.0


def test_score_validates_against_the_shared_schema_with_evidence() -> None:
    result = score()
    assert Score.model_validate(result.model_dump(mode="json")) == result
    refs = {e.item_ref: e.call_id for e in result.evidence}
    assert refs == {
        "crowd_affinity": CALL,
        "past_fit": "00000000-0000-0000-0000-0000000000a1",
        "local_heat": "00000000-0000-0000-0000-0000000000b2",
        "momentum": "00000000-0000-0000-0000-0000000000c3",
    }
    assert result.neutral == []


def test_pure_same_input_same_score() -> None:
    assert score() == score()


# --- ordering properties ----------------------------------------------------------------


def test_high_affinity_beats_low() -> None:
    high = score(candidate(affinity=0.9))
    low = score(candidate(affinity=0.2))
    assert high.total > low.total
    assert high.breakdown.crowd_affinity == 0.9


def test_recently_booked_act_has_low_novelty() -> None:
    recent = score(candidate(), signals(months=2))
    never = score(candidate(), signals(months=None))
    old = score(candidate(), signals(months=NOVELTY_HORIZON_MONTHS + 1))
    assert recent.breakdown.novelty < 0.5
    assert never.breakdown.novelty == 1.0 == old.breakdown.novelty
    assert recent.total < never.total
    assert novelty_of(NOVELTY_HORIZON_MONTHS // 2) == pytest.approx(0.5)


def test_size_fit_decays_by_band_distance() -> None:
    emerging_pop = POPULARITY_BANDS["emerging"][0]
    mid_pop = POPULARITY_BANDS["mid"][0]
    established_pop = POPULARITY_BANDS["established"][0]
    assert size_fit_of(established_pop, "established") == 1.0
    assert size_fit_of(1.0, "established") == 1.0
    assert size_fit_of(mid_pop, "established") == 0.5
    assert size_fit_of(emerging_pop, "established") == 0.0
    assert size_fit_of(emerging_pop, "emerging") == 1.0
    assert score(candidate(popularity=mid_pop), tier="mid").breakdown.size_fit == 1.0


# --- neutral fallbacks ------------------------------------------------------------------


def test_local_heat_and_momentum_neutral_when_none_and_flagged() -> None:
    result = score(candidate(), signals(local_heat=None, momentum=None))
    assert result.breakdown.local_heat == NEUTRAL == 0.5
    assert result.breakdown.momentum == NEUTRAL
    assert sorted(result.neutral) == ["local_heat", "momentum"]
    assert {e.item_ref for e in result.evidence} == {"crowd_affinity", "past_fit"}


def test_no_signals_at_all_is_neutral_on_every_qloo_signal() -> None:
    result = score(candidate(), None)
    assert sorted(result.neutral) == ["local_heat", "momentum", "past_fit"]
    assert (
        result.breakdown.past_fit == result.breakdown.local_heat == result.breakdown.momentum == 0.5
    )
    assert result.breakdown.novelty == 1.0  # never booked: no venue data says otherwise
    assert [e.item_ref for e in result.evidence] == ["crowd_affinity"]


def test_neutral_fallback_never_becomes_the_top_reason() -> None:
    # Everything measured is low; the neutral 0.5s would win on value alone.
    result = score(
        candidate(affinity=0.1, popularity=0.5),
        signals(past_fit=None, local_heat=None, momentum=None, months=1),
    )
    assert result.top_reason
    assert "where_popular" not in result.top_reason
    assert "trends" not in result.top_reason
    assert "compare_audiences" not in result.top_reason


# --- top reason -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "workflow"),
    [
        ({"affinity": 0.99}, "recommend"),
        ({"past_fit": 0.99}, "compare_audiences"),
        ({"local_heat": 0.99}, "where_popular"),
        ({"momentum": 0.99}, "trends"),
    ],
)
def test_top_reason_names_the_workflow_behind_the_best_sub_score(
    kwargs: dict[str, float], workflow: str
) -> None:
    # Everything else is low: size_fit 0 (popularity 0.5 vs established), novelty 1/24.
    c = candidate(affinity=kwargs.pop("affinity", 0.3), popularity=0.5)
    low: dict[str, Any] = {"past_fit": 0.3, "local_heat": 0.3, "momentum": 0.3, "months": 1}
    s = signals(**{**low, **kwargs})
    result = score(c, s)
    assert workflow in result.top_reason
    assert "Synthetic Act" in result.top_reason
    assert "99%" in result.top_reason


def test_top_reason_for_novelty_and_size_fit_name_the_venue_data() -> None:
    c = candidate(affinity=0.3)
    novel = score(c, signals(past_fit=0.3, local_heat=0.3, momentum=0.3, months=None))
    assert "never played this room" in novel.top_reason
    sized = score(c, signals(past_fit=0.3, local_heat=0.3, momentum=0.3, months=1))
    assert "established band" in sized.top_reason
