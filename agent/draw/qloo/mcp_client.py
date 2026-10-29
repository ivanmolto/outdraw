"""`QlooMCP`: the gateway over the `qloo mcp` stdio server, for the agent's tool calls.

Same per-call pipeline as `QlooExec` (cache, daily counter, bounded retries, recording,
status branching): `QlooMCP` only replaces the transport. Instead of spawning `qloo exec`
per call, it keeps one `qloo mcp` process alive behind Strands' `MCPClient` and sends each
workflow as an MCP tool call. The server is spawned with the **full** process environment;
the MCP SDK forwards only a handful of variables by default and the harness would then
never see `QLOO_API_KEY` (every call answers `QLOO_AUTH`).

Contract of the harness's MCP server (0.1.26, verified on the real key):

- tools are `qloo_capabilities` plus `qloo_<workflow>` for the nine shared workflows (the
  eight Outdraw uses and `entity_tags`); Outdraw exposes them to the model under the bare
  workflow names;
- `qloo_capabilities` returns a manifest whose `adapter.ready` and
  `adapter.supported_operation_ids` say whether the server can execute workflows. Without
  a credential it answers `ready: false` with an empty list, so readiness is checked once
  on connect and the gateway fails fast with a readable message;
- every tool result is the six-status envelope as JSON text (also as structured content);
  `error` envelopes additionally set the MCP `isError` flag.

Use it as a context manager (`with QlooMCP(...) as qloo:`); the workflow coroutines are
inherited from `QlooExec`, and `tools()` returns the Strands tools for the agent.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from collections.abc import AsyncGenerator, Callable, Mapping, Sequence
from datetime import timedelta
from types import TracebackType
from typing import Any, Final, cast, get_args

from mcp import StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import Tool as MCPTool
from strands.tools.mcp import MCPClient
from strands.tools.mcp._compat import input_schema
from strands.tools.mcp.mcp_types import MCPTransport
from strands.types._events import ToolResultEvent
from strands.types.tools import AgentTool, ToolResult, ToolSpec, ToolUse

from draw.qloo.config import QlooConfig
from draw.qloo.envelope import (
    QlooEmpty,
    QlooEnvelope,
    QlooError,
    QlooGatewayError,
    candidates_of,
    normalise,
)
from draw.qloo.exec_client import QlooExec, Workflow

__all__ = [
    "MCP_TOOL_PREFIX",
    "READINESS_TOOL",
    "WORKFLOWS",
    "QlooMCP",
    "QlooWorkflowTool",
    "compact_envelope",
]

log = logging.getLogger("draw.qloo.mcp")

WORKFLOWS: Final[tuple[Workflow, ...]] = get_args(Workflow)
"""The eight workflows Outdraw exposes, in the task's order."""
MCP_TOOL_PREFIX: Final = "qloo_"
READINESS_TOOL: Final = "qloo_capabilities"
STARTUP_TIMEOUT_S: Final = 30
MAX_ITEMS: Final = 10
"""Rows of a result kept in what the model sees; the full envelope stays server-side."""
DICT_ITEMS: Final = 6
"""Shared tags kept from a `compare_audiences` result (its rows carry more fields)."""
MAX_TEXT: Final = 160
ROW_FIELDS: Final = (
    "entity_id",
    "id",
    "tag_id",
    "name",
    "type",
    "subtype",
    "popularity",
    "affinity",
    "score",
    "address",
)
MODEL_FIELDS: Final = ("status", "summary", "result_count", "call_id", "workflow", "cache_hit")


def mcp_tool_name(workflow: str) -> str:
    return MCP_TOOL_PREFIX + workflow


class QlooMCP(QlooExec):
    """`QlooExec` with `qloo mcp` as the transport. One process per gateway, started on enter."""

    def __init__(
        self,
        config: QlooConfig | None = None,
        *,
        command: Sequence[str] | None = None,
        startup_timeout_s: int = STARTUP_TIMEOUT_S,
        **gateway: Any,
    ) -> None:
        """`command` overrides `<qloo_bin> mcp` (tests point it at a fake server).

        `**gateway` are `QlooExec`'s keyword arguments: `cache`, `counter`, `recorder`,
        `retry_wait`, `run_id`, `observer`.
        """
        super().__init__(config, **gateway)
        self._command = list(command) if command else None
        self._startup_timeout_s = startup_timeout_s
        self._client: MCPClient | None = None
        self._specs: dict[str, MCPTool] = {}
        self.capabilities: dict[str, Any] = {}
        """The `qloo_capabilities` manifest (minus its result schema) read on connect."""

    # --- lifecycle ------------------------------------------------------------------

    def __enter__(self) -> QlooMCP:
        return self.start()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.stop()

    @property
    def connected(self) -> bool:
        return self._client is not None

    def server_command(self) -> list[str]:
        if self._command:
            return list(self._command)
        if self.config.qloo_bin is None:
            raise QlooError(
                "harness_missing",
                recovery="Install the pinned harness with `npm ci` inside /agent.",
            )
        return [self.config.qloo_bin, "mcp"]

    def start(self) -> QlooMCP:
        """Spawn the server with the full environment, check readiness, read the tool specs."""
        if self._client is not None:
            return self
        command = self.server_command()
        params = StdioServerParameters(
            command=command[0],
            args=command[1:],
            # The whole environment, not the SDK's short default list: the harness reads
            # QLOO_API_KEY / QLOO_BASE_URL / HOME from it.
            env=dict(os.environ),
        )
        # mcp 2.x types the stdio streams more narrowly than Strands' transport alias.
        transport = cast(Callable[[], MCPTransport], lambda: stdio_client(params))
        client = MCPClient(transport, startup_timeout=self._startup_timeout_s)
        try:
            client.start()
        except Exception as exc:
            raise QlooError(
                "mcp_start_failed",
                recovery=f"Could not start `{' '.join(command)}`: {exc}",
            ) from exc
        self._client = client
        try:
            self._check_ready()
            self._load_specs()
        except BaseException:
            self.stop()
            raise
        log.info("qloo_mcp connected command=%s workflows=%d", command[0], len(self._specs))
        return self

    def stop(self) -> None:
        client, self._client = self._client, None
        self._specs = {}
        if client is not None:
            client.stop(None, None, None)

    async def aclose(self) -> None:
        self.stop()
        await super().aclose()

    def _require_client(self) -> MCPClient:
        if self._client is None:
            raise QlooError(
                "mcp_not_connected",
                recovery="Use `with QlooMCP(...) as qloo:` (or call `start()`) before any call.",
            )
        return self._client

    def _check_ready(self) -> None:
        client = self._require_client()
        result = client.call_tool_sync(tool_use_id="readiness", name=READINESS_TOOL, arguments={})
        payload = payload_of(result)
        if payload is None:
            raise QlooError(
                "mcp_not_ready",
                recovery=f"`{READINESS_TOOL}` returned no manifest: {text_of(result)[:MAX_TEXT]}",
            )
        adapter = payload.get("adapter")
        adapter = adapter if isinstance(adapter, dict) else {}
        ready = bool(adapter.get("ready"))
        supported = adapter.get("supported_operation_ids")
        supported_ids = {str(s) for s in supported} if isinstance(supported, list) else set()
        missing = [w for w in WORKFLOWS if w not in supported_ids]
        if not ready or missing:
            raise QlooError(
                "mcp_not_ready",
                recovery=(
                    f"`qloo mcp` reports ready={str(ready).lower()}"
                    f"{', missing workflows: ' + ', '.join(missing) if missing else ''}. "
                    "Usually the server has no credential: set QLOO_API_KEY (and "
                    "QLOO_BASE_URL / QLOO_TRUSTED_BASE_URL on a server without ~/.qloo/config) "
                    "in the agent's environment, check with `qloo setup --status`, then restart."
                ),
            )
        self.capabilities = {k: v for k, v in payload.items() if k != "result_schema"}

    def _load_specs(self) -> None:
        client = self._require_client()
        listed = {t.mcp_tool.name: t.mcp_tool for t in client.list_tools_sync()}
        missing = [w for w in WORKFLOWS if mcp_tool_name(w) not in listed]
        if missing:
            raise QlooError(
                "mcp_tools_missing",
                recovery=(
                    "The MCP server does not expose "
                    + ", ".join(mcp_tool_name(w) for w in missing)
                    + "; check the pinned harness version (decision 001)."
                ),
            )
        self._specs = {w: listed[mcp_tool_name(w)] for w in WORKFLOWS}

    # --- transport (the only piece that differs from `qloo exec`) ---------------------

    async def _spawn(self, workflow: str, input_: dict[str, Any]) -> QlooEnvelope:
        client = self._require_client()
        result = await client.call_tool_async(
            tool_use_id=str(uuid.uuid4()),
            name=mcp_tool_name(workflow),
            arguments=dict(input_),
            read_timeout_seconds=timedelta(seconds=self.config.timeout_s),
        )
        raw = payload_of(result)
        if raw is None:
            raise QlooError(
                "mcp_failed",
                recovery=(
                    f"No envelope from `{mcp_tool_name(workflow)}`: "
                    f"{text_of(result)[:MAX_TEXT] or 'empty result'}"
                ),
            )
        raw.setdefault("operation", workflow)
        return normalise(raw)

    # --- Strands tools ----------------------------------------------------------------

    def tools(self) -> list[AgentTool]:
        """The eight Qloo workflows as Strands tools, named exactly as the workflows."""
        self._require_client()
        return [QlooWorkflowTool(self, w, self._specs[w]) for w in WORKFLOWS]

    async def call_for_agent(self, workflow: Workflow, input_: Mapping[str, Any]) -> dict[str, Any]:
        """Run `workflow` and return the compact six-status payload the model sees.

        Never raises on a Qloo outcome: `needs_input`, `empty` and `error` come back as
        payloads with that status so the agent can branch; `BudgetExceeded` and harness
        failures become `error` payloads with a `recovery` line.
        """
        try:
            result = await self.exec(workflow, dict(input_))
        except QlooGatewayError as exc:
            return compact_envelope(getattr(exc, "envelope", None), workflow=workflow, error=exc)
        return compact_envelope(result)


class QlooWorkflowTool(AgentTool):
    """A Qloo workflow as a Strands tool: the server's schema, the gateway's pipeline."""

    def __init__(self, gateway: QlooMCP, workflow: Workflow, mcp_tool: MCPTool) -> None:
        super().__init__()
        self.gateway = gateway
        self.workflow: Workflow = workflow
        self.mcp_tool = mcp_tool

    @property
    def tool_name(self) -> str:
        return self.workflow

    @property
    def tool_spec(self) -> ToolSpec:
        description = self.mcp_tool.description or f"Qloo {self.workflow} workflow."
        return {
            "name": self.workflow,
            "description": description,
            "inputSchema": {"json": input_schema(self.mcp_tool)},
        }

    @property
    def tool_type(self) -> str:
        return "qloo"

    async def stream(
        self, tool_use: ToolUse, invocation_state: dict[str, Any], **kwargs: Any
    ) -> AsyncGenerator[Any, None]:
        payload = await self.gateway.call_for_agent(self.workflow, tool_use.get("input") or {})
        result: ToolResult = {
            "toolUseId": tool_use["toolUseId"],
            "status": "error" if payload["status"] == "error" else "success",
            "content": [{"text": json.dumps(payload, ensure_ascii=False)}],
        }
        yield ToolResultEvent(result)


# --- result helpers -------------------------------------------------------------------


def text_of(result: ToolResult) -> str:
    return "\n".join(str(c["text"]) for c in result.get("content") or [] if "text" in c)


def payload_of(result: ToolResult) -> dict[str, Any] | None:
    """The JSON object a tool result carries: structured content first, else its text."""
    structured = cast(dict[str, Any], result).get("structuredContent")
    if isinstance(structured, dict):
        return dict(structured)
    text = text_of(result).strip()
    # Some servers prefix an error envelope with a message ("Error executing tool x: {...}");
    # the envelope is the first `{` to the last `}`.
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        loaded = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return loaded if isinstance(loaded, dict) else None


def _clip(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_TEXT:
        return value[: MAX_TEXT - 1] + "…"
    if isinstance(value, float):
        return round(value, 4)
    return value


def _row(row: Any) -> Any:
    if not isinstance(row, dict):
        return _clip(row)
    return {k: _clip(row[k]) for k in ROW_FIELDS if k in row}


def _series(series: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in series[:MAX_ITEMS] if isinstance(series, list) else []:
        if not isinstance(item, dict):
            continue
        raw_entity = item.get("entity")
        entity: dict[str, Any] = raw_entity if isinstance(raw_entity, dict) else {}
        points = [p for p in item.get("points") or [] if isinstance(p, dict)]
        percentiles = [
            p["population_percentile"]
            for p in points
            if isinstance(p.get("population_percentile"), int | float)
        ]
        out.append(
            {
                "entity_id": entity.get("entityId") or entity.get("input"),
                "name": entity.get("name"),
                "points": len(points),
                # The recording lists points newest first.
                "latest_percentile": _clip(percentiles[0]) if percentiles else None,
                "earliest_percentile": _clip(percentiles[-1]) if percentiles else None,
            }
        )
    return out


def compact_envelope(
    envelope: QlooEnvelope | None,
    *,
    workflow: str | None = None,
    error: QlooGatewayError | None = None,
) -> dict[str, Any]:
    """What the model sees for one call: the status contract plus a trimmed result.

    Keeps `status`, `summary`, `result_count`, `call_id`, `workflow`, `cache_hit`, up to
    `MAX_ITEMS` rows reduced to `ROW_FIELDS`, `candidates` for `needs_input`, `series` for
    `trends`, and `error` with its `recovery`. Provenance, explainability and properties stay
    out of the prompt; `get_evidence` reads them from the store by `call_id`.
    """
    data = envelope.model_dump(mode="json") if envelope is not None else {}
    out: dict[str, Any] = {k: data[k] for k in MODEL_FIELDS if k in data}
    out.setdefault("status", "error")
    if workflow and "workflow" not in out:
        out["workflow"] = workflow

    results = data.get("results")
    if isinstance(results, list):
        out["results"] = [_row(r) for r in results[:MAX_ITEMS]]
        if len(results) > MAX_ITEMS:
            out["results_truncated"] = len(results) - MAX_ITEMS
    elif isinstance(results, dict):
        # compare_audiences: {tags[], a[], b[], matchEntities[]}. The shared tags are what
        # Outdraw reads; the per-side lists are reported as counts.
        out["results"] = {
            k: ([_row(r) for r in v[:DICT_ITEMS]] if k == "tags" else len(v))
            if isinstance(v, list)
            else _clip(v)
            for k, v in results.items()
        }
    if isinstance(data.get("series"), list):
        out["series"] = _series(data["series"])
    if envelope is not None and envelope.status == "needs_input":
        out["candidates"] = [_row(c) for c in candidates_of(envelope)[:MAX_ITEMS]]
    if isinstance(data.get("error"), dict):
        out["error"] = {k: _clip(v) for k, v in data["error"].items() if k != "layer"}

    if error is not None:
        if isinstance(error, QlooEmpty):
            # The harness answers `ok` with no rows (README); the model branches on `empty`.
            out["status"] = "empty"
            if not out.get("summary"):
                out["summary"] = str(error)
        elif isinstance(error, QlooError):
            out["status"] = "error"
            out.setdefault(
                "error",
                {"code": error.code, "retryable": error.retryable, "recovery": error.recovery},
            )
            out.setdefault("summary", str(error))
        elif out["status"] == "error":
            out.setdefault("error", {"code": type(error).__name__, "retryable": False})
            out.setdefault("summary", str(error))
        out.setdefault("summary", str(error))
    return out
