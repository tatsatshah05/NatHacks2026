"""Purpose: turn two official weather responses into inputs for the ice rule.

Environment Canada supplies Blatchford's observed air and dew-point temperatures.
Open-Meteo supplies modelled ground temperature and recent hourly history.
Fetch both, validate their timestamps/units, then return one consistent reading.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import Any

import httpx

ECCC_URL = "https://api.weather.gc.ca/collections/swob-realtime/items"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
BLATCHFORD_ID = "3012209"
MAX_OBSERVATION_AGE = timedelta(hours=2)
CLOCK_SKEW = timedelta(seconds=5)


def timestamp(value: str) -> datetime:
    """Read provider timestamps and compare them in UTC."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)  # Open-Meteo requests UTC.
    return parsed.astimezone(UTC)


def number(value: Any) -> float:
    """Reject missing or unusable numbers instead of treating them as zero."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("Missing or nonnumeric weather value")
    if not isfinite(value):
        raise ValueError("Nonfinite weather value")
    return float(value)


def normalize_weather(
    eccc: dict[str, Any], meteo: dict[str, Any], now: datetime
) -> dict[str, Any]:
    """Combine station observations with modelled ground/history data.

    Reject incomplete, stale or incompatible responses rather than manufacturing
    a clear-weather result. Forecast hours after the station observation are
    excluded. Open-Meteo precipitation is a preceding-hour total.
    """
    # Use the newest recent observation from the correct station.
    candidates = []
    for feature in eccc["features"]:
        props = feature["properties"]
        if str(props.get("msc_id-value")) != BLATCHFORD_ID:
            continue
        observed = timestamp(props["obs_date_tm"])
        if -CLOCK_SKEW <= now - observed <= MAX_OBSERVATION_AGE:
            candidates.append((observed, props))
    if not candidates:
        raise ValueError("No recent Blatchford observation")
    observed, props = max(candidates, key=lambda pair: pair[0])
    for field in ("air_temp", "dwpt_temp"):
        if props.get(field + "-uom") != "°C":
            raise ValueError("Unexpected Environment Canada temperature units")
        if props.get(field + "-qa", 100) != 100:
            raise ValueError("Environment Canada flagged an invalid reading")
    air_c = number(props["air_temp"])
    dew_c = number(props["dwpt_temp"])

    # Check units before combining station readings with modelled data.
    units = meteo["hourly_units"]
    fields = ("temperature_2m", "soil_temperature_0cm", "precipitation")
    if meteo.get("utc_offset_seconds") != 0:
        raise ValueError("Open-Meteo response must use UTC")
    for field in fields:
        if units.get(field) != ("mm" if field == "precipitation" else "°C"):
            raise ValueError("Unexpected Open-Meteo units")
    hourly = meteo["hourly"]
    times = [timestamp(t) for t in hourly["time"]]
    if len(set(times)) != len(times):
        raise ValueError("Duplicate hourly timestamps")
    if any(len(hourly[field]) != len(times) for field in fields):
        raise ValueError("Mismatched hourly arrays")
    indices = {t: i for i, t in enumerate(times)}
    # Align the hourly history to the station reading, excluding future hours.
    anchor = observed.replace(minute=0, second=0, microsecond=0)

    def value_at(field: str, at: datetime) -> float:
        if at not in indices:
            raise ValueError("Incomplete hourly weather history")
        return number(hourly[field][indices[at]])

    # The rule checks melting over 12 hours and precipitation over 6 hours.
    temperatures = [
        value_at("temperature_2m", anchor - timedelta(hours=h)) for h in range(12)
    ]
    precipitation = [
        value_at("precipitation", anchor - timedelta(hours=h)) for h in range(6)
    ]
    if any(value < 0 for value in precipitation):
        raise ValueError("Negative precipitation")
    return {
        "air_c": air_c,
        "ground_c": value_at("soil_temperature_0cm", anchor),
        "dew_c": dew_c,
        "precipitation_last_6h_mm": sum(precipitation),
        "max_air_last_12h_c": max(air_c, *temperatures),
        "observed_at": observed,
    }


class WeatherSources:
    """Fetch both providers; tests can supply a fake HTTP client."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        latitude: float = 53.57,
        longitude: float = -113.52,
        retries: int = 3,
    ) -> None:
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise ValueError("Invalid weather coordinates")
        if retries < 0:
            raise ValueError("Retries cannot be negative")
        self.client = client
        self.latitude = latitude
        self.longitude = longitude
        self.retries = retries

    async def _get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        """Fetch JSON; retry timeouts and 5xx responses after 1, 2, then 4 seconds."""
        for attempt in range(self.retries + 1):
            try:
                response = await asyncio.wait_for(
                    self.client.get(url, params=params, timeout=10), timeout=10
                )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise TypeError("Expected a JSON object")
                return payload
            except httpx.HTTPStatusError as exc:
                if not 500 <= exc.response.status_code < 600 or attempt == self.retries:
                    raise
            except (httpx.TimeoutException, TimeoutError):
                if attempt == self.retries:
                    raise
            await asyncio.sleep(2**attempt)
        raise AssertionError("Unreachable")

    async def __call__(self, now: datetime) -> dict[str, Any]:
        """Fetch and combine readings for one refresh of the weather service."""
        station_params = {
            "f": "json",
            "msc_id-value": BLATCHFORD_ID,
            "sortby": "-obs_date_tm",
            "limit": 24,
            "datetime": (now - MAX_OBSERVATION_AGE).isoformat()
            + "/"
            + (now + CLOCK_SKEW).isoformat(),
        }
        model_params = {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "hourly": "temperature_2m,precipitation,soil_temperature_0cm",
            "past_days": 1,
            "forecast_days": 1,
            "timezone": "UTC",
            "temperature_unit": "celsius",
            "precipitation_unit": "mm",
        }
        # Fetch concurrently; cancel and await unfinished work on failure/shutdown.
        tasks = [
            asyncio.create_task(self._get(ECCC_URL, station_params)),
            asyncio.create_task(self._get(OPEN_METEO_URL, model_params)),
        ]
        try:
            eccc, meteo = await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        return normalize_weather(eccc, meteo, now)
