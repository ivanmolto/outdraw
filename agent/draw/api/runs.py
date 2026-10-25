"""Run routes: one synchronous agent run (T13) per POST, stored with its candidates.

A run needs the venue's stored crowd profile (409 otherwise: the profile step is where the
confirm gate sits). The gateway for the run carries the run's id, so every Qloo call it
makes lands in `qloo_calls` under that id. The stored result is the agent's final JSON
(`shortlist` from `score_and_rank`, or `ask`); each ranked candidate is stored with its
score and its full evidence trail (`DrawTools.evidence_trail`: the T22 `EvidenceTrail` plus
the candidate's ids, validated for completeness when it was built). Shortlist entries also
carry their
sub-scores, `top_reason`, neutral fallbacks and source labels, and the result the
`weights`; `RunResult` is the typed view the web reads through the OpenAPI contract.

`POST /runs/{id}/refine {message}` (T25) is a new run chained to its parent by
`parent_run_id`: the message is parsed into a `ConstraintsDelta` (one structured-output
call), merged into the parent's constraints, and the agent re-runs under the merged
constraints with the same caps as any run. The stored result also carries `refinement`:
the message, the delta and a one-sentence explanation of what changed.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from draw.agent.agent import AgentResult, run_venue_agent
from draw.agent.compare import CompareOut, llm_only_shortlist
from draw.agent.refine import ConstraintsDelta, explain, merge, parse_delta, refine_request
from draw.agent.store import new_id
from draw.agent.tools import DrawTools
from draw.api.auth import require_internal_key
from draw.api.deps import Deps, get_deps
from draw.api.venues import load_resolutions, load_venue
from draw.db.models import CandidateRow, CrowdProfileRow, QlooCallRow, RunRow
from draw.qloo.exec_client import QlooExec
from draw.ranking.score import WEIGHTS, SourceLabel, SubScore, source_labels
from draw.schemas import (
    Constraints,
    CrowdProfile,
    DataQuality,
    ResolvedEntity,
    ScoreBreakdown,
    ScoreWeights,
    VenueInput,
)

__all__ = ["router"]

MAX_MESSAGE = 2000

router = APIRouter(prefix="/runs", dependencies=[Depends(require_internal_key)])
DepsDep = Annotated[Deps, Depends(get_deps)]


class RunBody(BaseModel):
    venue_id: str
    constraints: Constraints
    message: str = Field(default="", max_length=MAX_MESSAGE)


class RefineBody(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_MESSAGE)


class Refinement(BaseModel):
    """How a refined run came to be: the booker's message, the delta read from it (as
    applied) and the explanation of what changed against the parent run."""

    message: str
    delta: ConstraintsDelta
    explanation: str
    token_usage: dict[str, int] = Field(default_factory=dict)
    """The parse call's tokens; the run's own are on `RunOut.token_usage`."""


class ShortlistEntry(BaseModel):
    """One ranked act, in `score_and_rank`'s order. The score fields come from the stored
    candidate; `reason` is the agent's own (templated for the top five, else the model's)."""

    candidate_id: str
    entity_id: str
    name: str
    total: float
    reason: str | None = None
    top_reason: str | None = None
    sub_scores: ScoreBreakdown | None = None
    neutral: list[SubScore] = Field(default_factory=list)
    source_labels: dict[SubScore, SourceLabel] | None = None


class AskOut(BaseModel):
    question: str
    candidates: list[dict[str, Any]]


class RunResult(BaseModel):
    """The agent's final JSON as stored: `shortlist` for an ok run, `ask` when a tool
    needed the user, `error` when the run failed."""

    shortlist: list[ShortlistEntry] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    data_quality: DataQuality = "degraded"
    weights: ScoreWeights | None = None
    ask: AskOut | None = None
    error: str | None = None
    stop_reason: str | None = None
    steps: int | None = None
    profile_id: str | None = None
    refinement: Refinement | None = None


class RunOut(BaseModel):
    run_id: str
    venue_id: str
    status: str
    constraints: Constraints
    result: RunResult | None
    token_usage: dict[str, Any] | None
    created_at: datetime | None = None
    parent_run_id: str | None = None
    """The run this one refined (`POST /runs/{id}/refine`); None for a first run."""


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
        result=RunResult.model_validate(row.result) if row.result is not None else None,
        token_usage=row.token_usage,
        created_at=row.created_at,
        parent_run_id=row.parent_run_id,
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
        # The full EvidenceTrail (validated when score_and_rank stored it), not the model's view.
        evidence = tools.evidence_trail(run.candidate_ids[entity_id])
        rows.append(
            CandidateRow(
                run_id=run_id,
                entity_id=entity_id,
                score=item.score.model_dump(mode="json"),
                evidence=evidence,
            )
        )
    return rows


def _with_scores(result: dict[str, Any], candidates: list[CandidateRow]) -> dict[str, Any]:
    """The result with each shortlist entry's `top_reason`, `sub_scores`, `neutral` and
    `source_labels`, and the run's `weights`, so the shortlist page draws its chips without
    a call per act (the `ShortlistEntry` shape)."""
    if "shortlist" not in result:
        return result
    scores = {row.entity_id: row.score for row in candidates}
    shortlist = []
    for entry in result["shortlist"]:
        score = scores.get(entry["entity_id"])
        if score is not None:
            entry = {
                **entry,
                "top_reason": score["top_reason"],
                "sub_scores": score["breakdown"],
                "neutral": score["neutral"],
                "source_labels": source_labels(score["neutral"]),
            }
        shortlist.append(entry)
    return {**result, "shortlist": shortlist, "weights": dict(WEIGHTS)}


@dataclass(frozen=True)
class _Venue:
    """What a run needs about its venue, as stored: the input, its resolutions, its profile."""

    venue_id: str
    venue: VenueInput
    acts: Sequence[ResolvedEntity]
    venue_entity: ResolvedEntity | None
    profile: CrowdProfile


async def _venue_for_run(session: AsyncSession, venue_id: str) -> _Venue:
    """404 for an unknown venue, 409 before its crowd profile exists (the confirm gate)."""
    _, venue = await load_venue(session, venue_id)
    venue_entity, acts, _ = await load_resolutions(session, venue_id, venue)
    profile_row = await session.get(CrowdProfileRow, venue_id)
    if profile_row is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "No crowd profile for this venue; confirm its matches and POST /profile first.",
        )
    return _Venue(
        venue_id=venue_id,
        venue=venue,
        acts=acts,
        venue_entity=venue_entity if venue_entity and venue_entity.entity_id else None,
        profile=CrowdProfile.model_validate(profile_row.profile),
    )


async def _execute_run(
    deps: Deps,
    run_id: str,
    target: _Venue,
    constraints: Constraints,
    message: str,
    *,
    parent_run_id: str | None = None,
    finish: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> RunOut:
    """Store the run as running, run the agent, store the result (or the error) and read it
    back. `finish` sees the stored result before it is written (the refinement adds to it)."""
    async with deps.sessions.begin() as session:
        session.add(
            RunRow(
                id=run_id,
                venue_id=target.venue_id,
                parent_run_id=parent_run_id,
                constraints=constraints.model_dump(mode="json"),
                status="running",
            )
        )

    try:
        async with deps.gateways.agent(run_id) as qloo:
            result = await run_venue_agent(
                target.venue,
                constraints,
                message,
                qloo=qloo,
                acts=target.acts,
                venue_entity=target.venue_entity,
                profile=target.profile,
                venue_id=target.venue_id,
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
        **_with_scores(result.to_json(), candidates),
        "stop_reason": result.stop_reason,
        "steps": result.steps,
        "profile_id": result.profile_id,
    }
    if finish is not None:
        stored = finish(stored)
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


@router.post("")
async def create_run(body: RunBody, deps: DepsDep) -> RunOut:
    async with deps.sessions() as session:
        target = await _venue_for_run(session, body.venue_id)
    return await _execute_run(deps, new_id("run"), target, body.constraints, body.message)


def _shortlist_names(result: dict[str, Any] | None) -> list[str]:
    return [str(e.get("name", "")) for e in (result or {}).get("shortlist") or []]


@router.post("/{run_id}/refine")
async def refine_run(run_id: str, body: RefineBody, deps: DepsDep) -> RunOut:
    """A new run under the parent's constraints changed as the message asks.

    404 for an unknown parent; 422 when no change can be read from the message (nothing is
    spent on Qloo then); 429 at the LLM token cap. The parse is one model call, the re-run a
    normal agent run with every cap of `POST /runs`.
    """
    async with deps.sessions() as session:
        parent = await session.get(RunRow, run_id)
        if parent is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown run {run_id!r}.")
        target = await _venue_for_run(session, parent.venue_id)
    previous = Constraints.model_validate(parent.constraints)
    before = _shortlist_names(parent.result)

    parsed = await parse_delta(
        body.message,
        previous,
        before,
        model_factory=deps.model_factory,
        token_counter=deps.llm_counter,
        token_cap=deps.llm_cap,
    )
    if parsed.delta is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, parsed.error)
    delta = parsed.delta
    merged = merge(previous, delta)
    if merged == previous and not delta.hints:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "The message asks for no change to the constraints; say what to change "
            "(genres, size of act, recent bookings, how many candidates).",
        )

    def finish(stored: dict[str, Any]) -> dict[str, Any]:
        refinement = Refinement(
            message=body.message,
            delta=delta,
            explanation=explain(delta, previous, merged, before, _shortlist_names(stored)),
            token_usage=parsed.token_usage,
        )
        return {**stored, "refinement": refinement.model_dump(mode="json")}

    return await _execute_run(
        deps,
        new_id("run"),
        target,
        merged,
        refine_request(body.message, delta, before),
        parent_run_id=run_id,
        finish=finish,
    )


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


_compare_locks: dict[str, asyncio.Lock] = {}
"""One lock per run around its first compare call, so concurrent requests spend one call
(the service runs as one process; a second request waits, then reads the cached copy)."""


async def _cached_compare(deps: Deps, run_id: str) -> CompareOut | tuple[VenueInput, Constraints]:
    """The run's cached compare, or what the first call needs; 404 for an unknown run."""
    async with deps.sessions() as session:
        row = await session.get(RunRow, run_id)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown run {run_id!r}.")
        if row.compare is not None:
            return CompareOut.model_validate({**row.compare, "cached": True})
        _, venue = await load_venue(session, row.venue_id)
        return venue, Constraints.model_validate(row.constraints)


@router.get("/{run_id}/compare")
async def get_compare(run_id: str, deps: DepsDep) -> CompareOut:
    """The LLM-only shortlist for the run's venue and constraints: one tool-less Haiku 5.5
    call the first time, then the copy cached on the run (no tokens spent)."""
    found = await _cached_compare(deps, run_id)
    if isinstance(found, CompareOut):
        return found
    lock = _compare_locks.setdefault(run_id, asyncio.Lock())
    async with lock:
        # A request that waited here finds the first one's result cached.
        found = await _cached_compare(deps, run_id)
        if isinstance(found, CompareOut):
            return found
        venue, constraints = found
        out = await llm_only_shortlist(
            run_id,
            venue,
            constraints,
            model_factory=deps.model_factory,
            token_counter=deps.llm_counter,
            token_cap=deps.llm_cap,
        )
        if out.error is None:
            async with deps.sessions.begin() as session:
                row = await session.get(RunRow, run_id)
                assert row is not None
                row.compare = out.model_dump(mode="json", exclude={"cached"})
    if not lock.locked():
        _compare_locks.pop(run_id, None)
    return out


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
