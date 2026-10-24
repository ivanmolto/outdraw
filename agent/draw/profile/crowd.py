"""Crowd profile: from a venue's confirmed past acts to a `CrowdProfile` backed by Qloo.

Pure data module: no LLM, no personal data. The inputs are venue location, capacities,
ticket counts and confirmed Qloo entity IDs; the outputs are aggregate affinities.

Pipeline (`build_crowd_profile`):

1. **Signals.** Only confirmed past acts (T07) become signals; unconfirmed ones are skipped
   and listed in `notes`. Each act is weighted by its sell-through (`tickets_sold /
   capacity`, the show's own capacity or else the venue's), averaged over its shows and
   clamped to 0.1-1.0, with 0.5 when unknown. Signals are ordered by weight. The harness
   takes signals as a plain list of IDs (no per-signal weights), so the weights decide the
   order, which of the top acts get a demographics call, and are exported in
   `profile.signals` for the ranking stage.
2. **Fill calls**, one per target type, all through the gateway: `recommend` for related
   artists (with `signal_location`), places (with `filter_location`, plus `include_tags`
   when the caller passes bar/restaurant tag URNs), films and brands; `compare_audiences`
   (best-selling act vs the rest, or vs itself for a single act) for genre tags, since
   `recommend` results carry no tags and its `urn:tag:genre:music` rows are the music
   genres the crowd shares. `audience_demographics` on the top signals while the call
   budget allows. Cold, a venue with confirmed acts costs 5 fill calls + 1 demographics =
   6 (`DEFAULT_MAX_CALLS`); a second demographics call needs `max_calls=7`.
3. **Status branching**, per envelope: `ok` fills its list; `partial` fills it and sets
   `data_quality=partial`; `degraded`, `empty`, `needs_input` and non-retryable `error`
   leave the list empty (or keep what came back for `degraded`) and set `degraded`. Every
   non-ok summary goes into `notes`. `BudgetExceeded` propagates: nothing was spawned.
4. **Evidence.** Every item points at the `call_id` of the call that produced it, as
   `<list>[<index>] -> call_id`; `demographics` points at every demographics call.

A venue with no confirmed acts (or no past shows at all) still gets a profile: empty lists,
`data_quality=degraded`, and a note saying why. No Qloo call is made for it.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from draw.profile.resolve import normalise_name
from draw.qloo import QlooEmpty, QlooError, QlooExec, QlooNeedsInput, QlooResult
from draw.schemas import (
    CrowdProfile,
    DataQuality,
    EvidenceRef,
    PastShow,
    ProfileItem,
    ResolvedEntity,
    Signal,
    VenueInput,
)

__all__ = [
    "DEFAULT_MAX_CALLS",
    "FILL_CALLS",
    "build_crowd_profile",
    "sell_through_weight",
    "venue_region",
    "weigh_signals",
]

DEFAULT_MAX_CALLS = 6
"""The per-run profile budget from CLAUDE.md (profile ≤ 6 Qloo calls cold)."""
FILL_CALLS = 5
"""recommend × 4 (artists, places, films, brands) + compare_audiences (genres)."""
DEMOGRAPHICS_SIGNALS = 2
MAX_SIGNALS = 10  # the harness accepts at most 10 signal IDs per call
ITEM_LIMIT = 10
WEIGHT_MIN, WEIGHT_MAX, WEIGHT_DEFAULT = 0.1, 1.0, 0.5
GENRE_MARKER = ":genre:"

ListName = Literal["genres", "related_artists", "places", "films", "brands"]
_SLUG = re.compile(r"[^a-z0-9]+")


# --- signals --------------------------------------------------------------------------


def sell_through_weight(shows: Iterable[PastShow], venue_capacity: int) -> float:
    """Mean sell-through over `shows`, clamped to 0.1-1.0; 0.5 when no show has numbers."""
    ratios = [
        show.tickets_sold / cap
        for show in shows
        if show.tickets_sold is not None and (cap := show.capacity or venue_capacity) > 0
    ]
    if not ratios:
        return WEIGHT_DEFAULT
    return round(min(WEIGHT_MAX, max(WEIGHT_MIN, sum(ratios) / len(ratios))), 4)


@dataclass(frozen=True)
class _Seed:
    entity_id: str
    name: str
    weight: float


def weigh_signals(
    venue: VenueInput, confirmed: Sequence[ResolvedEntity]
) -> tuple[list[_Seed], list[str]]:
    """Confirmed acts as weighted seeds, best-selling first, plus the notes for the rest."""
    shows_by_act: dict[str, list[PastShow]] = {}
    for show in venue.past_shows:
        shows_by_act.setdefault(normalise_name(show.act), []).append(show)

    notes: list[str] = []
    seeds: dict[str, _Seed] = {}
    for act in confirmed:
        if not act.confirmed or not act.entity_id:
            notes.append(f"Skipped {act.query!r}: not confirmed as a Qloo entity.")
            continue
        shows = shows_by_act.get(normalise_name(act.query)) or shows_by_act.get(
            normalise_name(act.canonical_name)
        )
        if shows is None:
            notes.append(f"{act.query!r} has no past show on record; weighted {WEIGHT_DEFAULT}.")
            weight = WEIGHT_DEFAULT
        else:
            weight = sell_through_weight(shows, venue.capacity)
        previous = seeds.get(act.entity_id)
        if previous is None or weight > previous.weight:
            seeds[act.entity_id] = _Seed(act.entity_id, act.canonical_name, weight)

    ordered = sorted(seeds.values(), key=lambda s: (-s.weight, s.name.casefold(), s.entity_id))
    if len(ordered) > MAX_SIGNALS:
        dropped = ", ".join(repr(s.name) for s in ordered[MAX_SIGNALS:])
        notes.append(
            f"Only the {MAX_SIGNALS} best-selling acts are used as signals; dropped {dropped}."
        )
        ordered = ordered[:MAX_SIGNALS]
    return ordered, notes


def venue_region(venue: VenueInput) -> str:
    """The free-text location query Qloo geocodes, e.g. "New York, United States"."""
    return ", ".join(part.strip() for part in (venue.city, venue.country) if part.strip())


def _venue_id(venue: VenueInput) -> str:
    return _SLUG.sub("-", f"{venue.name} {venue.city}".casefold()).strip("-") or "venue"


# --- calls ----------------------------------------------------------------------------


@dataclass
class _Outcome:
    """What one gateway call contributed, whatever its status."""

    name: str
    status: str
    call_id: str | None = None
    rows: list[dict[str, Any]] = field(default_factory=list)
    note: str | None = None


async def _call(name: str, make: Callable[[], Awaitable[QlooResult]]) -> _Outcome:
    """Run one gateway call and fold every status into an `_Outcome`; never raises on data."""
    try:
        result = await make()
    except QlooEmpty as exc:
        env = exc.envelope
        return _Outcome(
            name,
            "empty",
            call_id=getattr(env, "call_id", None),
            note=f"{name}: {env.summary or 'Qloo returned no results.'}",
        )
    except QlooNeedsInput as exc:
        # Signals are confirmed IDs, so this means an ID Qloo no longer knows; nothing to ask.
        return _Outcome(
            name,
            "needs_input",
            call_id=getattr(exc.envelope, "call_id", None),
            note=f"{name}: {exc.envelope.summary or 'Qloo could not resolve an input.'}",
        )
    except QlooError as exc:
        return _Outcome(
            name,
            "error",
            call_id=getattr(exc.envelope, "call_id", None),
            note=f"{name}: {exc.code}. {exc.recovery}".rstrip(),
        )
    rows = _rows(result.results)
    note = None
    if result.status != "ok":
        note = f"{name}: {result.summary or f'Qloo reported status {result.status}.'}"
    return _Outcome(name, result.status, call_id=result.call_id, rows=rows, note=note)


def _rows(results: Any) -> list[dict[str, Any]]:
    """The result rows of an envelope.

    `recommend` and `audience_demographics` return a list. `compare_audiences` returns an
    object `{tags, a, b, matchEntities}`: the shared tags come first (they carry a
    `query.score`), then each group's own tags, so a single-act crowd still gets genres.
    """
    if isinstance(results, list):
        return [r for r in results if isinstance(r, dict)]
    if isinstance(results, dict):
        return [
            r for key in ("tags", "a", "b") for r in results.get(key) or [] if isinstance(r, dict)
        ]
    return []


def _affinity(row: Mapping[str, Any]) -> float | None:
    """`affinity` (recommend) or `query.score` (compare_audiences), clamped to [0, 1]."""
    value = row.get("affinity")
    if value is None:
        query = row.get("query")
        value = query.get("score") if isinstance(query, dict) else None
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return min(1.0, max(0.0, float(value)))


def _items(rows: Iterable[Mapping[str, Any]], *, genres_only: bool = False) -> list[ProfileItem]:
    """Deduplicated `ProfileItem`s in Qloo's order; `genres_only` keeps `*:genre:*` tags."""
    items: list[ProfileItem] = []
    seen: set[str] = set()
    for row in rows:
        # compare_audiences tag rows carry both `tag_id` (the tag) and `entity_id` (its source).
        entity_id = row.get("tag_id") or row.get("entity_id") or row.get("id")
        name = row.get("name")
        if not isinstance(entity_id, str) or not entity_id or entity_id in seen:
            continue
        kind = f"{row.get('subtype', '')}{row.get('type', '')}{entity_id}"
        if genres_only and GENRE_MARKER not in kind:
            continue
        seen.add(entity_id)
        items.append(
            ProfileItem(
                entity_id=entity_id,
                name=name if isinstance(name, str) else entity_id,
                affinity=_affinity(row),
            )
        )
        if len(items) >= ITEM_LIMIT:
            break
    return items


def _demographics(outcomes: list[tuple[_Seed, _Outcome]]) -> dict[str, Any] | None:
    """Weight-averaged `age`/`gender` affinities over the signals that returned data."""
    buckets: dict[str, dict[str, float]] = {}
    total = 0.0
    entities: list[str] = []
    for seed, outcome in outcomes:
        row = outcome.rows[0] if outcome.rows else None
        query = row.get("query") if isinstance(row, dict) else None
        if not isinstance(query, dict):
            continue
        entities.append(seed.entity_id)
        total += seed.weight
        for group in ("age", "gender"):
            values = query.get(group)
            if not isinstance(values, dict):
                continue
            target = buckets.setdefault(group, {})
            for key, value in values.items():
                if isinstance(value, int | float) and not isinstance(value, bool):
                    target[key] = target.get(key, 0.0) + seed.weight * float(value)
    if not entities or total <= 0:
        return None
    averaged = {
        group: {k: round(v / total, 4) for k, v in values.items()}
        for group, values in buckets.items()
    }
    return {**averaged, "entities": entities}


# --- public API -----------------------------------------------------------------------


async def build_crowd_profile(
    venue: VenueInput,
    confirmed: Sequence[ResolvedEntity],
    *,
    qloo: QlooExec,
    venue_entity: ResolvedEntity | None = None,
    venue_id: str | None = None,
    place_tags: Sequence[str] = (),
    max_calls: int = DEFAULT_MAX_CALLS,
) -> CrowdProfile:
    """Build the venue's `CrowdProfile` from its confirmed past acts with at most `max_calls`.

    `place_tags` are pre-resolved tag URNs (bars, restaurants) to narrow the places list;
    words are not accepted because the harness would answer `needs_input`. `venue_entity`
    is the venue's own resolution (T07); its ID is carried as `qloo_venue_id` when confirmed.
    """
    seeds, notes = weigh_signals(venue, confirmed)
    profile = CrowdProfile(
        venue_id=venue_id or _venue_id(venue),
        qloo_venue_id=venue_entity.entity_id if venue_entity and venue_entity.confirmed else None,
        signals=[Signal(entity_id=s.entity_id, weight=s.weight) for s in seeds],
        genres=[],
        related_artists=[],
        places=[],
        films=[],
        brands=[],
        demographics=None,
        evidence=[],
        data_quality="degraded",
        notes=notes,
    )
    if not seeds:
        notes.append("No confirmed past act to profile from; the crowd profile is empty.")
        profile.notes = notes
        return profile

    ids = [s.entity_id for s in seeds]
    region = venue_region(venue)
    fills: dict[ListName, Callable[[], Awaitable[QlooResult]]] = {
        "related_artists": lambda: qloo.recommend(
            "artist", signals=ids, signal_location=region, limit=ITEM_LIMIT
        ),
        "genres": lambda: qloo.compare_audiences(ids[:1], ids[1:] or ids[:1], limit=ITEM_LIMIT),
        "places": lambda: qloo.recommend(
            "place",
            signals=ids,
            filter_location=region,
            include_tags=list(place_tags) or None,
            include_tags_operator="union" if place_tags else None,
            limit=ITEM_LIMIT,
        ),
        "films": lambda: qloo.recommend("movie", signals=ids, limit=ITEM_LIMIT),
        "brands": lambda: qloo.recommend("brand", signals=ids, limit=ITEM_LIMIT),
    }
    demographic_seeds = seeds[: max(0, min(DEMOGRAPHICS_SIGNALS, max_calls - len(fills)))]
    if len(demographic_seeds) < min(DEMOGRAPHICS_SIGNALS, len(seeds)):
        notes.append(
            f"Demographics read for {len(demographic_seeds)} of the top "
            f"{min(DEMOGRAPHICS_SIGNALS, len(seeds))} signals to stay within {max_calls} calls."
        )

    def demographics_call(seed: _Seed) -> Callable[[], Awaitable[QlooResult]]:
        return lambda: qloo.audience_demographics(seed.entity_id, entity_type="artist")

    fill_outcomes, demo_outcomes = await asyncio.gather(
        asyncio.gather(*(_call(name, make) for name, make in fills.items())),
        asyncio.gather(
            *(
                _call(f"audience_demographics[{seed.name}]", demographics_call(seed))
                for seed in demographic_seeds
            )
        ),
    )

    evidence: list[EvidenceRef] = []
    for name, outcome in zip(fills, fill_outcomes, strict=True):
        items = _items(outcome.rows, genres_only=name == "genres")
        if name == "genres":
            # A group's own tags carry no score and are not shared by the crowd (one act's
            # "Canadian"); they stand in only when no shared genre came back.
            items = [i for i in items if i.affinity is not None] or items
        setattr(profile, name, items)
        if outcome.call_id:
            evidence.extend(
                EvidenceRef(item_ref=f"{name}[{i}]", call_id=outcome.call_id)
                for i in range(len(items))
            )

    profile.demographics = _demographics(list(zip(demographic_seeds, demo_outcomes, strict=True)))
    if profile.demographics is not None:
        evidence.extend(
            EvidenceRef(item_ref="demographics", call_id=o.call_id)
            for o in demo_outcomes
            if o.call_id and o.rows
        )

    outcomes = [*fill_outcomes, *demo_outcomes]
    notes.extend(o.note for o in outcomes if o.note)
    profile.notes = notes
    profile.evidence = evidence
    profile.data_quality = _data_quality(o.status for o in outcomes)
    return profile


def _data_quality(statuses: Iterable[str]) -> DataQuality:
    """ok if all ok; partial if any partial; degraded if any degraded, empty or failed."""
    seen = set(statuses)
    if seen - {"ok", "partial"}:
        return "degraded"
    if "partial" in seen:
        return "partial"
    return "ok"
