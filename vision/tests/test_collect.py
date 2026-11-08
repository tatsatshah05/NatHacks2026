"""Purpose: verify OP-201 collection with synthetic bytes and mocked HTTP only."""

import csv
import io
import json
from datetime import UTC, datetime

import httpx
import pytest
from PIL import Image

from vision.cameras import Camera, RateLimiter
from vision.tools.collect import collect, main, select_views, trusted_view
from vision.tools.label import parse_image_name


def camera(camera_id=12, url="https://511.alberta.ca/map/Cctv/123"):
    return Camera.model_validate(
        {
            "Id": camera_id,
            "Latitude": 53.54,
            "Longitude": -113.49,
            "Views": [{"Id": 123, "Url": url, "Status": "Enabled"}],
        }
    )


def image_bytes():
    data = io.BytesIO()
    Image.new("RGB", (8, 8), (20, 30, 40)).save(data, format="PNG")
    return data.getvalue()


def run_collection(tmp_path, response=None, action="k", cameras=None):
    selected = cameras or [camera()]
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: (
                response
                if response is not None
                else httpx.Response(200, content=image_bytes())
            )
        )
    )
    with client:
        return collect(
            [(c, c.views[0]) for c in selected],
            tmp_path / "raw",
            "Estella",
            client,
            RateLimiter(),
            review=lambda data, title: action,
            now=lambda: datetime(2026, 11, 8, 4, 20, tzinfo=UTC),
        )


@pytest.mark.parametrize(
    "url",
    [
        "http://511.alberta.ca/image",
        "https://evil.example/image",
        "https://511.alberta.ca.evil.example/image",
        "https://user:password@511.alberta.ca/image",
        "https://511.alberta.ca:8443/image",
        "https://511.alberta.ca:bad/image",
    ],
)
def test_only_approved_https_host_is_used(url):
    assert not trusted_view(url)


def test_selection_uses_one_enabled_view_per_distinct_camera(tmp_path):
    first = camera()
    first.views.append(first.views[0].model_copy(update={"id": 124}))
    outside = camera(20).model_copy(update={"latitude": 50})
    disabled = camera(21)
    disabled.views[0].status = "Disabled"
    cache = tmp_path / "cameras.json"
    cache.write_text(
        json.dumps(
            {
                "cameras": [
                    c.model_dump()
                    for c in [first, first, outside, disabled, camera(13)]
                ]
            }
        )
    )
    assert [c.id for c, _ in select_views(cache, 6)] == [12, 13]


def test_provincial_training_scope_and_specific_camera_selection(tmp_path):
    outside = camera(20).model_copy(update={"latitude": 50})
    cache = tmp_path / "cameras.json"
    cache.write_text(
        json.dumps({"cameras": [camera().model_dump(), outside.model_dump()]})
    )
    assert [c.id for c, _ in select_views(cache, 6)] == [12]
    assert [c.id for c, _ in select_views(cache, 6, "alberta")] == [12, 20]
    assert [c.id for c, _ in select_views(cache, 6, "alberta", {20})] == [20]
    assert select_views(cache, 6, "edmonton", {20}) == []
    assert select_views(cache, 6, "alberta", set()) == []


@pytest.mark.parametrize("action", ["s", "q", "invalid"])
def test_no_frame_is_written_without_explicit_privacy_confirmation(tmp_path, action):
    assert run_collection(tmp_path, action=action) == 0
    assert not list(tmp_path.rglob("*.png"))
    assert not (tmp_path / "collection.csv").exists()


def test_retained_frame_is_unlabelled_original_with_camera_id_and_provenance(tmp_path):
    assert run_collection(tmp_path) == 1
    path = next((tmp_path / "raw").glob("*.png"))
    assert path.read_bytes() == image_bytes()
    assert parse_image_name(path.name) == (
        "12",
        datetime(2026, 11, 8, 4, 20, tzinfo=UTC),
    )
    with (tmp_path / "collection.csv").open(newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["camera_id"] == "12" and row["view_id"] == "123"
    assert row["privacy_checked_by"] == "Estella"
    assert row["timestamp_basis"] == "retrieved_at_not_verified_capture_time"
    assert "label" not in row and "url" not in row


@pytest.mark.parametrize("status", [302, 404, 500])
def test_redirect_and_http_failure_are_not_retained(tmp_path, status):
    response = httpx.Response(
        status,
        headers={"Location": "https://evil.example/frame"},
        content=image_bytes(),
    )
    assert run_collection(tmp_path, response=response) == 0
    assert not list(tmp_path.rglob("*.png"))


def test_429_stops_without_fetching_remaining_cameras(tmp_path, capsys):
    requests = []

    def fetch(request):
        requests.append(request)
        return httpx.Response(429)

    cameras = [camera(), camera(13)]
    with httpx.Client(transport=httpx.MockTransport(fetch)) as client:
        assert (
            collect(
                [(c, c.views[0]) for c in cameras],
                tmp_path / "raw",
                "Estella",
                client,
                RateLimiter(),
            )
            == 0
        )
    assert len(requests) == 1
    assert "wait at least 60 seconds" in capsys.readouterr().out


def test_error_logs_do_not_include_url_or_key(tmp_path, capsys):
    c = camera(url="https://511.alberta.ca/map/Cctv/123?key=secret-test-value")

    def fetch(request):
        raise httpx.ReadTimeout(f"Timed out {request.url}", request=request)

    with httpx.Client(transport=httpx.MockTransport(fetch)) as client:
        assert (
            collect(
                [(c, c.views[0])], tmp_path / "raw", "Estella", client, RateLimiter()
            )
            == 0
        )
    output = capsys.readouterr().out
    assert "secret-test-value" not in output and "https://" not in output


def test_non_image_response_is_not_previewed_or_saved(tmp_path):
    assert (
        run_collection(tmp_path, response=httpx.Response(200, text="login required"))
        == 0
    )


def test_plan_reads_cache_without_key_or_network(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ALBERTA_511_KEY", raising=False)
    cache = tmp_path / "cameras.json"
    cache.write_text(json.dumps({"cameras": [camera().model_dump()]}))
    assert main(["--plan", "--cache", str(cache)]) == 0
    assert "camera=12 view=123" in capsys.readouterr().out
    with pytest.raises(SystemExit) as error:
        main(["--cache", str(cache)])
    assert error.value.code == 2


@pytest.mark.parametrize(
    "content", ["[]", '{"cameras": null}', '{"cameras": [null]}', '{"cameras": {}}']
)
def test_cli_handles_malformed_cache_without_traceback(tmp_path, content, capsys):
    cache = tmp_path / "bad.json"
    cache.write_text(content)
    with pytest.raises(SystemExit) as error:
        main(["--plan", "--cache", str(cache)])
    assert error.value.code == 2
    assert "Cannot read cached camera metadata" in capsys.readouterr().err


def test_live_collection_requires_the_actual_reviewer(monkeypatch, capsys):
    monkeypatch.setenv("ALBERTA_511_KEY", "placeholder")
    with pytest.raises(SystemExit) as error:
        main([])
    assert error.value.code == 2
    assert "--reviewer is required" in capsys.readouterr().err


def test_missing_key_with_explicit_reviewer_is_named(monkeypatch, capsys):
    monkeypatch.delenv("ALBERTA_511_KEY", raising=False)
    with pytest.raises(SystemExit) as error:
        main(["--reviewer", "Ipsa"])
    assert error.value.code == 2
    assert "ALBERTA_511_KEY" in capsys.readouterr().err
