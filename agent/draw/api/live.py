"""The kill switch (T33): `LIVE_MODE` (on|off) and the seed a refused call falls back to.

With `LIVE_MODE=off` every route that would spend a Qloo or model call is refused before it
does, with 503 `{reason: "live_mode_off", fallback_seed}`: `POST /runs` and
`POST /runs/{id}/refine` (`live_run`), and through `live_call` a `/venues/{id}/resolve` with
acts left to match, a `/venues/{id}/profile` build and the first brief or compare of a live
run. Nothing is stored and nothing is spent; what is stored (a cached brief, a resolve with
nothing new) is still served. The same routes name the seed on a 429 `{reason: "budget"}`
(the before-run guard or a cap crossed midway), so the web answers both alike. Unset means
on. Any value other than on/off fails the app's startup, so a typo never leaves the switch
in an unknown state (Render keeps the previous deploy serving).

The fallback is the precomputed seed closest to the venue: the venue itself when it is a
seed, else the first seed in the same city, else the first in the same country, else the
first in `venues.json`. Seeds not precomputed yet are skipped. A refusal must stay cheap and
must never fail, so the seeds file is read once and a failed lookup is the first seed.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import cache
from typing import Final

from sqlalchemy import select

from draw.api.deps import Deps
from draw.db.models import RunRow, VenueRow
from draw.qloo.envelope import BudgetExceeded
from draw.seeds import Seed, load_seeds, run_id_for, venue_id_for

__all__ = [
    "LIVE_MODE_OFF",
    "LiveModeOff",
    "closest_seed",
    "fallback_seed",
    "live_call",
    "live_mode",
    "live_run",
]

log = logging.getLogger("draw.live")

LIVE_MODE_OFF: Final = "live_mode_off"


class LiveModeOff(Exception):  # noqa: N818  (a refusal, like BudgetExceeded)
    """`LIVE_MODE=off`: the call was refused; the API answers 503 with `fallback_seed`."""

    def __init__(self, fallback_seed: str) -> None:
        super().__init__("Live analysis is switched off (LIVE_MODE=off).")
        self.fallback_seed = fallback_seed


def live_mode() -> bool:
    """`LIVE_MODE` from the environment: on (the default) or off; anything else raises."""
    value = os.environ.get("LIVE_MODE", "on").strip().lower() or "on"
    if value not in ("on", "off"):
        raise ValueError(f"LIVE_MODE must be 'on' or 'off', not {value!r}")
    return value == "on"


@cache
def _seeds() -> tuple[Seed, ...]:
    """`venues.json` once per process: it never changes while the service runs."""
    return tuple(load_seeds())


def _place(name: str) -> str:
    return " ".join(name.lower().split())


def closest_seed(
    seeds: tuple[Seed, ...] | list[Seed],
    *,
    venue_id: str | None = None,
    city: str | None = None,
    country: str | None = None,
) -> str:
    """The slug of the seed nearest to what is known of the venue; the first seed when
    nothing matches. `seeds` must not be empty."""

    def key(seed: Seed) -> tuple[bool, bool, bool]:
        return (
            venue_id == venue_id_for(seed.slug),
            city is not None and _place(city) == _place(seed.venue.city),
            country is not None and _place(country) == _place(seed.venue.country),
        )

    # max() keeps the first of equal keys, so ties go to file order.
    return max(seeds, key=key).slug


async def fallback_seed(
    deps: Deps, *, venue_id: str | None = None, run_id: str | None = None
) -> str:
    """The seed for a refused call on `venue_id`, or on the venue of run `run_id`. One read
    for the precomputed seeds and one for the venue; never raises."""
    seeds = _seeds()
    try:
        async with deps.sessions() as session:
            ready = await session.scalars(
                select(RunRow.seed_slug).where(
                    RunRow.id.in_([run_id_for(s.slug) for s in seeds]),
                    RunRow.is_seed.is_(True),
                    RunRow.status == "ok",
                )
            )
            slugs = set(ready.all())
            if run_id is not None:
                venue_id = await session.scalar(select(RunRow.venue_id).where(RunRow.id == run_id))
            venue = await session.get(VenueRow, venue_id) if venue_id else None
    except Exception as exc:
        log.warning("fallback seed: not read (%s); using the first seed", type(exc).__name__)
        return seeds[0].slug
    candidates = tuple(s for s in seeds if s.slug in slugs) or seeds
    if venue is None:
        return closest_seed(candidates, venue_id=venue_id)
    return closest_seed(candidates, venue_id=venue.id, city=venue.city, country=venue.country)


@asynccontextmanager
async def live_call(
    deps: Deps, *, venue_id: str | None = None, run_id: str | None = None
) -> AsyncIterator[None]:
    """Around the part of a route that spends: `LiveModeOff` when the switch is off, and a
    `BudgetExceeded` raised inside leaves with the fallback seed on it."""
    if not live_mode():
        raise LiveModeOff(await fallback_seed(deps, venue_id=venue_id, run_id=run_id))
    try:
        yield
    except BudgetExceeded as exc:
        if exc.fallback_seed is None:
            exc.fallback_seed = await fallback_seed(deps, venue_id=venue_id, run_id=run_id)
        raise


@asynccontextmanager
async def live_run(
    deps: Deps, *, venue_id: str | None = None, run_id: str | None = None
) -> AsyncIterator[None]:
    """`live_call` plus the daily-budget check, for a run or a refinement."""
    async with live_call(deps, venue_id=venue_id, run_id=run_id):
        await deps.guard.check()
        yield
