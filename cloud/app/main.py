"""OpenPath API entry point: ``uvicorn cloud.app.main:app`` from the repository root.

``create_app()`` reads its settings from environment variables (all listed in
``.env.example``):

- ``DATABASE_URL``: Postgres on Render, otherwise a local SQLite file (``db.py``).
- ``DATA_MODE``: ``live`` (the default) or ``replay``. ``/health`` reports it, so
  the web app can show the Replay badge.
- ``CORS_ORIGINS``: the web origins allowed to call the API from a browser.
- ``RENDER_GIT_COMMIT``: set by Render. ``/health`` reports it as ``version``.
- ``SCHEDULER``: ``on`` (the default) runs every data source on its cadence (OP-103);
  ``off`` serves only what the database already holds, which tests rely on.
- ``REPLAY_SNAPSHOT``: in replay mode, the folder of recorded responses
  (``cloud/tests/fixtures`` for ``make dev``). ``REPLAY_AT`` optionally freezes the clock.
- ``SOCRATA_APP_TOKEN``: raises the City open data rate limit. Optional.
"""

from __future__ import annotations

import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from cloud.app import (
    ingest_api,
    obstructions_api,
    routing_api,
    sources,
    status,
    weather_service,
)
from cloud.app.camera_readings import CameraReadingStore
from cloud.app.db import make_engine
from cloud.app.network import DEFAULT_NETWORK_PATH, WalkNetwork
from cloud.app.profiles import load_routing_config
from cloud.app.routing import Router
from cloud.app.scheduler import Scheduler, utcnow
from cloud.app.store import ObstructionStore

# /health reports this version when RENDER_GIT_COMMIT is not set (local runs).
LOCAL_VERSION = "dev"

DataMode = Literal["live", "replay"]


class Health(BaseModel):
    """The ``GET /health`` body."""

    status: Literal["ok"]
    # The deployed git commit. CI's deploy job polls until it matches the merge.
    version: str
    # "replay" means the data is recorded, not live; every page must say so.
    mode: DataMode


def app_version() -> str:
    """The deployed git commit (Render sets ``RENDER_GIT_COMMIT``), or ``dev`` locally."""
    return os.environ.get("RENDER_GIT_COMMIT") or LOCAL_VERSION


def data_mode(raw: str | None) -> DataMode:
    """Read ``DATA_MODE``: ``live`` when it is unset or empty, else ``live`` or ``replay``.

    Any other value (a typo such as ``Replay``) stops the app at startup, so replayed
    data can never be served as live, or live data as a replay.
    """
    if raw is None or raw == "" or raw == "live":
        return "live"
    if raw == "replay":
        return "replay"
    raise ValueError(f"DATA_MODE must be 'live' or 'replay', not {raw!r}")


def cors_settings(raw: str | None) -> tuple[list[str], str | None]:
    """Split a comma-separated ``CORS_ORIGINS`` value into exact origins and one regex.

    An entry may contain ``*``, which matches one or more letters, digits and hyphens
    inside a single host-name label. This is for Vercel preview URLs, which look like
    ``<project>-<hash>-<team-slug>.vercel.app`` and
    ``<project>-git-<branch>-<team-slug>.vercel.app``, so the pattern to use is
    ``https://openpath-*-<team-slug>.vercel.app``.

    CORS is not a security boundary for this API. Its data is public, it never allows
    credentials (no cookies), and writes need the ``X-Admin-Key`` or ``X-Worker-Key``
    header. The allowlist only decides which web pages can read responses in a
    browser. The team slug narrows a wildcard, but Vercel project names are chosen by
    their owners, so do not rely on it to keep anyone out.
    """
    exact: list[str] = []
    patterns: list[str] = []
    for entry in (raw or "").split(","):
        origin = entry.strip().rstrip("/")
        if not origin:
            continue
        if "*" in origin:
            patterns.append(re.escape(origin).replace(r"\*", "[a-z0-9-]+"))
        else:
            exact.append(origin)
    regex = f"^(?:{'|'.join(patterns)})$" if patterns else None
    return exact, regex


def scheduler_enabled(raw: str | None) -> bool:
    """``SCHEDULER=off`` (or ``0`` / ``false``) disables fetching; anything else enables it."""
    return (raw or "on").strip().lower() not in {"off", "0", "false", "no"}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open the store, build the data sources and start the scheduler; undo it all at shutdown."""
    store = ObstructionStore(make_engine())
    store.create_tables()
    app.state.store = store  # read by obstructions_api.get_store
    camera_store = CameraReadingStore(
        store.engine
    )  # OP-213: readings from the vision worker
    camera_store.create_tables()
    app.state.camera_store = camera_store
    mode: str = app.state.mode
    snapshot_raw = os.environ.get("REPLAY_SNAPSHOT")
    snapshot = Path(snapshot_raw) if snapshot_raw else None
    run_scheduler = scheduler_enabled(os.environ.get("SCHEDULER"))
    app.state.scheduler = None
    app.state.weather_service = None
    # Routing (OP-207): the prebuilt walking network loads in well under a second.
    # Without the file (a bare checkout) /api/route answers 503 instead of crashing startup.
    app.state.router = None
    app.state.hazard_provider = None  # OP-206 sets the matcher here
    if DEFAULT_NETWORK_PATH.exists():
        app.state.router = Router(WalkNetwork.load(), load_routing_config())
    # One HTTP client per flavour, shared by every adapter and closed at shutdown.
    async with httpx.AsyncClient(timeout=10) as async_client:
        with httpx.Client(timeout=10) as sync_client:
            scheduler: Scheduler | None = None
            if run_scheduler:
                weather = sources.build_weather_service(mode, snapshot, async_client)
                app.state.weather_service = (
                    weather  # read by weather_service.get_weather
                )
                built = sources.build_sources(
                    mode=mode,
                    snapshot=snapshot,
                    app_token=os.environ.get("SOCRATA_APP_TOKEN"),
                    async_client=async_client,
                    sync_client=sync_client,
                    weather_service=weather,
                )
                clock = sources.replay_clock(snapshot) if mode == "replay" else utcnow
                scheduler = Scheduler(store, built, clock=clock)
                app.state.scheduler = scheduler
                await scheduler.start()
            try:
                yield
            finally:
                if scheduler is not None:
                    await scheduler.stop()
                store.engine.dispose()


def create_app() -> FastAPI:
    """Build the API. Reads the environment once, so a bad ``DATA_MODE`` fails here."""
    version = app_version()
    mode = data_mode(os.environ.get("DATA_MODE"))

    app = FastAPI(title="OpenPath API", version="0.1.0", lifespan=lifespan)
    app.state.mode = mode  # read by the lifespan and by /api/status
    app.state.version = version

    origins, origin_regex = cors_settings(os.environ.get("CORS_ORIGINS"))
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_origin_regex=origin_regex,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    app.include_router(obstructions_api.router)
    app.include_router(status.router)
    app.include_router(routing_api.router)
    app.include_router(weather_service.router)
    app.include_router(ingest_api.router)

    @app.get("/health", tags=["ops"])
    def health() -> Health:
        """Liveness check for Render and the CI deploy job, plus the data mode."""
        return Health(status="ok", version=version, mode=mode)

    return app


app = create_app()
