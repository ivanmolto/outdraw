"""Generated Pydantic models: valid samples parse, invalid samples fail."""

from __future__ import annotations

import json
from typing import Any

import pytest
from conftest import envelope_files
from pydantic import BaseModel, ValidationError

from draw import schemas

EVIDENCE = {"item_ref": "genres[0]", "call_id": "00000000-0000-0000-0000-000000000001"}
BREAKDOWN = {
    "crowd_affinity": 0.8,
    "past_fit": 0.6,
    "local_heat": 0.5,
    "novelty": 0.4,
    "size_fit": 0.9,
}
WEIGHTS = {
    "crowd_affinity": 0.35,
    "past_fit": 0.2,
    "local_heat": 0.2,
    "novelty": 0.1,
    "size_fit": 0.15,
}

VALID: dict[type[BaseModel], dict[str, Any]] = {
    schemas.VenueInput: {
        "name": "Synthetic Room",
        "city": "New York",
        "country": "US",
        "lat": 40.73,
        "lng": -73.99,
        "capacity": 300,
        "past_shows": [
            {"act": "Synthetic Act", "date": "2026-05-01", "tickets_sold": 280, "capacity": 300},
            {"act": "Other Act", "date": "2026-06-12"},
        ],
    },
    schemas.QlooEnvelope: {
        "status": "error",
        "summary": "Qloo rate limit reached.",
        "results": [],
        "result_count": 0,
        "error": {"code": "QLOO_RATE_LIMIT", "retryable": True, "recovery": "Retry later."},
        "operation": "find_tags",
    },
    schemas.ResolvedEntity: {
        "query": "synthetic act",
        "entity_id": "urn:entity:synthetic:1",
        "canonical_name": "Synthetic Act",
        "confidence": 0.92,
        "candidates": [
            {"entity_id": "urn:entity:synthetic:1", "name": "Synthetic Act", "type": "artist"}
        ],
        "confirmed": True,
    },
    schemas.CrowdProfile: {
        "venue_id": "venue-1",
        "signals": [{"entity_id": "urn:entity:synthetic:1", "weight": 0.93}],
        "genres": [
            {"entity_id": "urn:tag:synthetic:genre:1", "name": "Synthetic", "affinity": 0.7}
        ],
        "related_artists": [],
        "places": [],
        "films": [],
        "brands": [],
        "demographics": {"age": {"25_to_29": 0.3}},
        "evidence": [EVIDENCE],
        "data_quality": "partial",
    },
    schemas.Constraints: {
        "include_tags": ["urn:tag:synthetic:genre:1"],
        "exclude_tags": [],
        "size_tier": "mid",
        "exclude_recent_months": 6,
        "max_candidates": 10,
    },
    schemas.Candidate: {
        "entity_id": "urn:entity:synthetic:2",
        "name": "Synthetic Candidate",
        "popularity": 0.6,
        "affinity": 0.8,
        "tags": ["urn:tag:synthetic:genre:1"],
        "evidence": [EVIDENCE],
    },
    schemas.Score: {
        "total": 0.71,
        "breakdown": {**BREAKDOWN, "momentum": 0.3},
        "weights": WEIGHTS,
        "top_reason": "Strong overlap with the room's crowd.",
    },
    schemas.EvidenceTrail: {
        "inputs": [{"ref": "venue", "label": "Synthetic Room, 300 cap"}],
        "qloo_calls": [
            {
                "call_id": EVIDENCE["call_id"],
                "workflow": "recommend",
                "params_summary": "artists like 1 signal",
                "status": "ok",
                "cache_hit": False,
            }
        ],
        "affinities": [{"entity": "urn:entity:synthetic:2", "value": 0.8, "call_id": "c1"}],
        "sub_scores": BREAKDOWN,
        "weights": WEIGHTS,
        "total": 0.71,
        "source_labels": {"affinities[0]": "qloo", "sub_scores": "draw"},
    },
    schemas.BookingBrief: {
        "why_it_fits": [{"text": "Crowd overlap is high.", "evidence_ref": "affinities[0]"}],
        "night_format": "Two-band bill, doors 8pm.",
        "partners": ["Synthetic Records"],
        "outreach_email": {"subject": "Booking enquiry", "body": "Hi, ..."},
    },
}

# (model, field path, bad value) — each breaks exactly one rule in the schema.
INVALID: list[tuple[type[BaseModel], str, Any]] = [
    (schemas.VenueInput, "capacity", 0),
    (schemas.VenueInput, "past_shows.0.date", "May 1st"),
    (schemas.VenueInput, "unknown_field", "x"),
    (schemas.QlooEnvelope, "status", "unknown"),
    (schemas.QlooEnvelope, "error.retryable", None),
    (schemas.ResolvedEntity, "confidence", 1.5),
    (schemas.CrowdProfile, "data_quality", "error"),
    (schemas.Constraints, "size_tier", "huge"),
    (schemas.Constraints, "max_candidates", 0),
    (schemas.Candidate, "popularity", -0.1),
    (schemas.Score, "breakdown.novelty", 2),
    (schemas.EvidenceTrail, "source_labels.affinities[0]", "llm"),
    (schemas.EvidenceTrail, "qloo_calls.0.workflow", "search"),
    (schemas.BookingBrief, "why_it_fits", []),
]


def _with(sample: dict[str, Any], path: str, value: Any) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(json.dumps(sample))
    *parents, leaf = path.split(".")
    node: Any = data
    for key in parents:
        node = node[int(key)] if isinstance(node, list) else node[key]
    if isinstance(node, list):
        node[int(leaf)] = value
    else:
        node[leaf] = value
    return data


@pytest.mark.parametrize("model", VALID, ids=lambda m: m.__name__)
def test_valid_sample_round_trips(model: type[BaseModel]) -> None:
    parsed = model.model_validate(VALID[model])
    assert model.model_validate(parsed.model_dump(mode="json", exclude_none=True)) == parsed


REQUIRED = [
    (model, name)
    for model in VALID
    for name, field in model.model_fields.items()
    if field.is_required()
]


@pytest.mark.parametrize(
    ("model", "field"), REQUIRED, ids=[f"{m.__name__}.{f}" for m, f in REQUIRED]
)
def test_missing_required_field_fails(model: type[BaseModel], field: str) -> None:
    sample = {k: v for k, v in VALID[model].items() if k != field}
    with pytest.raises(ValidationError):
        model.model_validate(sample)


@pytest.mark.parametrize(
    ("model", "path", "value"), INVALID, ids=[f"{m.__name__}.{p}" for m, p, _ in INVALID]
)
def test_invalid_sample_fails(model: type[BaseModel], path: str, value: Any) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(_with(VALID[model], path, value))


def test_every_synthetic_fixture_is_a_valid_envelope() -> None:
    paths = envelope_files()
    assert paths
    for path in paths:
        schemas.QlooEnvelope.model_validate_json(path.read_text())
