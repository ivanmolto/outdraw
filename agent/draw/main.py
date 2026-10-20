"""FastAPI entrypoint: `uvicorn draw.main:app`."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, status
from pydantic import BaseModel

from draw import __version__
from draw.qloo.harness import RuntimeVersions, probe_versions


class Health(BaseModel):
    ok: bool
    version: str
    qloo_harness_version: str | None
    node_version: str | None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Probe once at startup; /health only reads the cached result.
    app.state.runtime = probe_versions()
    yield


app = FastAPI(title="Outdraw agent", version=__version__, lifespan=lifespan)


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
