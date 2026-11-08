"""OP-204: test camera scheduling, rate limits, stability and metadata-only posting.

Use synthetic camera metadata and MockTransport. No API key, live camera,
stored image or trained model is needed for these reliability checks.
"""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from vision import worker
from vision.cameras import API_URL, CameraAdapter, CameraView, RateLimiter
from vision.inference import Prediction
from vision.worker import (
    CameraWorker,
    FrameFetcher,
    FrameUnavailable,
    HttpPublisher,
    Reading,
    StabilityFilter,
)

NOW = datetime(2026, 11, 8, 2, tzinfo=UTC)
SAMPLE = json.loads(
    (Path(__file__).parent / "fixtures/alberta511_cameras_sample.json").read_text()
)
CAMERA = {**SAMPLE[0], "Views": SAMPLE[0]["Views"][:1]}


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("Tests must not call the network")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)


class Clock:
    def __init__(self) -> None:
        self.seconds = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.seconds

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.seconds += seconds


class Classifier:
    version = "synthetic-test-model"

    def __init__(self, labels: list[str]) -> None:
        self.labels = iter(labels)

    def predict(self, frame: bytes) -> Prediction:
        return Prediction(next(self.labels), 0.9)


def make_worker(labels: list[str], *, handler=None, cameras=None):
    clock = Clock()
    requests: list[tuple[float, httpx.Request]] = []
    frame_number = 0

    def transport(request: httpx.Request) -> httpx.Response:
        nonlocal frame_number
        requests.append((clock(), request))
        if str(request.url).startswith(API_URL):
            return httpx.Response(
                200, json=cameras if cameras is not None else [CAMERA]
            )
        frame_number += 1
        if handler:
            return handler(request)
        return httpx.Response(
            200,
            content=f"distinct-frame-{frame_number}".encode(),
            headers={"content-type": "image/png"},
        )

    client = httpx.Client(transport=httpx.MockTransport(transport))
    limiter = RateLimiter(clock=clock, sleep=clock.sleep)
    adapter = CameraAdapter(
        "test-value-only", client=client, limiter=limiter, now=lambda: NOW
    )
    camera_worker = CameraWorker(
        adapter,
        FrameFetcher(client, limiter),
        Classifier(labels),
        clock=clock,
        now=lambda: NOW + timedelta(seconds=clock()),
    )
    return camera_worker, clock, requests


def test_two_agreeing_frames_and_one_log_per_condition_change(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="vision.worker")
    camera_worker, clock, _ = make_worker(
        ["dry", "wet", "wet", "wet", "ice", "wet", "ice", "ice"]
    )
    results = []
    for _ in range(8):
        results.append(camera_worker.cycle())
        clock.sleep(300)
    assert [bool(result) for result in results] == [
        False,
        False,
        True,
        True,
        False,
        True,
        False,
        True,
    ]
    assert results[2][0].label == "wet"
    assert results[-1][0].label == "ice"
    changes = [record.message for record in caplog.records if "->" in record.message]
    assert changes == [
        "CHANGE camera_model:511:9001:1 pending -> wet",
        "CHANGE camera_model:511:9001:1 wet -> ice",
    ]


def test_offline_frames_confirm_unknown_and_do_not_renew_previous_dry() -> None:
    camera_worker, clock, _ = make_worker(["dry", "dry"])
    assert not camera_worker.cycle()
    clock.sleep(300)
    saved = camera_worker.cycle()[0]
    camera_worker.frames.client = httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(503))
    )
    clock.sleep(300)
    assert not camera_worker.cycle()
    assert camera_worker.last_good[saved.source_ref] == saved
    clock.sleep(300)
    unknown = camera_worker.cycle()[0]
    assert unknown.label == "unknown"
    assert unknown.reason == "camera_http_error"


def test_identical_frozen_frame_never_confirms_or_renews_safety() -> None:
    camera_worker, clock, _ = make_worker(
        ["dry"] * 3,
        handler=lambda req: httpx.Response(
            200, content=b"frozen", headers={"content-type": "image/jpeg"}
        ),
    )
    for _ in range(3):
        assert not camera_worker.cycle()
        clock.sleep(300)
    assert not camera_worker.last_good


def test_failed_and_disabled_views_are_unknown_without_breaking_good_neighbor() -> None:
    cameras = [deepcopy(CAMERA), deepcopy(CAMERA)]
    cameras[1]["Id"] = 9002
    cameras[1]["Views"][0]["Status"] = "Disabled"
    camera_worker, clock, requests = make_worker(["wet", "wet"], cameras=cameras)
    camera_worker.cycle()
    clock.sleep(300)
    result = camera_worker.cycle()
    assert {reading.label for reading in result} == {"wet", "unknown"}
    assert len(requests) == 3  # one catalog + two enabled-view images


def test_all_metadata_and_image_requests_share_511_window_limit() -> None:
    cameras = [{**deepcopy(CAMERA), "Id": number} for number in range(5)]
    camera_worker, _, requests = make_worker(["wet"] * 15, cameras=cameras)
    for _ in range(3):
        camera_worker.cycle()
    times = [at for at, _ in requests]
    assert len(times) == 16
    assert times[10] >= 60
    for start in times:
        assert sum(start <= at < start + 60 for at in times) <= 10


def test_catalog_refreshes_daily_and_outside_edmonton_is_ignored() -> None:
    camera_worker, clock, requests = make_worker(
        ["wet"] * 10, cameras=[CAMERA, SAMPLE[2]]
    )
    camera_worker.cycle()
    clock.sleep(300)
    camera_worker.cycle()
    assert sum("/api/v2/" in req.url.path for _, req in requests) == 1
    clock.sleep(86_400)
    camera_worker.cycle()
    assert sum("/api/v2/" in req.url.path for _, req in requests) == 2
    assert len(camera_worker.stability.states) == 1


def test_catalog_outage_uses_last_good_and_retries_next_cycle() -> None:
    camera_worker, clock, _ = make_worker(["wet"] * 4)
    camera_worker.cycle()
    original = camera_worker.adapter._client
    camera_worker.adapter._client = httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(503))
    )
    clock.sleep(86_400)
    assert camera_worker.cycle()[0].label == "wet"
    camera_worker.adapter._client = original
    clock.sleep(300)
    assert camera_worker.cycle()[0].label == "wet"


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<html>Viewer</html>"),
        httpx.Response(302, headers={"location": "https://example.org/private"}),
        httpx.Response(200, content=b"", headers={"content-type": "image/jpeg"}),
        httpx.Response(429),
    ],
)
def test_viewer_pages_redirects_empty_frames_and_http_errors_are_not_images(
    response: httpx.Response,
) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return response

    fetcher = FrameFetcher(
        httpx.Client(transport=httpx.MockTransport(handler)), RateLimiter()
    )
    with pytest.raises(FrameUnavailable):
        fetcher.fetch(CameraView.model_validate(CAMERA["Views"][0]))
    assert len(calls) == 1


@pytest.mark.parametrize(
    "url",
    [
        "http://511.alberta.ca/frame",
        "https://example.org/frame",
        "https://user:pass@511.alberta.ca/frame",
    ],
)
def test_unapproved_image_urls_never_make_a_request(url: str) -> None:
    fetcher = FrameFetcher(httpx.Client(), RateLimiter())
    with pytest.raises(FrameUnavailable, match="unapproved"):
        fetcher.fetch(CameraView(id=1, url=url, status="Enabled"))


def test_frame_download_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker, "MAX_FRAME_BYTES", 3)
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200, content=b"large", headers={"content-type": "image/png"}
            )
        )
    )
    with pytest.raises(FrameUnavailable, match="too_large"):
        FrameFetcher(client, RateLimiter()).fetch(
            CameraView.model_validate(CAMERA["Views"][0])
        )


def reading() -> Reading:
    return Reading(
        source_ref="511:9001:1",
        camera_id=9001,
        view_id=1,
        latitude=53.5,
        longitude=-113.5,
        label="wet",
        score=0.9,
        observed_at=NOW,
        expires_at=NOW + timedelta(minutes=15),
        model_version="test",
        mode="live",
    )


def test_publisher_posts_metadata_with_worker_header_and_never_replay() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    publisher = HttpPublisher(
        httpx.Client(transport=httpx.MockTransport(handler)),
        "https://api.example.org",
        "test-header-value",
    )
    assert publisher.publish(reading())
    request = requests[0]
    assert request.url.path == "/api/ingest/camera"
    assert request.headers["X-Worker-Key"] == "test-header-value"
    payload = json.loads(request.content)
    assert payload["source"] == "camera_model"
    assert "test-header-value" not in request.content.decode()
    assert not ({"frame", "image", "pixels"} & payload.keys())
    with pytest.raises(ValueError, match="Replay"):
        publisher.publish(reading().model_copy(update={"mode": "replay"}))
    assert len(requests) == 1


def test_failed_publish_retains_saved_reading_and_next_cycle_recovers() -> None:
    camera_worker, clock, _ = make_worker(["wet"] * 4)
    responses = iter([204, 503, 204])
    publisher = HttpPublisher(
        httpx.Client(
            transport=httpx.MockTransport(lambda req: httpx.Response(next(responses)))
        ),
        "http://localhost:8000",
        "test-value",
    )
    camera_worker.publisher = publisher
    camera_worker.cycle()
    clock.sleep(300)
    saved = camera_worker.cycle()[0]
    clock.sleep(300)
    camera_worker.cycle()
    assert camera_worker.last_good[saved.source_ref] == saved
    clock.sleep(300)
    assert camera_worker.cycle()[0] == camera_worker.last_good[saved.source_ref]
    assert not camera_worker._publish_failed


def test_publisher_timeout_returns_failure_without_leaking_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("failed", request=request)

    publisher = HttpPublisher(
        httpx.Client(transport=httpx.MockTransport(handler)),
        "https://api.example.org",
        "private-test-value",
    )
    assert not publisher.publish(reading())
    assert "private-test-value" not in caplog.text


def test_background_loop_waits_between_complete_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    camera_worker, _, _ = make_worker([])
    events = []
    monkeypatch.setattr(camera_worker, "cycle", lambda: events.append("cycle"))

    def stop(seconds: float) -> None:
        events.append(seconds)
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        camera_worker.run(sleep=stop)
    assert events == ["cycle", 300]


def test_replay_command_is_explicitly_synthetic_and_needs_no_network(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert worker.main([]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == "replay" and result["synthetic_predictions"]
    assert [item["label"] for item in result["readings"]] == ["wet", "ice", "unknown"]
    assert all(item["mode"] == "replay" for item in result["readings"])


@pytest.mark.parametrize(
    "argv", [["--publish"], ["--interval-s", "nan"], ["--interval-s", "0"]]
)
def test_cli_rejects_unsafe_or_invalid_options(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as error:
        worker.main(argv)
    assert error.value.code == 2


def test_live_command_cannot_override_replay_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATA_MODE", "replay")
    with pytest.raises(SystemExit) as error:
        worker.main(["--live"])
    assert error.value.code == 2


def test_no_camera_image_is_written(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("The worker must not store frames")

    monkeypatch.setattr(Path, "write_bytes", forbidden)
    monkeypatch.setattr(Path, "write_text", forbidden)
    camera_worker, _, _ = make_worker(["wet", "wet"])
    camera_worker.cycle()
    assert camera_worker.cycle()[0].label == "wet"


def test_unknown_cannot_be_replaced_by_a_single_clear_frame() -> None:
    stability = StabilityFilter()
    assert not stability.observe("A", "unknown", "error1")
    assert stability.observe("A", "unknown", "error2")
    assert not stability.observe("A", "dry", "frame1")
    assert stability.states["A"].confirmed == "unknown"
    assert stability.observe("A", "dry", "frame2")


def test_failed_post_retries_identical_frame_without_refreshing_timestamp() -> None:
    payloads = []

    class Publisher:
        def publish(self, reading: Reading) -> bool:
            payloads.append(reading)
            return len(payloads) > 1

    camera_worker, clock, _ = make_worker(["wet"] * 3)
    camera_worker.publisher = Publisher()
    camera_worker.cycle()
    clock.sleep(300)
    camera_worker.cycle()
    camera_worker.frames.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200, content=b"distinct-frame-2", headers={"content-type": "image/png"}
            )
        )
    )
    clock.sleep(300)
    assert not camera_worker.cycle()
    assert payloads[0] == payloads[1]
    assert camera_worker.last_good[payloads[0].source_ref] == payloads[0]


def test_real_decode_and_quality_gate_replace_dry_with_unknown() -> None:
    from vision.inference import OnnxClassifier
    from vision.tests.test_worker_inference import FakeSession, png, spec_for

    images = iter([png(seed=1), png(seed=2), png(level=0), png(level=0)])
    camera_worker, clock, _ = make_worker(
        [],
        handler=lambda req: httpx.Response(
            200, content=next(images), headers={"content-type": "image/png"}
        ),
    )
    camera_worker.classifier = OnnxClassifier(
        FakeSession([[20, 0, 0, 0, 0, 0]]), spec_for()
    )
    output = []
    for _ in range(4):
        output.extend(camera_worker.cycle())
        clock.sleep(300)
    assert [item.label for item in output] == ["dry", "unknown"]
