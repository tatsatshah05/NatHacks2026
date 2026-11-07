"""Purpose: refresh the weather watch and serve the last successful result.

Each refresh fetches readings, runs the ice rule, and saves a complete response.
A failed refresh keeps the previous response and marks it stale. The API reads
this cache, so opening the endpoint does not make extra calls to the providers.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional

from fastapi import APIRouter, HTTPException, Request, Response

from .weather import evaluate_weather_watch

logger = logging.getLogger(__name__)
REFRESH_SECONDS = 600


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class WeatherService:
    """Manage one weather cache and its refresh/error state."""

    def __init__(
        self,
        fetch: Callable[[datetime], Awaitable[dict[str, Any]]],
        *,
        mode: str = "live",
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        if mode not in ("live", "replay"):
            raise ValueError("Weather mode must be live or replay")
        self.fetch = fetch
        self.mode = mode
        self.clock = clock
        self.last_good: Optional[dict[str, Any]] = None
        self.last_success: Optional[datetime] = None
        self.last_error: Optional[str] = None
        self._lock: Optional[asyncio.Lock] = None

    async def refresh(self) -> bool:
        """Return True after saving a new result, or False after a failed refresh."""
        # Python 3.9 requires constructing locks inside a running event loop.
        if self._lock is None:
            self._lock = asyncio.Lock()
        # Let only one refresh update the cache at a time.
        async with self._lock:
            try:
                readings = dict(await self.fetch(self.clock()))
                observed_at = readings.pop("observed_at")
                if observed_at.tzinfo is None:
                    raise ValueError("Observation must be timezone-aware")
                decision = evaluate_weather_watch(**readings)
                snapshot = {
                    "active": decision.active,
                    "reason": decision.reason,
                    "air_c": readings["air_c"],
                    "ground_c": readings["ground_c"],
                    "dew_c": readings["dew_c"],
                    "observed_at": observed_at.astimezone(timezone.utc).isoformat(),
                    "mode": self.mode,
                }
            except Exception as exc:
                # Cancellation is not swallowed. Existing good data stays intact.
                self.last_error = type(exc).__name__ + ": " + str(exc)
                logger.warning("Weather refresh failed: %s", self.last_error)
                return False
            # Publish only after fetching, validation, and calculation all succeed.
            self.last_good = snapshot
            self.last_success = self.clock()
            self.last_error = None
            return True

    @property
    def stale(self) -> bool:
        """Flag missing data, a failed refresh, or three missed refresh intervals."""
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


@router.get("/api/weather")
async def get_weather(request: Request, response: Response) -> dict[str, Any]:
    """Serve cached weather, with a header showing whether it is stale."""
    service: WeatherService = request.app.state.weather_service
    response.headers["Cache-Control"] = "no-store"
    # No successful reading yet means unavailable; it cannot imply safe weather.
    if service.last_good is None:
        raise HTTPException(
            status_code=503,
            detail="Weather data is not available yet.",
            headers={"Retry-After": "60", "Cache-Control": "no-store"},
        )
    response.headers["X-Weather-Stale"] = str(service.stale).lower()
    return dict(service.last_good)
