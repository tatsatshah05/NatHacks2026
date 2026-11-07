"""Purpose: let us run and test the weather API on its own.

Choose live providers or a recorded replay, start the refresh loop, and attach
the weather endpoint. On shutdown, stop the loop and close the HTTP client.
The shared OP-102 app can reuse the router and this startup/shutdown pattern.
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager, suppress
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Optional

import httpx
from fastapi import FastAPI

from .adapters.weather import WeatherSources, normalize_weather, timestamp
from .weather_service import WeatherService, router


def create_app(service: Optional[WeatherService] = None) -> FastAPI:
    """Build the app; tests may provide their own service with fake readings."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with httpx.AsyncClient() as client:
            chosen = service
            if chosen is None:
                mode = os.getenv("DATA_MODE", "live")
                if mode == "replay":
                    fixture_dir = Path(__file__).parents[1] / "tests/fixtures/weather"
                    eccc = json.loads((fixture_dir / "eccc.json").read_text())
                    meteo = json.loads((fixture_dir / "open_meteo.json").read_text())
                    # An explicitly frozen recording, never presented as live.
                    recorded = max(
                        timestamp(f["properties"]["obs_date_tm"])
                        for f in eccc["features"]
                    )

                    async def replay_fetch(now: datetime) -> dict[str, Any]:
                        return normalize_weather(eccc, meteo, now)

                    chosen = WeatherService(
                        replay_fetch, mode="replay", clock=lambda: recorded
                    )
                elif mode == "live":
                    chosen = WeatherService(
                        WeatherSources(
                            client,
                            latitude=float(os.getenv("WEATHER_LAT", "53.57")),
                            longitude=float(os.getenv("WEATHER_LNG", "-113.52")),
                        )
                    )
                else:
                    raise ValueError("DATA_MODE must be live or replay")
            # Make the service available to the route, then start background work.
            app.state.weather_service = chosen
            task = asyncio.create_task(chosen.run())
            try:
                yield
            finally:
                # Stop work before closing the HTTP client used by the providers.
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(title="OpenPath weather watch", lifespan=lifespan)
    app.include_router(router)
    return app
