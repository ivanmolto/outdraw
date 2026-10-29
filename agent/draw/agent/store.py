"""`RunStore`: the per-run in-memory state behind Draw's tools, keyed by short ids.

Large payloads (venues, resolved entities, crowd profiles, candidate lists, signal sets,
rankings, the Qloo calls behind them) live here and never pass through the model: tools
take ids and return compact summaries plus ids. One store per agent run; nothing in it is
shared across runs or processes. Persistence (T14) stores the same objects by the same ids.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from draw.qloo.envelope import QlooResult
from draw.ranking.candidates import CandidateList
from draw.ranking.evidence import params_summary
from draw.ranking.rank import RankedCandidate
from draw.ranking.signals import SignalSet
from draw.schemas import (
    Constraints,
    CrowdProfile,
    EvidenceTrail,
    QlooCallRecord,
    QlooWorkflow,
    ResolvedEntity,
    VenueInput,
)

if TYPE_CHECKING:
    from draw.agent.brief import BriefOut

__all__ = ["Run", "RunStore", "new_id"]


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@dataclass
class Run:
    """One candidate run: constraints, the candidates, their signals and the ranking."""

    run_id: str
    venue_id: str
    profile_id: str
    constraints: Constraints
    region: str
    booked: dict[str, dt.date]
    candidates: CandidateList
    signals: SignalSet | None = None
    ranked: list[RankedCandidate] | None = None
    candidate_ids: dict[str, str] = field(default_factory=dict)
    """entity_id -> candidate_id, assigned by `score_and_rank`."""
    trails: dict[str, EvidenceTrail] = field(default_factory=dict)
    """candidate_id -> the evidence trail behind its score, built by `score_and_rank`."""
    briefs: dict[str, BriefOut] = field(default_factory=dict)
    """candidate_id -> its brief, cached by `draft_brief` (one model call per candidate and run)."""


@dataclass
class RunStore:
    venues: dict[str, VenueInput] = field(default_factory=dict)
    venue_entities: dict[str, ResolvedEntity] = field(default_factory=dict)
    resolved: dict[str, ResolvedEntity] = field(default_factory=dict)
    resolved_by_venue: dict[str, list[str]] = field(default_factory=dict)
    profiles: dict[str, CrowdProfile] = field(default_factory=dict)
    profile_venue: dict[str, str] = field(default_factory=dict)
    runs: dict[str, Run] = field(default_factory=dict)
    candidates: dict[str, tuple[str, str]] = field(default_factory=dict)
    """candidate_id -> (run_id, entity_id)."""
    calls: dict[str, QlooCallRecord] = field(default_factory=dict)
    """Every gateway call observed during the run, by call_id, in call order (evidence trail)."""
    results: dict[str, QlooResult] = field(default_factory=dict)
    """The observed results themselves (request shape, summary, counts) for the walkthrough.
    They never leave the process: the model sees `QlooCallRecord`s, the API the recordings."""
    requested_constraints: Constraints | None = None
    """The booker's constraints as the request carried them. `generate_candidates` holds the
    model to them: the size tier and the candidate count are fixed, tags and the recency
    window may only be read from the message or broadened, and every change is noted."""

    # --- seeding (done by the API layer before the agent runs) -------------------------

    def add_venue(self, venue: VenueInput, *, venue_id: str | None = None) -> str:
        venue_id = venue_id or new_id("ven")
        self.venues[venue_id] = venue
        self.resolved_by_venue.setdefault(venue_id, [])
        return venue_id

    def set_venue_entity(self, venue_id: str, resolved: ResolvedEntity) -> None:
        self.venue(venue_id)
        self.venue_entities[venue_id] = resolved

    def add_resolved(self, venue_id: str, resolved: ResolvedEntity) -> str:
        """Register a past act's resolution (T07) for `venue_id`; returns its resolved_id."""
        self.venue(venue_id)
        resolved_id = new_id("res")
        self.resolved[resolved_id] = resolved
        self.resolved_by_venue.setdefault(venue_id, []).append(resolved_id)
        return resolved_id

    # --- lookups (raise ValueError so a tool call reports a readable error) -----------

    def venue(self, venue_id: str) -> VenueInput:
        return _get(self.venues, venue_id, "venue")

    def resolved_entity(self, resolved_id: str) -> ResolvedEntity:
        return _get(self.resolved, resolved_id, "resolved entity")

    def acts_for(self, venue_id: str) -> list[ResolvedEntity]:
        return [self.resolved[r] for r in self.resolved_by_venue.get(venue_id, [])]

    def profile(self, profile_id: str) -> CrowdProfile:
        return _get(self.profiles, profile_id, "profile")

    def run(self, run_id: str) -> Run:
        return _get(self.runs, run_id, "run")

    def candidate(self, candidate_id: str) -> tuple[Run, str]:
        run_id, entity_id = _get(self.candidates, candidate_id, "candidate")
        return self.run(run_id), entity_id

    def trail(self, candidate_id: str) -> EvidenceTrail:
        """The stored evidence trail for a scored candidate (`score_and_rank` stores one each)."""
        run, _ = self.candidate(candidate_id)
        return _get(run.trails, candidate_id, "evidence trail for candidate")

    def signal_names(self, venue_id: str) -> dict[str, str]:
        """Canonical act name by Qloo entity ID, for the trail's input labels."""
        return {
            act.entity_id: act.canonical_name
            for act in self.acts_for(venue_id)
            if act.entity_id and act.confirmed
        }

    # --- writes ---------------------------------------------------------------------

    def add_profile(self, venue_id: str, profile: CrowdProfile) -> str:
        profile_id = new_id("prof")
        self.profiles[profile_id] = profile
        self.profile_venue[profile_id] = venue_id
        return profile_id

    def add_run(self, run: Run) -> str:
        self.runs[run.run_id] = run
        return run.run_id

    def assign_candidate_ids(self, run: Run) -> None:
        for item in run.ranked or []:
            entity_id = item.candidate.entity_id
            if entity_id not in run.candidate_ids:
                candidate_id = new_id("cand")
                run.candidate_ids[entity_id] = candidate_id
                self.candidates[candidate_id] = (run.run_id, entity_id)

    def observe(self, result: QlooResult) -> None:
        """`QlooExec(observer=store.observe)`: keep one record per gateway call."""
        workflow: QlooWorkflow = result.workflow  # type: ignore[assignment]
        self.calls[result.call_id] = QlooCallRecord(
            call_id=result.call_id,
            workflow=workflow,
            params_summary=params_summary(result.workflow, result.request),
            status=result.status,
            cache_hit=result.cache_hit,
        )
        self.results[result.call_id] = result


def _get[T](table: dict[str, T], key: str, kind: str) -> T:
    try:
        return table[key]
    except KeyError:
        raise ValueError(f"Unknown {kind} id {key!r}.") from None
