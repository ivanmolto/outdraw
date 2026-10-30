#!/usr/bin/env python3
"""Export a run's Qloo calls as a redacted markdown walkthrough (T22).

For one run, the ordered Qloo calls become ``docs/walkthrough-<slug>.md``: per call the
request shape (keys, list lengths, Draw's own scalars; entity and tag IDs as ``<id>``), the
envelope summary (status, result count, the harness's summary line, cache flag) and how it
fed the score (which crowd-profile fields it filled, or which sub-score of which acts it
produced), then every shortlisted act's score with its source labels and the result of the
completeness validator (``draw.ranking.evidence.validate_trail``).

Two sources:

- ``--fixture nyc-jazz`` (default): runs the whole pipeline offline on the synthetic fixtures
  in ``tests/fixtures/qloo`` (a New York jazz room with three past acts, a ``jazz`` include
  tag), so the exported file holds no Qloo data at all and can live in the public repo.
- ``--run-id <id>``: reads a stored run from Postgres (``DATABASE_URL``: ``runs``,
  ``venues``, ``crowd_profiles``, ``candidates`` and the recorded ``qloo_calls``). Its
  export carries Qloo's real summaries, names and numbers: Qloo's terms keep that out of the
  public repo, so share such a file privately (the judges' copy) and never commit it.

Redaction: the Qloo key (``QLOO_API_KEY``, if set), every UUID and every 32+ character
token are replaced before writing; the file never carries personal data because no input
of the pipeline does (entity IDs, tags, regions, dates, the venue's own name and shows).

Usage (from ``/agent``)::

    uv run python scripts/walkthrough.py                       # docs/walkthrough-nyc-jazz.md
    uv run python scripts/walkthrough.py --run-id run_ab12cd34 --slug mercury-lounge
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import re
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from draw.agent.store import RunStore  # noqa: E402
from draw.agent.tools import DrawTools  # noqa: E402
from draw.profile.resolve import resolve_entity  # noqa: E402
from draw.qloo.config import QlooConfig  # noqa: E402
from draw.qloo.envelope import QlooEnvelope, normalise  # noqa: E402
from draw.qloo.exec_client import QlooExec  # noqa: E402
from draw.ranking.evidence import (  # noqa: E402
    affinities_by_sub_score,
    params_summary,
    validate_trail,
)
from draw.ranking.score import WEIGHTS, WORKFLOW_OF  # noqa: E402
from draw.schemas import (  # noqa: E402
    Constraints,
    EvidenceTrail,
    PastShow,
    Score,
    VenueInput,
)

AGENT_DIR = Path(__file__).resolve().parents[1]
FIXTURES_DIR = AGENT_DIR / "tests" / "fixtures" / "qloo"
DOCS_DIR = AGENT_DIR.parent / "docs"
TODAY = dt.date(2026, 10, 24)
"""Fixed so the fixture walkthrough is reproducible (trend windows, months since booked)."""

UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9]{32,}(?![A-Za-z0-9])")
"""A contiguous 32+ character token: the shape of an API key. UUIDs have dashes and are
replaced separately; entity names and words are far shorter."""
SLUG_RE = re.compile(r"[^a-z0-9]+")
MAX_ACTS_PER_CALL = 6

SUB_SCORE_OF_WORKFLOW: dict[str, str] = {
    workflow: name for name, workflow in WORKFLOW_OF.items() if workflow
}
PROFILE_WORKFLOWS = {"recommend", "audience_demographics"}
RESOLVE_WORKFLOWS = {"describe", "find_tags"}


# --- what a walkthrough is made of -----------------------------------------------------


@dataclass
class CallStep:
    """One Qloo call as the walkthrough shows it: shape and summary, never the payload."""

    call_id: str
    workflow: str
    status: str
    cache_hit: bool
    request: dict[str, Any]
    summary: str | None
    result_count: int | None


@dataclass
class ScoredAct:
    name: str
    entity_id: str
    score: Score
    trail: EvidenceTrail


@dataclass
class RunExport:
    slug: str
    source: str
    """Where the run came from, for the file's header: the fixture set or a run id."""
    venue: str
    constraints: Constraints
    calls: list[CallStep]
    profile_items: dict[str, list[str]]
    """call_id -> the crowd-profile item refs it filled (`genres[0]`, `demographics`)."""
    acts: list[ScoredAct]
    """Ranked order, best first."""
    notes: list[str] = field(default_factory=list)


# --- redaction -------------------------------------------------------------------------


def redact(text: str, secret: str | None = None) -> str:
    """Strip the Qloo key, every UUID and every key-shaped token from `text`."""
    if secret and len(secret) >= 8:
        text = text.replace(secret, "<redacted>")
    text = UUID_RE.sub("<id>", text)
    return TOKEN_RE.sub("<redacted>", text)


def slugify(text: str) -> str:
    return SLUG_RE.sub("-", text.casefold()).strip("-") or "run"


# --- rendering -------------------------------------------------------------------------


def _pct(value: float | None) -> str:
    return "–" if value is None else f"{value:.3f}"


def _fed(
    step: CallStep,
    acts: Iterable[ScoredAct],
    profile_items: Mapping[str, list[str]],
) -> str:
    """One sentence on how this call fed the score, from the trails and the profile."""
    by_sub: dict[str, list[str]] = defaultdict(list)
    for act in acts:
        if not any(c.call_id == step.call_id for c in act.trail.qloo_calls):
            continue
        for affinity in act.trail.affinities:
            if affinity.call_id == step.call_id:
                name = SUB_SCORE_OF_WORKFLOW.get(step.workflow, step.workflow)
                by_sub[name].append(f"{act.name} {affinity.value:.3f}")
    if by_sub:
        parts = []
        for name, values in by_sub.items():
            shown = ", ".join(values[:MAX_ACTS_PER_CALL])
            extra = len(values) - MAX_ACTS_PER_CALL
            more = f" and {extra} more" if extra > 0 else ""
            weight = WEIGHTS.get(name, 0.0)  # type: ignore[call-overload]
            parts.append(f"`{name}` (weight {weight:.2f}) for {shown}{more}")
        return "Fed " + "; ".join(parts) + "."
    items = profile_items.get(step.call_id)
    if items:
        fields = sorted({ref.split("[")[0] for ref in items})
        return (
            f"Filled the crowd profile's {', '.join(f'`{f}`' for f in fields)} "
            f"({len(items)} item{'s' if len(items) != 1 else ''}); the profile's signals and "
            "region then shaped every later call."
        )
    if step.workflow in RESOLVE_WORKFLOWS:
        return (
            "Resolution step (resolve → confirm → ask): turned a typed word into a Qloo "
            "entity or tag. No score number comes from this call."
        )
    if step.workflow == "recommend" and step.request.get("target_type") == "artist":
        return (
            "Candidate pool: every act it returned was already credited to an earlier "
            "`recommend` call (an act's `crowd_affinity` points at the first call that "
            "returned it)."
        )
    if step.status != "ok":
        return (
            f"Status `{step.status}`: no value was read, so the sub-score fell back to the run's "
            "median and is labelled `draw`."
        )
    sub = SUB_SCORE_OF_WORKFLOW.get(step.workflow)
    if sub:
        return (
            f"Returned `ok` but held no usable value for the acts asked about, so `{sub}` fell "
            "back to the run's median for them and is labelled `draw`."
        )
    return "No score number was read from this call."


def _request_shape(step: CallStep) -> str:
    return params_summary(step.workflow, step.request)


def _envelope_summary(step: CallStep) -> str:
    bits = [f"status `{step.status}`"]
    if step.result_count is not None:
        bits.append(f"{step.result_count} result{'s' if step.result_count != 1 else ''}")
    bits.append("cache hit" if step.cache_hit else "live call")
    line = ", ".join(bits)
    if step.summary:
        line += f" — “{step.summary.strip()}”"
    return line


def render(export: RunExport, *, secret: str | None = None) -> str:
    """The markdown document, redacted."""
    acts = export.acts
    lines: list[str] = [
        f"# Walkthrough: {export.venue} ({export.slug})",
        "",
        f"Generated by `agent/scripts/walkthrough.py` from {export.source} on {TODAY.isoformat()}.",
        "Every Qloo call of the run in order: the request shape Outdraw sent through the Qloo",
        "harness (`qloo exec <workflow>`), what the envelope said, and how it fed the score.",
        "Entity and tag IDs are shown as `<id>`; no key, raw request or raw response appears.",
        "",
        f"Constraints: size tier `{export.constraints.size_tier}`, include tags "
        f"{export.constraints.include_tags or '[]'}, exclude tags "
        f"{export.constraints.exclude_tags or '[]'}, exclude recent months "
        f"{export.constraints.exclude_recent_months}, max candidates "
        f"{export.constraints.max_candidates}.",
        "",
        "Source labels: `qloo` = the number came out of a Qloo envelope; `draw` = Outdraw",
        "computed or templated it (weights, totals, neutral fallbacks, novelty, size fit,",
        "sell-through weights, the top reason). A neutral fallback is the run's median of the",
        "acts Qloo did measure (0.5 only when it measured none), so the Soundcheck panel shows",
        "that value, not a fixed 0.5.",
        "",
        "## Qloo calls",
        "",
    ]
    for i, step in enumerate(export.calls, start=1):
        lines += [
            f"### {i}. `{step.workflow}`",
            "",
            f"- Request shape: `{_request_shape(step)}`",
            f"- Envelope: {_envelope_summary(step)}",
            f"- Score: {_fed(step, acts, export.profile_items)}",
            "",
        ]
    if export.notes:
        lines += ["## Data notes", ""] + [f"- {n}" for n in export.notes] + [""]

    lines += ["## Scores", ""]
    names = [n for n in WEIGHTS if any(getattr(a.score.breakdown, n) is not None for a in acts)]
    header = "| # | Act | total | " + " | ".join(names) + " | trail |"
    lines.append(header)
    lines.append("|" + "---|" * (len(names) + 4))
    for i, act in enumerate(acts, start=1):
        cells = []
        for name in names:
            label = act.trail.source_labels.get(name, "?")
            cells.append(
                f"{_pct(getattr(act.score.breakdown, name))} {'Q' if label == 'qloo' else 'D'}"
            )
        problems = validate_trail(act.score, act.trail)
        verdict = "complete" if not problems else "INCOMPLETE: " + "; ".join(problems)
        lines.append(
            f"| {i} | {act.name} | {act.score.total:.3f} | " + " | ".join(cells) + f" | {verdict} |"
        )
    lines += [
        "",
        "Q = `qloo` (the number came out of a Qloo envelope), D = `draw` (Outdraw computed "
        "or templated it). Weights: "
        + ", ".join(f"{n} {w:.2f}" for n, w in WEIGHTS.items())
        + ".",
        "",
        "## Top act, item by item",
        "",
    ]
    if acts:
        top = acts[0]
        labels = top.trail.source_labels
        lines.append(f"**{top.name}** — total {top.score.total:.3f}. {top.score.top_reason}")
        lines.append("")
        for item in top.trail.inputs:
            label = labels.get(f"input:{item.ref}", "?")
            lines.append(f"- input `{item.ref}` ({label}): {item.label}")
        for call in top.trail.qloo_calls:
            label = labels.get(f"call:{call.call_id}", "?")
            hit = "cache hit" if call.cache_hit else "live"
            lines.append(
                f"- call `{call.workflow}` ({label}): `{call.params_summary}`, "
                f"status `{call.status}`, {hit}"
            )
        for sub, affinity in affinities_by_sub_score(top.trail).items():
            workflow = WORKFLOW_OF.get(sub) or "?"
            lines.append(
                f"- affinity `{sub}` (qloo): {affinity.value:.3f} read from a `{workflow}` call"
            )
        for sub in WEIGHTS:
            value = getattr(top.score.breakdown, sub)
            if value is None:
                continue
            lines.append(
                f"- sub-score `{sub}` ({labels.get(sub, '?')}): {_pct(value)} × weight "
                f"{WEIGHTS[sub]:.2f}"
            )
        lines.append(f"- total (draw): {top.score.total:.3f}, the weighted sum above")
        lines.append("")
    return redact("\n".join(lines).rstrip() + "\n", secret)


def write(export: RunExport, *, docs_dir: Path = DOCS_DIR, secret: str | None = None) -> Path:
    docs_dir.mkdir(parents=True, exist_ok=True)
    path = docs_dir / f"walkthrough-{export.slug}.md"
    path.write_text(render(export, secret=secret), encoding="utf-8")
    return path


# --- source 1: the offline fixture run --------------------------------------------------


def fixture(workflow: str, name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES_DIR / workflow / f"{name}.json").read_text("utf-8"))
    return data


def _describe_fixtures() -> dict[str, str]:
    """Synthetic act name -> describe fixture, for the three crowd acts."""
    out: dict[str, str] = {}
    for path in sorted((FIXTURES_DIR / "describe").glob("crowd_act_*.json")):
        env = json.loads(path.read_text("utf-8"))
        rows = env.get("results") or []
        if rows and isinstance(rows[0].get("name"), str):
            out[rows[0]["name"]] = path.stem
    return out


def jazz_word() -> str:
    """The genre word that auto-confirms against the `find_tags/jazz` fixture."""
    hit = next(
        r for r in fixture("find_tags", "jazz")["results"] if r["type"] == "urn:tag:genre:music"
    )
    return str(hit["name"])


def route(workflow: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Which synthetic envelope answers each call of the NYC jazz run."""
    if workflow == "describe":
        name = str(payload.get("entity", ""))
        return fixture("describe", _describe_fixtures().get(name, "radiohead"))
    if workflow == "find_tags":
        return fixture("find_tags", "jazz")
    if workflow == "recommend":
        target = str(payload.get("target_type"))
        if target != "artist":
            return fixture(
                "recommend",
                {"place": "crowd_places", "movie": "crowd_movies"}.get(target, "crowd_brands"),
            )
        if int(payload.get("limit", 0)) <= 10:
            return fixture("recommend", "crowd_artists")
        return fixture(
            "recommend",
            "candidates_include_jazz" if payload.get("include_tags") else "candidates_baseline",
        )
    if workflow == "audience_demographics":
        return fixture("audience_demographics", "crowd_demographics_1")
    if workflow == "compare_audiences":
        return fixture("compare_audiences", "crowd_genres")
    if workflow == "where_popular":
        return fixture("where_popular", "radiohead_new_york_ny")
    if workflow == "trends":
        return fixture("trends", "three_artists_last_12_months")
    raise ValueError(f"no fixture route for {workflow}")


class FixtureExec(QlooExec):
    """`QlooExec` answering from the synthetic fixtures instead of spawning the harness.

    Everything else of the gateway runs as in production: cache, status branching,
    `call_id`s, the observer that feeds the run store.
    """

    async def _run_with_retries(
        self, workflow: str, input_: dict[str, Any]
    ) -> tuple[QlooEnvelope, int]:
        return normalise(route(workflow, input_)), 1


def nyc_jazz_venue() -> VenueInput:
    """A 150-cap New York room with three past acts named after the synthetic fixtures."""
    names = sorted(_describe_fixtures())
    sold = [150, 100, 45]
    return VenueInput(
        name="Blue Note Jazz Club",
        city="New York",
        country="United States",
        capacity=150,
        past_shows=[
            PastShow(
                act=name, date=dt.date(2026, 6 + i, 12), tickets_sold=sold[i % 3], capacity=150
            )
            for i, name in enumerate(names[:3])
        ],
    )


async def run_nyc_jazz() -> tuple[RunStore, str]:
    """The NYC jazz pipeline on synthetic fixtures: resolve → profile → candidates → signals →
    rank. Returns the run store and the run id; every scored candidate has a stored trail."""
    venue = nyc_jazz_venue()
    constraints = Constraints(
        include_tags=[jazz_word()],
        exclude_tags=[],
        size_tier="emerging",  # the synthetic candidates' popularity band
        exclude_recent_months=0,
        max_candidates=20,
    )
    store = RunStore()
    qloo = FixtureExec(QlooConfig(qloo_bin="/fixtures/qloo"), observer=store.observe)
    venue_id = store.add_venue(venue, venue_id="ven_nyc_jazz")
    for show in venue.past_shows:
        store.add_resolved(venue_id, await resolve_entity(show.act, "artist", qloo=qloo))
    tools = DrawTools(store, qloo, today=TODAY)
    profile = await tools.build_crowd_profile(venue_id)
    run = await tools.generate_candidates(profile["profile_id"], constraints.model_dump_json())
    if not run["count"]:
        raise RuntimeError(f"the fixture run produced no candidates: {run['reason']}")
    await tools.gather_signals(run["run_id"])
    await tools.score_and_rank(run["run_id"])
    return store, str(run["run_id"])


async def fixture_run(slug: str = "nyc-jazz") -> RunExport:
    store, run_id = await run_nyc_jazz()
    return export_from_store(
        store,
        run_id,
        slug=slug,
        source="the synthetic Qloo fixtures in `agent/tests/fixtures/qloo`",
    )


def export_from_store(store: RunStore, run_id: str, *, slug: str, source: str) -> RunExport:
    run = store.run(run_id)
    venue = store.venue(run.venue_id)
    profile = store.profile(run.profile_id)
    calls = [
        CallStep(
            call_id=r.call_id,
            workflow=r.workflow,
            status=r.status,
            cache_hit=r.cache_hit,
            request=dict(r.request),
            summary=r.summary,
            # compare_audiences returns an object, whose count means nothing.
            result_count=r.result_count if isinstance(r.results, list) else None,
        )
        for r in store.results.values()
    ]
    profile_items: dict[str, list[str]] = defaultdict(list)
    for ref in profile.evidence:
        profile_items[ref.call_id].append(ref.item_ref)
    acts = [
        ScoredAct(
            name=item.candidate.name,
            entity_id=item.candidate.entity_id,
            score=item.score,
            trail=store.trail(run.candidate_ids[item.candidate.entity_id]),
        )
        for item in run.ranked or []
    ]
    notes = [
        *(profile.notes or []),
        *run.candidates.notes,
        *(run.signals.notes if run.signals else []),
    ]
    return RunExport(
        slug=slug,
        source=source,
        venue=f"{venue.name}, {venue.city}",
        constraints=run.constraints,
        calls=calls,
        profile_items=dict(profile_items),
        acts=acts,
        notes=list(dict.fromkeys(notes)),
    )


# --- source 2: a stored run ---------------------------------------------------------------


async def db_run(run_id: str, database_url: str, *, slug: str | None) -> RunExport:
    """A stored run from Postgres: its venue, constraints, recorded calls and candidate trails."""
    import asyncpg

    conn = await asyncpg.connect(database_url)
    try:
        run = await conn.fetchrow(
            "SELECT venue_id, constraints, result FROM runs WHERE id = $1", run_id
        )
        if run is None:
            sys.exit(f"no run {run_id!r}")
        venue = await conn.fetchrow("SELECT name, city FROM venues WHERE id = $1", run["venue_id"])
        profile = await conn.fetchrow(
            "SELECT profile FROM crowd_profiles WHERE venue_id = $1", run["venue_id"]
        )
        calls = await conn.fetch(
            "SELECT call_id, workflow, status, cache_hit, request, envelope FROM qloo_calls"
            " WHERE run_id = $1 ORDER BY created_at, call_id",
            run_id,
        )
        rows = await conn.fetch(
            "SELECT entity_id, score, evidence FROM candidates WHERE run_id = $1", run_id
        )
    finally:
        await conn.close()

    def loads(value: Any) -> Any:
        return json.loads(value) if isinstance(value, str) else value

    steps = []
    for r in calls:
        envelope = loads(r["envelope"]) or {}
        steps.append(
            CallStep(
                call_id=r["call_id"],
                workflow=r["workflow"],
                status=r["status"],
                cache_hit=bool(r["cache_hit"]),
                request=dict(loads(r["request"]) or {}),
                summary=envelope.get("summary"),
                result_count=(
                    envelope.get("result_count")
                    if isinstance(envelope.get("results"), list)
                    else None
                ),
            )
        )
    acts = []
    for r in rows:
        score = Score.model_validate(loads(r["score"]))
        evidence = dict(loads(r["evidence"]) or {})
        trail_fields = {k: evidence[k] for k in EvidenceTrail.model_fields if k in evidence}
        acts.append(
            ScoredAct(
                name=str(evidence.get("name") or r["entity_id"]),
                entity_id=r["entity_id"],
                score=score,
                trail=EvidenceTrail.model_validate(trail_fields),
            )
        )
    acts.sort(key=lambda a: -a.score.total)
    profile_items: dict[str, list[str]] = defaultdict(list)
    for ref in (loads(profile["profile"]) if profile else {}).get("evidence") or []:
        profile_items[ref["call_id"]].append(ref["item_ref"])
    venue_label = f"{venue['name']}, {venue['city']}" if venue else run["venue_id"]
    result = loads(run["result"]) or {}
    return RunExport(
        slug=slug or slugify(venue_label),
        source=f"stored run `{run_id}` (private data: do not commit)",
        venue=venue_label,
        constraints=Constraints.model_validate(loads(run["constraints"])),
        calls=steps,
        profile_items=dict(profile_items),
        acts=acts,
        notes=list(result.get("notes") or []),
    )


# --- CLI ---------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--fixture", default="nyc-jazz", help="offline fixture run (default)")
    source.add_argument("--run-id", help="a stored run id (needs DATABASE_URL)")
    parser.add_argument("--slug", help="file name suffix: docs/walkthrough-<slug>.md")
    parser.add_argument("--out", type=Path, default=DOCS_DIR, help="output folder (default docs/)")
    args = parser.parse_args(argv)

    secret = os.environ.get("QLOO_API_KEY") or None
    if args.run_id:
        url = os.environ.get("DATABASE_URL")
        if not url:
            sys.exit("--run-id needs DATABASE_URL (source ../.env.local)")
        export = asyncio.run(db_run(args.run_id, url, slug=args.slug))
    else:
        if args.fixture != "nyc-jazz":
            sys.exit(f"unknown fixture run {args.fixture!r}; only nyc-jazz exists")
        export = asyncio.run(fixture_run(args.slug or args.fixture))
    path = write(export, docs_dir=args.out, secret=secret)
    incomplete = [a.name for a in export.acts if validate_trail(a.score, a.trail)]
    print(f"wrote {path} ({len(export.calls)} Qloo calls, {len(export.acts)} scored acts)")
    if incomplete:
        print(f"INCOMPLETE trails: {', '.join(incomplete)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
