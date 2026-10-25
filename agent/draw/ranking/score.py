"""The deterministic score. The LLM never ranks; this function and `rank.py` do.

`score_candidate(candidate, profile, constraints, signals)` is a pure function: same
inputs, same `Score`, no I/O, no randomness. Six sub-scores in [0, 1], weighted by
`WEIGHTS` (exported for the UI, sums to 1.0):

- **crowd_affinity** (0.35) — the candidate's `affinity` from the recommend envelope.
- **past_fit** (0.20) — `compare_audiences` tag overlap with the best-selling past acts.
- **local_heat** (0.20) — mean `where_popular` affinity_rank for the venue region.
- **novelty** (0.10) — 1.0 if never booked here, else months since the last show over
  `NOVELTY_HORIZON_MONTHS` (24): booked 6 months ago scores 0.25, a year ago 0.5.
- **size_fit** (0.10) — how close the act's popularity band sits to `size_tier`: 1.0 in
  the band, 0.5 one band away, 0.0 two away (`POPULARITY_BANDS` from `candidates.py`).
- **momentum** (0.05) — `trends` direction over the last six months, 0.5 = flat.

A Qloo-backed sub-score (`past_fit`, `local_heat`, `momentum`) with no value falls back to
the run's median for that sub-score (`fallbacks_of`, computed by `rank` over every
candidate measured in the run; `NEUTRAL` 0.5 when none was) and is listed in
`Score.neutral`, so the UI can show "no Qloo signal" instead of a number that looks
measured. The median keeps an unmeasured act level with the measured ones: a fixed 0.5
rewarded the acts past the signals budget whenever the measured values sat below 0.5
(past_fit usually does), and penalised them whenever they sat above. `Score.evidence` holds one
`EvidenceRef(item_ref=<sub-score>, call_id=...)` per sub-score that came from a Qloo call,
so the evidence trail can point back at the exact call.

`top_reason` is a templated sentence (no LLM) for the highest non-neutral sub-score, naming
the Qloo workflow behind it, or the venue's own data for `novelty` and `size_fit`. `rank`
then replaces it with `distinctive_reason`: the sub-score where the act leads the list's
median by the most weight, so acts that share a value do not all share a sentence.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from statistics import median
from typing import Final, Literal

from draw.ranking.candidates import POPULARITY_BANDS
from draw.ranking.signals import CandidateSignals, SignalValue
from draw.schemas import (
    Candidate,
    Constraints,
    CrowdProfile,
    EvidenceRef,
    Score,
    ScoreBreakdown,
    ScoreWeights,
    SizeTier,
)

__all__ = [
    "NEUTRAL",
    "NOVELTY_HORIZON_MONTHS",
    "QLOO_SIGNALS",
    "WEIGHTS",
    "SourceLabel",
    "SubScore",
    "baseline_of",
    "distinctive_reason",
    "fallbacks_of",
    "novelty_of",
    "score_candidate",
    "size_fit_of",
    "source_labels",
    "top_reason_for",
]

SubScore = Literal["crowd_affinity", "past_fit", "local_heat", "novelty", "size_fit", "momentum"]
SourceLabel = Literal["qloo", "draw"]

WEIGHTS: Final[Mapping[SubScore, float]] = {
    "crowd_affinity": 0.35,
    "past_fit": 0.20,
    "local_heat": 0.20,
    "novelty": 0.10,
    "size_fit": 0.10,
    "momentum": 0.05,
}
"""Weight per sub-score, in display order. Exported for the UI; sums to 1.0."""
assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9

NEUTRAL = 0.5
NOVELTY_HORIZON_MONTHS = 24
"""Months after the last show at this venue for novelty to climb back to 1.0."""

WORKFLOW_OF: Final[Mapping[SubScore, str | None]] = {
    "crowd_affinity": "recommend",
    "past_fit": "compare_audiences",
    "local_heat": "where_popular",
    "novelty": None,
    "size_fit": None,
    "momentum": "trends",
}
"""Which Qloo workflow backs each sub-score; `None` means Outdraw's own data."""

QLOO_SIGNALS: Final[tuple[SubScore, ...]] = ("past_fit", "local_heat", "momentum")
"""The sub-scores read by `gather_signals`, which fall back when Qloo gave no value."""

_TIER_ORDER: Final[tuple[SizeTier, ...]] = ("emerging", "mid", "established")


def source_labels(neutral: Iterable[str]) -> dict[SubScore, SourceLabel]:
    """Per sub-score, whether its number is Qloo data or Outdraw's own interpretation
    (a neutral fallback counts as Outdraw's, however Qloo-backed the sub-score is)."""
    fell_back = set(neutral)
    return {
        name: "qloo" if WORKFLOW_OF[name] and name not in fell_back else "draw" for name in WEIGHTS
    }


# --- sub-scores -----------------------------------------------------------------------


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))


def novelty_of(months_since_booked: int | None) -> float:
    """1.0 if never booked here, climbing from 0 to 1 over `NOVELTY_HORIZON_MONTHS`."""
    if months_since_booked is None:
        return 1.0
    return _clamp(max(0, months_since_booked) / NOVELTY_HORIZON_MONTHS)


def _band_index(popularity: float) -> int:
    for i, tier in enumerate(_TIER_ORDER):
        low, high = POPULARITY_BANDS[tier]
        if low <= popularity < high or (high >= 1.0 and popularity >= high):
            return i
    return len(_TIER_ORDER) - 1  # popularity > 1 after clamping cannot happen; be safe


def size_fit_of(popularity: float, size_tier: SizeTier) -> float:
    """1.0 inside the requested band, losing 0.5 per band of distance."""
    distance = abs(_band_index(_clamp(popularity)) - _TIER_ORDER.index(size_tier))
    return _clamp(1.0 - distance / (len(_TIER_ORDER) - 1))


def _or_fallback(signal: SignalValue | None, fallback: float) -> tuple[float, bool]:
    """The signal's value, or (fallback, True) when Qloo gave none."""
    if signal is None or signal.value is None:
        return fallback, True
    return _clamp(signal.value), False


def fallbacks_of(signals: Iterable[CandidateSignals | None]) -> dict[SubScore, float]:
    """Per Qloo-backed sub-score, the median of the values measured across `signals`, or
    `NEUTRAL` when none was. Order-independent, so ranking stays a pure function."""
    rows = [s for s in signals if s is not None]
    out: dict[SubScore, float] = {}
    for name in QLOO_SIGNALS:
        measured = [_clamp(v) for s in rows if (v := getattr(s, name).value) is not None]
        out[name] = median(measured) if measured else NEUTRAL
    return out


# --- reason ---------------------------------------------------------------------------


def top_reason_for(
    candidate: Candidate, constraints: Constraints, best: SubScore, value: float
) -> str:
    """A templated sentence for the winning sub-score, naming the workflow behind it."""
    name = candidate.name
    pct = f"{round(value * 100)}%"
    if best == "crowd_affinity":
        return f"Qloo's recommend puts {name} at {pct} affinity with this crowd."
    if best == "past_fit":
        return (
            f"Qloo's compare_audiences shows a {pct} tag overlap between {name} "
            "and the room's best-selling past acts."
        )
    if best == "local_heat":
        return (
            f"Qloo's where_popular heatmap gives {name} {pct} affinity across the venue's region."
        )
    if best == "momentum":
        return f"Qloo's trends shows {name} climbing over the last six months ({pct} momentum)."
    if best == "size_fit":
        return (
            f"{name}'s Qloo popularity sits in the {constraints.size_tier} band "
            "this room asked for."
        )
    return f"{name} has never played this room, so the crowd gets something new."


def baseline_of(scores: Iterable[Score]) -> dict[SubScore, float]:
    """Per sub-score, the median across a ranked list: what a typical act there scores."""
    rows = [s.breakdown.model_dump() for s in scores]
    return {name: median(r[name] for r in rows) if rows else NEUTRAL for name in WEIGHTS}


def distinctive_reason(
    candidate: Candidate,
    constraints: Constraints,
    score: Score,
    baseline: Mapping[SubScore, float],
) -> str:
    """The reason that sets this act apart from the rest of its list.

    Picks the measured sub-score with the largest weighted lead over `baseline`, so a value
    every act shares (novelty 1.0 for a room's first booking of each act) never repeats as
    every act's reason. With no lead anywhere, the act's crowd affinity is the reason.
    """
    values = score.breakdown.model_dump()
    neutral = set(score.neutral or [])
    leads = [
        (WEIGHTS[name] * (values[name] - baseline[name]), WEIGHTS[name], name)
        for name in WEIGHTS
        if name not in neutral
    ]
    lead, _, best = max(leads)
    if lead <= 0:
        best = "crowd_affinity"
    return top_reason_for(candidate, constraints, best, values[best])


# --- public API -----------------------------------------------------------------------


def score_candidate(
    candidate: Candidate,
    profile: CrowdProfile,
    constraints: Constraints,
    signals: CandidateSignals | None,
    fallback: Mapping[SubScore, float] | None = None,
) -> Score:
    """Score one candidate. Pure: no I/O, and `profile` only confirms the signals' venue.

    `signals` is `gather_signals(...).get(candidate.entity_id)`; `None` (a candidate past
    the top N, or no gathering at all) falls back on every Qloo-backed sub-score.
    `fallback` is `fallbacks_of(...)` over the run; missing entries use `NEUTRAL`.
    """
    del profile  # the signals were gathered for this profile; nothing else is read from it
    fb = fallback or {}
    past_fit, past_neutral = _or_fallback(
        signals.past_fit if signals else None, fb.get("past_fit", NEUTRAL)
    )
    local_heat, heat_neutral = _or_fallback(
        signals.local_heat if signals else None, fb.get("local_heat", NEUTRAL)
    )
    momentum, momentum_neutral = _or_fallback(
        signals.momentum if signals else None, fb.get("momentum", NEUTRAL)
    )
    months = signals.months_since_booked if signals else None

    values: dict[SubScore, float] = {
        "crowd_affinity": _clamp(candidate.affinity),
        "past_fit": past_fit,
        "local_heat": local_heat,
        "novelty": novelty_of(months),
        "size_fit": size_fit_of(candidate.popularity, constraints.size_tier),
        "momentum": momentum,
    }
    flags: dict[SubScore, bool] = {
        "past_fit": past_neutral,
        "local_heat": heat_neutral,
        "momentum": momentum_neutral,
    }
    neutral: list[SubScore] = [name for name, flag in flags.items() if flag]
    total = sum(WEIGHTS[name] * values[name] for name in WEIGHTS)

    evidence = [
        EvidenceRef(item_ref="crowd_affinity", call_id=call_id)
        for call_id in dict.fromkeys(e.call_id for e in candidate.evidence)
    ]
    if signals is not None:
        for name, signal in (
            ("past_fit", signals.past_fit),
            ("local_heat", signals.local_heat),
            ("momentum", signals.momentum),
        ):
            if signal.value is not None and signal.call_id:
                evidence.append(EvidenceRef(item_ref=name, call_id=signal.call_id))

    # Highest sub-score wins, never a neutral fallback; ties go to the heavier weight,
    # then to display order (both already encoded in WEIGHTS' order).
    best = max(
        (name for name in WEIGHTS if name not in neutral),
        key=lambda name: (values[name], WEIGHTS[name]),
    )
    return Score(
        total=total,
        breakdown=ScoreBreakdown.model_validate(dict(values)),
        weights=ScoreWeights.model_validate(dict(WEIGHTS)),
        top_reason=top_reason_for(candidate, constraints, best, values[best]),
        neutral=neutral,
        evidence=evidence,
    )
