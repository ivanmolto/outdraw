"""Shared test helpers: fixture paths, the fixture generator's rules, and a fake harness."""

from __future__ import annotations

import asyncio
import importlib.util
import json
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from draw.qloo import exec_client

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


def fixture_text(workflow: str, name: str) -> str:
    return (FIXTURES_DIR / workflow / f"{name}.json").read_text("utf-8")


# --- fake harness: `create_subprocess_exec` printing fixture envelopes ----------------


class FakeProc:
    def __init__(self, stdout: str, stderr: str, returncode: int, delay: float) -> None:
        self._stdout, self._stderr, self._delay = stdout, stderr, delay
        self.returncode: int | None = None
        self._returncode = returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        if self._delay:
            await asyncio.sleep(self._delay)
        self.returncode = self._returncode
        return self._stdout.encode(), self._stderr.encode()

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode or 0


class FakeHarness:
    """Scripted stand-in for `asyncio.create_subprocess_exec`.

    Each response is `(stdout, stderr, exit_code)`; the gateway's real status branching,
    exceptions, cache and retries run on top of it.
    """

    def __init__(self, responses: list[tuple[str, str, int]], delay: float = 0.0) -> None:
        self.responses = list(responses)
        self.delay = delay
        self.spawns = 0
        self.in_flight = 0
        self.max_in_flight = 0
        self.argv: list[list[str]] = []
        self.env_seen: list[Any] = []
        self.inputs: list[dict[str, Any]] = []

    async def __call__(self, *argv: str, **kwargs: Any) -> FakeProc:
        self.spawns += 1
        self.argv.append(list(argv))
        self.env_seen.append(kwargs.get("env"))
        self.inputs.append(json.loads(Path(argv[argv.index("--input-file") + 1]).read_text()))
        stdout, stderr, rc = self.responses.pop(0) if self.responses else self.responses_default()
        proc = FakeProc(stdout, stderr, rc, self.delay)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        original = proc.communicate

        async def tracked() -> tuple[bytes, bytes]:
            try:
                return await original()
            finally:
                self.in_flight -= 1

        proc.communicate = tracked  # type: ignore[method-assign]
        return proc

    @staticmethod
    def responses_default() -> tuple[str, str, int]:
        return fixture_text("find_tags", "jazz"), "", 0

    def workflows(self) -> list[str]:
        """The workflow of each spawn, in order."""
        return [argv[2] for argv in self.argv]


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> Callable[..., FakeHarness]:
    def make(*responses: tuple[str, str, int], delay: float = 0.0) -> FakeHarness:
        fake = FakeHarness(list(responses), delay=delay)
        monkeypatch.setattr(exec_client, "create_subprocess_exec", fake)
        return fake

    return make
