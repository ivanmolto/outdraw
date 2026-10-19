"""Shared test helpers: fixture paths and access to the fixture generator's rules."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

AGENT_DIR = Path(__file__).resolve().parents[1]
FIXTURES_DIR = AGENT_DIR / "tests" / "fixtures" / "qloo"
RECORDINGS_DIR = AGENT_DIR / "recordings" / "audit"
STATUSES = {"ok", "empty", "needs_input", "partial", "degraded", "error"}
WORKFLOWS = (
    "find_tags",
    "describe",
    "recommend",
    "rank",
    "where_popular",
    "compare_audiences",
    "audience_demographics",
    "trends",
)


def load_script(name: str) -> ModuleType:
    """Import a script from /agent/scripts without making scripts a package."""
    path = AGENT_DIR / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, path
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def envelope_files() -> list[Path]:
    return sorted(p for p in FIXTURES_DIR.rglob("*.json") if not p.name.endswith(".request.json"))


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))
