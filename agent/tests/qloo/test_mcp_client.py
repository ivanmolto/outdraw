"""QlooMCP against a fake stdio MCP server that speaks the harness's contract."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from conftest import AGENT_DIR
from strands.types.tools import AgentTool, ToolUse
from tenacity import wait_none

from draw.qloo.budget import MemoryCounter
from draw.qloo.cache import MemoryCache
from draw.qloo.config import QlooConfig
from draw.qloo.envelope import BudgetExceeded, QlooEmpty, QlooError, QlooNeedsInput
from draw.qloo.mcp_client import WORKFLOWS, QlooMCP, QlooWorkflowTool, compact_envelope
from draw.qloo.recording import Recorder

FAKE_SERVER = Path(__file__).with_name("fake_mcp_server.py")
FAKE_COMMAND = [sys.executable, str(FAKE_SERVER)]
# Assembled at runtime so the gitleaks pre-commit hook does not mistake it for a real key.
FAKE_KEY = "-".join(["fake", "qloo", "key", "0" * 12])
MAX_TOOL_OUTPUT = 2048


@pytest.fixture(autouse=True)
def fake_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QLOO_API_KEY", FAKE_KEY)
    monkeypatch.delenv("FAKE_QLOO_MODE", raising=False)


def gateway(**kwargs: Any) -> QlooMCP:
    kwargs.setdefault("retry_wait", wait_none())
    return QlooMCP(QlooConfig(qloo_bin="/fake/qloo"), command=FAKE_COMMAND, **kwargs)


def call_number(result: Any) -> int:
    execution = getattr(result, "execution", None)
    assert isinstance(execution, dict)
    return int(execution["call_number"])


# --- connect -------------------------------------------------------------------------


def test_env_is_passed_through_and_readiness_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DRAW_TEST_MARKER", "through")
    with gateway() as qloo:
        assert qloo.connected
        assert qloo.capabilities["adapter"]["ready"] is True
        assert qloo.capabilities["env"] == {"QLOO_API_KEY": True, "DRAW_TEST_MARKER": "through"}
        assert "result_schema" not in qloo.capabilities
    assert not qloo.connected


def test_missing_key_fails_fast_with_a_readable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """The server never sees the key: readiness is false and the gateway stops at once."""
    monkeypatch.delenv("QLOO_API_KEY")
    qloo = gateway()
    with pytest.raises(QlooError) as info:
        qloo.start()
    assert info.value.code == "mcp_not_ready"
    assert "QLOO_API_KEY" in info.value.recovery and "ready=false" in info.value.recovery
    assert "find_tags" in info.value.recovery  # the missing workflows are named
    assert not qloo.connected


def test_server_that_cannot_start_is_reported() -> None:
    qloo = QlooMCP(
        QlooConfig(qloo_bin="/fake/qloo"),
        command=[sys.executable, "-c", "import sys; sys.exit(3)"],
        startup_timeout_s=5,
    )
    with pytest.raises(QlooError) as info:
        qloo.start()
    assert info.value.code == "mcp_start_failed"
    assert not qloo.connected


def test_default_command_is_the_pinned_harness() -> None:
    assert QlooMCP(QlooConfig(qloo_bin="/x/qloo")).server_command() == ["/x/qloo", "mcp"]
    with pytest.raises(QlooError) as info:
        QlooMCP(QlooConfig(qloo_bin=None)).server_command()
    assert info.value.code == "harness_missing"


async def test_calls_before_start_are_refused() -> None:
    with pytest.raises(QlooError) as info:
        await gateway().find_tags("jazz")
    assert info.value.code == "mcp_not_connected"


# --- envelopes -----------------------------------------------------------------------


async def test_ok_envelope_is_normalised_with_gateway_fields() -> None:
    with gateway() as qloo:
        result = await qloo.find_tags("jazz", limit=5)
    assert result.status == "ok" and result.result_count == 5 and result.workflow == "find_tags"
    assert len(result.call_id) == 36 and result.cache_hit is False
    assert call_number(result) == 1


async def test_auth_error_envelope_maps_to_qloo_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """The key disappears after startup: every call is a non-retryable QLOO_AUTH error."""
    monkeypatch.setenv("FAKE_QLOO_MODE", "auth_error")
    with gateway() as qloo:
        with pytest.raises(QlooError) as info:
            await qloo.find_tags("jazz")
    assert info.value.code == "QLOO_AUTH" and info.value.retryable is False
    assert "QLOO_API_KEY" in info.value.recovery
    assert info.value.envelope is not None and info.value.envelope.status == "error"


async def test_missing_status_and_non_json_results_are_normalised() -> None:
    with gateway() as qloo:
        with pytest.raises(QlooError) as nostatus:
            await qloo.find_tags("nostatus")
        with pytest.raises(QlooError) as garbage:
            await qloo.find_tags("garbage")
    assert nostatus.value.code == "BOOM"
    assert nostatus.value.envelope is not None and nostatus.value.envelope.status == "error"
    assert garbage.value.code == "mcp_failed" and "not an envelope" in garbage.value.recovery


async def test_needs_input_and_empty_branches() -> None:
    with gateway() as qloo:
        with pytest.raises(QlooNeedsInput) as needs:
            await qloo.describe("Blue Note Jazz Club", type="place")
        with pytest.raises(QlooEmpty):
            await qloo.find_tags("nonsense", semantic=False)
    assert len(needs.value.candidates) == 2
    assert all(c["field"] == "entity" for c in needs.value.candidates)


async def test_retryable_error_is_retried_once_then_ok() -> None:
    with gateway() as qloo:
        result = await qloo.find_tags("flaky")
    assert result.status == "ok"
    assert call_number(result) == 2, "first answer was retryable, second was ok"


# --- cache, counter, recording ---------------------------------------------------------


async def test_cache_and_counter_apply_to_mcp_calls() -> None:
    counter = MemoryCounter(cap=1)
    cache = MemoryCache()
    with gateway(cache=cache, counter=counter) as qloo:
        first = await qloo.find_tags("jazz")
        second = await qloo.find_tags("jazz")
        assert second.cache_hit is True and call_number(second) == call_number(first)
        assert second.call_id != first.call_id
        assert await counter.used() == 1 and len(cache) == 1
        with pytest.raises(BudgetExceeded):
            await qloo.find_tags("indie rock")  # a miss: needs a slot the cap no longer has
        assert await counter.used() == 1


async def test_calls_are_recorded_with_the_key_redacted(tmp_path: Path) -> None:
    recorder = Recorder(tmp_path, secret=FAKE_KEY)
    with gateway(recorder=recorder, run_id="run_test") as qloo:
        result = await qloo.find_tags("jazz")
    files = list(tmp_path.rglob("*.json"))
    assert len(files) == 1 and files[0].stem == result.call_id
    entry = json.loads(files[0].read_text("utf-8"))
    assert entry["workflow"] == "find_tags" and entry["status"] == "ok"
    assert entry["request"] == {"query": "jazz", "limit": 5}
    assert FAKE_KEY not in files[0].read_text("utf-8")


# --- Strands tools ---------------------------------------------------------------------


async def collect(tool: AgentTool, **input_: Any) -> dict[str, Any]:
    tool_use = ToolUse(toolUseId="t1", name=tool.tool_name, input=input_)
    events = [e async for e in tool.stream(tool_use, {})]
    result: dict[str, Any] = events[-1]["tool_result"]
    return result


async def test_tools_keep_workflow_names_and_server_schemas() -> None:
    with gateway() as qloo:
        tools = qloo.tools()
        assert [t.tool_name for t in tools] == list(WORKFLOWS)
        for t in tools:
            spec = t.tool_spec
            assert spec["name"] == t.tool_name and spec["description"]
            schema = spec["inputSchema"]["json"]
            assert schema["type"] == "object" and "properties" in schema
        by_name = {t.tool_name: t for t in tools}
        assert isinstance(by_name["find_tags"], QlooWorkflowTool)

        ok = await collect(by_name["find_tags"], query="jazz", limit=5)
        assert ok["status"] == "success" and ok["toolUseId"] == "t1"
        payload = json.loads(ok["content"][0]["text"])
        assert payload["status"] == "ok" and len(payload["results"]) == 5
        assert payload["workflow"] == "find_tags" and len(payload["call_id"]) == 36
        assert "provenance" not in payload and "properties" not in payload["results"][0]
        assert len(ok["content"][0]["text"]) < MAX_TOOL_OUTPUT

        asks = await collect(by_name["describe"], entity="Blue Note Jazz Club", type="place")
        assert asks["status"] == "success", "needs_input is an outcome, not a tool failure"
        ask_payload = json.loads(asks["content"][0]["text"])
        assert ask_payload["status"] == "needs_input" and len(ask_payload["candidates"]) == 2
        assert {"id", "name", "type"} <= set(ask_payload["candidates"][0])

        failed = await collect(by_name["find_tags"], query="garbage")
        assert failed["status"] == "error"
        fail_payload = json.loads(failed["content"][0]["text"])
        assert fail_payload["status"] == "error" and fail_payload["error"]["code"] == "mcp_failed"


async def test_every_tool_output_stays_compact() -> None:
    calls: dict[str, dict[str, Any]] = {
        "find_tags": {"query": "jazz"},
        "describe": {"entity": "Radiohead", "type": "artist"},
        "recommend": {"target_type": "artist", "signals": ["a", "b"], "limit": 10},
        "rank": {"options": ["a", "b"], "option_type": "artist", "signals": ["c"]},
        "where_popular": {"entity": "a", "entity_type": "artist", "within": "New York, NY"},
        "compare_audiences": {"group_a": ["a"], "group_b": ["b"]},
        "audience_demographics": {"entity": "a", "entity_type": "artist"},
        "trends": {
            "entities": ["a"],
            "entity_type": "artist",
            "start_date": "2025-10-01",
            "end_date": "2026-10-01",
        },
    }
    with gateway() as qloo:
        for tool in qloo.tools():
            result = await collect(tool, **calls[tool.tool_name])
            text = result["content"][0]["text"]
            assert len(text) < MAX_TOOL_OUTPUT, (tool.tool_name, len(text))
            assert json.loads(text)["status"] == "ok", tool.tool_name


def test_compact_envelope_without_an_envelope_reports_the_error() -> None:
    error = QlooError("harness_timeout", recovery="No answer in 20s.")
    payload = compact_envelope(None, workflow="trends", error=error)
    assert payload == {
        "status": "error",
        "workflow": "trends",
        "error": {"code": "harness_timeout", "retryable": False, "recovery": "No answer in 20s."},
        "summary": "harness_timeout: No answer in 20s.",
    }


# --- integration --------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(not os.environ.get("RUN_INTEGRATION"), reason="needs the pinned harness")
def test_real_harness_without_a_key_reports_qloo_auth(tmp_path: Path) -> None:
    """The real `qloo mcp` with no key and no ~/.qloo/config fails readiness, in a subprocess."""
    script = (
        "from draw.qloo import QlooMCP\n"
        "from draw.qloo.config import QlooConfig\n"
        "from draw.qloo.envelope import QlooError\n"
        "try:\n"
        "    QlooMCP(QlooConfig()).start()\n"
        "except QlooError as exc:\n"
        "    print(exc.code); print(exc.recovery)\n"
    )
    env = {k: v for k, v in os.environ.items() if k != "QLOO_API_KEY"}
    env["HOME"] = str(tmp_path)  # hides ~/.qloo/config
    proc = subprocess.run(
        [sys.executable, "-c", script], cwd=AGENT_DIR, env=env, capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr[-800:]
    assert proc.stdout.splitlines()[0] == "mcp_not_ready"
    assert "QLOO_API_KEY" in proc.stdout
