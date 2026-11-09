"""Purpose: turn two official weather responses into inputs for the ice rule.

Environment Canada supplies Blatchford's observed air and dew-point temperatures.
Open-Meteo supplies modelled ground temperature and recent hourly history.
Fetch both, validate their timestamps/units, then return one consistent reading.

Open-Meteo is optional (fix of November 8, 2026). Its free API answers HTTP 429
to Render's shared address, so a failed Open-Meteo call, whatever the status or
exception, no longer fails the refresh. The station still gives the current air
and dew point. Each history hour takes the last good Open-Meteo value while that
response is under 24 hours old (and the hour had begun when it was fetched, so
no forecast counts as history), otherwise the station's own observation from a
rolling in-memory log of the last 12 hours; an hour with neither is left out and
the note says how much of the window the result rests on. Ground temperature is
Open-Meteo only, so without it the rule uses the air temperature alone.

After a 429 the adapter does not call Open-Meteo again until ``Retry-After`` has
passed, or 10 minutes, doubling to a cap of 60 minutes; the next 200 resets it.
Environment Canada keeps its normal cadence throughout. One line per state
change: ``WARN weather open_meteo: 429, next try at <UTC time>`` when a backoff
starts, ``WARN weather open_meteo: unavailable (...)`` when another failure
starts, and ``CHANGE weather open_meteo: back`` on recovery.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import Any

import httpx

from cloud.app.adapters._retry import retry_after_s

logger = logging.getLogger(__name__)

ECCC_URL = "https://api.weather.gc.ca/collections/swob-realtime/items"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
BLATCHFORD_ID = "3012209"
MAX_OBSERVATION_AGE = timedelta(hours=2)
CLOCK_SKEW = timedelta(seconds=5)
# The rule checks melting over 12 hours and precipitation over 6 hours.
MELT_HOURS = 12
PRECIPITATION_HOURS = 6
# How long the last good Open-Meteo response still supplies history and ground.
MODEL_MAX_AGE = timedelta(hours=24)
# How much of the station's own hourly history the adapter keeps in memory.
STATION_LOG = timedelta(hours=MELT_HOURS)
# Backoff after an Open-Meteo 429 without Retry-After: first wait, then doubling.
MODEL_BACKOFF_FIRST_S = 600.0
MODEL_BACKOFF_MAX_S = 3600.0


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


def hour_of(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def station_observation(
    eccc: dict[str, Any], now: datetime
) -> tuple[datetime, float, float]:
    """The newest recent Blatchford observation: (observed_at, air_c, dew_c).

    Rejects a missing, stale, future, flagged or oddly-united observation rather
    than manufacturing a clear-weather result.
    """
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
    return observed, number(props["air_temp"]), number(props["dwpt_temp"])


def model_series(
    meteo: dict[str, Any],
) -> tuple[dict[datetime, int], dict[str, Any]]:
    """Validate an Open-Meteo response; return its hour index and hourly arrays."""
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
    return {t: i for i, t in enumerate(times)}, hourly


def normalize_weather(
    eccc: dict[str, Any], meteo: dict[str, Any], now: datetime
) -> dict[str, Any]:
    """Combine station observations with modelled ground/history data.

    Reject incomplete, stale or incompatible responses rather than manufacturing
    a clear-weather result. Forecast hours after the station observation are
    excluded. Open-Meteo precipitation is a preceding-hour total.
    """
    observed, air_c, dew_c = station_observation(eccc, now)
    indices, hourly = model_series(meteo)
    # Align the hourly history to the station reading, excluding future hours.
    anchor = hour_of(observed)

    def value_at(field: str, at: datetime) -> float:
        if at not in indices:
            raise ValueError("Incomplete hourly weather history")
        return number(hourly[field][indices[at]])

    temperatures = [
        value_at("temperature_2m", anchor - timedelta(hours=h))
        for h in range(MELT_HOURS)
    ]
    precipitation = [
        value_at("precipitation", anchor - timedelta(hours=h))
        for h in range(PRECIPITATION_HOURS)
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


@dataclass(frozen=True)
class StationHour:
    """One Blatchford observation kept for the station-only history."""

    observed_at: datetime
    air_c: float
    # ``pcpn_amt_pst1hr``, the preceding hour's total; None when the station did not
    # report it or flagged it, which is unknown, never zero.
    precipitation_mm: float | None


def station_hours(eccc: dict[str, Any]) -> list[StationHour]:
    """Every usable Blatchford observation in a response, for the rolling log.

    Air temperature needs the °C unit and a passing quality flag; an observation
    without them is skipped. Precipitation is kept only with the mm unit and a
    passing flag.
    """
    hours = []
    for feature in eccc.get("features") or []:
        props = feature.get("properties") or {}
        if str(props.get("msc_id-value")) != BLATCHFORD_ID:
            continue
        if props.get("air_temp-uom") != "°C" or props.get("air_temp-qa", 100) != 100:
            continue
        try:
            observed = timestamp(props["obs_date_tm"])
            air_c = number(props["air_temp"])
        except (KeyError, TypeError, ValueError):
            continue
        precipitation = None
        if (
            props.get("pcpn_amt_pst1hr-uom") == "mm"
            and props.get("pcpn_amt_pst1hr-qa", 100) == 100
        ):
            try:
                value = number(props["pcpn_amt_pst1hr"])
            except (KeyError, TypeError, ValueError):
                value = -1.0
            if value >= 0:
                precipitation = value
        hours.append(StationHour(observed, air_c, precipitation))
    return hours


def partial_note(
    *,
    temperature_hours: int,
    precipitation_hours: int,
    model_hours: int,
    model_fetched_at: datetime | None,
    ground_known: bool,
) -> str:
    """The sentence added to the reason when Open-Meteo did not answer."""
    if model_hours and model_fetched_at is not None:
        history = "Open-Meteo data from " + model_fetched_at.strftime("%H:%M UTC")
        if temperature_hours > model_hours:
            history += " and Blatchford station observations"
    else:
        history = "Blatchford station observations"
    ground = "" if ground_known else " and the ground temperature is unknown"
    return (
        f"Open-Meteo is unavailable{ground}; the history covers {temperature_hours} "
        f"of the last {MELT_HOURS} hours ({precipitation_hours} of "
        f"{PRECIPITATION_HOURS} for precipitation) from {history}."
    )


def combine_partial(
    eccc: dict[str, Any],
    model: tuple[dict[str, Any], datetime] | None,
    log: Mapping[datetime, StationHour],
    now: datetime,
) -> dict[str, Any]:
    """The rule's inputs when Open-Meteo did not answer this refresh.

    Air and dew point come from the station as always. Each history hour takes the
    last good Open-Meteo value when that response is under 24 hours old and the
    hour had begun when it was fetched (later hours in it are forecasts), else the
    station's own observation for that hour; an hour with neither is left out.
    Ground temperature is Open-Meteo only: known here only when the last good
    response covers the observation hour under the same condition.
    """
    observed, air_c, dew_c = station_observation(eccc, now)
    anchor = hour_of(observed)

    model_at = None
    fetched_at = None
    if model is not None and now - model[1] < MODEL_MAX_AGE:
        payload, fetched_at = model
        try:
            indices, hourly = model_series(payload)
        except (KeyError, TypeError, ValueError):
            fetched_at = None
        else:
            last_hour = hour_of(fetched_at)

            def model_at(field: str, at: datetime) -> float | None:
                if at > last_hour or at not in indices:
                    return None
                try:
                    return number(hourly[field][indices[at]])
                except (TypeError, ValueError):
                    return None

    temperatures: list[float] = []
    precipitation: list[float] = []
    model_hours = 0
    for h in range(MELT_HOURS):
        at = anchor - timedelta(hours=h)
        temperature = model_at("temperature_2m", at) if model_at else None
        if temperature is not None:
            model_hours += 1
            rain = model_at("precipitation", at) if model_at else None
        else:
            entry = log.get(at)
            if entry is None:
                continue
            temperature, rain = entry.air_c, entry.precipitation_mm
        temperatures.append(temperature)
        if h < PRECIPITATION_HOURS and rain is not None:
            if rain < 0:
                raise ValueError("Negative precipitation")
            precipitation.append(rain)
    ground_c = model_at("soil_temperature_0cm", anchor) if model_at else None
    return {
        "air_c": air_c,
        "ground_c": ground_c,
        "dew_c": dew_c,
        "precipitation_last_6h_mm": sum(precipitation) if precipitation else None,
        "max_air_last_12h_c": max(air_c, *temperatures),
        "observed_at": observed,
        "note": partial_note(
            temperature_hours=len(temperatures),
            precipitation_hours=len(precipitation),
            model_hours=model_hours,
            model_fetched_at=fetched_at,
            ground_known=ground_c is not None,
        ),
    }


def error_reason(response: httpx.Response) -> str | None:
    """Open-Meteo's own words for an error: ``{"error": true, "reason": "..."}``."""
    try:
        body = response.json()
    except ValueError:
        return None
    reason = body.get("reason") if isinstance(body, dict) else None
    if isinstance(reason, str) and reason.strip():
        return " ".join(reason.split())
    return None


class WeatherSources:
    """Fetch both providers; tests can supply a fake HTTP client.

    Environment Canada is required. Open-Meteo is optional: its last good
    response, the station's rolling log and the 429 backoff live on the instance,
    one per process, and nothing is persisted.
    """

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
        # The last Open-Meteo response that parsed, with the fetch time.
        self.last_model: tuple[dict[str, Any], datetime] | None = None
        # The station's own history, one observation per hour, pruned to 12 h.
        self.station_log: dict[datetime, StationHour] = {}
        # Open-Meteo's current problem (None when healthy) and the backoff state.
        self.model_error: str | None = None
        self.model_retry_at: datetime | None = None
        self._model_backoff_s = 0.0

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

    # -- Open-Meteo state -------------------------------------------------------

    def model_due(self, now: datetime) -> bool:
        """False while a 429 backoff is running."""
        return self.model_retry_at is None or now >= self.model_retry_at

    def _rate_limited(self, response: httpx.Response, now: datetime) -> None:
        """Start a backoff: Retry-After when given, else 10 min doubling to 60 min."""
        self._model_backoff_s = (
            min(MODEL_BACKOFF_MAX_S, self._model_backoff_s * 2)
            if self._model_backoff_s
            else MODEL_BACKOFF_FIRST_S
        )
        delay = retry_after_s(response.headers.get("Retry-After"), now)
        if delay is None:
            delay = self._model_backoff_s
        self.model_retry_at = now + timedelta(seconds=delay)
        next_try = self.model_retry_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        reason = error_reason(response)
        said = f" ({reason})" if reason else ""
        self.model_error = f"open_meteo: HTTP 429{said}, next try at {next_try}"
        logger.warning("WARN weather open_meteo: 429, next try at %s%s", next_try, said)

    def _model_failed(self, message: str) -> None:
        """Any other Open-Meteo failure: no backoff, one line when it starts."""
        if self.model_error is None:
            logger.warning("WARN weather open_meteo: unavailable (%s)", message)
        self.model_error = f"open_meteo: {message}"

    def _model_recovered(self) -> None:
        if self.model_error is not None:
            logger.info("CHANGE weather open_meteo: back")
        self.model_error = None
        self.model_retry_at = None
        self._model_backoff_s = 0.0

    async def _fetch_model(
        self, params: dict[str, Any], now: datetime
    ) -> dict[str, Any] | None:
        """Open-Meteo's response, or None after recording why it failed."""
        try:
            payload = await self._get(OPEN_METEO_URL, params)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                self._rate_limited(exc.response, now)
            else:
                self._model_failed(f"HTTP {exc.response.status_code}")
            return None
        except (httpx.HTTPError, TimeoutError, ValueError, TypeError, KeyError) as exc:
            self._model_failed(f"{type(exc).__name__}: {exc}")
            return None
        return payload  # recovery is recorded once the content has been validated

    def record_station(self, eccc: dict[str, Any], now: datetime) -> None:
        """Add a response's observations to the rolling log and drop old hours."""
        for entry in station_hours(eccc):
            hour = hour_of(entry.observed_at)
            current = self.station_log.get(hour)
            # Prefer the report on the hour, then the newest observation of the hour.
            if current is None or (
                entry.observed_at.minute == 0,
                entry.observed_at,
            ) > (current.observed_at.minute == 0, current.observed_at):
                self.station_log[hour] = entry
        oldest = hour_of(now) - STATION_LOG
        for hour in [hour for hour in self.station_log if hour < oldest]:
            del self.station_log[hour]

    # -- one refresh -------------------------------------------------------------

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
        # Open-Meteo is skipped during its backoff; its failures end inside its task.
        tasks = [asyncio.create_task(self._get(ECCC_URL, station_params))]
        fetch_model = self.model_due(now)
        if fetch_model:
            tasks.append(asyncio.create_task(self._fetch_model(model_params, now)))
        try:
            results = await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        eccc = results[0]
        meteo = results[1] if fetch_model else None
        # The station must be usable before anything else; the rule needs it.
        station_observation(eccc, now)
        self.record_station(eccc, now)
        if meteo is not None:
            try:
                readings = normalize_weather(eccc, meteo, now)
            except (KeyError, TypeError, ValueError) as exc:
                # A 200 with unusable content counts as an Open-Meteo failure; the
                # last response that parsed stays in use while it is young enough.
                self._model_failed(f"{type(exc).__name__}: {exc}")
            else:
                self._model_recovered()
                self.last_model = (meteo, now)
                return readings
        readings = combine_partial(eccc, self.last_model, self.station_log, now)
        readings["warning"] = self.model_error or "open_meteo: unavailable"
        return readings
