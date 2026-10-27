"""`PATCH /runs/{id}/candidates/{entity_id}` (T28): the pencil hold is stored on the
candidate row, read back by the evidence route, idempotent, scoped to one candidate, and
404s for an unknown run or act.

Reuses `test_api`'s migrated Postgres, fake harness and `ScriptedModel`.
"""

# Its fixtures are imported, so the tests taking them as arguments "redefine" them.
# ruff: noqa: F811

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from test_api import (  # noqa: F401  (fixtures)
    Scripts,
    client,
    db_url,
    harness,
    post_run,
    profiled_venue,
    scripts,
)
from test_refine_route import RECENT_AND_MORE, refine


def ranked_run(client: TestClient, scripts: Scripts) -> tuple[str, list[dict[str, Any]]]:
    venue_id = profiled_venue(client)
    scripts.pipeline()
    res = post_run(client, venue_id)
    assert res.status_code == 200, res.text
    body = res.json()
    return body["run_id"], body["result"]["shortlist"]


def pencilled(client: TestClient, run_id: str, entity_id: str) -> bool:
    res = client.get(f"/runs/{run_id}/candidates/{entity_id}/evidence")
    assert res.status_code == 200, res.text
    value = res.json()["pencilled"]
    assert isinstance(value, bool)
    return value


def run_entries(client: TestClient, run_id: str) -> dict[str, dict[str, Any]]:
    res = client.get(f"/runs/{run_id}")
    assert res.status_code == 200, res.text
    return {e["entity_id"]: e for e in res.json()["result"]["shortlist"]}


def test_pencil_hold_persists_and_releases(client: TestClient, scripts: Scripts) -> None:
    run_id, shortlist = ranked_run(client, scripts)
    top, other = shortlist[0]["entity_id"], shortlist[1]["entity_id"]
    assert pencilled(client, run_id, top) is False, "no hold by default"

    res = client.patch(f"/runs/{run_id}/candidates/{top}", json={"pencilled": True})
    assert res.status_code == 200, res.text
    assert res.json() == {"run_id": run_id, "entity_id": top, "pencilled": True, "date": None}
    assert pencilled(client, run_id, top) is True
    assert pencilled(client, run_id, other) is False, "only that candidate"

    # Idempotent: pencilling twice keeps the hold.
    again = client.patch(f"/runs/{run_id}/candidates/{top}", json={"pencilled": True})
    assert again.status_code == 200 and pencilled(client, run_id, top) is True

    released = client.patch(f"/runs/{run_id}/candidates/{top}", json={"pencilled": False})
    assert released.json()["pencilled"] is False
    assert pencilled(client, run_id, top) is False


def test_pencil_hold_keeps_its_night_on_the_run(client: TestClient, scripts: Scripts) -> None:
    run_id, shortlist = ranked_run(client, scripts)
    top = shortlist[0]["entity_id"]

    res = client.patch(
        f"/runs/{run_id}/candidates/{top}", json={"pencilled": True, "date": "2026-11-14"}
    )
    assert res.status_code == 200, res.text
    assert res.json()["date"] == "2026-11-14"
    evidence = client.get(f"/runs/{run_id}/candidates/{top}/evidence").json()
    assert evidence["pencil_date"] == "2026-11-14"

    # The run read back carries the hold on its entry, so the calendar needs no call per act.
    entries = run_entries(client, run_id)
    assert entries[top]["pencil_hold"] == {"date": "2026-11-14"}
    assert all(e["pencil_hold"] is None for k, e in entries.items() if k != top)
    assert all(isinstance(e["genres"], list) for e in entries.values())

    # A release clears the night.
    release = {"pencilled": False, "date": "2026-11-14"}
    client.patch(f"/runs/{run_id}/candidates/{top}", json=release)
    entry = run_entries(client, run_id)[top]
    assert entry["pencil_hold"] is None


def test_pencil_404s_and_rejects_bad_bodies(client: TestClient, scripts: Scripts) -> None:
    run_id, shortlist = ranked_run(client, scripts)
    top = shortlist[0]["entity_id"]
    body = {"pencilled": True}
    assert client.patch(f"/runs/{run_id}/candidates/NOPE", json=body).status_code == 404
    assert client.patch("/runs/run_missing/candidates/E", json=body).status_code == 404
    for bad in ({}, {"pencilled": "maybe"}, {"pencilled": True, "date": "2026-02-30"}):
        assert client.patch(f"/runs/{run_id}/candidates/{top}", json=bad).status_code == 400
    assert pencilled(client, run_id, top) is False


def test_venue_holds_span_every_run(client: TestClient, scripts: Scripts) -> None:
    run_id, shortlist = ranked_run(client, scripts)
    venue_id = client.get(f"/runs/{run_id}").json()["venue_id"]
    top, other = shortlist[0]["entity_id"], shortlist[1]["entity_id"]
    assert client.get(f"/venues/{venue_id}/holds").json() == {"venue_id": venue_id, "holds": []}

    client.patch(f"/runs/{run_id}/candidates/{top}", json={"pencilled": True, "date": "2026-11-14"})
    client.patch(f"/runs/{run_id}/candidates/{other}", json={"pencilled": True})
    parent = client.get(f"/runs/{run_id}").json()
    res, _ = refine(client, scripts, parent, "nothing from the last year", RECENT_AND_MORE)
    assert res.status_code == 200, res.text
    child_id = res.json()["run_id"]
    child_top = res.json()["result"]["shortlist"][0]["entity_id"]
    client.patch(
        f"/runs/{child_id}/candidates/{child_top}", json={"pencilled": True, "date": "2026-11-02"}
    )

    body = client.get(f"/venues/{venue_id}/holds").json()
    holds = [(h["run_id"], h["entity_id"], h["date"]) for h in body["holds"]]
    # Dated holds first by night, across both runs; the undated last.
    assert holds == [
        (child_id, child_top, "2026-11-02"),
        (run_id, top, "2026-11-14"),
        (run_id, other, None),
    ]
    assert all(h["name"] for h in body["holds"])
    assert client.get("/venues/ven_missing/holds").status_code == 404
