"""Purpose: run the weather API independently until the shared app is ready.

Choose live providers or a snapshot at REPLAY_SNAPSHOT. Start one refresh loop,
then cancel it and close the HTTP client on shutdown. Production replay loading
uses only the configured file and does not depend on the test directory.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI

from .adapters.weather import WeatherSources, normalize_weather, timestamp
from .weather_service import WeatherService, router


def replay_service(snapshot_path: str) -> WeatherService:
    """Read the weather section of a configured snapshot and freeze its clock.

    Weather section: {"eccc": <raw response>, "open_meteo": <raw response>}.
    Other source sections may coexist for the shared OP-305 snapshot recorder.
    """
    snapshot = json.loads(Path(snapshot_path).read_text())
    weather = snapshot["weather"]
    eccc, meteo = weather["eccc"], weather["open_meteo"]
    recorded = max(timestamp(f["properties"]["obs_date_tm"]) for f in eccc["features"])
    # Fail configuration early if the selected snapshot cannot form a valid watch.
    normalize_weather(eccc, meteo, recorded)

    async def fetch(now: datetime) -> dict[str, Any]:
        return normalize_weather(eccc, meteo, now)

    return WeatherService(fetch, mode="replay", clock=lambda: recorded)


def create_app(service: WeatherService | None = None) -> FastAPI:
    """Build the app; tests may provide a service with controlled source readings."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with httpx.AsyncClient() as client:
            chosen = service
            if chosen is None:
                mode = os.getenv("DATA_MODE", "live")
                if mode == "replay":
                    path = os.getenv("REPLAY_SNAPSHOT")
                    if not path:
                        raise ValueError("DATA_MODE=replay requires REPLAY_SNAPSHOT")
                    chosen = replay_service(path)
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
            app.state.weather_service = chosen
            task = asyncio.create_task(chosen.run())
            try:
                yield
            finally:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(title="OpenPath weather watch", lifespan=lifespan)
    app.include_router(router)
    return app
