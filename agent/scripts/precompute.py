#!/usr/bin/env python3
"""Precompute the demo venues (T29) so judges open them with zero Qloo or model calls.

For each seed in ``seeds/venues.json`` (``draw.seeds``): create the venue
(``ven_seed_<slug>``), resolve and auto-confirm its past acts through ``QlooExec`` (seed
acts are chosen to resolve unambiguously; one that does not fails the run loudly, with its
candidates listed), build the crowd profile, run the agent once (``run_seed_<slug>``), the
LLM-only compare and the briefs for the top five, then mark the run ``is_seed`` with its
``seed_slug``. From then on the API serves it read-only with an ETag.

Idempotent: every stage reads what is stored first, so a second run makes zero Qloo calls
and zero model calls, and a run interrupted midway resumes where it stopped. A seed whose
venue changed in the file is rebuilt from scratch. The pipeline itself needs no model (the
resolve and profile stages are ``QlooExec`` only); the agent, the compare and the briefs do.

Quota guard, before any Qloo call: the uncached calls are estimated per seed and overall
from cache lookups (``CacheProbe``: the describe/find_tags inputs are checked against the
envelope cache, nothing is spawned) and the per-run budgets for the stages whose inputs
depend on earlier results (profile ≤ 6, candidates ≤ 3, signals ≤ 33, briefs ≤ 5). Stages
already stored cost nothing. ``--dry-run`` prints the estimate and exits. A real run asks
for confirmation (or takes ``--yes``) and aborts when the remaining monthly quota minus the
estimate would fall below ``QLOO_JUDGING_RESERVE`` (default 2000). The remaining quota is
read from ``qloo setup --status`` when it reports one; it does not today (decision 001), so
``--remaining N`` is required whenever the estimate is above zero.

Recording is on (``QLOO_RECORD=1``) unless ``--no-record``: the seed runs' calls land in
``qloo_calls`` and ``agent/recordings/<date>/`` and become the walkthrough source.

Usage (from ``/agent``; ``../.env.local`` is read for ``DATABASE_URL`` and the keys)::

    uv run python scripts/precompute.py --dry-run
    uv run python scripts/precompute.py --remaining 8200
    uv run python scripts/precompute.py --remaining 8200 --yes --seed nola-jazz
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import subprocess
import sys
from collections.abc import Awaitable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import HTTPException  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

from draw.api.deps import Deps  # noqa: E402
from draw.api.runs import brief_for, compare_for, execute_run, venue_for_run  # noqa: E402
from draw.api.venues import (  # noqa: E402
    VENUE_PREFIX,
    ConfirmBody,
    confirm,
    load_resolutions,
    load_venue,
    profile,
    resolve,
)
from draw.db.models import (  # noqa: E402
    CandidateRow,
    CrowdProfileRow,
    QlooCallRow,
    ResolutionRow,
    RunRow,
    VenueRow,
)
from draw.profile import crowd  # noqa: E402
from draw.profile.resolve import GENRE_TAG_LIMIT  # noqa: E402
from draw.qloo.config import QlooConfig  # noqa: E402
from draw.qloo.envelope import QlooEnvelope, QlooGatewayError  # noqa: E402
from draw.qloo.exec_client import QlooExec  # noqa: E402
from draw.ranking import candidates as candidates_mod  # noqa: E402
from draw.ranking import signals as signals_mod  # noqa: E402
from draw.seeds import SEEDS_FILE, Seed, load_seeds, run_id_for, venue_id_for  # noqa: E402

AGENT_DIR = Path(__file__).resolve().parents[1]
ENV_FILE = AGENT_DIR.parent / ".env.local"

BRIEFS: Final = 5
"""Briefs per seed: the top five of the shortlist, one Qloo call (support act) each at most."""
PROFILE_BUDGET: Final = crowd.DEFAULT_MAX_CALLS
CANDIDATES_BUDGET: Final = candidates_mod.MAX_RECOMMEND_CALLS
SIGNALS_BUDGET: Final = signals_mod.DEFAULT_MAX_CALLS
DEFAULT_RESERVE: Final = 2000
RESERVE_ENV: Final = "QLOO_JUDGING_RESERVE"

EXIT_ABORTED: Final = 1
EXIT_NO_REMAINING: Final = 2
EXIT_RESERVE: Final = 3

_REMAINING = re.compile(
    r"(?i)(?:remaining|left)\D{0,24}?(\d[\d,]*)|(\d[\d,]*)\s*(?:requests?\s*)?(?:remaining|left)"
)


# --- cache probe: what would be a live call? -------------------------------------------------


class CacheMiss(Exception):  # noqa: N818 - a probe outcome, not an error
    """The probe reached the point where the gateway would spawn the harness."""


class CacheProbe(QlooExec):
    """A `QlooExec` over the real cache that never spawns: a miss raises `CacheMiss`.

    Calling the same workflow methods the pipeline calls keeps the probed input shapes equal
    to the live ones (the cache key is the workflow plus the canonical input).
    """

    async def _run_with_retries(
        self, workflow: str, input_: dict[str, Any]
    ) -> tuple[QlooEnvelope, int]:
        raise CacheMiss(workflow)


async def is_cached(call: Awaitable[Any]) -> bool:
    """Await a probe call: True when the cache answered, False when it would spawn."""
    try:
        await call
    except CacheMiss:
        return False
    except QlooGatewayError:
        return True  # a cached envelope the status branch rejects is still a cache hit
    return True


def probe_for(deps: Deps) -> CacheProbe:
    return CacheProbe(deps.gateways.config, cache=deps.gateways.cache)


# --- what is stored for a seed ---------------------------------------------------------------


@dataclass
class SeedState:
    venue_stored: bool = False
    venue_changed: bool = False
    missing_keys: list[str] = field(default_factory=list)
    """Resolution keys (`venue:<name>`, act names) with no stored row yet."""
    unconfirmed: list[str] = field(default_factory=list)
    """Stored acts the resolver could not auto-confirm: the seed cannot proceed."""
    profile_stored: bool = False
    run: RunRow | None = None
    top: list[str] = field(default_factory=list)
    """Entity ids of the shortlist's top five, when the run finished ok."""
    briefed: set[str] = field(default_factory=set)

    @property
    def run_ok(self) -> bool:
        return self.run is not None and self.run.status == "ok"

    @property
    def fresh(self) -> bool:
        return not self.venue_stored or self.venue_changed


def _shortlist_ids(run: RunRow | None) -> list[str]:
    if run is None or run.status != "ok":
        return []
    entries = (run.result or {}).get("shortlist") or []
    return [str(e["entity_id"]) for e in entries[:BRIEFS] if isinstance(e, dict)]


async def inspect(deps: Deps, seed: Seed) -> SeedState:
    venue_id, run_id = venue_id_for(seed.slug), run_id_for(seed.slug)
    state = SeedState()
    async with deps.sessions() as session:
        row = await session.get(VenueRow, venue_id)
        if row is None:
            return state
        state.venue_stored = True
        _, stored = await load_venue(session, venue_id)
        state.venue_changed = stored != seed.venue
        if state.venue_changed:
            return state
        venue_entity, acts, missing = await load_resolutions(session, venue_id, seed.venue)
        state.missing_keys = missing
        state.unconfirmed = [a.query for a in acts if not a.confirmed]
        state.profile_stored = await session.get(CrowdProfileRow, venue_id) is not None
        state.run = await session.get(RunRow, run_id)
        state.top = _shortlist_ids(state.run)
        if state.run is not None:
            rows = await session.scalars(
                select(CandidateRow).where(
                    CandidateRow.run_id == run_id, CandidateRow.brief.is_not(None)
                )
            )
            state.briefed = {r.entity_id for r in rows}
    return state


# --- the estimate ----------------------------------------------------------------------------


@dataclass
class SeedEstimate:
    slug: str
    resolve: int = 0
    profile: int = 0
    tags: int = 0
    candidates: int = 0
    signals: int = 0
    briefs: int = 0
    stored: list[str] = field(default_factory=list)
    """Stages already stored, so costing nothing."""
    warnings: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(
            (self.resolve, self.profile, self.tags, self.candidates, self.signals, self.briefs)
        )


@dataclass
class Estimate:
    seeds: list[SeedEstimate]

    @property
    def total(self) -> int:
        return sum(s.total for s in self.seeds)


def _keys_of(seed: Seed) -> list[str]:
    acts = dict.fromkeys(s.act.strip() for s in seed.venue.past_shows if s.act.strip())
    return [VENUE_PREFIX + seed.venue.name.strip(), *acts]


async def estimate_seed(deps: Deps, seed: Seed, state: SeedState) -> SeedEstimate:
    probe = probe_for(deps)
    est = SeedEstimate(seed.slug)
    keys = _keys_of(seed) if state.fresh else state.missing_keys
    for key in keys:
        if key.startswith(VENUE_PREFIX):
            hit = await is_cached(probe.describe(key.removeprefix(VENUE_PREFIX), type="place"))
        else:
            hit = await is_cached(probe.describe(key, type="artist"))
        est.resolve += 0 if hit else 1
    if not keys:
        est.stored.append("resolve")
    if state.unconfirmed:
        est.warnings.append(
            "unconfirmed past acts (the run will stop here): "
            + ", ".join(repr(a) for a in state.unconfirmed)
        )

    if state.fresh or not state.profile_stored:
        est.profile = PROFILE_BUDGET
    else:
        est.stored.append("profile")

    if state.fresh or not state.run_ok:
        words = [
            w
            for w in (*seed.constraints.include_tags, *seed.constraints.exclude_tags)
            if not w.strip().startswith(candidates_mod.TAG_PREFIX)
        ]
        for word in words:
            hit = await is_cached(probe.find_tags(word, limit=GENRE_TAG_LIMIT))
            est.tags += 0 if hit else 1
        est.candidates = CANDIDATES_BUDGET
        est.signals = SIGNALS_BUDGET
        est.briefs = BRIEFS
    else:
        est.stored.append("run")
        if state.run is not None and state.run.compare is not None:
            est.stored.append("compare")
        missing = [e for e in state.top if e not in state.briefed]
        est.briefs = len(missing)
        if not missing:
            est.stored.append("briefs")
    return est


async def estimate(deps: Deps, seeds: Sequence[Seed]) -> Estimate:
    """The uncached Qloo calls a run would make, per seed. Makes no Qloo call itself."""
    out = []
    for seed in seeds:
        out.append(await estimate_seed(deps, seed, await inspect(deps, seed)))
    return Estimate(out)


def format_estimate(est: Estimate) -> str:
    head = (
        f"{'seed':<18}{'resolve':>8}{'profile':>8}{'tags':>6}{'cand.':>7}{'signals':>8}"
        f"{'briefs':>7}{'total':>7}  stored"
    )
    lines = [
        "Qloo call estimate: uncached calls only (cache lookups, nothing spent); budgets "
        f"profile ≤ {PROFILE_BUDGET}, candidates ≤ {CANDIDATES_BUDGET}, signals ≤ "
        f"{SIGNALS_BUDGET}, briefs ≤ {BRIEFS}.",
        head,
    ]
    for s in est.seeds:
        lines.append(
            f"{s.slug:<18}{s.resolve:>8}{s.profile:>8}{s.tags:>6}{s.candidates:>7}"
            f"{s.signals:>8}{s.briefs:>7}{s.total:>7}  {', '.join(s.stored) or '-'}"
        )
        lines.extend(f"  warning: {w}" for w in s.warnings)
    lines.append(f"{'overall':<18}{'':>44}{est.total:>7}")
    return "\n".join(lines)


# --- quota -----------------------------------------------------------------------------------


def parse_remaining(status_output: str) -> int | None:
    """The remaining request count if `qloo setup --status` prints one (it does not on 0.1.26)."""
    match = _REMAINING.search(status_output)
    if match is None:
        return None
    return int((match.group(1) or match.group(2)).replace(",", ""))


def remaining_from_status(qloo_bin: str | None = None) -> int | None:
    binary = qloo_bin or QlooConfig.from_env().qloo_bin
    if binary is None:
        return None
    try:
        proc = subprocess.run(
            [binary, "setup", "--status"],
            env=dict(os.environ),
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return parse_remaining(proc.stdout)


def reserve_problem(remaining: int, estimated: int, reserve: int) -> str | None:
    """Why the run must not start, or None when `remaining - estimated >= reserve`."""
    left = remaining - estimated
    if left < reserve:
        return (
            f"aborting before any call: {remaining} remaining - {estimated} estimated = {left}, "
            f"below the judging reserve of {reserve} ({RESERVE_ENV})"
        )
    return None


def read_reserve() -> int:
    raw = os.environ.get(RESERVE_ENV, "").strip()
    return int(raw) if raw else DEFAULT_RESERVE


# --- the pipeline ----------------------------------------------------------------------------


@dataclass
class SeedReport:
    slug: str
    counts: dict[str, int]
    notes: list[str] = field(default_factory=list)


class SeedFailed(SystemExit):
    def __init__(self, slug: str, message: str) -> None:
        super().__init__(f"seed {slug!r}: {message}")


def _venue_fields(seed: Seed) -> dict[str, Any]:
    venue = seed.venue
    return {
        **venue.model_dump(mode="json", exclude={"past_shows"}),
        "past_shows": [s.model_dump(mode="json") for s in venue.past_shows],
    }


async def _ensure_venue(deps: Deps, seed: Seed, state: SeedState, notes: list[str]) -> None:
    venue_id = venue_id_for(seed.slug)
    if state.venue_stored and not state.venue_changed:
        return
    async with deps.sessions.begin() as session:
        if state.venue_changed:
            row = await session.get(VenueRow, venue_id)
            assert row is not None
            # Everything downstream was built on the old venue: resolutions, profile, run.
            await session.delete(row)
            await session.flush()
            notes.append("venue changed in the seed file; rebuilt from scratch")
        session.add(VenueRow(id=venue_id, **_venue_fields(seed)))


async def _resolve_acts(deps: Deps, seed: Seed, notes: list[str]) -> None:
    venue_id = venue_id_for(seed.slug)
    resolutions = await resolve(venue_id, deps)
    failed = []
    for item in resolutions.items:
        if item.confirmed:
            continue
        if item.kind == "venue":
            # Seed venues are fictional rooms: a same-named Qloo place is not ours.
            await confirm(venue_id, ConfirmBody(query=item.query, chosen_entity_id=None), deps)
            notes.append(f"venue {seed.venue.name!r} kept as location-only (not a Qloo place)")
            continue
        names = [f"{c.name} ({c.entity_id})" for c in item.candidates[:5]] or ["no candidates"]
        failed.append(f"{item.query!r} did not auto-confirm; candidates: {', '.join(names)}")
    if failed:
        raise SeedFailed(
            seed.slug,
            "past acts must resolve unambiguously; pick other acts in seeds/venues.json:\n  "
            + "\n  ".join(failed),
        )


async def _ensure_profile(deps: Deps, seed: Seed, state: SeedState) -> None:
    if state.profile_stored and not state.fresh:
        return
    await profile(venue_id_for(seed.slug), deps)


async def _ensure_run(deps: Deps, seed: Seed, state: SeedState, notes: list[str]) -> None:
    venue_id, run_id = venue_id_for(seed.slug), run_id_for(seed.slug)
    if state.run_ok and not state.fresh:
        return
    async with deps.sessions.begin() as session:
        stale = await session.get(RunRow, run_id)
        if stale is not None:
            notes.append(f"previous run ended {stale.status!r}; rerun")
            await session.delete(stale)
    async with deps.sessions() as session:
        target = await venue_for_run(session, venue_id)
    out = await execute_run(deps, run_id, target, seed.constraints, seed.message)
    if out.status != "ok" or out.result is None or not out.result.shortlist:
        detail = out.result.error if out.result and out.result.error else None
        if out.result and out.result.ask:
            detail = f"the agent asked: {out.result.ask.question}"
        raise SeedFailed(
            seed.slug,
            f"agent run ended {out.status!r} ({detail or 'empty shortlist'}); "
            "fix the seed and rerun (the run is redone next time)",
        )


async def _set_seed_mark(deps: Deps, run_id: str, slug: str | None) -> None:
    async with deps.sessions.begin() as session:
        row = await session.get(RunRow, run_id)
        assert row is not None
        row.is_seed = slug is not None
        row.seed_slug = slug


async def _ensure_compare_and_briefs(deps: Deps, seed: Seed) -> None:
    run_id = run_id_for(seed.slug)
    state = await inspect(deps, seed)
    assert state.run is not None
    missing = [e for e in state.top if e not in state.briefed]
    if state.run.compare is None or missing:
        # The seed mark makes these reads refuse to compute; lift it while we fill them.
        await _set_seed_mark(deps, run_id, None)
    compare = await compare_for(deps, run_id)
    if compare.error:
        raise SeedFailed(seed.slug, f"compare was unusable and not cached: {compare.error}")
    for entity_id in state.top:
        brief = await brief_for(deps, run_id, entity_id)
        if brief.error:
            raise SeedFailed(
                seed.slug, f"brief for {brief.name!r} was unusable and not cached: {brief.error}"
            )
    await _set_seed_mark(deps, run_id, seed.slug)


async def counts(deps: Deps, seed: Seed) -> dict[str, int]:
    """Row counts behind one seed: the idempotency check compares these across runs."""
    venue_id, run_id = venue_id_for(seed.slug), run_id_for(seed.slug)
    async with deps.sessions() as session:

        async def count(stmt: Any) -> int:
            return int(await session.scalar(stmt) or 0)

        return {
            "venues": await count(select(func.count()).where(VenueRow.id == venue_id)),
            "resolutions": await count(
                select(func.count()).where(ResolutionRow.venue_id == venue_id)
            ),
            "crowd_profiles": await count(
                select(func.count()).where(CrowdProfileRow.venue_id == venue_id)
            ),
            "runs": await count(
                select(func.count()).where(RunRow.id == run_id, RunRow.is_seed.is_(True))
            ),
            "candidates": await count(select(func.count()).where(CandidateRow.run_id == run_id)),
            "briefs": await count(
                select(func.count()).where(
                    CandidateRow.run_id == run_id, CandidateRow.brief.is_not(None)
                )
            ),
            "qloo_calls": await count(select(func.count()).where(QlooCallRow.run_id == run_id)),
        }


async def precompute_seed(deps: Deps, seed: Seed) -> SeedReport:
    notes: list[str] = []
    try:
        state = await inspect(deps, seed)
        await _ensure_venue(deps, seed, state, notes)
        await _resolve_acts(deps, seed, notes)
        await _ensure_profile(deps, seed, state)
        await _ensure_run(deps, seed, state, notes)
        await _ensure_compare_and_briefs(deps, seed)
    except HTTPException as exc:
        raise SeedFailed(seed.slug, f"HTTP {exc.status_code}: {exc.detail}") from None
    return SeedReport(seed.slug, await counts(deps, seed), notes)


async def precompute(deps: Deps, seeds: Sequence[Seed]) -> list[SeedReport]:
    """Run every seed to completion, in file order; the first failure stops the batch."""
    reports = []
    for seed in seeds:
        print(f"\n== {seed.slug}: {seed.title}")
        report = await precompute_seed(deps, seed)
        for note in report.notes:
            print(f"  note: {note}")
        print("  rows: " + ", ".join(f"{k}={v}" for k, v in report.counts.items()))
        reports.append(report)
    return reports


# --- CLI -------------------------------------------------------------------------------------


def load_env_file(path: Path = ENV_FILE) -> None:
    """`KEY=value` lines into the environment, never overriding what is already set."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if value[:1] in {'"', "'"} and value.endswith(value[0]):
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].strip()
        if value and value != "...":
            os.environ.setdefault(key.strip(), value)


async def make_deps() -> Deps:
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set (put it in ../.env.local or the environment)")
    return await Deps.create(url)


def confirm_spend(estimated: int) -> bool:
    answer = input(f"Spend up to {estimated} Qloo calls now? [y/N] ").strip().lower()
    return answer in {"y", "yes"}


async def run(args: argparse.Namespace, seeds: Sequence[Seed]) -> int:
    deps = await make_deps()
    try:
        est = await estimate(deps, seeds)
        print(format_estimate(est))
        if args.dry_run:
            print("dry run: no Qloo call made")
            return 0
        if est.total > 0:
            remaining = args.remaining
            if remaining is None:
                remaining = remaining_from_status()
            if remaining is None:
                print(
                    "`qloo setup --status` does not report the remaining monthly quota: pass "
                    "--remaining N (requests left this month)",
                    file=sys.stderr,
                )
                return EXIT_NO_REMAINING
            problem = reserve_problem(remaining, est.total, args.reserve)
            if problem:
                print(problem, file=sys.stderr)
                return EXIT_RESERVE
            print(f"{remaining} requests remaining; {remaining - est.total} after this run.")
        if not args.yes and not confirm_spend(est.total):
            print("aborted before any call")
            return EXIT_ABORTED
        reports = await precompute(deps, seeds)
        print(f"\n{len(reports)} seed{'s' if len(reports) != 1 else ''} ready.")
        return 0
    finally:
        await deps.aclose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dry-run", action="store_true", help="print the estimate; no Qloo call")
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    parser.add_argument(
        "--remaining", type=int, help="requests left this month (when the harness won't say)"
    )
    parser.add_argument(
        "--reserve",
        type=int,
        default=None,
        help=f"calls to leave for judging (default {RESERVE_ENV} or {DEFAULT_RESERVE})",
    )
    parser.add_argument("--seeds", type=Path, default=SEEDS_FILE, help="seeds file")
    parser.add_argument(
        "--seed", action="append", dest="only", metavar="SLUG", help="only this seed (repeatable)"
    )
    parser.add_argument(
        "--no-record", action="store_true", help="do not set QLOO_RECORD=1 for this run"
    )
    args = parser.parse_args(argv)

    load_env_file()
    if args.reserve is None:
        args.reserve = read_reserve()
    if not args.no_record:
        os.environ["QLOO_RECORD"] = "1"
    seeds = load_seeds(args.seeds)
    if args.only:
        unknown = set(args.only) - {s.slug for s in seeds}
        if unknown:
            parser.error(f"unknown seed(s) {sorted(unknown)}; known: {[s.slug for s in seeds]}")
        seeds = [s for s in seeds if s.slug in set(args.only)]
    return asyncio.run(run(args, seeds))


if __name__ == "__main__":
    sys.exit(main())
