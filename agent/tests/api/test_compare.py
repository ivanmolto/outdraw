"""`GET /runs/{id}/compare` (T23): one tool-less model call, labelled `llm_only`, cached on
the run, under the daily token budget.

The `llm_only_shortlist` tests run without a database; the route tests reuse `test_api`'s
migrated Postgres, fake harness and `ScriptedModel`.
"""

# Its fixtures are imported, so the tests taking them as arguments "redefine" them.
# ruff: noqa: F811

from __future__ import annotations

import asyncio
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from test_api import (  # noqa: F401  (fixtures)
    CONSTRAINTS,
    VENUE,
    Scripts,
    client,
    db_url,
    harness,
    post_run,
    profiled_venue,
    scripts,
)

from draw.agent.compare import COMPARE_ACTS, LLM_ONLY, disclaimer, llm_only_shortlist
from draw.qloo.budget import LLM_PREFIX, MemoryCounter
from draw.qloo.envelope import BudgetExceeded
from draw.schemas import VenueInput

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from scripted_model import INPUT_TOKENS, OUTPUT_TOKENS, ScriptedModel  # noqa: E402

MODEL_ID = "claude-haiku-5-5"

ANSWER = json.dumps(
    {
        "acts": [
            {"name": f"Synthetic Act {i}", "reason": "Fits the room.", "label": "qloo"}
            for i in range(COMPARE_ACTS + 2)
        ]
    }
)
CALL_TOKENS = INPUT_TOKENS + OUTPUT_TOKENS


def llm_counter(cap: int = 10_000_000) -> MemoryCounter:
    return MemoryCounter(cap, prefix=LLM_PREFIX, what="LLM token", charge_over=True)


# --- llm_only_shortlist ------------------------------------------------------------------


async def test_one_call_no_tools_every_act_labelled() -> None:
    model = ScriptedModel([ANSWER])
    counter = llm_counter()
    out = await llm_only_shortlist(
        "run_x",
        VenueInput.model_validate(VENUE),
        CONSTRAINTS,
        model_factory=lambda: model,
        token_counter=counter,
    )

    assert len(model.requests) == 1 and model.tool_specs == []
    assert out.label == LLM_ONLY and out.model == MODEL_ID
    assert out.disclaimer == disclaimer(MODEL_ID) and MODEL_ID in out.disclaimer
    assert len(out.acts) == COMPARE_ACTS, "capped at ten"
    assert {a.label for a in out.acts} == {LLM_ONLY}, "the model cannot relabel an act"
    assert out.error is None and not out.cached
    assert out.token_usage == {"model_calls": 1, "tokens_counted": CALL_TOKENS}
    assert await counter.used() == CALL_TOKENS

    prompt = "".join(str(b.get("text", "")) for b in model.requests[0][0]["content"])
    assert "Blue Note Jazz Club" in prompt and '"size_tier":"emerging"' in prompt
    assert "Synthetic Entity 3 (sold 100%)" in prompt


async def test_budget_guard_before_the_model_is_built() -> None:
    built: list[ScriptedModel] = []

    def factory() -> ScriptedModel:
        built.append(ScriptedModel([ANSWER]))
        return built[-1]

    counter = llm_counter(cap=500)
    await counter.reserve(500)
    with pytest.raises(BudgetExceeded):
        await llm_only_shortlist(
            "run_x",
            VenueInput.model_validate(VENUE),
            CONSTRAINTS,
            model_factory=factory,
            token_counter=counter,
        )
    assert built == [], "the model is not even built at the cap"


async def test_disclaimer_names_the_configured_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-haiku-9-9")
    out = await llm_only_shortlist(
        "run_x",
        VenueInput.model_validate(VENUE),
        CONSTRAINTS,
        model_factory=lambda: ScriptedModel([ANSWER]),
        token_counter=llm_counter(),
    )
    assert out.model == "claude-haiku-9-9" and "claude-haiku-9-9" in out.disclaimer


async def test_budget_guard_during_the_call() -> None:
    counter = llm_counter(cap=CALL_TOKENS - 1)
    with pytest.raises(BudgetExceeded):
        await llm_only_shortlist(
            "run_x",
            VenueInput.model_validate(VENUE),
            CONSTRAINTS,
            model_factory=lambda: ScriptedModel([ANSWER]),
            token_counter=counter,
        )
    assert await counter.used() == CALL_TOKENS, "the spend stays on the counter"


async def test_unusable_answer_is_an_error() -> None:
    out = await llm_only_shortlist(
        "run_x",
        VenueInput.model_validate(VENUE),
        CONSTRAINTS,
        model_factory=lambda: ScriptedModel(["Here are some acts: Synthetic Act 1."]),
        token_counter=llm_counter(),
    )
    assert out.acts == [] and out.error and out.label == LLM_ONLY


# --- the route ---------------------------------------------------------------------------


def ranked_run(client: TestClient, scripts: Scripts) -> str:
    venue_id = profiled_venue(client)
    scripts.pipeline()
    res = post_run(client, venue_id)
    assert res.status_code == 200, res.text
    run_id: str = res.json()["run_id"]
    scripts.turns = [ANSWER]
    return run_id


def test_compare_is_labelled_and_cached_on_second_call(
    client: TestClient, scripts: Scripts
) -> None:
    run_id = ranked_run(client, scripts)
    models_before = len(scripts.models)

    first = client.get(f"/runs/{run_id}/compare")
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["label"] == LLM_ONLY and body["run_id"] == run_id
    assert len(body["acts"]) == COMPARE_ACTS
    assert all(a["label"] == LLM_ONLY for a in body["acts"])
    assert body["cached"] is False
    assert len(scripts.models) == models_before + 1
    assert scripts.models[-1].tool_specs == [] and len(scripts.models[-1].requests) == 1
    used = client.get("/limits").json()["llm"]["used"]

    second = client.get(f"/runs/{run_id}/compare")
    assert second.status_code == 200
    assert second.json() == {**body, "cached": True}
    assert len(scripts.models) == models_before + 1, "no second model call"
    assert client.get("/limits").json()["llm"]["used"] == used, "no tokens spent"
    # The run itself is untouched: Outdraw's shortlist and the compare list stay apart.
    assert "compare" not in client.get(f"/runs/{run_id}").json()


def test_unusable_answer_is_not_cached(client: TestClient, scripts: Scripts) -> None:
    run_id = ranked_run(client, scripts)
    scripts.turns = ["not json"]
    assert client.get(f"/runs/{run_id}/compare").json()["error"]
    scripts.turns = [ANSWER]
    retry = client.get(f"/runs/{run_id}/compare").json()
    assert retry["error"] is None and retry["cached"] is False


def test_compare_budget_guard_429(
    client: TestClient, scripts: Scripts, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = ranked_run(client, scripts)
    deps = client.app.state.deps  # type: ignore[attr-defined]
    models_before = len(scripts.models)
    monkeypatch.setattr(deps, "llm_cap", 1)

    res = client.get(f"/runs/{run_id}/compare")
    assert res.status_code == 429
    assert res.json()["what"] == "LLM token"
    assert len(scripts.models) == models_before, "no model built at the cap"


class SlowModel(ScriptedModel):
    """Yields to the event loop before answering, so two requests can overlap."""

    async def stream(self, messages: Any, *args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(0.3)
        async for event in super().stream(messages, *args, **kwargs):
            yield event


def test_concurrent_first_calls_spend_one_model_call(
    client: TestClient, scripts: Scripts, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = ranked_run(client, scripts)
    deps = client.app.state.deps  # type: ignore[attr-defined]
    built: list[SlowModel] = []

    def factory() -> SlowModel:
        built.append(SlowModel([ANSWER]))
        return built[-1]

    monkeypatch.setattr(deps, "model_factory", factory)
    with ThreadPoolExecutor(2) as pool:
        a, b = pool.map(lambda _: client.get(f"/runs/{run_id}/compare"), range(2))

    assert a.status_code == 200 and b.status_code == 200
    assert len(built) == 1, "the second request waited for the first and read its cache"
    assert sorted(r.json()["cached"] for r in (a, b)) == [False, True]
    assert a.json()["acts"] == b.json()["acts"]


def test_compare_404_for_unknown_run(client: TestClient) -> None:
    assert client.get("/runs/run_missing/compare").status_code == 404
