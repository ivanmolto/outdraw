"""Request bounds (`draw.api.inputs`): each rule, and the 400 the routes answer with.

Offline: the route tests use a `Deps` stand-in that fails on any use, so a 400 proves the
input was refused before anything could reach Qloo or Claude.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError

from draw import main
from draw.api.auth import HEADER
from draw.api.inputs import LIMITS, ConfirmBody, RefineBody, RunBody, VenueUpsert, clean

SHOW = {"act": "Synthetic Act", "date": "2026-05-01", "tickets_sold": 280, "capacity": 300}
VENUE: dict[str, Any] = {
    "name": "Synthetic Room",
    "city": "New York",
    "country": "US",
    "capacity": 300,
    "past_shows": [SHOW],
}
CONSTRAINTS = {
    "include_tags": [],
    "exclude_tags": ["urn:tag:synthetic:genre:2"],
    "size_tier": "emerging",
    "exclude_recent_months": 6,
    "max_candidates": 10,
}
RUN = {"venue_id": "ven_1", "constraints": CONSTRAINTS, "message": "Thursday jazz."}


def first_error(model: type[BaseModel], value: dict[str, Any]) -> str | None:
    """Dotted path of the first error, or None when `value` is valid."""
    try:
        model.model_validate(value)
    except ValidationError as exc:
        return ".".join(str(p) for p in exc.errors()[0]["loc"])
    return None


# --- cleaning -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "cleaned"),
    [
        ("  Synthetic Room  ", "Synthetic Room"),
        ("Synthetic\x00 Room\x07", "Synthetic Room"),
        ("Synthetic\tRoom\r\n", "Synthetic\tRoom"),
        ("line one\nline two", "line one\nline two"),
        ("Synthetic\x1b[31m Room\x7f\x9b", "Synthetic[31m Room"),
        ("\x00\x01 \t\n", ""),
        ("Café — Ünïcode", "Café — Ünïcode"),
        (42, 42),
    ],
)
def test_clean(raw: Any, cleaned: Any) -> None:
    assert clean(raw) == cleaned


def test_cleaned_values_reach_the_model() -> None:
    body = VenueUpsert.model_validate({**VENUE, "name": " \x00Synthetic Room\x07 "})
    assert body.name == "Synthetic Room"
    assert body.venue().name == "Synthetic Room"


# --- each rule --------------------------------------------------------------------------


def test_valid_bodies_pass() -> None:
    assert first_error(VenueUpsert, {**VENUE, "id": "ven_123", "lat": 40.7}) is None
    assert first_error(RunBody, RUN) is None
    assert first_error(RefineBody, {"message": "more emerging acts"}) is None


def test_valid_confirm() -> None:
    venue_key = {"query": "venue:Synthetic Room", "chosen_entity_id": None}
    assert first_error(ConfirmBody, venue_key) is None
    assert first_error(ConfirmBody, {"query": "Synthetic Act", "chosen_entity_id": None}) is None
    pick = {"query": "Synthetic Act", "chosen_entity_id": "urn:entity:x:1"}
    assert first_error(ConfirmBody, pick) is None


@pytest.mark.parametrize(
    ("field", "limit"),
    [("name", LIMITS.name), ("city", LIMITS.city), ("country", LIMITS.country)],
)
def test_venue_text_at_and_over_its_limit(field: str, limit: int) -> None:
    assert first_error(VenueUpsert, {**VENUE, field: "x" * limit}) is None
    assert first_error(VenueUpsert, {**VENUE, field: "x" * (limit + 1)}) == field
    # The limit counts the cleaned value: padding and control characters don't count.
    assert first_error(VenueUpsert, {**VENUE, field: f"  {'x' * limit}\x00 "}) is None


@pytest.mark.parametrize(
    ("label", "patch", "field"),
    [
        ("empty name", {"name": ""}, "name"),
        ("whitespace-only name", {"name": "   "}, "name"),
        ("control-only name", {"name": "\x00\x1b\n"}, "name"),
        ("whitespace-only city", {"city": "\t"}, "city"),
        ("missing country", {"country": None}, "country"),
        ("capacity zero", {"capacity": 0}, "capacity"),
        ("capacity too large", {"capacity": LIMITS.capacity + 1}, "capacity"),
        ("lat out of range", {"lat": 91}, "lat"),
        ("unknown field", {"email": "a@b.c"}, "email"),
        ("bad id", {"id": "../etc"}, "id"),
        ("id too long", {"id": "x" * (LIMITS.id + 1)}, "id"),
    ],
)
def test_venue_rejects(label: str, patch: dict[str, Any], field: str) -> None:
    assert first_error(VenueUpsert, {**VENUE, **patch}) == field, label


def test_past_shows_capped() -> None:
    assert first_error(VenueUpsert, {**VENUE, "past_shows": [SHOW] * LIMITS.past_shows}) is None
    too_many = {**VENUE, "past_shows": [SHOW] * (LIMITS.past_shows + 1)}
    assert first_error(VenueUpsert, too_many) == "past_shows"


@pytest.mark.parametrize(
    ("label", "patch", "field"),
    [
        ("empty act", {"act": ""}, "act"),
        ("whitespace-only act", {"act": " \n "}, "act"),
        ("act too long", {"act": "x" * (LIMITS.act + 1)}, "act"),
        ("bad date", {"date": "May 1st"}, "date"),
        ("negative tickets", {"tickets_sold": -1}, "tickets_sold"),
        ("price too large", {"ticket_price": LIMITS.ticket_price + 1}, "ticket_price"),
        ("gross too large", {"gross": LIMITS.gross + 1}, "gross"),
        ("unknown field", {"promoter": "x"}, "promoter"),
    ],
)
def test_past_show_rejects(label: str, patch: dict[str, Any], field: str) -> None:
    value = {**VENUE, "past_shows": [{**SHOW, **patch}]}
    assert first_error(VenueUpsert, value) == f"past_shows.0.{field}", label


@pytest.mark.parametrize(
    ("label", "value", "field"),
    [
        ("message too long", {**RUN, "message": "x" * (LIMITS.message + 1)}, "message"),
        ("bad venue id", {**RUN, "venue_id": "a b"}, "venue_id"),
        ("unknown field", {**RUN, "email": "a@b.c"}, "email"),
        (
            "too many tags",
            {**RUN, "constraints": {**CONSTRAINTS, "include_tags": ["t"] * (LIMITS.tags + 1)}},
            "constraints.include_tags",
        ),
        (
            "tag too long",
            {**RUN, "constraints": {**CONSTRAINTS, "exclude_tags": ["t" * (LIMITS.tag + 1)]}},
            "constraints.exclude_tags.0",
        ),
        (
            "whitespace-only tag",
            {**RUN, "constraints": {**CONSTRAINTS, "exclude_tags": ["  "]}},
            "constraints.exclude_tags.0",
        ),
        (
            "too many months",
            {**RUN, "constraints": {**CONSTRAINTS, "exclude_recent_months": 61}},
            "constraints.exclude_recent_months",
        ),
    ],
)
def test_run_rejects(label: str, value: dict[str, Any], field: str) -> None:
    assert first_error(RunBody, value) == field, label


def test_run_message_is_optional_and_cleaned() -> None:
    assert RunBody.model_validate({**RUN, "message": " \x00 "}).message == ""
    assert RunBody.model_validate({k: v for k, v in RUN.items() if k != "message"}).message == ""
    at_limit = RunBody.model_validate({**RUN, "message": "x" * LIMITS.message})
    assert len(at_limit.message) == LIMITS.message


@pytest.mark.parametrize(
    ("label", "message"),
    [
        ("empty", ""),
        ("whitespace-only", "   \n\t"),
        ("control-only", "\x00\x07"),
        ("too long", "x" * (LIMITS.message + 1)),
    ],
)
def test_refine_rejects(label: str, message: str) -> None:
    assert first_error(RefineBody, {"message": message}) == "message", label


@pytest.mark.parametrize(
    ("label", "value", "field"),
    [
        ("empty query", {"query": " ", "chosen_entity_id": None}, "query"),
        ("query too long", {"query": "x" * (LIMITS.query + 1), "chosen_entity_id": None}, "query"),
        ("missing choice", {"query": "x"}, "chosen_entity_id"),
        ("bad entity id", {"query": "x", "chosen_entity_id": "a b"}, "chosen_entity_id"),
    ],
)
def test_confirm_rejects(label: str, value: dict[str, Any], field: str) -> None:
    assert first_error(ConfirmBody, value) == field, label


# --- the routes answer 400 before any route code runs ---------------------------------


class NoDeps:
    """Fails on any use: a refused request must not touch the database, Qloo or Claude."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"route code ran: deps.{name}")

    async def aclose(self) -> None:
        pass


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("INTERNAL_API_KEY", "test-key")

    async def factory() -> Any:
        return NoDeps()

    with TestClient(main.create_app(factory), headers={HEADER: "test-key"}) as c:
        yield c


@pytest.mark.parametrize(
    ("path", "body", "field"),
    [
        ("/venues", {**VENUE, "name": "   "}, "name"),
        ("/venues", {**VENUE, "city": "x" * (LIMITS.city + 1)}, "city"),
        ("/venues", {**VENUE, "past_shows": [SHOW] * (LIMITS.past_shows + 1)}, "past_shows"),
        ("/venues", {**VENUE, "past_shows": [{**SHOW, "act": "\x00"}]}, "past_shows.0.act"),
        ("/venues/ven_1/confirm", {"query": "", "chosen_entity_id": None}, "query"),
        ("/runs", {**RUN, "message": "x" * (LIMITS.message + 1)}, "message"),
        ("/runs/run_1/refine", {"message": " \n "}, "message"),
        ("/runs/run_1/refine", {"message": "x" * (LIMITS.message + 1)}, "message"),
    ],
)
def test_route_answers_400_invalid_input(
    client: TestClient, path: str, body: dict[str, Any], field: str
) -> None:
    res = client.post(path, json=body)
    assert res.status_code == 400
    assert res.json() == {"reason": "invalid_input", "field": field}


def test_unparseable_body_has_no_field(client: TestClient) -> None:
    res = client.post(
        "/runs/run_1/refine", content=b"{", headers={"content-type": "application/json"}
    )
    assert res.status_code == 400
    assert res.json()["reason"] == "invalid_input"
