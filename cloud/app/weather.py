"""OpenPath's city-wide weather watch, following PLAN.md section 7.4.

This module evaluates normalized readings. Fetching, caching, scheduling and
the API endpoint are separate integration steps in OP-109.
"""

from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class WatchDecision:
    """Internal calculation result; this is not the shared API schema."""

    active: bool
    reason: str


def evaluate_weather_watch(
    *,
    air_c: float,
    ground_c: float,
    dew_c: float,
    precipitation_last_6h_mm: float,
    max_air_last_12h_c: float,
) -> WatchDecision:
    """Evaluate complete readings in Celsius and millimeters.

    Callers must calculate historical windows ending at the observation time,
    excluding future forecast hours. Missing or invalid readings must not be
    replaced with zero: retain the last good watch and mark the source stale.
    A city-wide watch is an advisory and must not change routing costs.
    """
    readings = (
        air_c,
        ground_c,
        dew_c,
        precipitation_last_6h_mm,
        max_air_last_12h_c,
    )
    if not all(isfinite(value) for value in readings):
        raise ValueError("Weather readings must be finite numbers")
    if precipitation_last_6h_mm < 0:
        raise ValueError("Precipitation cannot be negative")

    if air_c > 0 and ground_c > 0:
        return WatchDecision(False, "Air and ground are above freezing.")

    reasons = []
    if precipitation_last_6h_mm > 0:
        reasons.append("precipitation in the last 6 hours")
    if max_air_last_12h_c > 0:
        reasons.append("melt and refreeze conditions in the last 12 hours")
    if abs(air_c - dew_c) <= 1:
        reasons.append("air temperature within 1 C of the dew point")

    if reasons:
        return WatchDecision(
            True, "Ice risk: freezing conditions with " + "; ".join(reasons) + "."
        )
    return WatchDecision(False, "Freezing conditions without a watch trigger.")
