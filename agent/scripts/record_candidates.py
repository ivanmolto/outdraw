#!/usr/bin/env python3
"""Record the three `recommend` shapes the candidate stage (T09) relies on, on the real key.

Reuses the signal IDs and the jazz tag URN already recorded by ``record_crowd.py`` and
``qloo_audit.py`` (no new resolution calls) and makes exactly three artist `recommend`
calls through the gateway, saved under ``agent/recordings/audit/recommend/candidates_*``
in the layout ``make_fixtures.py`` turns into synthetic fixtures:

- ``candidates_baseline``: 3 signals + ``signal_location``, ``limit: 20`` (the harness
  maximum), to see the popularity spread behind ``POPULARITY_BANDS``;
- ``candidates_exclude_jazz``: the same with ``exclude_tags: [jazz music genre URN]``;
- ``candidates_include_jazz``: the same with ``include_tags: [jazz music genre URN]``.

It prints the popularity spread and which entities each tag filter removed or kept, which
is what decision 001 records. ``agent/recordings/`` is gitignored: Qloo's terms forbid its
response data in the public repo.

Usage (from ``/agent``)::

    uv run python scripts/record_candidates.py          # prints the call estimate, asks first
    uv run python scripts/record_candidates.py --yes    # no confirmation prompt
    uv run python scripts/make_fixtures.py              # then derive the synthetic fixtures
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from pathlib import Path
from typing import Any

# Scripts run from /agent without the package installed; keep imports below this line.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from record_crowd import AuditRecorder, preflight, read_secret  # noqa: E402

from draw.qloo.cache import MemoryCache  # noqa: E402
from draw.qloo.config import QlooConfig  # noqa: E402
from draw.qloo.exec_client import QlooExec  # noqa: E402
from draw.ranking.candidates import RECOMMEND_LIMIT  # noqa: E402

AGENT_DIR = Path(__file__).resolve().parents[1]
RECORDINGS_DIR = AGENT_DIR / "recordings" / "audit"
MUSIC_GENRE_TYPE = "urn:tag:genre:music"
ESTIMATED_CALLS = 3


class CandidatesRecorder(AuditRecorder):
    def __init__(self, secret: str | None) -> None:
        super().__init__(secret)
        self.names = ["candidates_baseline", "candidates_exclude_jazz", "candidates_include_jazz"]

    def check_name(self, workflow: str, request: dict[str, Any]) -> str:
        return self.names[self.count - 1]


def recorded_inputs() -> tuple[list[str], str, str]:
    """Signals and region from the crowd recording; the jazz music-genre URN from the audit."""
    crowd = json.loads(
        (RECORDINGS_DIR / "recommend" / "crowd_artists.request.json").read_text("utf-8")
    )["input"]
    jazz = json.loads((RECORDINGS_DIR / "find_tags" / "jazz.json").read_text("utf-8"))
    urn = next(r["id"] for r in jazz["results"] if r.get("type") == MUSIC_GENRE_TYPE)
    return list(crowd["signals"]), str(crowd["signal_location"]), str(urn)


def rows(result: Any) -> list[dict[str, Any]]:
    return (
        [r for r in result.results if isinstance(r, dict)]
        if isinstance(result.results, list)
        else []
    )


def spread(label: str, items: list[dict[str, Any]]) -> None:
    pops = sorted(float(r["popularity"]) for r in items if "popularity" in r)
    if not pops:
        print(f"  {label}: no popularity values")
        return
    print(
        f"  {label}: n={len(pops)} min={pops[0]:.4f} median={statistics.median(pops):.4f} "
        f"max={pops[-1]:.4f}  all={[round(p, 4) for p in pops]}"
    )


async def run() -> int:
    config = QlooConfig.from_env()
    if config.qloo_bin is None:
        sys.exit("qloo harness not found; run `npm ci` inside /agent")
    preflight(config.qloo_bin)
    signals, region, jazz = recorded_inputs()
    recorder = CandidatesRecorder(read_secret())
    qloo = QlooExec(config, cache=MemoryCache(), recorder=recorder)

    common: dict[str, Any] = {
        "signals": signals,
        "signal_location": region,
        "limit": RECOMMEND_LIMIT,
    }
    baseline = await qloo.recommend("artist", **common)
    excluded = await qloo.recommend(
        "artist", exclude_tags=[jazz], exclude_tags_operator="union", **common
    )
    included = await qloo.recommend(
        "artist", include_tags=[jazz], include_tags_operator="union", **common
    )

    base, excl, incl = rows(baseline), rows(excluded), rows(included)
    base_ids = {r["entity_id"] for r in base}
    b, e, i = baseline.status, excluded.status, included.status
    print(f"\nstatuses: baseline={b} exclude={e} include={i}")
    spread("baseline", base)
    spread("exclude ", excl)
    spread("include ", incl)
    removed = [r["name"] for r in base if r["entity_id"] not in {r["entity_id"] for r in excl}]
    kept = [r["name"] for r in incl if r["entity_id"] in base_ids]
    print(f"  exclude removed from baseline: {len(removed)} -> {removed}")
    print(f"  include kept from baseline:    {len(kept)} of {len(incl)}")
    for name, res in (("exclude", excluded), ("include", included)):
        interp = getattr(res, "interpretation", None) or {}
        print(f"  {name} interpreted tags: {interp.get(f'{name}_tags')}")
    print(f"\n{recorder.count} harness calls recorded under {RECORDINGS_DIR} (gitignored)")
    print("next: uv run python scripts/make_fixtures.py")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = parser.parse_args()
    print(f"Estimated Qloo calls: {ESTIMATED_CALLS} (3 recommend, artists)")
    if not args.yes:
        answer = input("Spend them now? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print("aborted before any call")
            return 1
    return asyncio.run(run())


if __name__ == "__main__":
    sys.exit(main())
