"""Replay the black-ice watch from a recorded Open-Meteo archive night (OP-305).

A live weather reading combines an Environment Canada station observation with
Open-Meteo's modelled history (``adapters/weather.py``). Station observations are
only kept for 30 days, so a demo of a past freeze-thaw night uses one official
response instead: Open-Meteo's historical archive (ERA5 reanalysis) for Edmonton,
with hourly air temperature, dew point, precipitation and soil temperature.

``scripts/record_snapshot.py --weather-archive YYYY-MM-DD`` fetches and stores that
response unchanged as ``weather/open_meteo_archive.json``. In replay mode
``sources.build_weather_service`` reads it and serves the hour the app's clock
points at, or the recording's documented demo hour when the clock is outside the
night. Every reading is labelled ``mode: replay`` and ``observed_at`` is the real
archive hour, so a replayed night is never presented as live weather.

Ground temperature: the archive publishes a 0 to 7 cm soil layer average
(``soil_temperature_0_to_7cm``), not the 0 cm surface value the forecast API
gives. It is used as ``ground_c`` and the recording's manifest says so.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from .adapters.weather import number, timestamp
from .weather import evaluate_weather_watch

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
ARCHIVE_HOURLY = (
    "temperature_2m",
    "dew_point_2m",
    "precipitation",
    "soil_temperature_0_to_7cm",
)
# The first of these present in the response is the ground temperature.
GROUND_FIELDS = ("soil_temperature_0cm", "soil_temperature_0_to_7cm")
HISTORY_HOURS = 12  # the rule looks back 12 h for melting and 6 h for precipitation


def archive_params(
    latitude: float, longitude: float, start: date, end: date
) -> dict[str, Any]:
    """Query parameters for one archive request, always in UTC and metric units."""
    return {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "hourly": ",".join(ARCHIVE_HOURLY),
        "timezone": "UTC",
        "temperature_unit": "celsius",
        "precipitation_unit": "mm",
    }


def ground_field(archive: dict[str, Any]) -> str:
    units = archive["hourly_units"]
    for field in GROUND_FIELDS:
        if field in units:
            return field
    raise ValueError("Archive response has no soil temperature series")


def archive_hours(archive: dict[str, Any]) -> list[datetime]:
    """The response's hours, after the same checks the live adapter applies."""
    if archive.get("utc_offset_seconds") != 0:
        raise ValueError("Open-Meteo archive response must use UTC")
    units = archive["hourly_units"]
    fields = ("temperature_2m", "dew_point_2m", "precipitation", ground_field(archive))
    for field in fields:
        if units.get(field) != ("mm" if field == "precipitation" else "°C"):
            raise ValueError("Unexpected Open-Meteo archive units")
    hourly = archive["hourly"]
    times = [timestamp(value) for value in hourly["time"]]
    if len(set(times)) != len(times):
        raise ValueError("Duplicate hourly timestamps")
    if any(len(hourly[field]) != len(times) for field in fields):
        raise ValueError("Mismatched hourly arrays")
    return times


def hour_of(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def normalize_archive(archive: dict[str, Any], at: datetime) -> dict[str, Any]:
    """The ice rule's inputs for one archive hour, in ``normalize_weather``'s shape.

    Air and dew point come from the archive's 2 m series, the ground value from its
    soil series. The hour needs 11 earlier hours in the response; otherwise the
    history windows would be incomplete and the result is refused, never zeroed.
    """
    times = archive_hours(archive)
    indices = {t: i for i, t in enumerate(times)}
    anchor = hour_of(at)
    hourly = archive["hourly"]
    ground = ground_field(archive)

    def value_at(field: str, when: datetime) -> float:
        if when not in indices:
            raise ValueError("Incomplete hourly weather history")
        return number(hourly[field][indices[when]])

    temperatures = [
        value_at("temperature_2m", anchor - timedelta(hours=h))
        for h in range(HISTORY_HOURS)
    ]
    precipitation = [
        value_at("precipitation", anchor - timedelta(hours=h)) for h in range(6)
    ]
    if any(value < 0 for value in precipitation):
        raise ValueError("Negative precipitation")
    return {
        "air_c": temperatures[0],
        "ground_c": value_at(ground, anchor),
        "dew_c": value_at("dew_point_2m", anchor),
        "precipitation_last_6h_mm": round(sum(precipitation), 3),
        "max_air_last_12h_c": max(temperatures),
        "observed_at": anchor,
    }


def usable_hours(archive: dict[str, Any]) -> list[datetime]:
    """Hours with enough history behind them to run the rule."""
    times = archive_hours(archive)
    return times[HISTORY_HOURS - 1 :]


@dataclass(frozen=True)
class ArchiveHour:
    """One hour of the night with the rule's inputs and its decision."""

    at: datetime
    air_c: float
    dew_c: float
    ground_c: float
    precipitation_last_6h_mm: float
    max_air_last_12h_c: float
    active: bool
    reason: str


def watch_table(archive: dict[str, Any]) -> list[ArchiveHour]:
    """Run the black-ice rule over every usable hour, for verification and docs."""
    table = []
    for at in usable_hours(archive):
        readings = normalize_archive(archive, at)
        observed = readings.pop("observed_at")
        decision = evaluate_weather_watch(**readings)
        table.append(
            ArchiveHour(
                at=observed,
                air_c=readings["air_c"],
                dew_c=readings["dew_c"],
                ground_c=readings["ground_c"],
                precipitation_last_6h_mm=readings["precipitation_last_6h_mm"],
                max_air_last_12h_c=readings["max_air_last_12h_c"],
                active=decision.active,
                reason=decision.reason,
            )
        )
    return table


def demo_hour(table: list[ArchiveHour]) -> datetime:
    """The hour a replay should show: the first active hour after precipitation.

    A freeze-thaw night is one where rain or snow fell and the air then froze. If
    no hour has that trigger, the first active hour of any kind is used. A night
    on which the watch never fires is refused, so a demo cannot claim an ice risk
    the rule would not raise.
    """
    for hour in table:
        if hour.active and "precipitation" in hour.reason:
            return hour.at
    for hour in table:
        if hour.active:
            return hour.at
    raise ValueError("The black-ice watch never fires in this archive window")


def archive_fetch(
    archive: dict[str, Any], demo_at: datetime
) -> Callable[[datetime], Awaitable[dict[str, Any]]]:
    """A ``WeatherService`` fetch that reads the archive at the clock's hour.

    When the clock is outside the recorded night (``make dev`` runs on the real
    clock), the documented demo hour is served. ``observed_at`` always names the
    archive hour that was actually read.
    """
    usable = set(usable_hours(archive))
    demo = hour_of(demo_at)
    if demo not in usable:
        raise ValueError("The demo hour is not inside the recorded archive night")

    async def fetch(now: datetime) -> dict[str, Any]:
        at = hour_of(now)
        return normalize_archive(archive, at if at in usable else demo)

    return fetch


__all__ = [
    "ARCHIVE_HOURLY",
    "ARCHIVE_URL",
    "ArchiveHour",
    "archive_fetch",
    "archive_hours",
    "archive_params",
    "demo_hour",
    "normalize_archive",
    "usable_hours",
    "watch_table",
]
