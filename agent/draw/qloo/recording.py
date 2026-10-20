"""Per-call recordings for the submission walkthrough (`QLOO_RECORD=1`).

Each call writes `{call_id, ts, workflow, request, envelope, status, cache_hit}` to
`/agent/recordings/<date>/<call_id>.json` and, when a Postgres pool is available, to the
`qloo_calls` table. The API key is redacted from the file text before it touches disk.
The folder is gitignored: Qloo response data never enters the public repo.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg

REDACTED = "<redacted>"

# Owned by the T14 migration once it exists; `ensure_schema` is idempotent.
QLOO_CALLS_DDL = """
CREATE TABLE IF NOT EXISTS qloo_calls (
    call_id    TEXT PRIMARY KEY,
    run_id     TEXT NULL,
    workflow   TEXT NOT NULL,
    request    JSONB NOT NULL,
    envelope   JSONB NOT NULL,
    status     TEXT NOT NULL,
    cache_hit  BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def redact(text: str, secret: str | None) -> str:
    if secret and secret in text:
        return text.replace(secret, REDACTED)
    return text


class Recorder:
    def __init__(
        self,
        root: Path,
        *,
        secret: str | None = None,
        pool: asyncpg.Pool | None = None,
    ) -> None:
        self.root = root
        self._secret = secret
        self._pool = pool

    async def ensure_schema(self) -> None:
        if self._pool is not None:
            await self._pool.execute(QLOO_CALLS_DDL)

    async def record(
        self,
        *,
        call_id: str,
        workflow: str,
        request: dict[str, Any],
        envelope: dict[str, Any],
        status: str,
        cache_hit: bool,
        run_id: str | None = None,
    ) -> Path:
        now = datetime.now(UTC)
        entry = {
            "call_id": call_id,
            "ts": now.isoformat(),
            "workflow": workflow,
            "request": request,
            "envelope": envelope,
            "status": status,
            "cache_hit": cache_hit,
        }
        text = redact(json.dumps(entry, indent=2, ensure_ascii=False), self._secret) + "\n"
        folder = self.root / now.strftime("%Y-%m-%d")
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{call_id}.json"
        path.write_text(text, encoding="utf-8")

        if self._pool is not None:
            redacted = json.loads(text)
            await self._pool.execute(
                "INSERT INTO qloo_calls"
                " (call_id, run_id, workflow, request, envelope, status, cache_hit, created_at)"
                " VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6, $7, $8)"
                " ON CONFLICT (call_id) DO NOTHING",
                call_id,
                run_id,
                workflow,
                json.dumps(redacted["request"]),
                json.dumps(redacted["envelope"]),
                status,
                cache_hit,
                now,
            )
        return path
