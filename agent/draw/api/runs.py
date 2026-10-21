"""Run routes: one synchronous agent run (T13) per POST, stored with its candidates.

A run needs the venue's stored crowd profile (409 otherwise: the profile step is where the
confirm gate sits). The gateway for the run carries the run's id, so every Qloo call it
makes lands in `qloo_calls` under that id. The stored result is the agent's final JSON
(`shortlist` from `score_and_rank`, or `ask`); each ranked candidate is stored with its
score and its evidence trail (`get_evidence`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from draw.agent.agent import AgentResult, run_venue_agent
from draw.agent.store import new_id
from draw.agent.tools import DrawTools
from draw.api.auth import require_internal_key
from draw.api.deps import Deps, get_deps
from draw.api.venues import load_resolutions, load_venue
from draw.db.models import CandidateRow, CrowdProfileRow, QlooCallRow, RunRow
from draw.qloo.exec_client import QlooExec
from draw.schemas import Constraints, CrowdProfile

__all__ = ["router"]

MAX_MESSAGE = 2000

router = APIRouter(prefix="/runs", dependencies=[Depends(require_internal_key)])
DepsDep = Annotated[Deps, Depends(get_deps)]


class RunBody(BaseModel):
    venue_id: str
    constraints: Constraints
    message: str = Field(default="", max_length=MAX_MESSAGE)


class RunOut(BaseModel):
    run_id: str
    venue_id: str
    status: str
    constraints: Constraints
    result: dict[str, Any] | None
    token_usage: dict[str, Any] | None
    created_at: datetime | None = None


class Evidence(BaseModel):
    run_id: str
    entity_id: str
    score: dict[str, Any]
    evidence: dict[str, Any]


class QlooCall(BaseModel):
    call_id: str
    workflow: str
    status: str
    cache_hit: bool
    request: dict[str, Any]
    envelope: dict[str, Any]
    created_at: datetime


def _out(row: RunRow) -> RunOut:
    return RunOut(
        run_id=row.id,
        venue_id=row.venue_id,
        status=row.status,
        constraints=Constraints.model_validate(row.constraints),
        result=row.result,
        token_usage=row.token_usage,
        created_at=row.created_at,
    )


async def _candidates(result: AgentResult, qloo: QlooExec, run_id: str) -> list[CandidateRow]:
    """Every ranked candidate of the agent's run, with its score and evidence trail."""
    store = result.store
    if store is None or result.run_id is None or result.run_id not in store.runs:
        return []
    run = store.run(result.run_id)
    store.assign_candidate_ids(run)
    tools = DrawTools(store, qloo)
    rows: list[CandidateRow] = []
    for item in run.ranked or []:
        entity_id = item.candidate.entity_id
        evidence = await tools.get_evidence(run.candidate_ids[entity_id])
        rows.append(
            CandidateRow(
                run_id=run_id,
                entity_id=entity_id,
                score=item.score.model_dump(mode="json"),
                evidence=evidence,
            )
        )
    return rows


@router.post("")
async def create_run(body: RunBody, deps: DepsDep) -> RunOut:
    async with deps.sessions() as session:
        _, venue = await load_venue(session, body.venue_id)
        venue_entity, acts, _ = await load_resolutions(session, body.venue_id, venue)
        profile_row = await session.get(CrowdProfileRow, body.venue_id)
    if profile_row is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "No crowd profile for this venue; confirm its matches and POST /profile first.",
        )
    profile = CrowdProfile.model_validate(profile_row.profile)

    run_id = new_id("run")
    constraints = body.constraints.model_dump(mode="json")
    async with deps.sessions.begin() as session:
        session.add(
            RunRow(id=run_id, venue_id=body.venue_id, constraints=constraints, status="running")
        )

    try:
        async with deps.gateways.agent(run_id) as qloo:
            result = await run_venue_agent(
                venue,
                body.constraints,
                body.message,
                qloo=qloo,
                acts=acts,
                venue_entity=venue_entity if venue_entity and venue_entity.entity_id else None,
                profile=profile,
                venue_id=body.venue_id,
                model=deps.model_factory() if deps.model_factory else None,
                token_counter=deps.llm_counter,
                token_cap=deps.llm_cap,
            )
            candidates = await _candidates(result, qloo, run_id)
    except Exception as exc:
        async with deps.sessions.begin() as session:
            row = await session.get(RunRow, run_id)
            assert row is not None
            row.status = "error"
            row.result = {"error": str(exc) or type(exc).__name__}
        raise

    stored = {
        **result.to_json(),
        "stop_reason": result.stop_reason,
        "steps": result.steps,
        "profile_id": result.profile_id,
    }
    async with deps.sessions.begin() as session:
        row = await session.get(RunRow, run_id)
        assert row is not None
        row.status = result.status
        row.result = stored
        row.token_usage = result.usage
        session.add_all(candidates)
    async with deps.sessions() as session:
        row = await session.get(RunRow, run_id)
        assert row is not None
        return _out(row)


@router.get("/{run_id}")
async def get_run(run_id: str, deps: DepsDep) -> RunOut:
    async with deps.sessions() as session:
        row = await session.get(RunRow, run_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown run {run_id!r}.")
    return _out(row)


@router.get("/{run_id}/candidates/{entity_id}/evidence")
async def get_evidence(run_id: str, entity_id: str, deps: DepsDep) -> Evidence:
    async with deps.sessions() as session:
        row = await session.get(CandidateRow, (run_id, entity_id))
    if row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"No candidate {entity_id!r} in run {run_id!r}."
        )
    return Evidence(run_id=run_id, entity_id=entity_id, score=row.score, evidence=row.evidence)


@router.get("/{run_id}/calls")
async def get_calls(run_id: str, deps: DepsDep) -> list[QlooCall]:
    """The run's Qloo calls in order, as recorded (the API key already redacted)."""
    async with deps.sessions() as session:
        if await session.get(RunRow, run_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown run {run_id!r}.")
        rows = await session.scalars(
            select(QlooCallRow)
            .where(QlooCallRow.run_id == run_id)
            .order_by(QlooCallRow.created_at, QlooCallRow.call_id)
        )
        return [
            QlooCall(
                call_id=r.call_id,
                workflow=r.workflow,
                status=r.status,
                cache_hit=r.cache_hit,
                request=r.request,
                envelope=r.envelope,
                created_at=r.created_at,
            )
            for r in rows
        ]
