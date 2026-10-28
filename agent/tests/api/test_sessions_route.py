"""`POST /sessions`, `GET /sessions/{id}` (T46) and `PATCH /sessions/{id}`: a new session
starts free, reads back, gets an unguessable id, flips plan on a PATCH, and an unknown id
is a 404.

Reuses `test_api`'s migrated Postgres.
"""

# Its fixtures are imported, so the tests taking them as arguments "redefine" them.
# ruff: noqa: F811

from __future__ import annotations

import re

from fastapi.testclient import TestClient
from test_api import (  # noqa: F401  (fixtures)
    _sql,
    client,
    db_url,
    harness,
    scripts,
)


def test_new_session_is_free_and_reads_back(client: TestClient) -> None:
    res = client.post("/sessions")
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["plan"] == "free"
    assert re.fullmatch(r"ses_[A-Za-z0-9_-]{28}", body["id"])

    read = client.get(f"/sessions/{body['id']}")
    assert read.status_code == 200, read.text
    assert read.json() == body


def test_each_session_gets_its_own_id(client: TestClient) -> None:
    ids = {client.post("/sessions").json()["id"] for _ in range(3)}
    assert len(ids) == 3


def test_stored_pro_plan_reads_back(client: TestClient, db_url: str) -> None:
    session_id = client.post("/sessions").json()["id"]
    _sql(db_url, f"UPDATE sessions SET plan = 'pro' WHERE id = '{session_id}'")
    assert client.get(f"/sessions/{session_id}").json()["plan"] == "pro"


def test_unknown_session_is_404(client: TestClient) -> None:
    assert client.get("/sessions/ses_nope").status_code == 404
    assert client.patch("/sessions/ses_nope", json={"plan": "pro"}).status_code == 404


def test_patch_flips_the_stored_plan(client: TestClient) -> None:
    session_id = client.post("/sessions").json()["id"]

    res = client.patch(f"/sessions/{session_id}", json={"plan": "pro"})
    assert res.status_code == 200, res.text
    assert res.json() == {"id": session_id, "plan": "pro"}
    assert client.get(f"/sessions/{session_id}").json()["plan"] == "pro"

    assert client.patch(f"/sessions/{session_id}", json={"plan": "free"}).json()["plan"] == "free"
    assert client.get(f"/sessions/{session_id}").json()["plan"] == "free"


def test_patch_rejects_an_unknown_plan(client: TestClient) -> None:
    session_id = client.post("/sessions").json()["id"]
    # The agent answers invalid input with its own 400 (draw/api/inputs.py), not FastAPI's 422.
    assert client.patch(f"/sessions/{session_id}", json={"plan": "gold"}).status_code == 400
    assert client.get(f"/sessions/{session_id}").json()["plan"] == "free"
