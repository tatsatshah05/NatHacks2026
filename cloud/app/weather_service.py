"""Purpose: refresh the weather watch and serve the last successful result.

Each refresh validates readings, runs the ice rule, and replaces the cached
response only after success. The API always returns the planned weather model;
active=None means no reading yet. Freshness stays available to the status owner.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, field_validator

from .weather import evaluate_weather_watch

logger = logging.getLogger(__name__)
REFRESH_SECONDS = 600


class WeatherResponse(BaseModel):
    """The planned /api/weather fields, including an explicit unknown state."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    active: bool | None = None
    reason: str = "Weather data is not available yet."
    air_c: float | None = None
    ground_c: float | None = None
    dew_c: float | None = None
    observed_at: datetime | None = None
    mode: Literal["live", "replay"] = "live"

    @field_validator("observed_at")
    @classmethod
    def observation_in_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Observation must be timezone-aware")
        return value.astimezone(UTC)


def utcnow() -> datetime:
    return datetime.now(UTC)


class WeatherService:
    """Manage one weather cache and refresh/error state for status reporting."""

    def __init__(
        self,
        fetch: Callable[[datetime], Awaitable[dict[str, Any]]],
        *,
        mode: Literal["live", "replay"] = "live",
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        # Validate the mode even before the first successful fetch.
        WeatherResponse(mode=mode)
        self.fetch = fetch
        self.mode = mode
        self.clock = clock
        self.last_good: WeatherResponse | None = None
        self.last_success: datetime | None = None
        self.last_error: str | None = None
        self._lock = asyncio.Lock()

    async def refresh(self) -> bool:
        """Publish a complete reading, or preserve the last good result on failure."""
        async with self._lock:
            try:
                readings = dict(await self.fetch(self.clock()))
                observed_at = readings.pop("observed_at")
                decision = evaluate_weather_watch(**readings)
                snapshot = WeatherResponse(
                    active=decision.active,
                    reason=decision.reason,
                    air_c=readings["air_c"],
                    ground_c=readings["ground_c"],
                    dew_c=readings["dew_c"],
                    observed_at=observed_at,
                    mode=self.mode,
                )
            except (
                httpx.HTTPError,
                TimeoutError,
                ValueError,
                KeyError,
                TypeError,
            ) as exc:
                # Known source/validation failures retain the previous response.
                self.last_error = type(exc).__name__ + ": " + str(exc)
                logger.warning("Weather refresh failed: %s", self.last_error)
                return False
            previous = self.last_good.active if self.last_good else None
            self.last_good = snapshot
            self.last_success = self.clock()
            self.last_error = None
            if previous != snapshot.active:
                before = (
                    "unknown"
                    if previous is None
                    else "active"
                    if previous
                    else "inactive"
                )
                after = "active" if snapshot.active else "inactive"
                logger.info(
                    "CHANGE weather watch %s->%s %s", before, after, snapshot.reason
                )
            return True

    @property
    def stale(self) -> bool:
        """Internal freshness for /api/status integration, not an HTTP header."""
        return (
            self.last_success is None
            or self.last_error is not None
            or self.clock() - self.last_success > timedelta(seconds=REFRESH_SECONDS * 3)
        )

    async def run(self) -> None:
        """Refresh immediately, then wait 10 minutes before the next refresh."""
        while True:
            await self.refresh()
            await asyncio.sleep(REFRESH_SECONDS)


router = APIRouter()


@router.get("/api/weather", response_model=WeatherResponse)
async def get_weather(request: Request) -> WeatherResponse:
    """Always return 200: cached weather, or an explicit unknown first reading."""
    service: WeatherService = request.app.state.weather_service
    return service.last_good or WeatherResponse(mode=service.mode)
