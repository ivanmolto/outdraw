#!/usr/bin/env python3
"""Backfill each stored candidate's genre tag names from the run's recorded past-fit call.

Runs stored before `evidence_trail` kept `genres` (T44) show no genre filter. Their
`compare_audiences` calls were recorded under the run in `qloo_calls` (`QLOO_RECORD=1`,
as every seed run is), and the tag rows there carry the names, so the genres can be read
back with `genres_of` (the same reader a live run uses) and written into
`candidates.evidence["genres"]`. Zero Qloo calls, zero model calls, no run rebuilt: the
score and the rest of the trail are untouched.

A candidate whose past-fit call was not recorded (outside the signalled top N, or a run
recorded with `--no-record`) is left as it is, with `genres: []`, and counted as skipped.

Usage (from ``/agent``; ``../.env.local`` is read for ``DATABASE_URL``)::

    uv run python scripts/backfill_genres.py --dry-run
    uv run python scripts/backfill_genres.py                 # every ok run
    uv run python scripts/backfill_genres.py --run run_seed_nola-jazz
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from precompute import load_env_file  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm.attributes import flag_modified  # noqa: E402

from draw.db.engine import make_engine, make_sessionmaker  # noqa: E402
from draw.db.models import CandidateRow, QlooCallRow, RunRow  # noqa: E402
from draw.qloo.envelope import QlooResult  # noqa: E402
from draw.ranking.signals import genres_of  # noqa: E402


def genres_by_entity(calls: list[QlooCallRow]) -> dict[str, list[str]]:
    """Each candidate's genres from its own past-fit call (`group_a` is the candidate)."""
    out: dict[str, list[str]] = {}
    for call in calls:
        group_a = call.request.get("group_a")
        if not isinstance(group_a, list) or len(group_a) != 1 or call.status != "ok":
            continue
        result = QlooResult.model_validate(
            {**call.envelope, "call_id": call.call_id, "workflow": call.workflow}
        )
        out[str(group_a[0])] = list(genres_of(result))
    return out


async def backfill(only: str | None, *, dry_run: bool) -> int:
    engine = make_engine()
    sessions = make_sessionmaker(engine)
    filled = skipped = 0
    try:
        async with sessions.begin() as session:
            query = select(RunRow).where(RunRow.status == "ok")
            if only:
                query = query.where(RunRow.id == only)
            for run in (await session.scalars(query)).all():
                calls = (
                    await session.scalars(
                        select(QlooCallRow).where(
                            QlooCallRow.run_id == run.id,
                            QlooCallRow.workflow == "compare_audiences",
                        )
                    )
                ).all()
                genres = genres_by_entity(list(calls))
                rows = (
                    await session.scalars(select(CandidateRow).where(CandidateRow.run_id == run.id))
                ).all()
                hits = 0
                for row in rows:
                    found = genres.get(row.entity_id)
                    if found is None:
                        skipped += 1
                        continue
                    if row.evidence.get("genres") == found:
                        continue
                    row.evidence = {**row.evidence, "genres": found}
                    flag_modified(row, "evidence")
                    hits += 1
                filled += hits
                print(f"{run.id}: {hits} of {len(rows)} candidates gain genres")
            if dry_run:
                await session.rollback()
    finally:
        await engine.dispose()
    verb = "would gain" if dry_run else "gained"
    print(f"{filled} candidates {verb} genres; {skipped} have no recorded past-fit call")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="report; write nothing")
    parser.add_argument("--run", help="only this run id")
    args = parser.parse_args(argv)
    load_env_file()
    return asyncio.run(backfill(args.run, dry_run=args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
