"""`GET /seeds` (T29): the demo venues and what has been precomputed for each.

The list joins `/agent/seeds/venues.json` (the seeds themselves) with the stored rows:
the seed's venue and run ids, whether the run is `ready` (precomputed, marked `is_seed`),
whether its compare is cached and which candidates carry a brief, so the web renders a
seed page from reads alone and never asks for a brief that would be refused (409). The
response is precomputed data, so it carries an `ETag` and answers `304` like every other
seed read.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from draw.api.auth import require_internal_key
from draw.api.deps import Deps, get_deps
from draw.api.etag import conditional
from draw.db.models import CandidateRow, CrowdProfileRow, RunRow, VenueRow
from draw.schemas import Constraints, VenueInput
from draw.seeds import Seed, load_seeds, run_id_for, venue_id_for

__all__ = ["SeedOut", "SeedsOut", "router"]

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
