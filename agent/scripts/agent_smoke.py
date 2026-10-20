#!/usr/bin/env python3
"""Smoke-run the venue agent against the real APIs: Claude Haiku 5.5 and `qloo mcp`.

Resolves the demo New York room's three past acts (T07), then runs `run_venue_agent` once
and prints the tool trace, token usage, Qloo calls and the final JSON. The DoD for T13:
under 40 s, at least three tool calls with at least one Qloo MCP tool among them, and a
final JSON that validates as `FinalAnswer`. Exit 0 only when all three hold.

Spends Qloo quota (printed as an estimate first; `--yes` skips the prompt) and Anthropic
tokens; needs `ANTHROPIC_API_KEY`, `QLOO_API_KEY` (or `~/.qloo/config`) and, for the
budgets, `REDIS_URL` in the environment:

    cd agent && set -a && source ../.env.local && set +a && uv run python scripts/agent_smoke.py

Nothing secret is printed; the key is read only so recordings can redact it.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from draw.agent.agent import (  # noqa: E402
    MAX_STEPS,
    AgentResult,
    AgentSettings,
    FinalAnswer,
    run_venue_agent,
)
from draw.profile.crowd import DEFAULT_MAX_CALLS  # noqa: E402
from draw.profile.resolve import resolve_entity, resolve_venue  # noqa: E402
from draw.qloo.mcp_client import QlooMCP  # noqa: E402
from draw.ranking.candidates import MAX_RECOMMEND_CALLS  # noqa: E402
from draw.ranking.signals import DEFAULT_MAX_CALLS as SIGNAL_CALLS  # noqa: E402
from draw.schemas import Constraints, PastShow, ResolvedEntity, VenueInput  # noqa: E402

TIME_LIMIT_S = 40
MIN_TOOL_CALLS = 3

# The demo room from scripts/record_crowd.py (decision 001: New York, not Malta).
VENUE = VenueInput(
    name="Draw Room",
    city="New York",
    country="United States",
    capacity=150,
    past_shows=[
        PastShow(act="Norah Jones", date=dt.date(2026, 6, 12), tickets_sold=150, capacity=150),
        PastShow(act="Radiohead", date=dt.date(2026, 7, 3), tickets_sold=100, capacity=150),
        PastShow(act="Gregory Porter", date=dt.date(2026, 8, 21), tickets_sold=45, capacity=150),
    ],
)
CONSTRAINTS = Constraints(
    include_tags=[],
    exclude_tags=[],
    size_tier="emerging",
    exclude_recent_months=6,
    max_candidates=20,
)
MESSAGE = "Shortlist emerging acts for a Thursday night; the crowd leans jazz."
ESTIMATED_QLOO_CALLS = (
    1 + len(VENUE.past_shows) + DEFAULT_MAX_CALLS + MAX_RECOMMEND_CALLS + SIGNAL_CALLS
)


def _print(title: str, payload: Any) -> None:
    print(f"\n=== {title}")
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


def _trace_rows(result: AgentResult) -> list[dict[str, Any]]:
    return [
        {
            "step": t.step,
            "tool": t.tool + (" (qloo mcp)" if t.qloo_tool else ""),
            "input": t.input,
            "status": t.status,
            "outcome": t.outcome,
            "ms": t.duration_ms,
        }
        for t in result.trace
    ]


async def main(args: argparse.Namespace) -> int:
    settings = AgentSettings.from_env()
    print(f"model: {settings.model_id}; LLM token cap: {settings.token_cap}; "
          f"redis: {'yes' if settings.redis_url else 'no (counters off)'}")  # fmt: skip
    print(f"estimated Qloo calls (cold cache): up to {ESTIMATED_QLOO_CALLS}")
    if not args.yes and input("Continue? [y/N] ").strip().lower() != "y":
        print("aborted")
        return 2

    qloo = await QlooMCP.from_env(run_id="agent_smoke")
    assert isinstance(qloo, QlooMCP)
    with qloo:
        venue_entity = await resolve_venue(VENUE.name, VENUE.city, qloo=qloo)
        acts: list[ResolvedEntity] = []
        for show in VENUE.past_shows:
            resolved = await resolve_entity(show.act, "artist", qloo=qloo)
            acts.append(resolved)
            state = (
                "confirmed" if resolved.confirmed else f"UNCONFIRMED ({len(resolved.candidates)})"
            )
            print(f"resolved {show.act!r}: {resolved.canonical_name} {state}")

        started = time.monotonic()
        result = await run_venue_agent(
            VENUE,
            CONSTRAINTS,
            MESSAGE,
            qloo=qloo,
            acts=acts,
            venue_entity=venue_entity if venue_entity.entity_id else None,
        )
        elapsed = time.monotonic() - started
        qloo_calls = list(result.store.calls.values()) if result.store else []

    _print("tool trace", _trace_rows(result))
    _print(
        "run",
        {
            "status": result.status,
            "stop_reason": result.stop_reason,
            "steps": f"{result.steps}/{MAX_STEPS}",
            "seconds": round(elapsed, 1),
            "usage": result.usage,
            "qloo_calls": len(qloo_calls),
            "qloo_cache_hits": sum(1 for c in qloo_calls if c.cache_hit),
            "qloo_workflows": sorted({c.workflow for c in qloo_calls}),
            "error": result.error,
        },
    )
    final = result.to_json()
    _print("final JSON", final)
    if result.model_answer:
        _print("model's own answer (verbatim)", result.model_answer)

    checks = {
        f"under {TIME_LIMIT_S}s": elapsed < TIME_LIMIT_S,
        f"at least {MIN_TOOL_CALLS} tool calls": result.steps >= MIN_TOOL_CALLS,
        "at least one Qloo MCP tool": any(t.qloo_tool for t in result.trace)
        or any(not c.cache_hit for c in qloo_calls),
        "final JSON validates": _validates(final),
        "shortlist or ask": result.status in ("ok", "ask"),
    }
    _print("DoD", checks)
    return 0 if all(checks.values()) else 1


def _validates(final: dict[str, Any]) -> bool:
    try:
        FinalAnswer.model_validate(final)
    except ValueError:
        return False
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    sys.exit(asyncio.run(main(parser.parse_args())))
