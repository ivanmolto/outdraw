"""Runtime probes for the pinned Qloo harness and the Node it runs on.

These run once at startup; they never reach Qloo and spend no quota.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parents[2]
# Prefer the harness pinned in /agent/package.json over any global install.
LOCAL_QLOO_BIN = AGENT_DIR / "node_modules" / ".bin" / "qloo"
PROBE_TIMEOUT_S = 10.0


@dataclass(frozen=True)
class RuntimeVersions:
    qloo_harness: str | None
    node: str | None


def qloo_bin() -> str | None:
    if LOCAL_QLOO_BIN.exists():
        return str(LOCAL_QLOO_BIN)
    return shutil.which("qloo")


def _first_line(cmd: list[str]) -> str | None:
    """Run `cmd` and return the first stdout line, or None if it fails in any way."""
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=PROBE_TIMEOUT_S, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = proc.stdout.strip().splitlines()
    if proc.returncode != 0 or not lines:
        return None
    return lines[0].strip()


def probe_versions() -> RuntimeVersions:
    binary = qloo_bin()
    return RuntimeVersions(
        qloo_harness=_first_line([binary, "--version"]) if binary else None,
        node=_first_line(["node", "--version"]),
    )
