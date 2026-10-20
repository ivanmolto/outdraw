"""normalise(): all six statuses pass through, a missing status becomes an error, error
fields survive, and the shape quirks from decision 001 are tolerated."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from draw.qloo.envelope import (
    STATUSES,
    QlooEmpty,
    QlooError,
    QlooNeedsInput,
    candidates_of,
    is_empty,
    normalise,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "qloo"


def fixture(workflow: str, name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / workflow / f"{name}.json").read_text("utf-8"))
    return data


STATUS_FIXTURES = {
    "ok": ("find_tags", "jazz"),
    "empty": ("recommend", "derived_empty"),
    "needs_input": ("describe", "blue_note_jazz_club"),
    "partial": ("recommend", "derived_partial"),
    "degraded": ("recommend", "derived_degraded"),
    "error": ("find_tags", "empty_query"),
}


def test_every_status_has_a_fixture() -> None:
    assert set(STATUS_FIXTURES) == STATUSES


@pytest.mark.parametrize("status", sorted(STATUSES))
def test_all_six_statuses_normalise(status: str) -> None:
    env = normalise(fixture(*STATUS_FIXTURES[status]))
    assert env.status == status
    assert isinstance(env.results, list | dict)
    assert env.result_count >= 0
    if status == "error":
        assert env.error is not None
    else:
        assert env.error is None


def test_missing_status_is_error_missing_status() -> None:
    env = normalise({"operation": "find_tags", "correlation_id": "exec-x"})
    assert env.status == "error"
    assert env.error is not None
    assert env.error.code == "missing_status"
    assert env.error.retryable is False
    assert env.results == [] and env.result_count == 0
    assert env.summary


def test_missing_status_keeps_the_harness_error_code() -> None:
    # exit 4: `{error, operation, correlation_id}` with no status
    raw = {
        "error": {"code": "QLOO_AUTH", "retryable": False, "recovery": "Run qloo setup --qloo."},
        "operation": "describe",
    }
    env = normalise(raw)
    assert env.status == "error"
    assert env.error is not None
    assert env.error.code == "QLOO_AUTH"
    assert env.error.recovery == "Run qloo setup --qloo."


def test_unknown_status_is_error() -> None:
    env = normalise({"status": "weird", "results": []})
    assert env.status == "error"
    assert env.error is not None and env.error.code == "invalid_status"


def test_error_fields_preserved() -> None:
    env = normalise(fixture("find_tags", "derived_error_retryable"))
    assert env.status == "error"
    assert env.error is not None
    assert env.error.code == "QLOO_RATE_LIMIT"
    assert env.error.retryable is True
    assert env.error.recovery.startswith("Wait")
    assert env.error.model_extra and env.error.model_extra["layer"] == "qloo"


def test_error_block_completed_when_fields_missing() -> None:
    env = normalise({"status": "error", "error": {"code": "X"}})
    assert env.error is not None
    assert (env.error.code, env.error.retryable, env.error.recovery) == ("X", False, "")
    env = normalise({"status": "error"})
    assert env.error is not None and env.error.code == "unknown_error"


def test_summary_optional_and_extras_kept() -> None:
    env = normalise(fixture("find_tags", "jazz"))
    assert env.summary is None
    assert env.model_extra is not None
    assert "provenance" in env.model_extra and "execution" in env.model_extra


def test_results_may_be_an_object() -> None:
    env = normalise(fixture("compare_audiences", "two_artists"))
    assert env.status == "ok"
    assert isinstance(env.results, dict)
    assert set(env.results) >= {"tags", "a", "b"}
    assert env.result_count == 0
    assert not is_empty(env), "an object result is data, not an empty answer"


def test_result_count_defaults_to_list_length() -> None:
    env = normalise({"status": "ok", "results": [{"id": 1}, {"id": 2}]})
    assert env.result_count == 2
    env = normalise({"status": "ok", "results": "nope", "result_count": -4})
    assert env.results == [] and env.result_count == 0


def test_ok_with_zero_results_is_empty_but_trends_is_not() -> None:
    assert is_empty(normalise(fixture("find_tags", "nonsense_query_exact")))
    assert is_empty(normalise(fixture("recommend", "derived_empty")))
    trends = normalise(fixture("trends", "three_artists_last_12_months"))
    assert trends.results == [] and trends.result_count > 0
    assert not is_empty(trends)


def test_candidates_of_needs_input() -> None:
    env = normalise(fixture("describe", "blue_note_jazz_club"))
    cands = candidates_of(env)
    assert len(cands) == 2
    assert all({"id", "name", "type", "for_input"} <= set(c) for c in cands)
    assert candidates_of(normalise({"status": "needs_input", "results": []})) == []


def test_exceptions_carry_their_context() -> None:
    env = normalise(fixture("describe", "blue_note_jazz_club"))
    exc = QlooNeedsInput(candidates_of(env), envelope=env)
    assert len(exc.candidates) == 2 and exc.issues[0]["kind"] == "ambiguous"
    err = QlooError("QLOO_RATE_LIMIT", retryable=True, recovery="wait")
    assert err.retryable and "wait" in str(err)
    assert "no results" in str(QlooEmpty(normalise({"status": "empty"})))
