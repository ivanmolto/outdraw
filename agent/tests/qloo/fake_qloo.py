#!/usr/bin/env python3
"""A stand-in for the `qloo` harness binary: `exec <workflow> --input-file <path>` and `mcp`.

Where the fake harness in `tests/conftest.py` replaces `create_subprocess_exec` inside the
process, this one is a real executable the gateway spawns, so a run through it exercises
the production spawn code (full environment, JSON on stdout, exit codes) and every spawn
is a real process. `scripts/cache_audit.py` points `QlooConfig.qloo_bin` at a shell wrapper
around this file and counts the processes.

- `exec <workflow> --input-file <path>` prints one synthetic fixture envelope from
  `tests/fixtures/qloo` (`route`, the same choices as the API tests' `qloo exec` router,
  plus a name-keyed `describe` so the three crowd acts auto-confirm) and exits 0.
- `mcp` runs `fake_mcp_server.py` over stdio (the agent's `qloo mcp`).

With `FAKE_QLOO_LOG=<file>` every start appends one line to that file (`exec <workflow>`
or `mcp`), and the MCP server appends `tool <workflow>` per tool call: a process-level
record no gateway code can hide. Without a key in the environment the MCP server reports
not ready, as the real one does.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from functools import cache
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
FIXTURES = HERE.parents[0] / "fixtures" / "qloo"
LOG_ENV = "FAKE_QLOO_LOG"


def log_event(line: str) -> None:
    """Append `line` to `$FAKE_QLOO_LOG` when set (the audit's process ledger)."""
    path = os.environ.get(LOG_ENV)
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def fixture(workflow: str, name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / workflow / f"{name}.json").read_text("utf-8"))
    return data


@cache
def describe_by_name() -> dict[str, str]:
    """Synthetic act name -> `describe` fixture, for the crowd acts (exact-name matches)."""
    out: dict[str, str] = {}
    for path in sorted((FIXTURES / "describe").glob("crowd_act_*.json")):
        rows = json.loads(path.read_text("utf-8")).get("results") or []
        if rows and isinstance(rows[0].get("name"), str):
            out[rows[0]["name"]] = path.stem
    return out


def route(workflow: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Which fixture envelope answers an `exec` call."""
    if workflow == "describe":
        name = str(payload.get("entity", ""))
        if name in describe_by_name():
            return fixture("describe", describe_by_name()[name])
        if name.lower().startswith("blue note"):
            return fixture("describe", "blue_note_jazz_club")
        return fixture("describe", "radiohead")
    if workflow == "find_tags":
        return fixture("find_tags", "jazz")
    if workflow == "recommend":
        target = str(payload.get("target_type"))
        crowd = {"artist": "crowd_artists", "place": "crowd_places", "movie": "crowd_movies"}
        return fixture("recommend", crowd.get(target, "crowd_brands"))
    if workflow == "compare_audiences":
        return fixture("compare_audiences", "crowd_genres")
    if workflow == "audience_demographics":
        return fixture("audience_demographics", "crowd_demographics_1")
    if workflow == "where_popular":
        return fixture("where_popular", "radiohead_new_york_ny")
    if workflow == "trends":
        return fixture("trends", "three_artists_last_12_months")
    if workflow == "rank":
        return fixture("rank", "five_artists_vs_signals")
    raise SystemExit(f"fake qloo: no fixture route for workflow {workflow!r}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="qloo", description="fake Qloo harness")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("exec")
    run.add_argument("workflow")
    run.add_argument("--input-file", required=True)
    sub.add_parser("mcp")
    sub.add_parser("--version")
    args = parser.parse_args(argv)

    if args.command == "exec":
        log_event(f"exec {args.workflow}")
        payload = json.loads(Path(args.input_file).read_text("utf-8"))
        envelope = route(args.workflow, payload)
        envelope.setdefault("operation", args.workflow)
        print(json.dumps(envelope))
        return 0
    if args.command == "mcp":
        log_event("mcp")
        sys.path.insert(0, str(HERE))
        from fake_mcp_server import server

        server.run("stdio")
        return 0
    print("0.0.0-fake")
    return 0


if __name__ == "__main__":
    sys.exit(main())
