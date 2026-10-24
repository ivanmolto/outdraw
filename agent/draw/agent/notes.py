"""Run notes in the booker's words: the shortlist page shows them to a venue, not a developer.

Two sources feed `AgentResult.notes`. Outdraw's own notes are deterministic, but name the
Qloo workflow behind a warning (`compare_audiences[Act]: …`); `plain_note` swaps that label
for a plain one. The model's free-text notes tend to narrate its own pipeline
("build_crowd_profile skipped", "profile prof_1a2b"), so `model_notes` keeps only the ones
free of tool names, workflow names and internal ids, and at most `MAX_MODEL_NOTES` of them.
Pure functions: no I/O, same input, same notes.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Final

__all__ = ["MAX_MODEL_NOTES", "model_notes", "plain_note", "plain_notes"]

MAX_MODEL_NOTES = 2

LABELS: Final = {
    "recommend": "Qloo recommendations",
    "rank": "Qloo ranking",
    "describe": "Qloo lookup",
    "find_tags": "Qloo tag search",
    "compare_audiences": "Audience comparison",
    "where_popular": "Local heatmap",
    "trends": "Trend data",
    "audience_demographics": "Audience demographics",
    "build_crowd_profile": "Crowd profile",
    "generate_candidates": "Candidate search",
    "gather_signals": "Signal check",
    "score_and_rank": "Scoring",
}
"""Workflow and tool names as a booker would read them."""

_LABELLED = re.compile(r"^(?P<name>[a-z_]+)(?:\[(?P<subject>[^\]]*)\])?(?P<rest>(?: \([a-z]+\))?:)")
_INTERNAL = re.compile(
    r"\b(?:" + "|".join(map(re.escape, LABELS)) + r")\b"  # a tool or workflow name
    r"|\b[a-z]+_[a-z_]+\b"  # any other snake_case identifier
    r"|\b(?:prof|run|cand|ven|call)_[0-9a-f]+\b"  # an internal id
    r"|\burn:[\w:]+",  # a Qloo URN
    re.IGNORECASE,
)
"""Hard rules only. Plain words such as "candidates" or "signals" pass: a note like "Only 6
acts matched the genre" is worth keeping, and the prompt already tells the model not to
narrate its steps."""


def plain_note(note: str) -> str:
    """`compare_audiences[Big Thief]: …` → `Audience comparison for Big Thief: …`."""
    match = _LABELLED.match(note)
    if match is None or match["name"] not in LABELS:
        return note
    label = LABELS[match["name"]]
    if match["subject"]:
        label = f"{label} for {match['subject']}"
    return f"{label}{match['rest']}{note[match.end() :]}"


def plain_notes(notes: Iterable[str]) -> list[str]:
    """`plain_note` over Outdraw's own notes, first occurrence kept."""
    return list(dict.fromkeys(plain_note(n) for n in notes if n))


def model_notes(notes: Iterable[str]) -> list[str]:
    """The model's notes a booker can read: none that narrates tools or ids, at most two."""
    keep = [n.strip() for n in notes if n.strip() and not _INTERNAL.search(n)]
    return list(dict.fromkeys(keep))[:MAX_MODEL_NOTES]
