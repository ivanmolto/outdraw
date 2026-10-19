#!/usr/bin/env python3
"""Turn private Qloo recordings into synthetic fixtures that are safe for the public repo.

Reads ``agent/recordings/audit/<workflow>/<check>.json`` (+ ``<check>.request.json``) and
writes ``agent/tests/fixtures/qloo/<workflow>/<check>.json`` (+ ``<check>.request.json``)
with the same structure, status and field names but fake entity IDs, names, tags, scores
and free text. The request shape is kept, with entity IDs swapped for the same fake IDs.

Qloo's terms forbid its response data in the public repo, so the recordings stay gitignored
and only these fixtures are committed. A few extra fixtures with forced statuses (`empty`,
`partial`, `degraded`, retryable `error`) are derived from real `ok`/`error` envelopes so
every status branch can be unit-tested.

Usage (from ``/agent``)::

    uv run python scripts/make_fixtures.py
"""

from __future__ import annotations

import argparse
import json
import random
import re
import shutil
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

AGENT_DIR = Path(__file__).resolve().parents[1]
RECORDINGS_DIR = AGENT_DIR / "recordings" / "audit"
FIXTURES_DIR = AGENT_DIR / "tests" / "fixtures" / "qloo"

NAMESPACE = uuid.UUID("6f1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d")
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
TAG_PREFIX = "urn:tag:"
WORD_RE = re.compile(r"^[A-Za-z0-9 ,.'&\-]+$")

# String values under these keys are structural (statuses, type URNs, endpoints) and stay.
KEEP_KEYS = frozenset(
    {
        "status",
        "operation",
        "schema_version",
        "type",
        "subtype",
        "parent_types",
        "match",
        "kind",
        "input_kind",
        "field",
        "scope",
        "purpose",
        "transport",
        "transport_name",
        "method",
        "path",
        "endpoint",
        "source",
        "documentation",
        "provider_id",
        "provider_name",
        "entity_strategy",
        "tag_strategy",
        "fallback",
        "strategy",
        "contract_version",
        "source_contract_version",
        "job",
        "code",
        "layer",
        "recovery",
        "start_date",
        "end_date",
        "date",
        "entity_type",
        "target_type",
        "option_type",
        "semantic",
        "assumptions",
        "include_tags_operator",
        "signal_tags_operator",
        "exclude_tags_operator",
        "workflow",
        "filter.type",
        "filter.start_date",
        "filter.end_date",
        "feature.explainability",
        "feature.semantic_search",
        "harness_exit_code",
        "count",
        "_synthetic",
    }
)
# String values under these keys are names of real things (entities, tags, places, words).
NAME_KEYS = frozenset(
    {
        "name",
        "title",
        "input",
        "query",
        "entity",
        "within",
        "signal_location",
        "filter_location",
        "canonical_name",
        "label",
        "entities",
        "options",
        "signals",
        "group_a",
        "group_b",
        "include_tags",
        "exclude_tags",
        "signal_tags",
        "filter.location.query",
        "signal.location.query",
        "filter.query",
        "location",
        "concept",
    }
)
REGION_KEYS = frozenset(
    {
        "within",
        "signal_location",
        "filter_location",
        "filter.location.query",
        "signal.location.query",
        "location",
    }
)
CONCEPT_KEYS = frozenset({"query", "concept", "filter.query"})
# Free text or contact data that is simply replaced by a placeholder.
TEXT_KEYS = frozenset(
    {"description", "short_description", "address", "website", "phone", "url", "geohash"}
)
SCORE_KEYS = frozenset({"popularity", "affinity", "score", "avg_score", "rank", "affinity_rank"})
SKIP_FILES = ("_",)


def recordings() -> Iterator[tuple[str, str, Path, Path | None]]:
    """Yield (workflow, check, envelope_path, request_path) for every recording."""
    for folder in sorted(p for p in RECORDINGS_DIR.iterdir() if p.is_dir()):
        for env_path in sorted(folder.glob("*.json")):
            if env_path.name.startswith(SKIP_FILES) or env_path.name.endswith(".request.json"):
                continue
            check = env_path.stem
            req_path = folder / f"{check}.request.json"
            yield folder.name, check, env_path, (req_path if req_path.is_file() else None)


def is_tag_id(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(TAG_PREFIX)


def walk(
    value: Any, key: str = "", parent: dict[str, Any] | None = None
) -> Iterator[tuple[str, Any, dict[str, Any] | None]]:
    """Yield (key, leaf, parent dict) for every leaf under value."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield from walk(v, k, value)
    elif isinstance(value, list):
        for v in value:
            yield from walk(v, key, parent)
    else:
        yield key, value, parent


def real_names(data: Any) -> set[str]:
    """Names of real things in a recording: plain words under name keys (not IDs or URNs)."""
    names: set[str] = set()
    for key, leaf, _ in walk(data):
        if key in NAME_KEYS and isinstance(leaf, str):
            text = leaf.strip()
            if len(text) >= 3 and not UUID_RE.search(text) and not is_tag_id(text):
                if WORD_RE.match(text) and not text.isdigit():
                    names.add(text)
    return names


def real_tag_ids(data: Any) -> set[str]:
    ids: set[str] = set()
    for key, leaf, _ in walk(data):
        if is_tag_id(leaf) and key in {
            "id",
            "tag_id",
            "include_tags",
            "exclude_tags",
            "signal_tags",
            "filter.tags",
            "signal.interests.tags",
        }:
            ids.add(leaf)
        elif isinstance(leaf, str) and key not in KEEP_KEYS and "," in leaf and TAG_PREFIX in leaf:
            ids.update(part for part in leaf.split(",") if is_tag_id(part))
    return ids


def real_uuids(text: str) -> set[str]:
    return {m.upper() for m in UUID_RE.findall(text)}


class Synthesizer:
    """Deterministic real→fake mapping shared across every fixture."""

    def __init__(self) -> None:
        self.names: dict[str, str] = {}  # lower-cased real name -> fake name
        self.tags: dict[str, str] = {}
        self.counters: dict[str, int] = {}
        self.name_re: re.Pattern[str] | None = None

    def learn(self, data: Any) -> None:
        for key, leaf, parent in walk(data):
            if key in NAME_KEYS and isinstance(leaf, str):
                self.learn_name(leaf, key, parent)
        for tag in real_tag_ids(data):
            self.fake_tag(tag)

    def learn_name(self, value: str, key: str, parent: dict[str, Any] | None) -> None:
        text = value.strip()
        if len(text) < 3 or UUID_RE.search(text) or is_tag_id(text) or not WORD_RE.match(text):
            return
        if text.isdigit() or text.lower() in self.names:
            return
        if key in REGION_KEYS:
            category = "Region"
        elif key in CONCEPT_KEYS:
            category = "Concept"
        elif parent and any(is_tag_id(parent.get(k)) for k in ("id", "tag_id")):
            category = "Tag"
        elif key in {"include_tags", "exclude_tags", "signal_tags"}:
            category = "Tag"
        else:
            category = "Entity"
        self.names[text.lower()] = f"Synthetic {category} {self.next(category)}"
        self.name_re = None

    def next(self, category: str) -> int:
        self.counters[category] = self.counters.get(category, 0) + 1
        return self.counters[category]

    def fake_tag(self, tag: str) -> str:
        if tag not in self.tags:
            # Keep only the tag class (genre, cuisine, ...) so no real URN segment survives:
            # a real tag id can be a prefix of another, and its last segment is a real name.
            parts = tag.split(":")
            tag_class = parts[2] if len(parts) > 2 else "tag"
            self.tags[tag] = f"urn:tag:synthetic:{tag_class}:{self.next('tag_id'):03d}"
        return self.tags[tag]

    @staticmethod
    def fake_uuid(real: str) -> str:
        fake = str(uuid.uuid5(NAMESPACE, real.lower()))
        return fake.upper() if real.isupper() or real == real.upper() else fake

    def names_pattern(self) -> re.Pattern[str] | None:
        if not self.names:
            return None
        if self.name_re is None:
            ordered = sorted(self.names, key=len, reverse=True)
            self.name_re = re.compile(
                r"(?<![A-Za-z0-9])("
                + "|".join(re.escape(n) for n in ordered)
                + r")(?![A-Za-z0-9])",
                re.IGNORECASE,
            )
        return self.name_re

    def rewrite_text(self, text: str) -> str:
        text = UUID_RE.sub(lambda m: self.fake_uuid(m.group(0)), text)
        for real, fake in self.tags.items():
            text = text.replace(real, fake)
        pattern = self.names_pattern()
        if pattern:
            text = pattern.sub(lambda m: self.names[m.group(0).lower()], text)
        return text

    def rewrite(self, value: Any, key: str = "", path: tuple[str | int, ...] = ()) -> Any:
        if isinstance(value, dict):
            return {k: self.rewrite(v, k, (*path, k)) for k, v in value.items()}
        if isinstance(value, list):
            return [self.rewrite(v, key, (*path, i)) for i, v in enumerate(value)]
        if isinstance(value, bool) or value is None:
            return value
        if isinstance(value, float) or (isinstance(value, int) and key in SCORE_KEYS):
            return self.fake_number(path)
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            return self.rewrite_str(value, key, path)
        return value

    def rewrite_str(self, value: str, key: str, path: tuple[str | int, ...]) -> str:
        if key in KEEP_KEYS and not UUID_RE.search(value):
            return value
        if is_tag_id(value) and value in self.tags:
            return self.tags[value]
        if key in TEXT_KEYS or "properties" in path:
            return "synthetic text" if key != "geohash" else "s0000000"
        if key in NAME_KEYS and value.strip().lower() in self.names:
            return self.names[value.strip().lower()]
        if value.isdigit():  # numeric strings such as a population rank are scores too
            return str(int(self.fake_number(path) * 1000))
        return self.rewrite_text(value)

    @staticmethod
    def fake_number(path: tuple[str | int, ...]) -> float:
        """Descending along a list so ordered results keep their order; deterministic."""
        index = next((p for p in reversed(path) if isinstance(p, int)), 0)
        rng = random.Random("|".join(map(str, path)))
        value = 0.97 - 0.045 * index + rng.uniform(-0.01, 0.01)
        return round(min(0.99, max(0.01, value)), 4)


def derived_fixtures(
    synth_env: dict[str, Any], workflow: str, check: str
) -> dict[str, dict[str, Any]]:
    """Extra statuses the real key never returned, forced onto a synthetic envelope."""
    out: dict[str, dict[str, Any]] = {}
    note = f"derived from {workflow}/{check}: status forced so the branch can be tested"
    if workflow == "recommend" and check == "artists_two_signals" and synth_env["status"] == "ok":
        results = synth_env.get("results") or []
        base = {k: v for k, v in synth_env.items() if k not in {"explainability"}}
        out["derived_empty"] = {
            **base,
            "_synthetic": note,
            "status": "empty",
            "results": [],
            "result_count": 0,
            "summary": "No results matched the supplied signals. Refine the input.",
        }
        out["derived_partial"] = {
            **base,
            "_synthetic": note,
            "status": "partial",
            "results": results[:4],
            "result_count": len(results[:4]),
            "summary": "Only some results came back; one signal could not be applied.",
            "warnings": ["One signal was ignored by the upstream request."],
        }
        out["derived_degraded"] = {
            **base,
            "_synthetic": note,
            "status": "degraded",
            "summary": "Results came back through a fallback with reduced quality.",
            "warnings": ["Semantic resolution fell back to exact matching."],
        }
    if workflow == "find_tags" and check == "empty_query" and synth_env["status"] == "error":
        out["derived_error_retryable"] = {
            **{k: v for k, v in synth_env.items() if k not in {"raw", "stderr"}},
            "_synthetic": note,
            "summary": "Qloo rate limit reached.",
            "error": {
                "code": "QLOO_RATE_LIMIT",
                "layer": "qloo",
                "retryable": True,
                "recovery": "Wait for the rate-limit window to pass, then retry the operation.",
            },
        }
    return out


def build(clean: bool) -> int:
    if not RECORDINGS_DIR.is_dir():
        sys.exit(f"no recordings at {RECORDINGS_DIR}; run scripts/qloo_audit.py first")
    items = list(recordings())
    synth = Synthesizer()
    for _, _, env_path, req_path in items:
        synth.learn(json.loads(env_path.read_text(encoding="utf-8")))
        if req_path:
            synth.learn(json.loads(req_path.read_text(encoding="utf-8")))
    if clean and FIXTURES_DIR.is_dir():
        shutil.rmtree(FIXTURES_DIR)
    written = 0
    for workflow, check, env_path, req_path in items:
        folder = FIXTURES_DIR / workflow
        folder.mkdir(parents=True, exist_ok=True)
        env = synth.rewrite(json.loads(env_path.read_text(encoding="utf-8")))
        env = {
            "_synthetic": "structure from a real envelope; ids, names and scores are fake",
            **env,
        }
        (folder / f"{check}.json").write_text(json.dumps(env, indent=2) + "\n", encoding="utf-8")
        written += 1
        if req_path:
            req = synth.rewrite(json.loads(req_path.read_text(encoding="utf-8")))
            (folder / f"{check}.request.json").write_text(
                json.dumps(req, indent=2) + "\n", encoding="utf-8"
            )
        for name, derived in derived_fixtures(env, workflow, check).items():
            (folder / f"{name}.json").write_text(
                json.dumps(derived, indent=2) + "\n", encoding="utf-8"
            )
            written += 1
    print(f"wrote {written} fixtures to {FIXTURES_DIR} from {len(items)} recordings")
    print(f"mapped {len(synth.names)} names, {len(synth.tags)} tag ids")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--no-clean", action="store_true", help="keep fixtures that have no recording"
    )
    args = parser.parse_args()
    return build(clean=not args.no_clean)


if __name__ == "__main__":
    sys.exit(main())
