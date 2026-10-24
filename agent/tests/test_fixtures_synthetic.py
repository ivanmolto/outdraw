"""No real Qloo entity ID, tag ID or name from a private recording leaks into a fixture.

Runs only where /agent/recordings/audit exists (it is gitignored); skipped in CI.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest
from conftest import FIXTURES_DIR, RECORDINGS_DIR, load_script

pytestmark = pytest.mark.skipif(
    not RECORDINGS_DIR.is_dir() or bool(os.environ.get("CI")),
    reason="needs the private recordings (gitignored), never available in CI",
)

make_fixtures = load_script("make_fixtures")


def _recordings() -> list[Path]:
    return sorted(p for p in RECORDINGS_DIR.rglob("*.json") if not p.name.startswith("_"))


def _fixtures() -> list[Path]:
    return sorted(FIXTURES_DIR.rglob("*.json"))


def _real_things() -> tuple[set[str], set[str], set[str]]:
    uuids: set[str] = set()
    tags: set[str] = set()
    names: set[str] = set()
    for path in _recordings():
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
        uuids |= make_fixtures.real_uuids(text)
        tags |= make_fixtures.real_tag_ids(data)
        names |= {n.lower() for n in make_fixtures.real_names(data)}
    return uuids, tags, names


def _free_strings(value: Any, key: str = "") -> list[str]:
    """String values that are not structural (statuses, type URNs, endpoints)."""
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in _free_strings(v, k)]
    if isinstance(value, list):
        return [s for v in value for s in _free_strings(v, key)]
    if isinstance(value, str) and key not in make_fixtures.KEEP_KEYS:
        return [value]
    return []


def test_recordings_and_fixtures_present() -> None:
    assert _recordings(), "no recordings"
    assert _fixtures(), "no fixtures; run scripts/make_fixtures.py"


def test_no_real_uuid_or_tag_id_in_fixtures() -> None:
    uuids, tags, _ = _real_things()
    assert uuids and tags
    for path in _fixtures():
        text = path.read_text(encoding="utf-8")
        found_uuids = {m.upper() for m in make_fixtures.UUID_RE.findall(text)} & uuids
        assert not found_uuids, f"{path}: real entity ids {sorted(found_uuids)[:3]}"
        leaked_tags = [t for t in tags if t in text]
        assert not leaked_tags, f"{path}: real tag ids {leaked_tags[:3]}"


def test_no_real_name_in_fixtures() -> None:
    _, _, names = _real_things()
    assert names
    ordered = sorted(names, key=len, reverse=True)
    pattern = re.compile(
        r"(?<!\w)(" + "|".join(re.escape(n) for n in ordered) + r")(?!\w)",
        re.IGNORECASE,
    )
    for path in _fixtures():
        for text in _free_strings(json.loads(path.read_text(encoding="utf-8"))):
            match = pattern.search(text)
            assert not match, f"{path}: real name {match.group(0)!r} in {text[:60]!r}"


def test_fixtures_are_marked_synthetic() -> None:
    for path in _fixtures():
        if path.name.endswith(".request.json"):
            continue
        assert "_synthetic" in json.loads(path.read_text(encoding="utf-8")), path
