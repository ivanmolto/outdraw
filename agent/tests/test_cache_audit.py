"""scripts/cache_audit.py (T39): the audit itself, on a migrated throwaway Postgres (Docker),
and the ledger and the fake harness binary offline.

The audit is the test: two passes of the venue flow through the real service code, the fake
harness binary and the scripted model; it passes only when the second pass spawns no qloo
process and makes no model call, and the first stays within the per-run budgets. CI also
runs the script directly (`.github/workflows/ci.yml`).
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from collections import Counter
from pathlib import Path

import pytest
from conftest import load_script

audit = load_script("cache_audit")


def test_audit_passes(pg_url: str) -> None:
    audit.migrate(pg_url)
    result = asyncio.run(audit.run_audit(pg_url))
    report = result.report()
    assert result.passed, report

    first, second = result.passes
    assert second.venue_id == first.venue_id
    assert first.stage("profile").exec.total() == audit.PROFILE_BUDGET
    assert first.stage("run").mcp_starts == 1 and first.stage("run").tools["recommend"] >= 1
    assert first.total.model_calls >= 3 + audit.BRIEFS + 1, "agent, five briefs, compare"
    assert second.total.processes == 0 and second.total.tool_calls == 0
    assert second.total.model_calls == 0
    assert second.run.result is not None and second.run.result.cache_source == "semantic"
    assert "PASSED" in report and "second pass (repeat)" in report


def test_ledger_counts_what_the_fake_harness_wrote(tmp_path: Path) -> None:
    path = tmp_path / "qloo.log"
    ledger = audit.Ledger(path)
    assert ledger.take().processes == 0
    with path.open("a", encoding="utf-8") as fh:
        fh.write("exec describe\nexec describe\nmcp\ntool recommend\ntool trends\n")
    spend = ledger.take()
    assert spend.exec == Counter({"describe": 2}) and spend.mcp_starts == 1
    assert spend.tools == Counter({"recommend": 1, "trends": 1})
    assert spend.processes == 3 and spend.tool_calls == 2
    assert ledger.take().processes == 0, "each take reads only what came after the previous"


def test_fake_binary_answers_exec_and_logs_the_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(audit.LEDGER_ENV, str(tmp_path / "qloo.log"))
    binary = audit.fake_binary(tmp_path)
    (tmp_path / "input.json").write_text(json.dumps({"entity": "Synthetic Entity 3"}))
    proc = subprocess.run(
        [str(binary), "exec", "describe", "--input-file", str(tmp_path / "input.json")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    envelope = json.loads(proc.stdout)
    assert envelope["status"] == "ok" and envelope["results"][0]["name"] == "Synthetic Entity 3"
    assert (tmp_path / "qloo.log").read_text() == "exec describe\n"
