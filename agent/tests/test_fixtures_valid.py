"""Every synthetic Qloo fixture is well-formed and covers the statuses the code branches on."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import FIXTURES_DIR, STATUSES, WORKFLOWS, envelope_files, read_json

ENVELOPES = envelope_files()


def test_fixtures_exist_for_every_workflow() -> None:
    assert ENVELOPES, f"no fixtures under {FIXTURES_DIR}; run scripts/make_fixtures.py"
    folders = {p.parent.name for p in ENVELOPES}
    assert set(WORKFLOWS) <= folders, f"missing workflows: {set(WORKFLOWS) - folders}"


@pytest.mark.parametrize("path", ENVELOPES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_envelope_is_valid(path: Path) -> None:
    env = read_json(path)
    assert isinstance(env, dict)
    assert env.get("status") in STATUSES, env.get("status")
    if env["status"] in ("ok", "partial"):
        assert "results" in env
        assert isinstance(env["result_count"], int) and env["result_count"] >= 0
    if env["status"] == "error":
        err = env["error"]
        assert isinstance(err["code"], str) and err["code"]
        assert isinstance(err["retryable"], bool)
        assert isinstance(err["recovery"], str) and err["recovery"]


@pytest.mark.parametrize("path", ENVELOPES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_request_sits_next_to_real_envelopes(path: Path) -> None:
    if path.stem.startswith("derived_"):
        return
    request = path.with_name(f"{path.stem}.request.json")
    assert request.is_file(), f"missing {request.name}"
    body = read_json(request)
    assert body["workflow"] == path.parent.name
    assert isinstance(body["input"], dict)


def test_non_ok_statuses_are_covered() -> None:
    seen = {read_json(p)["status"] for p in ENVELOPES}
    assert seen >= {"ok", "needs_input", "error", "empty", "partial", "degraded"}, seen
    retryable = [p for p in ENVELOPES if read_json(p).get("error", {}).get("retryable") is True]
    assert retryable, "no retryable error fixture"
