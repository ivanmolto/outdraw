"""`GET /runs/{id}/candidates/{entity_id}/brief` (T27): one Qloo call and one model call the
first time, the brief cached on the candidate row after, 404s, the token-cap 429, and an
unusable brief that is served but not cached.

Reuses `test_api`'s migrated Postgres, fake harness and `ScriptedModel`.
"""

# Its fixtures are imported, so the tests taking them as arguments "redefine" them.
# ruff: noqa: F811

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from test_api import (  # noqa: F401  (fixtures)
    VENUE,
    Scripts,
    client,
    db_url,
    harness,
    post_run,
    profiled_venue,
    scripts,
)

from draw.ranking.evidence import REF_TOP_REASON
from draw.schemas import BookingBrief

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))


def answer(act_name: str, refs: list[str], venue: str = VENUE["name"]) -> str:
    return json.dumps(
        {
            "why_it_fits": [{"text": f"Fits because of {r}.", "evidence_ref": r} for r in refs],
            "night_format": "model's",
            "support_act": None,
            "partners": [],
            "outreach_email": {
                "subject": f"{act_name} at {venue}",
                "body": f"Hello, {venue} would like to host {act_name}. Best, bookings.",
            },
        }
    )


def ranked_run(client: TestClient, scripts: Scripts) -> tuple[str, dict[str, Any]]:
    venue_id = profiled_venue(client)
    scripts.pipeline()
    res = post_run(client, venue_id)
    assert res.status_code == 200, res.text
    body = res.json()
    return body["run_id"], body["result"]["shortlist"][0]


def test_brief_is_written_once_and_cached(client: TestClient, scripts: Scripts) -> None:
    run_id, top = ranked_run(client, scripts)
    evidence = client.get(f"/runs/{run_id}/candidates/{top['entity_id']}/evidence").json()
    call_ref = "call:" + evidence["evidence"]["qloo_calls"][0]["call_id"]
    scripts.turns = [answer(top["name"], [REF_TOP_REASON, call_ref])]
    models_before = len(scripts.models)
    used = client.get("/limits").json()

    first = client.get(f"/runs/{run_id}/candidates/{top['entity_id']}/brief")
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["run_id"] == run_id and body["entity_id"] == top["entity_id"]
    assert body["name"] == top["name"] and body["cached"] is False and body["error"] is None
    brief = BookingBrief.model_validate(body["brief"])
    assert [r.evidence_ref for r in brief.why_it_fits] == [REF_TOP_REASON, call_ref]
    assert all(r.evidence_ref in body["evidence"] for r in brief.why_it_fits)
    assert body["evidence"][call_ref]["source"] == "qloo"
    assert VENUE["name"] in brief.outreach_email.body and top["name"] in brief.outreach_email.body
    # Outdraw's facts, not the model's.
    assert brief.night_format != "model's" and "show" in brief.night_format
    assert brief.partners, "the profile's places"
    assert brief.support_act is None, "every synthetic candidate sits in the emerging band"
    assert body["support"]["entity_id"] is None and body["support"]["note"]
    assert body["source_labels"]["partners"] == "qloo"
    assert len(scripts.models) == models_before + 1
    assert scripts.models[-1].tool_specs == [] and len(scripts.models[-1].requests) == 1
    after = client.get("/limits").json()
    assert after["llm"]["used"] > used["llm"]["used"]
    assert after["qloo"]["used"] == used["qloo"]["used"], "no smaller act: no support call"

    second = client.get(f"/runs/{run_id}/candidates/{top['entity_id']}/brief")
    assert second.status_code == 200
    assert second.json() == {**body, "cached": True}
    assert len(scripts.models) == models_before + 1, "no second model call"
    assert client.get("/limits").json()["llm"]["used"] == after["llm"]["used"]
    # The run and the evidence are untouched.
    assert "brief" not in client.get(f"/runs/{run_id}").json()


def test_unusable_brief_is_served_as_template_but_not_cached(
    client: TestClient, scripts: Scripts
) -> None:
    run_id, top = ranked_run(client, scripts)
    scripts.turns = [answer(top["name"], ["call:ghost"])]
    first = client.get(f"/runs/{run_id}/candidates/{top['entity_id']}/brief").json()
    assert first["error"] and first["attempts"] == 2
    assert first["brief"]["why_it_fits"][0]["evidence_ref"] == REF_TOP_REASON
    assert top["name"] in first["brief"]["outreach_email"]["body"]

    scripts.turns = [answer(top["name"], [REF_TOP_REASON])]
    retry = client.get(f"/runs/{run_id}/candidates/{top['entity_id']}/brief").json()
    assert retry["error"] is None and retry["cached"] is False and retry["attempts"] == 1


def test_brief_404s(client: TestClient, scripts: Scripts) -> None:
    run_id, _ = ranked_run(client, scripts)
    assert client.get(f"/runs/{run_id}/candidates/NOPE/brief").status_code == 404
    assert client.get("/runs/run_missing/candidates/E/brief").status_code == 404


def test_brief_budget_guard_429(
    client: TestClient, scripts: Scripts, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id, top = ranked_run(client, scripts)
    deps = client.app.state.deps  # type: ignore[attr-defined]
    models_before = len(scripts.models)
    monkeypatch.setattr(deps, "llm_cap", 1)

    res = client.get(f"/runs/{run_id}/candidates/{top['entity_id']}/brief")
    assert res.status_code == 429 and res.json()["what"] == "LLM token"
    assert len(scripts.models) == models_before, "no model built at the cap"
