"""The kill switch (T33): `LIVE_MODE` (on|off) and the seed a refused run falls back to.

With `LIVE_MODE=off` every route that would spend a Qloo or model call is refused before it
does, with 503 `{reason: "live_mode_off", fallback_seed}`: `POST /runs` and
`POST /runs/{id}/refine` (`live_run`), and through `require_live` a `/venues/{id}/resolve`
with acts left to match, a `/venues/{id}/profile` build and the first brief or compare of a
live run. Nothing is stored and nothing is spent; what is stored (a cached brief, a resolve
with nothing new) is still served.
The same routes name the seed on a 429 `{reason: "budget"}` too (the before-run guard or a
cap crossed midway), so the web answers both alike: it shows that seed's precomputed page
with a banner. Unset means on. Any value other than on/off fails the app's startup, so a
typo never leaves the switch in an unknown state (Render keeps the previous deploy serving).

The fallback is the precomputed seed closest to the run's venue: the venue itself when it is
a seed, then same city (New York's boroughs count as one), same country, shared include
tags and the nearest capacity. Seeds not precomputed yet are skipped; with none ready, or
when the venue can't be read, it is the first seed in `venues.json`.
"""

from __future__ import annotations

import logging
import math
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final

from draw.api.deps import Deps
from draw.db.models import RunRow, VenueRow
from draw.qloo.envelope import BudgetExceeded
from draw.schemas import Constraints
from draw.seeds import Seed, load_seeds, run_id_for, venue_id_for

__all__ = [
    "LIVE_MODE_OFF",
    "LiveModeOff",
    "closest_seed",
    "fallback_seed",
    "live_mode",
    "live_run",
    "require_live",
]

log = logging.getLogger("draw.live")

LIVE_MODE_OFF: Final = "live_mode_off"

# The five boroughs and the usual spellings of the city, as one place.
_NEW_YORK: Final = frozenset(
    {
        "new york",
        "new york city",
        "nyc",
        "manhattan",
        "brooklyn",
        "queens",
        "bronx",
        "the bronx",
        "staten island",
    }
)


class LiveModeOff(Exception):  # noqa: N818  (a refusal, like BudgetExceeded)
    """`LIVE_MODE=off`: the run was refused; the API answers 503 with `fallback_seed`."""

    def __init__(self, fallback_seed: str) -> None:
        super().__init__("Live analysis is switched off (LIVE_MODE=off).")
        self.fallback_seed = fallback_seed


def live_mode() -> bool:
    """`LIVE_MODE` from the environment: on (the default) or off; anything else raises."""
    value = os.environ.get("LIVE_MODE", "on").strip().lower() or "on"
    if value not in ("on", "off"):
        raise ValueError(f"LIVE_MODE must be 'on' or 'off', not {value!r}")
    return value == "on"


def _city(city: str) -> str:
    key = " ".join(city.lower().split())
    return "new york" if key in _NEW_YORK else key


def closest_seed(
    seeds: list[Seed],
    *,
    venue_id: str | None = None,
    city: str | None = None,
    country: str | None = None,
    capacity: int | None = None,
    include_tags: list[str] | None = None,
) -> str:
    """The slug of the seed nearest to what is known of the venue; the first seed when
    nothing is known. `seeds` must not be empty."""
    tags = set(include_tags or [])

    def key(seed: Seed) -> tuple[bool, bool, bool, int, float]:
        venue = seed.venue
        return (
            venue_id == venue_id_for(seed.slug),
            city is not None and _city(city) == _city(venue.city),
            country is not None and country.strip().lower() == venue.country.lower(),
            len(tags & set(seed.constraints.include_tags)),
            -abs(math.log(capacity / venue.capacity)) if capacity else 0.0,
        )

    # max() keeps the first of equal keys, so ties go to file order.
    return max(seeds, key=key).slug


async def _ready(deps: Deps, seeds: list[Seed]) -> list[Seed]:
    async with deps.sessions() as session:
        rows = [await session.get(RunRow, run_id_for(s.slug)) for s in seeds]
    return [s for s, r in zip(seeds, rows, strict=True) if r and r.is_seed and r.status == "ok"]


async def fallback_seed(
    deps: Deps,
    *,
    venue_id: str | None = None,
    run_id: str | None = None,
    include_tags: list[str] | None = None,
) -> str:
    """The seed for a refused run on `venue_id` with `include_tags`, or for a refinement of
    run `run_id` (its venue and tags). Never raises: a refusal must still name a seed, so a
    failed read falls back to the first seed."""
    seeds = load_seeds()
    try:
        seeds = await _ready(deps, seeds) or seeds
        async with deps.sessions() as session:
            run = await session.get(RunRow, run_id) if run_id else None
            if run is not None:
                venue_id = run.venue_id
                include_tags = Constraints.model_validate(run.constraints).include_tags
            venue = await session.get(VenueRow, venue_id) if venue_id else None
    except Exception as exc:
        log.warning("fallback seed: venue not read (%s); using the first seed", type(exc).__name__)
        return seeds[0].slug
    if venue is None:
        return closest_seed(seeds, venue_id=venue_id, include_tags=include_tags)
    return closest_seed(
        seeds,
        venue_id=venue.id,
        city=venue.city,
        country=venue.country,
        capacity=venue.capacity,
        include_tags=include_tags,
    )


async def require_live(
    deps: Deps, *, venue_id: str | None = None, run_id: str | None = None
) -> None:
    """`LiveModeOff` when the switch is off: called right before a route spends."""
    if not live_mode():
        raise LiveModeOff(await fallback_seed(deps, venue_id=venue_id, run_id=run_id))


@asynccontextmanager
async def live_run(
    deps: Deps,
    *,
    venue_id: str | None = None,
    run_id: str | None = None,
    include_tags: list[str] | None = None,
) -> AsyncIterator[None]:
    """Around a run route: `LiveModeOff` when the switch is off, then the daily-budget check;
    a `BudgetExceeded` raised before or during the run leaves with the fallback seed on it."""

    async def seed() -> str:
        return await fallback_seed(
            deps, venue_id=venue_id, run_id=run_id, include_tags=include_tags
        )

    await require_live(deps, venue_id=venue_id, run_id=run_id)
    try:
        await deps.guard.check()
        yield
    except BudgetExceeded as exc:
        exc.fallback_seed = await seed()
        raise
