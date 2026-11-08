"""OP-305: the replay clock, the freeze-thaw weather night and the replay labels.

The freeze-thaw recording (``fixtures/weather/freeze_thaw/``) is Open-Meteo's
archive for Edmonton on the night of November 5 to 6, 2025, recorded unchanged by
``scripts/record_snapshot.py --weather-archive 2025-11-05``. Rain fell from the
evening of November 5 and the air dropped below 0 °C at 05:00 MST on November 6
(12:00 UTC), so the black-ice watch fires from 12:00 to 18:00 UTC.
"""

from __future__ import annotations

import asyncio
import copy
import json
import shutil
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from cloud.app import sources as src
from cloud.app import weather_replay
from cloud.app.main import create_app
from cloud.app.schemas import Obstruction

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FREEZE_THAW = FIXTURES / "weather" / "freeze_thaw"
START = datetime(2026, 11, 7, 20, 10, tzinfo=UTC)  # conftest's NOW
DEMO = datetime(2025, 11, 6, 12, 0, tzinfo=UTC)  # first hour of the watch


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("SCHEDULER", "off")
    for name in (
        "DATA_MODE",
        "REPLAY_SNAPSHOT",
        "REPLAY_AT",
        "REPLAY_CLOCK",
        "WEATHER_REPLAY",
        "RENDER_GIT_COMMIT",
    ):
        monkeypatch.delenv(name, raising=False)


class FakeMonotonic:
    """A monotonic clock the test moves by hand."""

    def __init__(self) -> None:
        self.value = 1000.0

    def __call__(self) -> float:
        return self.value


@pytest.fixture
def ticks(monkeypatch: pytest.MonkeyPatch) -> FakeMonotonic:
    fake = FakeMonotonic()
    monkeypatch.setattr(src.time, "monotonic", fake)
    return fake


def archive() -> dict:
    return json.loads(
        (FREEZE_THAW / "open_meteo_archive.json").read_text(encoding="utf-8")
    )


# ------------------------------------------------------------------ the clock


def test_running_clock_advances_from_its_start() -> None:
    fake = FakeMonotonic()
    clock = src.running_clock(START, monotonic=fake)
    assert clock() == START
    fake.value += 90.5
    assert clock() == START + timedelta(seconds=90.5)


def test_replay_at_alone_still_freezes_the_clock(monkeypatch, ticks) -> None:
    monkeypatch.setenv("REPLAY_AT", "2026-11-07T20:10:00Z")
    clock = src.replay_clock(FIXTURES)
    ticks.value += 3600
    assert clock() == START  # frozen: an hour later it still reads REPLAY_AT


def test_running_clock_starts_at_replay_at(monkeypatch, ticks) -> None:
    monkeypatch.setenv("REPLAY_AT", "2026-11-07T13:10:00-07:00")  # any zone is fine
    monkeypatch.setenv("REPLAY_CLOCK", "running")
    clock = src.replay_clock(FIXTURES)
    assert clock() == START and clock().tzinfo is not None
    ticks.value += 600
    assert clock() == START + timedelta(minutes=10)


def test_running_clock_starts_at_the_manifest_time(
    monkeypatch, ticks, tmp_path: Path
) -> None:
    (tmp_path / "manifest.json").write_text(
        json.dumps({"recorded_at": "2026-11-08T16:00:00Z", "replay_at": iso(START)})
    )
    monkeypatch.setenv("REPLAY_CLOCK", "running")
    clock = src.replay_clock(tmp_path)
    assert clock() == START
    ticks.value += 30
    assert clock() == START + timedelta(seconds=30)
    # REPLAY_AT wins over the manifest.
    monkeypatch.setenv("REPLAY_AT", "2026-11-09T00:00:00Z")
    assert src.replay_clock(tmp_path)() == datetime(2026, 11, 9, tzinfo=UTC)


def test_frozen_clock_can_come_from_the_manifest(
    monkeypatch, ticks, tmp_path: Path
) -> None:
    (tmp_path / "manifest.json").write_text(json.dumps({"recorded_at": iso(START)}))
    monkeypatch.setenv("REPLAY_CLOCK", "frozen")
    clock = src.replay_clock(tmp_path)
    ticks.value += 600
    assert clock() == START


def test_running_clock_needs_a_start(monkeypatch) -> None:
    monkeypatch.setenv("REPLAY_CLOCK", "running")
    with pytest.raises(ValueError, match="REPLAY_AT or a manifest"):
        src.replay_clock(FIXTURES)  # the test fixtures have no manifest.json
    monkeypatch.setenv("REPLAY_CLOCK", "fast")
    with pytest.raises(ValueError, match="REPLAY_CLOCK"):
        src.replay_clock(FIXTURES)


def test_without_settings_replay_uses_the_real_clock() -> None:
    """``make dev`` sets neither REPLAY_AT nor REPLAY_CLOCK: nothing changes for it."""
    before = datetime.now(UTC)
    now = src.replay_clock(FIXTURES)()
    assert before <= now <= datetime.now(UTC)


def test_running_replay_ages_and_expires_items_as_live_would(
    monkeypatch, ticks, make_obstruction: Callable[..., Obstruction]
) -> None:
    """An item recorded with a 10-minute lifetime disappears 10 replayed minutes later."""
    monkeypatch.setenv("DATA_MODE", "replay")
    monkeypatch.setenv("REPLAY_SNAPSHOT", str(FIXTURES))
    monkeypatch.setenv("REPLAY_AT", iso(START))
    monkeypatch.setenv("REPLAY_CLOCK", "running")
    item = make_obstruction()  # observed at START, expires START + 10 min
    app = create_app()
    with TestClient(app) as client:
        app.state.store.upsert([item], START)
        first = client.get("/api/obstructions").json()
        ticks.value += 9 * 60
        later = client.get("/api/obstructions").json()
        ticks.value += 2 * 60
        expired = client.get("/api/obstructions").json()
    assert [f["id"] for f in first["features"]] == [item.id]
    assert first["generated_at"] == iso(START)
    assert later["generated_at"] == iso(START + timedelta(minutes=9))
    assert [f["id"] for f in later["features"]] == [item.id]
    assert expired["features"] == []  # expired on the replay clock


# ------------------------------------------------------- the freeze-thaw night


def test_the_recorded_night_fires_the_watch_at_the_documented_hour() -> None:
    data = archive()
    readings = weather_replay.normalize_archive(data, DEMO)
    assert readings == {
        "air_c": -0.1,
        "ground_c": 0.2,
        "dew_c": -0.4,
        "precipitation_last_6h_mm": 3.5,
        "max_air_last_12h_c": 3.0,
        "observed_at": DEMO,
    }
    table = weather_replay.watch_table(data)
    by_hour = {hour.at: hour for hour in table}
    assert by_hour[DEMO].active
    assert "precipitation in the last 6 hours" in by_hour[DEMO].reason
    assert "melt and refreeze" in by_hour[DEMO].reason
    # An hour earlier the air (0.5 °C) and ground (0.8 °C) were above freezing.
    assert not by_hour[DEMO - timedelta(hours=1)].active
    assert weather_replay.demo_hour(table) == DEMO
    night = [DEMO + timedelta(hours=h) for h in range(7)]  # 12:00 to 18:00 UTC
    assert all(by_hour[at].active for at in night)
    assert not by_hour[DEMO + timedelta(hours=7)].active  # 19:00: thawed again


def test_the_fixture_manifest_matches_the_rule() -> None:
    manifest = json.loads((FREEZE_THAW / "manifest.json").read_text(encoding="utf-8"))
    table = weather_replay.watch_table(archive())
    assert manifest["replay_at"] == iso(DEMO)
    assert manifest["watch_active_hours"] == [iso(h.at) for h in table if h.active]
    assert manifest["ground_field"] == "soil_temperature_0_to_7cm"
    assert "archive-api.open-meteo.com/v1/archive" in manifest["url"]


def test_a_night_without_a_watch_is_refused() -> None:
    data = archive()
    hourly = data["hourly"]
    hourly["temperature_2m"] = [10.0] * len(hourly["time"])
    hourly["soil_temperature_0_to_7cm"] = [8.0] * len(hourly["time"])
    with pytest.raises(ValueError, match="never fires"):
        weather_replay.demo_hour(weather_replay.watch_table(data))


@pytest.mark.parametrize(
    "mutation", ["timezone", "units", "gap", "length", "negative", "null"]
)
def test_archive_responses_are_validated(mutation: str) -> None:
    data = copy.deepcopy(archive())
    hourly, i = data["hourly"], data["hourly"]["time"].index("2025-11-06T12:00")
    if mutation == "timezone":
        data["utc_offset_seconds"] = -25200
    elif mutation == "units":
        data["hourly_units"]["temperature_2m"] = "°F"
    elif mutation == "gap":
        hourly["time"][i - 3] = "2000-01-01T00:00"
    elif mutation == "length":
        hourly["precipitation"].pop()
    elif mutation == "negative":
        hourly["precipitation"][i] = -1.0
    else:
        hourly["soil_temperature_0_to_7cm"][i] = None
    with pytest.raises((ValueError, TypeError)):
        weather_replay.normalize_archive(data, DEMO)


def weather_service(clock: Callable[[], datetime]):
    async def build():
        async with httpx.AsyncClient() as client:
            service = src.build_weather_service("replay", FIXTURES, client, clock)
            assert await service.refresh(), service.last_error
            return service.last_good

    return asyncio.run(build())


def test_weather_replay_switch_serves_the_hour_on_the_clock(monkeypatch) -> None:
    monkeypatch.setenv("WEATHER_REPLAY", "freeze_thaw")
    at_14 = weather_service(lambda: DEMO + timedelta(hours=2, minutes=25))
    assert at_14.active is True and at_14.mode == "replay"
    assert at_14.observed_at == DEMO + timedelta(hours=2)  # the archive hour read
    assert at_14.air_c == -0.3
    at_19 = weather_service(lambda: DEMO + timedelta(hours=7))
    assert at_19.active is False and at_19.mode == "replay"


def test_outside_the_night_the_demo_hour_is_served(monkeypatch) -> None:
    """``make dev`` runs on the real clock, a year after the night: show the watch."""
    monkeypatch.setenv("WEATHER_REPLAY", "freeze_thaw")
    reading = weather_service(lambda: datetime.now(UTC))
    assert reading.active is True and reading.mode == "replay"
    assert reading.observed_at == DEMO  # the real archive hour, never "now"


def test_default_weather_replay_is_unchanged() -> None:
    reading = weather_service(lambda: START)
    assert reading.observed_at == datetime(2026, 11, 7, 21, 0, tzinfo=UTC)
    assert reading.mode == "replay"


@pytest.mark.parametrize("value", ["../lrt", "Freeze-Thaw", "missing_night"])
def test_weather_replay_rejects_bad_names(monkeypatch, value: str) -> None:
    monkeypatch.setenv("WEATHER_REPLAY", value)
    with pytest.raises(ValueError, match="WEATHER_REPLAY"):
        src.replay_weather_folder(FIXTURES)


# -------------------------------------------- replay is labelled on every endpoint


def test_replay_endpoints_say_replay_with_the_freeze_thaw_night(monkeypatch) -> None:
    """``make dev`` plus WEATHER_REPLAY=freeze_thaw, with the scheduler running."""
    monkeypatch.setenv("DATA_MODE", "replay")
    monkeypatch.setenv("REPLAY_SNAPSHOT", str(FIXTURES))
    monkeypatch.setenv("SCHEDULER", "on")
    monkeypatch.setenv("WEATHER_REPLAY", "freeze_thaw")
    app = create_app()
    with TestClient(app) as client:
        assert client.portal.call(app.state.weather_service.refresh)
        health = client.get("/health").json()
        status = client.get("/api/status").json()
        weather = client.get("/api/weather").json()
        cameras = client.get("/api/cameras").json()
    assert health["mode"] == "replay"
    assert status["mode"] == "replay" and status["scheduler_running"] is True
    assert "weather_watch" in {s["source"] for s in status["sources"]}
    assert weather["mode"] == "replay" and weather["active"] is True
    assert weather["observed_at"] == iso(DEMO)
    assert cameras["features"] == []  # the test fixtures hold no camera readings


def test_recorded_camera_readings_load_as_replay(
    monkeypatch, tmp_path: Path, ticks
) -> None:
    snapshot = tmp_path / "snapshot"
    shutil.copytree(FIXTURES / "weather", snapshot / "weather")
    observed = START - timedelta(minutes=2)
    feature = {
        "type": "Feature",
        "id": "511:56:117",
        "geometry": {"type": "Point", "coordinates": [-113.49, 53.54]},
        "properties": {
            "source": "camera_model",
            "source_ref": "511:56:117",
            "camera_id": 56,
            "view_id": 117,
            "label": "wet",
            "score": 0.91,
            "reason": None,
            "observed_at": iso(observed),
            "expires_at": iso(observed + timedelta(minutes=15)),
            "age_s": 120.0,
            "model_version": "a" * 64,
            "mode": "live",  # as the live API served it
            "attribution": "Road camera data: 511 Alberta, Government of Alberta",
        },
    }
    broken = {"type": "Feature", "geometry": None, "properties": {}}
    (snapshot / "cameras").mkdir()
    (snapshot / "cameras" / "readings.json").write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "generated_at": iso(START),
                "features": [feature, broken],
            }
        )
    )
    monkeypatch.setenv("DATA_MODE", "replay")
    monkeypatch.setenv("REPLAY_SNAPSHOT", str(snapshot))
    monkeypatch.setenv("REPLAY_AT", iso(START))
    monkeypatch.setenv("REPLAY_CLOCK", "running")
    with TestClient(create_app()) as client:
        (shown,) = client.get("/api/cameras").json()["features"]
        ticks.value += 20 * 60  # past the reading's 15-minute lifetime
        later = client.get("/api/cameras").json()["features"]
    assert shown["id"] == "511:56:117"
    assert shown["properties"]["mode"] == "replay"  # never shown as live
    assert shown["properties"]["age_s"] == 120.0  # aged on the replay clock
    assert later == []


def iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
