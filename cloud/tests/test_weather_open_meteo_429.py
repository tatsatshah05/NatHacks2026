"""Purpose: keep the black-ice watch working when Open-Meteo rate-limits.

On November 8, 2026 Open-Meteo answered 429 to every request from Render, and one
429 failed the whole weather refresh. These tests drive the adapter through a fake
HTTP transport with the recorded Environment Canada response and a 429 body (with
and without Retry-After), on a fake clock. Nothing touches the network.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from cloud.app import sources as src
from cloud.app.adapters._retry import retry_after_s
from cloud.app.adapters.weather import (
    MODEL_BACKOFF_FIRST_S,
    MODEL_BACKOFF_MAX_S,
    WeatherSources,
    hour_of,
    normalize_weather,
    timestamp,
)
from cloud.app.scheduler import Scheduler
from cloud.app.weather_app import create_app
from cloud.app.weather_service import WeatherService

FIXTURES = Path(__file__).parent / "fixtures/weather"
ECCC = json.loads((FIXTURES / "eccc.json").read_text())
METEO = json.loads((FIXTURES / "open_meteo.json").read_text())
LIMITED = json.loads((FIXTURES / "open_meteo_429.json").read_text())
RECORDED = timestamp(ECCC["features"][0]["properties"]["obs_date_tm"])
ADAPTER_LOG = "cloud.app.adapters.weather"
WATCH_NOTE = "Open-Meteo is unavailable and the ground temperature is unknown; "


def station(
    observations: list[tuple[datetime, float, float, float | None]],
) -> dict[str, Any]:
    """An Environment Canada response built from the recorded feature.

    Each tuple is (observed_at, air_c, dew_c, precipitation_mm); None marks the
    hour's precipitation as flagged by the station, which the adapter must treat
    as unknown.
    """
    template = ECCC["features"][0]
    features = []
    for observed_at, air_c, dew_c, rain_mm in observations:
        feature = copy.deepcopy(template)
        props = feature["properties"]
        props["obs_date_tm"] = observed_at.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        props["air_temp"] = air_c
        props["dwpt_temp"] = dew_c
        if rain_mm is None:
            props["pcpn_amt_pst1hr-qa"] = 0
        else:
            props["pcpn_amt_pst1hr"] = rain_mm
        features.append(feature)
    return {**ECCC, "features": features, "numberReturned": len(features)}


def model(end: datetime, *, air_c=-4.0, ground_c=-1.0, rain_mm=0.0) -> dict[str, Any]:
    """An Open-Meteo response in the recorded shape covering 48 hours up to ``end``."""
    hours = [end - timedelta(hours=h) for h in range(47, -1, -1)]
    return {
        **{k: v for k, v in METEO.items() if k not in ("hourly", "hourly_units")},
        "hourly_units": dict(METEO["hourly_units"]),
        "hourly": {
            "time": [hour.strftime("%Y-%m-%dT%H:%M") for hour in hours],
            "temperature_2m": [air_c] * 48,
            "dew_point_2m": [air_c - 3] * 48,
            "precipitation": [rain_mm] * 48,
            "soil_temperature_0cm": [ground_c] * 48,
        },
    }


class Providers:
    """Both providers behind one fake transport, each switchable per test."""

    def __init__(self, clock: Callable[[], datetime]) -> None:
        self.clock = clock
        self.eccc: dict[str, Any] | None = None  # None: the recorded fixture
        self.eccc_status = 200
        self.meteo: dict[str, Any] | None = None  # None: a model built for now
        self.model_status = 200
        self.retry_after: str | None = None
        self.model_content: bytes | None = None  # a 200 with this raw body
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if "weather.gc.ca" in request.url.host:
            if self.eccc_status != 200:
                return httpx.Response(self.eccc_status)
            return httpx.Response(200, json=self.eccc or ECCC)
        if self.model_status == 200:
            if self.model_content is not None:
                return httpx.Response(200, content=self.model_content)
            return httpx.Response(
                200,
                json=self.meteo or model(hour_of(self.clock()) + timedelta(hours=23)),
            )
        headers = {"Retry-After": self.retry_after} if self.retry_after else {}
        if self.model_status == 429:
            return httpx.Response(429, json=LIMITED, headers=headers)
        return httpx.Response(self.model_status, headers=headers)

    def model_calls(self) -> int:
        return sum("open-meteo" in request.url.host for request in self.requests)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


def run(coro):
    return asyncio.run(coro)


def lines(caplog, prefix: str) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith(prefix)
    ]


# -- both providers healthy: nothing changes ------------------------------------


def test_both_sources_healthy_give_the_same_readings_as_before(caplog) -> None:
    providers = Providers(lambda: RECORDED)
    providers.meteo = METEO

    async def scenario():
        async with providers.client() as client:
            source = WeatherSources(client)
            readings = await source(RECORDED)
            service = WeatherService(source, clock=lambda: RECORDED)
            assert await service.refresh()
            return source, readings, service

    with caplog.at_level(logging.INFO, logger=ADAPTER_LOG):
        source, readings, service = run(scenario())
    assert readings == normalize_weather(ECCC, METEO, RECORDED)
    assert "note" not in readings and "warning" not in readings
    assert source.model_error is None and source.model_retry_at is None
    assert service.warning is None and service.last_error is None
    assert service.last_good is not None
    assert service.last_good.reason == "Air and ground are above freezing."
    assert not lines(caplog, "WARN weather open_meteo") and not lines(
        caplog, "CHANGE weather open_meteo"
    )


# -- a 429 no longer fails the refresh ------------------------------------------


@pytest.mark.parametrize(
    "retry_after,expected_wait_s",
    [(None, MODEL_BACKOFF_FIRST_S), ("120", 120.0)],
)
def test_open_meteo_429_keeps_the_watch_and_the_endpoint(
    caplog, retry_after: str | None, expected_wait_s: float
) -> None:
    providers = Providers(lambda: RECORDED)
    providers.model_status = 429
    providers.retry_after = retry_after

    async def scenario():
        async with providers.client() as client:
            source = WeatherSources(client)
            service = WeatherService(source, clock=lambda: RECORDED)
            assert await service.refresh()
            return source, service

    with caplog.at_level(logging.INFO, logger=ADAPTER_LOG):
        source, service = run(scenario())
    next_try = RECORDED + timedelta(seconds=expected_wait_s)
    assert source.model_retry_at == next_try
    assert providers.model_calls() == 1
    assert service.last_error is None and not service.stale
    assert service.warning is not None
    assert service.warning.startswith("open_meteo: HTTP 429 (Minutely API request")
    assert service.warning.endswith(
        "next try at " + next_try.strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    reading = service.last_good
    assert reading is not None
    assert reading.active is False
    assert reading.air_c == 10.0 and reading.dew_c == -5.5
    assert reading.ground_c is None  # Open-Meteo only
    assert reading.observed_at == RECORDED
    assert reading.reason == (
        "Air is above freezing. " + WATCH_NOTE + "the history covers 1 of the last "
        "12 hours (1 of 6 for precipitation) from Blatchford station observations."
    )
    # The standalone app starts the service's own refresh loop; its fetch now fails
    # (the fake client is closed), so the endpoint serves the last good reading.
    service.fetch = AsyncMock(side_effect=httpx.ReadTimeout("client closed"))
    with TestClient(create_app(service)) as client:
        body = client.get("/api/weather").json()
    assert set(body) == {
        "active",
        "reason",
        "air_c",
        "ground_c",
        "dew_c",
        "observed_at",
        "mode",
    }
    assert body["air_c"] == 10.0 and body["ground_c"] is None
    assert body["reason"] == reading.reason
    warned = lines(caplog, "WARN weather open_meteo: 429, next try at ")
    assert warned == [
        "WARN weather open_meteo: 429, next try at "
        + next_try.strftime("%Y-%m-%dT%H:%M:%SZ")
        + " ("
        + LIMITED["reason"]
        + ")"
    ]


def test_backoff_waits_doubles_to_the_cap_and_resets_on_200(caplog) -> None:
    start = datetime(2026, 11, 7, 21, 0, tzinfo=UTC)
    clock = [start]
    providers = Providers(lambda: clock[0])
    providers.model_status = 429

    def observe() -> None:
        providers.eccc = station([(hour_of(clock[0]), -2.0, -8.0, 0.0)])

    async def scenario():
        async with providers.client() as client:
            source = WeatherSources(client)
            # One 429 after another: 10 min, 20, 40, 60, 60. No call during a backoff.
            expected = [600.0, 1200.0, 2400.0, 3600.0, 3600.0]
            assert expected[-1] == MODEL_BACKOFF_MAX_S
            for calls, wait_s in enumerate(expected, start=1):
                observe()
                readings = await source(clock[0])
                assert providers.model_calls() == calls
                assert source.model_retry_at == clock[0] + timedelta(seconds=wait_s)
                assert readings["warning"].startswith("open_meteo: HTTP 429")
                clock[0] += timedelta(seconds=wait_s / 2)
                observe()
                readings = await source(clock[0])  # inside the backoff: no call
                assert providers.model_calls() == calls
                assert readings["warning"].startswith("open_meteo: HTTP 429")
                clock[0] += timedelta(seconds=wait_s / 2)
            # The next 200 resets everything, and a later 429 starts at 10 min again.
            providers.model_status = 200
            observe()
            readings = await source(clock[0])
            assert providers.model_calls() == len(expected) + 1
            assert "warning" not in readings and readings["ground_c"] == -1.0
            assert source.model_error is None and source.model_retry_at is None
            clock[0] += timedelta(minutes=10)
            providers.model_status = 429
            observe()
            await source(clock[0])
            assert source.model_retry_at == clock[0] + timedelta(
                seconds=MODEL_BACKOFF_FIRST_S
            )

    with caplog.at_level(logging.INFO, logger=ADAPTER_LOG):
        run(scenario())
    assert len(lines(caplog, "WARN weather open_meteo: 429, next try at ")) == 6
    assert lines(caplog, "CHANGE weather open_meteo") == [
        "CHANGE weather open_meteo: back"
    ]


def test_retry_after_http_date_is_honoured() -> None:
    providers = Providers(lambda: RECORDED)
    providers.model_status = 429
    providers.retry_after = (RECORDED + timedelta(minutes=5)).strftime(
        "%a, %d %b %Y %H:%M:%S GMT"
    )

    async def scenario():
        async with providers.client() as client:
            source = WeatherSources(client)
            await source(RECORDED)
            return source.model_retry_at

    assert run(scenario()) == RECORDED + timedelta(minutes=5)
    assert retry_after_s("90", RECORDED) == 90.0
    assert retry_after_s("invalid", RECORDED) is None
    assert retry_after_s(None, RECORDED) is None


def test_other_open_meteo_failures_do_not_fail_the_refresh_or_back_off(
    caplog,
) -> None:
    providers = Providers(lambda: RECORDED)

    async def scenario():
        async with providers.client() as client:
            source = WeatherSources(client)
            with patch(f"{ADAPTER_LOG}.asyncio.sleep", new_callable=AsyncMock):
                providers.model_status = 503
                first = await source(RECORDED)
                assert providers.model_calls() == 4  # the 5xx retries, as before
                providers.model_status = 200
                providers.model_content = b"not JSON"
                second = await source(RECORDED)
                assert providers.model_calls() == 5  # no backoff for these
                providers.model_content = None
                providers.meteo = {**METEO, "utc_offset_seconds": -25200}
                third = await source(RECORDED)
                assert providers.model_calls() == 6
                assert source.last_model is None  # unusable content is not kept
            return first, second, third

    with caplog.at_level(logging.INFO, logger=ADAPTER_LOG):
        first, second, third = run(scenario())
    assert first["warning"] == "open_meteo: HTTP 503"
    assert second["warning"].startswith("open_meteo: JSONDecodeError")
    assert (
        third["warning"] == "open_meteo: ValueError: Open-Meteo response must use UTC"
    )
    for readings in (first, second, third):
        assert readings["ground_c"] is None and readings["air_c"] == 10.0
        assert readings["note"].startswith(WATCH_NOTE)
    # One line when Open-Meteo starts failing, not one per refresh.
    assert lines(caplog, "WARN weather open_meteo: unavailable") == [
        "WARN weather open_meteo: unavailable (HTTP 503)"
    ]


def test_eccc_failure_still_fails_the_refresh_even_with_open_meteo_limited() -> None:
    providers = Providers(lambda: RECORDED)
    providers.model_status = 429
    providers.eccc_status = 503

    async def scenario():
        async with providers.client() as client:
            source = WeatherSources(client)
            with patch(f"{ADAPTER_LOG}.asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(httpx.HTTPStatusError, match="503"):
                    await source(RECORDED)
                service = WeatherService(source, clock=lambda: RECORDED)
                assert not await service.refresh()
                assert service.last_good is None and service.stale
                assert service.last_error is not None and "503" in service.last_error

    run(scenario())


# -- the rule on the station alone -------------------------------------------------


def station_only(
    observations: list[tuple[datetime, float, float, float | None]],
    now: datetime,
) -> dict[str, Any]:
    providers = Providers(lambda: now)
    providers.model_status = 429
    providers.eccc = station(observations)

    async def scenario():
        async with providers.client() as client:
            source = WeatherSources(client)
            service = WeatherService(source, clock=lambda: now)
            assert await service.refresh()
            assert service.last_good is not None
            return service.last_good.model_dump()

    return run(scenario())


HOUR = datetime(2026, 11, 7, 21, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "observations,active,reason,history",
    [
        (
            [(HOUR, -2.0, -2.5, 0.0)],
            True,
            "Ice risk: freezing conditions with air temperature within 1 C of the dew point.",
            "1 of the last 12 hours (1 of 6 for precipitation)",
        ),
        (
            [(HOUR, -2.0, -8.0, 0.0), (HOUR - timedelta(hours=1), -1.0, -8.0, 1.2)],
            True,
            "Ice risk: freezing conditions with precipitation in the last 6 hours.",
            "2 of the last 12 hours (2 of 6 for precipitation)",
        ),
        (
            [(HOUR, -2.0, -8.0, 0.0)],
            False,
            "Freezing conditions without a watch trigger.",
            "1 of the last 12 hours (1 of 6 for precipitation)",
        ),
        (
            [(HOUR, -2.0, -8.0, None)],
            False,
            "Freezing conditions without a watch trigger.",
            "1 of the last 12 hours (0 of 6 for precipitation)",
        ),
        (
            [(HOUR, 3.0, 2.5, 2.0)],
            False,
            "Air is above freezing.",
            "1 of the last 12 hours (1 of 6 for precipitation)",
        ),
    ],
    ids=[
        "frost_dew_point",
        "precipitation_from_station_history",
        "freezing_without_history",
        "flagged_precipitation_is_unknown_not_zero",
        "wet_without_freezing_is_ignored",
    ],
)
def test_station_only_watch_branches(observations, active, reason, history) -> None:
    reading = station_only(observations, HOUR + timedelta(minutes=10))
    assert reading["active"] is active
    assert reading["ground_c"] is None
    assert reading["reason"] == (
        f"{reason} {WATCH_NOTE}the history covers {history} from Blatchford "
        "station observations."
    )
    assert "black ice" not in reading["reason"].lower()


def test_melt_and_refreeze_comes_from_the_stations_own_log() -> None:
    clock = [HOUR + timedelta(minutes=10)]
    providers = Providers(lambda: clock[0])
    providers.model_status = 429

    async def scenario():
        async with providers.client() as client:
            source = WeatherSources(client)
            service = WeatherService(source, clock=lambda: clock[0])
            providers.eccc = station([(HOUR, 1.0, -8.0, 0.0)])
            assert await service.refresh()
            assert service.last_good.active is False
            clock[0] += timedelta(hours=1)
            providers.eccc = station([(HOUR + timedelta(hours=1), -1.0, -8.0, 0.0)])
            assert await service.refresh()
            return service.last_good

    reading = run(scenario())
    assert reading.active is True
    assert reading.reason.startswith(
        "Ice risk: freezing conditions with melt and refreeze conditions in the "
        "last 12 hours. " + WATCH_NOTE + "the history covers 2 of the last 12 hours"
    )


# -- the last good Open-Meteo data -----------------------------------------------------


def test_last_good_open_meteo_history_is_used_under_24_hours() -> None:
    t0 = datetime(2026, 11, 7, 21, 5, tzinfo=UTC)
    clock = [t0]
    providers = Providers(lambda: clock[0])
    providers.meteo = model(hour_of(t0) + timedelta(hours=23), rain_mm=0.5)

    async def scenario():
        async with providers.client() as client:
            source = WeatherSources(client)
            providers.eccc = station([(hour_of(t0), -3.0, -9.0, 0.0)])
            healthy = await source(clock[0])
            assert "note" not in healthy
            assert healthy["precipitation_last_6h_mm"] == pytest.approx(3.0)
            # Ten minutes later Open-Meteo is limited; the same hour's model data
            # still gives the ground temperature and the whole history.
            clock[0] += timedelta(minutes=10)
            providers.model_status = 429
            same_hour = await source(clock[0])
            # An hour later the observation hour is after the fetch: no ground
            # value (that would be a forecast); the history mixes both sources.
            clock[0] += timedelta(hours=1)
            at = hour_of(clock[0])
            providers.eccc = station([(at, -3.0, -9.0, 0.0)])
            next_hour = await source(clock[0])
            # After 24 hours the old response is not used at all.
            clock[0] = t0 + timedelta(hours=25)
            at = hour_of(clock[0])
            providers.eccc = station([(at, -3.0, -9.0, 0.0)])
            day_later = await source(clock[0])
            return same_hour, next_hour, day_later

    same_hour, next_hour, day_later = run(scenario())
    assert same_hour["ground_c"] == -1.0
    assert same_hour["precipitation_last_6h_mm"] == pytest.approx(3.0)
    assert same_hour["note"] == (
        "Open-Meteo is unavailable; the history covers 12 of the last 12 hours "
        "(6 of 6 for precipitation) from Open-Meteo data from 21:05 UTC."
    )
    assert next_hour["ground_c"] is None
    assert next_hour["precipitation_last_6h_mm"] == pytest.approx(2.5)  # 5 x 0.5 + 0
    assert next_hour["note"] == (
        WATCH_NOTE + "the history covers 12 of the last 12 hours (6 of 6 for "
        "precipitation) from Open-Meteo data from 21:05 UTC and Blatchford "
        "station observations."
    )
    assert day_later["ground_c"] is None
    assert day_later["precipitation_last_6h_mm"] == 0.0
    assert day_later["note"] == (
        WATCH_NOTE + "the history covers 1 of the last 12 hours (1 of 6 for "
        "precipitation) from Blatchford station observations."
    )


def test_station_log_keeps_twelve_hours_in_memory() -> None:
    clock = [HOUR + timedelta(minutes=5)]
    providers = Providers(lambda: clock[0])
    providers.model_status = 429

    async def scenario():
        async with providers.client() as client:
            source = WeatherSources(client)
            for _ in range(16):
                at = hour_of(clock[0])
                providers.eccc = station([(at, -2.0, -8.0, 0.0)])
                readings = await source(clock[0])
                clock[0] += timedelta(hours=1)
            return source, readings

    source, readings = run(scenario())
    assert len(source.station_log) == 13  # this hour and the 12 before it
    assert min(source.station_log) == hour_of(
        clock[0] - timedelta(hours=1)
    ) - timedelta(hours=12)
    assert "the history covers 12 of the last 12 hours" in readings["note"]


# -- /api/status ---------------------------------------------------------------------


def test_status_stays_fresh_on_a_429_and_goes_stale_only_when_eccc_fails(
    store, caplog
) -> None:
    clock = [HOUR + timedelta(minutes=10)]
    providers = Providers(lambda: clock[0])
    providers.model_status = 429

    def refuse(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected request: {request.url}")

    async def scenario():
        async with providers.client() as client:
            with httpx.Client(transport=httpx.MockTransport(refuse)) as sync:
                service = WeatherService(WeatherSources(client), clock=lambda: clock[0])
                (source,) = src.build_sources(
                    mode="live",
                    snapshot=None,
                    app_token=None,
                    async_client=client,
                    sync_client=sync,
                    weather_service=service,
                    configs=[src.SourceConfig(key="weather")],
                )
                scheduler = Scheduler(
                    store, [source], clock=lambda: clock[0], backoff_s=(0,)
                )
                providers.eccc = station([(HOUR, -2.0, -2.5, 0.0)])
                with patch(f"{ADAPTER_LOG}.asyncio.sleep", new_callable=AsyncMock):
                    assert await scheduler.run_once(source)
                    limited = scheduler.status("weather_watch").model_copy()
                    good = service.last_good
                    providers.eccc_status = 503
                    for _ in range(3):
                        clock[0] += timedelta(minutes=10)
                        assert not await scheduler.run_once(source)
                    down = scheduler.status("weather_watch").model_copy()
                    assert service.last_good == good  # a failed source keeps its data
                    providers.eccc_status = 200
                    clock[0] += timedelta(minutes=10)
                    providers.eccc = station([(hour_of(clock[0]), -2.0, -2.5, 0.0)])
                    assert await scheduler.run_once(source)
                    back = scheduler.status("weather_watch").model_copy()
                return limited, down, back

    with caplog.at_level(logging.INFO):
        limited, down, back = run(scenario())
    assert limited.stale is False and limited.consecutive_failures == 0
    assert limited.last_success == HOUR + timedelta(minutes=10)
    assert limited.last_error is not None
    assert limited.last_error.startswith("open_meteo: HTTP 429")
    assert down.stale is True and down.consecutive_failures == 3
    assert down.last_error is not None and "503" in down.last_error
    assert back.stale is False and back.consecutive_failures == 0
    assert back.last_error is not None and back.last_error.startswith(
        "open_meteo: HTTP 429"
    )
    assert "SOURCE weather_watch stale" in [r.getMessage() for r in caplog.records]
