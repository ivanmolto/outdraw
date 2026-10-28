"""Anonymous sessions (T46): the web's cookie names a `sessions` row that holds the plan.

`POST /sessions` makes a new row on the free plan and returns its id, which the web sets as
an httpOnly cookie; `GET /sessions/{id}` reads the plan back (404 for an unknown id, after
which the web makes a new one). No login, no personal data: the id is random and the row
holds nothing but the plan. HACKATHON_MODE is the web's concern: it lifts every session to
Pro in the response, and the stored plan stays what was paid for (T47 flips it).
"""

from __future__ import annotations

import secrets
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from draw.api.auth import require_internal_key
from draw.api.deps import Deps, get_deps
from draw.db.models import SessionRow

__all__ = ["SESSION_PREFIX", "SessionOut", "router"]

SESSION_PREFIX = "ses_"

router = APIRouter(prefix="/sessions", dependencies=[Depends(require_internal_key)])
DepsDep = Annotated[Deps, Depends(get_deps)]


class SessionOut(BaseModel):
    id: str
    plan: Literal["free", "pro"]


def new_session_id() -> str:
    """32 URL-safe characters: 144 random bits, so an id cannot be guessed."""
    return SESSION_PREFIX + secrets.token_urlsafe(21)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_session(deps: DepsDep) -> SessionOut:
    """A new session on the free plan."""
    row = SessionRow(id=new_session_id(), plan="free")
    async with deps.sessions.begin() as session:
        session.add(row)
    return SessionOut(id=row.id, plan="free")


@router.get("/{session_id}")
async def get_session(session_id: str, deps: DepsDep) -> SessionOut:
    """The session's stored plan; 404 for an unknown id."""
    async with deps.sessions() as session:
        row = await session.get(SessionRow, session_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown session.")
    return SessionOut(id=row.id, plan="pro" if row.plan == "pro" else "free")
