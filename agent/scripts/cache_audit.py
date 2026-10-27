#!/usr/bin/env python3
"""Cache audit (T39): prove that repeating the venue flow costs nothing.

Runs the whole venue flow twice through the service's own code (`draw.api`: save the venue,
resolve and confirm its matches, build the crowd profile, run the agent, write the briefs
for the top five, the LLM-only compare) against the mocked backends CI has, and asserts:

- the second pass spawns **0 qloo processes**: no `qloo exec`, no `qloo mcp` server and no
  MCP tool call, and makes **at most 1 LLM call**;
- the first pass stays within the per-run call budgets of CLAUDE.md: profile ≤ 6 `qloo exec`
  spawns, candidates ≤ 3 and signals ≤ 33 MCP tool calls, briefs ≤ 5 spawns.

What is mocked, and how the counts are taken:

- **Qloo**: `tests/qloo/fake_qloo.py`, a real executable standing in for the harness binary
  (`QlooConfig.qloo_bin`), so the gateway spawns real processes: `qloo exec <workflow>` for
  resolve, profile and briefs, `qloo mcp` (the fake MCP server) for the agent run. Every
  process start and every MCP tool call appends a line to a ledger file
  (`FAKE_QLOO_LOG`), written by the child itself, so the audit counts from outside the
  gateway and no code path can hide a spawn.
- **The model**: `tests/agent/scripted_model.py`, built through `Deps.model_factory` exactly
  where the service builds Haiku 5.5 (the agent, each brief, the compare); every model
  built and every call it answers is counted.
- **Postgres**: a throwaway pgvector container (testcontainers, Docker) migrated to head,
  or ``--database-url`` for one you provide (it is migrated, nothing is truncated). The
  envelope cache, the semantic run cache (T36) and the cached briefs and compare are the
  real tables, as in production.

The second pass is the same venue saved again by id, as the web does when a booker comes
back: no field changes, the stored matches are confirmed again, the profile is rebuilt
from the cached envelopes and the run is served by the semantic cache, briefs and compare
included. Usage (from ``/agent``; CI runs it on every push)::

    uv run python scripts/cache_audit.py
    uv run python scripts/cache_audit.py --database-url postgresql://.../throwaway

Exit 0 when every check holds, 1 with the failed checks listed. Nothing here needs a key:
`QLOO_API_KEY` is set to a fake value for the fake harness and restored afterwards.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import logging
import os
import re
import sys
import tempfile
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

AGENT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT_DIR))
sys.path.insert(0, str(AGENT_DIR / "tests" / "agent"))

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from scripted_model import Results, ScriptedModel, Turn  # noqa: E402

from draw.agent.compare import COMPARE_ACTS  # noqa: E402
from draw.agent.store import new_id  # noqa: E402
from draw.api.deps import Deps  # noqa: E402
from draw.api.inputs import ConfirmBody, VenueUpsert  # noqa: E402
from draw.api.runs import RunOut, brief_for, compare_for, execute_run, venue_for_run  # noqa: E402
from draw.api.venues import confirm, profile, resolve, upsert_venue  # noqa: E402
from draw.profile import crowd  # noqa: E402
from draw.qloo.budget import LLM_PREFIX, MemoryCounter  # noqa: E402
from draw.qloo.config import QlooConfig  # noqa: E402
from draw.ranking import candidates as candidates_mod  # noqa: E402
from draw.ranking import signals as signals_mod  # noqa: E402
from draw.ranking.evidence import REF_TOP_REASON  # noqa: E402
from draw.schemas import Constraints  # noqa: E402

FAKE_QLOO: Final = AGENT_DIR / "tests" / "qloo" / "fake_qloo.py"
FAKE_KEY: Final = "-".join(["fake", "qloo", "key", "0" * 12])
LEDGER_ENV: Final = "FAKE_QLOO_LOG"
PG_IMAGE: Final = "pgvector/pgvector:pg18"
"""Same major version as the Neon project (tests/conftest.py)."""
SCRUBBED: Final = (
    "REDIS_URL",
    "QLOO_RECORD",
    "LIVE_MODE",
    "LLM_DAILY_TOKEN_CAP",
    "SEMANTIC_CACHE",
    "EMBED_URL",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
)
"""Environment the service reads; unset so the audit runs on the in-process defaults."""

PROFILE_BUDGET: Final = crowd.DEFAULT_MAX_CALLS
CANDIDATES_BUDGET: Final = candidates_mod.MAX_RECOMMEND_CALLS
SIGNALS_BUDGET: Final = signals_mod.DEFAULT_MAX_CALLS
BRIEFS: Final = 5
"""Briefs per pass (the top five), one `compare_audiences` spawn each at most."""
WARM_MODEL_CALLS: Final = 1
SIGNAL_WORKFLOWS: Final = frozenset({"compare_audiences", "where_popular", "trends"})

# The demo room (decision 001: New York, not Malta), its three past acts named after the
# synthetic `describe` fixtures so they auto-confirm; the venue itself is not a Qloo place.
VENUE: Final[dict[str, Any]] = {
    "name": "Blue Note Jazz Club",
    "city": "New York",
    "country": "United States",
    "capacity": 150,
    "past_shows": [
        {"act": "Synthetic Entity 1", "date": "2026-06-12", "tickets_sold": 150, "capacity": 150},
        {"act": "Synthetic Entity 3", "date": "2026-07-03", "tickets_sold": 100, "capacity": 150},
        {"act": "Synthetic Entity 4", "date": "2026-08-21", "tickets_sold": 45, "capacity": 150},
    ],
}
CONSTRAINTS: Final = Constraints(
    include_tags=[],
    exclude_tags=[],
    size_tier="emerging",
    exclude_recent_months=6,
    max_candidates=20,
)
MESSAGE: Final = "Shortlist emerging acts for a Thursday night; the crowd leans jazz."


# --- the ledger: what the fake harness wrote ---------------------------------------------


@dataclass
class Spend:
    """Qloo processes and model calls of one stage, as counted from outside the gateway."""

    exec: Counter[str] = field(default_factory=Counter)
    """`qloo exec` spawns by workflow."""
    mcp_starts: int = 0
    """`qloo mcp` servers started."""
    tools: Counter[str] = field(default_factory=Counter)
    """MCP tool calls by workflow."""
    model_calls: int = 0

    @property
    def processes(self) -> int:
        return sum(self.exec.values()) + self.mcp_starts

    @property
    def tool_calls(self) -> int:
        return sum(self.tools.values())

    def add(self, other: Spend) -> None:
        self.exec.update(other.exec)
        self.mcp_starts += other.mcp_starts
        self.tools.update(other.tools)
        self.model_calls += other.model_calls


class Ledger:
    """The file the fake harness appends to: `exec <workflow>`, `mcp`, `tool <workflow>`."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.write_text("", encoding="utf-8")
        self._read = 0

    def take(self) -> Spend:
        """The spend since the previous `take`."""
        lines = self.path.read_text(encoding="utf-8").splitlines()
        fresh, self._read = lines[self._read :], len(lines)
        spend = Spend()
        for line in fresh:
            kind, _, workflow = line.partition(" ")
            if kind == "exec":
                spend.exec[workflow] += 1
            elif kind == "mcp":
                spend.mcp_starts += 1
            elif kind == "tool":
                spend.tools[workflow] += 1
        return spend


# --- the scripted model -------------------------------------------------------------------


def _latest(results: Results, key: str) -> Any:
    return next(r[key] for r in reversed(results) if key in r)


class Models:
    """`Deps.model_factory`: a `ScriptedModel` playing `turns` wherever the service builds one."""

    def __init__(self) -> None:
        self.turns: list[Any] = []
        self.built: list[ScriptedModel] = []

    def __call__(self) -> ScriptedModel:
        model = ScriptedModel(self.turns)
        self.built.append(model)
        return model

    def calls(self) -> int:
        """Model calls answered so far, every model built included."""
        return sum(len(m.requests) for m in self.built)

    def prompt(self) -> str:
        return "".join(str(b.get("text", "")) for b in self.built[-1].requests[0][0]["content"])

    def pipeline(self) -> None:
        """The agent: generate (on the stored profile) → gather_signals → score_and_rank → JSON."""
        self.turns = [self._generate, _gather, _rank, _answer]

    def brief(self, act: str) -> None:
        self.turns = [
            json.dumps(
                {
                    "why_it_fits": [
                        {"text": "Fits the room on its top reason.", "evidence_ref": REF_TOP_REASON}
                    ],
                    "night_format": "-",
                    "support_act": None,
                    "partners": [],
                    "outreach_email": {
                        "subject": f"{act} at {VENUE['name']}",
                        "body": f"Hello, {VENUE['name']} would like to host {act}. Best, bookings.",
                    },
                }
            )
        ]

    def compare(self) -> None:
        self.turns = [
            json.dumps(
                {
                    "acts": [
                        {"name": f"Synthetic Act {i}", "reason": "Fits the room."}
                        for i in range(COMPARE_ACTS)
                    ]
                }
            )
        ]

    def _generate(self, results: Results) -> Turn:
        match = re.search(r"profile_id (prof_\w+)", self.prompt())
        if not match:
            raise AssertionError("the stored profile's id is not in the agent's prompt")
        constraints = CONSTRAINTS.model_dump_json()
        return [
            ("generate_candidates", {"profile_id": match.group(1), "constraints_json": constraints})
        ]


def _gather(results: Results) -> Turn:
    return [("gather_signals", {"run_id": _latest(results, "run_id")})]


def _rank(results: Results) -> Turn:
    return [("score_and_rank", {"run_id": _latest(results, "run_id")})]


def _answer(results: Results) -> Turn:
    ranked = results[-1]
    return json.dumps({"shortlist": ranked["shortlist"], "notes": [], "data_quality": "ok"})


# --- the flow ----------------------------------------------------------------------------


@dataclass
class Stage:
    name: str
    spend: Spend
    note: str = ""


@dataclass
class Pass:
    label: str
    venue_id: str
    run: RunOut
    stages: list[Stage]
    briefs_cached: list[bool]
    compare_cached: bool

    @property
    def total(self) -> Spend:
        total = Spend()
        for stage in self.stages:
            total.add(stage.spend)
        return total

    def stage(self, name: str) -> Spend:
        return next(s.spend for s in self.stages if s.name == name)


class Flow:
    """One pass of the venue flow, stage by stage, each stage's spend taken off the ledger."""

    def __init__(self, deps: Deps, models: Models, ledger: Ledger) -> None:
        self.deps = deps
        self.models = models
        self.ledger = ledger
        self.stages: list[Stage] = []
        self._model_calls = models.calls()

    def _close(self, name: str, note: str = "") -> None:
        spend = self.ledger.take()
        calls = self.models.calls()
        spend.model_calls, self._model_calls = calls - self._model_calls, calls
        self.stages.append(Stage(name, spend, note))

    async def run(self, label: str, venue_id: str | None) -> Pass:
        deps = self.deps
        saved = await upsert_venue(VenueUpsert.model_validate({**VENUE, "id": venue_id}), deps)
        self._close("save venue", "saved again by id" if venue_id else "new venue")
        venue_id = saved.id

        resolutions = await resolve(venue_id, deps)
        self._close("resolve", f"{len(resolutions.items)} items")

        # The room is not a Qloo place: the booker keeps it location-only, every pass.
        for item in resolutions.items:
            if item.kind == "venue":
                await confirm(venue_id, ConfirmBody(query=item.query, chosen_entity_id=None), deps)
        unconfirmed = [i.query for i in (await resolve(venue_id, deps)).items if not i.confirmed]
        if unconfirmed:
            raise AssertionError(f"past acts did not auto-confirm: {unconfirmed}")
        self._close("confirm", "venue kept location-only")

        built = await profile(venue_id, deps)
        self._close("profile", f"quality {built.data_quality}, {len(built.signals)} signals")

        self.models.pipeline()
        async with deps.sessions() as session:
            target = await venue_for_run(session, venue_id)
        run = await execute_run(deps, new_id("run"), target, CONSTRAINTS, MESSAGE, semantic=True)
        if run.status != "ok" or run.result is None or not run.result.shortlist:
            detail = run.result.error if run.result else None
            raise AssertionError(f"the agent run ended {run.status!r}: {detail or 'no shortlist'}")
        source = run.result.cache_source or "live"
        self._close("run", f"{source}, {len(run.result.shortlist)} acts, steps {run.result.steps}")

        briefs_cached = []
        for entry in run.result.shortlist[:BRIEFS]:
            self.models.brief(entry.name)
            brief = await brief_for(deps, run.run_id, entry.entity_id)
            if brief.error:
                raise AssertionError(f"brief for {entry.name!r} unusable: {brief.error}")
            briefs_cached.append(brief.cached)
        self._close("briefs", f"{len(briefs_cached)} briefs, {sum(briefs_cached)} cached")

        self.models.compare()
        compare = await compare_for(deps, run.run_id)
        if compare.error:
            raise AssertionError(f"compare unusable: {compare.error}")
        self._close("compare", "cached" if compare.cached else "live")

        stages, self.stages = self.stages, []
        return Pass(label, venue_id, run, stages, briefs_cached, compare.cached)


# --- the checks --------------------------------------------------------------------------


def check(first: Pass, second: Pass) -> list[str]:
    """Every failed assertion, as one line each; empty when the audit passes."""
    problems: list[str] = []

    def expect(condition: bool, message: str) -> None:
        if not condition:
            problems.append(message)

    cold, warm = first.total, second.total
    # Sanity: the first pass really did the work the second pass is measured against.
    expect(cold.processes > 0 and cold.tool_calls > 0, "first pass: no Qloo process was spawned")
    expect(cold.mcp_starts == 1, f"first pass: {cold.mcp_starts} qloo mcp servers, expected 1")
    expect(cold.model_calls >= 3, f"first pass: {cold.model_calls} model calls, expected ≥ 3")
    expect(first.run.result is not None and first.run.result.cache_source is None,
           "first pass: the run came from a cache")  # fmt: skip

    # The per-run budgets (CLAUDE.md), stage by stage.
    prof = first.stage("profile")
    expect(
        prof.exec.total() <= PROFILE_BUDGET,
        f"profile: {prof.exec.total()} qloo exec spawns, budget {PROFILE_BUDGET}",
    )
    run = first.stage("run")
    expect(
        run.tools["recommend"] <= CANDIDATES_BUDGET,
        f"candidates: {run.tools['recommend']} recommend calls, budget {CANDIDATES_BUDGET}",
    )
    signals = sum(n for w, n in run.tools.items() if w in SIGNAL_WORKFLOWS)
    expect(signals <= SIGNALS_BUDGET, f"signals: {signals} calls, budget {SIGNALS_BUDGET}")
    expect(run.exec.total() == 0, f"run: {run.exec.total()} qloo exec spawns beside the MCP server")
    briefs = first.stage("briefs")
    expect(
        briefs.exec.total() <= BRIEFS and briefs.tool_calls == 0,
        f"briefs: {briefs.exec.total()} spawns and {briefs.tool_calls} tool calls, budget {BRIEFS}",
    )

    # The second pass: zero Qloo processes, at most one model call, everything served cached.
    expect(
        warm.processes == 0 and warm.tool_calls == 0,
        f"second pass: {warm.exec.total()} qloo exec spawns, {warm.mcp_starts} qloo mcp "
        f"servers, {warm.tool_calls} MCP tool calls; expected 0",
    )
    expect(
        warm.model_calls <= WARM_MODEL_CALLS,
        f"second pass: {warm.model_calls} model calls, at most {WARM_MODEL_CALLS} allowed",
    )
    expect(second.venue_id == first.venue_id, "second pass: the venue id changed")
    result = second.run.result
    expect(
        result is not None and result.cache_source == "semantic",
        "second pass: the run was not served by the semantic cache",
    )
    expect(
        result is not None and first.run.result is not None
        and result.shortlist == first.run.result.shortlist,
        "second pass: the shortlist differs from the first pass",
    )  # fmt: skip
    expect(all(second.briefs_cached), "second pass: a brief was written again")
    expect(second.compare_cached, "second pass: the compare was computed again")
    return problems


# --- reporting ---------------------------------------------------------------------------


def _workflows(counter: Counter[str]) -> str:
    return ", ".join(f"{w}×{n}" for w, n in sorted(counter.items())) or "-"


def render(passes: list[Pass], problems: list[str]) -> str:
    lines = []
    for p in passes:
        lines.append(f"== {p.label} (venue {p.venue_id}, run {p.run.run_id})")
        lines.append(f"{'stage':<12}{'exec':>6}{'mcp':>5}{'tools':>7}{'model':>7}  detail")
        for s in p.stages:
            detail = s.note
            if s.spend.exec:
                detail += f"; exec {_workflows(s.spend.exec)}"
            if s.spend.tools:
                detail += f"; tools {_workflows(s.spend.tools)}"
            lines.append(
                f"{s.name:<12}{s.spend.exec.total():>6}{s.spend.mcp_starts:>5}"
                f"{s.spend.tool_calls:>7}{s.spend.model_calls:>7}  {detail.lstrip('; ')}"
            )
        t = p.total
        lines.append(
            f"{'total':<12}{t.exec.total():>6}{t.mcp_starts:>5}{t.tool_calls:>7}{t.model_calls:>7}"
        )
        lines.append("")
    lines.append(
        f"Budgets: profile ≤ {PROFILE_BUDGET} exec, candidates ≤ {CANDIDATES_BUDGET} and signals "
        f"≤ {SIGNALS_BUDGET} tool calls, briefs ≤ {BRIEFS} exec; second pass 0 processes, "
        f"≤ {WARM_MODEL_CALLS} model call."
    )
    if problems:
        lines.append("FAILED:")
        lines.extend(f"  - {p}" for p in problems)
    else:
        lines.append("PASSED: the second pass spawned no qloo process and made no model call.")
    return "\n".join(lines)


# --- wiring: database, fake harness, environment --------------------------------------------


def migrate(database_url: str) -> None:
    cfg = Config(str(AGENT_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(AGENT_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")


@contextmanager
def throwaway_postgres() -> Iterator[str]:
    """A pgvector Postgres container for the audit's lifetime (needs Docker)."""
    # Ryuk, the reaper sidecar, cannot mount Docker Desktop's socket on macOS.
    os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer(PG_IMAGE, driver=None) as pg:
        yield pg.get_connection_url()


def fake_binary(folder: Path) -> Path:
    """A `qloo` executable: a shell wrapper running `fake_qloo.py` with this interpreter."""
    wrapper = folder / "qloo"
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_QLOO}" "$@"\n', encoding="utf-8")
    wrapper.chmod(0o755)
    return wrapper


@contextmanager
def audit_environment(ledger: Path) -> Iterator[None]:
    """The fake key and ledger for the harness children; the rest unset, all restored after."""
    saved = {name: os.environ.get(name) for name in (*SCRUBBED, "QLOO_API_KEY", LEDGER_ENV)}
    for name in SCRUBBED:
        os.environ.pop(name, None)
    os.environ["QLOO_API_KEY"] = FAKE_KEY
    os.environ[LEDGER_ENV] = str(ledger)
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


async def make_deps(database_url: str, qloo_bin: Path, models: Models) -> Deps:
    return await Deps.create(
        database_url,
        config=QlooConfig(qloo_bin=str(qloo_bin), api_key=FAKE_KEY, daily_cap=1000),
        qloo_counter=MemoryCounter(1000),
        llm_counter=MemoryCounter(
            10_000_000, prefix=LLM_PREFIX, what="LLM token", charge_over=True
        ),
        model_factory=models,
    )


@dataclass
class Audit:
    passes: list[Pass]
    problems: list[str]

    @property
    def passed(self) -> bool:
        return not self.problems

    def report(self) -> str:
        return render(self.passes, self.problems)


async def run_audit(database_url: str) -> Audit:
    """Both passes on a database migrated to head (`migrate`, synchronous, runs before the
    loop); the report says what was spent and what failed."""
    with tempfile.TemporaryDirectory(prefix="cache-audit-") as tmp:
        folder = Path(tmp)
        ledger_path = folder / "qloo.log"
        with audit_environment(ledger_path):
            models = Models()
            deps = await make_deps(database_url, fake_binary(folder), models)
            try:
                flow = Flow(deps, models, Ledger(ledger_path))
                first = await flow.run("first pass (cold)", None)
                second = await flow.run("second pass (repeat)", first.venue_id)
            finally:
                await deps.aclose()
    return Audit([first, second], check(first, second))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("CACHE_AUDIT_DATABASE_URL"),
        help="a throwaway Postgres (migrated to head); default: a testcontainers container",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    started = dt.datetime.now(dt.UTC)

    if args.database_url:
        migrate(args.database_url)
        audit = asyncio.run(run_audit(args.database_url))
    else:
        with throwaway_postgres() as url:
            migrate(url)
            audit = asyncio.run(run_audit(url))
    print(audit.report())
    print(f"({(dt.datetime.now(dt.UTC) - started).total_seconds():.1f}s)")
    return 0 if audit.passed else 1


if __name__ == "__main__":
    sys.exit(main())
