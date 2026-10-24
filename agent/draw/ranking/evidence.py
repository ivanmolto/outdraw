"""The evidence trail: every number in a `Score` traced to a Qloo call or to Draw's own work.

`build_trail(candidate, score, ...)` is a pure function that assembles the `EvidenceTrail`
(T04 schema) behind one scored candidate:

- **inputs** — what fed the score, one `TrailInput` each: the crowd signals with their
  sell-through weights, the venue region, the requested size tier, the act's Qloo
  popularity and its booking history at this venue.
- **qloo_calls** — the gateway calls behind the Qloo-backed sub-scores (`Score.evidence`),
  as the run store observed them: `call_id`, workflow, request shape (`params_summary`),
  envelope status and cache flag. No raw request or response.
- **affinities** — the value Draw read from an envelope for each Qloo-backed sub-score,
  with the `call_id` it came from: `recommend` affinity for `crowd_affinity`,
  `compare_audiences` overlap for `past_fit`, `where_popular` for `local_heat`, `trends`
  for `momentum`.
- **sub_scores**, **weights**, **total** — copied from the `Score`, so the validator can
  check that nothing in the score is missing from the trail.
- **source_labels** — one label per item: `"qloo"` when the number came out of an envelope,
  `"draw"` when Draw computed or templated it (weights, the total, neutral fallbacks,
  novelty, size fit, the sell-through weights).

Label keys name the item they describe (`REF_*`): a sub-score by its bare name (what the
UI reads), `total`, `weight:<sub-score>`, `input:<ref>`, `call:<call_id>` and
`affinity:<sub-score>`.

`validate_trail(score, trail)` is the completeness check behind the DoD "no orphan
numbers": every number in the `Score` appears in the trail with a source label, every trail
item is labelled, every label points at an item, every Qloo-labelled sub-score has an
affinity from a call listed in the trail, and the total is the weighted sum of the
sub-scores. It returns the problems found (empty when complete); `assert_complete` raises
`TrailError` with them.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any, Final

from draw.ranking.score import WEIGHTS, WORKFLOW_OF, SourceLabel, SubScore, source_labels
from draw.ranking.signals import CandidateSignals, SignalValue
from draw.schemas import (
    Affinity,
    Candidate,
    Constraints,
    CrowdProfile,
    EvidenceTrail,
    QlooCallRecord,
    Score,
    TrailInput,
)

__all__ = [
    "REF_AFFINITY",
    "REF_CALL",
    "REF_INPUT",
    "REF_TOTAL",
    "REF_WEIGHT",
    "TrailError",
    "affinities_by_sub_score",
    "assert_complete",
    "build_trail",
    "params_summary",
    "validate_trail",
]

REF_TOTAL: Final = "total"
REF_WEIGHT: Final = "weight:"
REF_INPUT: Final = "input:"
REF_CALL: Final = "call:"
REF_AFFINITY: Final = "affinity:"
TOLERANCE: Final = 1e-9

_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_TAG = "urn:tag:"
# Request fields whose values are Draw's own choices (types, limits, operators, dates); the
# rest are IDs (shown as placeholders) or free text Draw typed (regions, query words).
_SCALARS: Final = frozenset(
    {
        "target_type",
        "entity_type",
        "option_type",
        "type",
        "limit",
        "semantic",
        "include_tags_operator",
        "exclude_tags_operator",
        "start_date",
        "end_date",
    }
)


class TrailError(ValueError):
    """An evidence trail with orphan numbers or unlabelled items."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


# --- request shape --------------------------------------------------------------------


def _is_id(value: str) -> bool:
    return bool(_UUID.match(value)) or value.startswith(_TAG)


def params_summary(workflow: str, request: Mapping[str, Any]) -> str:
    """The request shape as one line: keys, list lengths and Draw's scalars, IDs as `<id>`.

    `recommend(target_type=artist, signals[3], signal_location="New York, United States",
    limit=20)`. Entity and tag IDs never appear (they are Qloo data); regions, query words
    and dates are Draw's own input.
    """
    parts: list[str] = []
    for key, value in request.items():
        if isinstance(value, list):
            parts.append(f"{key}[{len(value)}]")
        elif isinstance(value, bool):
            parts.append(f"{key}={str(value).lower()}")
        elif isinstance(value, int | float):
            parts.append(f"{key}={value}")
        elif isinstance(value, str):
            if _is_id(value):
                parts.append(f"{key}=<id>")
            elif key in _SCALARS:
                parts.append(f"{key}={value}")
            else:
                parts.append(f'{key}="{value}"')
        elif value is None:
            continue
        else:
            parts.append(f"{key}={{…}}")
    return f"{workflow}({', '.join(parts)})"


# --- building -------------------------------------------------------------------------


def _months_label(months: int | None) -> str:
    if months is None:
        return "Never booked at this venue (novelty 1.0)"
    if months == 0:
        return "Booked at this venue this month"
    return f"Booked at this venue {months} month{'s' if months != 1 else ''} ago"


def _affinity_of(value: float | None, call_id: str | None, entity_id: str) -> Affinity | None:
    if value is None or not call_id:
        return None
    return Affinity(entity=entity_id, value=min(1.0, max(0.0, value)), call_id=call_id)


def build_trail(
    candidate: Candidate,
    score: Score,
    *,
    profile: CrowdProfile,
    constraints: Constraints,
    region: str,
    signals: CandidateSignals | None,
    calls: Mapping[str, QlooCallRecord],
    signal_names: Mapping[str, str] | None = None,
) -> EvidenceTrail:
    """Assemble the trail behind `score`. Pure: reads its arguments, calls nothing.

    `calls` is the run store's record of every gateway call by `call_id`; `signal_names`
    maps a crowd signal's entity ID to its canonical act name for the input labels. A call
    the store never observed is left out, which `validate_trail` then reports.
    """
    names = signal_names or {}
    neutral = set(score.neutral or [])
    labels_by_sub: dict[SubScore, SourceLabel] = source_labels(neutral)
    labels: dict[str, SourceLabel] = {}

    inputs: list[TrailInput] = []
    for signal in profile.signals:
        ref = f"signal:{signal.entity_id}"
        who = names.get(signal.entity_id, "a past act")
        inputs.append(
            TrailInput(
                ref=ref, label=f"Crowd signal {who}, weight {signal.weight:.2f} (sell-through)"
            )
        )
        labels[f"{REF_INPUT}{ref}"] = "draw"
    inputs.append(TrailInput(ref="region", label=f"Venue region: {region or 'unknown'}"))
    labels[f"{REF_INPUT}region"] = "draw"
    inputs.append(
        TrailInput(ref="size_tier", label=f"Requested size tier: {constraints.size_tier}")
    )
    labels[f"{REF_INPUT}size_tier"] = "draw"
    inputs.append(
        TrailInput(
            ref="popularity", label=f"Qloo popularity {candidate.popularity:.3f} (recommend)"
        )
    )
    labels[f"{REF_INPUT}popularity"] = "qloo"
    months = signals.months_since_booked if signals else None
    inputs.append(TrailInput(ref="months_since_booked", label=_months_label(months)))
    labels[f"{REF_INPUT}months_since_booked"] = "draw"

    call_ids = list(dict.fromkeys(e.call_id for e in score.evidence))
    qloo_calls = [calls[c] for c in call_ids if c in calls]
    for record in qloo_calls:
        labels[f"{REF_CALL}{record.call_id}"] = "qloo"

    # One affinity per Qloo-backed sub-score that was measured, with the call it came from.
    recommend_call = next(
        (e.call_id for e in score.evidence if e.item_ref == "crowd_affinity"), None
    )
    measured: list[tuple[SubScore, float | None, str | None]] = [
        ("crowd_affinity", candidate.affinity, recommend_call)
    ]
    if signals is not None:
        for name in ("past_fit", "local_heat", "momentum"):
            value: SignalValue = getattr(signals, name)
            measured.append((name, value.value, value.call_id))
    affinities: list[Affinity] = []
    for name, value_, call_id in measured:
        if labels_by_sub[name] != "qloo":
            continue
        affinity = _affinity_of(value_, call_id, candidate.entity_id)
        if affinity is not None:
            affinities.append(affinity)
            labels[f"{REF_AFFINITY}{name}"] = "qloo"

    for name, label in labels_by_sub.items():
        if name == "momentum" and score.breakdown.momentum is None:
            continue
        labels[name] = label
        labels[f"{REF_WEIGHT}{name}"] = "draw"
    labels[REF_TOTAL] = "draw"

    return EvidenceTrail(
        inputs=inputs,
        qloo_calls=qloo_calls,
        affinities=affinities,
        sub_scores=score.breakdown.model_copy(),
        weights=score.weights.model_copy(),
        total=score.total,
        source_labels=labels,
    )


# --- validation -----------------------------------------------------------------------


def _close(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is b
    return math.isclose(a, b, abs_tol=TOLERANCE)


def _sub_names(score: Score) -> list[SubScore]:
    return [n for n in WEIGHTS if n != "momentum" or score.breakdown.momentum is not None]


def affinities_by_sub_score(trail: EvidenceTrail) -> dict[SubScore, Affinity]:
    """Affinities keyed by sub-score: the workflow of the call behind each one names it."""
    workflow_of_call = {c.call_id: c.workflow for c in trail.qloo_calls}
    sub_of_workflow: dict[str, SubScore] = {
        workflow: name for name, workflow in WORKFLOW_OF.items() if workflow
    }
    sub_of_workflow["rank"] = "crowd_affinity"  # `rank_given`: Qloo's affinity for a shortlist
    by_name: dict[SubScore, Affinity] = {}
    for affinity in trail.affinities:
        workflow = workflow_of_call.get(affinity.call_id)
        name = sub_of_workflow.get(workflow or "")
        if name is not None and name not in by_name:
            by_name[name] = affinity
    return by_name


def validate_trail(score: Score, trail: EvidenceTrail) -> list[str]:
    """The problems that make `trail` an incomplete account of `score`; `[]` when complete."""
    problems: list[str] = []
    labels = trail.source_labels
    valid: set[str] = {"qloo", "draw"}
    for key, label in labels.items():
        if label not in valid:
            problems.append(f"{key}: label {label!r} is neither qloo nor draw")

    expected: dict[str, float | None] = {REF_TOTAL: score.total}
    for name in _sub_names(score):
        expected[name] = getattr(score.breakdown, name)
        expected[f"{REF_WEIGHT}{name}"] = getattr(score.weights, name)
    found: dict[str, float | None] = {REF_TOTAL: trail.total}
    for name in _sub_names(score):
        found[name] = getattr(trail.sub_scores, name)
        found[f"{REF_WEIGHT}{name}"] = getattr(trail.weights, name)

    # Every number in the Score is in the trail, equal, and labelled.
    for key, value in expected.items():
        if key not in found or not _close(found[key], value):
            problems.append(f"{key}: score has {value}, trail has {found.get(key)}")
        if key not in labels:
            problems.append(f"{key}: no source label")

    # Every other trail item is labelled, and every label points at an item.
    items: set[str] = set(found)
    for item in trail.inputs:
        items.add(f"{REF_INPUT}{item.ref}")
    call_ids = {c.call_id for c in trail.qloo_calls}
    items.update(f"{REF_CALL}{c}" for c in call_ids)
    affinities = affinities_by_sub_score(trail)
    items.update(f"{REF_AFFINITY}{name}" for name in affinities)
    for key in items - set(labels):
        problems.append(f"{key}: no source label")
    for key in set(labels) - items:
        problems.append(f"{key}: label without an item")

    # Qloo-labelled sub-scores come from an affinity read out of a listed call; Draw-labelled
    # ones (neutral fallbacks, novelty, size fit) have none.
    neutral = set(score.neutral or [])
    for name in _sub_names(score):
        sub_label = labels.get(name)
        affinity = affinities.get(name)
        if name in neutral and sub_label == "qloo":
            problems.append(f"{name}: neutral fallback labelled qloo")
        if sub_label == "qloo":
            if affinity is None:
                problems.append(f"{name}: labelled qloo but no affinity from {WORKFLOW_OF[name]}")
            elif not _close(affinity.value, getattr(score.breakdown, name)):
                problems.append(
                    f"{name}: sub-score {getattr(score.breakdown, name)} differs from its "
                    f"affinity {affinity.value}"
                )
        elif affinity is not None:
            problems.append(f"{name}: has a Qloo affinity but is labelled {sub_label}")
    for affinity in trail.affinities:
        if affinity.call_id not in call_ids:
            problems.append(f"affinity for {affinity.entity}: call {affinity.call_id} not in trail")
        if labels.get(f"{REF_CALL}{affinity.call_id}") not in (None, "qloo"):
            problems.append(f"call {affinity.call_id}: a Qloo call labelled draw")
    for ref in {e.call_id for e in score.evidence} - call_ids:
        problems.append(f"call {ref}: in Score.evidence but not in the trail")

    # The total is Draw's arithmetic over the listed numbers.
    weighted = sum(
        (getattr(trail.weights, n) or 0.0) * (getattr(trail.sub_scores, n) or 0.0)
        for n in _sub_names(score)
    )
    if not _close(weighted, trail.total):
        problems.append(f"total {trail.total} is not the weighted sum {weighted}")
    if labels.get(REF_TOTAL) == "qloo":
        problems.append("total: Draw's arithmetic labelled qloo")
    return problems


def assert_complete(score: Score, trail: EvidenceTrail) -> None:
    """Raise `TrailError` unless `trail` accounts for every number in `score`."""
    problems = validate_trail(score, trail)
    if problems:
        raise TrailError(problems)
