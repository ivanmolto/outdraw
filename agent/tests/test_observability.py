"""T38: the run id in every log line and on every response, and Sentry events without PII."""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Iterator
from typing import Any

import pytest
import sentry_sdk
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport

from draw.api.deps import Deps
from draw.main import create_app
from draw.observability import (
    RUN_ID_HEADER,
    bind_run_id,
    configure_logging,
    init_sentry,
    redact_text,
    scrub_event,
)

KEY = "test-internal-key"
RUN_ID = "run_0123abcd"
VENUE = "The Synthetic Room"
MESSAGE = "more synthetic jazz on thursdays"


async def _no_deps() -> Deps | None:
    return None


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setenv("INTERNAL_API_KEY", KEY)
    monkeypatch.setattr("draw.main.probe_versions", lambda: None)
    return create_app(_no_deps)


@pytest.fixture
def logs() -> Iterator[io.StringIO]:
    stream = io.StringIO()
    configure_logging("INFO", stream)
    yield stream
    configure_logging()


class Capture(Transport):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[dict[str, Any]] = []

    def capture_envelope(self, envelope: Envelope) -> None:
        self.events.extend(
            i.payload.json for i in envelope.items if i.type == "event" and i.payload.json
        )


@pytest.fixture
def sentry(monkeypatch: pytest.MonkeyPatch) -> Iterator[Capture]:
    monkeypatch.setenv("SENTRY_DSN", "https://public@o0.ingest.sentry.io/0")
    capture = Capture()
    assert init_sentry(transport=capture)
    yield capture
    sentry_sdk.get_client().close()
    # Before monkeypatch restores the env: init() would read the fake DSN and send for real.
    monkeypatch.delenv("SENTRY_DSN")
    sentry_sdk.init()


# --- run id -----------------------------------------------------------------------------


def test_run_id_header_reaches_logs_and_response(app: FastAPI, logs: io.StringIO) -> None:
    with TestClient(app) as client:
        res = client.post(
            "/ops/sentry-test", headers={"X-Draw-Internal-Key": KEY, RUN_ID_HEADER: RUN_ID}
        )

    assert res.status_code == 200
    assert res.headers[RUN_ID_HEADER] == RUN_ID
    assert res.json()["run_id"] == RUN_ID
    lines = [line for line in logs.getvalue().splitlines() if "sentry_test" in line]
    assert lines and all(f"run_id={RUN_ID}" in line for line in lines)


def test_run_id_from_the_path_without_a_header(app: FastAPI) -> None:
    with TestClient(app) as client:
        res = client.get(f"/runs/{RUN_ID}/calls", headers={"X-Draw-Internal-Key": KEY})

    assert res.headers[RUN_ID_HEADER] == RUN_ID


@pytest.mark.parametrize("header", [None, "not an id", "run_x'; drop table runs"])
def test_request_id_when_no_valid_run_id(app: FastAPI, header: str | None) -> None:
    headers = {"X-Draw-Internal-Key": KEY} | ({RUN_ID_HEADER: header} if header else {})
    with TestClient(app) as client:
        res = client.get("/venues/v_1", headers=headers)

    assert res.headers[RUN_ID_HEADER].startswith("req_")


def test_log_lines_outside_a_request_say_dash(logs: io.StringIO) -> None:
    logging.getLogger("draw.test").info("outside")
    with bind_run_id(RUN_ID):
        logging.getLogger("draw.test").info("inside")

    outside, inside = logs.getvalue().splitlines()
    assert "run_id=- outside" in outside
    assert f"run_id={RUN_ID} inside" in inside


# --- Sentry -----------------------------------------------------------------------------


def test_sentry_off_without_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    assert init_sentry() is False


def test_sentry_test_event_carries_the_run_id(app: FastAPI, sentry: Capture) -> None:
    with TestClient(app) as client:
        res = client.post(
            "/ops/sentry-test", headers={"X-Draw-Internal-Key": KEY, RUN_ID_HEADER: RUN_ID}
        )
    sentry_sdk.flush()

    assert res.json()["event_id"]
    (event,) = sentry.events
    assert event["tags"]["run_id"] == RUN_ID
    assert KEY not in json.dumps(event)


def test_sentry_event_has_no_venue_name_or_message(app: FastAPI, sentry: Capture) -> None:
    @app.post("/boom")
    async def boom(body: dict[str, Any]) -> None:
        logging.getLogger("draw.test").warning("refining %r for %r", body["message"], body["name"])
        raise RuntimeError(f"no act fits venue {body['name']!r}: {body['message']!r}")

    with TestClient(app, raise_server_exceptions=False) as client:
        res = client.post(
            "/boom?venue=" + VENUE.replace(" ", "+"),
            json={"name": VENUE, "message": MESSAGE},
            headers={"X-Draw-Internal-Key": KEY, RUN_ID_HEADER: RUN_ID},
        )
    sentry_sdk.flush()

    assert res.status_code == 500
    (event,) = [e for e in sentry.events if "exception" in e]
    dumped = json.dumps(event)
    for secret in (VENUE, MESSAGE, KEY, "Synthetic"):
        assert secret not in dumped
    assert event["tags"]["run_id"] == RUN_ID
    request = event["request"]
    assert set(request) <= {"method", "url", "headers"}
    assert {k.lower() for k in request["headers"]} <= {RUN_ID_HEADER.lower(), "content-type"}
    assert "user" not in event


def test_scrub_event_on_a_synthetic_event() -> None:
    event: dict[str, Any] = {
        "message": f"venue {VENUE!r}",
        "logentry": {
            "message": "refine %r",
            "params": [MESSAGE],
            "formatted": f"refine {MESSAGE!r}",
        },
        "user": {"ip_address": "203.0.113.1"},
        "extra": {"venue": {"name": VENUE}, "steps": 3},
        "contexts": {"runtime": {"name": "CPython"}, "run": {"message": MESSAGE}},
        "breadcrumbs": {
            "values": [{"category": "log", "message": f"name={VENUE!r}", "data": {"name": VENUE}}]
        },
        "exception": {
            "values": [
                {"value": f"bad {MESSAGE!r}", "stacktrace": {"frames": [{"vars": {"name": VENUE}}]}}
            ]
        },
    }
    with bind_run_id(RUN_ID):
        out = scrub_event(event)

    dumped = json.dumps(out)
    assert VENUE not in dumped and MESSAGE not in dumped
    assert out["contexts"]["runtime"]["name"] == "CPython"
    assert out["extra"]["steps"] == 3
    assert out["tags"] == {"run_id": RUN_ID}


def test_redact_text_keeps_the_shape() -> None:
    assert redact_text(f"Unknown venue {VENUE!r}.") == "Unknown venue '[redacted]'."
    assert redact_text("no quotes, kept") == "no quotes, kept"
