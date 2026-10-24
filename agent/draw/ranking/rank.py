"""The deterministic order. The LLM never ranks; `rank` and `score_candidate` do.

`rank(candidates, profile, constraints, signals)` is a pure function: it scores every
candidate with `score_candidate` and sorts by `Score.total` descending, ties by
`crowd_affinity` descending, then by name (case-folded, then exact), then by `entity_id`
so two acts with the same name still land in a fixed order. Same input, same order;
ranking an already ranked list returns it unchanged, whatever order it arrives in.

`rank_given(entity_ids, profile, qloo=..., constraints=..., months_since_booked=...)` is
for a shortlist the user brings: one `QlooExec.rank` call against the profile's heaviest
`RANK_SIGNALS` signals, kept in Qloo's order and merged into the same `Score` shape for the
Soundcheck panel. Qloo's `affinity` becomes `crowd_affinity`; the other Qloo-backed
sub-scores were not gathered, so they score neutral and are flagged; `novelty` comes from the
venue's own booking history when the caller passes it; `top_reason` names the `rank`
workflow behind the order.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from draw.qloo import QlooEmpty, QlooError, QlooExec
from draw.ranking.score import (
    baseline_of,
    distinctive_reason,
    fallbacks_of,
    score_candidate,
)
from draw.ranking.signals import CandidateSignals, SignalSet
from draw.schemas import Candidate, Constraints, CrowdProfile, EvidenceRef, Score

__all__ = ["RANK_SIGNALS", "GivenRanking", "RankedCandidate", "rank", "rank_given", "sort_key"]

RANK_SIGNALS = 5
"""Most profile signals sent to one `rank` call, heaviest first. Decision 001 records the
two-ID shape as working on the real key; five keeps the request small and the quota safe."""


@dataclass(frozen=True)
class RankedCandidate:
    """A candidate with its score; `qloo_rank` is the 1-based Qloo position from `rank_given`."""

    candidate: Candidate
    score: Score
    qloo_rank: int | None = None


@dataclass
class GivenRanking:
    """The user's shortlist in Qloo's order, plus what the envelope said about it."""

    ranked: list[RankedCandidate] = field(default_factory=list)
    """Rows in Qloo's order (`qloo_rank` 1..n), NOT sorted by `Score.total`. The two can
    disagree because `size_fit` and `novelty` are Outdraw's own data; the Soundcheck panel
    labels this view as Qloo's ranking rather than re-sorting it."""
    status: str = "ok"
    """Envelope status; `partial`/`degraded` keep their rows and say why in `notes`."""
    call_id: str | None = None
    missing: list[str] = field(default_factory=list)
    """Entity IDs the user gave that Qloo returned no row for."""
    notes: list[str] = field(default_factory=list)


def sort_key(item: RankedCandidate) -> tuple[float, float, str, str, str]:
    """Total desc, then crowd_affinity desc, then name, then entity_id."""
    name = item.candidate.name
    return (
        -item.score.total,
        -item.score.breakdown.crowd_affinity,
        name.casefold(),
        name,
        item.candidate.entity_id,
    )


def rank(
    candidates: Sequence[Candidate],
    profile: CrowdProfile,
    constraints: Constraints,
    signals: SignalSet | Mapping[str, CandidateSignals] | None,
) -> list[RankedCandidate]:
    """Score and sort. Pure: no I/O, no randomness, and the input order never matters.

    Unmeasured Qloo sub-scores fall back to the median of the candidates measured here, and
    each `top_reason` names what sets the act apart from this list (`distinctive_reason`).
    """
    found = [signals.get(c.entity_id) if signals else None for c in candidates]
    fallback = fallbacks_of(found)
    scores = [
        score_candidate(c, profile, constraints, s, fallback)
        for c, s in zip(candidates, found, strict=True)
    ]
    baseline = baseline_of(scores)
    scored = [
        RankedCandidate(
            c,
            score.model_copy(
                update={"top_reason": distinctive_reason(c, constraints, score, baseline)}
            ),
        )
        for c, score in zip(candidates, scores, strict=True)
    ]
    return sorted(scored, key=sort_key)


# --- user-supplied shortlist ----------------------------------------------------------


def _given_reason(name: str, position: int, count: int, affinity: float) -> str:
    return (
        f"Qloo's rank places {name} #{position} of {count} for this crowd "
        f"({round(affinity * 100)}% affinity)."
    )


def _candidate_of(row: Mapping[str, Any], call_id: str) -> Candidate | None:
    entity_id = row.get("entity_id")
    affinity = row.get("affinity")
    if not isinstance(entity_id, str) or not entity_id or not isinstance(affinity, int | float):
        return None
    popularity = row.get("popularity")
    return Candidate(
        entity_id=entity_id,
        name=str(row.get("name") or entity_id),
        popularity=min(1.0, max(0.0, float(popularity)))
        if isinstance(popularity, int | float)
        else 0.0,
        affinity=min(1.0, max(0.0, float(affinity))),
        tags=[],
        evidence=[EvidenceRef(item_ref=entity_id, call_id=call_id)],
    )


async def rank_given(
    entity_ids: Sequence[str],
    profile: CrowdProfile,
    *,
    qloo: QlooExec,
    constraints: Constraints,
    months_since_booked: Mapping[str, int] | None = None,
) -> GivenRanking:
    """Rank the user's own shortlist with one `rank` call; keep Qloo's order.

    The IDs must already be confirmed entities (resolve → confirm → ask happens upstream).
    `months_since_booked` is the venue's own history keyed by entity ID (whole months since
    the act's last show here); an act not in it counts as never booked. `QlooNeedsInput` and
    `BudgetExceeded` propagate so the agent can ask or stop; `empty` and `error` come back
    as an empty ranking with a note.
    """
    options = list(dict.fromkeys(e for e in entity_ids if e))
    heaviest = sorted(profile.signals, key=lambda s: -s.weight)
    signals = list(dict.fromkeys(s.entity_id for s in heaviest))[:RANK_SIGNALS]
    if not options:
        return GivenRanking(status="empty", notes=["No shortlist given."])
    if not signals:
        return GivenRanking(
            status="empty",
            missing=options,
            notes=["The crowd profile has no signals to rank the shortlist against."],
        )

    try:
        result = await qloo.rank(options, option_type="artist", signals=signals)
    except QlooEmpty as exc:
        summary = exc.envelope.summary or "Qloo returned no rank for the shortlist."
        return GivenRanking(
            status="empty",
            call_id=getattr(exc.envelope, "call_id", None),
            missing=options,
            notes=[f"rank: {summary}"],
        )
    except QlooError as exc:
        return GivenRanking(
            status="error",
            call_id=getattr(exc.envelope, "call_id", None),
            missing=options,
            notes=[f"rank: {exc.code}. {exc.recovery}".rstrip()],
        )

    rows = result.results if isinstance(result.results, list) else []
    wanted = set(options)
    seen: set[str] = set()
    candidates: list[Candidate] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        c = _candidate_of(row, result.call_id)
        if c is None or c.entity_id not in wanted or c.entity_id in seen:
            continue
        seen.add(c.entity_id)
        candidates.append(c)

    count = len(candidates)
    history = months_since_booked or {}
    ranked = []
    for position, c in enumerate(candidates, start=1):
        # Only the venue's own history travels in; the three Qloo signals stay unset so
        # they score neutral and are flagged.
        own = CandidateSignals(c.entity_id, months_since_booked=history.get(c.entity_id))
        score = score_candidate(c, profile, constraints, own)
        reason = _given_reason(c.name, position, count, score.breakdown.crowd_affinity)
        ranked.append(RankedCandidate(c, score.model_copy(update={"top_reason": reason}), position))

    notes = []
    if result.status != "ok":
        notes.append(f"rank: {result.summary or f'Qloo reported status {result.status}.'}")
    missing = [e for e in options if e not in seen]
    if missing:
        notes.append(f"rank: Qloo returned no row for {len(missing)} of {len(options)} acts.")
    return GivenRanking(ranked, result.status, result.call_id, missing, notes)
