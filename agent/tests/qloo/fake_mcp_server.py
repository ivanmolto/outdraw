"""A stand-in for `qloo mcp`: the same tool names and envelope contract, fixture data.

Run as a subprocess by `test_mcp_client.py` (`python fake_mcp_server.py`, stdio). It
mirrors what the real harness does on 0.1.26:

- `qloo_capabilities` answers `adapter.ready: false` with no supported operations when
  `QLOO_API_KEY` is missing from *its* environment (so a client that forwards only the
  SDK's default variables fails readiness, exactly like the real server), and echoes the
  environment it sees under `env` so tests can prove the pass-through;
- `qloo_<workflow>` tools return synthetic fixture envelopes as structured content plus
  JSON text (`qloo_find_tags` text only, to cover both client paths); `error` envelopes
  are raised so they arrive with the MCP `isError` flag (`MCPServer` prefixes the text
  with "Error executing tool ...: "; the real harness sends the bare envelope, and the
  client accepts both);
- each live envelope carries `execution.call_number`, a per-process counter, so a cache
  hit (same number twice) is distinguishable from a second call.

`FAKE_QLOO_MODE=auth_error` keeps readiness green but answers every workflow with the
`QLOO_AUTH` error envelope (the key vanished after startup).
"""

from __future__ import annotations

import itertools
import json
import os
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "qloo"
WORKFLOWS = [
    "recommend",
    "rank",
    "describe",
    "where_popular",
    "compare_audiences",
    "entity_tags",
    "audience_demographics",
    "trends",
    "find_tags",
]
AUTH_ERROR: dict[str, Any] = {
    "schema_version": "1.0-preview.1",
    "status": "error",
    "summary": "Qloo workflow execution requires a configured Qloo API credential.",
    "results": [],
    "result_count": 0,
    "error": {
        "code": "QLOO_AUTH",
        "layer": "qloo",
        "retryable": False,
        "recovery": "Run qloo setup or set QLOO_API_KEY, then restart the MCP server.",
    },
}

server = MCPServer("fake-qloo")
_calls = itertools.count(1)
_flaky_seen = {"n": 0}


def _has_key() -> bool:
    return bool(os.environ.get("QLOO_API_KEY"))


def _mode() -> str:
    return os.environ.get("FAKE_QLOO_MODE", "ok")


def _fixture(workflow: str, name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / workflow / f"{name}.json").read_text("utf-8"))
    return data


def _answer(workflow: str, envelope: dict[str, Any]) -> dict[str, Any]:
    """Return an envelope (text + structured content), or raise it so it arrives `isError`."""
    if _mode() == "auth_error" or not _has_key():
        envelope = {**AUTH_ERROR, "operation": workflow}
    envelope.setdefault("operation", workflow)
    envelope.setdefault("execution", {})
    envelope["execution"]["call_number"] = next(_calls)
    if envelope.get("status") == "error":
        # `ToolError` keeps the text; any other exception is masked as "Error executing tool".
        raise ToolError(json.dumps(envelope))
    return envelope


@server.tool(name="qloo_capabilities")
def capabilities() -> dict[str, Any]:
    ready = _has_key()
    return {
        "manifest_schema_version": "1.1",
        "contract_version": "1.0.0",
        "capabilities_tool": "qloo_capabilities",
        "result_schema": {"type": "object"},
        "adapter": {
            "id": "fake_qloo_mcp",
            "ready": ready,
            "supported_operation_ids": WORKFLOWS if ready else [],
            "tool_name_mapping": {w: f"qloo_{w}" for w in WORKFLOWS},
        },
        "env": {
            "QLOO_API_KEY": ready,
            "DRAW_TEST_MARKER": os.environ.get("DRAW_TEST_MARKER"),
        },
    }


@server.tool(name="qloo_find_tags", structured_output=False)
def find_tags(query: str, semantic: bool = True, limit: int = 10) -> str:
    """Text-only results, like a server without structured content."""
    if query == "nostatus":
        return json.dumps({"error": {"code": "BOOM"}, "operation": "find_tags"})
    if query == "garbage":
        return "this is not an envelope"
    if query == "flaky":
        _flaky_seen["n"] += 1
        if _flaky_seen["n"] == 1:
            return json.dumps(
                _answer("find_tags", _fixture("find_tags", "derived_error_retryable"))
            )
        return json.dumps(_answer("find_tags", _fixture("find_tags", "jazz")))
    if query == "nonsense":
        return json.dumps(_answer("find_tags", _fixture("find_tags", "nonsense_query_exact")))
    return json.dumps(_answer("find_tags", _fixture("find_tags", "jazz")))


@server.tool(name="qloo_describe")
def describe(entity: str, type: str | None = None) -> dict[str, Any]:  # noqa: A002 - harness field name
    if entity.lower().startswith("blue note"):
        return _answer("describe", _fixture("describe", "blue_note_jazz_club"))
    return _answer("describe", _fixture("describe", "radiohead"))


@server.tool(name="qloo_recommend")
def recommend(
    target_type: str,
    signals: list[str] | None = None,
    signal_tags: list[str] | None = None,
    signal_location: str | None = None,
    filter_location: str | None = None,
    include_tags: list[str] | None = None,
    include_tags_operator: str | None = None,
    exclude_tags: list[str] | None = None,
    exclude_tags_operator: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    name = {"artist": "crowd_artists", "place": "crowd_places", "movie": "crowd_movies"}.get(
        target_type, "crowd_brands"
    )
    return _answer("recommend", _fixture("recommend", name))


@server.tool(name="qloo_rank")
def rank(options: list[str], option_type: str, signals: list[str]) -> dict[str, Any]:
    return _answer("rank", _fixture("rank", "five_artists_vs_signals"))


@server.tool(name="qloo_where_popular")
def where_popular(
    entity: str, within: str, entity_type: str | None = None, limit: int = 10
) -> dict[str, Any]:
    return _answer("where_popular", _fixture("where_popular", "radiohead_new_york_ny"))


@server.tool(name="qloo_compare_audiences")
def compare_audiences(group_a: list[str], group_b: list[str], limit: int = 10) -> dict[str, Any]:
    return _answer("compare_audiences", _fixture("compare_audiences", "two_artists"))


@server.tool(name="qloo_entity_tags")
def entity_tags(entities: list[str], entity_type: str | None = None) -> dict[str, Any]:
    return _answer("entity_tags", _fixture("find_tags", "jazz"))


@server.tool(name="qloo_audience_demographics")
def audience_demographics(entity: str, entity_type: str | None = None) -> dict[str, Any]:
    return _answer("audience_demographics", _fixture("audience_demographics", "radiohead"))


@server.tool(name="qloo_trends")
def trends(
    entities: list[str], entity_type: str, start_date: str, end_date: str, limit: int = 10
) -> dict[str, Any]:
    return _answer("trends", _fixture("trends", "three_artists_last_12_months"))


if __name__ == "__main__":
    server.run("stdio")
