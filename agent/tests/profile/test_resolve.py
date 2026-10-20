"""resolve → confirm → ask, with the harness replaced by synthetic envelopes."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest
from conftest import FIXTURES_DIR, FakeHarness

from draw.profile.resolve import (
    confirm_entity,
    normalise_name,
    resolve_entity,
    resolve_venue,
)
from draw.qloo.config import QlooConfig
from draw.qloo.envelope import QlooError
from draw.qloo.exec_client import QlooExec
from draw.schemas import EntityCandidate, ResolvedEntity

Harness = Callable[..., FakeHarness]


def fixture(workflow: str, name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES_DIR / workflow / f"{name}.json").read_text("utf-8"))
    return data


def printed(*envelopes: dict[str, Any]) -> list[tuple[str, str, int]]:
    return [(json.dumps(env), "", 0) for env in envelopes]


class Client:
    """Builds a real gateway whose harness prints the given envelopes, one per spawn."""

    def __init__(self, harness: Harness) -> None:
        self._harness = harness
        self.fake: FakeHarness

    def __call__(self, *envelopes: dict[str, Any]) -> QlooExec:
        self.fake = self._harness(*printed(*envelopes))
        return QlooExec(QlooConfig(qloo_bin="/fake/qloo"))

    @property
    def calls(self) -> list[tuple[str, dict[str, Any]]]:
        return list(zip(self.fake.workflows(), self.fake.inputs, strict=True))


@pytest.fixture
def client(harness: Harness) -> Client:
    return Client(harness)


def describe_ok(selected: dict[str, Any], *alternatives: dict[str, Any]) -> dict[str, Any]:
    """The `radiohead` fixture's structure with the given selected entity and alternatives."""
    env = fixture("describe", "radiohead")
    outcome = env["resolution"]["outcomes"][0]
    outcome["selected"] = {"type": "urn:entity:artist", **selected}
    outcome["alternatives"] = [{"type": "urn:entity:artist", **a} for a in alternatives]
    env["results"][0].update(entity_id=selected["id"], name=selected["name"])
    return env


def needs_input(candidates: list[dict[str, Any]], kind: str = "ambiguous") -> dict[str, Any]:
    env = fixture("describe", "blue_note_jazz_club")
    issue = env["resolution"]["issues"][0]
    issue["kind"] = kind
    issue["candidates"] = candidates
    return env


# --- normalisation --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("The Synthetic Band", "synthetic band"),
        ("Synth-Pop Collective!", "synthpop collective"),
        ("AC/DC Synthetic", "ac dc synthetic"),
        ("Café Sýnthetic", "cafe synthetic"),
        ("Bands & Synths", "bands and synths"),
    ],
)
def test_normalise_name(a: str, b: str) -> None:
    assert normalise_name(a) == normalise_name(b)


def test_normalise_keeps_inner_the() -> None:
    assert normalise_name("Bring The Synth") == "bringthesynth"


# --- resolve_entity -------------------------------------------------------------------


async def test_exact_match_auto_confirms(client: Client) -> None:
    env = fixture("describe", "radiohead")
    selected = env["resolution"]["outcomes"][0]["selected"]
    resolved = await resolve_entity(selected["name"], "artist", qloo=client(env))

    assert resolved.confirmed is True
    assert resolved.entity_id == selected["id"]
    assert resolved.canonical_name == selected["name"]
    assert resolved.candidates[0].entity_id == selected["id"]
    assert len(resolved.candidates) == 5  # selected + every alternative from the envelope
    assert 0.9 <= resolved.confidence <= 1.0
    assert client.calls == [("describe", {"entity": selected["name"], "type": "artist"})]


async def test_normalised_match_auto_confirms(client: Client) -> None:
    env = describe_ok(
        {"id": "ID-1", "name": "Synth-Pop Collective", "popularity": 0.8},
        {"id": "ID-2", "name": "Another Synthetic Act", "popularity": 0.79},
    )
    resolved = await resolve_entity("synthpop collective", "artist", qloo=client(env))

    assert resolved.confirmed is True
    assert resolved.entity_id == "ID-1"
    assert resolved.canonical_name == "Synth-Pop Collective"
    assert resolved.confidence < 1.0


async def test_the_prefix_auto_confirms(client: Client) -> None:
    env = describe_ok(
        {"id": "ID-1", "name": "The Synthetic Keys", "popularity": 0.7},
        {"id": "ID-2", "name": "Synthetic Locks", "popularity": 0.7},
    )
    resolved = await resolve_entity("Synthetic Keys", "artist", qloo=client(env))

    assert resolved.confirmed is True
    assert resolved.entity_id == "ID-1"


async def test_two_close_candidates_not_confirmed(client: Client) -> None:
    env = describe_ok(
        {"id": "ID-1", "name": "Synthetic Twins", "popularity": 0.9},
        {"id": "ID-2", "name": "Synthetic Twins", "popularity": 0.85},
    )
    resolved = await resolve_entity("Synthetic Twins", "artist", qloo=client(env))

    assert resolved.confirmed is False
    assert resolved.entity_id is None
    assert [c.entity_id for c in resolved.candidates] == ["ID-1", "ID-2"]


async def test_same_name_never_auto_confirms_even_with_popularity_gap(client: Client) -> None:
    env = describe_ok(
        {"id": "ID-1", "name": "Synthetic Twins", "popularity": 1.0},
        {"id": "ID-2", "name": "Synthetic Twins", "popularity": 0.0},
    )
    resolved = await resolve_entity("Synthetic Twins", "artist", qloo=client(env))
    assert resolved.confirmed is False


async def test_no_name_match_not_confirmed(client: Client) -> None:
    env = describe_ok({"id": "ID-1", "name": "Completely Different", "popularity": 0.99})
    resolved = await resolve_entity("Synthetic Query", "artist", qloo=client(env))

    assert resolved.confirmed is False
    assert [c.entity_id for c in resolved.candidates] == ["ID-1"]
    assert resolved.confidence < 0.6


async def test_needs_input_surfaces_candidates(client: Client) -> None:
    env = fixture("describe", "blue_note_jazz_club")
    expected = [c["id"] for c in env["resolution"]["issues"][0]["candidates"]]
    query = env["resolution"]["issues"][0]["input"]

    resolved = await resolve_entity(query, "place", qloo=client(env))

    assert resolved.confirmed is False
    assert resolved.entity_id is None
    assert [c.entity_id for c in resolved.candidates] == expected
    assert all(c.type == "urn:entity:place" for c in resolved.candidates)


async def test_needs_input_with_single_exact_candidate_still_asks(client: Client) -> None:
    env = needs_input([{"id": "ID-1", "name": "Synthetic Solo", "type": "urn:entity:artist"}])
    resolved = await resolve_entity("Synthetic Solo", "artist", qloo=client(env))

    assert resolved.confirmed is False
    assert [c.entity_id for c in resolved.candidates] == ["ID-1"]


async def test_not_found_returns_no_candidates(client: Client) -> None:
    env = fixture("describe", "invalid_id")
    resolved = await resolve_entity("Nobody Synthetic", "artist", qloo=client(env))

    assert resolved.confirmed is False
    assert resolved.candidates == []
    assert resolved.entity_id is None
    assert resolved.confidence == 0.0


async def test_ok_with_no_results_returns_no_candidates(client: Client) -> None:
    env = fixture("find_tags", "nonsense_query_exact")
    resolved = await resolve_entity("zzqx synthetic", "genre", qloo=client(env))

    assert resolved.confirmed is False
    assert resolved.candidates == []


async def test_blank_name_makes_no_call(client: Client) -> None:
    resolved = await resolve_entity("   ", "artist", qloo=client())

    assert resolved.confirmed is False
    assert resolved.candidates == []
    assert client.calls == []


async def test_genre_word_uses_find_tags_and_picks_music_genre(client: Client) -> None:
    env = fixture("find_tags", "jazz")
    music = next(r for r in env["results"] if r["type"] == "urn:tag:genre:music")
    resolved = await resolve_entity(music["name"], "genre", qloo=client(env))

    assert client.calls[0][0] == "find_tags"
    assert resolved.confirmed is True
    assert resolved.entity_id == music["id"]
    assert len(resolved.candidates) == len(env["results"])


async def test_gateway_errors_propagate(client: Client) -> None:
    env = fixture("find_tags", "empty_query")
    with pytest.raises(QlooError):
        await resolve_entity("Synthetic", "genre", qloo=client(env))


# --- resolve_venue --------------------------------------------------------------------


async def test_venue_exact_place_auto_confirms(client: Client) -> None:
    env = describe_ok({"id": "P-1", "name": "Synthetic Hall", "type": "urn:entity:place"})
    resolved = await resolve_venue("Synthetic Hall", "New York", qloo=client(env))

    assert resolved.confirmed is True
    assert resolved.entity_id == "P-1"
    assert client.calls == [("describe", {"entity": "Synthetic Hall", "type": "place"})]


async def test_venue_ambiguous_returns_candidates(client: Client) -> None:
    resolved = await resolve_venue(
        "Synthetic Entity 4",
        "New York",
        qloo=client(fixture("describe", "blue_note_jazz_club")),
    )
    assert resolved.confirmed is False
    assert len(resolved.candidates) == 2


async def test_venue_city_narrows_same_named_places(client: Client) -> None:
    env = describe_ok(
        {
            "id": "P-1",
            "name": "Synthetic Hall",
            "type": "urn:entity:place",
            "popularity": 0.9,
            "address": "1 Synthetic St, Elsewhere",
        },
        {
            "id": "P-2",
            "name": "Synthetic Hall",
            "type": "urn:entity:place",
            "popularity": 0.6,
            "address": "2 Synthetic Ave, New York, NY",
        },
    )
    resolved = await resolve_venue("Synthetic Hall", "New York", qloo=client(env))

    assert resolved.confirmed is True
    assert resolved.entity_id == "P-2"
    assert {c.entity_id for c in resolved.candidates} == {"P-1", "P-2"}


async def test_venue_needs_input_lists_in_city_candidate_first(client: Client) -> None:
    env = needs_input(
        [
            {
                "id": "P-1",
                "name": "Synthetic Hall",
                "type": "urn:entity:place",
                "popularity": 0.9,
                "address": "1 Synthetic St, Elsewhere",
            },
            {
                "id": "P-2",
                "name": "Synthetic Hall",
                "type": "urn:entity:place",
                "popularity": 0.6,
                "address": "2 Synthetic Ave, New York, NY",
            },
        ]
    )
    resolved = await resolve_venue("Synthetic Hall", "New York", qloo=client(env))

    assert resolved.confirmed is False  # needs_input always asks
    assert resolved.entity_id is None
    assert [c.entity_id for c in resolved.candidates] == ["P-2", "P-1"]


async def test_venue_not_found_falls_back_to_synthetic(client: Client) -> None:
    resolved = await resolve_venue(
        "Synthetic Basement", "New York", qloo=client(fixture("describe", "invalid_id"))
    )
    assert resolved.confirmed is True
    assert resolved.entity_id is None
    assert resolved.confidence == 0.0
    assert resolved.candidates == []
    assert resolved.canonical_name == "Synthetic Basement, New York"


# --- confirm_entity -------------------------------------------------------------------


async def test_confirm_entity_marks_choice(client: Client) -> None:
    env = fixture("describe", "blue_note_jazz_club")
    query = env["resolution"]["issues"][0]["input"]
    resolved = await resolve_entity(query, "place", qloo=client(env))
    chosen = resolved.candidates[1]

    confirmed = confirm_entity(resolved, chosen.entity_id)

    assert confirmed.confirmed is True
    assert confirmed.entity_id == chosen.entity_id
    assert confirmed.canonical_name == chosen.name
    assert confirmed.candidates == resolved.candidates
    assert resolved.confirmed is False  # the original is untouched


def test_confirm_entity_rejects_unknown_id() -> None:
    resolved = ResolvedEntity(
        query="q",
        canonical_name="q",
        confidence=0.5,
        candidates=[EntityCandidate(entity_id="A", name="A", type="urn:entity:artist")],
        confirmed=False,
    )
    with pytest.raises(ValueError):
        confirm_entity(resolved, "B")
