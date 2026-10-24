"""Entity resolution following the kit's resolve → confirm → ask pattern.

`resolve_entity` turns a typed name into Qloo candidates (`describe` for entities,
`find_tags` for genre words) and auto-confirms only an unambiguous name match; anything
else comes back `confirmed=False` with its candidates, and the caller must ask the user,
then call `confirm_entity`. Taste workflows must never run on an unconfirmed entity.

The rule: auto-confirm iff exactly one candidate's name matches the query, exactly or
normalised (case, accents, punctuation and a leading "the" ignored). Two same-named
candidates, or none, mean the user chooses. Popularity never decides.

`confidence` (0-1) is for display and ordering only: `0.9 * name_score + 0.1 *
popularity`, where `name_score` is 1.0 for an exact match, 0.9 for a normalised match and
at most 0.5 otherwise (half the string similarity), so a name match always outranks a
non-match.

Not-found never raises: `needs_input` with `kind: not_found`, `empty`, and `ok` with no
results all return an empty candidate list. Other gateway errors (`QlooError`,
`BudgetExceeded`) propagate so the caller can show `error.recovery`.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Literal

from draw.qloo import QlooEmpty, QlooExec, QlooNeedsInput, QlooResult
from draw.qloo.exec_client import TargetType
from draw.schemas import EntityCandidate, ResolvedEntity

__all__ = [
    "EntityKind",
    "confirm_entity",
    "normalise_name",
    "resolve_entity",
    "resolve_venue",
    "synthetic_venue",
]

EntityKind = TargetType | Literal["genre"]

NAME_WEIGHT = 0.9
POPULARITY_WEIGHT = 0.1
EXACT_SCORE = 1.0
NORMALISED_SCORE = 0.9
FUZZY_CAP = 0.5
# Decision 001: for a genre word the music genre is the `urn:tag:genre:music` hit.
MUSIC_GENRE_TYPE = "urn:tag:genre:music"
GENRE_TAG_LIMIT = 5

_PUNCT = re.compile(r"[^\w\s]")
_SPACE = re.compile(r"\s+")
_LEADING_THE = re.compile(r"^the\s+")


@dataclass(frozen=True)
class _Scored:
    candidate: EntityCandidate
    score: float
    name_matched: bool
    raw: Mapping[str, Any]


# --- public API -----------------------------------------------------------------------


async def resolve_entity(
    name: str,
    type: EntityKind,  # noqa: A002 - name fixed by the task spec
    *,
    qloo: QlooExec,
) -> ResolvedEntity:
    """Resolve `name` to a Qloo entity (or tag, for `type="genre"`); never guesses."""
    query = name.strip()
    if not query:
        return _unresolved(name or "?", [])
    if type == "genre":
        return await _resolve_genre(qloo, query)

    try:
        result = await qloo.describe(query, type=type)
    except QlooNeedsInput as exc:
        return _unresolved(query, _score_all(query, exc.candidates, f"urn:entity:{type}"))
    except QlooEmpty:
        return _unresolved(query, [])
    scored = _score_all(query, _describe_candidates(result), f"urn:entity:{type}")
    return _decide(query, scored)


async def resolve_venue(name: str, city: str, *, qloo: QlooExec) -> ResolvedEntity:
    """Resolve a venue as a Qloo place; fall back to a synthetic, location-only venue.

    Candidates whose address mentions `city` take precedence: on an `ok` envelope they
    alone decide ambiguity, so a same-named venue in another city does not force an ask; on
    `needs_input` the user still chooses, with the in-city candidate listed first. With no
    candidates at all the venue is synthetic: no `entity_id`, confidence 0, confirmed
    (there is nothing to ask).
    """
    query = name.strip()
    if not query:
        return synthetic_venue(name, city)
    city_key = normalise_name(city)

    def in_city(s: _Scored) -> bool:
        return bool(city_key) and city_key in normalise_name(str(s.raw.get("address", "")))

    try:
        result = await qloo.describe(query, type="place")
    except QlooNeedsInput as exc:
        scored = _score_all(query, exc.candidates, "urn:entity:place")
        if not scored:
            return synthetic_venue(query, city)
        return _unresolved(query, _promote(scored, in_city))
    except QlooEmpty:
        return synthetic_venue(query, city)

    scored = _score_all(query, _describe_candidates(result), "urn:entity:place")
    if not scored:
        return synthetic_venue(query, city)
    return _decide(query, scored, prefer=in_city)


def synthetic_venue(name: str, city: str) -> ResolvedEntity:
    """A venue Qloo does not know: represented by its location only."""
    label = ", ".join(p for p in (name.strip(), city.strip()) if p) or "venue"
    return ResolvedEntity(
        query=name.strip() or label,
        entity_id=None,
        canonical_name=label,
        confidence=0.0,
        candidates=[],
        confirmed=True,
    )


def confirm_entity(resolved: ResolvedEntity, chosen_entity_id: str) -> ResolvedEntity:
    """Record the user's choice among `resolved.candidates`.

    Raises `ValueError` if the id is not one of the candidates: confirming an entity the
    resolver never surfaced would bypass the confirm step.
    """
    chosen = next((c for c in resolved.candidates if c.entity_id == chosen_entity_id), None)
    if chosen is None:
        raise ValueError(f"{chosen_entity_id!r} is not a candidate for {resolved.query!r}")
    return resolved.model_copy(
        update={
            "entity_id": chosen.entity_id,
            "canonical_name": chosen.name,
            "confirmed": True,
            # The user chose it: this is now as certain as resolution gets.
            "confidence": 1.0,
        }
    )


def normalise_name(name: str) -> str:
    """Lowercase, strip accents and punctuation, drop a leading "the", ignore spacing."""
    text = unicodedata.normalize("NFKD", name)
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
    text = text.replace("&", " and ")
    text = _SPACE.sub(" ", _PUNCT.sub("", text)).strip()
    text = _LEADING_THE.sub("", text)
    return text.replace(" ", "")


# --- internals ------------------------------------------------------------------------


async def _resolve_genre(qloo: QlooExec, query: str) -> ResolvedEntity:
    try:
        result = await qloo.find_tags(query, limit=GENRE_TAG_LIMIT)
    except QlooNeedsInput as exc:
        return _unresolved(query, _score_all(query, exc.candidates, "urn:tag"))
    except QlooEmpty:
        return _unresolved(query, [])
    rows = result.results if isinstance(result.results, list) else []
    scored = _score_all(query, rows, "urn:tag")
    # Semantic search returns one hit per tag family (genre:qloo, genre:music, ...), all
    # with the same name; only the music genre is a booking signal.
    return _decide(query, scored, prefer=lambda s: s.candidate.type == MUSIC_GENRE_TYPE)


def _describe_candidates(result: QlooResult) -> list[Mapping[str, Any]]:
    """Selected + alternatives from `resolution.outcomes`, else the `results` rows.

    Outcome popularities come from the same `/search` call, so they are comparable with
    each other; `results[].popularity` comes from `/entities` and is only a fallback.
    """
    out: list[Mapping[str, Any]] = []
    resolution = getattr(result, "resolution", None)
    outcomes = resolution.get("outcomes") if isinstance(resolution, dict) else None
    for outcome in outcomes if isinstance(outcomes, list) else []:
        if not isinstance(outcome, dict):
            continue
        selected = outcome.get("selected")
        if isinstance(selected, dict):
            out.append(selected)
        out.extend(a for a in outcome.get("alternatives") or [] if isinstance(a, dict))
    if not out and isinstance(result.results, list):
        out.extend(r for r in result.results if isinstance(r, dict))
    return out


def _score_all(query: str, rows: Iterable[Mapping[str, Any]], default_type: str) -> list[_Scored]:
    """Turn raw candidate rows into scored candidates, best first, deduplicated by id."""
    seen: set[str] = set()
    scored: list[_Scored] = []
    for row in rows:
        entity_id = row.get("entity_id") or row.get("id")
        name = row.get("name")
        if not isinstance(entity_id, str) or not entity_id or entity_id in seen:
            continue
        if not isinstance(name, str):
            continue
        seen.add(entity_id)
        kind = row.get("type")
        address = row.get("address")
        candidate = EntityCandidate(
            entity_id=entity_id,
            name=name,
            type=kind if isinstance(kind, str) and kind else default_type,
            address=address.strip() if isinstance(address, str) and address.strip() else None,
        )
        name_score = _name_score(query, name)
        score = NAME_WEIGHT * name_score + POPULARITY_WEIGHT * _popularity(row)
        scored.append(_Scored(candidate, round(score, 4), name_score >= NORMALISED_SCORE, row))
    # Stable sort keeps Qloo's own order among equal scores.
    scored.sort(key=lambda s: s.score, reverse=True)
    return scored


def _name_score(query: str, name: str) -> float:
    if query.casefold().strip() == name.casefold().strip():
        return EXACT_SCORE
    q, n = normalise_name(query), normalise_name(name)
    if q and q == n:
        return NORMALISED_SCORE
    return FUZZY_CAP * SequenceMatcher(None, q, n).ratio()


def _popularity(row: Mapping[str, Any]) -> float:
    value = row.get("popularity")
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0.0
    return min(max(float(value), 0.0), 1.0)


def _promote(scored: list[_Scored], prefer: Callable[[_Scored], bool]) -> list[_Scored]:
    """Preferred candidates first, otherwise unchanged; nothing is dropped."""
    return [s for s in scored if prefer(s)] + [s for s in scored if not prefer(s)]


def _decide(
    query: str, scored: list[_Scored], prefer: Callable[[_Scored], bool] | None = None
) -> ResolvedEntity:
    """Auto-confirm iff exactly one candidate's name matches the query.

    `prefer` narrows the pool (music genres, venues in the right city) when any candidate
    satisfies it, so same-named entries outside the pool do not force an ask. All
    candidates are still returned, preferred and matching ones first.
    """
    if not scored:
        return _unresolved(query, [])
    ordered = _promote(scored, prefer) if prefer else scored
    pool = [s for s in ordered if prefer(s)] if prefer else []
    pool = pool or ordered
    matches = [s for s in pool if s.name_matched]
    if len(matches) != 1:
        return _unresolved(query, ordered)
    top = matches[0]
    ordered = [top, *(s for s in ordered if s is not top)]
    return ResolvedEntity(
        query=query,
        entity_id=top.candidate.entity_id,
        canonical_name=top.candidate.name,
        confidence=top.score,
        candidates=[s.candidate for s in ordered],
        confirmed=True,
    )


def _unresolved(query: str, scored: list[_Scored]) -> ResolvedEntity:
    """Ask the user: no `entity_id` until they choose one of `candidates`."""
    return ResolvedEntity(
        query=query,
        entity_id=None,
        canonical_name=scored[0].candidate.name if scored else query,
        confidence=scored[0].score if scored else 0.0,
        candidates=[s.candidate for s in scored],
        confirmed=False,
    )
