"""`POST /runs/{id}/refine` (T25) over the real app and a migrated Postgres: runs chain by
`parent_run_id`, the stored result carries the refinement (message, delta, explanation), and
nothing is spent on Qloo when the message asks for no change.

The parser and the agent are built from the same `model_factory`, so each test hands the
route a queue of `ScriptedModel`s: the parser's JSON answer first, then the agent's turns.
"""

# Its fixtures are imported, so the tests taking them as arguments "redefine" them.
# ruff: noqa: F811

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from test_api import (  # noqa: F401  (fixtures)
    CONSTRAINTS,
    Scripts,
    _answer,
    _rank,
    client,
    db_url,
    harness,
    post_run,
    profiled_venue,
    scripts,
)

from draw.agent.agent import MAX_STEPS

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from scripted_model import Results, ScriptedModel, Turn  # noqa: E402

# The parser's answer: a change that keeps the fake Qloo data flowing (no new tag to resolve,
# no popularity band the fixture artists fall out of).
RECENT_AND_MORE = json.dumps(
    {
        "include_tags_add": [],
        "include_tags_remove": [],
        "exclude_tags_add": [],
        "exclude_tags_remove": [],
        "size_tier": None,
        "draw_cap": None,
        "exclude_recent_months": 12,
        "max_candidates": 30,
        "hints": [],
    }
)
MERGED_CHANGE = {"exclude_recent_months": 12, "max_candidates": 30}
NO_CHANGE = json.dumps({"size_tier": None, "hints": []})


class Queue:
    """`Deps.model_factory`: the next scripted model in line (the last one repeats)."""

    def __init__(self, *scripts: list[Any]) -> None:
        self.models = [ScriptedModel(s) for s in scripts]
        self.built: list[ScriptedModel] = []

    def __call__(self) -> ScriptedModel:
        model = self.models.pop(0) if len(self.models) > 1 else self.models[0]
        self.built.append(model)
        return model

    def agent_turns(self, constraints_json: str) -> list[Any]:
        """generate (on the profile id in this run's own prompt) → score_and_rank → answer."""

        def generate(results: Results) -> Turn:
            prompt = "".join(
                str(b.get("text", "")) for b in self.built[-1].requests[0][0]["content"]
            )
            match = re.search(r"profile_id (prof_\w+)", prompt)
            assert match, "the stored profile's id is in the prompt"
            return [
                (
                    "generate_candidates",
                    {"profile_id": match.group(1), "constraints_json": constraints_json},
                )
            ]

        return [generate, _rank, _answer]


def parent_run(client: TestClient, scripts: Scripts) -> dict[str, Any]:
    venue_id = profiled_venue(client)
    scripts.pipeline()
    res = post_run(client, venue_id)
    assert res.status_code == 200, res.text
    run: dict[str, Any] = res.json()
    return run


def refine(
    client: TestClient, scripts: Scripts, run: dict[str, Any], message: str, parser: str
) -> tuple[Any, Queue]:
    deps = client.app.state.deps  # type: ignore[attr-defined]
    merged = {**run["constraints"], **MERGED_CHANGE}
    queue = Queue([parser], [])
    queue.models[1].script = queue.agent_turns(json.dumps(merged))
    deps.model_factory = queue
    try:
        return client.post(f"/runs/{run['run_id']}/refine", json={"message": message}), queue
    finally:
        deps.model_factory = scripts


def test_refine_chains_a_new_run_to_its_parent(client: TestClient, scripts: Scripts) -> None:
    run = parent_run(client, scripts)
    assert run["parent_run_id"] is None

    message = "nothing from the last year, and show me 30"
    res, queue = refine(client, scripts, run, message, RECENT_AND_MORE)
    assert res.status_code == 200, res.text
    child = res.json()
    assert child["run_id"] != run["run_id"] and child["parent_run_id"] == run["run_id"]
    assert child["venue_id"] == run["venue_id"] and child["status"] == "ok"
    assert child["constraints"] == {**run["constraints"], **MERGED_CHANGE}
    assert child["result"]["shortlist"], "the re-run ranked"

    refinement = child["result"]["refinement"]
    assert refinement["message"] == message
    assert refinement["delta"]["exclude_recent_months"] == 12
    assert refinement["delta"]["max_candidates"] == 30
    assert refinement["explanation"].strip() and refinement["explanation"].endswith(".")
    assert refinement["explanation"].startswith(
        "Skipped acts booked in the last 12 months and considered up to 30 candidates;"
    )
    assert refinement["token_usage"]["model_calls"] == 1
    assert len(queue.built) == 2, "one parser model, one agent model"
    parser, agent = queue.built
    assert parser.tool_specs == [] and len(parser.requests) == 1
    assert len(agent.tool_specs) > 0
    assert "Refinement of an earlier shortlist" in json.dumps(agent.requests[0])

    assert client.get(f"/runs/{child['run_id']}").json() == child
    assert client.get(f"/runs/{run['run_id']}").json()["result"].get("refinement") is None


def test_refinements_chain_by_parent(client: TestClient, scripts: Scripts) -> None:
    first = parent_run(client, scripts)
    second = refine(client, scripts, first, "nothing from last year", RECENT_AND_MORE)[0].json()
    # The same change again would be no change (422): the third asks for something new.
    forty = json.dumps({"max_candidates": 40})
    third = refine(client, scripts, second, "make it 40 acts", forty)[0].json()
    assert second["status"] == "ok" and third["status"] == "ok"
    chain = [third["parent_run_id"], second["parent_run_id"], first["parent_run_id"]]
    assert chain == [second["run_id"], first["run_id"], None]
    assert third["constraints"]["max_candidates"] == 40
    assert third["result"]["refinement"]["explanation"].startswith(
        "Considered up to 40 candidates;"
    )


def test_refine_404_and_422s_spend_nothing_on_qloo(client: TestClient, scripts: Scripts) -> None:
    run = parent_run(client, scripts)
    calls_before = client.get("/limits").json()["qloo"]["used"]

    missing = client.post("/runs/run_missing/refine", json={"message": "more emerging acts"})
    assert missing.status_code == 404

    res, _ = refine(client, scripts, run, "looks good", NO_CHANGE)
    assert res.status_code == 422 and "no change" in res.json()["detail"]
    res, _ = refine(client, scripts, run, "hmm", "not json at all")
    assert res.status_code == 422 and "expected shape" in res.json()["detail"]
    assert client.post(f"/runs/{run['run_id']}/refine", json={"message": ""}).status_code == 400

    assert client.get("/limits").json()["qloo"]["used"] == calls_before
    runs = client.get(f"/runs/{run['run_id']}").json()
    assert runs["parent_run_id"] is None, "no child run was created"


def test_refine_budget_guard_429(
    client: TestClient, scripts: Scripts, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = parent_run(client, scripts)
    deps = client.app.state.deps  # type: ignore[attr-defined]
    monkeypatch.setattr(deps, "llm_cap", 1)
    res = client.post(f"/runs/{run['run_id']}/refine", json={"message": "more emerging acts"})
    assert res.status_code == 429 and res.json()["what"] == "LLM token"


def test_refined_run_keeps_the_step_cap(client: TestClient, scripts: Scripts) -> None:
    run = parent_run(client, scripts)
    deps = client.app.state.deps  # type: ignore[attr-defined]
    runaway: list[Any] = [[("find_tags", {"query": "jazz", "limit": 3})]]
    queue = Queue([RECENT_AND_MORE], runaway)
    deps.model_factory = queue
    try:
        res = client.post(
            f"/runs/{run['run_id']}/refine", json={"message": "nothing from last year"}
        )
    finally:
        deps.model_factory = scripts
    assert res.status_code == 200, res.text
    child = res.json()
    assert child["status"] == "error" and child["parent_run_id"] == run["run_id"]
    assert child["result"]["steps"] == MAX_STEPS
    assert child["result"]["stop_reason"] == "step_limit"
    assert child["result"]["refinement"]["explanation"].endswith("no act survived the search.")
