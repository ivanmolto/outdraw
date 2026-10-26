"""The demo venues (T29): what `/agent/seeds/venues.json` holds and the ids it maps to.

A seed is a venue the precompute script (`scripts/precompute.py`) runs the whole pipeline
for ahead of time, so judges open its results with zero Qloo or model calls. The file is
the one source of the seeds; the database holds what was computed for each. Ids are
deterministic (`ven_seed_<slug>`, `run_seed_<slug>`) so the script is idempotent and
`GET /seeds` can join the file with the stored rows.

Seed acts are chosen to resolve unambiguously (one exact name match on `describe`); the
script fails loudly when one does not. Nothing here is personal data: venue names, cities,
act names, dates and ticket counts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from draw.limits import SHARED_LIMITS
from draw.qloo.harness import AGENT_DIR
from draw.schemas import Constraints, VenueInput

__all__ = ["SEEDS_FILE", "Seed", "load_seeds", "run_id_for", "venue_id_for"]

SEEDS_FILE: Final = AGENT_DIR / "seeds" / "venues.json"
SLUG_PATTERN: Final = r"^[a-z0-9]+(-[a-z0-9]+)*$"


class Seed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str = Field(pattern=SLUG_PATTERN, min_length=1, max_length=40)
    title: str = Field(min_length=1)
    description: str = ""
    venue: VenueInput
    constraints: Constraints
    message: str = Field(default="", max_length=SHARED_LIMITS.message)
    """The booker's request the agent run answers (the run's `message`), within the bound
    a booker could type (`draw.api.inputs`)."""


_SEEDS = TypeAdapter(list[Seed])


def venue_id_for(slug: str) -> str:
    return f"ven_seed_{slug}"


def run_id_for(slug: str) -> str:
    return f"run_seed_{slug}"


def load_seeds(path: Path = SEEDS_FILE) -> list[Seed]:
    """The seeds in file order; duplicate slugs are a configuration error."""
    seeds = _SEEDS.validate_python(json.loads(path.read_text(encoding="utf-8")))
    slugs = [s.slug for s in seeds]
    if len(set(slugs)) != len(slugs):
        raise ValueError(f"duplicate seed slugs in {path}: {slugs}")
    return seeds
