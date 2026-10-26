"""Observability (T38): one `run_id` per request in every log line, and Sentry without PII.

The web server sends `X-Draw-Run-Id` on every agent request: the new run's id for
`POST /runs` and `POST /runs/{id}/refine` (which adopt it as the run's id, so the web's
log line and the agent's name the same run), the run's id for any other run route, a
`req_…` id for venue routes. `RunIdMiddleware` binds it (or, without a valid header, the
`/runs/{id}` path segment or a fresh `req_…` id) to a context variable for the request and
echoes it on the response; `RunIdFilter` stamps it on every `draw.*` log record as
`run_id`, and every Sentry event carries it as a tag.

Sentry is on only with `SENTRY_DSN` (free tier: errors only, no tracing). Events leave
scrubbed by `scrub_event`: no request body, cookies, query string or headers beyond the run
id and content type (the internal key never leaves), no local variables, no user; quoted
text in messages and exception values (where a venue name or a booker's message lands:
`Unknown run 'x'`, pydantic's `input_value='…'`) is replaced, and so is any `name`,
`message`, `prompt` or similar key in `extra`, `contexts` and breadcrumb data.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import IO, Any, Final

import sentry_sdk
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

__all__ = [
    "RUN_ID_HEADER",
    "RunIdFilter",
    "RunIdMiddleware",
    "bind_run_id",
    "configure_logging",
    "current_run_id",
    "init_sentry",
    "is_new_run_id",
    "is_run_id",
    "redact_text",
    "scrub_event",
]

RUN_ID_HEADER: Final = "X-Draw-Run-Id"
RUN_ID_PATTERN: Final = re.compile(r"^(run|req)_[a-z0-9_-]{4,64}$")
"""A run id (seed runs' `run_seed_<slug>` included) or a venue request's `req_…` id."""
NEW_RUN_ID_PATTERN: Final = re.compile(r"^run_[0-9a-f]{8,32}$")
"""What a new run adopts from the header: `draw.agent.store.new_id`'s shape, never a seed's."""
_RUN_PATH: Final = re.compile(r"^/runs/(run_[a-z0-9_-]{4,64})(?:/|$)")

REDACTED: Final = "[redacted]"
SENSITIVE_KEYS: Final = frozenset(
    {
        "name",
        "venue",
        "venue_name",
        "message",
        "messages",
        "prompt",
        "body",
        "data",
        "act",
        "acts",
        "past_shows",
        "query",
        "x-draw-internal-key",
        "authorization",
        "cookie",
    }
)
_SAFE_HEADERS: Final = frozenset({RUN_ID_HEADER.lower(), "content-type"})
_QUOTED: Final = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"")

_run_id: ContextVar[str | None] = ContextVar("draw_run_id", default=None)


def current_run_id() -> str | None:
    return _run_id.get()


def is_run_id(value: str | None) -> bool:
    return value is not None and RUN_ID_PATTERN.match(value) is not None


def is_new_run_id(value: str | None) -> bool:
    return value is not None and NEW_RUN_ID_PATTERN.match(value) is not None


@contextmanager
def bind_run_id(run_id: str) -> Iterator[None]:
    """`run_id` for every log line and Sentry event inside the block. Sentry's tag goes on
    the request's isolation scope, which outlives the block: an unhandled error is captured
    outside every middleware, after the context variable is reset."""
    sentry_sdk.get_isolation_scope().set_tag("run_id", run_id)
    token = _run_id.set(run_id)
    try:
        yield
    finally:
        _run_id.reset(token)


# --- logs -------------------------------------------------------------------------------


class RunIdFilter(logging.Filter):
    """Stamps `record.run_id` from the context (`-` outside a request)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "run_id", None):
            record.run_id = current_run_id() or "-"
        return True


LOG_FORMAT: Final = "%(asctime)s %(levelname)s %(name)s run_id=%(run_id)s %(message)s"


def configure_logging(level: str | None = None, stream: IO[str] | None = None) -> None:
    """One handler on the `draw` logger (stderr by default) with the run id in every line;
    a second call replaces it. uvicorn configures only its own loggers, so nothing else
    prints `draw.*` records (and pytest's caplog still sees them through the root)."""
    logger = logging.getLogger("draw")
    logger.setLevel(level or os.environ.get("LOG_LEVEL", "INFO"))
    for old in [h for h in logger.handlers if any(isinstance(f, RunIdFilter) for f in h.filters)]:
        logger.removeHandler(old)
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.addFilter(RunIdFilter())
    logger.addHandler(handler)


# --- the middleware ---------------------------------------------------------------------


def _request_run_id(scope: Scope) -> str:
    for key, value in scope.get("headers", []):
        if key.decode("latin-1").lower() == RUN_ID_HEADER.lower():
            header: str = value.decode("latin-1")
            if is_run_id(header):
                return header
    path = _RUN_PATH.match(str(scope.get("path", "")))
    if path:
        return path.group(1)
    return f"req_{uuid.uuid4().hex[:8]}"


class RunIdMiddleware:
    """Binds the request's run id (see the module docstring) and echoes it on the response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        run_id = _request_run_id(scope)

        async def send_with_header(message: Message) -> None:
            if message["type"] == "http.response.start":
                # A route that named its own run (a taken or missing id) already set it.
                MutableHeaders(scope=message).setdefault(RUN_ID_HEADER, run_id)
            await send(message)

        with bind_run_id(run_id):
            await self.app(scope, receive, send_with_header)


# --- Sentry -----------------------------------------------------------------------------


def redact_text(text: str) -> str:
    """Quoted substrings replaced: where reprs of user input (names, messages) end up."""
    return _QUOTED.sub(f"'{REDACTED}'", text)


def _scrub(value: Any, keys: frozenset[str] = SENSITIVE_KEYS) -> Any:
    if isinstance(value, Mapping):
        return {
            k: REDACTED if str(k).lower() in keys else _scrub(v, keys) for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [_scrub(v, keys) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def _scrub_request(request: Mapping[str, Any]) -> dict[str, Any]:
    url = str(request.get("url") or "").split("?", 1)[0]
    headers = request.get("headers") or {}
    return {
        "method": request.get("method"),
        "url": url,
        "headers": {k: v for k, v in headers.items() if str(k).lower() in _SAFE_HEADERS},
    }


def _scrub_breadcrumb(crumb: Mapping[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in crumb.items() if k in ("type", "category", "level", "timestamp")}
    if isinstance(crumb.get("message"), str):
        out["message"] = redact_text(crumb["message"])
    if isinstance(crumb.get("data"), Mapping):
        out["data"] = _scrub(crumb["data"])
    return out


def scrub_event(event: Any, hint: Any = None) -> Any:
    """Sentry's `before_send`: the event without PII (module docstring), tagged with run_id."""
    event.pop("user", None)
    if isinstance(event.get("request"), Mapping):
        event["request"] = _scrub_request(event["request"])
    if isinstance(event.get("extra"), Mapping):
        event["extra"] = _scrub(event["extra"])
    if isinstance(event.get("contexts"), Mapping):
        # Sentry's own contexts name the runtime and OS (`runtime.name`): keep `name` there.
        event["contexts"] = _scrub(event["contexts"], SENSITIVE_KEYS - {"name"})
    if isinstance(event.get("message"), str):
        event["message"] = redact_text(event["message"])
    logentry = event.get("logentry")
    if isinstance(logentry, dict):
        if isinstance(logentry.get("message"), str):
            logentry["message"] = redact_text(logentry["message"])
        if isinstance(logentry.get("formatted"), str):
            logentry["formatted"] = redact_text(logentry["formatted"])
        logentry.pop("params", None)
    crumbs = event.get("breadcrumbs")
    values = crumbs.get("values") if isinstance(crumbs, Mapping) else crumbs
    if isinstance(values, list):
        event["breadcrumbs"] = {"values": [_scrub_breadcrumb(c) for c in values]}
    exception = event.get("exception")
    for exc in exception.get("values") or [] if isinstance(exception, Mapping) else []:
        if isinstance(exc.get("value"), str):
            exc["value"] = redact_text(exc["value"])
        for frame in (exc.get("stacktrace") or {}).get("frames") or []:
            frame.pop("vars", None)
    run_id = current_run_id()
    if run_id is not None:
        event.setdefault("tags", {})["run_id"] = run_id
    return event


def init_sentry(transport: Any = None) -> bool:
    """Sentry for the agent when `SENTRY_DSN` is set; False (and nothing sent) otherwise.
    `transport` replaces the HTTP one (tests capture events in memory)."""
    dsn = os.environ.get("SENTRY_DSN")
    if not dsn:
        return False
    from draw import __version__

    sentry_sdk.init(
        dsn=dsn,
        environment=os.environ.get("SENTRY_ENVIRONMENT", "development"),
        release=f"outdraw-agent@{__version__}",
        # Collected as little as the SDK allows; scrub_event removes what is left.
        data_collection={
            "user_info": False,
            "cookies": {"mode": "off"},
            "http_headers": {"request": {"mode": "allowlist", "terms": sorted(_SAFE_HEADERS)}},
            "http_bodies": [],
            "url_query_params": {"mode": "off"},
            "gen_ai": {"inputs": False, "outputs": False},
            "database_query_data": False,
            "stack_frame_variables": False,
        },
        traces_sample_rate=0.0,
        before_send=scrub_event,
        transport=transport,
    )
    return True
