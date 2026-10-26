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
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from draw import __version__
from draw.api import limits, runs, seeds, venues
from draw.api.deps import Deps
from draw.api.etag import CACHE_CONTROL, NotModified
from draw.api.inputs import InvalidInput
from draw.api.live import LIVE_MODE_OFF, LiveModeOff, live_mode
from draw.qloo.envelope import BudgetExceeded, QlooError, QlooGatewayError
from draw.qloo.harness import RuntimeVersions, probe_versions

DepsFactory = Callable[[], Awaitable[Deps | None]]

log = logging.getLogger("draw.main")


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
        try:
            yield
        finally:
            if deps is not None:
                await deps.aclose()

    app = FastAPI(title="Outdraw agent", version=__version__, lifespan=lifespan)
    app.include_router(venues.router)
    app.include_router(runs.router)
    app.include_router(limits.router)
    app.include_router(seeds.router)
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

    return app


app = create_app()
