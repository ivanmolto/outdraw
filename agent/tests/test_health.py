"""GET /health reports the harness and Node versions probed once at startup."""

from __future__ import annotations

import subprocess
import tomllib
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

import draw
from draw.main import app
from draw.qloo import harness

FAKE_QLOO = "/fake/node_modules/.bin/qloo"
RunFn = Callable[..., subprocess.CompletedProcess[str]]


def fake_run(outputs: dict[str, tuple[int, str]], calls: list[list[str]]) -> RunFn:
    def run(cmd: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        code, out = outputs[cmd[0]]
        return subprocess.CompletedProcess(cmd, code, stdout=out, stderr="")

    return run


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    monkeypatch.setattr(harness, "qloo_bin", lambda: FAKE_QLOO)
    return []


def test_health_reports_versions(monkeypatch: pytest.MonkeyPatch, calls: list[list[str]]) -> None:
    outputs = {FAKE_QLOO: (0, "0.1.26\n"), "node": (0, "v22.19.0\n")}
    monkeypatch.setattr("draw.qloo.harness.subprocess.run", fake_run(outputs, calls))

    with TestClient(app) as client:
        res = client.get("/health")

    assert res.status_code == 200
    assert res.json() == {
        "ok": True,
        "version": draw.__version__,
        "qloo_harness_version": "0.1.26",
        "node_version": "v22.19.0",
    }


def test_versions_probed_once_not_per_request(
    monkeypatch: pytest.MonkeyPatch, calls: list[list[str]]
) -> None:
    outputs = {FAKE_QLOO: (0, "0.1.26\n"), "node": (0, "v22.19.0\n")}
    monkeypatch.setattr("draw.qloo.harness.subprocess.run", fake_run(outputs, calls))

    with TestClient(app) as client:
        for _ in range(3):
            client.get("/health")

    assert calls == [[FAKE_QLOO, "--version"], ["node", "--version"]]


def test_health_unavailable_when_harness_fails(
    monkeypatch: pytest.MonkeyPatch, calls: list[list[str]]
) -> None:
    outputs = {FAKE_QLOO: (1, ""), "node": (0, "v22.19.0\n")}
    monkeypatch.setattr("draw.qloo.harness.subprocess.run", fake_run(outputs, calls))

    with TestClient(app) as client:
        res = client.get("/health")

    assert res.status_code == 503
    assert res.json()["ok"] is False
    assert res.json()["qloo_harness_version"] is None
    assert res.json()["node_version"] == "v22.19.0"


def test_health_unavailable_when_harness_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(harness, "qloo_bin", lambda: None)

    def node_timeout(cmd: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd, harness.PROBE_TIMEOUT_S)

    monkeypatch.setattr("draw.qloo.harness.subprocess.run", node_timeout)

    with TestClient(app) as client:
        res = client.get("/health")

    assert res.status_code == 503
    assert res.json()["qloo_harness_version"] is None
    assert res.json()["node_version"] is None


def test_package_version_matches_pyproject() -> None:
    pyproject_path = harness.AGENT_DIR / "pyproject.toml"
    pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    assert draw.__version__ == pyproject["project"]["version"]
