"""OP-305: scripts/record_snapshot.py, which `make record` runs. No network.

Every request goes to a mock transport that answers from the recorded fixtures
(the LRT and construction readers use ``urllib``, so their one request method is
replaced). The tests check the replay layout, the manifest, that a failed source
does not stop the others, that only JSON is stored, that the token is never
written, and that a recording is a drop-in ``REPLAY_SNAPSHOT``.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from cloud.app import sources as src
from cloud.app.adapters import construction, lrt_devices

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
NOW = datetime(2026, 11, 7, 21, 30, tzinfo=UTC)  # 30 min after the weather fixture
TOKEN = "test-token-never-written"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic"}


def load_recorder() -> ModuleType:
    """Import scripts/record_snapshot.py by path (scripts/ is not a package)."""
    spec = importlib.util.spec_from_file_location(
        "openpath_record_snapshot", ROOT / "scripts" / "record_snapshot.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Dataclasses look their module up in sys.modules while the class is built.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


recorder = load_recorder()


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


CAMERA_FEATURE = {
    "type": "Feature",
    "id": "511:56:117",
    "geometry": {"type": "Point", "coordinates": [-113.49, 53.54]},
    "properties": {
        "source": "camera_model",
        "source_ref": "511:56:117",
        "camera_id": 56,
        "view_id": 117,
        "label": "wet",
        "score": 0.9,
        "reason": None,
        "observed_at": "2026-11-07T21:25:00Z",
        "expires_at": "2026-11-07T21:40:00Z",
        "age_s": 300.0,
        "model_version": "b" * 64,
        "mode": "live",
        "attribution": "Road camera data: 511 Alberta, Government of Alberta",
    },
}


class FakeNetwork:
    """The City API, the two weather services, the archive and a live OpenPath API."""

    def __init__(self, *, fail: set[str] = frozenset(), camera_image: bool = False):
        self.fail = fail
        self.camera_image = camera_image
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host, path = request.url.host, request.url.path
        if host == "data.edmonton.ca":
            dataset = path.rsplit("/", 1)[-1].removesuffix(".json")
            if dataset in self.fail:
                return httpx.Response(404, json={"error": "gone"})
            if int(request.url.params.get("$offset", "0")) > 0:
                return httpx.Response(200, json=[])
            return httpx.Response(200, json=fixture(src.REPLAY_FILES[dataset]))
        if host == "api.weather.gc.ca":
            return httpx.Response(200, json=fixture("weather/eccc.json"))
        if host == "api.open-meteo.com":
            return httpx.Response(200, json=fixture("weather/open_meteo.json"))
        if host == "archive-api.open-meteo.com":
            return httpx.Response(
                200, json=fixture("weather/freeze_thaw/open_meteo_archive.json")
            )
        if host == "api.test" and path == "/api/cameras":
            if self.camera_image:
                return httpx.Response(
                    200, content=b"\xff\xd8\xff", headers={"content-type": "image/jpeg"}
                )
            return httpx.Response(
                200,
                json={
                    "type": "FeatureCollection",
                    "generated_at": "2026-11-07T21:30:00Z",
                    "features": [CAMERA_FEATURE],
                },
            )
        raise AssertionError(f"unexpected request {request.url}")


@pytest.fixture
def urllib_readers(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Serve the LRT and construction readers' ``urllib`` requests from fixtures."""
    seen: list[str] = []

    def lrt(self: Any, request: Any) -> list[dict[str, Any]]:
        seen.append(request.full_url)
        url = urlparse(request.full_url)
        if int(parse_qs(url.query)["$offset"][0]) > 0:
            return []
        dataset = url.path.rsplit("/", 1)[-1].removesuffix(".json")
        return fixture(src.REPLAY_FILES[dataset])

    def permits(self: Any, request: Any) -> list[dict[str, Any]]:
        seen.append(request.full_url)
        assert request.get_header("X-app-token") in (None, TOKEN)
        return fixture("construction/pedestrian_points.json")

    monkeypatch.setattr(lrt_devices.SocrataReader, "_request", lrt)
    monkeypatch.setattr(construction.ConstructionReader, "_request", permits)
    monkeypatch.delenv("SOCRATA_APP_TOKEN", raising=False)
    monkeypatch.delenv("WEATHER_REPLAY", raising=False)
    return seen


def record(argv: list[str], network: FakeNetwork) -> int:
    args = recorder.parse_args(argv)
    return asyncio.run(
        recorder.run(args, now=NOW, transport=httpx.MockTransport(network))
    )


def every_file(folder: Path) -> list[Path]:
    return sorted(path for path in folder.rglob("*") if path.is_file())


def test_records_every_source_in_the_replay_layout(
    tmp_path: Path, urllib_readers: list[str]
) -> None:
    out = tmp_path / "snap"
    network = FakeNetwork()
    code = record(["--out", str(out), "--cameras-from", "http://api.test"], network)
    assert code == 0
    names = {path.relative_to(out).as_posix() for path in every_file(out)}
    assert names == {
        "manifest.json",
        "lrt/outages.json",
        "lrt/devices.json",
        "lrt/stations.json",
        "city_advisories/snow.json",
        "city_advisories/trails.json",
        "snow_clearing/na.json",
        "traffic_disruptions/current.json",
        "construction/permits.json",
        "weather/eccc.json",
        "weather/open_meteo.json",
        "cameras/readings.json",
    }
    # Recorded rows are the responses, unchanged.
    assert json.loads((out / "lrt/outages.json").read_text()) == fixture(
        "lrt/outages.json"
    )
    assert json.loads((out / "traffic_disruptions/current.json").read_text()) == (
        fixture("traffic_disruptions/current.json")
    )
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["recorded_at"] == "2026-11-07T21:30:00Z"
    assert manifest["replay_at"] == "2026-11-07T21:30:00Z"
    assert manifest["no_images"] is True and manifest["socrata_token_used"] is False
    assert set(manifest["sources"]) == {
        "lrt",
        "snow_complaints",
        "trail_closures",
        "snow_clearing",
        "construction",
        "traffic_disruptions",
        "weather",
        "cameras",
    }
    assert all(entry["status"] == "ok" for entry in manifest["sources"].values())
    outages = manifest["files"]["lrt/outages.json"]
    assert outages["rows"] == len(fixture("lrt/outages.json"))
    assert len(outages["sha256"]) == 64
    assert manifest["files"]["cameras/readings.json"]["rows"] == 1


def test_a_recording_is_a_drop_in_replay_snapshot(
    tmp_path: Path, urllib_readers: list[str]
) -> None:
    out = tmp_path / "snap"
    assert record(["--out", str(out)], FakeNetwork()) == 0
    check = json.loads((out / "manifest.json").read_text())["replay_check"]
    # Every source built from the new folder, offline, with the recorded items.
    assert check["city_lrt_devices"] >= 1
    assert check["city_traffic_disruptions"] >= 1
    assert isinstance(check["city_construction"], int)
    for name in ("city_311_snow", "city_trail_closures", "city_snow_clearing"):
        assert isinstance(check[name], int), check[name]
    assert check["weather_watch"]["mode"] == "replay"
    assert check["weather_watch"]["observed_at"] == "2026-11-07T21:00:00Z"


def test_a_failing_source_is_reported_and_the_others_still_record(
    tmp_path: Path, urllib_readers: list[str]
) -> None:
    out = tmp_path / "snap"
    code = record(["--out", str(out)], FakeNetwork(fail={"k4mi-dkvi"}))
    assert code == 1  # honest: the run failed, but it wrote what it could
    manifest = json.loads((out / "manifest.json").read_text())
    trails = manifest["sources"]["trail_closures"]
    assert trails["status"] == "failed" and "404" in trails["error"]
    assert not (out / "city_advisories/trails.json").exists()
    assert (out / "city_advisories/snow.json").exists()
    ok = [key for key, entry in manifest["sources"].items() if entry["status"] == "ok"]
    assert len(ok) == 6
    assert "city_trail_closures" not in manifest["replay_check"]


def test_only_json_is_stored_never_an_image(
    tmp_path: Path, urllib_readers: list[str]
) -> None:
    out = tmp_path / "snap"
    network = FakeNetwork(camera_image=True)
    code = record(["--out", str(out), "--cameras-from", "http://api.test"], network)
    assert code == 1
    manifest = json.loads((out / "manifest.json").read_text())
    assert "only JSON" in manifest["sources"]["cameras"]["error"]
    assert not (out / "cameras").exists()
    files = every_file(out)
    assert all(path.suffix == ".json" for path in files)
    assert not any(path.suffix.lower() in IMAGE_SUFFIXES for path in files)
    for path in files:
        assert b"\xff\xd8\xff" not in path.read_bytes()


def test_capture_refuses_an_image_response() -> None:
    rec = recorder.Recorder(Path("unused"))
    request = httpx.Request("GET", "https://data.edmonton.ca/resource/k4tx-5k8p.json")
    image = httpx.Response(
        200, content=b"\x89PNG", headers={"content-type": "image/png"}, request=request
    )
    with pytest.raises(recorder.RecordingError, match="only JSON"):
        rec.capture(request, image)


def test_the_token_is_sent_but_never_written(
    tmp_path: Path, urllib_readers: list[str]
) -> None:
    out = tmp_path / "snap"
    network = FakeNetwork()
    assert record(["--out", str(out), "--token", TOKEN], network) == 0
    city = [r for r in network.requests if r.url.host == "data.edmonton.ca"]
    assert city and all(r.headers.get("X-App-Token") == TOKEN for r in city)
    for path in every_file(out):
        assert TOKEN not in path.read_text(encoding="utf-8"), path
    assert json.loads((out / "manifest.json").read_text())["socrata_token_used"]


def test_weather_archive_records_a_real_freeze_thaw_night(
    tmp_path: Path, urllib_readers: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "night"
    network = FakeNetwork()
    argv = ["--out", str(out), "--sources", "weather"]
    assert record([*argv, "--weather-archive", "2025-11-05"], network) == 0
    (request,) = network.requests
    assert request.url.host == "archive-api.open-meteo.com"
    params = request.url.params
    assert params["start_date"] == "2025-11-05" and params["end_date"] == "2025-11-06"
    assert params["timezone"] == "UTC"
    assert set(params["hourly"].split(",")) == {
        "temperature_2m",
        "dew_point_2m",
        "precipitation",
        "soil_temperature_0_to_7cm",
    }
    names = {path.relative_to(out).as_posix() for path in every_file(out)}
    assert names == {
        "manifest.json",
        "weather/open_meteo_archive.json",
        "weather/manifest.json",
    }
    manifest = json.loads((out / "manifest.json").read_text())
    # Only the night was recorded, so a replay starts at its first watch hour.
    assert manifest["replay_at"] == "2025-11-06T12:00:00Z"
    watch = manifest["replay_check"]["weather_watch"]
    assert watch["active"] is True and watch["mode"] == "replay"
    night = json.loads((out / "weather/manifest.json").read_text())
    assert night["demo_hour"]["air_c"] == -0.1
    assert night["demo_hour"]["precipitation_last_6h_mm"] == 3.5


def test_weather_archive_refuses_a_night_without_a_watch(
    tmp_path: Path, urllib_readers: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    warm = fixture("weather/freeze_thaw/open_meteo_archive.json")
    hours = len(warm["hourly"]["time"])
    warm["hourly"]["temperature_2m"] = [12.0] * hours
    warm["hourly"]["soil_temperature_0_to_7cm"] = [9.0] * hours

    class WarmNight(FakeNetwork):
        def __call__(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=warm)

    out = tmp_path / "warm"
    argv = [
        "--out",
        str(out),
        "--sources",
        "weather",
        "--weather-archive",
        "2025-07-01",
    ]
    assert record(argv, WarmNight()) == 1
    manifest = json.loads((out / "manifest.json").read_text())
    assert "never fires" in manifest["sources"]["weather"]["error"]
    assert not (out / "weather").exists()


def test_refuses_to_overwrite_a_recording(
    tmp_path: Path, urllib_readers: list[str]
) -> None:
    out = tmp_path / "snap"
    out.mkdir()
    (out / "keep.json").write_text("[]")
    assert record(["--out", str(out)], FakeNetwork()) == 2
    assert [path.name for path in every_file(out)] == ["keep.json"]


def test_unknown_source_keys_are_refused(tmp_path: Path) -> None:
    assert (
        record(["--out", str(tmp_path / "x"), "--sources", "nope"], FakeNetwork()) == 2
    )
