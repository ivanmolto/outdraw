"""scripts/walkthrough.py: the fixture run exports; nothing key-shaped or personal survives."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from conftest import AGENT_DIR, load_script

walkthrough = load_script("walkthrough")

FAKE_KEY = "qk_" + "a1b2c3d4" * 5
"""Key-shaped: a 40-character alphanumeric token after a prefix."""
KEY_PATTERNS = [
    re.compile(re.escape(FAKE_KEY)),
    re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9]{32,}(?![A-Za-z0-9])"),  # any key-shaped token
    re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"),
    re.compile(r"(?i)api[_-]?key|x-api-key|bearer"),
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),  # an email address
]
CHECKED_IN = AGENT_DIR.parent / "docs" / "walkthrough-nyc-jazz.md"


@pytest.fixture(scope="module")
def exported(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str]:
    import asyncio

    export = asyncio.run(walkthrough.fixture_run("nyc-jazz"))
    out = tmp_path_factory.mktemp("docs")
    path = walkthrough.write(export, docs_dir=out, secret=FAKE_KEY)
    return path, path.read_text(encoding="utf-8")


def test_export_runs_on_the_fixture_run(exported: tuple[Path, str]) -> None:
    path, text = exported
    assert path.name == "walkthrough-nyc-jazz.md"
    assert text.startswith("# Walkthrough: Blue Note Jazz Club, New York (nyc-jazz)")
    headings = re.findall(r"^### \d+\. `(\w+)`$", text, flags=re.MULTILINE)
    assert headings[:3] == ["describe"] * 3, "resolve → confirm → ask comes first"
    assert {"find_tags", "recommend", "compare_audiences", "where_popular", "trends"} <= set(
        headings
    )
    assert len(headings) == 44, "3 describe + 6 profile + find_tags + 1 recommend + 33 signals"
    for section in ("## Qloo calls", "## Scores", "## Top act, item by item"):
        assert section in text
    # request shape → envelope summary → how it fed the score, per call
    assert text.count("- Request shape: `") == 44
    assert text.count("- Envelope: status `") == 44
    assert text.count("- Score: ") == 44
    assert "Fed `crowd_affinity` (weight 0.35)" in text
    assert "Fed `past_fit` (weight 0.20)" in text and "Fed `local_heat` (weight 0.20)" in text
    rows = [line for line in text.splitlines() if re.match(r"^\| \d+ \| ", line)]
    assert len(rows) == 20 and all(line.endswith("| complete |") for line in rows)
    assert "INCOMPLETE" not in text


def test_no_key_or_personal_data_in_the_export(exported: tuple[Path, str]) -> None:
    _, text = exported
    for pattern in KEY_PATTERNS:
        assert not pattern.search(text), pattern.pattern
    assert "<id>" in text, "entity ids are shown as placeholders"
    assert "tickets_sold" not in text and "@" not in text


def test_redact_strips_keys_and_ids() -> None:
    secret = "s3cr3t-" + "x" * 30
    text = (
        f"key {secret}; token {'A' * 32}; id 746CD02C-8299-5FBF-9C15-615CBD354946; "
        "name Synthetic Entity 3 limit=20"
    )
    out = walkthrough.redact(text, secret)
    assert secret not in out and "A" * 32 not in out and "746CD02C" not in out
    assert out.endswith("name Synthetic Entity 3 limit=20")
    assert walkthrough.redact("short", "abc") == "short", "a too-short secret is ignored"


def test_cli_writes_the_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QLOO_API_KEY", FAKE_KEY)
    assert walkthrough.main(["--out", str(tmp_path)]) == 0
    assert (tmp_path / "walkthrough-nyc-jazz.md").is_file()
    with pytest.raises(SystemExit):
        walkthrough.main(["--fixture", "nope", "--out", str(tmp_path)])


def test_checked_in_walkthrough_is_current(exported: tuple[Path, str]) -> None:
    if not CHECKED_IN.is_file():
        pytest.skip("docs/walkthrough-nyc-jazz.md not generated; run scripts/walkthrough.py")
    _, text = exported
    assert CHECKED_IN.read_text(encoding="utf-8") == text, (
        "docs/walkthrough-nyc-jazz.md is stale: run `uv run python scripts/walkthrough.py`"
    )
