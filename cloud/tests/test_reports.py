"""OP-308: community reports become obstructions; the photo sets confidence and is dropped."""

from __future__ import annotations

import base64
import builtins
import io
import logging
import math
import os
import pathlib
import tempfile
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from cloud.app import obstructions_api, reports, reports_api
from cloud.app.main import create_app
from cloud.app.matching import HazardProvider
from cloud.app.network import RawEdge, RawNode, WalkNetwork, export_network
from cloud.app.profiles import load_routing_config
from cloud.app.reports import (
    CLASS_TO_TYPE,
    KINDS,
    PHOTO_CLASSES,
    ReportCounter,
    ReportFields,
    ReportsConfig,
    build_photo_checker,
    check_photo,
)
from cloud.app.reports_api import parse_multipart
from cloud.app.routing import Router
from cloud.app.schemas import Confidence, Obstruction
from cloud.app.store import ObstructionStore
from cloud.tests.conftest import NOW

LAT, LNG = 53.5444, -113.4909  # downtown, near Churchill Square
CLIENT = "c" * 32
# A JPEG header, then bytes that look like multipart framing, NULs and a marker, so
# a test can tell whether any copy of the photo survived anywhere.
MARKER = b"OP308-PHOTO-MARKER"
JPEG = (
    b"\xff\xd8\xff\xe0" + b"\r\n--x\r\n\x00\x01" + MARKER + b"\xff" * 64 + b"\xff\xd9"
)
PNG = b"\x89PNG\r\n\x1a\n" + MARKER + b"\x00" * 32
CONFIG = reports.load_config()
MODEL_ENV = ("MODEL_FILE", "MODEL_URL", "MODEL_LABELS", "MODEL_SHA256")


class FakeChecker:
    """A PhotoChecker that answers a fixed label and remembers only photo sizes."""

    name = "fake"

    def __init__(self, label: str = "ice", error: Exception | None = None) -> None:
        self.label = label
        self.error = error
        self.sizes: list[int] = []

    def classify(self, photo: bytes) -> str:
        self.sizes.append(len(photo))
        if self.error is not None:
            raise self.error
        return self.label


def report(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"client_id": CLIENT, "type": "ice", "lat": LAT, "lng": LNG}
    body.update(overrides)
    return {key: value for key, value in body.items() if value is not None}


def form(**overrides: Any) -> dict[str, str]:
    return {key: str(value) for key, value in report(**overrides).items()}


def make_app(
    store: ObstructionStore,
    checker: Any = None,
    config: ReportsConfig = CONFIG,
    now: datetime = NOW,
) -> FastAPI:
    """Only the obstructions and reports routers, on the test store, at a frozen time."""
    app = FastAPI()
    app.state.store = store
    app.state.photo_checker = checker
    app.include_router(obstructions_api.router)
    app.include_router(reports_api.router)
    app.dependency_overrides[obstructions_api.get_now] = lambda: now
    app.dependency_overrides[reports_api.get_reports_config] = lambda: config
    return app


@pytest.fixture
def client(store: ObstructionStore) -> TestClient:
    return TestClient(make_app(store))


@pytest.fixture(autouse=True)
def no_photo_check_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (*MODEL_ENV, "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)


# ------------------------------------------------------------------- the config


def test_config_file_sets_the_story_expiry_and_limits() -> None:
    assert dict(CONFIG.expiry_hours) == {"ice": 2, "snow": 12, "blocked": 6}
    assert CONFIG.per_client_per_hour == 5
    assert CONFIG.global_per_hour == 200


@pytest.mark.parametrize(
    "raw",
    [
        '{"expiry_hours": {"ice": 2, "snow": 12}, "rate_limit": {"per_client_per_hour": 5, "global_per_hour": 9}}',
        '{"expiry_hours": {"ice": 0, "snow": 12, "blocked": 6}, "rate_limit": {"per_client_per_hour": 5, "global_per_hour": 9}}',
        '{"expiry_hours": {"ice": 2, "snow": 12, "blocked": 6}, "rate_limit": {"per_client_per_hour": 5, "global_per_hour": 4}}',
    ],
)
def test_a_bad_config_is_refused(tmp_path: pathlib.Path, raw: str) -> None:
    path = tmp_path / "reports.json"
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(ValueError):
        reports.load_config(path)


def test_photo_classes_match_the_model_labels() -> None:
    from vision.tools.label import LABELS

    assert PHOTO_CLASSES == LABELS
    assert set(CLASS_TO_TYPE) <= set(LABELS)
    for report_type, kind in KINDS.items():
        assert {c for c, t in CLASS_TO_TYPE.items() if t == report_type} == set(
            kind.agreeing_classes
        )


# ------------------------------------------------------------------ validation


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"type": "flood"}, "type"),
        ({"type": None}, "type"),
        ({"client_id": "short"}, "client_id"),
        ({"client_id": "x" * 65}, "client_id"),
        ({"client_id": "has spaces in it ok"}, "client_id"),
        ({"client_id": "semi;colon;is;not;ok"}, "client_id"),
        ({"note": "n" * 121}, "note"),
        ({"note": "bell\x07"}, "note"),
        ({"lat": "north"}, "lat"),
        ({"lat": 91}, "lat"),
        ({"lng": None}, "lng"),
        ({"extra": "field"}, "extra"),
    ],
)
def test_invalid_fields_are_422(client: TestClient, overrides, field: str) -> None:
    response = client.post("/api/reports", json=report(**overrides))
    assert response.status_code == 422
    assert any(field in error["loc"] for error in response.json()["detail"])


@pytest.mark.parametrize(
    ("lat", "lng"),
    [(53.5444, -112.0), (51.0447, -114.0719), (10.0, LNG), (LAT, 0.0)],
)
def test_a_location_outside_edmonton_is_422(client: TestClient, lat, lng) -> None:
    response = client.post("/api/reports", json=report(lat=lat, lng=lng))
    assert response.status_code == 422
    assert "inside Edmonton" in response.text


def test_validation_errors_never_echo_the_body(client: TestClient) -> None:
    photo = base64.b64encode(JPEG).decode()
    response = client.post("/api/reports", json=report(lat=10.0, photo=photo))
    assert response.status_code == 422
    assert photo not in response.text and CLIENT not in response.text


def test_the_note_is_trimmed_and_an_empty_note_is_none() -> None:
    fields = ReportFields.model_validate(report(note="  Glare ice\n at the  ramp "))
    assert fields.note == "Glare ice at the ramp"
    assert ReportFields.model_validate(report(note="   ")).note is None
    assert len(ReportFields.model_validate(report(note="é" * 120)).note or "") == 120


@pytest.mark.parametrize(
    ("content_type", "body", "status"),
    [
        ("text/plain", b"ice", 415),
        ("application/x-www-form-urlencoded", b"type=ice", 415),
        ("application/json", b"{not json", 422),
        ("application/json", b"[1, 2]", 422),
        ("multipart/form-data", b"--x--", 422),  # no boundary parameter
    ],
)
def test_bodies_that_are_not_reports(client, content_type, body, status) -> None:
    response = client.post(
        "/api/reports", content=body, headers={"Content-Type": content_type}
    )
    assert response.status_code == status


def test_form_text_fields_must_be_short_text_and_sent_once(client: TestClient) -> None:
    as_file = client.post(
        "/api/reports",
        data=form(),
        files={"note": ("note.txt", b"text", "text/plain")},
    )
    assert as_file.status_code == 422
    twice = (
        b"--b\r\nContent-Disposition: form-data; name=type\r\n\r\nice\r\n"
        b"--b\r\nContent-Disposition: form-data; name=type\r\n\r\nsnow\r\n--b--\r\n"
    )
    response = client.post(
        "/api/reports",
        content=twice,
        headers={"Content-Type": "multipart/form-data; boundary=b"},
    )
    assert response.status_code == 422 and "sent twice" in response.text


# ---------------------------------------------------------- the stored report


def test_a_report_becomes_a_contract_obstruction(client, store) -> None:
    response = client.post("/api/reports", json=report(note="Ice at the curb ramp"))
    assert response.status_code == 201
    body = response.json()
    assert body["id"].startswith("community_report:")
    assert body["kind"] == "ice_risk" and body["effect"] == "ice_risk"
    assert body["confidence"] == "low"
    assert body["title"] == "Reported: icy sidewalk"
    assert body["observed_at"] == "2026-11-07T20:10:00Z"  # the app clock
    assert body["message"].startswith(
        "Thanks. Your report is live for the next 2 hours."
    )

    item = store.get(body["id"])
    assert item is not None
    # The stored row rebuilds into the contract model, every validator included.
    assert Obstruction.model_validate(item.model_dump()) == item
    assert item.source == "community_report" and item.category == "sidewalk"
    assert item.geometry.type == "Point" and item.geometry.coordinates == [LNG, LAT]
    assert item.detail == "Ice at the curb ramp"
    assert item.url is None and item.attribution == "Community report"
    assert item.starts_at is None and item.ends_at is None

    features = client.get("/api/obstructions").json()["features"]
    assert [f["id"] for f in features] == [body["id"]]
    assert features[0]["properties"]["kind"] == "ice_risk"


@pytest.mark.parametrize(
    ("report_type", "kind", "effect", "title"),
    [
        ("snow", "snow", "snow", "Reported: snow on the sidewalk"),
        ("ice", "ice_risk", "ice_risk", "Reported: icy sidewalk"),
        ("blocked", "blocked", "blocked", "Reported: sidewalk blocked"),
    ],
)
def test_each_type_maps_to_its_kind_and_expires_from_config(
    store, report_type, kind, effect, title
) -> None:
    config = ReportsConfig({"ice": 3, "snow": 7, "blocked": 1.5}, 5, 200)
    client = TestClient(make_app(store, config=config))
    body = client.post("/api/reports", json=report(type=report_type)).json()
    hours = config.expiry_hours[report_type]
    assert (body["kind"], body["effect"], body["title"]) == (kind, effect, title)
    assert body["expires_in_hours"] == hours
    item = store.get(body["id"])
    assert item is not None
    assert item.expires_at - item.observed_at == timedelta(hours=hours)
    assert store.active(NOW + timedelta(hours=hours) - timedelta(seconds=1))
    assert not store.active(NOW + timedelta(hours=hours))
    assert store.purge_expired(NOW + timedelta(hours=hours)) == [body["id"]]


def test_default_expiry_is_ice_2_snow_12_blocked_6(client, store) -> None:
    for report_type, hours in (("ice", 2), ("snow", 12), ("blocked", 6)):
        body = client.post("/api/reports", json=report(type=report_type)).json()
        item = store.get(body["id"])
        assert item is not None
        assert item.expires_at - item.observed_at == timedelta(hours=hours)


def test_every_report_is_its_own_item(client, store) -> None:
    first = client.post("/api/reports", json=report()).json()["id"]
    second = client.post("/api/reports", json=report()).json()["id"]
    assert first != second and len(store.active(NOW)) == 2


# ------------------------------------------------------------------ rate limit


def test_five_reports_per_client_per_hour(client: TestClient) -> None:
    for _ in range(5):
        assert client.post("/api/reports", json=report()).status_code == 201
    limited = client.post("/api/reports", json=report())
    assert limited.status_code == 429
    assert "5 reports in the last hour" in limited.json()["detail"]
    assert 1 <= int(limited.headers["Retry-After"]) <= 3601
    other = report(client_id="d" * 32)
    assert client.post("/api/reports", json=other).status_code == 201


def test_a_global_cap_protects_against_many_clients(store) -> None:
    config = ReportsConfig({"ice": 2, "snow": 12, "blocked": 6}, 2, 3)
    client = TestClient(make_app(store, config=config))
    for client_id in ("a" * 16, "a" * 16, "b" * 16):
        assert (
            client.post("/api/reports", json=report(client_id=client_id)).status_code
            == 201
        )
    limited = client.post("/api/reports", json=report(client_id="e" * 16))
    assert limited.status_code == 429
    assert "too many reports right now" in limited.json()["detail"]
    assert len(store.active(NOW)) == 3


def test_the_limit_window_slides_with_the_real_clock(store) -> None:
    app = make_app(store)
    moment = [datetime(2026, 11, 8, 12, 0, tzinfo=UTC)]
    counter = ReportCounter(store.engine, clock=lambda: moment[0])
    counter.create_tables()
    app.state.report_counter = counter
    client = TestClient(app)
    for _ in range(5):
        assert client.post("/api/reports", json=report()).status_code == 201
    moment[0] += timedelta(minutes=30)
    limited = client.post("/api/reports", json=report())
    assert limited.status_code == 429
    assert 1700 <= int(limited.headers["Retry-After"]) <= 1801
    moment[0] += timedelta(minutes=31)
    assert client.post("/api/reports", json=report()).status_code == 201
    assert counter.count(moment[0] - timedelta(hours=2), CLIENT) == 6


def test_a_refused_report_runs_no_photo_check_and_stores_nothing(store) -> None:
    checker = FakeChecker("ice")
    config = ReportsConfig({"ice": 2, "snow": 12, "blocked": 6}, 1, 200)
    client = TestClient(make_app(store, checker, config=config))
    photo = {"photo": ("p.jpg", JPEG, "image/jpeg")}
    assert client.post("/api/reports", data=form(), files=photo).status_code == 201
    assert client.post("/api/reports", data=form(), files=photo).status_code == 429
    assert checker.sizes == [len(JPEG)] and len(store.active(NOW)) == 1


def test_the_limit_survives_a_restart(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'openpath.db'}")
    monkeypatch.setenv("SCHEDULER", "off")
    monkeypatch.delenv("DATA_MODE", raising=False)
    with TestClient(create_app()) as first:
        for _ in range(5):
            assert first.post("/api/reports", json=report()).status_code == 201
    with TestClient(create_app()) as second:
        assert second.post("/api/reports", json=report()).status_code == 429
        assert len(second.get("/api/obstructions").json()["features"]) == 5


# ---------------------------------------------------------------- the photo


@pytest.fixture
def disk_writes(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Every attempt to write a file, temporary file or spool while the test runs."""
    writes: list[str] = []
    real_open, real_os_open = builtins.open, os.open

    def spy_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if any(flag in mode for flag in "wax+"):
            writes.append(f"open({file!r}, {mode!r})")
        return real_open(file, mode, *args, **kwargs)

    def spy_os_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND):
            writes.append(f"os.open({path!r})")
        return real_os_open(path, flags, *args, **kwargs)

    def refuse(name: str) -> Callable[..., Any]:
        def call(*args: Any, **kwargs: Any) -> Any:
            writes.append(name)
            raise AssertionError(f"{name} must not be used for a report")

        return call

    monkeypatch.setattr(builtins, "open", spy_open)
    monkeypatch.setattr(io, "open", spy_open)
    monkeypatch.setattr(os, "open", spy_os_open)
    for name in ("write_bytes", "write_text", "touch"):
        monkeypatch.setattr(pathlib.Path, name, refuse(f"Path.{name}"))
    for name in (
        "SpooledTemporaryFile",
        "NamedTemporaryFile",
        "TemporaryFile",
        "mkstemp",
    ):
        monkeypatch.setattr(tempfile, name, refuse(f"tempfile.{name}"))
    yield writes


def test_the_disk_spy_sees_writes(disk_writes, tmp_path) -> None:
    """A control for the test below: the spy is not blind to real writes."""
    with open(tmp_path / "x.bin", "wb") as file:
        file.write(b"x")
    with pytest.raises(AssertionError):
        tempfile.SpooledTemporaryFile()  # noqa: SIM115 - the spy raises first
    assert disk_writes[0].endswith("'wb')") and disk_writes[1] == (
        "tempfile.SpooledTemporaryFile"
    )


def database_values(store: ObstructionStore) -> list[tuple[str, str, Any]]:
    """(table, column, value) for every cell in the database."""
    cells = []
    with store.engine.connect() as connection:
        for table in inspect(store.engine).get_table_names():
            result = connection.execute(text(f'SELECT * FROM "{table}"'))
            for row in result.mappings():
                cells.extend((table, column, value) for column, value in row.items())
    return cells


@pytest.mark.parametrize("as_json", [False, True])
def test_the_photo_is_classified_in_memory_and_never_kept(
    store, disk_writes, caplog, as_json: bool
) -> None:
    checker = FakeChecker("ice")
    client = TestClient(make_app(store, checker))
    encoded = base64.b64encode(JPEG).decode()
    note = "Black glare by the bench"
    caplog.set_level(logging.INFO, logger="openpath.reports")
    if as_json:
        response = client.post("/api/reports", json=report(note=note, photo=encoded))
    else:
        response = client.post(
            "/api/reports",
            data=form(note=note),
            files={"photo": ("IMG_0001.jpg", JPEG, "image/jpeg")},
        )
    assert response.status_code == 201, response.text
    assert checker.sizes == [len(JPEG)]  # the checker saw the exact bytes
    assert disk_writes == []  # no file, temporary file or spool was written

    body = response.json()
    assert body["confidence"] == "medium"
    assert body["photo"]["received"] and body["photo"]["checked"]
    assert "discarded" in body["photo"]["note"]
    assert "photo" not in store.get(body["id"]).model_dump()  # type: ignore[union-attr]

    cells = database_values(store)
    assert cells and not any("photo" in column for _, column, _ in cells)
    for _, _, value in cells:
        assert not isinstance(value, bytes | memoryview)
        assert MARKER.decode() not in str(value) and encoded[:40] not in str(value)

    # The log has one line with the size and class, never the photo, note or client.
    (line,) = [
        r.getMessage() for r in caplog.records if r.getMessage().startswith("REPORT")
    ]
    assert f"photo={len(JPEG)} bytes class=ice" in line
    assert encoded[:40] not in caplog.text and MARKER.decode() not in caplog.text
    assert note not in caplog.text and CLIENT not in caplog.text


def test_a_png_and_a_data_url_are_accepted(store) -> None:
    client = TestClient(make_app(store, FakeChecker("snow_covered")))
    data_url = "data:image/png;base64," + base64.b64encode(PNG).decode()
    body = client.post("/api/reports", json=report(type="snow", photo=data_url)).json()
    assert body["confidence"] == "medium" and body["photo"]["label"] == "snow_covered"


@pytest.mark.parametrize(
    ("photo", "status"),
    [
        (b"GIF89a" + MARKER, 415),
        (b"\xff\xd8\xff" + b"\x00" * (5 * 1024 * 1024), 413),
    ],
)
def test_photos_that_are_not_small_jpeg_or_png_are_refused(
    store, photo, status
) -> None:
    checker = FakeChecker("ice")
    client = TestClient(make_app(store, checker))
    response = client.post(
        "/api/reports", data=form(), files={"photo": ("p", photo, "image/jpeg")}
    )
    assert response.status_code == status
    assert checker.sizes == [] and store.active(NOW) == []


def test_an_oversized_body_is_refused_before_parsing(client: TestClient) -> None:
    response = client.post(
        "/api/reports",
        content=b"x" * (reports_api.MAX_BODY_BYTES + 1),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413


def test_invalid_base64_is_422(client: TestClient) -> None:
    response = client.post("/api/reports", json=report(photo="not base64!"))
    assert response.status_code == 422


def test_an_empty_file_input_counts_as_no_photo(store) -> None:
    checker = FakeChecker("ice")
    client = TestClient(make_app(store, checker))
    response = client.post(
        "/api/reports",
        data=form(),
        files={"photo": ("", b"", "application/octet-stream")},
    )
    assert response.status_code == 201
    assert response.json()["photo"]["outcome"] == "none" and checker.sizes == []


# ----------------------------------------------------------- confidence rules


@pytest.mark.parametrize(
    ("report_type", "label", "confidence", "outcome"),
    [
        ("ice", "ice", "medium", "agrees"),
        ("snow", "snow_partial", "medium", "agrees"),
        ("snow", "snow_covered", "medium", "agrees"),
        ("ice", "wet", "low", "disagrees"),
        ("ice", "dry", "low", "disagrees"),
        ("snow", "ice", "low", "disagrees"),
        ("ice", "snow_covered", "low", "disagrees"),
        ("ice", "unusable", "low", "unusable"),
        ("ice", "unknown", "low", "unusable"),
        ("ice", "not-a-class", "low", "unusable"),
        ("blocked", "dry", "low", "unverifiable"),
        ("blocked", "snow_covered", "low", "unverifiable"),
    ],
)
def test_confidence_from_the_photo_check(
    report_type, label, confidence, outcome
) -> None:
    level, result = check_photo(JPEG, report_type, FakeChecker(label))
    assert (level.value, result.outcome) == (confidence, outcome)
    assert result.received and "discarded" in result.note


def test_no_photo_or_no_checker_is_low() -> None:
    level, result = check_photo(None, "ice", FakeChecker("ice"))
    assert level == Confidence.LOW and result.outcome == "none" and not result.received
    level, result = check_photo(JPEG, "ice", None)
    assert level == Confidence.LOW and result.outcome == "unavailable"
    assert result.received and not result.checked


def test_a_disagreement_keeps_the_type_and_says_so(store) -> None:
    client = TestClient(make_app(store, FakeChecker("wet")))
    body = client.post(
        "/api/reports", data=form(), files={"photo": ("p.jpg", JPEG, "image/jpeg")}
    ).json()
    assert body["kind"] == "ice_risk" and body["confidence"] == "low"
    assert body["photo"]["outcome"] == "disagrees" and body["photo"]["label"] == "wet"
    assert "shows a wet surface, not an icy sidewalk" in body["message"]
    assert "kept as an icy sidewalk with low confidence" in body["message"]


def test_a_failing_checker_still_accepts_the_report(store, caplog) -> None:
    checker = FakeChecker(error=RuntimeError("model crashed"))
    client = TestClient(make_app(store, checker))
    response = client.post(
        "/api/reports", data=form(), files={"photo": ("p.jpg", JPEG, "image/jpeg")}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["confidence"] == "low" and body["photo"]["outcome"] == "unavailable"
    assert len(store.active(NOW)) == 1


# --------------------------------------------------------------- photo checkers


def test_no_environment_means_no_checker() -> None:
    assert build_photo_checker({}) is None


def test_a_model_without_labels_falls_back_without_raising(monkeypatch) -> None:
    claude = FakeChecker("ice")
    monkeypatch.setattr(reports, "_claude_checker", lambda env: claude)
    assert build_photo_checker({"MODEL_URL": "https://example.invalid/m.onnx"}) is None
    env = {"MODEL_URL": "https://example.invalid/m.onnx", "ANTHROPIC_API_KEY": "k"}
    assert build_photo_checker(env) is claude


def test_the_model_is_preferred_over_claude(monkeypatch) -> None:
    model, claude = FakeChecker("ice"), FakeChecker("dry")
    monkeypatch.setattr(reports, "_model_checker", lambda env, f, u: model)
    monkeypatch.setattr(reports, "_claude_checker", lambda env: claude)
    env = {"MODEL_FILE": "m.onnx", "ANTHROPIC_API_KEY": "k"}
    assert build_photo_checker(env) is model


def test_a_claude_checker_that_cannot_load_means_no_checker(monkeypatch) -> None:
    def missing(env: Any) -> Any:
        raise ImportError("anthropic is not installed")

    monkeypatch.setattr(reports, "_claude_checker", missing)
    assert build_photo_checker({"ANTHROPIC_API_KEY": "k"}) is None


def test_the_adapters_wrap_the_vision_classifier_and_claude() -> None:
    class Classifier:
        version = "f" * 64

        def predict(self, frame: bytes) -> Any:
            return type("P", (), {"label": "snow_partial"})()

    class Check:
        model = "claude-haiku-5-5"

        def __init__(self, answer: Any) -> None:
            self.answer = answer

        def label(self, frame: bytes) -> Any:
            return self.answer

    model = reports.ModelPhotoChecker(Classifier())
    assert model.classify(JPEG) == "snow_partial" and model.name == "onnx:ffffffffffff"
    answered = reports.ClaudePhotoChecker(Check(type("A", (), {"label": "ice"})()))
    assert (
        answered.classify(JPEG) == "ice" and answered.name == "claude:claude-haiku-5-5"
    )
    assert reports.ClaudePhotoChecker(Check(None)).classify(JPEG) == "unknown"


def test_the_real_app_starts_without_a_checker(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("SCHEDULER", "off")
    monkeypatch.delenv("DATA_MODE", raising=False)
    app = create_app()
    with TestClient(app) as client:
        assert app.state.photo_checker is None
        body = client.post(
            "/api/reports", data=form(), files={"photo": ("p.jpg", JPEG, "image/jpeg")}
        ).json()
    assert body["confidence"] == "low" and body["photo"]["outcome"] == "unavailable"


def test_the_endpoint_documents_both_body_types() -> None:
    operation = create_app().openapi()["paths"]["/api/reports"]["post"]
    content = operation["requestBody"]["content"]
    assert set(content) == {"multipart/form-data", "application/json"}
    assert content["multipart/form-data"]["schema"]["properties"]["photo"]["anyOf"][
        0
    ] == {
        "type": "string",
        "format": "binary",
    }
    assert operation["responses"]["201"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ReportResult"
    }


# ------------------------------------------------------------ the form parser


def test_the_parser_keeps_binary_parts_exactly() -> None:
    boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
    body = (
        (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="type"\r\n\r\n'
            "ice\r\n"
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="photo"; filename="a b.jpg"\r\n'
            "Content-Type: image/jpeg\r\n\r\n"
        ).encode()
        + JPEG
        + f"\r\n--{boundary}--\r\n".encode()
    )
    parts = parse_multipart(body, f'multipart/form-data; boundary="{boundary}"')
    assert [(p.name, p.filename) for p in parts] == [
        ("type", None),
        ("photo", "a b.jpg"),
    ]
    assert parts[0].data == b"ice" and parts[1].data == JPEG


@pytest.mark.parametrize(
    "body",
    [
        b'--b\r\nContent-Disposition: form-data; name="type"\r\n\r\nice\r\n',  # no end
        b"--b\r\nContent-Type: text/plain\r\n\r\nice\r\n--b--\r\n",  # no disposition
        b"--b\r\nContent-Disposition: form-data\r\n\r\nice\r\n--b--\r\n",  # no name
        b'--b\r\nContent-Disposition: form-data; name="type"\r\nice\r\n--b--\r\n',
        b"--b--\r\n",  # no parts at all
    ],
)
def test_the_parser_rejects_malformed_bodies(body: bytes) -> None:
    with pytest.raises(ValueError):
        parse_multipart(body, "multipart/form-data; boundary=b")


# ------------------------------------------------------------------- routing


def _pos(x_m: float, y_m: float) -> tuple[float, float]:
    """(lng, lat) a number of metres east and north of downtown."""
    return (
        LNG + math.degrees(x_m / 6_371_008.8 / math.cos(math.radians(LAT))),
        LAT + math.degrees(y_m / 6_371_008.8),
    )


def diamond() -> WalkNetwork:
    """A-B-C-D straight along a street (200 m), or around through E (about 264 m)."""
    points = {"A": (-100, 0), "B": (-40, 0), "C": (40, 0), "D": (100, 0), "E": (0, 60)}
    names = list(points)
    nodes = [
        RawNode(i + 1, _pos(*points[n])[1], _pos(*points[n])[0])
        for i, n in enumerate(names)
    ]
    edges = [
        RawEdge(
            names.index(u) + 1,
            names.index(v) + 1,
            math.dist(points[u], points[v]),
            highway="footway",
            osmid=100 + k,
        )
        for k, (u, v) in enumerate(["AB", "BC", "CD", "BE", "EC"])
    ]
    return WalkNetwork.from_data(export_network(nodes, edges, source="OP-308 test"))


@pytest.mark.parametrize(
    ("report_type", "profile", "detours"),
    [
        ("ice", "walker_cane", True),  # ice risk: avoid with a walker or cane
        ("ice", "standard", False),  # x 1.5 on 80 m is cheaper than the 64 m detour
        ("snow", "wheelchair", True),
        ("blocked", "standard", True),
    ],
)
def test_a_report_changes_routes_per_profile(
    store, report_type, profile, detours
) -> None:
    network = diamond()
    client = TestClient(make_app(store))
    lng, lat = _pos(0, 0)  # the middle of the B-C street
    body = client.post(
        "/api/reports", json=report(type=report_type, lat=lat, lng=lng)
    ).json()
    hazards = HazardProvider(store, network, background=False)(NOW)
    assert [h.item_id for edge in hazards.values() for h in edge] == [body["id"]]

    router = Router(network, load_routing_config())
    (a_lng, a_lat), (d_lng, d_lat) = _pos(-100, 0), _pos(100, 0)
    best = router.route(a_lat, a_lng, d_lat, d_lng, profile, hazards).routes[0]
    if detours:
        assert best.distance_m == pytest.approx(60 + 2 * math.hypot(40, 60) + 60, abs=1)
        assert [h.item_id for h in best.hazards_avoided] == [body["id"]]
    else:
        assert best.distance_m == pytest.approx(200, abs=1)
        assert [h.item_id for h in best.hazards_on_route] == [body["id"]]
