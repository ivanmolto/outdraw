#!/usr/bin/env python3
"""Audit every Qloo workflow through the harness CLI and record the real envelopes.

Runs `qloo exec <workflow> --input-file <tmp.json>` for a fixed list of checks, prints one
table row per check (workflow, status, result_count, note) and saves every full envelope to
``agent/recordings/audit/<workflow>/<check>.json`` with the request body alongside as
``<check>.request.json``. ``agent/recordings/`` is gitignored: Qloo's terms forbid its
response data in the public repo.

Usage (from ``/agent``)::

    uv run python scripts/qloo_audit.py            # prints the call estimate, asks first
    uv run python scripts/qloo_audit.py --yes      # no confirmation prompt

The API key is never printed. It is read only to redact it from recordings, and only when
the harness has it in the environment or its private config file.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

AGENT_DIR = Path(__file__).resolve().parents[1]
RECORDINGS_DIR = AGENT_DIR / "recordings" / "audit"

STATUSES = ("ok", "empty", "needs_input", "partial", "degraded", "error")
EXEC_TIMEOUT_S = 90
MIN_NODE = (22, 19)
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

# A syntactically valid UUID that no Qloo entity should have.
INVALID_ID = "00000000-0000-4000-8000-000000000000"
NY = "New York, NY"

QUOTA_QUERIES = [
    "blues",
    "folk",
    "hip hop",
    "techno",
    "soul",
    "punk",
    "metal",
    "reggae",
    "country",
    "classical",
    "ambient",
    "funk",
    "latin",
    "gospel",
    "house",
]

Envelope = dict[str, Any]


@dataclass
class Row:
    workflow: str
    check: str
    status: str
    result_count: int | None
    note: str


@dataclass
class Audit:
    harness: Harness
    rows: list[Row] = field(default_factory=list)
    entities: dict[str, str] = field(default_factory=dict)  # query -> entity id
    tags: dict[str, str] = field(default_factory=dict)  # query -> tag urn
    artist_pool: list[str] = field(default_factory=list)  # artist ids from recommend
    shapes: dict[str, list[str]] = field(default_factory=dict)  # workflow -> ok shapes
    exec_calls: int = 0
    force_fallbacks: bool = False

    def seed_from_previous_run(self) -> None:
        """With --only, reuse IDs recorded by an earlier full run instead of re-resolving."""
        summary_file = RECORDINGS_DIR / "_summary.json"
        if not summary_file.is_file():
            return
        previous = json.loads(summary_file.read_text(encoding="utf-8"))
        self.entities = dict(previous.get("entities") or {})
        self.tags = dict(previous.get("tags") or {})
        self.artist_pool = list(previous.get("artist_pool") or [])
        self.shapes = {k: list(v) for k, v in (previous.get("ok_shapes") or {}).items()}
        self.rows = [Row(**r) for r in previous.get("rows") or []]  # re-run checks replace rows
        print(f"seeded {len(self.entities)} entities, {len(self.tags)} tags from the previous run")

    def call(self, workflow: str, check: str, payload: dict[str, Any]) -> Envelope:
        env = self.harness.exec(workflow, payload)
        self.add_result(workflow, check, payload, env)
        return env

    def add_result(self, workflow: str, check: str, payload: dict[str, Any], env: Envelope) -> None:
        self.exec_calls += 1
        self.harness.record(workflow, check, payload, env)
        row = Row(workflow, check, env["status"], env.get("result_count"), note_for(env))
        self.rows = [r for r in self.rows if (r.workflow, r.check) != (workflow, check)]
        self.rows.append(row)
        print_row(row)
        if env["status"] == "ok":
            self.shapes.setdefault(workflow, []).append(shape_of(payload))


class Harness:
    """Thin wrapper over `qloo exec` that passes the full environment through."""

    def __init__(self, qloo_bin: str, secret: str | None) -> None:
        self.qloo_bin = qloo_bin
        self.secret = secret

    def exec(self, workflow: str, payload: dict[str, Any]) -> Envelope:
        with tempfile.TemporaryDirectory(prefix="qloo-audit-") as tmp:
            input_file = Path(tmp) / "input.json"
            input_file.write_text(json.dumps(payload), encoding="utf-8")
            try:
                proc = subprocess.run(
                    [self.qloo_bin, "exec", workflow, "--input-file", str(input_file)],
                    env=os.environ,  # full environment: the harness needs it for the key
                    capture_output=True,
                    text=True,
                    timeout=EXEC_TIMEOUT_S,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return synth_error(
                    workflow, "harness_timeout", f"no answer in {EXEC_TIMEOUT_S}s", 124
                )
        return normalise(workflow, proc.stdout, proc.stderr, proc.returncode)

    def record(self, workflow: str, check: str, payload: dict[str, Any], env: Envelope) -> None:
        folder = RECORDINGS_DIR / workflow
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{check}.request.json").write_text(
            self.redact(json.dumps({"workflow": workflow, "input": payload}, indent=2)) + "\n",
            encoding="utf-8",
        )
        (folder / f"{check}.json").write_text(
            self.redact(json.dumps(env, indent=2)) + "\n", encoding="utf-8"
        )

    def redact(self, text: str) -> str:
        if self.secret and self.secret in text:
            return text.replace(self.secret, "<redacted>")
        return text


def synth_error(workflow: str, code: str, recovery: str, exit_code: int) -> Envelope:
    return {
        "operation": workflow,
        "status": "error",
        "summary": f"The harness did not return an envelope (exit {exit_code}).",
        "results": [],
        "result_count": 0,
        "error": {"code": code, "layer": "harness", "retryable": False, "recovery": recovery},
        "harness_exit_code": exit_code,
    }


def normalise(workflow: str, stdout: str, stderr: str, exit_code: int) -> Envelope:
    """Return a six-status envelope whatever the harness printed.

    Exit 0 carries the envelope on stdout. Exit 1/4 carry `{error, operation, correlation_id}`
    with no `status`; exit 2 is a usage error with the message on stderr. A missing status is
    treated as `error`, as the kit's backend.mjs does.
    """
    raw: Any = None
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
    if isinstance(raw, dict) and raw.get("status") in STATUSES:
        env: Envelope = raw
        env.setdefault("results", [])
        env.setdefault("result_count", 0)
        env["harness_exit_code"] = exit_code
        return env
    first_err = next((ln for ln in stderr.splitlines() if ln.strip()), "").strip()
    raw_error = raw.get("error") if isinstance(raw, dict) else None
    if isinstance(raw_error, dict):
        env = synth_error(
            workflow,
            str(raw_error.get("code", "missing_status")),
            str(raw_error.get("recovery", first_err or "see stderr")),
            exit_code,
        )
        env["error"]["retryable"] = bool(raw_error.get("retryable", False))
        env["raw"] = raw
        return env
    code = "harness_usage" if exit_code == 2 else "harness_failed"
    env = synth_error(workflow, code, first_err or "no output", exit_code)
    env["stderr"] = stderr[-2000:]
    return env


# --- helpers -------------------------------------------------------------------------------


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def results_of(env: Envelope) -> list[dict[str, Any]]:
    res = env.get("results")
    return [r for r in res if isinstance(r, dict)] if isinstance(res, list) else []


def entity_ids(env: Envelope) -> list[str]:
    out: list[str] = []
    for r in results_of(env):
        value = r.get("entity_id") or r.get("id")
        if isinstance(value, str) and UUID_RE.fullmatch(value):
            out.append(value)
    return out


def tag_ids(env: Envelope) -> list[str]:
    return [
        r["id"]
        for r in results_of(env)
        if isinstance(r.get("id"), str) and r["id"].startswith("urn:tag:")
    ]


def shape_of(payload: dict[str, Any]) -> str:
    parts: list[str] = []
    for key, value in payload.items():
        if isinstance(value, list):
            kinds = {
                "uuid" if isinstance(v, str) and UUID_RE.fullmatch(v) else "str" for v in value
            }
            parts.append(f"{key}=[{'|'.join(sorted(kinds))} x{len(value)}]")
        elif isinstance(value, str) and UUID_RE.fullmatch(value):
            parts.append(f"{key}=<uuid>")
        elif isinstance(value, str) and value.startswith("urn:"):
            parts.append(f"{key}=<urn>")
        elif key in {
            "query",
            "entity",
            "within",
            "signal_location",
            "filter_location",
            "demographic",
        }:
            parts.append(f"{key}=<text>")
        else:
            parts.append(f"{key}={value!r}")
    return ", ".join(parts)


def issues_of(env: Envelope) -> list[dict[str, Any]]:
    """`needs_input` envelopes list unresolved inputs under resolution.issues[]."""
    resolution = env.get("resolution")
    issues = resolution.get("issues") if isinstance(resolution, dict) else None
    return [i for i in issues if isinstance(i, dict)] if isinstance(issues, list) else []


def note_for(env: Envelope) -> str:
    status = env["status"]
    summary = str(env.get("summary") or "").replace("\n", " ")
    if status == "error":
        err = env.get("error") or {}
        return f"{err.get('code')} retryable={err.get('retryable')}: {err.get('recovery', '')}"[
            :110
        ]
    if status in ("ok", "partial", "degraded"):
        reqs = env.get("provenance", {}).get("requests") or []
        results = env.get("results")
        if isinstance(results, dict):  # compare_audiences returns an object
            where = f"results keys={sorted(results)}"
        elif isinstance(env.get("series"), list):  # trends returns series[]
            where = f"series={len(env['series'])}"
        else:
            top = results_of(env)
            name = top[0].get("name") if top else None
            where = f"{len(top)} results; top={name!r}"
        return f"{where}; {len(reqs)} http req; {summary}"[:110]
    if status == "needs_input":
        kinds = [
            f"{i.get('field')}:{i.get('kind')}({len(i.get('candidates') or [])})"
            for i in issues_of(env)
        ]
        return f"issues={' '.join(kinds) or 'none'}; {summary}"[:110]
    return summary[:110]


def print_row(row: Row) -> None:
    count = "" if row.result_count is None else str(row.result_count)
    print(f"{row.workflow:<22} {row.check:<34} {row.status:<12} {count:>5}  {row.note}")


def print_header() -> None:
    print(f"{'workflow':<22} {'check':<34} {'status':<12} {'count':>5}  note")
    print("-" * 120)


def node_version() -> tuple[int, ...]:
    out = subprocess.run(["node", "--version"], capture_output=True, text=True, check=False).stdout
    match = re.match(r"v(\d+)\.(\d+)", out.strip())
    return (int(match.group(1)), int(match.group(2))) if match else (0, 0)


def read_secret() -> str | None:
    """The key is read only so it can be redacted from recordings; it is never printed."""
    value = os.environ.get("QLOO_API_KEY")
    if value:
        return value
    home = Path(os.environ.get("QLOO_HOME") or Path.home() / ".qloo")
    config = home / "config"
    if config.is_file():
        for line in config.read_text(encoding="utf-8").splitlines():
            if line.startswith("api_key="):
                return line.split("=", 1)[1].strip() or None
    return None


def preflight(qloo_bin: str) -> None:
    version = subprocess.run([qloo_bin, "--version"], capture_output=True, text=True, check=False)
    print(f"qloo --version: {version.stdout.strip() or version.stderr.strip()}")
    node = node_version()
    print(f"node: {'.'.join(map(str, node))}")
    if node < MIN_NODE:
        sys.exit(f"Node {MIN_NODE[0]}.{MIN_NODE[1]}+ is required on PATH")
    status = subprocess.run(
        [qloo_bin, "setup", "--status"], env=os.environ, capture_output=True, text=True, check=False
    )
    print(status.stdout.strip())
    # The exit code is 1 whenever the optional model is unconfigured, so judge readiness
    # by the Qloo data line alone.
    if "Qloo data: ready" not in status.stdout:
        sys.exit("Qloo harness is not ready (run `qloo setup --qloo`); aborting before any call.")


# --- checks ---------------------------------------------------------------------------------


def check_find_tags(a: Audit) -> None:
    for query in ("jazz", "indie rock", "stand-up comedy"):
        env = a.call("find_tags", slug(query), {"query": query, "limit": 5})
        ids = tag_ids(env)
        if ids:
            a.tags[query] = ids[0]


def check_describe(a: Audit) -> None:
    # Norah Jones is added so the recommend checks have two resolved *artist* IDs.
    queries: list[tuple[str, str | None]] = [
        ("Radiohead", "artist"),
        ("Norah Jones", "artist"),
        ("Blue Note Jazz Club", "place"),
        ("NYC, NY", None),
    ]
    for name, kind in queries:
        payload: dict[str, Any] = {"entity": name}
        if kind:
            payload["type"] = kind
        env = a.call("describe", slug(name), payload)
        ids = entity_ids(env)
        if ids:
            a.entities[name] = ids[0]


def signal_ids(a: Audit) -> list[str]:
    ids = [a.entities[n] for n in ("Radiohead", "Norah Jones") if n in a.entities]
    if len(ids) < 2:
        print("!! fewer than two artist IDs resolved; recommend checks fall back to names")
        ids = ids + [n for n in ("Radiohead", "Norah Jones") if n not in a.entities]
    return ids


def check_recommend_artists(a: Audit) -> None:
    signals = signal_ids(a)
    env = a.call(
        "recommend",
        "artists_two_signals",
        {"target_type": "artist", "signals": signals, "limit": 10},
    )
    a.artist_pool = [i for i in entity_ids(env) if i not in signals]
    a.call(
        "recommend",
        "artists_two_signals_filter_location_ny",
        {"target_type": "artist", "signals": signals, "filter_location": NY, "limit": 10},
    )
    a.call(
        "recommend",
        "artists_two_signals_signal_location_ny",
        {"target_type": "artist", "signals": signals, "signal_location": NY, "limit": 10},
    )


def jazz_bar_tag(a: Audit) -> str | None:
    if "jazz bar" not in a.tags:
        env = a.call("find_tags", "jazz_bar", {"query": "jazz bar", "limit": 5})
        ids = tag_ids(env)
        if ids:
            a.tags["jazz bar"] = ids[0]
    return a.tags.get("jazz bar") or a.tags.get("jazz")


def confirm_first_candidates(env: Envelope) -> list[str]:
    """Resolve → confirm → ask, audit edition: take the top candidate of each unresolved tag."""
    chosen: list[str] = []
    for issue in issues_of(env):
        candidates = issue.get("candidates") or []
        first = candidates[0] if candidates and isinstance(candidates[0], dict) else None
        if first and isinstance(first.get("id"), str):
            chosen.append(first["id"])
    return chosen


def check_recommend_cross_domain(a: Audit) -> None:
    signals = signal_ids(a)
    single = signals[:1]
    for target in ("place", "movie", "brand"):
        base: dict[str, Any] = {"target_type": target, "signals": signals, "limit": 10}
        if target == "place":
            base["filter_location"] = NY  # places only make sense near the venue
        env = a.call("recommend", f"cross_{target}_two_signals", base)
        if target == "place":
            tags_env = a.call(
                "recommend",
                "cross_place_two_signals_bars_restaurants",
                {**base, "include_tags": ["bar", "restaurant"], "include_tags_operator": "union"},
            )
            urns = confirm_first_candidates(tags_env) if tags_env["status"] == "needs_input" else []
            if urns:  # words were ambiguous: retry with the confirmed tag URNs
                a.call(
                    "recommend",
                    "cross_place_two_signals_bars_restaurants_urns",
                    {**base, "include_tags": urns, "include_tags_operator": "union"},
                )
        if env["status"] == "ok" and not a.force_fallbacks:
            continue
        # (a) one well-known artist as the only signal: cross-domain gap vs data gap
        a.call("recommend", f"cross_{target}_single_signal", {**base, "signals": single})
        # (b) fallbacks independent of artist affinity
        region_key = "filter_location" if target == "place" else "signal_location"
        # A filter alone is refused locally ("provide at least one taste signal"), so the
        # region-only route always carries signal_location; places add the filter too.
        region_only: dict[str, Any] = {"target_type": target, "signal_location": NY, "limit": 10}
        if target == "place":
            region_only["filter_location"] = NY
        a.call("recommend", f"cross_{target}_region_only", region_only)
        tag = jazz_bar_tag(a) if target == "place" else a.tags.get("jazz")
        if tag:
            a.call(
                "recommend",
                f"cross_{target}_tag_plus_region",
                {"target_type": target, "signal_tags": [tag], region_key: NY, "limit": 10},
            )


def check_rank(a: Audit) -> None:
    signals = signal_ids(a)
    options = (a.artist_pool + signals)[:5]
    a.call(
        "rank",
        "five_artists_vs_signals",
        {"options": options, "option_type": "artist", "signals": signals},
    )


def check_where_popular(a: Audit) -> None:
    artist = a.entities.get("Radiohead", "Radiohead")
    # NY decides the artist side; United States shows the country-level shape.
    for region in (NY, "United States"):
        a.call(
            "where_popular",
            f"radiohead_{slug(region)}",
            {"entity": artist, "entity_type": "artist", "within": region, "limit": 10},
        )


def check_compare_audiences(a: Audit) -> None:
    signals = signal_ids(a)
    env = a.call(
        "compare_audiences",
        "two_artists",
        {"group_a": signals[:1], "group_b": signals[1:2], "limit": 10},
    )
    if env["status"] != "ok":
        a.call(
            "compare_audiences",
            "two_artists_target_artist",
            {"group_a": signals[:1], "group_b": signals[1:2], "target_type": "artist", "limit": 10},
        )


def check_audience_demographics(a: Audit) -> None:
    artist = a.entities.get("Radiohead", "Radiohead")
    a.call("audience_demographics", "radiohead", {"entity": artist, "entity_type": "artist"})


def check_trends(a: Audit) -> None:
    signals = signal_ids(a)
    entities = (signals + a.artist_pool)[:3]
    end = dt.date.today()
    start = end - dt.timedelta(days=365)
    a.call(
        "trends",
        "three_artists_last_12_months",
        {
            "entities": entities,
            "entity_type": "artist",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "limit": 10,
        },
    )


def check_errors(a: Audit) -> None:
    a.call("describe", "invalid_id", {"entity": INVALID_ID, "type": "artist"})
    a.call(
        "recommend",
        "invalid_signal_id",
        {"target_type": "artist", "signals": [INVALID_ID], "limit": 5},
    )
    a.call("find_tags", "empty_query", {"query": ""})  # rejected by schema validation, no quota
    a.call("find_tags", "nonsense_query", {"query": "zqxv plortch 7781 wumbleflax", "limit": 5})
    # Semantic search always finds *something*; exact matching is the route to `empty`.
    a.call(
        "find_tags",
        "nonsense_query_exact",
        {"query": "zqxv plortch 7781 wumbleflax", "semantic": False, "limit": 5},
    )


def check_quota(a: Audit) -> None:
    """15 quick find_tags calls in parallel, to see what rate limiting looks like."""
    payloads = [
        (f"quota_{i + 1:02d}", {"query": q, "limit": 1}) for i, q in enumerate(QUOTA_QUERIES)
    ]

    def run(item: tuple[str, dict[str, Any]]) -> tuple[str, dict[str, Any], Envelope]:
        check, payload = item
        return check, payload, a.harness.exec("find_tags", payload)

    with ThreadPoolExecutor(max_workers=8) as pool:
        for check, payload, env in pool.map(run, payloads):
            a.add_result("find_tags", check, payload, env)


CHECKS: list[tuple[str, Callable[[Audit], None], int]] = [
    ("1 find_tags", check_find_tags, 3),
    ("2 describe", check_describe, 4),
    ("3 recommend artists", check_recommend_artists, 3),
    ("4 recommend cross-domain", check_recommend_cross_domain, 14),
    ("5 rank", check_rank, 1),
    ("6 where_popular", check_where_popular, 2),
    ("7 compare_audiences", check_compare_audiences, 2),
    ("8 audience_demographics", check_audience_demographics, 1),
    ("9 trends", check_trends, 1),
    ("10 error handling", check_errors, 5),
    ("11 quota", check_quota, 15),
]


# --- summary --------------------------------------------------------------------------------


def print_summary(a: Audit) -> None:
    print("\n=== Summary ===")
    by_workflow: dict[str, list[Row]] = {}
    for row in a.rows:
        by_workflow.setdefault(row.workflow, []).append(row)
    print(f"harness invocations: {a.exec_calls} (each may issue more than one HTTP request)")
    print("\nWorkflows:")
    for workflow, rows in by_workflow.items():
        statuses = sorted({r.status for r in rows})
        ok = sum(1 for r in rows if r.status == "ok")
        print(f"  {workflow:<22} ok {ok}/{len(rows)}  statuses seen: {', '.join(statuses)}")
    print("\nInput shapes that returned ok (harness field names):")
    for workflow, shapes in a.shapes.items():
        for shape in sorted(set(shapes)):
            print(f"  {workflow:<22} {shape}")
    print("\nResolved entities (query -> id found):", {k: bool(v) for k, v in a.entities.items()})
    print("Tags found:", {k: bool(v) for k, v in a.tags.items()})
    print(f"Artist pool from recommend: {len(a.artist_pool)} ids")
    errors = [r for r in a.rows if r.status == "error"]
    print("\nError envelopes:")
    for r in errors or []:
        print(f"  {r.workflow}/{r.check}: {r.note}")
    if not errors:
        print("  none")
    quota_errors = [r for r in a.rows if r.check.startswith("quota_") and r.status != "ok"]
    non_ok = [r.note for r in quota_errors] or "none"
    print(f"\nQuota probe: {15 - len(quota_errors)}/15 ok; non-ok: {non_ok}")
    summary = {
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "exec_calls": a.exec_calls,
        "rows": [row.__dict__ for row in a.rows],
        "entities": a.entities,
        "tags": a.tags,
        "artist_pool": a.artist_pool,
        "ok_shapes": a.shapes,
    }
    (RECORDINGS_DIR / "_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\nRecordings: {RECORDINGS_DIR} (gitignored)")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    parser.add_argument("--only", help="comma-separated check numbers to run, e.g. 1,2,6")
    parser.add_argument(
        "--force-fallbacks",
        action="store_true",
        help="run the cross-domain fallback routes even when the primary route returns ok",
    )
    args = parser.parse_args()

    qloo_bin = shutil.which("qloo")
    if not qloo_bin:
        sys.exit("`qloo` is not on PATH (npm install --global @qloo/qloo-harness)")
    preflight(qloo_bin)

    selected = CHECKS
    if args.only:
        wanted = {s.strip() for s in args.only.split(",")}
        selected = [c for c in CHECKS if c[0].split(" ", 1)[0] in wanted]
    estimate = sum(n for _, _, n in selected)
    print(
        f"\nEstimated harness invocations: up to {estimate} (monthly quota is shared: 10,000/key)."
    )
    if not args.yes:
        answer = input("Spend quota on these calls? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Aborted before any Qloo call.")
            return 1

    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    audit = Audit(Harness(qloo_bin, read_secret()), force_fallbacks=args.force_fallbacks)
    if args.only:
        audit.seed_from_previous_run()
    print()
    print_header()
    for label, fn, _ in selected:
        print(f"-- {label}")
        fn(audit)
    print_summary(audit)
    missing = [r for r in audit.rows if r.status not in STATUSES or not r.note]
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
