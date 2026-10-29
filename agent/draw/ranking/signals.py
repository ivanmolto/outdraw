"""Per-candidate Qloo signals for the score, gathered with the fewest calls.

Pure data module: no LLM, no personal data, no ordering. For the top N candidates (N is
`DEFAULT_TOP_N` = 15, bounded by `constraints.max_candidates` and by the call budget)
three signals are read, every one through the gateway (`QlooExec`), so each value is
cached, budgeted, recorded and carries the `call_id` of the call that produced it:

- **past_fit** — `compare_audiences(candidate, top 2 best-selling past acts)`: the Jaccard
  overlap of the two groups' tag sets (`results.tags` shared / `tags ∪ a ∪ b`). One call
  per candidate.
- **local_heat** — `where_popular(candidate, venue region)`: the mean heatmap
  `affinity_rank` over the points Qloo returns for the region. Not `affinity`: Qloo
  normalises that to 1.0 at the top point and 0.0 at the bottom of each call (decision
  001), so its mean comes out the same for every act. One call per candidate.
- **momentum** — `trends(batch of up to 5 candidates, last 6 months)`: the change in
  `population_percentile` from the oldest to the newest point of the window, mapped from
  [-1, 1] to [0, 1] (0.5 = flat). One call per batch of `TRENDS_BATCH` candidates. The
  harness still makes one HTTP request per entity inside the batch (decision 001), so
  quota-wise this is one request per candidate; the gateway counts one call per batch.

Cold, 15 candidates cost 15 + 15 + 3 = 33 gateway calls (`DEFAULT_MAX_CALLS`, the per-run
signals budget from CLAUDE.md). When fewer calls are allowed, N shrinks so the budget
holds; the candidates past N get no signals, and `rank` gives them the median of the
measured candidates on all three (`fallbacks_of` in `score.py`).

**Missing or non-ok results yield `None`, never a guess.** Only an `ok` envelope produces a
value. `partial` and `degraded` keep their `call_id` and summary in `notes` but give `None`
(the score falls back to neutral and flags it); `empty`, `needs_input` and `error` do the
same. `BudgetExceeded` propagates: nothing was spawned.

`months_since_booked` is not a Qloo signal but the venue's own booking history (from
`last_booked`); it travels with the signals so `score_candidate` stays a pure function of
its four arguments.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from draw.qloo import QlooEmpty, QlooError, QlooExec, QlooNeedsInput, QlooResult
from draw.ranking.candidates import months_before
from draw.schemas import Candidate, Constraints, CrowdProfile

__all__ = [
    "DEFAULT_MAX_CALLS",
    "DEFAULT_TOP_N",
    "PAST_ACTS",
    "TRENDS_BATCH",
    "TRENDS_WINDOW_MONTHS",
    "CandidateSignals",
    "SignalSet",
    "SignalValue",
    "calls_for",
    "gather_signals",
    "top_n_within",
]

DEFAULT_TOP_N = 15
DEFAULT_MAX_CALLS = 33
"""The per-run signals budget from CLAUDE.md: 15 + 15 + ceil(15 / 5) calls cold."""
PAST_ACTS = 2
"""How many best-selling past acts the candidate is compared with."""
TRENDS_BATCH = 5
TRENDS_WINDOW_MONTHS = 6
HEATMAP_LIMIT = 10
TAG_LIMIT = 10


# --- types ----------------------------------------------------------------------------


@dataclass(frozen=True)
class SignalValue:
    """One sub-score input: the value Qloo gave (or `None`) and the call behind it."""

    value: float | None = None
    call_id: str | None = None
    status: str | None = None
    """Envelope status, or `None` when no call was made (budget, missing input)."""


@dataclass(frozen=True)
class CandidateSignals:
    entity_id: str
    past_fit: SignalValue = SignalValue()
    local_heat: SignalValue = SignalValue()
    momentum: SignalValue = SignalValue()
    months_since_booked: int | None = None
    """Whole months since the act's last show at this venue; `None` = never booked here."""
    genres: tuple[str, ...] = ()
    """The act's genre tag names, read from the past_fit call (no call of its own); not
    scored, only shown and filtered on."""


@dataclass
class SignalSet:
    """Signals keyed by candidate entity ID, plus the notes and call count of the gathering."""

    by_id: dict[str, CandidateSignals] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    calls: int = 0
    """Gateway calls issued (cache hits included)."""

    def get(self, entity_id: str) -> CandidateSignals | None:
        return self.by_id.get(entity_id)


# --- budget ---------------------------------------------------------------------------


def calls_for(n: int, *, past_acts: bool = True, region: bool = True) -> int:
    """Gateway calls needed to read every signal for `n` candidates."""
    if n <= 0:
        return 0
    per_candidate = int(past_acts) + int(region)
    return per_candidate * n + -(-n // TRENDS_BATCH)


def top_n_within(
    requested: int, max_calls: int, *, past_acts: bool = True, region: bool = True
) -> int:
    """The largest N ≤ `requested` whose signals fit in `max_calls`."""
    n = max(0, requested)
    while n > 0 and calls_for(n, past_acts=past_acts, region=region) > max_calls:
        n -= 1
    return n


# --- calls ----------------------------------------------------------------------------


@dataclass
class _Outcome:
    status: str
    call_id: str | None = None
    result: QlooResult | None = None
    note: str | None = None


def _call_id(envelope: object) -> str | None:
    """The gateway's `call_id` when the envelope is a `QlooResult` (harness failures have none)."""
    call_id = getattr(envelope, "call_id", None)
    return call_id if isinstance(call_id, str) else None


async def _call(label: str, make: Callable[[], Awaitable[QlooResult]]) -> _Outcome:
    """Fold every gateway status into an `_Outcome`; never raises on data."""
    try:
        result = await make()
    except QlooEmpty as exc:
        summary = exc.envelope.summary or "Qloo returned no results."
        return _Outcome("empty", _call_id(exc.envelope), note=f"{label}: {summary}")
    except QlooNeedsInput as exc:
        summary = exc.envelope.summary or "Qloo could not resolve an input."
        return _Outcome("needs_input", _call_id(exc.envelope), note=f"{label}: {summary}")
    except QlooError as exc:
        error_note = f"{label}: {exc.code}. {exc.recovery}".rstrip()
        return _Outcome("error", _call_id(exc.envelope), note=error_note)
    note: str | None = None
    if result.status != "ok":
        note = f"{label}: {result.summary or f'Qloo reported status {result.status}.'}"
    return _Outcome(result.status, result.call_id, result, note)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return min(1.0, max(0.0, float(value)))


def _tag_ids(rows: Any) -> set[str]:
    if not isinstance(rows, list):
        return set()
    return {
        r["tag_id"]
        for r in rows
        if isinstance(r, dict) and isinstance(r.get("tag_id"), str) and r["tag_id"]
    }


def past_fit_of(result: QlooResult) -> float | None:
    """Jaccard overlap of the tag sets: shared `tags` over `tags ∪ a ∪ b`."""
    results = result.results
    if not isinstance(results, dict):
        return None
    shared = _tag_ids(results.get("tags"))
    union = shared | _tag_ids(results.get("a")) | _tag_ids(results.get("b"))
    if not union:
        return None
    return len(shared) / len(union)


GENRE_MARKER = ":genre:"
GENRE_LIMIT = 3


def genres_of(result: QlooResult) -> tuple[str, ...]:
    """The candidate's genre tag names from its past_fit call (group a is the candidate):
    the shared tags first, then its own, `*:genre:*` subtypes only (as the crowd profile
    reads them), deduplicated by tag, at most `GENRE_LIMIT`."""
    results = result.results
    if not isinstance(results, dict):
        return ()
    names: list[str] = []
    seen: set[str] = set()
    for key in ("tags", "a"):
        rows = results.get(key)
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            tag_id, name = row.get("tag_id"), row.get("name")
            if not isinstance(tag_id, str) or not isinstance(name, str) or tag_id in seen:
                continue
            if GENRE_MARKER not in f"{row.get('subtype', '')}{tag_id}":
                continue
            seen.add(tag_id)
            names.append(name)
    return tuple(names[:GENRE_LIMIT])


def local_heat_of(result: QlooResult) -> float | None:
    """Mean heatmap `query.affinity_rank` over the points returned for the region.

    Not `query.affinity`: Qloo rescales that to 1.0 at the top point and 0.0 at the bottom of
    every response, so its mean is the same for every act (decision 001, item 4).
    """
    if not isinstance(result.results, list):
        return None
    values = [
        v
        for row in result.results
        if isinstance(row, dict)
        and isinstance(row.get("query"), dict)
        and (v := _number(row["query"].get("affinity_rank"))) is not None
    ]
    if not values:
        return None
    return sum(values) / len(values)


def momentum_of(result: QlooResult) -> dict[str, float]:
    """Per entity, the percentile change over the window mapped to [0, 1]; absent = no data."""
    return {entity_id: value for entity_id, (value, _) in _series_of(result).items()}


def flat_series(result: QlooResult) -> set[str]:
    """The entities whose series never moved: every point carries the same percentile.

    On the hackathon key `trends` answers with one static percentile repeated per week (the
    velocity and delta fields are 0 too), so a flat series is the common case, and momentum
    is then 0.5 for every act. The run says so in its notes instead of presenting that
    constant as a measured difference between acts.
    """
    return {entity_id for entity_id, (_, flat) in _series_of(result).items() if flat}


def _series_of(result: QlooResult) -> dict[str, tuple[float, bool]]:
    series = getattr(result, "series", None)
    out: dict[str, tuple[float, bool]] = {}
    if not isinstance(series, list):
        return out
    for item in series:
        if not isinstance(item, dict):
            continue
        entity = item.get("entity")
        if not isinstance(entity, dict):
            continue
        entity_id = entity.get("entityId") or entity.get("input")
        points = item.get("points")
        if not isinstance(entity_id, str) or not isinstance(points, list):
            continue
        dated = sorted(
            (
                (p["date"], v)
                for p in points
                if isinstance(p, dict)
                and isinstance(p.get("date"), str)
                and (v := _number(p.get("population_percentile"))) is not None
            ),
            key=lambda pair: pair[0],
        )
        if len(dated) < 2:
            continue
        delta = dated[-1][1] - dated[0][1]  # in [-1, 1]
        flat = len({v for _, v in dated}) == 1
        out[entity_id] = (min(1.0, max(0.0, 0.5 + delta / 2)), flat)
    return out


def _months_between(earlier: dt.date, later: dt.date) -> int:
    months = (later.year - earlier.year) * 12 + later.month - earlier.month
    if later.day < earlier.day:
        months -= 1
    return max(0, months)


# --- public API -----------------------------------------------------------------------


async def gather_signals(
    candidates: Sequence[Candidate],
    profile: CrowdProfile,
    constraints: Constraints,
    *,
    qloo: QlooExec,
    region: str,
    booked: Mapping[str, dt.date] | None = None,
    today: dt.date | None = None,
    top_n: int = DEFAULT_TOP_N,
    max_calls: int = DEFAULT_MAX_CALLS,
) -> SignalSet:
    """Read past_fit, local_heat and momentum for the top N candidates within `max_calls`.

    `candidates` are taken in the order given (Qloo's recommend order, as `generate_candidates`
    returns them); the first N get signals. `region` is `venue_region(venue)`; `booked` maps
    entity IDs to their latest show at the venue (`last_booked`); `today` anchors both the
    trends window and `months_since_booked`.
    """
    today = today or dt.date.today()
    booked = booked or {}
    past_acts = [s.entity_id for s in profile.signals[:PAST_ACTS]]
    region = region.strip()
    n = top_n_within(
        min(top_n, constraints.max_candidates, len(candidates)),
        max_calls,
        past_acts=bool(past_acts),
        region=bool(region),
    )
    top = list(candidates[:n])
    notes: list[str] = []
    if n < len(candidates):
        budget = (
            f" (to stay within {max_calls} Qloo calls)"
            if n < min(top_n, constraints.max_candidates, len(candidates))
            else ""
        )
        notes.append(
            f"Past fit, local heat and momentum were checked for the top {n} of "
            f"{len(candidates)} acts{budget}; the others get the typical value of those {n}."
        )
    if not past_acts:
        notes.append("No confirmed past act to compare with, so past fit can't tell acts apart.")
    if not region:
        notes.append("No venue region, so local heat can't tell acts apart.")

    def compare(c: Candidate) -> Callable[[], Awaitable[QlooResult]]:
        return lambda: qloo.compare_audiences([c.entity_id], past_acts, limit=TAG_LIMIT)

    def heat(c: Candidate) -> Callable[[], Awaitable[QlooResult]]:
        return lambda: qloo.where_popular(
            c.entity_id, entity_type="artist", within=region, limit=HEATMAP_LIMIT
        )

    start, end = months_before(today, TRENDS_WINDOW_MONTHS), today
    batches = [top[i : i + TRENDS_BATCH] for i in range(0, len(top), TRENDS_BATCH)]

    def trend(batch: list[Candidate]) -> Callable[[], Awaitable[QlooResult]]:
        return lambda: qloo.trends(
            [c.entity_id for c in batch],
            entity_type="artist",
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            limit=TRENDS_BATCH,
        )

    compare_outcomes, heat_outcomes, trend_outcomes = await asyncio.gather(
        asyncio.gather(
            *(_call(f"compare_audiences[{c.name}]", compare(c)) for c in top if past_acts)
        ),
        asyncio.gather(*(_call(f"where_popular[{c.name}]", heat(c)) for c in top if region)),
        asyncio.gather(
            *(
                _call(f"trends[{', '.join(c.name for c in batch)}]", trend(batch))
                for batch in batches
            )
        ),
    )

    def value(outcome: _Outcome | None, parse: Callable[[QlooResult], float | None]) -> SignalValue:
        if outcome is None:
            return SignalValue()
        parsed = parse(outcome.result) if outcome.status == "ok" and outcome.result else None
        return SignalValue(parsed, outcome.call_id, outcome.status)

    momentum: dict[str, SignalValue] = {}
    flat: set[str] = set()
    for batch, outcome in zip(batches, trend_outcomes, strict=True):
        parsed = momentum_of(outcome.result) if outcome.status == "ok" and outcome.result else {}
        if outcome.status == "ok" and outcome.result:
            flat |= flat_series(outcome.result)
        for c in batch:
            momentum[c.entity_id] = SignalValue(
                parsed.get(c.entity_id), outcome.call_id, outcome.status
            )
    measured = {e for e, v in momentum.items() if v.value is not None}
    if measured and measured <= flat:
        notes.append(
            f"Qloo's trend data was flat for every act checked over the last "
            f"{TRENDS_WINDOW_MONTHS} months, so momentum does not tell acts apart here."
        )

    def genres(outcome: _Outcome | None) -> tuple[str, ...]:
        if outcome is None or outcome.status != "ok" or not outcome.result:
            return ()
        return genres_of(outcome.result)

    by_id: dict[str, CandidateSignals] = {}
    for i, c in enumerate(top):
        last = booked.get(c.entity_id)
        by_id[c.entity_id] = CandidateSignals(
            entity_id=c.entity_id,
            past_fit=value(compare_outcomes[i] if past_acts else None, past_fit_of),
            local_heat=value(heat_outcomes[i] if region else None, local_heat_of),
            momentum=momentum.get(c.entity_id, SignalValue()),
            months_since_booked=_months_between(last, today) if last is not None else None,
            genres=genres(compare_outcomes[i] if past_acts else None),
        )
    for c in candidates[n:]:
        last = booked.get(c.entity_id)
        by_id[c.entity_id] = CandidateSignals(
            entity_id=c.entity_id,
            months_since_booked=_months_between(last, today) if last is not None else None,
        )

    outcomes = [*compare_outcomes, *heat_outcomes, *trend_outcomes]
    notes.extend(o.note for o in outcomes if o.note)
    return SignalSet(by_id=by_id, notes=notes, calls=len(outcomes))
