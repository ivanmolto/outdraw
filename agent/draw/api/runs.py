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

`GET /runs/{id}/candidates/{entity_id}/brief` (T27) writes the candidate's booking brief
from the stored run (`draw.agent.brief`): one Qloo call for the support act through a
scripted gateway under the run's id, one model call (plus one retry), then the brief is
cached on the candidate row and served from there with `cached: true`.

`PATCH /runs/{id}/candidates/{entity_id} {pencilled, date?}` (T28) sets the candidate's pencil
hold and, optionally, the night it holds (T44; releasing clears it); the evidence route reads
it back, so the Soundcheck panel opens with it. A run read back carries each entry's hold, and
the genre names and size band from its stored evidence, so the shortlist filters and the
holds calendar need no call per act.

Seed runs (T29, `is_seed` with a `seed_slug`, written by `scripts/precompute.py`) are
read-only: their GETs carry an `ETag` and answer `304` to a matching `If-None-Match`
(`draw.api.etag`), the pencil PATCH is refused with 403, and a compare or brief that was
not precomputed is refused with 409 rather than spending a call. A refine of a seed run is
allowed: it is a new live run chained to the seed by `parent_run_id`.

`POST /runs` goes through the semantic run cache first (T36, `draw.embed.semantic_cache`):
a request that reads like an earlier ok run of the same venue is answered with a copy of
that run (result, candidates, cached brief and compare) marked `cache_source="semantic"`
and `cached_from`, spending no Qloo call and no token. Each new ok run stores its request
embedding. Refined runs skip the cache: their hints steer the agent outside the constraints.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from draw.agent.agent import AgentResult, run_venue_agent
from draw.agent.brief import BriefAct, BriefContext, BriefOut, draft_brief
from draw.agent.compare import CompareOut, llm_only_shortlist
from draw.agent.refine import ConstraintsDelta, explain, merge, parse_delta, refine_request
from draw.agent.store import new_id
from draw.agent.tools import DrawTools
from draw.api.auth import require_internal_key
from draw.api.deps import Deps, get_deps
from draw.api.etag import conditional
from draw.api.inputs import INVALID_INPUT, RefineBody, RunBody
from draw.api.live import live_call, live_run
from draw.api.venues import load_resolutions, load_venue
from draw.db.models import CandidateRow, CrowdProfileRow, QlooCallRow, RunRow
from draw.embed import semantic_cache, tag_map
from draw.observability import RUN_ID_HEADER, bind_run_id, is_new_run_id
from draw.qloo.exec_client import QlooExec
from draw.ranking.score import WEIGHTS, SourceLabel, SubScore, band_of, source_labels
from draw.schemas import (
    Constraints,
    CrowdProfile,
    DataQuality,
    EvidenceTrail,
    ResolvedEntity,
    ScoreBreakdown,
    ScoreWeights,
    SizeTier,
    VenueInput,
)

__all__ = [
    "RunOut",
    "RunResult",
    "ShortlistEntry",
    "brief_for",
    "compare_for",
    "execute_run",
    "router",
    "read_run",
    "run_out",
    "venue_for_run",
]

log = logging.getLogger("draw.runs")

SEED_READ_ONLY = "seed_read_only"
SEED_NOT_PRECOMPUTED = "seed_not_precomputed"

router = APIRouter(
    prefix="/runs", dependencies=[Depends(require_internal_key)], responses=INVALID_INPUT
)
DepsDep = Annotated[Deps, Depends(get_deps)]


class Refinement(BaseModel):
    """How a refined run came to be: the booker's message, the delta read from it (as
    applied) and the explanation of what changed against the parent run."""

    message: str
    delta: ConstraintsDelta
    explanation: str
    token_usage: dict[str, int] = Field(default_factory=dict)
    """The parse call's tokens; the run's own are on `RunOut.token_usage`."""


class PencilHold(BaseModel):
    date: dt.date | None = None
    """The night held; None while the hold has no date."""


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
    genres: list[str] = Field(default_factory=list)
    """The act's genre tag names from its past_fit call (Qloo data); empty without one."""
    size_band: SizeTier | None = None
    """The size tier its Qloo popularity sits in (`band_of`), for the capacity filter."""
    pencil_hold: PencilHold | None = None
    """The booker's pencil hold on the act; None when it has none."""


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
    cache_source: Literal["semantic"] | None = None
    """`semantic`: copied from `cached_from`, whose request read the same (T36)."""
    cached_from: str | None = None


class RunOut(BaseModel):
    run_id: str
    venue_id: str
    status: str
    constraints: Constraints
    result: RunResult | None
    token_usage: dict[str, Any] | None
    created_at: dt.datetime | None = None
    parent_run_id: str | None = None
    """The run this one refined (`POST /runs/{id}/refine`); None for a first run."""
    is_seed: bool = False
    """A precomputed demo run (T29): read-only, served with an ETag."""
    seed_slug: str | None = None


class Evidence(BaseModel):
    run_id: str
    entity_id: str
    score: dict[str, Any]
    evidence: dict[str, Any]
    pencilled: bool = False
    pencil_date: dt.date | None = None


class CandidatePatch(BaseModel):
    pencilled: bool
    date: dt.date | None = None
    """The night to hold; ignored (and cleared) on a release."""


class CandidateOut(BaseModel):
    run_id: str
    entity_id: str
    pencilled: bool
    date: dt.date | None = None


class QlooCall(BaseModel):
    call_id: str
    workflow: str
    status: str
    cache_hit: bool
    request: dict[str, Any]
    envelope: dict[str, Any]
    created_at: dt.datetime


def run_out(row: RunRow, candidates: Sequence[CandidateRow] = ()) -> RunOut:
    """A stored run as the API serves it, each entry with what its candidate row holds
    (`_with_candidates`); also used by the seed page (`draw.api.seeds`)."""
    result = RunResult.model_validate(row.result) if row.result is not None else None
    return RunOut(
        run_id=row.id,
        venue_id=row.venue_id,
        status=row.status,
        constraints=Constraints.model_validate(row.constraints),
        result=_with_candidates(result, candidates) if result is not None else None,
        token_usage=row.token_usage,
        created_at=row.created_at,
        parent_run_id=row.parent_run_id,
        is_seed=row.is_seed,
        seed_slug=row.seed_slug,
    )


async def _is_seed(deps: Deps, run_id: str) -> bool:
    async with deps.sessions() as session:
        row = await session.get(RunRow, run_id)
    return bool(row is not None and row.is_seed)


def _seed_refusal(code: int, reason: str, message: str, slug: str | None) -> HTTPException:
    return HTTPException(code, {"reason": reason, "message": message, "seed_slug": slug})


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


def _with_candidates(result: RunResult, candidates: Sequence[CandidateRow]) -> RunResult:
    """Each entry with its pencil hold, and its genre names and size band from the stored
    evidence. Read at serve time: the hold changes after the run is stored, and runs
    stored before the genres were kept still read (with none)."""
    rows = {r.entity_id: r for r in candidates}
    shortlist = []
    for entry in result.shortlist:
        row = rows.get(entry.entity_id)
        if row is not None:
            genres = row.evidence.get("genres")
            popularity = row.evidence.get("popularity")
            entry = entry.model_copy(
                update={
                    "genres": [g for g in genres if isinstance(g, str)]
                    if isinstance(genres, list)
                    else [],
                    "size_band": band_of(float(popularity))
                    if isinstance(popularity, int | float) and not isinstance(popularity, bool)
                    else None,
                    "pencil_hold": PencilHold(date=row.pencil_date) if row.pencilled else None,
                }
            )
        shortlist.append(entry)
    return result.model_copy(update={"shortlist": shortlist})


async def read_run(session: AsyncSession, run_id: str) -> RunOut | None:
    """The stored run with its candidates' holds, or None for an unknown run."""
    row = await session.get(RunRow, run_id)
    if row is None:
        return None
    rows = (await session.scalars(select(CandidateRow).where(CandidateRow.run_id == run_id))).all()
    return run_out(row, rows)


@dataclass(frozen=True)
class _Venue:
    """What a run needs about its venue, as stored: the input, its resolutions, its profile."""

    venue_id: str
    venue: VenueInput
    acts: Sequence[ResolvedEntity]
    venue_entity: ResolvedEntity | None
    profile: CrowdProfile


async def venue_for_run(session: AsyncSession, venue_id: str) -> _Venue:
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


async def execute_run(
    deps: Deps,
    run_id: str,
    target: _Venue,
    constraints: Constraints,
    message: str,
    *,
    parent_run_id: str | None = None,
    finish: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    semantic: bool = False,
) -> RunOut:
    """Store the run as running, run the agent, store the result (or the error) and read it
    back. `finish` sees the stored result before it is written (the refinement adds to it).
    With `semantic`, a semantic cache hit is copied instead and an ok run stores its request
    embedding. The precompute script calls this directly with its deterministic seed run ids."""
    with bind_run_id(run_id):
        return await _execute_run(
            deps,
            run_id,
            target,
            constraints,
            message,
            parent_run_id=parent_run_id,
            finish=finish,
            semantic=semantic,
        )


async def _execute_run(
    deps: Deps,
    run_id: str,
    target: _Venue,
    constraints: Constraints,
    message: str,
    *,
    parent_run_id: str | None,
    finish: Callable[[dict[str, Any]], dict[str, Any]] | None,
    semantic: bool,
) -> RunOut:
    vector = await _request_vector(deps, target, constraints) if semantic else None
    if vector is not None:
        async with deps.sessions() as session:
            hit = await semantic_cache.lookup(session, vector, target.venue_id, constraints)
        if hit is not None:
            log.info("run_done source=semantic cached_from=%s", hit.run_id)
            return await _reuse(deps, run_id, hit, constraints)

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
            _log_run(result, qloo)
    except Exception as exc:
        log.warning("run_failed error=%s", type(exc).__name__)
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
        if vector is not None and result.status == "ok":
            semantic_cache.store(session, run_id, vector)
    async with deps.sessions() as session:
        out = await read_run(session, run_id)
        assert out is not None
        return out


def _log_run(result: AgentResult, qloo: QlooExec) -> None:
    """The run's summary line: steps, tokens and Qloo calls (cache hits apart)."""
    usage = result.usage
    log.info(
        "run_done status=%s stop=%s steps=%d model_calls=%d input_tokens=%d output_tokens=%d "
        "cache_read_tokens=%d qloo_calls=%d qloo_cache_hits=%d",
        result.status,
        result.stop_reason,
        result.steps,
        usage.get("model_calls", 0),
        usage.get("input_tokens", 0),
        usage.get("output_tokens", 0),
        usage.get("cache_read_tokens", 0),
        qloo.calls,
        qloo.cache_hits,
        extra={
            "status": result.status,
            "steps": result.steps,
            "usage": usage,
            "qloo_calls": qloo.calls,
            "qloo_cache_hits": qloo.cache_hits,
        },
    )


async def _request_vector(
    deps: Deps, target: _Venue, constraints: Constraints
) -> list[float] | None:
    """The request's embedding, or None when the cache is off or the embedder failed (the
    run then goes ahead uncached: the cache must never fail a run)."""
    if deps.embedder is None:
        return None
    try:
        return await deps.embedder.embed(semantic_cache.canonical_text(target.venue, constraints))
    except Exception:
        log.warning("semantic cache: embedding failed, running uncached", exc_info=True)
        return None


async def _reuse(
    deps: Deps, run_id: str, hit: semantic_cache.Hit, constraints: Constraints
) -> RunOut:
    """A new run copied from the hit: its result, candidates and cached brief and compare
    (re-addressed to the new run), with no tokens counted and pencil holds left behind."""
    async with deps.sessions.begin() as session:
        source = await session.get(RunRow, hit.run_id)
        assert source is not None and source.result is not None
        rows = (
            await session.scalars(select(CandidateRow).where(CandidateRow.run_id == source.id))
        ).all()
        session.add(
            RunRow(
                id=run_id,
                venue_id=source.venue_id,
                constraints=constraints.model_dump(mode="json"),
                status=source.status,
                result={**source.result, "cache_source": "semantic", "cached_from": source.id},
                token_usage={key: 0 for key in source.token_usage or {}},
                compare={**source.compare, "run_id": run_id} if source.compare else None,
            )
        )
        await session.flush()
        session.add_all(
            CandidateRow(
                run_id=run_id,
                entity_id=r.entity_id,
                score=r.score,
                evidence=r.evidence,
                brief={**r.brief, "run_id": run_id} if r.brief else None,
            )
            for r in rows
        )
    async with deps.sessions() as session:
        out = await read_run(session, run_id)
        assert out is not None
        return out


async def _new_run_id(deps: Deps, request: Request, response: Response) -> str:
    """The web's `X-Draw-Run-Id` when it is a fresh run id, so both services log the run
    under one id; a new one when it is missing, malformed or taken. The response's
    `X-Draw-Run-Id` names the run either way."""
    wanted = request.headers.get(RUN_ID_HEADER)
    run_id = new_id("run")
    if is_new_run_id(wanted):
        assert wanted is not None
        async with deps.sessions() as session:
            if await session.get(RunRow, wanted) is None:
                run_id = wanted
    response.headers[RUN_ID_HEADER] = run_id
    return run_id


@router.post("")
async def create_run(body: RunBody, request: Request, response: Response, deps: DepsDep) -> RunOut:
    """One agent run. 503 `live_mode_off` with `LIVE_MODE=off` and 429 `budget` at either
    daily cap, both before anything is stored and both naming `fallback_seed`."""
    async with live_run(deps, venue_id=body.venue_id):
        async with deps.sessions() as session:
            target = await venue_for_run(session, body.venue_id)
        return await execute_run(
            deps,
            await _new_run_id(deps, request, response),
            target,
            body.constraints,
            body.message,
            semantic=True,
        )


def _shortlist_names(result: dict[str, Any] | None) -> list[str]:
    return [str(e.get("name", "")) for e in (result or {}).get("shortlist") or []]


@router.post(
    "/{run_id}/refine", responses={422: {"description": "No change can be read from the message."}}
)
async def refine_run(
    run_id: str, body: RefineBody, request: Request, response: Response, deps: DepsDep
) -> RunOut:
    """A new run under the parent's constraints changed as the message asks.

    404 for an unknown parent; 422 when no change can be read from the message (nothing is
    spent on Qloo then); 429 at either daily cap. The parse is one model call, the re-run a
    normal agent run with every cap of `POST /runs`, and refused like one with `LIVE_MODE=off`.
    """
    async with live_run(deps, run_id=run_id):
        return await _refine(run_id, body, deps, await _new_run_id(deps, request, response))


async def _refine(run_id: str, body: RefineBody, deps: Deps, new_run_id: str) -> RunOut:
    async with deps.sessions() as session:
        parent = await session.get(RunRow, run_id)
        if parent is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown run {run_id!r}.")
        target = await venue_for_run(session, parent.venue_id)
    previous = Constraints.model_validate(parent.constraints)
    before = _shortlist_names(parent.result)

    parsed = await parse_delta(
        body.message,
        previous,
        before,
        model_factory=deps.model_factory,
        token_counter=deps.llm_counter,
        token_cap=deps.llm_cap,
        tag_hints=await _tag_hints(deps, body.message),
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

    return await execute_run(
        deps,
        new_run_id,
        target,
        merged,
        refine_request(body.message, delta, before),
        parent_run_id=run_id,
        finish=finish,
    )


async def _tag_hints(deps: Deps, message: str) -> list[tag_map.PhraseTags]:
    """The nearest Qloo tags for the message's phrases (T37), or none when embeddings are off
    or the lookup failed (the parser then works from the booker's words alone)."""
    if deps.embedder is None:
        return []
    try:
        async with deps.sessions() as session:
            return await tag_map.map_phrases(session, deps.embedder, message)
    except Exception:
        log.warning("tag map: lookup failed, parsing without tag hints", exc_info=True)
        return []


@router.get("/{run_id}")
async def get_run(run_id: str, request: Request, response: Response, deps: DepsDep) -> RunOut:
    async with deps.sessions() as session:
        out = await read_run(session, run_id)
    if out is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown run {run_id!r}.")
    conditional(request, response, out, seed=out.is_seed)
    return out


@router.get("/{run_id}/candidates/{entity_id}/evidence")
async def get_evidence(
    run_id: str, entity_id: str, request: Request, response: Response, deps: DepsDep
) -> Evidence:
    async with deps.sessions() as session:
        row = await session.get(CandidateRow, (run_id, entity_id))
        run = await session.get(RunRow, run_id)
    if row is None or run is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"No candidate {entity_id!r} in run {run_id!r}."
        )
    out = Evidence(
        run_id=run_id,
        entity_id=entity_id,
        score=row.score,
        evidence=row.evidence,
        pencilled=row.pencilled,
        pencil_date=row.pencil_date if row.pencilled else None,
    )
    conditional(request, response, out, seed=run.is_seed)
    return out


@router.patch("/{run_id}/candidates/{entity_id}")
async def patch_candidate(
    run_id: str, entity_id: str, body: CandidatePatch, deps: DepsDep
) -> CandidateOut:
    """Pencil (or release) a hold on the candidate, for a night when `date` is given;
    idempotent. A release clears the date. 404 when unknown, 403 on a seed run (read-only)."""
    async with deps.sessions.begin() as session:
        row = await session.get(CandidateRow, (run_id, entity_id))
        if row is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, f"No candidate {entity_id!r} in run {run_id!r}."
            )
        run = await session.get(RunRow, run_id)
        if run is not None and run.is_seed:
            raise _seed_refusal(
                status.HTTP_403_FORBIDDEN,
                SEED_READ_ONLY,
                "Seed runs are read-only; refine the run to pencil acts on a live copy.",
                run.seed_slug,
            )
        row.pencilled = body.pencilled
        row.pencil_date = body.date if body.pencilled else None
    return CandidateOut(
        run_id=run_id,
        entity_id=entity_id,
        pencilled=body.pencilled,
        date=row.pencil_date,
    )


_compare_locks: dict[str, asyncio.Lock] = {}
"""One lock per run around its first compare call, so concurrent requests spend one call
(the service runs as one process; a second request waits, then reads the cached copy)."""


async def _cached_compare(deps: Deps, run_id: str) -> CompareOut | tuple[VenueInput, Constraints]:
    """The run's cached compare, or what the first call needs; 404 for an unknown run, 409
    for a seed run whose compare was not precomputed (nothing is spent on a seed)."""
    async with deps.sessions() as session:
        row = await session.get(RunRow, run_id)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown run {run_id!r}.")
        if row.compare is not None:
            return CompareOut.model_validate({**row.compare, "cached": True})
        if row.is_seed:
            raise _seed_refusal(
                status.HTTP_409_CONFLICT,
                SEED_NOT_PRECOMPUTED,
                "This seed run has no precomputed comparison; start a live run for one.",
                row.seed_slug,
            )
        _, venue = await load_venue(session, row.venue_id)
        return venue, Constraints.model_validate(row.constraints)


async def compare_for(deps: Deps, run_id: str) -> CompareOut:
    """The run's LLM-only shortlist, computed once and cached on the run."""
    found = await _cached_compare(deps, run_id)
    if isinstance(found, CompareOut):
        return found
    # Around the lock, so a refusal leaves no lock behind.
    async with live_call(deps, run_id=run_id):
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


@router.get("/{run_id}/compare")
async def get_compare(
    run_id: str, request: Request, response: Response, deps: DepsDep
) -> CompareOut:
    """The LLM-only shortlist for the run's venue and constraints: one tool-less Haiku 5.5
    call the first time, then the copy cached on the run (no tokens spent)."""
    out = await compare_for(deps, run_id)
    conditional(request, response, out, seed=await _is_seed(deps, run_id))
    return out


_brief_locks: dict[tuple[str, str], asyncio.Lock] = {}
"""One lock per candidate around its first brief, as `_compare_locks` for the compare call."""


def _brief_act(row: CandidateRow) -> BriefAct:
    """A stored candidate as the brief sees it: name and popularity from its evidence."""
    evidence = row.evidence
    popularity = evidence.get("popularity")
    return BriefAct(
        entity_id=row.entity_id,
        name=str(evidence.get("name") or row.entity_id),
        popularity=float(popularity) if isinstance(popularity, int | float) else 0.0,
        total=float(row.score.get("total") or 0.0),
    )


async def _cached_brief(
    deps: Deps, run_id: str, entity_id: str
) -> BriefOut | tuple[CandidateRow, BriefContext]:
    """The candidate's cached brief, or the context for the first call; 404 when unknown."""
    async with deps.sessions() as session:
        run = await session.get(RunRow, run_id)
        if run is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown run {run_id!r}.")
        row = await session.get(CandidateRow, (run_id, entity_id))
        if row is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, f"No candidate {entity_id!r} in run {run_id!r}."
            )
        if row.brief is not None:
            return BriefOut.model_validate({**row.brief, "cached": True})
        if run.is_seed:
            raise _seed_refusal(
                status.HTTP_409_CONFLICT,
                SEED_NOT_PRECOMPUTED,
                "This brief was not precomputed for the seed run; refine it for a live copy.",
                run.seed_slug,
            )
        _, venue = await load_venue(session, run.venue_id)
        profile_row = await session.get(CrowdProfileRow, run.venue_id)
        if profile_row is None:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "The run's venue has no crowd profile any more."
            )
        rows = (
            await session.scalars(select(CandidateRow).where(CandidateRow.run_id == run_id))
        ).all()
    ranked = sorted((_brief_act(r) for r in rows), key=lambda a: -a.total)
    act = next(a for a in ranked if a.entity_id == entity_id)
    trail_fields = {k: v for k, v in row.evidence.items() if k in EvidenceTrail.model_fields}
    ctx = BriefContext(
        run_id=run_id,
        venue=venue,
        act=act,
        top_reason=str(row.score.get("top_reason") or ""),
        trail=EvidenceTrail.model_validate(trail_fields),
        profile=CrowdProfile.model_validate(profile_row.profile),
        constraints=Constraints.model_validate(run.constraints),
        ranked=ranked,
    )
    return row, ctx


async def brief_for(deps: Deps, run_id: str, entity_id: str) -> BriefOut:
    """The candidate's brief, written once and cached on the candidate row."""
    found = await _cached_brief(deps, run_id, entity_id)
    if isinstance(found, BriefOut):
        return found
    # Around the lock, so a refusal leaves no lock behind.
    async with live_call(deps, run_id=run_id):
        lock = _brief_locks.setdefault((run_id, entity_id), asyncio.Lock())
        async with lock:
            found = await _cached_brief(deps, run_id, entity_id)
            if isinstance(found, BriefOut):
                return found
            _, ctx = found
            out = await draft_brief(
                ctx,
                qloo=deps.gateways.scripted(run_id),
                model_factory=deps.model_factory,
                token_counter=deps.llm_counter,
                token_cap=deps.llm_cap,
            )
            if out.error is None:
                async with deps.sessions.begin() as session:
                    row = await session.get(CandidateRow, (run_id, entity_id))
                    assert row is not None
                    row.brief = out.model_dump(mode="json", exclude={"cached"})
    if not lock.locked():
        _brief_locks.pop((run_id, entity_id), None)
    return out


@router.get("/{run_id}/candidates/{entity_id}/brief")
async def get_brief(
    run_id: str, entity_id: str, request: Request, response: Response, deps: DepsDep
) -> BriefOut:
    """The candidate's booking brief: one Qloo call and one model call (one retry) the first
    time, then the copy cached on the candidate. Every `why_it_fits` ref exists in `evidence`;
    a brief with `error` set is Outdraw's template and is not cached. 429 at the token cap."""
    out = await brief_for(deps, run_id, entity_id)
    conditional(request, response, out, seed=await _is_seed(deps, run_id))
    return out


class QlooCalls(BaseModel):
    """The list body, so a seed run's calls can carry an ETag like its other reads."""

    calls: list[QlooCall]


@router.get("/{run_id}/calls")
async def get_calls(
    run_id: str, request: Request, response: Response, deps: DepsDep
) -> list[QlooCall]:
    """The run's Qloo calls in order, as recorded (the API key already redacted)."""
    async with deps.sessions() as session:
        run = await session.get(RunRow, run_id)
        if run is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown run {run_id!r}.")
        rows = await session.scalars(
            select(QlooCallRow)
            .where(QlooCallRow.run_id == run_id)
            .order_by(QlooCallRow.created_at, QlooCallRow.call_id)
        )
        calls = [
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
    conditional(request, response, QlooCalls(calls=calls), seed=run.is_seed)
    return calls
