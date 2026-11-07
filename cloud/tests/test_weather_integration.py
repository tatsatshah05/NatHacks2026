"""Purpose: check how the weather modules work together without live APIs.

Recorded responses test parsing; fake HTTP failures test retries and caching.
Endpoint tests check the response fields, replay mode, and unavailable/stale data.
"""

import asyncio
import copy
import json
import logging
import os
import shutil
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from cloud.app.adapters.weather import WeatherSources, normalize_weather, timestamp
from cloud.app.weather_app import create_app
from cloud.app.weather_service import REFRESH_SECONDS, WeatherResponse, WeatherService

FIXTURES = Path(__file__).parent / "fixtures/weather"


@pytest.fixture
def sources():
    eccc = json.loads((FIXTURES / "eccc.json").read_text())
    meteo = json.loads((FIXTURES / "open_meteo.json").read_text())
    now = timestamp(eccc["features"][0]["properties"]["obs_date_tm"])
    return eccc, meteo, now


def test_recorded_responses_use_station_air_and_model_ground(sources):
    eccc, meteo, now = sources
    actual = normalize_weather(eccc, meteo, now)
    i = meteo["hourly"]["time"].index(now.strftime("%Y-%m-%dT%H:00"))
    assert actual["air_c"] == eccc["features"][0]["properties"]["air_temp"]
    assert actual["dew_c"] == eccc["features"][0]["properties"]["dwpt_temp"]
    assert actual["ground_c"] == meteo["hourly"]["soil_temperature_0cm"][i]
    assert actual["precipitation_last_6h_mm"] == sum(
        meteo["hourly"]["precipitation"][i - 5 : i + 1]
    )
    assert actual["observed_at"] == now


def test_future_values_and_old_rain_do_not_trigger_watch(sources):
    eccc, meteo, now = sources
    i = meteo["hourly"]["time"].index(now.strftime("%Y-%m-%dT%H:00"))
    for field in ("temperature_2m", "soil_temperature_0cm"):
        meteo["hourly"][field] = [-4.0] * len(meteo["hourly"]["time"])
    meteo["hourly"]["precipitation"] = [0.0] * len(meteo["hourly"]["time"])
    meteo["hourly"]["precipitation"][i - 6] = 99.0
    meteo["hourly"]["precipitation"][i + 1] = 99.0
    meteo["hourly"]["temperature_2m"][i + 1] = 99.0
    props = eccc["features"][0]["properties"]
    props["air_temp"], props["dwpt_temp"] = -4.0, -10.0
    actual = normalize_weather(eccc, meteo, now)
    assert actual["precipitation_last_6h_mm"] == 0
    assert actual["max_air_last_12h_c"] == -4


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_air",
        "null_ground",
        "history_gap",
        "wrong_units",
        "wrong_station",
        "bad_quality",
        "duplicate_time",
        "array_length",
        "negative_rain",
        "wrong_timezone",
    ],
)
def test_rejects_incomplete_or_invalid_sources(sources, mutation):
    eccc, meteo, now = sources
    props = eccc["features"][0]["properties"]
    i = meteo["hourly"]["time"].index(now.strftime("%Y-%m-%dT%H:00"))
    if mutation == "missing_air":
        del props["air_temp"]
    elif mutation == "null_ground":
        meteo["hourly"]["soil_temperature_0cm"][i] = None
    elif mutation == "history_gap":
        meteo["hourly"]["time"][i - 11] = "2000-01-01T00:00"
    elif mutation == "wrong_units":
        meteo["hourly_units"]["temperature_2m"] = "°F"
    elif mutation == "wrong_station":
        props["msc_id-value"] = "other"
    elif mutation == "bad_quality":
        props["air_temp-qa"] = 0
    elif mutation == "duplicate_time":
        meteo["hourly"]["time"][0] = meteo["hourly"]["time"][1]
    elif mutation == "array_length":
        meteo["hourly"]["precipitation"].pop()
    elif mutation == "negative_rain":
        meteo["hourly"]["precipitation"][i] = -1
    else:
        meteo["utc_offset_seconds"] = -25200
    with pytest.raises((ValueError, KeyError, TypeError)):
        normalize_weather(eccc, meteo, now)


@pytest.mark.parametrize("offset", [-1, 121])
def test_rejects_future_or_stale_observations(sources, offset):
    eccc, meteo, now = sources
    with pytest.raises(ValueError, match="No recent"):
        normalize_weather(eccc, meteo, now + timedelta(minutes=offset))


def test_fetches_official_sources_with_history_and_station_filter(sources):
    eccc, meteo, now = sources
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200, json=eccc if "weather.gc.ca" in request.url.host else meteo
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await WeatherSources(client)(now)

    assert asyncio.run(run())["observed_at"] == now
    assert requests[0].url.params["msc_id-value"] == "3012209"
    assert requests[0].url.params["sortby"] == "-obs_date_tm"
    assert requests[1].url.params["past_days"] == "1"
    assert requests[1].url.params["timezone"] == "UTC"


def test_failed_http_requests_retry_three_times(sources):
    _, _, now = sources
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(503)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with patch(
                "cloud.app.adapters.weather.asyncio.sleep", new_callable=AsyncMock
            ) as sleep:
                with pytest.raises(httpx.HTTPStatusError):
                    await WeatherSources(client)(now)
                # Both providers have their own retry budget when fetched together.
                assert sorted(call.args[0] for call in sleep.await_args_list) == [
                    1,
                    1,
                    2,
                    2,
                    4,
                    4,
                ]

    asyncio.run(run())
    assert len(requests) == 8


def test_failed_refresh_preserves_result_and_recovers(sources):
    eccc, meteo, now = sources
    values = normalize_weather(eccc, meteo, now)
    fetch = AsyncMock(return_value=values)
    service = WeatherService(fetch, clock=lambda: now)

    async def run():
        assert await service.refresh()
        original = copy.deepcopy(service.last_good)
        fetch.side_effect = httpx.ReadTimeout("source timed out")
        assert not await service.refresh()
        assert service.last_good == original
        assert service.stale
        fetch.side_effect = None
        assert await service.refresh()
        assert not service.stale
        assert service.last_error is None

    asyncio.run(run())


def test_invalid_response_preserves_last_good(sources):
    eccc, meteo, now = sources
    fetch = AsyncMock(return_value=normalize_weather(eccc, meteo, now))
    service = WeatherService(fetch, clock=lambda: now)

    async def run():
        await service.refresh()
        old = copy.deepcopy(service.last_good)
        fetch.return_value = {"air_c": None}
        assert not await service.refresh()
        assert service.last_good == old

    asyncio.run(run())


def test_endpoint_contract_and_last_good_without_freshness_header(sources):
    eccc, meteo, now = sources
    service = WeatherService(
        AsyncMock(return_value=normalize_weather(eccc, meteo, now)), clock=lambda: now
    )
    asyncio.run(service.refresh())
    with TestClient(create_app(service)) as client:
        response = client.get("/api/weather")
        assert response.status_code == 200
        assert set(response.json()) == {
            "active",
            "reason",
            "air_c",
            "ground_c",
            "dew_c",
            "observed_at",
            "mode",
        }
        assert response.json()["mode"] == "live"
        assert "X-Weather-Stale" not in response.headers
        assert isinstance(service.last_good, WeatherResponse)
        service.last_error = "Source unavailable"
        assert service.stale
        assert "X-Weather-Stale" not in client.get("/api/weather").headers
        assert client.get("/api/weather").json() == response.json()


def test_no_success_returns_200_with_unknown_reading():
    service = WeatherService(AsyncMock(side_effect=ValueError("Missing data")))
    with TestClient(create_app(service)) as client:
        response = client.get("/api/weather")
        assert response.status_code == 200
        assert response.json() == WeatherResponse().model_dump(mode="json")
        assert response.json()["active"] is None
        assert "X-Weather-Stale" not in response.headers
        assert "Retry-After" not in response.headers


def test_replay_mode_is_visible_and_app_cleans_up(monkeypatch, tmp_path, sources):
    eccc, meteo, _ = sources
    path = tmp_path / "shared-snapshot.json"
    path.write_text(json.dumps({"weather": {"eccc": eccc, "open_meteo": meteo}}))
    monkeypatch.setenv("DATA_MODE", "replay")
    monkeypatch.setenv("REPLAY_SNAPSHOT", str(path))
    with TestClient(create_app()) as client:
        client.portal.call(client.app.state.weather_service.refresh)
        assert client.get("/api/weather").json()["mode"] == "replay"


def test_replay_requires_explicit_snapshot_path(monkeypatch):
    monkeypatch.setenv("DATA_MODE", "replay")
    monkeypatch.delenv("REPLAY_SNAPSHOT", raising=False)
    with pytest.raises(ValueError, match="REPLAY_SNAPSHOT"), TestClient(create_app()):
        pass


def test_replay_deploy_without_tests_directory(tmp_path, sources):
    eccc, meteo, _ = sources
    app_dir = Path(__file__).parents[1] / "app"
    shutil.copytree(
        app_dir, tmp_path / "cloud/app", ignore=shutil.ignore_patterns("__pycache__")
    )
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(
        json.dumps({"weather": {"eccc": eccc, "open_meteo": meteo}})
    )
    script = """
from fastapi.testclient import TestClient
from cloud.app.weather_app import create_app
with TestClient(create_app()) as client:
    client.portal.call(client.app.state.weather_service.refresh)
    response = client.get('/api/weather')
    assert response.status_code == 200
    assert response.json()['mode'] == 'replay'
    assert response.json()['observed_at'] is not None
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env={
            **os.environ,
            "PYTHONPATH": str(tmp_path),
            "DATA_MODE": "replay",
            "REPLAY_SNAPSHOT": str(snapshot_path),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "cloud/tests").exists()


def test_response_model_validates_mode_and_observation_timezone():
    with pytest.raises(ValidationError):
        WeatherResponse(mode="unknown")
    with pytest.raises(ValidationError):
        WeatherResponse(observed_at="2026-11-07T12:00:00")
    with pytest.raises(ValidationError):
        WeatherResponse(air_c=float("nan"))


def test_logs_only_when_watch_state_changes(sources, caplog):
    eccc, meteo, now = sources
    initial = normalize_weather(eccc, meteo, now)
    fetch = AsyncMock(return_value=initial)
    service = WeatherService(fetch, clock=lambda: now)

    async def run():
        assert await service.refresh()
        assert await service.refresh()
        fetch.return_value = {
            **initial,
            "air_c": -1,
            "ground_c": -1,
            "precipitation_last_6h_mm": 1,
        }
        assert await service.refresh()
        assert await service.refresh()
        fetch.side_effect = httpx.ReadTimeout("Source timed out")
        assert not await service.refresh()

    with caplog.at_level(logging.INFO, logger="cloud.app.weather_service"):
        asyncio.run(run())
    changes = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("CHANGE weather watch")
    ]
    assert len(changes) == 2
    assert changes[0].startswith("CHANGE weather watch unknown->inactive ")
    assert changes[1].startswith("CHANGE weather watch inactive->active ")
    assert all("\n" not in line for line in changes)


@pytest.mark.parametrize("seconds,accepted", [(1, True), (5, True), (6, False)])
def test_station_clock_skew_tolerance(sources, seconds, accepted):
    eccc, meteo, observed = sources
    now = observed - timedelta(seconds=seconds)
    if accepted:
        assert normalize_weather(eccc, meteo, now)["observed_at"] == observed
    else:
        with pytest.raises(ValueError, match="No recent"):
            normalize_weather(eccc, meteo, now)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429])
def test_client_errors_are_not_retried(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await WeatherSources(client)._get("https://example.test/weather", {})

    asyncio.run(run())
    assert len(calls) == 1


def test_invalid_json_is_not_retried():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content="invalid JSON")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError):
                await WeatherSources(client)._get("https://example.test/weather", {})

    asyncio.run(run())
    assert len(calls) == 1


def test_timeouts_are_retried():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("Timed out")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with patch(
                "cloud.app.adapters.weather.asyncio.sleep", new_callable=AsyncMock
            ) as sleep:
                with pytest.raises(httpx.ReadTimeout):
                    await WeatherSources(client)._get(
                        "https://example.test/weather", {}
                    )
                assert [call.args[0] for call in sleep.await_args_list] == [1, 2, 4]

    asyncio.run(run())
    assert len(calls) == 4


def test_providers_fetch_concurrently(sources):
    eccc, meteo, now = sources

    async def run():
        model_started = asyncio.Event()

        async def fake_get(url, params):
            if "weather.gc.ca" in url:
                await model_started.wait()
                return eccc
            model_started.set()
            return meteo

        source = WeatherSources(None)
        with patch.object(source, "_get", side_effect=fake_get):
            result = await asyncio.wait_for(source(now), timeout=1)
        assert result["observed_at"] == now

    asyncio.run(run())


def test_failed_provider_cancels_and_awaits_peer(sources):
    _, _, now = sources

    async def run():
        model_started = asyncio.Event()
        peer_cancelled = asyncio.Event()

        async def fake_get(url, params):
            if "weather.gc.ca" in url:
                await model_started.wait()
                raise httpx.ReadTimeout("Station unavailable")
            model_started.set()
            try:
                await asyncio.Future()
            finally:
                peer_cancelled.set()

        source = WeatherSources(None)
        with (
            patch.object(source, "_get", side_effect=fake_get),
            pytest.raises(httpx.ReadTimeout),
        ):
            await asyncio.wait_for(source(now), timeout=1)
        assert peer_cancelled.is_set()

    asyncio.run(run())


def test_refresh_interval_and_cancellation(sources):
    eccc, meteo, now = sources
    fetch = AsyncMock(return_value=normalize_weather(eccc, meteo, now))
    service = WeatherService(fetch, clock=lambda: now)

    async def run():
        with patch(
            "cloud.app.weather_service.asyncio.sleep", new_callable=AsyncMock
        ) as sleep:
            sleep.side_effect = asyncio.CancelledError
            with pytest.raises(asyncio.CancelledError):
                await service.run()
            sleep.assert_awaited_once_with(600)
        service.clock = lambda: now + timedelta(seconds=REFRESH_SECONDS * 3 + 1)
        assert service.stale

    asyncio.run(run())
