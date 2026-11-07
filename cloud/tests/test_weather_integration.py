"""Recorded-source, failure, retry, lifecycle and endpoint tests for OP-109."""

import asyncio
import copy
import json
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from cloud.app.adapters.weather import WeatherSources, normalize_weather, timestamp
from cloud.app.weather_app import create_app
from cloud.app.weather_service import REFRESH_SECONDS, WeatherService

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
    with pytest.raises((ValueError, KeyError)):
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
                assert [call.args[0] for call in sleep.await_args_list] == [1, 2, 4]

    asyncio.run(run())
    assert len(requests) == 4


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


def test_endpoint_contract_and_stale_header(sources):
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
        assert response.headers["X-Weather-Stale"] == "false"
        service.last_error = "Source unavailable"
        assert client.get("/api/weather").headers["X-Weather-Stale"] == "true"
        assert client.get("/api/weather").json() == response.json()


def test_no_success_returns_unavailable_not_clear():
    service = WeatherService(AsyncMock(side_effect=ValueError("Missing data")))
    with TestClient(create_app(service)) as client:
        response = client.get("/api/weather")
        assert response.status_code == 503
        assert "active" not in response.json()


def test_replay_mode_is_visible_and_app_cleans_up(monkeypatch):
    monkeypatch.setenv("DATA_MODE", "replay")
    with TestClient(create_app()) as client:
        asyncio.run(client.app.state.weather_service.refresh())
        assert client.get("/api/weather").json()["mode"] == "replay"


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
