"""OP-204 / PR #111: prevent cached stills and failed delivery from looking fresh.

Cover Tatsat's review with fake clocks/transports and recorded public metadata.
Keys are placeholders; no test fetches a real image or contacts a live service.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
import pytest

from vision import worker
from vision.cameras import (
    CameraAdapter,
    CameraView,
    RateLimiter,
    count_in_edmonton,
    parse_cameras,
)
from vision.tests.test_worker import CAMERA, NOW, Clock, make_worker, reading
from vision.tests.test_worker_inference import spec_for
from vision.worker import FrameFetcher, FrameUnavailable, StabilityFilter


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("Tests must not contact live services")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)


def test_alternating_cached_stills_cannot_keep_renewing_dry() -> None:
    images = iter([b"a", b"b", b"a", b"b", b"a", b"b"])
    camera_worker, clock, _ = make_worker(
        ["dry"] * 6,
        handler=lambda req: httpx.Response(
            200, content=next(images), headers={"content-type": "image/jpeg"}
        ),
    )
    assert not camera_worker.cycle()
    clock.sleep(300)
    saved = camera_worker.cycle()[0]
    for _ in range(4):
        clock.sleep(300)
        assert not camera_worker.cycle()
        assert camera_worker.last_good[saved.source_ref] == saved
    assert saved.expires_at <= camera_worker.now()


def test_recent_good_hashes_survive_unknown_attempts_and_memory_is_bounded() -> None:
    votes = StabilityFilter()
    assert not votes.observe("A", "dry", "a")
    assert votes.observe("A", "dry", "b")
    for index in range(40):
        votes.observe("A", "unknown", f"error-{index}")
    assert not votes.observe("A", "dry", "a")
    assert not votes.observe("A", "dry", "b")
    assert votes.states["A"].confirmed == "unknown"
    for index in range(100):
        votes.observe("A", "wet", f"fresh-{index}")
    assert len(votes.states["A"].recent_frames) <= worker.RECENT_FRAME_LIMIT


def test_malformed_catalog_port_is_a_camera_failure_not_model_error() -> None:
    camera_worker, _, _ = make_worker([])
    camera_worker.adapter._last_good = []
    camera = {
        **CAMERA,
        "Views": [
            {"Id": 1, "Url": "https://511.alberta.ca:abc/cam.jpg", "Status": "Enabled"}
        ],
    }
    camera_worker.adapter._client = httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=[camera]))
    )
    assert not camera_worker.cycle()
    result = camera_worker.cycle()[0]
    assert result.label == "unknown" and result.reason == "camera_unavailable"


def test_unexpected_fetch_failure_and_predict_failure_have_distinct_reasons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failed(*args: object) -> bytes:
        raise ValueError("private transport detail")

    camera_worker, _, _ = make_worker([])
    monkeypatch.setattr(camera_worker.frames, "fetch", failed)
    camera_worker.cycle()
    assert camera_worker.cycle()[0].reason == "camera_unavailable"
    other, _, _ = make_worker([])
    monkeypatch.setattr(other.classifier, "predict", failed)
    other.cycle()
    assert other.cycle()[0].reason == "model_error"


def configure_live_cli(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, succeeded: int, failed: int
):
    labels = tmp_path / "labels.json"
    labels.write_text("{}")
    monkeypatch.setenv("ALBERTA_511_KEY", "placeholder")
    monkeypatch.setenv("MODEL_URL", "https://example.org/model.onnx")
    monkeypatch.setenv("CLOUD_URL", "http://localhost:8000")
    monkeypatch.setenv("WORKER_KEY", "placeholder")
    monkeypatch.delenv("DATA_MODE", raising=False)
    monkeypatch.setattr(
        worker.ModelSpec, "from_training_labels", lambda *a, **k: spec_for()
    )
    monkeypatch.setattr(worker, "download_model", lambda *a: b"model")
    monkeypatch.setattr(
        worker.OnnxClassifier, "load", lambda *a: SimpleNamespace(version="test")
    )
    captured = {}

    def adapter(*args: object, **kwargs: object):
        captured.update(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(worker, "CameraAdapter", adapter)
    monkeypatch.setattr(
        worker,
        "CameraWorker",
        lambda *a, **k: SimpleNamespace(
            cycle=lambda: [reading()] if succeeded + failed else [],
            publish_attempted=succeeded + failed,
            publish_succeeded=succeeded,
            publish_failed=failed,
        ),
    )
    argv = [
        "--live",
        "--once",
        "--publish",
        "--model-labels",
        str(labels),
        "--model-sha256",
        "0" * 64,
        "--min-score",
        "0.7",
    ]
    return argv, captured


@pytest.mark.parametrize(
    "succeeded,failed,exit_code", [(0, 2, 1), (0, 0, 0), (1, 1, 0), (1, 0, 0)]
)
def test_once_publish_reports_actual_delivery_and_honest_exit_status(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    succeeded: int,
    failed: int,
    exit_code: int,
) -> None:
    argv, captured = configure_live_cli(
        monkeypatch, tmp_path, succeeded=succeeded, failed=failed
    )
    assert worker.main(argv) == exit_code
    result = json.loads(capsys.readouterr().out)
    assert result["publish_requested"] is True
    assert result["published"] is (succeeded > 0)
    assert (
        result["publish_succeeded"] == succeeded and result["publish_failed"] == failed
    )
    assert captured["cache_path"] == worker.DEFAULT_CACHE_PATH


def test_worker_can_start_from_disk_metadata_during_catalog_outage(
    tmp_path: Path,
) -> None:
    cache = tmp_path / "catalog.json"
    clock = Clock()
    limiter = RateLimiter(clock=clock, sleep=clock.sleep)
    good = httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=[CAMERA]))
    )
    CameraAdapter(
        "placeholder", client=good, limiter=limiter, cache_path=cache, now=lambda: NOW
    ).refresh()
    restarted = CameraAdapter(
        "placeholder",
        client=httpx.Client(
            transport=httpx.MockTransport(lambda req: httpx.Response(503))
        ),
        limiter=limiter,
        cache_path=cache,
        now=lambda: NOW,
    )
    camera_worker, _, _ = make_worker(["wet", "wet"])
    camera_worker.adapter = restarted
    camera_worker.cycle()
    assert camera_worker.cycle()[0].label == "wet"


def test_expired_pending_post_logs_once_and_is_never_renewed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    camera_worker, clock, _ = make_worker(["wet"] * 4)
    camera_worker.publisher = SimpleNamespace(publish=lambda item: False)
    camera_worker.cycle()
    clock.sleep(300)
    pending = camera_worker.cycle()[0]
    camera_worker.frames.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200, content=b"distinct-frame-2", headers={"content-type": "image/jpeg"}
            )
        )
    )
    clock.sleep(901)
    with caplog.at_level(logging.WARNING, logger="vision.worker"):
        camera_worker.cycle()
        camera_worker.cycle()
    assert sum("expired undelivered" in r.message for r in caplog.records) == 1
    assert pending.source_ref not in camera_worker._pending_posts
    assert not camera_worker.last_good


@pytest.mark.parametrize(
    "payload",
    [
        {"mode": "replay", "observations": None},
        [],
        {"mode": "replay", "observations": [None]},
    ],
)
def test_malformed_replay_is_usage_error_without_traceback(
    tmp_path: Path, payload: object, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "replay.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(SystemExit) as error:
        worker.main(["--replay", str(path)])
    assert error.value.code == 2
    assert "Traceback" not in capsys.readouterr().err


@pytest.mark.parametrize(
    "payload", [{}, [], {"type": "Polygon", "coordinates": [[[1]]]}]
)
def test_incomplete_boundary_has_sanitised_live_startup_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    payload: object,
) -> None:
    real_worker = worker.CameraWorker
    argv, _ = configure_live_cli(monkeypatch, tmp_path, succeeded=0, failed=0)
    camera_worker, _, _ = make_worker([])
    monkeypatch.setattr(worker, "CameraAdapter", lambda *a, **k: camera_worker.adapter)
    monkeypatch.setattr(worker, "CameraWorker", real_worker)
    boundary = tmp_path / "boundary.json"
    boundary.write_text(json.dumps(payload))
    assert worker.main([*argv, "--boundary", str(boundary)]) == 1
    output = capsys.readouterr().err
    assert "Worker startup failed" in output and "Traceback" not in output


@pytest.mark.parametrize("content_type", ["image/png", "text/html", "image/webp", ""])
def test_non_jpeg_placeholder_cannot_confirm_a_previous_snow_vote(
    content_type: str,
) -> None:
    responses = iter(
        [
            httpx.Response(
                200, content=b"snow", headers={"content-type": "image/jpeg"}
            ),
            httpx.Response(
                200, content=b"placeholder", headers={"content-type": content_type}
            ),
            httpx.Response(
                200, content=b"placeholder", headers={"content-type": content_type}
            ),
        ]
    )
    camera_worker, _, _ = make_worker(
        ["snow_covered"], handler=lambda req: next(responses)
    )
    assert not camera_worker.cycle()
    assert not camera_worker.cycle()
    result = camera_worker.cycle()[0]
    assert result.label == "unknown" and result.reason == "camera_unavailable"
    assert camera_worker.stability.states[result.source_ref].confirmed == "unknown"


@pytest.mark.parametrize("first", ["metadata", "image"])
def test_http_429_cools_down_both_metadata_and_images_for_full_minute(
    first: str,
) -> None:
    clock = Clock()
    times = []

    def handler(request: httpx.Request) -> httpx.Response:
        times.append(clock())
        if len(times) == 1:
            return httpx.Response(429)
        if request.url.path == "/api/v2/get/cameras":
            return httpx.Response(200, json=[CAMERA])
        return httpx.Response(
            200, content=b"test", headers={"content-type": "image/jpeg"}
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    limiter = RateLimiter(clock=clock, sleep=clock.sleep)
    adapter = CameraAdapter("placeholder", client=client, limiter=limiter)
    fetcher = FrameFetcher(client, limiter)
    view = CameraView.model_validate(CAMERA["Views"][0])
    if first == "metadata":
        assert adapter.refresh().stale
        fetcher.fetch(view)
    else:
        with pytest.raises(FrameUnavailable):
            fetcher.fetch(view)
        assert not adapter.refresh().stale
    assert times == [0, 60]


def test_recorded_official_metadata_matches_provenance_and_contains_no_query_credentials() -> (
    None
):
    directory = Path(__file__).parent / "fixtures"
    manifest = json.loads(
        (directory / "alberta511_cameras_recorded.manifest.json").read_text()
    )
    text = (directory / manifest["file"]).read_text(encoding="utf-8")
    assert hashlib.sha256(text.encode()).hexdigest() == manifest["sha256_lf"]
    raw = json.loads(text)
    cameras = parse_cameras(raw)
    assert len(cameras) == len(raw) == manifest["camera_count"]
    counts = count_in_edmonton(cameras)
    assert counts.in_edmonton == manifest["edmonton_bbox_camera_count"]
    assert counts.enabled_views_in_edmonton == manifest["edmonton_bbox_enabled_views"]
    for camera in cameras:
        for view in camera.views:
            url = urlsplit(view.url)
            assert url.hostname == "511.alberta.ca" and not url.query
    assert manifest["single_view_check"]["images_saved"] == 0
