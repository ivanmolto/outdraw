#!/usr/bin/env python3
"""Build one real crowd profile and record every envelope for the T08 fixtures.

Runs resolve → confirm → profile for a demo New York room with three past acts, through
the gateway (`QlooExec`), and saves each call as
``agent/recordings/audit/<workflow>/crowd_<check>.json`` with the request alongside as
``crowd_<check>.request.json``, the layout ``scripts/make_fixtures.py`` turns into synthetic
fixtures. ``agent/recordings/`` is gitignored: Qloo's terms forbid its response data in the
public repo.

Usage (from ``/agent``)::

    uv run python scripts/record_crowd.py          # prints the call estimate, asks first
    uv run python scripts/record_crowd.py --yes    # no confirmation prompt
    uv run python scripts/make_fixtures.py         # then derive the synthetic fixtures

The API key is never printed; it is read only to redact it from the recordings.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# Scripts run from /agent without the package installed; keep imports below this line.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from draw.profile.crowd import DEFAULT_MAX_CALLS, build_crowd_profile  # noqa: E402
from draw.profile.resolve import resolve_entity  # noqa: E402
from draw.qloo.cache import MemoryCache  # noqa: E402
from draw.qloo.config import QlooConfig  # noqa: E402
from draw.qloo.exec_client import QlooExec  # noqa: E402
from draw.qloo.recording import Recorder, redact  # noqa: E402
from draw.schemas import PastShow, ResolvedEntity, VenueInput  # noqa: E402

AGENT_DIR = Path(__file__).resolve().parents[1]
RECORDINGS_DIR = AGENT_DIR / "recordings" / "audit"
PLACE_TAG_WORDS = ("bar", "restaurant")
PLACE_TAG_TYPE = "urn:tag:category:place"

# A synthetic 150-cap room in the key's region of record (decision 001: New York, not
# Malta). The acts are inputs we type, chosen to resolve unambiguously; the sell-throughs
# give three distinct weights (1.0, ~0.67, 0.3).
VENUE = VenueInput(
    name="Draw Room",
    city="New York",
    country="United States",
    capacity=150,
    past_shows=[
        PastShow(act="Norah Jones", date=dt.date(2026, 6, 12), tickets_sold=150, capacity=150),
        PastShow(act="Radiohead", date=dt.date(2026, 7, 3), tickets_sold=100, capacity=150),
        PastShow(act="Gregory Porter", date=dt.date(2026, 8, 21), tickets_sold=45, capacity=150),
    ],
)
ESTIMATED_CALLS = len(VENUE.past_shows) + len(PLACE_TAG_WORDS) + DEFAULT_MAX_CALLS


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.casefold()).strip("_")


class AuditRecorder(Recorder):
    """Writes the audit layout `make_fixtures.py` reads, named by what each call was for."""

    def __init__(self, secret: str | None) -> None:
        super().__init__(RECORDINGS_DIR, secret=secret)
        self.count = 0
        self.checks: list[str] = []

    def check_name(self, workflow: str, request: dict[str, Any]) -> str:
        if workflow == "describe":
            return f"crowd_act_{slug(str(request.get('entity', '')))}"
        if workflow == "find_tags":
            return f"crowd_tag_{slug(str(request.get('query', '')))}"
        if workflow == "recommend":
            return f"crowd_{request.get('target_type', 'x')}s"
        if workflow == "compare_audiences":
            return "crowd_genres"
        if workflow == "audience_demographics":
            n = sum(c.startswith("crowd_demographics") for c in self.checks) + 1
            return f"crowd_demographics_{n}"
        return f"crowd_{workflow}"

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
        self.count += 1
        check = self.check_name(workflow, request)
        self.checks.append(check)
        folder = RECORDINGS_DIR / workflow
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{check}.request.json").write_text(
            redact(json.dumps({"workflow": workflow, "input": request}, indent=2), self._secret)
            + "\n",
            encoding="utf-8",
        )
        path = folder / f"{check}.json"
        # The gateway dumps optional fields as null; the harness simply omits them.
        printed = {k: v for k, v in envelope.items() if v is not None}
        path.write_text(
            redact(json.dumps(printed, indent=2), self._secret) + "\n", encoding="utf-8"
        )
        print(f"  {workflow:22} {status:12} {'hit ' if cache_hit else 'miss'}  -> {path.name}")
        return path


def read_secret() -> str | None:
    value = os.environ.get("QLOO_API_KEY")
    if value:
        return value
    config = Path(os.environ.get("QLOO_HOME") or Path.home() / ".qloo") / "config"
    if config.is_file():
        for line in config.read_text(encoding="utf-8").splitlines():
            if line.startswith("api_key="):
                return line.split("=", 1)[1].strip() or None
    return None


def preflight(qloo_bin: str) -> None:
    status = subprocess.run(
        [qloo_bin, "setup", "--status"], env=os.environ, capture_output=True, text=True, check=False
    )
    if "Qloo data: ready" not in status.stdout:
        sys.exit("Qloo harness is not ready (run `qloo setup --qloo`); aborting before any call.")


async def place_tags(qloo: QlooExec) -> list[str]:
    """Resolve the bar/restaurant words to tag URNs (the harness refuses words)."""
    urns: list[str] = []
    for word in PLACE_TAG_WORDS:
        result = await qloo.find_tags(word, limit=5)
        rows = result.results if isinstance(result.results, list) else []
        typed = [r for r in rows if str(r.get("type", "")).startswith(PLACE_TAG_TYPE)]
        chosen = (typed or rows)[0] if rows else None
        if chosen and isinstance(chosen.get("id"), str):
            urns.append(chosen["id"])
    return urns


async def run() -> int:
    config = QlooConfig.from_env()
    if config.qloo_bin is None:
        sys.exit("qloo harness not found; run `npm ci` inside /agent")
    preflight(config.qloo_bin)
    recorder = AuditRecorder(read_secret())
    qloo = QlooExec(config, cache=MemoryCache(), recorder=recorder)

    confirmed: list[ResolvedEntity] = []
    for show in VENUE.past_shows:
        resolved = await resolve_entity(show.act, "artist", qloo=qloo)
        if not resolved.confirmed:
            names = [c.name for c in resolved.candidates[:3]]
            sys.exit(f"{show.act!r} did not auto-confirm (candidates {names}); pick another act")
        confirmed.append(resolved)

    tags = await place_tags(qloo)
    started = time.monotonic()
    profile = await build_crowd_profile(VENUE, confirmed, qloo=qloo, place_tags=tags)
    elapsed = time.monotonic() - started

    print(f"\nprofile built in {elapsed:.1f}s, data_quality={profile.data_quality}")
    for name in ("genres", "related_artists", "places", "films", "brands"):
        print(f"  {name:16} {len(getattr(profile, name)):2} items")
    print(f"  demographics     {'yes' if profile.demographics else 'no'}")
    print(f"  evidence         {len(profile.evidence)} refs")
    print(f"  signals          {[(round(s.weight, 2)) for s in profile.signals]}")
    for note in profile.notes or []:
        print(f"  note: {note}")
    print(f"\n{recorder.count} harness calls recorded under {RECORDINGS_DIR} (gitignored)")
    print("next: uv run python scripts/make_fixtures.py")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = parser.parse_args()
    print(f"Estimated Qloo calls: {ESTIMATED_CALLS} (3 describe, 2 find_tags, 6 profile)")
    if not args.yes:
        answer = input("Spend them now? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print("aborted before any call")
            return 1
    return asyncio.run(run())


if __name__ == "__main__":
    sys.exit(main())
