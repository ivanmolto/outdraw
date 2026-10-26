"""scripts/export_openapi.py: the checked-in /shared/openapi.json matches the routes, offline."""

from __future__ import annotations

import json

from conftest import load_script

ROUTE_MODELS = {
    "VenueOut",
    "Resolutions",
    "ResolutionItem",
    "RunOut",
    "Evidence",
    "QlooCall",
    "Refinement",
    "ConstraintsDelta",
    "BriefOut",
    "BookingBrief",
    "CandidateOut",
    "InvalidInput",
}


def test_openapi_lists_every_route_model() -> None:
    doc = load_script("export_openapi").openapi()
    assert ROUTE_MODELS <= set(doc["components"]["schemas"])
    assert {"/venues", "/runs", "/limits", "/health"} <= set(doc["paths"])


def test_input_routes_document_400_not_the_default_422() -> None:
    doc = load_script("export_openapi").openapi()
    venues = doc["paths"]["/venues"]["post"]["responses"]
    assert venues["400"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/InvalidInput"
    }
    assert "422" not in venues
    # A route that really answers 422 keeps its own entry, without the dropped schema.
    confirm = doc["paths"]["/venues/{venue_id}/confirm"]["post"]["responses"]
    assert "422" in confirm and "content" not in confirm["422"]
    assert "HTTPValidationError" not in doc["components"]["schemas"]


def test_checked_in_document_is_current() -> None:
    script = load_script("export_openapi")
    assert json.loads(script.OUTPUT.read_text()) == script.openapi(), (
        "shared/openapi.json is stale: run `npm run gen:api` at the repo root"
    )
