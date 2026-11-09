"""Purpose: decide whether the weather suggests a city-wide ice risk.

The rule needs freezing air or ground plus rain/snow, recent melting, or frost
conditions. It returns a yes/no decision and a readable reason. Source fetching
and API responses live in the other weather modules. See PLAN.md section 7.4.

Ground temperature comes only from Open-Meteo, and so may the history terms. When
Open-Meteo is unavailable (``adapters/weather.py``) those inputs are None: the
freezing test then uses the air temperature alone, and a history term that is
unknown cannot fire. A missing reading is never treated as zero.
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
    ground_c: float | None,
    dew_c: float,
    precipitation_last_6h_mm: float | None,
    max_air_last_12h_c: float | None,
) -> WatchDecision:
    """Evaluate readings in Celsius and millimeters.

    Callers must calculate historical windows ending at the observation time,
    excluding future forecast hours. Missing or invalid readings must not be
    replaced with zero: retain the last good watch and mark the source stale, or
    pass None for the Open-Meteo inputs when only the station answered.
    A city-wide watch is an advisory and must not change routing costs.
    """
    readings = (
        air_c,
        ground_c,
        dew_c,
        precipitation_last_6h_mm,
        max_air_last_12h_c,
    )
    # Invalid readings should fail the refresh so the service keeps its cache.
    if not all(isfinite(value) for value in readings if value is not None):
        raise ValueError("Weather readings must be finite numbers")
    if precipitation_last_6h_mm is not None and precipitation_last_6h_mm < 0:
        raise ValueError("Precipitation cannot be negative")

    # First require freezing conditions; moisture alone does not activate a watch.
    if air_c > 0 and ground_c is None:
        return WatchDecision(False, "Air is above freezing.")
    if air_c > 0 and ground_c is not None and ground_c > 0:
        return WatchDecision(False, "Air and ground are above freezing.")

    # Then collect the moisture, melt/refreeze, and frost triggers that apply.
    reasons = []
    if precipitation_last_6h_mm is not None and precipitation_last_6h_mm > 0:
        reasons.append("precipitation in the last 6 hours")
    if max_air_last_12h_c is not None and max_air_last_12h_c > 0:
        reasons.append("melt and refreeze conditions in the last 12 hours")
    if abs(air_c - dew_c) <= 1:
        reasons.append("air temperature within 1 C of the dew point")

    if reasons:
        return WatchDecision(
            True, "Ice risk: freezing conditions with " + "; ".join(reasons) + "."
        )
    return WatchDecision(False, "Freezing conditions without a watch trigger.")
