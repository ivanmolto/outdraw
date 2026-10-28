"""FastAPI entrypoint: `uvicorn draw.main:app`.

`create_app(deps_factory)` builds the service; tests pass a factory over their own database
and fakes. Every route except /health needs `X-Draw-Internal-Key` (see `draw.api.auth`).
Gateway exceptions map to HTTP: `BudgetExceeded` → 429 `{reason: "budget"}`, `QlooError` →
502 with its `code`, `retryable` and `recovery`, any other gateway error → 502;
`NotModified` (a seed read whose `If-None-Match` matched) → 304 with the `ETag`;
`LiveModeOff` (`LIVE_MODE=off`, `draw.api.live`) → 503 `{reason: "live_mode_off",
fallback_seed}`. A run route's 429 carries `fallback_seed` too. A request that fails
validation (`draw.api.inputs`) → 400 `{reason: "invalid_input", field}`, before any route
code runs, so it never reaches Qloo or Claude.

Every request runs under a run id (`draw.observability`): the web's `X-Draw-Run-Id`, echoed
on the response and stamped on every `draw.*` log line and Sentry event (`SENTRY_DSN`).

At startup, with a database and embeddings on, the cached Qloo tag names are embedded in
the background for the refine parser's tag hints (T37, `draw.embed.tag_map.index_tags`).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import Depends, FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from draw import __version__
from draw.api import limits, runs, seeds, sessions, venues
from draw.api.auth import require_internal_key
from draw.api.deps import Deps
from draw.api.etag import CACHE_CONTROL, NotModified
from draw.api.inputs import InvalidInput
from draw.api.live import LIVE_MODE_OFF, LiveModeOff, live_mode
from draw.embed.tag_map import index_tags
from draw.observability import (
    RunIdMiddleware,
    configure_logging,
    current_run_id,
    init_sentry,
)
from draw.qloo.envelope import BudgetExceeded, QlooError, QlooGatewayError
from draw.qloo.harness import RuntimeVersions, probe_versions

DepsFactory = Callable[[], Awaitable[Deps | None]]

log = logging.getLogger("draw.main")


class SentryTest(BaseModel):
    run_id: str | None
    event_id: str | None
    """None when `SENTRY_DSN` is unset: nothing was sent."""


class Health(BaseModel):
    ok: bool
    version: str
    qloo_harness_version: str | None
    node_version: str | None


async def _budget_exceeded(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, BudgetExceeded)
    return JSONResponse(
        {
            "reason": "budget",
            "detail": str(exc),
            "what": exc.what,
            "used": exc.used,
            "cap": exc.cap,
            **({"fallback_seed": exc.fallback_seed} if exc.fallback_seed else {}),
        },
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
    )


def _field(loc: tuple[int | str, ...]) -> str | None:
    """Dotted path of an error location without its `body`/`path`/`query` part."""
    path = loc[1:] if loc and loc[0] in ("body", "path", "query", "header") else loc
    return ".".join(str(p) for p in path) or None


async def _invalid_input(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, RequestValidationError)
    errors = exc.errors()
    field = _field(tuple(errors[0]["loc"])) if errors else None
    body = InvalidInput(reason="invalid_input", field=field)
    return JSONResponse(body.model_dump(exclude_none=True), status_code=status.HTTP_400_BAD_REQUEST)


async def _live_mode_off(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, LiveModeOff)
    return JSONResponse(
        {"reason": LIVE_MODE_OFF, "detail": str(exc), "fallback_seed": exc.fallback_seed},
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    )


async def _qloo_error(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, QlooError)
    return JSONResponse(
        {
            "detail": "Qloo call failed.",
            "error": {"code": exc.code, "retryable": exc.retryable, "recovery": exc.recovery},
        },
        status_code=status.HTTP_502_BAD_GATEWAY,
    )


async def _gateway_error(request: Request, exc: Exception) -> Response:
    return JSONResponse({"detail": str(exc)}, status_code=status.HTTP_502_BAD_GATEWAY)


async def _not_modified(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, NotModified)
    return Response(
        status_code=status.HTTP_304_NOT_MODIFIED,
        headers={"ETag": exc.etag, "Cache-Control": CACHE_CONTROL},
    )


async def _index_tags(deps: Deps) -> None:
    """Startup's tag index; a failure only leaves refine without tag hints."""
    try:
        if deps.embedder is not None:
            await index_tags(deps.sessions, deps.embedder)
    except Exception:
        log.warning("tag index failed; refine parses without tag hints", exc_info=True)


def create_app(deps_factory: DepsFactory = Deps.from_env) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # An invalid LIVE_MODE fails the start, so the previous deploy keeps serving.
        if not live_mode():
            log.warning("LIVE_MODE=off: runs and refinements answer 503 with a fallback seed")
        # Probe once at startup; /health only reads the cached result.
        app.state.runtime = probe_versions()
        deps = await deps_factory()
        app.state.deps = deps
        app.state.tag_index = asyncio.create_task(_index_tags(deps)) if deps else None
        try:
            yield
        finally:
            task: asyncio.Task[None] | None = app.state.tag_index
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            if deps is not None:
                await deps.aclose()

    app = FastAPI(title="Outdraw agent", version=__version__, lifespan=lifespan)
    app.add_middleware(RunIdMiddleware)
    app.include_router(venues.router)
    app.include_router(runs.router)
    app.include_router(limits.router)
    app.include_router(seeds.router)
    app.include_router(sessions.router)
    app.add_exception_handler(BudgetExceeded, _budget_exceeded)
    app.add_exception_handler(QlooError, _qloo_error)
    app.add_exception_handler(QlooGatewayError, _gateway_error)
    app.add_exception_handler(NotModified, _not_modified)
    app.add_exception_handler(LiveModeOff, _live_mode_off)
    app.add_exception_handler(RequestValidationError, _invalid_input)

    @app.get("/health")
    def health(request: Request, response: Response) -> Health:
        runtime: RuntimeVersions = request.app.state.runtime
        ok = runtime.qloo_harness is not None and runtime.node is not None
        if not ok:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return Health(
            ok=ok,
            version=__version__,
            qloo_harness_version=runtime.qloo_harness,
            node_version=runtime.node,
        )

    @app.post(
        "/ops/sentry-test",
        dependencies=[Depends(require_internal_key)],
        include_in_schema=False,
    )
    async def sentry_test() -> SentryTest:
        """One Sentry test event under the request's run id (docs/ops.md, T38)."""
        log.info("sentry_test")
        return SentryTest(
            run_id=current_run_id(),
            event_id=sentry_sdk.capture_message("Outdraw agent: Sentry test event", "info"),
        )

    return app


configure_logging()
init_sentry()
app = create_app()
