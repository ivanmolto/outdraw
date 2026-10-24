"""Draw's own Strands tools: the booking pipeline as steps the agent can call.

`DrawTools(store, qloo)` binds the seven tools to one `RunStore` and one gateway. Each tool
reads and writes the store by id and returns a compact, JSON-serialisable summary (under
2 KB) plus the ids the next step needs, so no envelope, profile or candidate list ever
passes through the model. The tools are plain async methods (`await
tools.build_crowd_profile("ven_1")`); `tools()` wraps them with Strands' `@tool` for the
agent, which reads each docstring as the tool description.

The order the agent follows: `confirm_entity` (for each unconfirmed act) → `build_crowd_profile`
→ `generate_candidates` → `gather_signals` → `score_and_rank` → `get_evidence` / `draft_brief`.
Ranking is the deterministic function in `draw.ranking` (`score_and_rank` only calls it);
the model never orders candidates itself.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Sequence
from typing import Any, Final

from pydantic import ValidationError
from strands import tool
from strands.types.tools import AgentTool

from draw.agent.store import Run, RunStore, new_id
from draw.profile import crowd, resolve
from draw.qloo.exec_client import QlooExec
from draw.ranking import candidates as candidates_mod
from draw.ranking import signals as signals_mod
from draw.ranking.evidence import (
    TrailError,
    affinities_by_sub_score,
    assert_complete,
    build_trail,
)
from draw.ranking.rank import RankedCandidate, rank
from draw.ranking.score import WEIGHTS
from draw.schemas import Constraints, CrowdProfile, DataQuality, EvidenceTrail

__all__ = ["DrawTools", "TOOL_NAMES"]

TOOL_NAMES: Final = (
    "confirm_entity",
    "build_crowd_profile",
    "generate_candidates",
    "gather_signals",
    "score_and_rank",
    "get_evidence",
    "draft_brief",
)
SHORTLIST: Final = 8
REASONS: Final = 5
TOP_ITEMS: Final = 5
QUALITY_ORDER: Final[dict[DataQuality, int]] = {"ok": 0, "partial": 1, "degraded": 2}


def _worst(*qualities: DataQuality) -> DataQuality:
    return max(qualities, key=lambda q: QUALITY_ORDER[q]) if qualities else "ok"


def _names(items: Sequence[Any], n: int = TOP_ITEMS) -> list[str]:
    return [str(getattr(i, "name", i)) for i in items[:n]]


def _r(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


class DrawTools:
    """The seven tools, bound to a run store and a gateway (`qloo` is `QlooExec` or `QlooMCP`)."""

    def __init__(self, store: RunStore, qloo: QlooExec, *, today: dt.date | None = None) -> None:
        self.store = store
        self.qloo = qloo
        self.today = today
        if qloo.observer is None:
            qloo.observer = store.observe

    def tools(self) -> list[AgentTool]:
        """The seven methods as Strands tools for `Agent(tools=...)`, in pipeline order."""
        return [tool(getattr(self, name)) for name in TOOL_NAMES]

    # --- resolve → confirm → ask ------------------------------------------------------

    async def confirm_entity(self, resolved_id: str, chosen_entity_id: str) -> dict[str, Any]:
        """Record the user's choice for an act or venue that resolved to several candidates.

        Call this only after the user picked one of the candidates you showed them; never
        guess. Taste workflows must not run on an unconfirmed entity.

        Args:
            resolved_id: The id of the resolution (from the venue's resolved past acts).
            chosen_entity_id: The Qloo entity id the user chose among its candidates.

        Returns:
            {resolved_id, entity_id, canonical_name, confirmed}.
        """
        resolved = self.store.resolved_entity(resolved_id)
        confirmed = resolve.confirm_entity(resolved, chosen_entity_id)
        self.store.resolved[resolved_id] = confirmed
        return {
            "resolved_id": resolved_id,
            "query": confirmed.query,
            "entity_id": confirmed.entity_id,
            "canonical_name": confirmed.canonical_name,
            "confirmed": True,
        }

    # --- profile ----------------------------------------------------------------------

    async def build_crowd_profile(self, venue_id: str) -> dict[str, Any]:
        """Build the venue's crowd profile from its confirmed past acts (≤ 6 Qloo calls).

        Unconfirmed acts are skipped and listed in `notes`; a venue with no Qloo entity is
        profiled from its location and acts. Check `data_quality` and carry `notes` into
        your answer when it is not "ok".

        Args:
            venue_id: The venue's id in this run.

        Returns:
            {profile_id, venue_id, signals, genres, related_artists, places, data_quality,
            notes, unconfirmed}.
        """
        venue = self.store.venue(venue_id)
        acts = self.store.acts_for(venue_id)
        profile = await crowd.build_crowd_profile(
            venue,
            acts,
            qloo=self.qloo,
            venue_entity=self.store.venue_entities.get(venue_id),
            venue_id=venue_id,
        )
        profile_id = self.store.add_profile(venue_id, profile)
        return {
            "profile_id": profile_id,
            "venue_id": venue_id,
            "qloo_venue_id": profile.qloo_venue_id,
            "signals": len(profile.signals),
            "genres": _names(profile.genres),
            "related_artists": _names(profile.related_artists),
            "places": _names(profile.places, 3),
            "data_quality": profile.data_quality,
            "notes": list(profile.notes or []),
            "unconfirmed": [a.query for a in acts if not a.confirmed],
        }

    # --- candidates -------------------------------------------------------------------

    async def generate_candidates(self, profile_id: str, constraints_json: str) -> dict[str, Any]:
        """Pull acts from Qloo that match the crowd profile and the booking constraints.

        Starts a run. If `count` is 0, `reason` says what to change (usually broaden tags);
        broaden once, then report. `unresolved_tags` lists tag words the user must choose
        a candidate for before the run can go on.

        Args:
            profile_id: The crowd profile id from build_crowd_profile.
            constraints_json: JSON with include_tags, exclude_tags, size_tier
                ("emerging" | "mid" | "established"), exclude_recent_months, max_candidates.

        Returns:
            {run_id, count, candidates_quality, reason, notes, unresolved_tags, top}.
        """
        profile = self.store.profile(profile_id)
        venue_id = self.store.profile_venue[profile_id]
        venue = self.store.venue(venue_id)
        try:
            constraints = Constraints.model_validate_json(constraints_json)
        except ValidationError as exc:
            raise ValueError(f"constraints_json is not a valid Constraints object: {exc}") from None
        region = crowd.venue_region(venue)
        booked = candidates_mod.last_booked(venue, self.store.acts_for(venue_id))
        result = await candidates_mod.generate_candidates(
            profile, constraints, qloo=self.qloo, region=region, booked=booked, today=self.today
        )
        run = Run(
            run_id=new_id("run"),
            venue_id=venue_id,
            profile_id=profile_id,
            constraints=constraints,
            region=region,
            booked=booked,
            candidates=result,
        )
        self.store.add_run(run)
        return {
            "run_id": run.run_id,
            "count": len(result.candidates),
            "candidates_quality": result.candidates_quality,
            "reason": result.reason,
            "notes": result.notes,
            "unresolved_tags": [
                {"query": t.query, "candidates": [c.entity_id for c in t.candidates[:5]]}
                for t in result.unresolved_tags
            ],
            "top": _names(result.candidates),
        }

    async def gather_signals(self, run_id: str) -> dict[str, Any]:
        """Read past_fit, local_heat and momentum from Qloo for the run's top candidates.

        Costs up to 33 Qloo calls; candidates past the top N take the median of the
        measured candidates on these.
        Optional but recommended before score_and_rank.

        Args:
            run_id: The run id from generate_candidates.

        Returns:
            {run_id, candidates_with_signals, calls, notes}.
        """
        run = self.store.run(run_id)
        profile = self.store.profile(run.profile_id)
        signals = await signals_mod.gather_signals(
            run.candidates.candidates,
            profile,
            run.constraints,
            qloo=self.qloo,
            region=run.region,
            booked=run.booked,
            today=self.today,
        )
        run.signals = signals
        return {
            "run_id": run_id,
            "candidates_with_signals": len(signals.by_id),
            "calls": signals.calls,
            "notes": signals.notes,
        }

    # --- ranking ----------------------------------------------------------------------

    async def score_and_rank(self, run_id: str) -> dict[str, Any]:
        """Score every candidate with Outdraw's deterministic weights and sort them.

        This is the only way to order candidates: report the shortlist as returned.
        Each entry has a `candidate_id` for get_evidence and draft_brief.

        Args:
            run_id: The run id from generate_candidates.

        Returns:
            {run_id, ranked, data_quality, weights, shortlist: [{candidate_id, entity_id,
            name, total, reason?}]}.
        """
        run = self.store.run(run_id)
        profile = self.store.profile(run.profile_id)
        run.ranked = rank(run.candidates.candidates, profile, run.constraints, run.signals)
        self.store.assign_candidate_ids(run)
        self._store_trails(run, profile)
        shortlist = []
        for i, item in enumerate(run.ranked[:SHORTLIST]):
            entry: dict[str, Any] = {
                "candidate_id": run.candidate_ids[item.candidate.entity_id],
                "entity_id": item.candidate.entity_id,
                "name": item.candidate.name,
                "total": _r(item.score.total),
            }
            if i < REASONS:
                entry["reason"] = item.score.top_reason
            shortlist.append(entry)
        return {
            "run_id": run_id,
            "ranked": len(run.ranked),
            "signals_gathered": run.signals is not None,
            "data_quality": _worst(profile.data_quality, run.candidates.candidates_quality),
            "weights": dict(WEIGHTS),
            "shortlist": shortlist,
        }

    # --- evidence and brief -----------------------------------------------------------

    async def get_evidence(self, candidate_id: str) -> dict[str, Any]:
        """The evidence trail behind one candidate's score: what fed it, the Qloo calls, labels.

        `inputs` are what fed the score (crowd signals with their weights, region, size
        tier, popularity, booking history); `qloo_calls` the calls behind it with status and
        cache flag; `affinities` the values read from those calls per sub-score;
        `sub_scores`, `weights` and `total` the score itself. `source_labels` marks each
        sub-score and the total "qloo" (came out of a Qloo envelope) or "draw" (computed or
        templated by Outdraw, neutral fallbacks included); `neutral` lists the sub-scores
        Qloo gave no value for. This is the compact view of the stored EvidenceTrail
        (`evidence_trail`), which the API serves in full.

        Args:
            candidate_id: A candidate id from score_and_rank's shortlist.

        Returns:
            {candidate_id, run_id, entity_id, name, top_reason, neutral, inputs, qloo_calls,
            affinities, sub_scores, weights, total, source_labels}.
        """
        run, entity_id, item = self._ranked(candidate_id)
        trail = self.store.trail(candidate_id)
        return {
            "candidate_id": candidate_id,
            "run_id": run.run_id,
            "entity_id": entity_id,
            "name": item.candidate.name,
            "top_reason": item.score.top_reason,
            "neutral": sorted(item.score.neutral or []),
            "total": _r(trail.total),
            "sub_scores": {
                k: _r(v) for k, v in trail.sub_scores.model_dump(exclude_none=True).items()
            },
            "weights": trail.weights.model_dump(exclude_none=True),
            "source_labels": {
                k: v for k, v in trail.source_labels.items() if k in WEIGHTS or k == "total"
            },
            "inputs": [i.label for i in trail.inputs],
            "qloo_calls": [
                c.model_dump(mode="json", exclude={"params_summary"}) for c in trail.qloo_calls
            ],
            "affinities": [
                {"sub_score": name, "value": _r(a.value), "call_id": a.call_id}
                for name, a in affinities_by_sub_score(trail).items()
            ],
        }

    def evidence_trail(self, candidate_id: str) -> dict[str, Any]:
        """The full stored EvidenceTrail plus the candidate's ids (what the API stores)."""
        run, entity_id, item = self._ranked(candidate_id)
        trail = self.store.trail(candidate_id)
        return {
            "candidate_id": candidate_id,
            "run_id": run.run_id,
            "entity_id": entity_id,
            "name": item.candidate.name,
            "top_reason": item.score.top_reason,
            "neutral": sorted(item.score.neutral or []),
            **trail.model_dump(mode="json", exclude_none=True),
        }

    def _store_trails(self, run: Run, profile: CrowdProfile) -> None:
        """Build, validate and store the trail of every ranked candidate (no orphan numbers)."""
        names = self.store.signal_names(run.venue_id)
        for item in run.ranked or []:
            candidate_id = run.candidate_ids[item.candidate.entity_id]
            trail: EvidenceTrail = build_trail(
                item.candidate,
                item.score,
                profile=profile,
                constraints=run.constraints,
                region=run.region,
                signals=run.signals.get(item.candidate.entity_id) if run.signals else None,
                calls=self.store.calls,
                signal_names=names,
            )
            try:
                assert_complete(item.score, trail)
            except TrailError as exc:
                raise ValueError(
                    f"Evidence trail for {item.candidate.name!r} is incomplete: {exc}"
                ) from None
            run.trails[candidate_id] = trail

    async def draft_brief(self, candidate_id: str) -> dict[str, Any]:
        """Draft the one-page booking brief for a candidate (stub: templated reasons only).

        Returns the templated `top_reason` as the first fit reason; the night format,
        partners and outreach email arrive with the brief writer (T18).

        Args:
            candidate_id: A candidate id from score_and_rank's shortlist.

        Returns:
            {candidate_id, name, why_it_fits: [{text, evidence_ref}], night_format,
            support_act, partners, outreach_email, status}.
        """
        _run, entity_id, item = self._ranked(candidate_id)
        score = item.score
        evidence_ref = score.evidence[0].call_id if score.evidence else f"draw:{entity_id}"
        return {
            "candidate_id": candidate_id,
            "name": item.candidate.name,
            "why_it_fits": [{"text": score.top_reason, "evidence_ref": evidence_ref}],
            "night_format": None,
            "support_act": None,
            "partners": [],
            "outreach_email": None,
            "status": "stub",
        }

    def _ranked(self, candidate_id: str) -> tuple[Run, str, RankedCandidate]:
        run, entity_id = self.store.candidate(candidate_id)
        item = next((r for r in run.ranked or [] if r.candidate.entity_id == entity_id), None)
        if item is None:
            raise ValueError(f"Candidate {candidate_id!r} has no score; call score_and_rank first.")
        return run, entity_id, item


def dumps(payload: dict[str, Any]) -> str:
    """The JSON the model receives for a tool result (what the 2 KB bound is measured on)."""
    return json.dumps(payload, ensure_ascii=False)
