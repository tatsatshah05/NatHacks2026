"""Standalone OP-109 app until the shared OP-102 app is available.

The shared app can include weather_service.router and install the same lifespan.
No shared schema, config or main.py is created here.
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
