"""Signal gathering over synthetic envelopes (the harness is never spawned)."""

from __future__ import annotations

import copy
import datetime as dt
import json
from collections.abc import Callable
from typing import Any

import pytest
from conftest import FIXTURES_DIR, RouterHarness

from draw.qloo import QlooResult, exec_client
from draw.qloo.config import QlooConfig
from draw.qloo.exec_client import QlooExec
from draw.ranking.signals import (
    DEFAULT_MAX_CALLS,
    DEFAULT_TOP_N,
    TRENDS_BATCH,
    SignalSet,
    calls_for,
    gather_signals,
    local_heat_of,
    top_n_within,
)
from draw.schemas import Candidate, Constraints, CrowdProfile, EvidenceRef, Signal

REGION = "New York, United States"
TODAY = dt.date(2026, 10, 20)


def fixture(workflow: str, name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES_DIR / workflow / f"{name}.json").read_text("utf-8"))
    return data


def candidate(i: int, *, popularity: float = 0.9995, affinity: float = 0.8) -> Candidate:
    return Candidate(
        entity_id=f"ID-C{i}",
        name=f"Synthetic Act {i}",
        popularity=popularity,
        affinity=affinity,
        tags=[],
        evidence=[EvidenceRef(item_ref=f"ID-C{i}", call_id="00000000-0000-0000-0000-00000000c000")],
    )


def candidates(n: int) -> list[Candidate]:
    return [candidate(i) for i in range(n)]


def profile(*signals: str) -> CrowdProfile:
    return CrowdProfile(
        venue_id="synthetic-room-new-york",
        signals=[Signal(entity_id=s, weight=1.0 - i / 10) for i, s in enumerate(signals)],
        genres=[],
        related_artists=[],
        places=[],
        films=[],
        brands=[],
        evidence=[],
        data_quality="ok",
    )


PROFILE = profile("ID-A", "ID-B", "ID-C")
CONSTRAINTS = Constraints(
    include_tags=[],
    exclude_tags=[],
    size_tier="established",
    exclude_recent_months=0,
    max_candidates=40,
)


def trends_for(ids: list[str], *, slope: float = 0.4) -> dict[str, Any]:
    """A trends envelope whose `series` covers `ids`, each rising by `slope` over the window."""
    envelope = copy.deepcopy(fixture("trends", "three_artists_last_12_months"))
    template = envelope["series"][0]
    series = []
    for entity_id in ids:
        item = copy.deepcopy(template)
        item["entity"]["entityId"] = item["entity"]["input"] = entity_id
        points = item["points"]  # newest first in the recording
        n = len(points)
        for k, point in enumerate(points):
            point["population_percentile"] = round(0.5 + slope * (n - 1 - k) / (n - 1), 4)
        series.append(item)
    envelope["series"] = series
    envelope["result_count"] = len(series)
    return envelope


def forced(envelope: dict[str, Any], status: str) -> dict[str, Any]:
    out = copy.deepcopy(envelope)
    out["status"] = status
    out["summary"] = f"Forced {status} for the test."
    if status == "error":
        out["error"] = {"code": "QLOO_HTTP", "retryable": False, "recovery": "Try later."}
        out["results"], out["result_count"] = [], 0
    return out


@pytest.fixture
def routed(monkeypatch: pytest.MonkeyPatch) -> Callable[..., RouterHarness]:
    def make(
        *,
        compare: dict[str, Any] | None = None,
        heat: dict[str, Any] | None = None,
        trends: Callable[[list[str]], dict[str, Any]] | None = None,
    ) -> RouterHarness:
        def router(workflow: str, payload: dict[str, Any]) -> dict[str, Any]:
            if workflow == "compare_audiences":
                return compare or fixture("compare_audiences", "two_artists")
            if workflow == "where_popular":
                return heat or fixture("where_popular", "radiohead_new_york_ny")
            if workflow == "trends":
                return (trends or trends_for)(payload["entities"])
            raise AssertionError(f"unexpected workflow {workflow}")

        fake = RouterHarness(router)
        monkeypatch.setattr(exec_client, "create_subprocess_exec", fake)
        return fake

    return make


def client() -> QlooExec:
    return QlooExec(QlooConfig(qloo_bin="/fake/qloo"))


async def run(cands: list[Candidate], **kwargs: Any) -> SignalSet:
    kwargs.setdefault("qloo", client())
    kwargs.setdefault("profile", PROFILE)
    kwargs.setdefault("constraints", CONSTRAINTS)
    return await gather_signals(cands, region=REGION, today=TODAY, **kwargs)


# --- call count ------------------------------------------------------------------------


async def test_fifteen_candidates_cost_at_most_33_calls_cold(
    routed: Callable[..., RouterHarness],
) -> None:
    fake = routed()
    result = await run(candidates(DEFAULT_TOP_N))
    assert fake.spawns == result.calls == 33 == DEFAULT_MAX_CALLS
    assert fake.workflows().count("compare_audiences") == 15
    assert fake.workflows().count("where_popular") == 15
    assert fake.workflows().count("trends") == 3
    assert len(result.by_id) == 15
    assert all(
        s.past_fit.value is not None
        and s.local_heat.value is not None
        and s.momentum.value is not None
        for s in result.by_id.values()
    )


async def test_second_run_is_all_cached(routed: Callable[..., RouterHarness]) -> None:
    fake = routed()
    qloo = client()
    await run(candidates(DEFAULT_TOP_N), qloo=qloo)
    second = await run(candidates(DEFAULT_TOP_N), qloo=qloo)
    assert fake.spawns == 33
    assert second.calls == 33  # gateway calls, every one a cache hit
    assert all(s.past_fit.call_id for s in second.by_id.values())


async def test_only_top_n_get_signals(routed: Callable[..., RouterHarness]) -> None:
    fake = routed()
    cands = candidates(20)
    result = await run(cands)
    assert fake.spawns == 33
    assert len(result.by_id) == 20
    tail = result.by_id[cands[19].entity_id]
    assert tail.past_fit.value is None and tail.past_fit.call_id is None
    assert any("top 15 of 20" in n for n in result.notes)


async def test_budget_shrinks_n(routed: Callable[..., RouterHarness]) -> None:
    fake = routed()
    result = await run(candidates(DEFAULT_TOP_N), max_calls=10)
    # 4 candidates need 4 + 4 + 1 = 9 calls; 5 would need 11.
    assert fake.spawns == 9
    assert sum(1 for s in result.by_id.values() if s.past_fit.call_id) == 4


def test_calls_for_matches_the_budget_formula() -> None:
    assert calls_for(15) == 33
    assert calls_for(5) == 11 and calls_for(6) == 14
    assert calls_for(0) == 0
    assert top_n_within(15, 33) == 15
    assert top_n_within(15, 32) == 14
    assert top_n_within(15, 10) == 4
    assert top_n_within(15, 0) == 0


async def test_max_candidates_bounds_n(routed: Callable[..., RouterHarness]) -> None:
    fake = routed()
    constraints = CONSTRAINTS.model_copy(update={"max_candidates": 3})
    await run(candidates(DEFAULT_TOP_N), constraints=constraints)
    assert fake.workflows().count("compare_audiences") == 3


# --- trends batching --------------------------------------------------------------------


async def test_trends_batched_by_five_over_six_months(
    routed: Callable[..., RouterHarness],
) -> None:
    fake = routed()
    cands = candidates(12)
    result = await run(cands)
    sent = fake.inputs_for("trends")
    assert [len(i["entities"]) for i in sent] == [5, 5, 2]
    assert all(len(i["entities"]) <= TRENDS_BATCH for i in sent)
    assert sent[0]["entities"] == [c.entity_id for c in cands[:5]]
    assert sent[0]["entity_type"] == "artist"
    assert sent[0]["start_date"] == "2026-04-20" and sent[0]["end_date"] == "2026-10-20"
    # Every candidate in a batch shares that batch's call_id.
    ids = {result.by_id[c.entity_id].momentum.call_id for c in cands[:5]}
    assert len(ids) == 1 and ids != {None}
    assert result.by_id[cands[5].entity_id].momentum.call_id not in ids


async def test_momentum_is_direction_normalised(routed: Callable[..., RouterHarness]) -> None:
    routed(trends=lambda ids: trends_for(ids, slope=0.4))
    rising = (await run(candidates(1))).by_id["ID-C0"].momentum.value
    routed(trends=lambda ids: trends_for(ids, slope=-0.4))
    falling = (await run(candidates(1))).by_id["ID-C0"].momentum.value
    routed(trends=lambda ids: trends_for(ids, slope=0.0))
    flat = (await run(candidates(1))).by_id["ID-C0"].momentum.value
    assert rising == pytest.approx(0.7) and falling == pytest.approx(0.3)
    assert flat == pytest.approx(0.5)


async def test_entity_missing_from_series_is_none(routed: Callable[..., RouterHarness]) -> None:
    routed(trends=lambda ids: trends_for(ids[:1]))
    result = await run(candidates(2))
    assert result.by_id["ID-C0"].momentum.value is not None
    assert result.by_id["ID-C1"].momentum.value is None
    assert result.by_id["ID-C1"].momentum.call_id  # the call happened; Qloo had no series


# --- values ------------------------------------------------------------------------------


async def test_past_fit_is_tag_overlap_with_top_two_past_acts(
    routed: Callable[..., RouterHarness],
) -> None:
    fake = routed()
    result = await run(candidates(1))
    (sent,) = fake.inputs_for("compare_audiences")
    assert sent["group_a"] == ["ID-C0"]
    assert sent["group_b"] == ["ID-A", "ID-B"]  # best-selling first; the third act is left out
    env = fixture("compare_audiences", "two_artists")
    shared = {t["tag_id"] for t in env["results"]["tags"]}
    union = shared | {t["tag_id"] for k in ("a", "b") for t in env["results"][k]}
    value = result.by_id["ID-C0"].past_fit.value
    assert value == pytest.approx(len(shared) / len(union))
    assert value is not None and 0.0 < value < 1.0


async def test_local_heat_is_mean_heatmap_affinity_rank(
    routed: Callable[..., RouterHarness],
) -> None:
    fake = routed()
    result = await run(candidates(1))
    (sent,) = fake.inputs_for("where_popular")
    assert sent == {"entity": "ID-C0", "entity_type": "artist", "within": REGION, "limit": 10}
    points = fixture("where_popular", "radiohead_new_york_ny")["results"]
    mean = sum(p["query"]["affinity_rank"] for p in points) / len(points)
    assert result.by_id["ID-C0"].local_heat.value == pytest.approx(mean)


def test_local_heat_ignores_the_per_call_normalised_affinity() -> None:
    # Qloo rescales `affinity` to 1.0 … 0.0 inside every heatmap, so its mean is 0.5 for any
    # act; only `affinity_rank` tells two acts apart.
    def heatmap(rank: float) -> QlooResult:
        envelope = forced(fixture("where_popular", "radiohead_new_york_ny"), "ok")
        envelope["results"] = [
            {"query": {"affinity": 1 - i / 7, "affinity_rank": rank}, "location": {}}
            for i in range(8)
        ]
        return QlooResult.model_validate(
            {**envelope, "call_id": "call-heat", "workflow": "where_popular"}
        )

    assert local_heat_of(heatmap(0.95)) == pytest.approx(0.95)
    assert local_heat_of(heatmap(0.65)) == pytest.approx(0.65)


async def test_months_since_booked_travels_with_signals(
    routed: Callable[..., RouterHarness],
) -> None:
    routed()
    booked = {"ID-C0": dt.date(2026, 7, 21), "ID-C1": dt.date(2024, 1, 1)}
    result = await run(candidates(3), booked=booked)
    assert result.by_id["ID-C0"].months_since_booked == 2  # 21 Jul → 20 Oct: not yet 3 months
    assert result.by_id["ID-C1"].months_since_booked == 33
    assert result.by_id["ID-C2"].months_since_booked is None


# --- status branches --------------------------------------------------------------------


@pytest.mark.parametrize("status", ["partial", "degraded", "error"])
async def test_non_ok_compare_gives_none_with_call_id(
    routed: Callable[..., RouterHarness], status: str
) -> None:
    routed(compare=forced(fixture("compare_audiences", "two_artists"), status))
    result = await run(candidates(1))
    signal = result.by_id["ID-C0"].past_fit
    assert signal.value is None
    assert signal.status == status
    assert signal.call_id if status != "error" else True
    assert any(n.startswith("compare_audiences[") for n in result.notes)
    # The other signals are unaffected.
    assert result.by_id["ID-C0"].local_heat.value is not None


async def test_empty_heatmap_gives_none(routed: Callable[..., RouterHarness]) -> None:
    empty = forced(fixture("where_popular", "radiohead_new_york_ny"), "ok")
    empty["results"], empty["result_count"] = [], 0
    routed(heat=empty)
    result = await run(candidates(1))
    assert result.by_id["ID-C0"].local_heat.value is None
    assert result.by_id["ID-C0"].local_heat.status == "empty"


async def test_non_ok_trends_batch_gives_none_for_the_whole_batch(
    routed: Callable[..., RouterHarness],
) -> None:
    routed(trends=lambda ids: forced(trends_for(ids), "degraded"))
    result = await run(candidates(6))
    assert all(s.momentum.value is None for s in result.by_id.values())
    assert all(s.momentum.status == "degraded" for s in result.by_id.values())
    assert sum(1 for n in result.notes if n.startswith("trends[")) == 2


async def test_no_past_acts_skips_compare(routed: Callable[..., RouterHarness]) -> None:
    fake = routed()
    result = await run(candidates(3), profile=profile())
    assert "compare_audiences" not in fake.workflows()
    assert fake.workflows().count("where_popular") == 3
    assert all(
        s.past_fit.value is None and s.past_fit.status is None for s in result.by_id.values()
    )
    assert any("past fit can't tell acts apart" in n for n in result.notes)


async def test_no_region_skips_where_popular(routed: Callable[..., RouterHarness]) -> None:
    fake = routed()
    result = await gather_signals(
        candidates(3), PROFILE, CONSTRAINTS, qloo=client(), region="  ", today=TODAY
    )
    assert "where_popular" not in fake.workflows()
    assert all(s.local_heat.value is None for s in result.by_id.values())


async def test_no_candidates_makes_no_call(routed: Callable[..., RouterHarness]) -> None:
    fake = routed()
    result = await run([])
    assert fake.spawns == 0 and result.by_id == {} and result.calls == 0
