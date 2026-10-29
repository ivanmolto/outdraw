"""Request bodies: the bounds every user-typed field must meet before a route runs.

The bounds are `draw.limits`, generated from /shared/limits.json like `web/types/limits.ts`
(CI fails when either drifts), so input the web app let through also passes here, and
anything else is refused before it costs a Qloo call or Claude tokens. Bounds are tighter
than the generated models in `draw.schemas`, which also read stored rows: routes validate
with these, then hand on the generated type.

Every free-text field is cleaned first: control characters (Unicode Cc) are dropped, except
tab, newline and carriage return, which a booker's message may legitimately hold; then the
ends are trimmed, so a whitespace-only or control-only value counts as empty. A failure answers 400
`{reason: "invalid_input", field}` (`draw.main`).
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints

from draw.limits import SHARED_LIMITS as LIMITS
from draw.schemas import Constraints, PastShow, SizeTier, VenueInput

__all__ = [
    "LIMITS",
    "AnswerBody",
    "ConfirmBody",
    "ConstraintsBody",
    "InvalidInput",
    "PastShowBody",
    "RefineBody",
    "RunBody",
    "VenueUpsert",
    "clean",
]


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def clean(value: Any) -> Any:
    """Control characters out, then trimmed; non-strings pass on to fail the type check."""
    if not isinstance(value, str):
        return value
    return _CONTROL.sub("", value).strip()


Clean = BeforeValidator(clean)


def _bounded(max_length: int, min_length: int = 1) -> StringConstraints:
    return StringConstraints(min_length=min_length, max_length=max_length)


Name = Annotated[str, Clean, _bounded(LIMITS.name)]
City = Annotated[str, Clean, _bounded(LIMITS.city)]
Country = Annotated[str, Clean, _bounded(LIMITS.country)]
Act = Annotated[str, Clean, _bounded(LIMITS.act)]
Query = Annotated[str, Clean, _bounded(LIMITS.query)]
Tag = Annotated[str, Clean, _bounded(LIMITS.tag)]
Message = Annotated[str, Clean, _bounded(LIMITS.message)]
OptionalMessage = Annotated[str, Clean, _bounded(LIMITS.message, min_length=0)]
Id = Annotated[
    str, StringConstraints(min_length=1, max_length=LIMITS.id, pattern=r"^[A-Za-z0-9:._-]+$")
]


class PastShowBody(PastShow):
    act: Act
    tickets_sold: Annotated[int | None, Field(ge=0, le=LIMITS.capacity)] = None
    capacity: Annotated[int | None, Field(ge=1, le=LIMITS.capacity)] = None
    ticket_price: Annotated[float | None, Field(ge=0.0, le=LIMITS.ticket_price)] = None
    gross: Annotated[float | None, Field(ge=0.0, le=LIMITS.gross)] = None


class VenueUpsert(BaseModel):
    """`POST /venues`: omit `id` to create, pass an existing one to update that venue."""

    model_config = ConfigDict(extra="forbid")
    id: Id | None = None
    name: Name
    city: City
    country: Country
    lat: Annotated[float | None, Field(ge=-90.0, le=90.0)] = None
    lng: Annotated[float | None, Field(ge=-180.0, le=180.0)] = None
    capacity: Annotated[int, Field(ge=1, le=LIMITS.capacity)]
    past_shows: Annotated[list[PastShowBody], Field(max_length=LIMITS.past_shows)]

    def venue(self) -> VenueInput:
        return VenueInput.model_validate(self.model_dump(exclude={"id"}))


class ConfirmBody(BaseModel):
    """`POST /venues/{id}/confirm`."""

    model_config = ConfigDict(extra="forbid")
    query: Query
    """A `ResolutionItem.query` from /resolve, unchanged."""
    chosen_entity_id: Id | None
    """One of the item's candidates, or null for "not in Qloo" (skipped, nothing to ask)."""


class ConstraintsBody(Constraints):
    include_tags: Annotated[list[Tag], Field(max_length=LIMITS.tags)]
    exclude_tags: Annotated[list[Tag], Field(max_length=LIMITS.tags)]
    size_tier: SizeTier
    exclude_recent_months: Annotated[int, Field(ge=0, le=LIMITS.recent_months)]
    max_candidates: Annotated[int, Field(ge=1, le=LIMITS.max_candidates)]


class RunBody(BaseModel):
    """`POST /runs`."""

    model_config = ConfigDict(extra="forbid")
    venue_id: Id
    constraints: ConstraintsBody
    message: OptionalMessage = ""


class RefineBody(BaseModel):
    """`POST /runs/{id}/refine`: what to change, in the booker's words."""

    model_config = ConfigDict(extra="forbid")
    message: Message


class AnswerBody(BaseModel):
    """`POST /runs/{id}/answer`: the Qloo tag the booker chose for a word the run asked about."""

    model_config = ConfigDict(extra="forbid")
    for_input: Query
    """The word, as the ask's candidate carries it in `for_input`."""
    entity_id: Id
    """The chosen candidate's `id` (a tag URN)."""


class InvalidInput(BaseModel):
    """The 400 body for a request that fails these bounds (`draw.main`)."""

    reason: Literal["invalid_input"]
    field: str | None = None
    """Dotted path of the first invalid field (`past_shows.0.act`); absent for unparseable JSON."""


INVALID_INPUT: dict[int | str, dict[str, Any]] = {
    400: {"model": InvalidInput, "description": "Input out of bounds."}
}
"""`APIRouter(responses=...)` for every router whose routes take user input."""
