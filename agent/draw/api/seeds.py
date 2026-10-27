"""`GET /seeds` (T29): the demo venues and what has been precomputed for each.

The list joins `/agent/seeds/venues.json` (the seeds themselves) with the stored rows:
the seed's venue and run ids, whether the run is `ready` (precomputed, marked `is_seed`),
whether its compare is cached and which candidates carry a brief, so the web renders a
seed page from reads alone and never asks for a brief that would be refused (409). The
response is precomputed data, so it carries an `ETag` and answers `304` like every other
seed read.

`GET /seeds/{slug}/page` (T30) is everything the web's seed page shows in one read: the
venue with its profile, the run, every candidate's evidence, the run's recorded calls, the
precomputed briefs and the compare. One request instead of ~25, so a cold web instance
fills its cache in one round trip; 404 for an unknown slug, 409 while the seed is not
precomputed. Read-only like every seed read: nothing here can spend a call.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from draw.agent.brief import BriefOut
from draw.agent.compare import CompareOut
from draw.api.auth import require_internal_key
from draw.api.deps import Deps, get_deps
from draw.api.etag import conditional
from draw.api.runs import SEED_NOT_PRECOMPUTED, Evidence, QlooCall, RunOut, run_out
from draw.api.venues import VenueOut, get_venue
from draw.db.models import CandidateRow, CrowdProfileRow, QlooCallRow, RunRow, VenueRow
from draw.schemas import Constraints, VenueInput
from draw.seeds import Seed, load_seeds, run_id_for, venue_id_for

__all__ = ["SeedOut", "SeedPageOut", "SeedsOut", "router"]

router = APIRouter(prefix="/seeds", dependencies=[Depends(require_internal_key)])
DepsDep = Annotated[Deps, Depends(get_deps)]


class SeedOut(BaseModel):
    slug: str
    title: str
    description: str
    venue_id: str
    run_id: str
    venue: VenueInput
    constraints: Constraints
    message: str
    ready: bool
    """The run exists, finished `ok` and is marked `is_seed`: every read below is free."""
    venue_stored: bool
    profile_stored: bool
    run_status: str | None = None
    compare_ready: bool = False
    brief_entity_ids: list[str] = Field(default_factory=list)
    """Candidates whose brief was precomputed, in the run's shortlist order."""
    created_at: datetime | None = None


class SeedsOut(BaseModel):
    seeds: list[SeedOut]


class SeedPageOut(BaseModel):
    """One seed's page, as `GET /seeds/{slug}/page` serves it."""

    seed: SeedOut
    venue: VenueOut
    run: RunOut
    candidates: list[Evidence]
    """Every stored candidate of the run, in the shortlist's order."""
    calls: list[QlooCall]
    briefs: list[BriefOut]
    """The precomputed briefs, in the shortlist's order (`seed.brief_entity_ids`)."""
    compare: CompareOut | None = None


async def _seed_out(deps: Deps, seed: Seed) -> SeedOut:
    venue_id, run_id = venue_id_for(seed.slug), run_id_for(seed.slug)
    async with deps.sessions() as session:
        venue = await session.get(VenueRow, venue_id)
        profile = await session.get(CrowdProfileRow, venue_id)
        run = await session.get(RunRow, run_id)
        briefed: list[str] = []
        if run is not None:
            rows = await session.scalars(
                select(CandidateRow).where(
                    CandidateRow.run_id == run_id, CandidateRow.brief.is_not(None)
                )
            )
            briefed = [r.entity_id for r in rows]
    order = {
        str(e.get("entity_id")): i
        for i, e in enumerate((run.result or {}).get("shortlist") or [] if run else [])
    }
    briefed.sort(key=lambda e: order.get(e, len(order)))
    return SeedOut(
        slug=seed.slug,
        title=seed.title,
        description=seed.description,
        venue_id=venue_id,
        run_id=run_id,
        venue=seed.venue,
        constraints=seed.constraints,
        message=seed.message,
        ready=bool(run is not None and run.is_seed and run.status == "ok"),
        venue_stored=venue is not None,
        profile_stored=profile is not None,
        run_status=run.status if run else None,
        compare_ready=bool(run is not None and run.compare is not None),
        brief_entity_ids=briefed,
        created_at=run.created_at if run else None,
    )


@router.get("")
async def list_seeds(request: Request, response: Response, deps: DepsDep) -> SeedsOut:
    """Every seed in file order with its precompute state; ETag over the whole list."""
    out = SeedsOut(seeds=[await _seed_out(deps, seed) for seed in load_seeds()])
    conditional(request, response, out, seed=True)
    return out


@router.get("/{slug}/page")
async def seed_page(slug: str, request: Request, response: Response, deps: DepsDep) -> SeedPageOut:
    """The whole seed page in one read; 404 unknown slug, 409 not precomputed."""
    seed = next((s for s in load_seeds() if s.slug == slug), None)
    if seed is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown seed {slug!r}.")
    out = await _seed_out(deps, seed)
    if not out.ready or not out.profile_stored:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"reason": SEED_NOT_PRECOMPUTED, "message": "This seed is not precomputed yet."},
        )
    venue = await get_venue(out.venue_id, deps)
    async with deps.sessions() as session:
        run_row = await session.get(RunRow, out.run_id)
        assert run_row is not None  # `ready` above means it is stored
        rows = (
            await session.scalars(select(CandidateRow).where(CandidateRow.run_id == out.run_id))
        ).all()
        call_rows = (
            await session.scalars(
                select(QlooCallRow)
                .where(QlooCallRow.run_id == out.run_id)
                .order_by(QlooCallRow.created_at, QlooCallRow.call_id)
            )
        ).all()
    order = {
        str(e.get("entity_id")): i
        for i, e in enumerate((run_row.result or {}).get("shortlist") or [])
    }
    by_entity = {r.entity_id: r for r in rows}
    ranked = sorted(rows, key=lambda r: order.get(r.entity_id, len(order)))
    page = SeedPageOut(
        seed=out,
        venue=venue,
        run=run_out(run_row, rows),
        candidates=[
            Evidence(
                run_id=out.run_id,
                entity_id=r.entity_id,
                score=r.score,
                evidence=r.evidence,
                pencilled=r.pencilled,
                pencil_date=r.pencil_date if r.pencilled else None,
            )
            for r in ranked
        ],
        calls=[
            QlooCall(
                call_id=r.call_id,
                workflow=r.workflow,
                status=r.status,
                cache_hit=r.cache_hit,
                request=r.request,
                envelope=r.envelope,
                created_at=r.created_at,
            )
            for r in call_rows
        ],
        briefs=[
            BriefOut.model_validate({**brief, "cached": True})
            for e in out.brief_entity_ids
            if e in by_entity and (brief := by_entity[e].brief) is not None
        ],
        compare=(
            CompareOut.model_validate({**run_row.compare, "cached": True})
            if run_row.compare is not None
            else None
        ),
    )
    conditional(request, response, page, seed=True)
    return page
