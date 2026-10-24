"""Crowd-profile pipeline over synthetic envelopes (the harness is never spawned)."""

from __future__ import annotations

import copy
import datetime as dt
import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from conftest import FIXTURES_DIR, FakeHarness, FakeProc, load_script

from draw.profile.crowd import (
    DEFAULT_MAX_CALLS,
    build_crowd_profile,
    sell_through_weight,
    venue_region,
    weigh_signals,
)
from draw.profile.resolve import synthetic_venue
from draw.qloo import exec_client
from draw.qloo.budget import MemoryCounter
from draw.qloo.cache import MemoryCache
from draw.qloo.config import QlooConfig
from draw.qloo.envelope import BudgetExceeded
from draw.qloo.exec_client import QlooExec
from draw.schemas import CrowdProfile, PastShow, ResolvedEntity, VenueInput

LISTS = ("genres", "related_artists", "places", "films", "brands")
# Always present with the default budget: 5 fill calls leave room for one demographics read.
BUDGET_NOTE = "Demographics read for 1 of the top 2 signals to stay within 6 calls."


def notes_of(profile: CrowdProfile) -> list[str]:
    return [n for n in profile.notes or [] if n != BUDGET_NOTE]


def fixture(workflow: str, name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES_DIR / workflow / f"{name}.json").read_text("utf-8"))
    return data


# One synthetic envelope per call the pipeline makes, keyed by workflow and target type.
ROUTES: dict[tuple[str, str | None], dict[str, Any]] = {
    ("recommend", "artist"): fixture("recommend", "crowd_artists"),
    ("recommend", "place"): fixture("recommend", "crowd_places"),
    ("recommend", "movie"): fixture("recommend", "crowd_movies"),
    ("recommend", "brand"): fixture("recommend", "crowd_brands"),
    ("compare_audiences", None): fixture("compare_audiences", "crowd_genres"),
    ("audience_demographics", None): fixture("audience_demographics", "crowd_demographics_1"),
}


class RoutedHarness(FakeHarness):
    """A fake harness that answers by workflow and target type, whatever the spawn order."""

    def __init__(self, routes: dict[tuple[str, str | None], dict[str, Any]]) -> None:
        super().__init__([])
        self.routes = routes

    async def __call__(self, *argv: str, **kwargs: Any) -> FakeProc:
        payload = json.loads(Path(argv[argv.index("--input-file") + 1]).read_text())
        key = (argv[2], payload.get("target_type"))
        envelope = self.routes.get(key) or self.routes[(argv[2], None)]
        self.responses = [(json.dumps(envelope), "", 0)]
        return await super().__call__(*argv, **kwargs)


@pytest.fixture
def routed(monkeypatch: pytest.MonkeyPatch) -> Callable[..., RoutedHarness]:
    def make(**overrides: dict[str, Any]) -> RoutedHarness:
        routes = dict(ROUTES)
        for name, envelope in overrides.items():
            workflow, _, target = name.partition("__")
            routes[(workflow, target or None)] = envelope
        fake = RoutedHarness(routes)
        monkeypatch.setattr(exec_client, "create_subprocess_exec", fake)
        return fake

    return make


def client(**kwargs: Any) -> QlooExec:
    kwargs.setdefault("config", QlooConfig(qloo_bin="/fake/qloo"))
    return QlooExec(**kwargs)


def show(act: str, sold: int | None, capacity: int | None = 150) -> PastShow:
    return PastShow(act=act, date=dt.date(2026, 6, 1), tickets_sold=sold, capacity=capacity)


def act(name: str, entity_id: str | None = None, *, confirmed: bool = True) -> ResolvedEntity:
    return ResolvedEntity(
        query=name,
        entity_id=entity_id,
        canonical_name=name,
        confidence=1.0 if confirmed else 0.3,
        candidates=[],
        confirmed=confirmed,
    )


VENUE = VenueInput(
    name="Synthetic Room",
    city="New York",
    country="United States",
    capacity=150,
    past_shows=[show("Act Full", 150), show("Act Mid", 100), show("Act Low", 45)],
)
ACTS = [act("Act Full", "ID-FULL"), act("Act Mid", "ID-MID"), act("Act Low", "ID-LOW")]


# --- weights --------------------------------------------------------------------------


def test_full_house_weighs_more_than_thirty_percent() -> None:
    assert sell_through_weight([show("a", 150)], 150) == 1.0
    assert sell_through_weight([show("a", 45)], 150) == 0.3
    assert sell_through_weight([show("a", 150)], 150) > sell_through_weight([show("a", 45)], 150)


def test_weight_is_clamped_and_defaults() -> None:
    assert sell_through_weight([show("a", 3)], 150) == 0.1  # 2% sell-through -> floor
    assert sell_through_weight([show("a", 400)], 150) == 1.0  # oversold -> ceiling
    assert sell_through_weight([show("a", None)], 150) == 0.5  # unknown -> default
    assert sell_through_weight([show("a", 75, capacity=None)], 150) == 0.5  # venue capacity


def test_weight_averages_an_acts_shows() -> None:
    assert sell_through_weight([show("a", 150), show("a", 75)], 150) == 0.75


def test_signals_ordered_by_weight_and_unconfirmed_skipped() -> None:
    seeds, notes = weigh_signals(VENUE, [*ACTS, act("Act Unknown", confirmed=False)])
    assert [s.entity_id for s in seeds] == ["ID-FULL", "ID-MID", "ID-LOW"]
    assert [s.weight for s in seeds] == [1.0, 0.6667, 0.3]
    assert len(notes) == 1 and "Act Unknown" in notes[0] and "not confirmed" in notes[0]


def test_region_is_city_and_country() -> None:
    assert venue_region(VENUE) == "New York, United States"


# --- the pipeline ---------------------------------------------------------------------


async def test_profile_is_valid_and_fully_evidenced(routed: Callable[..., RoutedHarness]) -> None:
    fake = routed()
    profile = await build_crowd_profile(VENUE, ACTS, qloo=client())

    assert CrowdProfile.model_validate(profile.model_dump(mode="json")) == profile
    assert profile.venue_id == "synthetic-room-new-york"
    assert profile.data_quality == "ok"
    assert profile.notes == [BUDGET_NOTE]
    assert [s.weight for s in profile.signals] == [1.0, 0.6667, 0.3]
    for name in LISTS:
        items = getattr(profile, name)
        assert items, name
        refs = [e for e in profile.evidence if e.item_ref.startswith(f"{name}[")]
        assert [e.item_ref for e in refs] == [f"{name}[{i}]" for i in range(len(items))]
        assert len({e.call_id for e in refs}) == 1  # one call per list
    assert profile.demographics is not None
    assert set(profile.demographics) == {"age", "gender", "entities"}
    assert profile.demographics["entities"] == ["ID-FULL"]
    assert sum(e.item_ref == "demographics" for e in profile.evidence) == 1
    assert len({e.call_id for e in profile.evidence}) == DEFAULT_MAX_CALLS
    assert fake.spawns == DEFAULT_MAX_CALLS


async def test_call_count_is_at_most_six_cold(routed: Callable[..., RoutedHarness]) -> None:
    fake = routed()
    await build_crowd_profile(VENUE, ACTS, qloo=client())
    assert fake.spawns <= 6
    assert sorted(fake.workflows()) == [
        "audience_demographics",
        "compare_audiences",
        "recommend",
        "recommend",
        "recommend",
        "recommend",
    ]


async def test_call_shapes(routed: Callable[..., RoutedHarness]) -> None:
    fake = routed()
    await build_crowd_profile(
        VENUE, ACTS, qloo=client(), place_tags=["urn:tag:synthetic:category:001"]
    )
    by_key = {
        (w, i.get("target_type")): i for w, i in zip(fake.workflows(), fake.inputs, strict=True)
    }
    ids = ["ID-FULL", "ID-MID", "ID-LOW"]
    assert by_key[("recommend", "artist")] == {
        "target_type": "artist",
        "signals": ids,
        "signal_location": "New York, United States",
        "limit": 10,
    }
    assert by_key[("recommend", "place")] == {
        "target_type": "place",
        "signals": ids,
        "filter_location": "New York, United States",
        "include_tags": ["urn:tag:synthetic:category:001"],
        "include_tags_operator": "union",
        "limit": 10,
    }
    assert by_key[("recommend", "movie")] == {"target_type": "movie", "signals": ids, "limit": 10}
    assert by_key[("recommend", "brand")] == {"target_type": "brand", "signals": ids, "limit": 10}
    assert by_key[("compare_audiences", None)] == {
        "group_a": ["ID-FULL"],
        "group_b": ["ID-MID", "ID-LOW"],
        "limit": 10,
    }
    assert by_key[("audience_demographics", None)] == {
        "entity": "ID-FULL",
        "entity_type": "artist",
    }


def _genre_ids(rows: list[dict[str, Any]]) -> list[str]:
    expected: list[str] = []
    for row in rows:
        if ":genre:" in row["subtype"] and row["tag_id"] not in expected:
            expected.append(row["tag_id"])
    return expected


async def test_genres_are_the_shared_scored_genre_tags(
    routed: Callable[..., RoutedHarness],
) -> None:
    routed()
    profile = await build_crowd_profile(VENUE, ACTS, qloo=client())
    shared = ROUTES[("compare_audiences", None)]["results"]["tags"]
    expected = _genre_ids(shared)
    assert expected, "the fixture shares at least one genre"
    assert [g.entity_id for g in profile.genres] == expected[:10]
    # One group's own tags (no score) never sit beside shared ones as a bar with no value.
    assert all(g.affinity is not None and 0 <= g.affinity <= 1 for g in profile.genres)


async def test_genres_fall_back_to_own_tags_when_none_are_shared(
    routed: Callable[..., RoutedHarness],
) -> None:
    envelope = copy.deepcopy(ROUTES[("compare_audiences", None)])
    envelope["results"]["tags"] = [
        t for t in envelope["results"]["tags"] if ":genre:" not in t["subtype"]
    ]
    routed(compare_audiences=envelope)
    profile = await build_crowd_profile(VENUE, ACTS, qloo=client())
    own = _genre_ids([*envelope["results"]["a"], *envelope["results"]["b"]])
    assert own, "the fixture's groups carry genre tags of their own"
    assert [g.entity_id for g in profile.genres] == own[:10]


async def test_single_act_compares_itself(routed: Callable[..., RoutedHarness]) -> None:
    fake = routed()
    profile = await build_crowd_profile(VENUE, ACTS[:1], qloo=client())
    compare = fake.inputs[fake.workflows().index("compare_audiences")]
    assert compare == {"group_a": ["ID-FULL"], "group_b": ["ID-FULL"], "limit": 10}
    assert profile.genres and profile.data_quality == "ok"
    assert profile.notes == []  # one act: only one demographics read is wanted anyway


async def test_synthetic_venue_still_gets_a_profile(routed: Callable[..., RoutedHarness]) -> None:
    routed()
    venue = synthetic_venue("Synthetic Room", "New York")
    profile = await build_crowd_profile(VENUE, ACTS, qloo=client(), venue_entity=venue)
    assert profile.qloo_venue_id is None
    assert profile.related_artists and profile.data_quality == "ok"


async def test_known_venue_id_is_carried(routed: Callable[..., RoutedHarness]) -> None:
    routed()
    venue = act("Synthetic Room", "PLACE-1")
    profile = await build_crowd_profile(
        VENUE, ACTS, qloo=client(), venue_entity=venue, venue_id="venue-42"
    )
    assert profile.qloo_venue_id == "PLACE-1" and profile.venue_id == "venue-42"


async def test_no_confirmed_acts_makes_no_call(routed: Callable[..., RoutedHarness]) -> None:
    fake = routed()
    profile = await build_crowd_profile(VENUE, [act("Act Full", confirmed=False)], qloo=client())
    assert fake.spawns == 0
    assert profile.signals == [] and profile.evidence == []
    assert all(getattr(profile, name) == [] for name in LISTS)
    assert profile.data_quality == "degraded"
    assert len(profile.notes or []) == 2 and "empty" in (profile.notes or [])[1]


async def test_partial_envelope_sets_partial_and_a_note(
    routed: Callable[..., RoutedHarness],
) -> None:
    partial = fixture("recommend", "derived_partial")
    routed(recommend__artist=partial)
    profile = await build_crowd_profile(VENUE, ACTS, qloo=client())
    assert profile.data_quality == "partial"
    assert len(profile.related_artists) == partial["result_count"]
    assert notes_of(profile) == [f"related_artists: {partial['summary']}"]


async def test_empty_places_envelope_leaves_places_empty(
    routed: Callable[..., RoutedHarness],
) -> None:
    routed(recommend__place=fixture("recommend", "derived_empty"))
    profile = await build_crowd_profile(VENUE, ACTS, qloo=client())
    assert profile.places == []
    assert not any(e.item_ref.startswith("places[") for e in profile.evidence)
    assert profile.related_artists and profile.films and profile.brands and profile.genres
    assert profile.data_quality == "degraded"
    assert notes_of(profile) == [f"places: {fixture('recommend', 'derived_empty')['summary']}"]


async def test_degraded_envelope_keeps_rows_and_degrades(
    routed: Callable[..., RoutedHarness],
) -> None:
    degraded = fixture("recommend", "derived_degraded")
    routed(recommend__brand=degraded)
    profile = await build_crowd_profile(VENUE, ACTS, qloo=client())
    assert profile.data_quality == "degraded"
    assert len(profile.brands) == min(10, degraded["result_count"])
    assert notes_of(profile) == [f"brands: {degraded['summary']}"]


async def test_error_and_needs_input_envelopes_degrade_without_raising(
    routed: Callable[..., RoutedHarness],
) -> None:
    routed(
        recommend__movie=fixture("find_tags", "empty_query"),  # non-retryable TOOL_INPUT error
        audience_demographics=fixture("recommend", "invalid_signal_id"),  # needs_input
    )
    profile = await build_crowd_profile(VENUE, ACTS, qloo=client())
    assert profile.films == [] and profile.demographics is None
    assert profile.data_quality == "degraded"
    assert any(n.startswith("films: TOOL_INPUT") for n in profile.notes or [])
    assert any(n.startswith("audience_demographics[Act Full]: ") for n in profile.notes or [])


async def test_budget_exceeded_propagates(routed: Callable[..., RoutedHarness]) -> None:
    fake = routed()
    with pytest.raises(BudgetExceeded):
        await build_crowd_profile(VENUE, ACTS, qloo=client(counter=MemoryCounter(cap=0)))
    assert fake.spawns == 0


async def test_warm_cache_spawns_nothing_and_is_fast(routed: Callable[..., RoutedHarness]) -> None:
    fake = routed()
    qloo = client(cache=MemoryCache())
    cold = await build_crowd_profile(VENUE, ACTS, qloo=qloo)
    started = time.monotonic()
    warm = await build_crowd_profile(VENUE, ACTS, qloo=qloo)
    assert time.monotonic() - started < 5
    assert fake.spawns == DEFAULT_MAX_CALLS
    assert warm.model_dump(exclude={"evidence"}) == cold.model_dump(exclude={"evidence"})
    assert {e.call_id for e in warm.evidence}.isdisjoint({e.call_id for e in cold.evidence})


async def test_seven_calls_reads_two_demographics(routed: Callable[..., RoutedHarness]) -> None:
    fake = routed()
    profile = await build_crowd_profile(VENUE, ACTS, qloo=client(), max_calls=7)
    assert fake.workflows().count("audience_demographics") == 2
    assert profile.demographics is not None
    assert profile.demographics["entities"] == ["ID-FULL", "ID-MID"]
    assert sum(e.item_ref == "demographics" for e in profile.evidence) == 2
    assert profile.notes == []


async def test_six_calls_notes_the_skipped_demographics_read(
    routed: Callable[..., RoutedHarness],
) -> None:
    routed()
    profile = await build_crowd_profile(VENUE, ACTS, qloo=client(), max_calls=6)
    assert profile.notes == [BUDGET_NOTE]


# --- integration ----------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(not os.environ.get("RUN_INTEGRATION"), reason="needs the real Qloo key")
async def test_real_build_for_the_new_york_venue() -> None:
    """One real build (6 calls cold) for the demo room used by scripts/record_crowd.py."""
    from draw.profile.resolve import resolve_entity

    script = load_script("record_crowd")
    qloo = await QlooExec.from_env()
    try:
        confirmed = [
            await resolve_entity(s.act, "artist", qloo=qloo) for s in script.VENUE.past_shows
        ]
        assert all(c.confirmed for c in confirmed)
        started = time.monotonic()
        profile = await build_crowd_profile(script.VENUE, confirmed, qloo=qloo)
        elapsed = time.monotonic() - started
    finally:
        await qloo.aclose()
    assert CrowdProfile.model_validate(profile.model_dump(mode="json")) == profile
    assert profile.data_quality == "ok", profile.notes
    assert all(getattr(profile, name) for name in LISTS)
    assert len({e.call_id for e in profile.evidence}) == DEFAULT_MAX_CALLS
    print(f"real build in {elapsed:.1f}s")
