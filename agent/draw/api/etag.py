"""ETags for precomputed (seed) responses.

Seed runs never change after precompute, so their GET responses carry a strong `ETag`
(the SHA-256 of the response body) and a `Cache-Control` the web layer may honour; a
request whose `If-None-Match` names that tag gets `304 Not Modified` with no body.
`conditional` is a no-op for live runs, which keep their default headers.
"""

from __future__ import annotations

import hashlib
from typing import Final

from fastapi import Request, Response
from pydantic import BaseModel

__all__ = ["CACHE_CONTROL", "NotModified", "conditional", "etag_of"]

CACHE_CONTROL: Final = "public, max-age=3600"


class NotModified(Exception):  # noqa: N818 - an HTTP outcome, mapped by the app's handler
    """Raised from a route to answer 304; `draw.main` turns it into the response."""

    def __init__(self, etag: str) -> None:
        super().__init__(etag)
        self.etag = etag


def etag_of(payload: BaseModel) -> str:
    """A strong validator over the exact JSON the route would send."""
    digest = hashlib.sha256(payload.model_dump_json().encode("utf-8")).hexdigest()
    return f'"{digest[:32]}"'


def _matches(header: str, etag: str) -> bool:
    if header.strip() == "*":
        return True
    tags = {t.strip().removeprefix("W/") for t in header.split(",")}
    return etag in tags


def conditional(request: Request, response: Response, payload: BaseModel, *, seed: bool) -> None:
    """For a seed response: set `ETag`/`Cache-Control`, or raise `NotModified` on a match."""
    if not seed:
        return
    etag = etag_of(payload)
    if _matches(request.headers.get("if-none-match", ""), etag):
        raise NotModified(etag)
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = CACHE_CONTROL
