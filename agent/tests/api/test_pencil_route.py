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


def test_pencil_hold_persists_and_releases(client: TestClient, scripts: Scripts) -> None:
    run_id, shortlist = ranked_run(client, scripts)
    top, other = shortlist[0]["entity_id"], shortlist[1]["entity_id"]
    assert pencilled(client, run_id, top) is False, "no hold by default"

    res = client.patch(f"/runs/{run_id}/candidates/{top}", json={"pencilled": True})
    assert res.status_code == 200, res.text
    assert res.json() == {"run_id": run_id, "entity_id": top, "pencilled": True}
    assert pencilled(client, run_id, top) is True
    assert pencilled(client, run_id, other) is False, "only that candidate"

    # Idempotent: pencilling twice keeps the hold.
    again = client.patch(f"/runs/{run_id}/candidates/{top}", json={"pencilled": True})
    assert again.status_code == 200 and pencilled(client, run_id, top) is True

    released = client.patch(f"/runs/{run_id}/candidates/{top}", json={"pencilled": False})
    assert released.json()["pencilled"] is False
    assert pencilled(client, run_id, top) is False


def test_pencil_404s_and_rejects_bad_bodies(client: TestClient, scripts: Scripts) -> None:
    run_id, shortlist = ranked_run(client, scripts)
    top = shortlist[0]["entity_id"]
    body = {"pencilled": True}
    assert client.patch(f"/runs/{run_id}/candidates/NOPE", json=body).status_code == 404
    assert client.patch("/runs/run_missing/candidates/E", json=body).status_code == 404
    for bad in ({}, {"pencilled": "maybe"}):
        assert client.patch(f"/runs/{run_id}/candidates/{top}", json=bad).status_code == 400
    assert pencilled(client, run_id, top) is False
