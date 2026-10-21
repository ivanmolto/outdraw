"""FastAPI entrypoint: `uvicorn draw.main:app`.

`create_app(deps_factory)` builds the service; tests pass a factory over their own database
and fakes. Every route except /health needs `X-Draw-Internal-Key` (see `draw.api.auth`).
Gateway exceptions map to HTTP: `BudgetExceeded` → 429, `QlooError` → 502 with its
`code`, `retryable` and `recovery`, any other gateway error → 502.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from draw import __version__
from draw.api import limits, runs, venues
from draw.api.deps import Deps
from draw.qloo.envelope import BudgetExceeded, QlooError, QlooGatewayError
from draw.qloo.harness import RuntimeVersions, probe_versions

DepsFactory = Callable[[], Awaitable[Deps | None]]


class Health(BaseModel):
    ok: bool
    version: str
    qloo_harness_version: str | None
    node_version: str | None


async def _budget_exceeded(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, BudgetExceeded)
    return JSONResponse(
        {"detail": str(exc), "what": exc.what, "used": exc.used, "cap": exc.cap},
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
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


def create_app(deps_factory: DepsFactory = Deps.from_env) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
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
    app.add_exception_handler(BudgetExceeded, _budget_exceeded)
    app.add_exception_handler(QlooError, _qloo_error)
    app.add_exception_handler(QlooGatewayError, _gateway_error)

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
