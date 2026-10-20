"""Candidate generation over synthetic envelopes (the harness is never spawned)."""

from __future__ import annotations

import copy
import datetime as dt
import json
from collections.abc import Callable
from typing import Any

import pytest
from conftest import FIXTURES_DIR, RouterHarness

from draw.qloo import exec_client
from draw.qloo.config import QlooConfig
from draw.qloo.exec_client import QlooExec
from draw.ranking.candidates import (
    DEFAULT_MAX_CANDIDATES,
    POPULARITY_BANDS,
    CandidateList,
    generate_candidates,
    last_booked,
    months_before,
)
from draw.schemas import (
    Candidate,
    Constraints,
    CrowdProfile,
    PastShow,
    ResolvedEntity,
    Signal,
    SizeTier,
    VenueInput,
)

REGION = "New York, United States"
TODAY = dt.date(2026, 10, 20)
OTHER_URN = "urn:tag:genre:music:synthetic-other"
ESTABLISHED, MID, EMERGING = 0.9995, 0.997, 0.9


def fixture(workflow: str, name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES_DIR / workflow / f"{name}.json").read_text("utf-8"))
    return data


# find_tags/jazz: the `urn:tag:genre:music` hit is the music genre (its word is the fixture's
# query, so the regenerated numbering never matters).
_JAZZ_HIT = next(
    r for r in fixture("find_tags", "jazz")["results"] if r["type"] == "urn:tag:genre:music"
)
JAZZ_WORD, JAZZ_URN = str(_JAZZ_HIT["name"]), str(_JAZZ_HIT["id"])


def artists(
    name: str = "crowd_artists", *, popularity: float = ESTABLISHED, ids: list[str] | None = None
) -> dict[str, Any]:
    """A recommend envelope with every row's popularity set (and IDs renamed if given)."""
    envelope = copy.deepcopy(fixture("recommend", name))
    for i, row in enumerate(envelope["results"]):
        row["popularity"] = popularity
        if ids is not None and i < len(ids):
            row["entity_id"] = ids[i]
    if ids is not None:
        envelope["results"] = envelope["results"][: len(ids)]
        envelope["result_count"] = len(envelope["results"])
    return envelope


def row_ids(envelope: dict[str, Any]) -> list[str]:
    return [r["entity_id"] for r in envelope["results"]]


@pytest.fixture
def routed(monkeypatch: pytest.MonkeyPatch) -> Callable[..., RouterHarness]:
    def make(
        primary: dict[str, Any],
        single: dict[str, dict[str, Any]] | None = None,
        tags: dict[str, dict[str, Any]] | None = None,
    ) -> RouterHarness:
        def router(workflow: str, payload: dict[str, Any]) -> dict[str, Any]:
            if workflow == "find_tags":
                return (tags or {}).get(payload["query"]) or fixture("find_tags", "jazz")
            signals = payload.get("signals") or []
            if len(signals) == 1 and single and signals[0] in single:
                return single[signals[0]]
            return primary

        fake = RouterHarness(router)
        monkeypatch.setattr(exec_client, "create_subprocess_exec", fake)
        return fake

    return make


def client() -> QlooExec:
    return QlooExec(QlooConfig(qloo_bin="/fake/qloo"))


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


def constraints(
    *,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    tier: SizeTier = "established",
    months: int = 0,
    cap: int = DEFAULT_MAX_CANDIDATES,
) -> Constraints:
    return Constraints(
        include_tags=include or [],
        exclude_tags=exclude or [],
        size_tier=tier,
        exclude_recent_months=months,
        max_candidates=cap,
    )


PROFILE = profile("ID-A")


async def run(c: Constraints, prof: CrowdProfile = PROFILE, **kwargs: Any) -> CandidateList:
    kwargs.setdefault("qloo", client())
    return await generate_candidates(prof, c, region=REGION, today=TODAY, **kwargs)


# --- constraints ----------------------------------------------------------------------


async def test_excluded_tags_absent(routed: Callable[..., RouterHarness]) -> None:
    # Qloo filters tags server-side (decision 001); the stage must send the URNs and never
    # label a candidate with an excluded one.
    envelope = artists()
    fake = routed(envelope)

    result = await run(constraints(exclude=[JAZZ_WORD, OTHER_URN]))

    assert len(result.candidates) == len(envelope["results"])
    assert all(c.tags == [] for c in result.candidates)
    # The word is resolved once with find_tags; both URNs go to Qloo's exclude filter.
    assert fake.workflows().count("find_tags") == 1
    (sent,) = fake.inputs_for("recommend")
    assert sent["exclude_tags"] == [JAZZ_URN, OTHER_URN]
    assert sent["exclude_tags_operator"] == "union"
    assert "include_tags" not in sent


async def test_included_tag_sent_and_attached(routed: Callable[..., RouterHarness]) -> None:
    fake = routed(artists())
    result = await run(constraints(include=[JAZZ_WORD]))
    (sent,) = fake.inputs_for("recommend")
    assert sent["include_tags"] == [JAZZ_URN]
    assert sent["include_tags_operator"] == "union"
    assert sent["signal_location"] == REGION
    assert all(c.tags == [JAZZ_URN] for c in result.candidates)


async def test_tag_resolution_is_cached(routed: Callable[..., RouterHarness]) -> None:
    fake = routed(artists())
    qloo = client()
    await run(constraints(exclude=[JAZZ_WORD]), qloo=qloo)
    await run(constraints(exclude=[JAZZ_WORD]), qloo=qloo)
    assert fake.workflows().count("find_tags") == 1


async def test_unconfirmed_tag_asks_before_any_taste_call(
    routed: Callable[..., RouterHarness],
) -> None:
    fake = routed(artists())
    result = await run(constraints(include=["Something Else"]))
    assert result.candidates == []
    assert result.reason and "Something Else" in result.reason
    assert [t.query for t in result.unresolved_tags] == ["Something Else"]
    assert result.unresolved_tags[0].candidates
    assert "recommend" not in fake.workflows()


async def test_recently_booked_acts_absent(routed: Callable[..., RouterHarness]) -> None:
    envelope = artists()
    recent, older, *_ = row_ids(envelope)
    routed(envelope)
    booked = {recent: dt.date(2026, 8, 1), older: dt.date(2025, 12, 1)}

    result = await run(constraints(months=6), booked=booked)

    ids = [c.entity_id for c in result.candidates]
    assert recent not in ids
    assert older in ids  # booked 10+ months ago, outside the window
    assert len(ids) == len(envelope["results"]) - 1


async def test_recent_months_zero_keeps_everyone(routed: Callable[..., RouterHarness]) -> None:
    envelope = artists()
    routed(envelope)
    result = await run(constraints(months=0), booked={row_ids(envelope)[0]: TODAY})
    assert len(result.candidates) == len(envelope["results"])


@pytest.mark.parametrize("tier", ["emerging", "mid", "established"])
async def test_size_tier_maps_to_popularity_band(
    routed: Callable[..., RouterHarness], tier: SizeTier
) -> None:
    envelope = artists()
    for row, popularity in zip(
        envelope["results"], [EMERGING, MID, ESTABLISHED, 1.0], strict=False
    ):
        row["popularity"] = popularity
    routed(envelope)
    low, high = POPULARITY_BANDS[tier]
    result = await run(constraints(tier=tier))
    assert result.candidates
    assert all(low <= c.popularity < high or c.popularity == high == 1.0 for c in result.candidates)


def test_bands_cover_zero_to_one_without_gaps() -> None:
    bands = [POPULARITY_BANDS[t] for t in ("emerging", "mid", "established")]
    assert bands[0][0] == 0.0 and bands[-1][1] == 1.0
    assert all(a[1] == b[0] for a, b in zip(bands, bands[1:], strict=False))


# --- dedupe, cap, evidence ------------------------------------------------------------


async def test_dedupe_across_calls_and_within_one(routed: Callable[..., RouterHarness]) -> None:
    primary = artists(ids=["ID-1", "ID-2", "ID-3"])
    primary["results"].append(copy.deepcopy(primary["results"][0]))  # ID-1 twice
    widen = artists(ids=["ID-2", "ID-4"])
    fake = routed(primary, single={"ID-A": widen, "ID-B": artists(ids=["ID-5"])})

    result = await run(constraints(), prof=profile("ID-A", "ID-B"))

    ids = [c.entity_id for c in result.candidates]
    assert ids == ["ID-1", "ID-2", "ID-3", "ID-4", "ID-5"]
    assert len(set(ids)) == len(ids)
    two = next(c for c in result.candidates if c.entity_id == "ID-2")
    assert len({e.call_id for e in two.evidence}) == 2  # returned by two calls
    assert [len(i["signals"]) for i in fake.inputs_for("recommend")] == [2, 1, 1]


async def test_cap_honoured_and_stops_calling(routed: Callable[..., RouterHarness]) -> None:
    fake = routed(artists())
    result = await run(constraints(cap=3), prof=profile("ID-A", "ID-B"))
    assert len(result.candidates) == 3
    assert fake.workflows() == ["recommend"]  # cap reached on the first call: no widening


async def test_default_cap_is_forty_and_calls_are_bounded(
    routed: Callable[..., RouterHarness],
) -> None:
    many = artists()
    rows = many["results"]
    many["results"] = [
        {**copy.deepcopy(rows[i % len(rows)]), "entity_id": f"ID-{i}"} for i in range(60)
    ]
    fake = routed(many)
    result = await run(constraints(), prof=profile("ID-A", "ID-B", "ID-C"))
    assert len(result.candidates) == DEFAULT_MAX_CANDIDATES == 40
    assert fake.workflows() == ["recommend"]


async def test_evidence_present(routed: Callable[..., RouterHarness]) -> None:
    routed(artists())
    result = await run(constraints())
    assert result.candidates
    for candidate in result.candidates:
        assert Candidate.model_validate(candidate.model_dump(mode="json")) == candidate
        assert candidate.evidence
        assert all(e.item_ref == candidate.entity_id and e.call_id for e in candidate.evidence)
        assert 0.0 <= candidate.affinity <= 1.0
    assert len({e.call_id for c in result.candidates for e in c.evidence}) == 1
    assert result.candidates_quality == "ok"


# --- status branches ------------------------------------------------------------------


async def test_empty_envelope_returns_empty_with_reason(
    routed: Callable[..., RouterHarness],
) -> None:
    fake = routed(fixture("recommend", "derived_empty"))
    result = await run(constraints(include=[JAZZ_WORD]), prof=profile("ID-A", "ID-B"))
    assert result.candidates == []
    assert result.reason and "broaden" in result.reason
    assert result.candidates_quality == "degraded"
    assert result.notes and "No results matched" in result.notes[0]
    assert fake.workflows().count("recommend") == 1  # no widening after an empty first call


async def test_all_filtered_out_returns_reason(routed: Callable[..., RouterHarness]) -> None:
    routed(artists(popularity=EMERGING))
    result = await run(constraints(tier="established"))
    assert result.candidates == []
    assert result.reason and "established popularity band" in result.reason


async def test_partial_envelope_flagged(routed: Callable[..., RouterHarness]) -> None:
    envelope = artists("derived_partial")
    routed(envelope)
    result = await run(constraints())
    assert [c.entity_id for c in result.candidates] == row_ids(envelope)
    assert result.candidates_quality == "partial"
    assert result.notes and "Only some results" in result.notes[0]


async def test_degraded_envelope_flagged(routed: Callable[..., RouterHarness]) -> None:
    routed(artists("derived_degraded"))
    result = await run(constraints())
    assert result.candidates
    assert result.candidates_quality == "degraded"


async def test_error_envelope_does_not_raise(routed: Callable[..., RouterHarness]) -> None:
    error = copy.deepcopy(fixture("recommend", "invalid_signal_id"))
    error.update(
        status="error",
        results=[],
        result_count=0,
        error={"code": "QLOO_HTTP", "retryable": False, "recovery": "Check the signal IDs."},
    )
    routed(error)
    result = await run(constraints())
    assert result.candidates == []
    assert result.reason and "Check the signal IDs." in result.reason


async def test_no_signals_makes_no_call(routed: Callable[..., RouterHarness]) -> None:
    fake = routed(artists())
    result = await run(constraints(), prof=profile())
    assert result.candidates == [] and result.reason
    assert fake.spawns == 0


# --- helpers --------------------------------------------------------------------------


def test_months_before_clamps_month_end() -> None:
    assert months_before(dt.date(2026, 10, 20), 6) == dt.date(2026, 4, 20)
    assert months_before(dt.date(2026, 3, 31), 1) == dt.date(2026, 2, 28)
    assert months_before(dt.date(2026, 1, 15), 13) == dt.date(2024, 12, 15)


def test_last_booked_keys_confirmed_acts_by_entity_id() -> None:
    venue = VenueInput(
        name="Synthetic Room",
        city="New York",
        country="United States",
        capacity=150,
        past_shows=[
            PastShow(act="Act One", date=dt.date(2026, 1, 5)),
            PastShow(act="act one", date=dt.date(2026, 7, 5)),
            PastShow(act="Act Two", date=dt.date(2025, 3, 1)),
        ],
    )

    def act(name: str, entity_id: str | None, confirmed: bool = True) -> ResolvedEntity:
        return ResolvedEntity(
            query=name,
            entity_id=entity_id,
            canonical_name=name,
            confidence=1.0,
            candidates=[],
            confirmed=confirmed,
        )

    booked = last_booked(venue, [act("Act One", "ID-1"), act("Act Two", None, False)])
    assert booked == {"ID-1": dt.date(2026, 7, 5)}
