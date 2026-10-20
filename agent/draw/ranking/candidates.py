"""Candidate acts: artists Qloo associates with the crowd, filtered by the booking constraints.

Pure data module: no LLM, and no ordering beyond Qloo's own (ranking is `score.py`'s job).

Pipeline (`generate_candidates`):

1. **Tags.** `include_tags`/`exclude_tags` words become tag URNs with `find_tags` (via
   `resolve_entity(..., "genre")`, music genres preferred); URNs pass through untouched.
   The gateway caches every `find_tags` envelope, so a repeated word costs no quota. A word
   that does not resolve unambiguously stops the run before any taste call (resolve →
   confirm → ask): `[]` with the candidates in `unresolved_tags` for the agent to ask about.
2. **Recommend calls**, all through the gateway: `recommend("artist")` with the profile's
   signals (already ordered by sell-through weight) and `signal_location` = the venue
   region (decision 001), plus the tag URNs as `include_tags`/`exclude_tags`, which the
   harness turns into Qloo's `filter.tags`/`filter.exclude.tags`. The harness returns at
   most 20 rows per call, so when fewer than `max_candidates` survive the filters, up to
   `MAX_RECOMMEND_CALLS - 1` more calls widen the pool with the strongest single signals
   (decision 001, fallback 1). Calls run one at a time and stop once the cap is reached.
3. **Client-side filters**, per row: popularity must fall in the `size_tier` band
   (`POPULARITY_BANDS`; the harness has no popularity filter); acts booked within
   `exclude_recent_months` are dropped. Tags are filtered by Qloo only: recommend rows
   carry no tags (decision 001), so `Candidate.tags` is the include URNs the act passed
   (`union`: at least one of them applies), or `[]` with no include constraint. Rows are
   deduplicated by `entity_id`, first call first, and the list is capped at
   `max_candidates`.
4. **Status branching**, per envelope: `ok` and `partial` fill; `degraded` fills and is
   flagged; `empty` on the first call returns `[]` with a reason to broaden the tags (on a
   widening call it only means nothing new); `needs_input` and `error` return what earlier
   calls gave, or `[]` with the recovery text. Every non-ok summary goes into `notes`.
   `BudgetExceeded` propagates: nothing was spawned.
5. **Evidence.** Each candidate keeps the `call_id` of every recommend call that returned
   it, as `EvidenceRef(item_ref=<entity_id>, call_id=...)`.

`candidates_quality` is `ok`, `partial` (some call was partial, or a widening call failed)
or `degraded` (a degraded envelope, or no candidates at all).
"""

from __future__ import annotations

import asyncio
import calendar
import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from draw.profile.resolve import normalise_name, resolve_entity
from draw.qloo import QlooEmpty, QlooError, QlooExec, QlooNeedsInput, QlooResult
from draw.schemas import (
    Candidate,
    Constraints,
    CrowdProfile,
    DataQuality,
    EvidenceRef,
    ResolvedEntity,
    SizeTier,
    VenueInput,
)

__all__ = [
    "DEFAULT_MAX_CANDIDATES",
    "MAX_RECOMMEND_CALLS",
    "POPULARITY_BANDS",
    "CandidateList",
    "generate_candidates",
    "last_booked",
    "months_before",
]

DEFAULT_MAX_CANDIDATES = 40
RECOMMEND_LIMIT = 20  # the harness's maximum `limit`
MAX_RECOMMEND_CALLS = 3
"""One call with every signal, then up to two single-signal calls to widen the pool."""
TAG_PREFIX = "urn:tag:"

# Qloo popularity is a percentile across all entities, so everything `recommend` returns
# for a crowd of touring acts sits near 1.0. The bands are the terciles of the 51 artists
# recorded by `scripts/record_candidates.py` (0.81-1.0, terciles 0.9963 and 0.9991; decision
# 001), rounded: tiers are relative to the pool Qloo offers, not absolute fame. Lower
# bound inclusive; 1.0 belongs to `established`.
POPULARITY_BANDS: dict[SizeTier, tuple[float, float]] = {
    "emerging": (0.0, 0.996),
    "mid": (0.996, 0.999),
    "established": (0.999, 1.0),
}


@dataclass(frozen=True)
class CandidateList:
    """The candidates, plus what the agent needs when there are few or none.

    A dataclass rather than a `list` subclass so that slicing the top N for scoring
    (`result.candidates[:n]`) cannot silently drop the metadata. `reason` is set whenever
    `candidates` is empty and says what to change; `unresolved_tags` holds tag words that
    need the user to pick a candidate before the run can go on.
    """

    candidates: list[Candidate] = field(default_factory=list)
    candidates_quality: DataQuality = "ok"
    reason: str | None = None
    notes: list[str] = field(default_factory=list)
    unresolved_tags: list[ResolvedEntity] = field(default_factory=list)


# --- bookings -------------------------------------------------------------------------


def last_booked(venue: VenueInput, confirmed: Sequence[ResolvedEntity]) -> dict[str, dt.date]:
    """Each confirmed act's latest show date at the venue, keyed by Qloo entity ID."""
    latest_by_name: dict[str, dt.date] = {}
    for show in venue.past_shows:
        key = normalise_name(show.act)
        if key not in latest_by_name or show.date > latest_by_name[key]:
            latest_by_name[key] = show.date
    booked: dict[str, dt.date] = {}
    for act in confirmed:
        if not act.confirmed or not act.entity_id:
            continue
        dates = [
            d
            for name in (act.query, act.canonical_name)
            if (d := latest_by_name.get(normalise_name(name))) is not None
        ]
        if dates:
            booked[act.entity_id] = max([*dates, booked.get(act.entity_id, dates[0])])
    return booked


def months_before(day: dt.date, months: int) -> dt.date:
    """`day` moved back `months` calendar months, clamped to the end of a shorter month."""
    index = day.year * 12 + day.month - 1 - months
    year, month = divmod(index, 12)
    month += 1
    return dt.date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


# --- tags -----------------------------------------------------------------------------


@dataclass
class _Tags:
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    unresolved: list[ResolvedEntity] = field(default_factory=list)


async def _resolve_tags(constraints: Constraints, qloo: QlooExec) -> _Tags:
    """Tag words to URNs; URNs pass through. Words resolve concurrently (and from cache)."""

    async def one(word: str) -> str | ResolvedEntity:
        if word.strip().startswith(TAG_PREFIX):
            return word.strip()
        resolved = await resolve_entity(word, "genre", qloo=qloo)
        return resolved.entity_id if resolved.confirmed and resolved.entity_id else resolved

    words = [*constraints.include_tags, *constraints.exclude_tags]
    resolved = await asyncio.gather(*(one(w) for w in words))
    tags = _Tags()
    for i, value in enumerate(resolved):
        if isinstance(value, ResolvedEntity):
            tags.unresolved.append(value)
            continue
        target = tags.include if i < len(constraints.include_tags) else tags.exclude
        if value not in target:
            target.append(value)
    return tags


# --- calls ----------------------------------------------------------------------------


@dataclass
class _Call:
    status: str
    call_id: str | None = None
    rows: list[dict[str, Any]] = field(default_factory=list)
    note: str | None = None


async def _recommend(
    qloo: QlooExec, signals: Sequence[str], region: str, tags: _Tags, label: str
) -> _Call:
    """One `recommend("artist")` folded into a `_Call`; never raises on data."""
    try:
        result: QlooResult = await qloo.recommend(
            "artist",
            signals=signals,
            signal_location=region or None,
            include_tags=tags.include or None,
            include_tags_operator="union" if tags.include else None,
            exclude_tags=tags.exclude or None,
            exclude_tags_operator="union" if tags.exclude else None,
            limit=RECOMMEND_LIMIT,
        )
    except QlooEmpty as exc:
        summary = exc.envelope.summary or "Qloo returned no artists."
        return _Call("empty", getattr(exc.envelope, "call_id", None), note=f"{label}: {summary}")
    except QlooNeedsInput as exc:
        summary = exc.envelope.summary or "Qloo could not resolve an input."
        return _Call(
            "needs_input", getattr(exc.envelope, "call_id", None), note=f"{label}: {summary}"
        )
    except QlooError as exc:
        return _Call(
            "error",
            getattr(exc.envelope, "call_id", None),
            note=f"{label}: {exc.code}. {exc.recovery}".rstrip(),
        )
    rows = (
        [r for r in result.results if isinstance(r, dict)]
        if isinstance(result.results, list)
        else []
    )
    note = None
    if result.status != "ok":
        note = f"{label}: {result.summary or f'Qloo reported status {result.status}.'}"
    return _Call(result.status, result.call_id, rows, note)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return min(1.0, max(0.0, float(value)))


def _in_band(popularity: float, tier: SizeTier) -> bool:
    low, high = POPULARITY_BANDS[tier]
    return low <= popularity < high or (high >= 1.0 and popularity >= high)


@dataclass
class _Dropped:
    band: int = 0
    recent: int = 0
    unusable: int = 0

    def total(self) -> int:
        return self.band + self.recent + self.unusable


# --- public API -----------------------------------------------------------------------


async def generate_candidates(
    profile: CrowdProfile,
    constraints: Constraints,
    *,
    qloo: QlooExec,
    region: str,
    booked: Mapping[str, dt.date] | None = None,
    today: dt.date | None = None,
    max_calls: int = MAX_RECOMMEND_CALLS,
) -> CandidateList:
    """Up to `constraints.max_candidates` deduplicated candidates honouring every constraint.

    `region` is the venue's location query (`venue_region(venue)`); `booked` maps entity
    IDs to their latest show at the venue (`last_booked(venue, confirmed)`); `today`
    anchors `exclude_recent_months` (defaults to the current date).
    """
    signals = [s.entity_id for s in profile.signals]
    if not signals:
        return CandidateList(
            candidates_quality="degraded",
            reason="The crowd profile has no signals; confirm at least one past act first.",
        )

    try:
        tags = await _resolve_tags(constraints, qloo)
    except QlooError as exc:
        return CandidateList(
            candidates_quality="degraded",
            reason=f"Could not resolve the tag constraints: {exc.code}. {exc.recovery}".rstrip(),
        )
    if tags.unresolved:
        words = ", ".join(repr(t.query) for t in tags.unresolved)
        return CandidateList(
            candidates_quality="degraded",
            reason=f"Confirm which Qloo tag is meant for {words} before searching.",
            unresolved_tags=tags.unresolved,
        )

    cap = constraints.max_candidates
    months = constraints.exclude_recent_months
    cutoff = months_before(today or dt.date.today(), months) if months > 0 else None
    booked = booked or {}

    signal_sets: list[list[str]] = [signals]
    if len(signals) > 1:
        signal_sets += [[s] for s in signals[: max(0, max_calls - 1)]]
    signal_sets = signal_sets[: max(1, max_calls)]

    kept: dict[str, Candidate] = {}
    dropped = _Dropped()
    calls: list[_Call] = []
    for i, signal_set in enumerate(signal_sets):
        if len(kept) >= cap:
            break
        label = "recommend[artists]" if i == 0 else f"recommend[artists from {signal_set[0]}]"
        call = await _recommend(qloo, signal_set, region, tags, label)
        calls.append(call)
        for row in call.rows:
            entity_id = row.get("entity_id") or row.get("id")
            if not isinstance(entity_id, str) or not entity_id:
                dropped.unusable += 1
                continue
            if entity_id in kept:
                if call.call_id:
                    kept[entity_id].evidence.append(
                        EvidenceRef(item_ref=entity_id, call_id=call.call_id)
                    )
                continue
            popularity, affinity = _number(row.get("popularity")), _number(row.get("affinity"))
            name = row.get("name")
            if popularity is None or affinity is None or not call.call_id:
                dropped.unusable += 1
                continue
            if not _in_band(popularity, constraints.size_tier):
                dropped.band += 1
                continue
            last = booked.get(entity_id)
            if cutoff is not None and last is not None and last >= cutoff:
                dropped.recent += 1
                continue
            if len(kept) >= cap:
                continue
            kept[entity_id] = Candidate(
                entity_id=entity_id,
                name=name if isinstance(name, str) else entity_id,
                popularity=popularity,
                affinity=affinity,
                tags=list(tags.include),
                evidence=[EvidenceRef(item_ref=entity_id, call_id=call.call_id)],
            )
        if i == 0 and call.status in {"empty", "needs_input", "error"}:
            break  # the widest call failed; narrower ones would not do better

    notes = [c.note for c in calls if c.note]
    if not kept:
        return CandidateList(
            candidates_quality="degraded",
            reason=_empty_reason(calls[0], dropped, constraints, tags),
            notes=notes,
        )
    return CandidateList(list(kept.values()), candidates_quality=_quality(calls), notes=notes)


def _quality(calls: Sequence[_Call]) -> DataQuality:
    """degraded if any envelope was; partial if any was partial or a widening call failed."""
    statuses = {c.status for c in calls}
    if "degraded" in statuses:
        return "degraded"
    if statuses & {"partial", "needs_input", "error"}:
        return "partial"
    return "ok"


def _empty_reason(first: _Call, dropped: _Dropped, constraints: Constraints, tags: _Tags) -> str:
    """What the agent should change when nothing survives."""
    if first.status == "empty":
        if tags.include or tags.exclude:
            return (
                "Qloo found no artists for this crowd with these tags; "
                "broaden include_tags or drop some exclude_tags."
            )
        return "Qloo found no artists for this crowd in this region; add or change past acts."
    if first.status in {"needs_input", "error"}:
        return first.note or "The candidate search failed; try again later."
    parts = []
    if dropped.band:
        parts.append(f"{dropped.band} outside the {constraints.size_tier} popularity band")
    if dropped.recent:
        parts.append(
            f"{dropped.recent} booked in the last {constraints.exclude_recent_months} months"
        )
    if dropped.unusable:
        parts.append(f"{dropped.unusable} without popularity or affinity")
    detail = "; ".join(parts) or "none usable"
    return (
        f"All {dropped.total()} artists Qloo returned were filtered out ({detail}); "
        "try another size_tier, a shorter exclude_recent_months or broader tags."
    )
