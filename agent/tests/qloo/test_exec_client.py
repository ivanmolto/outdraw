"""QlooExec with the harness subprocess mocked to print fixture envelopes."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from conftest import FakeHarness, fixture_text
from tenacity import wait_none

from draw.qloo.budget import MemoryCounter
from draw.qloo.cache import MemoryCache, cache_key
from draw.qloo.config import QlooConfig
from draw.qloo.envelope import BudgetExceeded, QlooEmpty, QlooError, QlooNeedsInput
from draw.qloo.exec_client import QlooExec
from draw.qloo.recording import Recorder

# Assembled at runtime so the gitleaks pre-commit hook does not mistake it for a real key.
FAKE_KEY = "-".join(["fake", "qloo", "key", "0" * 12])

OK = (fixture_text("find_tags", "jazz"), "", 0)
RETRYABLE = (fixture_text("find_tags", "derived_error_retryable"), "", 0)
NON_RETRYABLE = (fixture_text("find_tags", "empty_query"), "", 2)
NEEDS_INPUT = (fixture_text("describe", "blue_note_jazz_club"), "", 0)
EMPTY = (fixture_text("recommend", "derived_empty"), "", 0)
PARTIAL = (fixture_text("recommend", "derived_partial"), "", 0)
DEGRADED = (fixture_text("recommend", "derived_degraded"), "", 0)


def make_client(**kwargs: Any) -> QlooExec:
    kwargs.setdefault("config", QlooConfig(qloo_bin="/fake/qloo"))
    kwargs.setdefault("retry_wait", wait_none())
    return QlooExec(**kwargs)


async def test_ok_call_has_call_id_and_spawns_with_full_env(
    harness: Callable[..., FakeHarness],
) -> None:
    fake = harness(OK)
    client = make_client()
    result = await client.find_tags("jazz", limit=5)
    assert result.status == "ok" and result.result_count == 5
    assert len(result.call_id) == 36 and result.cache_hit is False
    assert result.workflow == "find_tags" and result.latency_ms >= 0
    assert fake.argv == [["/fake/qloo", "exec", "find_tags", "--input-file", fake.argv[0][4]]]
    assert type(fake.env_seen[0]) is dict and fake.env_seen[0] == dict(os.environ)
    assert fake.inputs == [{"query": "jazz", "limit": 5}]


async def test_cache_hit_avoids_a_spawn(harness: Callable[..., FakeHarness]) -> None:
    fake = harness(OK)
    cache = MemoryCache()
    client = make_client(cache=cache)
    first = await client.find_tags("jazz")
    second = await client.find_tags("jazz")
    assert fake.spawns == 1
    assert second.cache_hit is True and first.cache_hit is False
    assert second.call_id != first.call_id, "every call gets its own id, hits included"
    assert second.results == first.results
    assert len(cache) == 1
    assert await cache.get(cache_key("find_tags", {"query": "jazz", "limit": 5}))


async def test_cache_key_is_order_independent_and_workflow_scoped() -> None:
    assert cache_key("find_tags", {"a": 1, "b": 2}) == cache_key("find_tags", {"b": 2, "a": 1})
    assert cache_key("find_tags", {"a": 1}) != cache_key("describe", {"a": 1})


async def test_only_ok_and_partial_are_cached(harness: Callable[..., FakeHarness]) -> None:
    cache = MemoryCache()
    fake = harness(PARTIAL, DEGRADED, NON_RETRYABLE, NEEDS_INPUT)
    client = make_client(cache=cache)
    partial = await client.recommend("artist", signals=["a"])
    assert partial.status == "partial" and len(cache) == 1
    degraded = await client.recommend("artist", signals=["b"])
    assert degraded.status == "degraded" and len(cache) == 1
    with pytest.raises(QlooError):
        await client.find_tags("")
    with pytest.raises(QlooNeedsInput):
        await client.describe("Blue Note", type="place")
    assert len(cache) == 1 and fake.spawns == 4


async def test_retryable_error_retried_then_succeeds(
    harness: Callable[..., FakeHarness],
) -> None:
    fake = harness(RETRYABLE, OK)
    result = await make_client().find_tags("jazz")
    assert result.status == "ok"
    assert fake.spawns == 2


async def test_retryable_error_gives_up_after_three_attempts(
    harness: Callable[..., FakeHarness],
) -> None:
    fake = harness(RETRYABLE, RETRYABLE, RETRYABLE, OK)
    with pytest.raises(QlooError) as info:
        await make_client().find_tags("jazz")
    assert fake.spawns == 3
    assert info.value.code == "QLOO_RATE_LIMIT" and info.value.retryable is True
    assert info.value.recovery.startswith("Wait")


async def test_non_retryable_error_not_retried(harness: Callable[..., FakeHarness]) -> None:
    fake = harness(NON_RETRYABLE, OK)
    with pytest.raises(QlooError) as info:
        await make_client().find_tags("x")
    assert fake.spawns == 1
    assert info.value.code == "TOOL_INPUT" and info.value.retryable is False
    assert info.value.envelope is not None and info.value.envelope.status == "error"


async def test_needs_input_raises_with_candidates_and_is_not_retried(
    harness: Callable[..., FakeHarness],
) -> None:
    fake = harness(NEEDS_INPUT, OK)
    with pytest.raises(QlooNeedsInput) as info:
        await make_client().describe("Blue Note", type="place")
    assert fake.spawns == 1
    expected = [
        c["name"] for c in json.loads(NEEDS_INPUT[0])["resolution"]["issues"][0]["candidates"]
    ]
    assert [c["name"] for c in info.value.candidates] == expected and len(expected) == 2
    assert info.value.issues[0]["kind"] == "ambiguous"


async def test_empty_raises_qloo_empty_and_is_not_retried(
    harness: Callable[..., FakeHarness],
) -> None:
    fake = harness(EMPTY, (fixture_text("find_tags", "nonsense_query_exact"), "", 0))
    client = make_client()
    with pytest.raises(QlooEmpty):
        await client.recommend("artist", signals=["a"])
    with pytest.raises(QlooEmpty):  # ok with zero results behaves like empty
        await client.find_tags("qwzx", semantic=False)
    assert fake.spawns == 2


async def test_budget_cap_raises_before_spawn(harness: Callable[..., FakeHarness]) -> None:
    fake = harness(OK, OK, OK)
    counter = MemoryCounter(cap=2)
    client = make_client(counter=counter)
    await client.find_tags("a")
    await client.find_tags("b")
    with pytest.raises(BudgetExceeded) as info:
        await client.find_tags("c")
    assert fake.spawns == 2
    assert (info.value.used, info.value.cap) == (2, 2)
    # a cache hit costs nothing
    await client.find_tags("a")
    assert await counter.used() == 2


async def test_retries_count_against_the_budget(harness: Callable[..., FakeHarness]) -> None:
    fake = harness(RETRYABLE, OK)
    counter = MemoryCounter(cap=10)
    await make_client(counter=counter).find_tags("jazz")
    assert fake.spawns == 2 and await counter.used() == 2


async def test_concurrency_never_exceeds_limit(harness: Callable[..., FakeHarness]) -> None:
    fake = harness(delay=0.02)
    client = make_client(config=QlooConfig(qloo_bin="/fake/qloo", concurrency=3))
    results = await asyncio.gather(*(client.find_tags(f"q{i}") for i in range(10)))
    assert fake.spawns == 10 and len({r.call_id for r in results}) == 10
    assert fake.max_in_flight == 3


async def test_harness_failure_without_json(harness: Callable[..., FakeHarness]) -> None:
    fake = harness(("", "Usage: qloo exec <workflow>", 2))
    with pytest.raises(QlooError) as info:
        await make_client().find_tags("jazz")
    assert info.value.code == "harness_failed" and info.value.retryable is False
    assert "exit 2" in info.value.recovery and "Usage" in info.value.recovery
    assert fake.spawns == 1


async def test_missing_status_output_becomes_error(harness: Callable[..., FakeHarness]) -> None:
    exit4 = json.dumps(
        {"error": {"code": "QLOO_AUTH", "retryable": False, "recovery": "set the key"}}
    )
    harness((exit4, "", 4))
    with pytest.raises(QlooError) as info:
        await make_client().find_tags("jazz")
    assert info.value.code == "QLOO_AUTH"


async def test_timeout_kills_and_raises(harness: Callable[..., FakeHarness]) -> None:
    harness(OK, delay=0.2)
    client = make_client(config=QlooConfig(qloo_bin="/fake/qloo", timeout_s=0.01))
    with pytest.raises(QlooError) as info:
        await client.find_tags("jazz")
    assert info.value.code == "harness_timeout"


async def test_missing_harness_binary(harness: Callable[..., FakeHarness]) -> None:
    fake = harness(OK)
    with pytest.raises(QlooError) as info:
        await make_client(config=QlooConfig(qloo_bin=None)).find_tags("jazz")
    assert info.value.code == "harness_missing" and fake.spawns == 0


async def test_recording_file_written_without_key(
    harness: Callable[..., FakeHarness], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("QLOO_API_KEY", FAKE_KEY)
    leaky = json.loads(OK[0])
    leaky["execution"]["debug_auth"] = f"Bearer {FAKE_KEY}"  # a leak the redactor must catch
    harness((json.dumps(leaky), "", 0))
    recorder = Recorder(tmp_path / "recordings", secret=os.environ["QLOO_API_KEY"])
    client = make_client(recorder=recorder, run_id="run-1")
    result = await client.find_tags("jazz")
    hit = await client.find_tags("jazz")

    files = sorted((tmp_path / "recordings").rglob("*.json"))
    assert [f.stem for f in files] == sorted([result.call_id, hit.call_id])
    assert files[0].parent.name.count("-") == 2  # <date>/<call_id>.json
    for path in files:
        text = path.read_text("utf-8")
        assert FAKE_KEY not in text
        entry = json.loads(text)
        expected_keys = {"call_id", "ts", "workflow", "request", "envelope", "status", "cache_hit"}
        assert set(entry) == expected_keys
        assert entry["workflow"] == "find_tags" and entry["status"] == "ok"
        assert entry["request"] == {"query": "jazz", "limit": 5}
    by_id = {json.loads(p.read_text())["call_id"]: json.loads(p.read_text()) for p in files}
    assert by_id[result.call_id]["cache_hit"] is False
    assert by_id[hit.call_id]["cache_hit"] is True
    assert by_id[result.call_id]["envelope"]["execution"]["debug_auth"] == "Bearer <redacted>"


async def test_workflow_input_shapes_match_decision_001(
    harness: Callable[..., FakeHarness],
) -> None:
    fake = harness(
        OK,
        (fixture_text("describe", "radiohead"), "", 0),
        (fixture_text("recommend", "artists_two_signals"), "", 0),
        (fixture_text("rank", "five_artists_vs_signals"), "", 0),
        (fixture_text("where_popular", "radiohead_new_york_ny"), "", 0),
        (fixture_text("compare_audiences", "two_artists"), "", 0),
        (fixture_text("audience_demographics", "radiohead"), "", 0),
        (fixture_text("trends", "three_artists_last_12_months"), "", 0),
    )
    c = make_client()
    await c.find_tags("jazz", limit=3, semantic=False)
    await c.describe("Synthetic Entity 1", type="artist")
    await c.recommend(
        "place",
        signals=["A", "B"],
        filter_location="New York, NY",
        include_tags=["urn:tag:x"],
        include_tags_operator="union",
        limit=10,
    )
    await c.rank(["O1", "O2"], option_type="artist", signals=["A"])
    await c.where_popular("A", entity_type="artist", within="New York, NY")
    await c.compare_audiences(["A"], ["B"])
    await c.audience_demographics("A", entity_type="artist")
    await c.trends(["A", "B"], entity_type="artist", start_date="2025-10-20", end_date="2026-10-20")
    assert [a[2] for a in fake.argv] == [
        "find_tags",
        "describe",
        "recommend",
        "rank",
        "where_popular",
        "compare_audiences",
        "audience_demographics",
        "trends",
    ]
    assert fake.inputs == [
        {"query": "jazz", "limit": 3, "semantic": False},
        {"entity": "Synthetic Entity 1", "type": "artist"},
        {
            "target_type": "place",
            "signals": ["A", "B"],
            "filter_location": "New York, NY",
            "include_tags": ["urn:tag:x"],
            "include_tags_operator": "union",
            "limit": 10,
        },
        {"options": ["O1", "O2"], "option_type": "artist", "signals": ["A"]},
        {"entity": "A", "entity_type": "artist", "within": "New York, NY", "limit": 10},
        {"group_a": ["A"], "group_b": ["B"], "limit": 10},
        {"entity": "A", "entity_type": "artist"},
        {
            "entities": ["A", "B"],
            "entity_type": "artist",
            "start_date": "2025-10-20",
            "end_date": "2026-10-20",
            "limit": 10,
        },
    ]


async def test_memory_cache_expires() -> None:
    now = [0.0]
    cache = MemoryCache(ttl_s=10, clock=lambda: now[0])
    await cache.set("k", {"status": "ok"})
    assert await cache.get("k") == {"status": "ok"}
    now[0] = 11
    assert await cache.get("k") is None and len(cache) == 0


def test_real_spawn_under_uvloop(tmp_path: Path) -> None:
    """uvicorn runs on uvloop, whose subprocess API rejects `os.environ` (it wants a dict)."""
    import uvloop

    envelope = tmp_path / "envelope.json"
    envelope.write_text(fixture_text("find_tags", "jazz"), encoding="utf-8")
    fake_qloo = tmp_path / "qloo"
    fake_qloo.write_text(f'#!/bin/sh\ncat "{envelope}"\n', encoding="utf-8")
    fake_qloo.chmod(0o755)
    client = QlooExec(QlooConfig(qloo_bin=str(fake_qloo)), retry_wait=wait_none())

    with asyncio.Runner(loop_factory=uvloop.new_event_loop) as runner:
        result = runner.run(client.find_tags("jazz", limit=5))
    assert result.status == "ok" and result.result_count == 5
