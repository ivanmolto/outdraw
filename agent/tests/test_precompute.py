"""scripts/precompute.py (T29): the quota guard without a database, and the pipeline on a
migrated Postgres with the fake harness, the fake `qloo mcp` server and the scripted model.

Pipeline tests: idempotency (a second run adds no rows and spawns nothing), the briefs
count, the flags, a dry run that makes zero calls, an estimate that counts only uncached
calls, a reserve breach that aborts before the first call, no run without confirmation or
`--yes`, a seed whose act does not auto-confirm failing loudly before any taste call, and
the seed routes (read-only, ETag, `GET /seeds`).
"""

# test_api's fixtures are imported, so the tests taking them as arguments "redefine" them.
# ruff: noqa: F811

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from conftest import FIXTURES_DIR, RouterHarness, load_script
from fastapi.testclient import TestClient
from tenacity import wait_none

from draw import main as main_mod
from draw.api import seeds as seeds_api
from draw.api.auth import HEADER
from draw.api.deps import Deps
from draw.db.models import RunRow
from draw.qloo import exec_client
from draw.qloo.budget import LLM_PREFIX, MemoryCounter
from draw.qloo.cache import cache_key
from draw.qloo.config import QlooConfig
from draw.qloo.harness import RuntimeVersions
from draw.ranking.evidence import REF_TOP_REASON
from draw.seeds import Seed, load_seeds, run_id_for, venue_id_for

sys.path.insert(0, str(Path(__file__).resolve().parent / "api"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "agent"))
from scripted_model import ScriptedModel, Turn  # noqa: E402
from test_api import (  # noqa: E402, F401  (fixtures)
    FAKE_QLOO_KEY,
    FAKE_SERVER,
    INTERNAL_KEY,
    TABLES,
    _answer,
    _rank,
    _sql,
    db_url,
)

precompute = load_script("precompute")
MakeDeps = Callable[[], Awaitable[Deps]]

# The synthetic describe fixtures: one exact-name entity each, so these names auto-confirm.
ACTS = {
    "Synthetic Entity 1": "crowd_act_norah_jones",
    "Synthetic Entity 3": "crowd_act_radiohead",
    "Synthetic Entity 4": "crowd_act_gregory_porter",
}
RUN_BUDGET = 3 + 33  # candidates + signals
BRIEFS = 5


def genre_word() -> str:
    """The music-genre name in the `find_tags/jazz` fixture: the word that auto-confirms."""
    rows = json.loads((FIXTURES_DIR / "find_tags" / "jazz.json").read_text())["results"]
    return str(next(r["name"] for r in rows if r["type"] == "urn:tag:genre:music"))


GENRE = genre_word()


def make_seed(slug: str, acts: list[str], *, include_tags: list[str] | None = None) -> Seed:
    return Seed.model_validate(
        {
            "slug": slug,
            "title": f"Seed {slug}",
            "description": "test seed",
            "venue": {
                "name": f"Seed Room {slug}",
                "city": "New York",
                "country": "United States",
                "capacity": 150,
                "past_shows": [
                    {"act": act, "date": f"2026-0{i + 1}-12", "tickets_sold": 100, "capacity": 150}
                    for i, act in enumerate(acts)
                ],
            },
            "constraints": {
                "include_tags": include_tags or [],
                "exclude_tags": [],
                "size_tier": "emerging",
                "exclude_recent_months": 0,
                "max_candidates": 20,
            },
            "message": "Seed run.",
        }
    )


SEEDS = [
    make_seed("alpha", ["Synthetic Entity 3", "Synthetic Entity 1"], include_tags=[GENRE]),
    make_seed("beta", ["Synthetic Entity 4", "Synthetic Entity 3"]),
]
AMBIGUOUS = make_seed("gamma", ["Synthetic Entity 3", "Unknown Act"])


# --- the seeds file and the guard, without a database -------------------------------------------


def test_seeds_file_holds_three_venues_with_distinct_acts() -> None:
    seeds = load_seeds()
    assert [s.slug for s in seeds] == ["nola-jazz", "brooklyn-indie", "london-comedy"]
    assert [s.venue.capacity for s in seeds] == [150, 400, 250]
    assert [s.venue.city for s in seeds] == ["New Orleans", "Brooklyn", "London"]
    for seed in seeds:
        assert len(seed.venue.past_shows) >= 6
        acts = {s.act for s in seed.venue.past_shows}
        assert 5 <= len(acts) <= 8, "distinct acts: one describe each, kept small for quota"
        assert all(s.tickets_sold is not None for s in seed.venue.past_shows)
    assert venue_id_for("nola-jazz") == "ven_seed_nola-jazz"
    assert run_id_for("nola-jazz") == "run_seed_nola-jazz"


def test_duplicate_slugs_are_refused(tmp_path: Path) -> None:
    file = tmp_path / "venues.json"
    file.write_text(json.dumps([SEEDS[0].model_dump(mode="json")] * 2))
    with pytest.raises(ValueError, match="duplicate"):
        load_seeds(file)


def test_parse_remaining_reads_a_quota_line_or_none() -> None:
    assert precompute.parse_remaining("Qloo setup\n  Qloo data: ready (config)\n") is None
    assert precompute.parse_remaining("Quota: 7,812 requests remaining this month") == 7812
    assert precompute.parse_remaining("remaining requests: 120") == 120
    assert precompute.parse_remaining("  Requests left: 42\n") == 42


def test_reserve_problem_only_below_the_reserve() -> None:
    assert precompute.reserve_problem(5000, 165, 2000) is None
    assert precompute.reserve_problem(2165, 165, 2000) is None, "exactly the reserve is allowed"
    problem = precompute.reserve_problem(2100, 165, 2000)
    assert problem and "1935" in problem and "2000" in problem


def test_read_reserve_defaults_to_2000(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QLOO_JUDGING_RESERVE", raising=False)
    assert precompute.read_reserve() == 2000
    monkeypatch.setenv("QLOO_JUDGING_RESERVE", "3500")
    assert precompute.read_reserve() == 3500


def test_load_env_file_never_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = tmp_path / ".env.local"
    env.write_text(
        "# comment\nDATABASE_URL=postgres://x  # inline\nQUOTED='a b'\nEMPTY=\nDOTS=...\nKEEP=new\n"
    )
    for name in ("DATABASE_URL", "QUOTED", "EMPTY", "DOTS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("KEEP", "old")
    precompute.load_env_file(env)
    assert os.environ["DATABASE_URL"] == "postgres://x" and os.environ["QUOTED"] == "a b"
    assert "EMPTY" not in os.environ and "DOTS" not in os.environ
    assert os.environ["KEEP"] == "old"
    monkeypatch.delenv("DATABASE_URL")
    monkeypatch.delenv("QUOTED")


class FakeDeps:
    """Stands in for `Deps` where the test replaces the estimate and the pipeline."""

    async def aclose(self) -> None:
        pass


@pytest.fixture
def guarded(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """`main` with a fixed 9-call estimate, a spy on `precompute` and no harness status."""
    calls = {"precompute": 0, "prompted": 0}

    async def fake_deps() -> FakeDeps:
        return FakeDeps()

    async def fake_estimate(deps: Any, seeds: Any) -> Any:
        return precompute.Estimate([precompute.SeedEstimate("alpha", resolve=3, profile=6)])

    async def fake_precompute(deps: Any, seeds: Any) -> list[Any]:
        calls["precompute"] += 1
        return []

    def prompt(estimated: int) -> bool:
        calls["prompted"] += 1
        return False

    monkeypatch.setattr(precompute, "make_deps", fake_deps)
    monkeypatch.setattr(precompute, "estimate", fake_estimate)
    monkeypatch.setattr(precompute, "precompute", fake_precompute)
    monkeypatch.setattr(precompute, "confirm_spend", prompt)
    monkeypatch.setattr(precompute, "remaining_from_status", lambda *a, **k: None)
    monkeypatch.setattr(precompute, "load_env_file", lambda *a, **k: None)
    monkeypatch.delenv("QLOO_JUDGING_RESERVE", raising=False)
    return calls


def test_dry_run_prints_the_estimate_and_runs_nothing(
    guarded: dict[str, int], capsys: pytest.CaptureFixture[str]
) -> None:
    assert precompute.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "alpha" in out and "overall" in out and "dry run" in out
    assert guarded == {"precompute": 0, "prompted": 0}


def test_remaining_is_required_when_the_harness_does_not_report_it(
    guarded: dict[str, int],
) -> None:
    assert precompute.main(["--yes"]) == precompute.EXIT_NO_REMAINING
    assert guarded["precompute"] == 0


def test_reserve_breach_aborts_before_the_first_call(
    guarded: dict[str, int], capsys: pytest.CaptureFixture[str]
) -> None:
    assert precompute.main(["--yes", "--remaining", "2005"]) == precompute.EXIT_RESERVE
    assert guarded["precompute"] == 0 and "aborting before any call" in capsys.readouterr().err
    # 2009 - 9 = 2000: exactly the reserve passes.
    assert precompute.main(["--yes", "--remaining", "2009"]) == 0
    assert guarded["precompute"] == 1


def test_reserve_env_and_flag(guarded: dict[str, int], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QLOO_JUDGING_RESERVE", "100")
    assert precompute.main(["--yes", "--remaining", "109"]) == 0
    assert precompute.main(["--yes", "--remaining", "500", "--reserve", "495"]) == 3
    assert guarded["precompute"] == 1


def test_no_run_without_confirmation_or_yes(guarded: dict[str, int]) -> None:
    assert precompute.main(["--remaining", "9000"]) == precompute.EXIT_ABORTED
    assert guarded == {"precompute": 0, "prompted": 1}
    assert precompute.main(["--remaining", "9000", "--yes"]) == 0
    assert guarded == {"precompute": 1, "prompted": 1}


def test_remaining_from_the_harness_status_counts(
    guarded: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(precompute, "remaining_from_status", lambda *a, **k: 2005)
    assert precompute.main(["--yes"]) == precompute.EXIT_RESERVE
    monkeypatch.setattr(precompute, "remaining_from_status", lambda *a, **k: 9000)
    assert precompute.main(["--yes"]) == 0


def test_unknown_seed_flag_is_a_usage_error(guarded: dict[str, int]) -> None:
    with pytest.raises(SystemExit) as exc:
        precompute.main(["--dry-run", "--seed", "nope"])
    assert exc.value.code == 2


def test_record_env_defaults_on(guarded: dict[str, int], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QLOO_RECORD", "0")
    precompute.main(["--dry-run"])
    assert os.environ["QLOO_RECORD"] == "1"
    monkeypatch.setenv("QLOO_RECORD", "0")
    precompute.main(["--dry-run", "--no-record"])
    assert os.environ["QLOO_RECORD"] == "0"


# --- the pipeline, on Postgres -----------------------------------------------------------------


def fixture(workflow: str, name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES_DIR / workflow / f"{name}.json").read_text())
    return data


def route(workflow: str, payload: dict[str, Any]) -> dict[str, Any]:
    """`qloo exec` answers: describe per act (the venue finds no match), profile fills, tags."""
    if workflow == "describe":
        return fixture("describe", ACTS.get(str(payload.get("entity", "")), "radiohead"))
    if workflow == "find_tags":
        return fixture("find_tags", "jazz")
    if workflow == "recommend":
        target = {"artist": "artists", "place": "places", "movie": "movies"}
        return fixture("recommend", f"crowd_{target.get(payload['target_type'], 'brands')}")
    if workflow == "compare_audiences":
        return fixture("compare_audiences", "crowd_genres")
    if workflow == "audience_demographics":
        return fixture("audience_demographics", "crowd_demographics_1")
    raise AssertionError(f"unexpected qloo exec {workflow}")


def brief_answer(act: str, venue: str) -> str:
    return json.dumps(
        {
            "why_it_fits": [{"text": "Fits the room.", "evidence_ref": REF_TOP_REASON}],
            "night_format": "copied",
            "support_act": None,
            "partners": [],
            "outreach_email": {
                "subject": f"{act} at {venue}",
                "body": f"Hello, {venue} would like to host {act}. Best, bookings.",
            },
        }
    )


class SeedModel(ScriptedModel):
    """Plays the right script for whichever call built it: the agent run, the compare or a
    brief, told apart by the system prompt of the first request."""

    def __init__(self) -> None:
        super().__init__([])

    def next_turn(self, messages: Any) -> Turn:
        if not self.script:
            system = self.system_prompt or ""
            if "you never rank" in system:
                self.script = [self._generate, _rank, _answer]
            elif "booking brief" in system:
                self.script = [self._brief]
            else:
                acts = [{"name": f"LLM Act {i}", "reason": "Fits."} for i in range(10)]
                self.script = [json.dumps({"acts": acts})]
        return super().next_turn(messages)

    def _prompt(self) -> str:
        return "".join(str(b.get("text", "")) for b in self.requests[0][0]["content"])

    def _generate(self, results: Any) -> Turn:
        prompt = self._prompt()
        profile = re.search(r"profile_id (prof_\w+)", prompt)
        constraints = re.search(r"Constraints JSON: (\{.*\})", prompt)
        assert profile and constraints, "the stored profile's id and the constraints are given"
        return [
            (
                "generate_candidates",
                {"profile_id": profile.group(1), "constraints_json": constraints.group(1)},
            )
        ]

    def _brief(self, results: Any) -> Turn:
        prompt = self._prompt()
        venue = re.search(r"Venue: ([^,]+),", prompt)
        act = re.search(r"Act: ([^;]+);", prompt)
        assert venue and act
        return brief_answer(act.group(1), venue.group(1))


class Models:
    def __init__(self) -> None:
        self.built: list[SeedModel] = []

    def __call__(self) -> SeedModel:
        self.built.append(SeedModel())
        return self.built[-1]


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> RouterHarness:
    fake = RouterHarness(route)
    monkeypatch.setattr(exec_client, "create_subprocess_exec", fake)
    return fake


@pytest.fixture
def models() -> Models:
    return Models()


@pytest.fixture
def make_deps(
    db_url: str, models: Models, harness: RouterHarness, monkeypatch: pytest.MonkeyPatch
) -> MakeDeps:
    """A fresh `Deps` over the migrated test database; also what the script's `main` uses."""
    _sql(db_url, f"TRUNCATE {TABLES} CASCADE")
    monkeypatch.setenv("INTERNAL_API_KEY", INTERNAL_KEY)
    monkeypatch.setenv("QLOO_API_KEY", FAKE_QLOO_KEY)
    for name in ("DATABASE_URL", "REDIS_URL", "QLOO_RECORD", "LLM_DAILY_TOKEN_CAP"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(main_mod, "probe_versions", lambda: RuntimeVersions("0.0.0", "v22.19.0"))

    async def make() -> Deps:
        return await Deps.create(
            db_url,
            config=QlooConfig(qloo_bin="/fake/qloo", api_key=FAKE_QLOO_KEY),
            qloo_counter=MemoryCounter(300),
            llm_counter=MemoryCounter(
                10_000_000, prefix=LLM_PREFIX, what="LLM token", charge_over=True
            ),
            model_factory=models,
            mcp_command=[sys.executable, str(FAKE_SERVER)],
            retry_wait=wait_none(),
        )

    monkeypatch.setattr(precompute, "make_deps", make)
    monkeypatch.setattr(precompute, "load_env_file", lambda *a, **k: None)
    monkeypatch.setattr(precompute, "remaining_from_status", lambda *a, **k: None)
    return make


def with_deps(make: MakeDeps, fn: Callable[[Deps], Awaitable[Any]]) -> Any:
    async def go() -> Any:
        deps = await make()
        try:
            return await fn(deps)
        finally:
            await deps.aclose()

    return asyncio.run(go())


def all_counts(make: MakeDeps, seeds: list[Seed]) -> list[dict[str, int]]:
    async def go(deps: Deps) -> list[dict[str, int]]:
        return [await precompute.counts(deps, s) for s in seeds]

    result: list[dict[str, int]] = with_deps(make, go)
    return result


def write_seeds(path: Path, seeds: list[Seed]) -> Path:
    file = path / "venues.json"
    file.write_text(json.dumps([s.model_dump(mode="json") for s in seeds]), encoding="utf-8")
    return file


def run_main(seeds_file: Path, *extra: str) -> int:
    code: int = precompute.main(
        ["--yes", "--remaining", "9000", "--seeds", str(seeds_file), *extra]
    )
    return code


def test_dry_run_estimates_from_cache_lookups_and_spawns_nothing(
    make_deps: MakeDeps,
    harness: RouterHarness,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seeds_file = write_seeds(tmp_path, SEEDS)
    assert precompute.main(["--dry-run", "--seeds", str(seeds_file)]) == 0
    out = capsys.readouterr().out
    assert harness.spawns == 0, "a dry run never spawns the harness"
    assert "alpha" in out and "beta" in out and "dry run" in out
    est = with_deps(make_deps, lambda deps: precompute.estimate(deps, SEEDS))
    alpha, beta = est.seeds
    # alpha: venue + 2 acts to describe, 6 profile, 1 find_tags, 3 + 33 run, 5 briefs.
    assert (alpha.resolve, alpha.profile, alpha.tags) == (3, 6, 1)
    assert (alpha.candidates, alpha.signals, alpha.briefs) == (3, 33, BRIEFS)
    assert alpha.total == 3 + 6 + 1 + RUN_BUDGET + BRIEFS == 51
    assert beta.total == 50 and est.total == 101
    assert alpha.stored == [] and alpha.warnings == []


def test_estimate_counts_only_uncached_calls(make_deps: MakeDeps) -> None:
    async def warm_then_estimate(deps: Deps) -> Any:
        cache = deps.gateways.cache
        key = cache_key("describe", {"entity": "Synthetic Entity 3", "type": "artist"})
        await cache.set(key, fixture("describe", "crowd_act_radiohead"))
        await cache.set(
            cache_key("find_tags", {"query": GENRE, "limit": 5}), fixture("find_tags", "jazz")
        )
        return await precompute.estimate(deps, SEEDS)

    est = with_deps(make_deps, warm_then_estimate)
    alpha, beta = est.seeds
    assert alpha.resolve == 2 and alpha.tags == 0, "one describe and the tag word are cached"
    assert beta.resolve == 2, "beta shares the cached act"
    assert est.total == 101 - 3


def test_precompute_is_idempotent_and_the_second_run_makes_zero_calls(
    make_deps: MakeDeps,
    harness: RouterHarness,
    models: Models,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seeds_file = write_seeds(tmp_path, SEEDS)
    assert run_main(seeds_file) == 0
    assert "2 seeds ready" in capsys.readouterr().out
    first_spawns, first_models = harness.spawns, len(models.built)
    assert first_spawns > 0 and first_models == 2 * (1 + 1 + BRIEFS), "run, compare, 5 briefs"
    first = all_counts(make_deps, SEEDS)
    for report in first:
        assert report["venues"] == 1 and report["crowd_profiles"] == 1 and report["runs"] == 1
        assert report["resolutions"] == 3 and report["briefs"] == BRIEFS
        assert report["candidates"] >= 6 and report["qloo_calls"] > 0

    assert run_main(seeds_file) == 0
    assert harness.spawns == first_spawns, "the second run makes zero Qloo calls"
    assert len(models.built) == first_models, "and zero model calls"
    assert all_counts(make_deps, SEEDS) == first, "identical row counts"
    est = with_deps(make_deps, lambda deps: precompute.estimate(deps, SEEDS))
    assert est.total == 0
    assert est.seeds[0].stored == ["resolve", "profile", "run", "compare", "briefs"]


def test_seed_runs_are_marked_and_served_read_only_with_etag(
    make_deps: MakeDeps,
    db_url: str,
    models: Models,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seeds_file = write_seeds(tmp_path, SEEDS[:1])
    assert run_main(seeds_file) == 0
    run_id = run_id_for("alpha")
    monkeypatch.setattr(seeds_api, "load_seeds", lambda path=seeds_file: load_seeds(path))

    with TestClient(main_mod.create_app(make_deps), headers={HEADER: INTERNAL_KEY}) as client:
        run = client.get(f"/runs/{run_id}")
        assert run.status_code == 200, run.text
        body = run.json()
        assert body["is_seed"] is True and body["seed_slug"] == "alpha"
        etag = run.headers["etag"]
        assert etag.startswith('"') and run.headers["cache-control"] == "public, max-age=3600"
        again = client.get(f"/runs/{run_id}", headers={"If-None-Match": etag})
        assert again.status_code == 304 and again.headers["etag"] == etag and not again.content
        weak = client.get(f"/runs/{run_id}", headers={"If-None-Match": f'W/{etag}, "other"'})
        assert weak.status_code == 304
        stale = client.get(f"/runs/{run_id}", headers={"If-None-Match": '"stale"'})
        assert stale.status_code == 200

        top = body["result"]["shortlist"][0]["entity_id"]
        for path in (
            f"/runs/{run_id}/candidates/{top}/evidence",
            f"/runs/{run_id}/compare",
            f"/runs/{run_id}/candidates/{top}/brief",
            f"/runs/{run_id}/calls",
            "/seeds",
        ):
            res = client.get(path)
            assert res.status_code == 200, (path, res.text)
            assert "etag" in res.headers, path
            cached = client.get(path, headers={"If-None-Match": res.headers["etag"]})
            assert cached.status_code == 304, path
        assert client.get(f"/runs/{run_id}/compare").json()["cached"] is True
        assert client.get(f"/runs/{run_id}/candidates/{top}/brief").json()["cached"] is True

        # Read-only: no pencil hold, no uncached brief, nothing spent.
        built = len(models.built)
        patched = client.patch(f"/runs/{run_id}/candidates/{top}", json={"pencilled": True})
        assert patched.status_code == 403
        assert patched.json()["detail"]["reason"] == "seed_read_only"
        evidence = client.get(f"/runs/{run_id}/candidates/{top}/evidence").json()
        assert evidence["pencilled"] is False
        sixth = body["result"]["shortlist"][5]["entity_id"]
        refused = client.get(f"/runs/{run_id}/candidates/{sixth}/brief")
        assert refused.status_code == 409
        assert refused.json()["detail"]["reason"] == "seed_not_precomputed"
        assert len(models.built) == built, "no model call for a seed"

        seeds_out = client.get("/seeds").json()["seeds"]
        assert [s["slug"] for s in seeds_out] == ["alpha"]
        alpha = seeds_out[0]
        assert alpha["run_id"] == run_id and alpha["venue_id"] == venue_id_for("alpha")
        assert alpha["ready"] is True and alpha["run_status"] == "ok"
        assert alpha["compare_ready"] is True and len(alpha["brief_entity_ids"]) == BRIEFS
        assert alpha["brief_entity_ids"][0] == top and alpha["venue"]["capacity"] == 150

        # A live run has no ETag and keeps its pencil.
        _sql(db_url, f"UPDATE runs SET is_seed = false, seed_slug = NULL WHERE id = '{run_id}'")
        live = client.get(f"/runs/{run_id}")
        assert live.status_code == 200 and "etag" not in live.headers
        hold = client.patch(f"/runs/{run_id}/candidates/{top}", json={"pencilled": True})
        assert hold.status_code == 200
        assert client.get("/seeds").json()["seeds"][0]["ready"] is False


def test_seeds_lists_unbuilt_seeds_as_not_ready(
    make_deps: MakeDeps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeds_file = write_seeds(tmp_path, SEEDS)
    monkeypatch.setattr(seeds_api, "load_seeds", lambda path=seeds_file: load_seeds(path))
    with TestClient(main_mod.create_app(make_deps), headers={HEADER: INTERNAL_KEY}) as client:
        res = client.get("/seeds")
        assert res.status_code == 200
        seeds = res.json()["seeds"]
        assert [s["slug"] for s in seeds] == ["alpha", "beta"]
        for seed in seeds:
            assert not seed["ready"] and not seed["venue_stored"] and seed["run_status"] is None
            assert seed["brief_entity_ids"] == [] and not seed["compare_ready"]
        assert client.get("/seeds", headers={HEADER: ""}).status_code == 401


def test_ambiguous_act_fails_loudly_before_any_taste_call(
    make_deps: MakeDeps, harness: RouterHarness, tmp_path: Path
) -> None:
    seeds_file = write_seeds(tmp_path, [AMBIGUOUS])
    with pytest.raises(SystemExit) as exc:
        run_main(seeds_file)
    message = str(exc.value)
    assert "gamma" in message and "'Unknown Act' did not auto-confirm" in message
    assert "Synthetic Entity 3" in message, "the candidates are listed"
    assert harness.workflows() == ["describe"] * 3, "resolution only; no taste workflow"
    counts = all_counts(make_deps, [AMBIGUOUS])[0]
    assert counts["venues"] == 1 and counts["runs"] == 0 and counts["crowd_profiles"] == 0
    est = with_deps(make_deps, lambda deps: precompute.estimate(deps, [AMBIGUOUS]))
    assert est.seeds[0].resolve == 0 and "Unknown Act" in est.seeds[0].warnings[0]


def test_a_failed_run_is_redone_and_a_changed_venue_rebuilt(
    make_deps: MakeDeps, db_url: str, harness: RouterHarness, tmp_path: Path
) -> None:
    seeds_file = write_seeds(tmp_path, SEEDS[:1])
    assert run_main(seeds_file) == 0
    spawns = harness.spawns

    # The run ended in error last time: it is deleted and rerun; resolve and profile are kept.
    _sql(
        db_url,
        f"UPDATE runs SET status = 'error', is_seed = false WHERE id = '{run_id_for('alpha')}'",
    )
    est = with_deps(make_deps, lambda deps: precompute.estimate(deps, SEEDS[:1]))
    assert est.seeds[0].stored == ["resolve", "profile"] and est.seeds[0].resolve == 0
    assert (est.seeds[0].candidates, est.seeds[0].signals, est.seeds[0].briefs) == (3, 33, 5)
    assert run_main(seeds_file) == 0
    assert harness.spawns == spawns, "resolve and profile were stored: no qloo exec spawn"

    async def marked(deps: Deps) -> bool:
        async with deps.sessions() as session:
            row = await session.get(RunRow, run_id_for("alpha"))
            return bool(row and row.is_seed and row.status == "ok")

    assert with_deps(make_deps, marked) is True

    changed = SEEDS[0].model_copy(
        update={"venue": SEEDS[0].venue.model_copy(update={"capacity": 200})}
    )
    write_seeds(tmp_path, [changed])
    est = with_deps(make_deps, lambda deps: precompute.estimate(deps, [changed]))
    assert est.seeds[0].stored == [] and est.seeds[0].profile == 6, "rebuilt from scratch"
    assert est.seeds[0].resolve == 0, "the describes are cached from the first build"
    assert run_main(seeds_file) == 0
    counts = all_counts(make_deps, [changed])[0]
    assert counts["venues"] == 1 and counts["runs"] == 1 and counts["briefs"] == BRIEFS

    async def capacity(deps: Deps) -> int:
        async with deps.sessions() as session:
            from draw.db.models import VenueRow

            row = await session.get(VenueRow, venue_id_for("alpha"))
            assert row is not None
            return row.capacity

    assert with_deps(make_deps, capacity) == 200


def test_changed_constraints_redo_the_run_but_keep_resolve_and_profile(
    make_deps: MakeDeps, harness: RouterHarness, models: Models, tmp_path: Path
) -> None:
    seeds_file = write_seeds(tmp_path, SEEDS[1:])
    assert run_main(seeds_file) == 0
    spawns, built = harness.spawns, len(models.built)

    changed = SEEDS[1].model_copy(
        update={"constraints": SEEDS[1].constraints.model_copy(update={"max_candidates": 12})}
    )
    write_seeds(tmp_path, [changed])
    est = with_deps(make_deps, lambda deps: precompute.estimate(deps, [changed]))
    assert est.seeds[0].stored == ["resolve", "profile"]
    assert (est.seeds[0].candidates, est.seeds[0].signals, est.seeds[0].briefs) == (3, 33, 5)
    assert run_main(seeds_file) == 0
    assert harness.spawns == spawns, "no qloo exec spawn: resolve and profile were kept"
    assert len(models.built) == built + 1 + 1 + BRIEFS, "a new run, compare and briefs"
    counts = all_counts(make_deps, [changed])[0]
    assert counts["runs"] == 1 and counts["briefs"] == BRIEFS and counts["candidates"] <= 12
    est = with_deps(make_deps, lambda deps: precompute.estimate(deps, [changed]))
    assert est.total == 0


def test_seed_flag_limits_the_batch(
    make_deps: MakeDeps, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seeds_file = write_seeds(tmp_path, SEEDS)
    assert precompute.main(["--dry-run", "--seeds", str(seeds_file), "--seed", "beta"]) == 0
    out = capsys.readouterr().out
    assert "beta" in out and "alpha" not in out
