"""The deterministic order: hand-built expectations, the tie-break rule and properties."""

from __future__ import annotations

import copy
import itertools
import json
from collections.abc import Callable
from typing import Any

import pytest
from conftest import FIXTURES_DIR, RouterHarness
from hypothesis import given, settings
from hypothesis import strategies as st

from draw.qloo import QlooExec, exec_client
from draw.qloo.config import QlooConfig
from draw.ranking.rank import RANK_SIGNALS, RankedCandidate, rank, rank_given, sort_key
from draw.ranking.score import WEIGHTS
from draw.ranking.signals import CandidateSignals, SignalValue
from draw.schemas import (
    Candidate,
    Constraints,
    CrowdProfile,
    EvidenceRef,
    Score,
    ScoreBreakdown,
    ScoreWeights,
    Signal,
)

CALL = "00000000-0000-0000-0000-00000000c000"
CONSTRAINTS = Constraints(
    include_tags=[], exclude_tags=[], size_tier="established", exclude_recent_months=0,
    max_candidates=40,
)  # fmt: skip


def profile(*signals: str) -> CrowdProfile:
    return CrowdProfile(
        venue_id="synthetic-room-new-york",
        signals=[Signal(entity_id=s, weight=1.0) for s in signals],
        genres=[],
        related_artists=[],
        places=[],
        films=[],
        brands=[],
        evidence=[],
        data_quality="ok",
    )


PROFILE = profile()


def candidate(entity_id: str, name: str, affinity: float, popularity: float = 0.9995) -> Candidate:
    return Candidate(
        entity_id=entity_id,
        name=name,
        popularity=popularity,
        affinity=affinity,
        tags=[],
        evidence=[EvidenceRef(item_ref=entity_id, call_id=CALL)],
    )


def signals(
    entity_id: str,
    *,
    past_fit: float | None = None,
    local_heat: float | None = None,
    momentum: float | None = None,
    months: int | None = None,
) -> CandidateSignals:
    def sv(value: float | None) -> SignalValue:
        return SignalValue(value, CALL if value is not None else None, "ok")

    return CandidateSignals(
        entity_id=entity_id,
        past_fit=sv(past_fit),
        local_heat=sv(local_heat),
        momentum=sv(momentum),
        months_since_booked=months,
    )


def order(ranked: list[RankedCandidate]) -> list[str]:
    return [r.candidate.entity_id for r in ranked]


# --- hand-built set -------------------------------------------------------------------

# Never booked, in the requested band, no Qloo signals: total = 0.35 * affinity + 0.425.
# A: 0.6 affinity but strong Qloo signals      -> 0.21 + 0.18 + 0.18 + 0.1 + 0.1 + 0.045 = 0.815
# B: 0.9 affinity, nothing else measured       -> 0.315 + 0.425                         = 0.740
# D: 0.5 affinity, nothing else measured       -> 0.175 + 0.425                         = 0.600
# C: 0.7 affinity, played here 6 months ago    -> 0.245 + 0.425 - 0.1 * 0.75            = 0.595
HAND = [
    candidate("ID-B", "Bravo", 0.9),
    candidate("ID-C", "Charlie", 0.7),
    candidate("ID-A", "Alpha", 0.6),
    candidate("ID-D", "Delta", 0.5),
]
HAND_SIGNALS = {
    "ID-A": signals("ID-A", past_fit=0.9, local_heat=0.9, momentum=0.9),
    "ID-C": signals("ID-C", months=6),
}


def test_expected_order_on_hand_built_set() -> None:
    ranked = rank(HAND, PROFILE, CONSTRAINTS, HAND_SIGNALS)
    assert order(ranked) == ["ID-A", "ID-B", "ID-D", "ID-C"]
    assert [round(r.score.total, 3) for r in ranked] == [0.815, 0.740, 0.600, 0.595]


def test_every_input_order_gives_the_same_ranking() -> None:
    expected = rank(HAND, PROFILE, CONSTRAINTS, HAND_SIGNALS)
    for perm in itertools.permutations(HAND):
        assert rank(list(perm), PROFILE, CONSTRAINTS, HAND_SIGNALS) == expected


def test_no_signals_ranks_by_affinity() -> None:
    assert order(rank(HAND, PROFILE, CONSTRAINTS, None)) == ["ID-B", "ID-C", "ID-A", "ID-D"]


def test_empty_input() -> None:
    assert rank([], PROFILE, CONSTRAINTS, None) == []


# --- tie-break rule -------------------------------------------------------------------


def _scored(entity_id: str, name: str, total: float, affinity: float) -> RankedCandidate:
    """A `RankedCandidate` with a hand-set total, to pin the tie-break independently of math."""
    score = Score(
        total=total,
        breakdown=ScoreBreakdown(
            crowd_affinity=affinity, past_fit=0.5, local_heat=0.5, novelty=1.0, size_fit=1.0,
            momentum=0.5,
        ),
        weights=ScoreWeights.model_validate(dict(WEIGHTS)),
        top_reason="",
        neutral=[],
        evidence=[],
    )  # fmt: skip
    return RankedCandidate(candidate(entity_id, name, affinity), score)


def test_equal_totals_break_on_crowd_affinity_then_name() -> None:
    items = [
        _scored("ID-1", "Zulu", 0.7, 0.6),
        _scored("ID-2", "Yankee", 0.7, 0.8),  # same total, higher affinity: first
        _scored("ID-3", "Alpha", 0.7, 0.6),  # same total and affinity as Zulu: name wins
        _scored("ID-4", "Whiskey", 0.9, 0.1),  # higher total beats any affinity
    ]
    assert [i.candidate.name for i in sorted(items, key=sort_key)] == [
        "Whiskey",
        "Yankee",
        "Alpha",
        "Zulu",
    ]


def test_full_tie_breaks_on_name_then_entity_id() -> None:
    # Identical inputs but the name: totals and affinity tie exactly through `rank`.
    tied = [
        candidate("ID-3", "beta", 0.7),
        candidate("ID-2", "Alpha", 0.7),
        candidate("ID-9", "alpha", 0.7),
        candidate("ID-1", "alpha", 0.7),
    ]
    ranked = rank(tied, PROFILE, CONSTRAINTS, None)
    assert len({r.score.total for r in ranked}) == 1
    assert [(r.candidate.name, r.candidate.entity_id) for r in ranked] == [
        ("Alpha", "ID-2"),
        ("alpha", "ID-1"),
        ("alpha", "ID-9"),
        ("beta", "ID-3"),
    ]


# --- properties -----------------------------------------------------------------------

unit = st.floats(min_value=0.0, max_value=1.0, allow_nan=False)
maybe_unit = st.none() | unit


@st.composite
def candidate_sets(draw: st.DrawFn) -> tuple[list[Candidate], dict[str, CandidateSignals]]:
    n = draw(st.integers(min_value=0, max_value=12))
    names = st.sampled_from(["Alpha", "alpha", "Bravo", "Charlie", "Delta", "Echo"])
    cands: list[Candidate] = []
    sigs: dict[str, CandidateSignals] = {}
    for i in range(n):
        entity_id = f"ID-{i:02d}"
        # A small pool of affinities makes ties common, so the tie-break gets exercised.
        affinity = draw(st.sampled_from([0.2, 0.5, 0.8]) | unit)
        cands.append(candidate(entity_id, draw(names), affinity, draw(unit)))
        if draw(st.booleans()):
            sigs[entity_id] = signals(
                entity_id,
                past_fit=draw(maybe_unit),
                local_heat=draw(maybe_unit),
                momentum=draw(maybe_unit),
                months=draw(st.none() | st.integers(min_value=0, max_value=60)),
            )
    return cands, sigs


@settings(max_examples=200, deadline=None)
@given(
    data=candidate_sets(),
    tier=st.sampled_from(["emerging", "mid", "established"]),
    seed=st.randoms(use_true_random=False),
)
def test_rank_is_idempotent_and_total_is_weighted_sum(
    data: tuple[list[Candidate], dict[str, CandidateSignals]], tier: str, seed: Any
) -> None:
    cands, sigs = data
    constraints = CONSTRAINTS.model_copy(update={"size_tier": tier})
    ranked = rank(cands, PROFILE, constraints, sigs)

    # Idempotent: re-ranking the ranked list, or any shuffle of it, changes nothing.
    again = rank([r.candidate for r in ranked], PROFILE, constraints, sigs)
    assert again == ranked
    shuffled = list(cands)
    seed.shuffle(shuffled)
    assert rank(shuffled, PROFILE, constraints, sigs) == ranked

    assert len(ranked) == len(cands)
    for r in ranked:
        b = r.score.breakdown.model_dump()
        assert abs(r.score.total - sum(w * b[k] for k, w in WEIGHTS.items())) <= 1e-9
    for a, b2 in itertools.pairwise(ranked):
        assert sort_key(a) <= sort_key(b2)


# --- rank_given: the user's own shortlist ---------------------------------------------

RANK_FIXTURE: dict[str, Any] = json.loads(
    (FIXTURES_DIR / "rank" / "five_artists_vs_signals.json").read_text("utf-8")
)
RANK_REQUEST: dict[str, Any] = json.loads(
    (FIXTURES_DIR / "rank" / "five_artists_vs_signals.request.json").read_text("utf-8")
)["input"]
OPTIONS: list[str] = RANK_REQUEST["options"]
SIGNALS: list[str] = RANK_REQUEST["signals"]


@pytest.fixture
def routed(monkeypatch: pytest.MonkeyPatch) -> Callable[[dict[str, Any]], RouterHarness]:
    def make(envelope: dict[str, Any]) -> RouterHarness:
        fake = RouterHarness(lambda workflow, payload: envelope)
        monkeypatch.setattr(exec_client, "create_subprocess_exec", fake)
        return fake

    return make


def client() -> QlooExec:
    return QlooExec(QlooConfig(qloo_bin="/fake/qloo"))


async def test_rank_given_one_call_keeps_qloo_order(
    routed: Callable[[dict[str, Any]], RouterHarness],
) -> None:
    fake = routed(RANK_FIXTURE)
    shortlist = list(reversed(OPTIONS))  # the user's order must not leak into the result
    given_ = await rank_given(shortlist, profile(*SIGNALS), qloo=client(), constraints=CONSTRAINTS)

    assert fake.workflows() == ["rank"]
    sent = fake.inputs[0]
    assert sent["option_type"] == "artist"
    assert sorted(sent["options"]) == sorted(OPTIONS)
    assert sent["signals"] == SIGNALS

    assert given_.status == "ok" and given_.missing == [] and given_.notes == []
    assert order(given_.ranked) == [r["entity_id"] for r in RANK_FIXTURE["results"]]
    assert [r.qloo_rank for r in given_.ranked] == [1, 2, 3, 4, 5]
    for r, row in zip(given_.ranked, RANK_FIXTURE["results"], strict=True):
        s = r.score
        assert s.breakdown.crowd_affinity == pytest.approx(row["affinity"])
        assert set(s.neutral) == {"past_fit", "local_heat", "momentum"}
        assert [(e.item_ref, e.call_id) for e in s.evidence] == [("crowd_affinity", given_.call_id)]
        assert "Qloo's rank" in s.top_reason and f"#{r.qloo_rank} of 5" in s.top_reason
        b = s.breakdown.model_dump()
        assert abs(s.total - sum(w * b[k] for k, w in WEIGHTS.items())) <= 1e-9


async def test_rank_given_partial_is_labelled_and_missing_listed(
    routed: Callable[[dict[str, Any]], RouterHarness],
) -> None:
    envelope = copy.deepcopy(RANK_FIXTURE)
    envelope["status"] = "partial"
    envelope["summary"] = "Synthetic: one option could not be scored."
    envelope["results"] = envelope["results"][:4]
    envelope["result_count"] = 4
    routed(envelope)

    given_ = await rank_given(OPTIONS, profile(*SIGNALS), qloo=client(), constraints=CONSTRAINTS)
    assert given_.status == "partial"
    assert len(given_.ranked) == 4
    assert given_.missing == [OPTIONS[4]]
    assert any("one option could not be scored" in n for n in given_.notes)


async def test_rank_given_uses_booking_history_and_keeps_qloo_order_over_total(
    routed: Callable[[dict[str, Any]], RouterHarness],
) -> None:
    routed(RANK_FIXTURE)
    # Qloo's #1 played the room this month: novelty 0, so its total drops below #2's.
    given_ = await rank_given(
        OPTIONS,
        profile(*SIGNALS),
        qloo=client(),
        constraints=CONSTRAINTS,
        months_since_booked={OPTIONS[0]: 0, OPTIONS[2]: 12},
    )
    first, second, third = given_.ranked[:3]
    assert first.score.breakdown.novelty == 0.0
    assert second.score.breakdown.novelty == 1.0
    assert third.score.breakdown.novelty == pytest.approx(0.5)
    assert first.score.total < second.score.total
    assert [r.qloo_rank for r in given_.ranked] == [1, 2, 3, 4, 5]
    assert order(given_.ranked) == [r["entity_id"] for r in RANK_FIXTURE["results"]]
    # Novelty is Outdraw's data: not a Qloo signal, so neither neutral nor in evidence.
    assert set(first.score.neutral) == {"past_fit", "local_heat", "momentum"}
    assert "Qloo's rank places" in first.score.top_reason


async def test_rank_given_sends_heaviest_signals_capped(
    routed: Callable[[dict[str, Any]], RouterHarness],
) -> None:
    fake = routed(RANK_FIXTURE)
    weighted = profile().model_copy(
        update={
            "signals": [
                Signal(entity_id=f"SIG-{i}", weight=w)
                for i, w in enumerate([0.2, 0.9, 0.5, 0.9, 0.1, 0.7, 0.3])
            ]
            + [Signal(entity_id="SIG-1", weight=0.9)]  # duplicate ID, must be sent once
        }
    )
    await rank_given(OPTIONS, weighted, qloo=client(), constraints=CONSTRAINTS)
    assert fake.inputs[0]["signals"] == ["SIG-1", "SIG-3", "SIG-5", "SIG-2", "SIG-6"]
    assert len(fake.inputs[0]["signals"]) == RANK_SIGNALS


@pytest.mark.parametrize(
    ("envelope", "status"),
    [
        (
            {"status": "empty", "summary": "Synthetic: nothing.", "results": [],
             "result_count": 0},
            "empty",
        ),
        (
            {"status": "error", "results": [], "result_count": 0,
             "error": {"code": "SYNTHETIC_BAD", "retryable": False, "recovery": "Fix it."}},
            "error",
        ),
    ],
)  # fmt: skip
async def test_rank_given_empty_and_error_fold_into_notes(
    routed: Callable[[dict[str, Any]], RouterHarness], envelope: dict[str, Any], status: str
) -> None:
    fake = routed(envelope)
    given_ = await rank_given(OPTIONS, profile(*SIGNALS), qloo=client(), constraints=CONSTRAINTS)
    assert fake.spawns == 1
    assert given_.status == status and given_.ranked == [] and given_.missing == OPTIONS
    assert given_.notes


async def test_rank_given_without_shortlist_or_signals_makes_no_call(
    routed: Callable[[dict[str, Any]], RouterHarness],
) -> None:
    fake = routed(RANK_FIXTURE)
    assert (
        await rank_given([], profile(*SIGNALS), qloo=client(), constraints=CONSTRAINTS)
    ).ranked == []
    assert (
        await rank_given(OPTIONS, profile(), qloo=client(), constraints=CONSTRAINTS)
    ).ranked == []
    assert fake.spawns == 0
