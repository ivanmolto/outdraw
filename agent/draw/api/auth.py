"""`X-Draw-Internal-Key`: only the web app's server routes may call the agent."""

from __future__ import annotations

import hmac
import os
from typing import Annotated

from fastapi import Header, HTTPException, status

HEADER = "X-Draw-Internal-Key"


def require_internal_key(
    key: Annotated[str | None, Header(alias=HEADER)] = None,
) -> None:
    """401 unless the header equals `INTERNAL_API_KEY`; fails closed when that is unset."""
    expected = os.environ.get("INTERNAL_API_KEY", "")
    if not expected or key is None or not hmac.compare_digest(key.encode(), expected.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Missing or wrong {HEADER} header.")
