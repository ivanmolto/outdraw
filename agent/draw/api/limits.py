"""`GET /limits`: today's Qloo calls and LLM tokens against their caps (counts only)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from draw.api.auth import require_internal_key
from draw.api.deps import Deps, get_deps

__all__ = ["router"]

router = APIRouter(dependencies=[Depends(require_internal_key)])


class Counter(BaseModel):
    used: int
    cap: int
    remaining: int


class Limits(BaseModel):
    date: str
    """UTC day the counters belong to (they reset at 00:00 UTC)."""
    qloo: Counter
    llm: Counter


def _counter(used: int, cap: int) -> Counter:
    return Counter(used=used, cap=cap, remaining=max(0, cap - used))


@router.get("/limits")
async def limits(deps: Annotated[Deps, Depends(get_deps)]) -> Limits:
    gateways = deps.gateways
    return Limits(
        date=datetime.now(UTC).strftime("%Y-%m-%d"),
        qloo=_counter(await gateways.counter.used(), gateways.config.daily_cap),
        llm=_counter(await deps.llm_counter.used(), deps.llm_cap),
    )
