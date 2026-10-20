"""Gateway settings, read from the environment once per `QlooExec`."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from draw.qloo.harness import AGENT_DIR, qloo_bin

RECORDINGS_DIR = AGENT_DIR / "recordings"
EXEC_TIMEOUT_S = 20.0
# Roughly a third of the 10,000/month key quota per day; T32 tunes this from the real usage.
DEFAULT_DAILY_CAP = 300
DEFAULT_CONCURRENCY = 3
CACHE_TTL_S = 7 * 24 * 3600


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


@dataclass(frozen=True)
class QlooConfig:
    qloo_bin: str | None = field(default_factory=qloo_bin)
    timeout_s: float = EXEC_TIMEOUT_S
    concurrency: int = DEFAULT_CONCURRENCY
    daily_cap: int = DEFAULT_DAILY_CAP
    record: bool = False
    recordings_dir: Path = RECORDINGS_DIR
    redis_url: str | None = None
    database_url: str | None = None
    # Read only so it can be redacted from recordings; never logged or printed.
    api_key: str | None = None

    @classmethod
    def from_env(cls) -> QlooConfig:
        return cls(
            concurrency=max(1, _env_int("QLOO_CONCURRENCY", DEFAULT_CONCURRENCY)),
            daily_cap=_env_int("QLOO_DAILY_CAP", DEFAULT_DAILY_CAP),
            record=os.environ.get("QLOO_RECORD", "").strip() == "1",
            redis_url=os.environ.get("REDIS_URL") or None,
            database_url=os.environ.get("DATABASE_URL") or None,
            api_key=os.environ.get("QLOO_API_KEY") or None,
        )
