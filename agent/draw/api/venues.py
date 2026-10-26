"""Venue routes: create/update, resolve (T07), confirm, crowd profile (T08).

Resolve → confirm → ask, persisted: `resolve` stores one `resolutions` row per item (the
venue and each distinct past act), auto-confirmed only when T07 says so; `confirm` records
the user's pick, or `chosen_entity_id: null` for "not in Qloo"; `profile` refuses (409)
while any item is unconfirmed, so no taste workflow ever runs on an unconfirmed entity.
The venue's own row is keyed `venue:<name>` so it cannot collide with a same-named act;
clients treat each item's `query` as an opaque key and echo it back to `confirm`, never
build one (`kind` says whether a row is the venue or an act).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from draw.agent.store import new_id
from draw.api.auth import require_internal_key
from draw.api.deps import Deps, get_deps
from draw.api.live import live_call
from draw.db.models import CrowdProfileRow, ResolutionRow, VenueRow
from draw.profile.crowd import build_crowd_profile
from draw.profile.resolve import confirm_entity, resolve_entity, resolve_venue, synthetic_venue
from draw.schemas import CrowdProfile, EntityCandidate, ResolvedEntity, VenueInput

__all__ = ["VENUE_PREFIX", "load_resolutions", "load_venue", "router"]

VENUE_PREFIX = "venue:"

router = APIRouter(prefix="/venues", dependencies=[Depends(require_internal_key)])
DepsDep = Annotated[Deps, Depends(get_deps)]


class VenueUpsert(VenueInput):
    id: str | None = None
    """Omit to create; pass an existing id to update that venue."""


class VenueId(BaseModel):
    id: str


class ResolutionItem(BaseModel):
    kind: Literal["venue", "act"]
    query: str
    """Opaque key: echo it back to /confirm as-is (the venue's is prefixed `venue:`)."""
    confirmed: bool
    entity_id: str | None
    canonical_name: str
    candidates: list[EntityCandidate]


class Resolutions(BaseModel):
    venue_id: str
    items: list[ResolutionItem]


class ConfirmBody(BaseModel):
    query: str
    """A `ResolutionItem.query` from /resolve, unchanged."""
    chosen_entity_id: str | None
    """One of the item's candidates, or null for "not in Qloo" (skipped, nothing to ask)."""


class VenueOut(BaseModel):
    id: str
    venue: VenueInput
    resolutions: list[ResolutionItem]
    profile: CrowdProfile | None


# --- helpers shared with the run routes -----------------------------------------------


def _venue_input(row: VenueRow) -> VenueInput:
    return VenueInput.model_validate(
        {
            "name": row.name,
            "city": row.city,
            "country": row.country,
            "lat": row.lat,
            "lng": row.lng,
            "capacity": row.capacity,
            "past_shows": row.past_shows,
        }
    )


async def load_venue(session: AsyncSession, venue_id: str) -> tuple[VenueRow, VenueInput]:
    row = await session.get(VenueRow, venue_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown venue {venue_id!r}.")
    return row, _venue_input(row)


def _keys(venue: VenueInput) -> list[tuple[Literal["venue", "act"], str]]:
    """The items to resolve, in display order: the venue, then each distinct past act."""
    acts = dict.fromkeys(s.act.strip() for s in venue.past_shows if s.act.strip())
    return [("venue", VENUE_PREFIX + venue.name.strip()), *(("act", a) for a in acts)]


def _resolved(row: ResolutionRow, venue: VenueInput) -> ResolvedEntity:
    candidates = [EntityCandidate.model_validate(c) for c in row.candidates]
    is_venue = row.query.startswith(VENUE_PREFIX)
    query = row.query.removeprefix(VENUE_PREFIX) if is_venue else row.query
    chosen = next((c for c in candidates if c.entity_id == row.chosen_entity_id), None)
    if chosen is not None:
        name = chosen.name
    elif is_venue:
        name = synthetic_venue(query, venue.city).canonical_name
    else:
        name = query
    return ResolvedEntity(
        query=query,
        entity_id=chosen.entity_id if chosen else None,
        canonical_name=name,
        confidence=1.0 if chosen and row.confirmed else 0.0,
        candidates=candidates,
        confirmed=row.confirmed,
    )


async def load_resolutions(
    session: AsyncSession, venue_id: str, venue: VenueInput
) -> tuple[ResolvedEntity | None, list[ResolvedEntity], list[str]]:
    """(venue entity, past acts, keys still to resolve) as stored for `venue_id`."""
    rows = {
        r.query: r
        for r in await session.scalars(
            select(ResolutionRow).where(ResolutionRow.venue_id == venue_id)
        )
    }
    venue_entity: ResolvedEntity | None = None
    acts: list[ResolvedEntity] = []
    missing: list[str] = []
    for kind, key in _keys(venue):
        row = rows.get(key)
        if row is None:
            missing.append(key)
        elif kind == "venue":
            venue_entity = _resolved(row, venue)
        else:
            acts.append(_resolved(row, venue))
    return venue_entity, acts, missing


def _item(kind: Literal["venue", "act"], key: str, resolved: ResolvedEntity) -> ResolutionItem:
    return ResolutionItem(
        kind=kind,
        query=key,
        confirmed=resolved.confirmed,
        entity_id=resolved.entity_id,
        canonical_name=resolved.canonical_name,
        candidates=resolved.candidates,
    )


async def _items(session: AsyncSession, venue_id: str, venue: VenueInput) -> list[ResolutionItem]:
    venue_entity, acts, _ = await load_resolutions(session, venue_id, venue)
    by_query = {a.query: a for a in acts}
    items: list[ResolutionItem] = []
    for kind, key in _keys(venue):
        resolved = venue_entity if kind == "venue" else by_query.get(key)
        if resolved is not None:
            items.append(_item(kind, key, resolved))
    return items


async def _upsert_resolution(
    session: AsyncSession, venue_id: str, key: str, resolved: ResolvedEntity
) -> None:
    values: dict[str, Any] = {
        "venue_id": venue_id,
        "query": key,
        "candidates": [c.model_dump(mode="json") for c in resolved.candidates],
        "chosen_entity_id": resolved.entity_id,
        "confirmed": resolved.confirmed,
    }
    stmt = insert(ResolutionRow).values(**values)
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=["venue_id", "query"],
            set_={**{k: stmt.excluded[k] for k in values}, "updated_at": func.now()},
        )
    )


async def _drop_profile(session: AsyncSession, venue_id: str) -> None:
    await session.execute(delete(CrowdProfileRow).where(CrowdProfileRow.venue_id == venue_id))


# --- routes ---------------------------------------------------------------------------


@router.post("", status_code=status.HTTP_201_CREATED)
async def upsert_venue(body: VenueUpsert, deps: DepsDep) -> VenueId:
    """Create a venue, or update one by `id`; an update drops stale resolutions and profile."""
    venue = VenueInput.model_validate(body.model_dump(exclude={"id"}))
    fields: dict[str, Any] = {
        **venue.model_dump(mode="json", exclude={"past_shows"}),
        "past_shows": [s.model_dump(mode="json") for s in venue.past_shows],
    }
    async with deps.sessions.begin() as session:
        if body.id is None:
            venue_id = new_id("ven")
            session.add(VenueRow(id=venue_id, **fields))
        else:
            venue_id = body.id
            row = await session.get(VenueRow, venue_id)
            if row is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown venue {venue_id!r}.")
            for name, value in fields.items():
                setattr(row, name, value)
            row.updated_at = func.now()
            keep = [key for _, key in _keys(venue)]
            await session.execute(
                delete(ResolutionRow).where(
                    ResolutionRow.venue_id == venue_id, ResolutionRow.query.not_in(keep)
                )
            )
            await _drop_profile(session, venue_id)
    return VenueId(id=venue_id)


@router.get("/{venue_id}")
async def get_venue(venue_id: str, deps: DepsDep) -> VenueOut:
    async with deps.sessions() as session:
        _, venue = await load_venue(session, venue_id)
        items = await _items(session, venue_id, venue)
        profile_row = await session.get(CrowdProfileRow, venue_id)
    profile = CrowdProfile.model_validate(profile_row.profile) if profile_row else None
    return VenueOut(id=venue_id, venue=venue, resolutions=items, profile=profile)


@router.post("/{venue_id}/resolve")
async def resolve(venue_id: str, deps: DepsDep) -> Resolutions:
    """Resolve the venue and each past act not resolved yet (stored items are not re-asked)."""
    async with deps.sessions() as session:
        _, venue = await load_venue(session, venue_id)
        _, _, missing = await load_resolutions(session, venue_id, venue)
    resolved: dict[str, ResolvedEntity] = {}
    if missing:
        async with live_call(deps, venue_id=venue_id):
            qloo = deps.gateways.scripted()
            for key in missing:
                if key.startswith(VENUE_PREFIX):
                    resolved[key] = await resolve_venue(venue.name, venue.city, qloo=qloo)
                else:
                    resolved[key] = await resolve_entity(key, "artist", qloo=qloo)

    async with deps.sessions.begin() as session:
        for key, entity in resolved.items():
            await _upsert_resolution(session, venue_id, key, entity)
        if resolved:
            await _drop_profile(session, venue_id)
    async with deps.sessions() as session:
        items = await _items(session, venue_id, venue)
    return Resolutions(venue_id=venue_id, items=items)


@router.post("/{venue_id}/confirm")
async def confirm(venue_id: str, body: ConfirmBody, deps: DepsDep) -> ResolutionItem:
    async with deps.sessions.begin() as session:
        venue_row, venue = await load_venue(session, venue_id)
        row = await session.get(ResolutionRow, (venue_id, body.query))
        if row is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"{body.query!r} has not been resolved for this venue; call /resolve first.",
            )
        current = _resolved(row, venue)
        if body.chosen_entity_id is None:
            chosen = current.model_copy(update={"entity_id": None, "confirmed": True})
        else:
            try:
                chosen = confirm_entity(current, body.chosen_entity_id)
            except ValueError as exc:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        await _upsert_resolution(session, venue_id, body.query, chosen)
        kind: Literal["venue", "act"] = "act"
        if body.query.startswith(VENUE_PREFIX):
            kind = "venue"
            venue_row.qloo_venue_id = chosen.entity_id
        await _drop_profile(session, venue_id)
    return _item(kind, body.query, chosen)


@router.post("/{venue_id}/profile")
async def profile(venue_id: str, deps: DepsDep) -> CrowdProfile:
    """Build and store the crowd profile; 409 while any item is unresolved or unconfirmed."""
    async with deps.sessions() as session:
        _, venue = await load_venue(session, venue_id)
        venue_entity, acts, missing = await load_resolutions(session, venue_id, venue)
    pending = missing + [a.query for a in acts if not a.confirmed]
    if venue_entity is not None and not venue_entity.confirmed:
        pending.insert(0, VENUE_PREFIX + venue_entity.query)
    if pending:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"message": "Confirm every match before building the profile.", "pending": pending},
        )
    async with live_call(deps, venue_id=venue_id):
        built = await build_crowd_profile(
            venue,
            acts,
            qloo=deps.gateways.scripted(),
            venue_entity=venue_entity,
            venue_id=venue_id,
        )
    data = built.model_dump(mode="json")
    async with deps.sessions.begin() as session:
        stmt = insert(CrowdProfileRow).values(venue_id=venue_id, profile=data)
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=["venue_id"],
                set_={"profile": stmt.excluded.profile, "updated_at": func.now()},
            )
        )
    return built
