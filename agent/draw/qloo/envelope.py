"""The six-status Qloo envelope, normalisation of raw harness output, and the gateway's errors.

`normalise` is the only place raw harness JSON becomes a `QlooEnvelope`. It tolerates what
the harness actually prints (see docs/decisions/001-qloo-workflows.md): exit 1 and exit 4
print `{error, operation, correlation_id}` with no `status`, `summary` is absent on `ok`,
and `compare_audiences` returns `results` as an object rather than a list.
"""

from __future__ import annotations

from typing import Any

from draw.schemas import QlooEnvelope

__all__ = [
    "STATUSES",
    "BudgetExceeded",
    "QlooEmpty",
    "QlooEnvelope",
    "QlooError",
    "QlooGatewayError",
    "QlooNeedsInput",
    "QlooResult",
    "candidates_of",
    "is_empty",
    "normalise",
]

STATUSES: frozenset[str] = frozenset({"ok", "empty", "needs_input", "partial", "degraded", "error"})
CACHEABLE_STATUSES: frozenset[str] = frozenset({"ok", "partial"})


class QlooResult(QlooEnvelope):
    """A normalised envelope plus the gateway's evidence-tracing fields.

    `call_id` is a UUID minted per gateway call (cache hits included) so a score can point
    back at the exact call that produced an affinity.
    """

    call_id: str
    workflow: str
    cache_hit: bool = False
    latency_ms: int = 0


# --- exceptions -----------------------------------------------------------------------


class QlooGatewayError(Exception):
    """Base class for everything the gateway raises."""


class QlooError(QlooGatewayError):
    """An `error` envelope (after the retry policy gave up) or a harness failure."""

    def __init__(
        self,
        code: str,
        *,
        retryable: bool = False,
        recovery: str = "",
        envelope: QlooEnvelope | None = None,
    ) -> None:
        super().__init__(f"{code}: {recovery}" if recovery else code)
        self.code = code
        self.retryable = retryable
        self.recovery = recovery
        self.envelope = envelope


class QlooNeedsInput(QlooGatewayError):  # noqa: N818 - names fixed by the task spec
    """A `needs_input` envelope: surface `candidates` and ask the user to choose."""

    def __init__(self, candidates: list[dict[str, Any]], *, envelope: QlooEnvelope) -> None:
        super().__init__(envelope.summary or "Qloo needs a choice to continue.")
        self.candidates = candidates
        self.issues: list[dict[str, Any]] = _issues_of(envelope)
        self.envelope = envelope


class QlooEmpty(QlooGatewayError):  # noqa: N818
    """An `empty` envelope, or `ok` with no results: refine the input."""

    def __init__(self, envelope: QlooEnvelope) -> None:
        super().__init__(envelope.summary or "Qloo returned no results.")
        self.envelope = envelope


class BudgetExceeded(QlooGatewayError):  # noqa: N818
    """A daily cap is spent: Qloo calls (nothing was spawned) or LLM tokens (T13)."""

    def __init__(self, used: int, cap: int, what: str = "Qloo") -> None:
        super().__init__(f"{what} daily budget exceeded: {used}/{cap}")
        self.used = used
        self.cap = cap
        self.what = what


# --- normalisation --------------------------------------------------------------------


def _error_field(raw: dict[str, Any], fallback_code: str) -> dict[str, Any]:
    """A complete `{code, retryable, recovery}` from whatever the harness printed."""
    error = raw.get("error")
    if not isinstance(error, dict):
        error = {}
    out: dict[str, Any] = dict(error)
    code = out.get("code")
    out["code"] = code if isinstance(code, str) and code else fallback_code
    out["retryable"] = bool(out.get("retryable", False))
    recovery = out.get("recovery")
    out["recovery"] = recovery if isinstance(recovery, str) else ""
    return out


def normalise(raw: dict[str, Any]) -> QlooEnvelope:
    """Turn raw harness output into a `QlooEnvelope`, never raising on shape problems.

    - No `status` (exit 1/4 output: `{error, operation, correlation_id}`) becomes `error`
      with code `missing_status`, unless the raw error carries its own code.
    - An unknown status string becomes `error` with code `invalid_status`.
    - `results` defaults to `[]`; `result_count` defaults to the length of a list result.
    - Every other field is kept (the model allows extras) so `resolution`, `provenance`,
      `execution` and `series` stay available to callers.
    """
    data: dict[str, Any] = dict(raw)
    status = data.get("status")

    if status is None:
        data["status"] = "error"
        data["error"] = _error_field(data, "missing_status")
        data.setdefault("summary", "The harness returned no status.")
    elif status not in STATUSES:
        data["status"] = "error"
        data["error"] = _error_field(data, "invalid_status")
        data.setdefault("summary", f"Unknown envelope status {status!r}.")
    elif status == "error":
        data["error"] = _error_field(data, "unknown_error")
    elif "error" in data and not isinstance(data["error"], dict):
        data.pop("error")

    results = data.get("results")
    if not isinstance(results, list | dict):
        results = []
    data["results"] = results

    count = data.get("result_count")
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        count = len(results) if isinstance(results, list) else 0
    data["result_count"] = count

    summary = data.get("summary")
    if summary is not None and not isinstance(summary, str):
        data["summary"] = str(summary)

    return QlooEnvelope.model_validate(data)


def is_empty(envelope: QlooEnvelope) -> bool:
    """`empty`, or `ok` with nothing in it (the harness never returns `empty` itself).

    `trends` reports `results: []` with `result_count` = number of entities and the data
    under `series`, so both the list and the count must be zero.
    """
    if envelope.status == "empty":
        return True
    return (
        envelope.status == "ok"
        and envelope.result_count == 0
        and isinstance(envelope.results, list)
        and not envelope.results
    )


def _issues_of(envelope: QlooEnvelope) -> list[dict[str, Any]]:
    resolution = getattr(envelope, "resolution", None)
    if not isinstance(resolution, dict):
        return []
    issues = resolution.get("issues")
    return [i for i in issues if isinstance(i, dict)] if isinstance(issues, list) else []


def candidates_of(envelope: QlooEnvelope) -> list[dict[str, Any]]:
    """Flatten `resolution.issues[].candidates[]`, tagging each with the input it resolves."""
    out: list[dict[str, Any]] = []
    for issue in _issues_of(envelope):
        for cand in issue.get("candidates") or []:
            if isinstance(cand, dict):
                out.append({**cand, "for_input": issue.get("input"), "field": issue.get("field")})
    return out
