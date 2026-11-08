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
  (``cloud/tests/fixtures`` for ``make dev``). ``REPLAY_AT`` optionally freezes the clock;
  ``REPLAY_CLOCK=running`` lets it advance from the recording's time (OP-305), and
  ``WEATHER_REPLAY`` picks a named weather recording (``sources.py``).
- ``SOCRATA_APP_TOKEN``: raises the City open data rate limit. Optional.
- ``LRT_TRIPS``: ``true`` adds walk and LRT trips to ``/api/route?mode=walk_lrt``
  (OP-311). Off by default; then every route is walking only.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from cloud.app import (
    ingest_api,
    lrt,
    obstructions_api,
    priority,
    push,
    push_api,
    reports_api,
    routing_api,
    search,
    sources,
    status,
    weather_service,
    ws,
)
from cloud.app.camera_ice_routing import CameraIceHazardProvider
from cloud.app.camera_readings import CameraReadingStore
from cloud.app.db import make_engine
from cloud.app.matching import HazardProvider
from cloud.app.network import DEFAULT_NETWORK_PATH, WalkNetwork
from cloud.app.profiles import load_routing_config
from cloud.app.quality import DailyCounts
from cloud.app.routing import Router
from cloud.app.scheduler import Scheduler, utcnow
from cloud.app.store import ObstructionStore

log = logging.getLogger(__name__)

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


def camera_ice_provider(
    network: WalkNetwork,
    camera_store: CameraReadingStore,
    weather: weather_service.WeatherService | None,
    base: HazardProvider,
    mode: DataMode,
) -> HazardProvider | CameraIceHazardProvider:
    """OP-205: camera ice risks on exact crossings, on top of the OP-206 matcher.

    Builds the crossing index once (about 15 ms for Edmonton). Without a weather
    service (``SCHEDULER=off``) ``ice`` readings still count and ``wet`` ones do not.
    If the index cannot be built, routing keeps the matcher alone.
    """
    try:
        return CameraIceHazardProvider(
            network,
            camera_store.latest,
            weather=weather,
            base=base,
            mode=mode,
        )
    except Exception:
        log.exception("CAMERA ice risks unavailable; routing with the matcher only")
        return base


async def stop_store_users(app: FastAPI, scheduler: Scheduler | None) -> bool:
    """Stop everything that uses the database, in order. True if all of it stopped.

    The engine may be disposed only after this returns True. In-memory SQLite (tests,
    ``db.py``) has one shared connection, and closing it while a worker thread is
    still executing on it crashes the process (a segmentation fault in CI). The
    order follows who calls whom:

    1. the scheduler, which writes the store (and whose runs feed the live hub);
    2. the push watcher (OP-304), whose ticks read saved routes and hazards;
    3. the live hub (OP-303), whose timer and re-routes read hazards;
    4. the hazard providers (OP-206 matcher, OP-205 camera ice), whose rebuild
       thread and camera reads use the database.

    Every step is bounded and wrapped, so one failure never skips the others.
    """
    steps: list[tuple[str, Callable[[], Awaitable[bool]]]] = []
    if scheduler is not None:
        steps.append(("scheduler", scheduler.stop))
    push_services = getattr(app.state, "push", None)
    watcher = push_services.watcher if push_services is not None else None
    if watcher is not None:
        steps.append(("push watcher", lambda: asyncio.to_thread(watcher.stop)))
    hub = getattr(app.state, "live", None)
    if hub is not None:
        steps.append(("live hub", hub.stop))
    provider = getattr(app.state, "hazard_provider", None)
    provider_stop = getattr(provider, "stop", None)
    if callable(provider_stop):
        steps.append(("hazard provider", lambda: asyncio.to_thread(provider_stop)))
    all_stopped = True
    for name, stop in steps:
        try:
            stopped = await stop()
        except Exception:  # one failure must not skip the other stops
            log.exception("SHUTDOWN %s failed to stop", name)
            stopped = False
        if not stopped:
            all_stopped = False
            log.warning("SHUTDOWN %s did not stop cleanly", name)
    return all_stopped


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open the store, build the data sources and start the scheduler; undo it all at shutdown."""
    store = ObstructionStore(make_engine())
    store.create_tables()
    app.state.store = store  # read by obstructions_api.get_store
    # OP-213: readings from the vision worker, in their own table.
    camera_store = CameraReadingStore(store.engine)
    camera_store.create_tables()
    camera_store.purge_expired(utcnow())  # drop readings that expired over a week ago
    app.state.camera_store = camera_store
    # OP-407: one item count per source and UTC day, for the sudden-drop check.
    counts = DailyCounts(store.engine)
    counts.create_tables()
    app.state.daily_counts = counts
    app.state.photo_checker = reports_api.build_photo_checker()  # OP-308
    mode: str = app.state.mode
    snapshot_raw = os.environ.get("REPLAY_SNAPSHOT")
    snapshot = Path(snapshot_raw) if snapshot_raw else None
    run_scheduler = scheduler_enabled(os.environ.get("SCHEDULER"))
    # One clock for the scheduler, the map and route hazards (obstructions_api.get_now).
    # In replay mode REPLAY_AT freezes it, so recorded closures stay active everywhere.
    clock = sources.replay_clock(snapshot) if mode == "replay" else utcnow
    app.state.clock = clock
    if mode == "replay" and snapshot is not None:
        # OP-305: recorded camera readings, re-labelled "replay"; none in the fixtures.
        sources.load_replay_cameras(snapshot, camera_store, clock())
    app.state.scheduler = None
    app.state.weather_service = None
    # Routing (OP-207): the prebuilt walking network loads in well under a second.
    # Without the file (a bare checkout) /api/route answers 503 instead of crashing startup.
    app.state.router = None
    app.state.hazard_provider = None
    app.state.lrt_planner = None  # OP-311: only with LRT_TRIPS on
    if DEFAULT_NETWORK_PATH.exists():
        network = WalkNetwork.load()
        app.state.router = Router(network, load_routing_config())
        # OP-206: matches the store's active obstructions to network edges for routing.
        # OP-205 wraps it below, once the weather service exists.
        app.state.hazard_provider = HazardProvider(store, network)
        if lrt.enabled(os.environ.get("LRT_TRIPS")):
            app.state.lrt_planner = lrt.load_planner(app.state.router)
    # One HTTP client per flavour, shared by every adapter and closed at shutdown.
    async with httpx.AsyncClient(timeout=10) as async_client:
        with httpx.Client(timeout=10) as sync_client:
            scheduler: Scheduler | None = None
            if run_scheduler:
                weather = sources.build_weather_service(
                    mode, snapshot, async_client, clock=clock
                )
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
                scheduler = Scheduler(store, built, clock=clock, counts=counts)
                app.state.scheduler = scheduler
                await scheduler.start()
            if app.state.router is not None:
                app.state.hazard_provider = camera_ice_provider(
                    app.state.router.network,
                    camera_store,
                    app.state.weather_service,
                    app.state.hazard_provider,
                    app.state.mode,
                )
            # OP-304: saved routes and push alerts; the watcher thread rechecks saved
            # routes when hazards change (idle with SCHEDULER=off, as in tests).
            app.state.push = push.start_route_watcher(
                store.engine,
                app.state.router,
                app.state.hazard_provider,
                hazard_clock=clock,
                run=run_scheduler,
            )
            app.state.live = ws.attach(app)  # OP-303: WS /ws live updates
            try:
                yield
            finally:
                # Every thread that uses the store stops before the engine closes.
                if await stop_store_users(app, scheduler):
                    store.engine.dispose()
                else:
                    # Closing the connection under a running call is what crashed;
                    # leave it to process exit instead.
                    log.warning(
                        "SHUTDOWN a database call is still running; "
                        "not disposing the engine"
                    )


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
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
    )

    app.include_router(obstructions_api.router)
    app.include_router(status.router)
    app.include_router(routing_api.router)
    app.include_router(weather_service.router)
    app.include_router(ingest_api.router)
    app.include_router(push_api.router)
    app.include_router(search.router)
    app.state.search = search.load_index()  # OP-302: prebuilt, None if missing (503)
    app.include_router(reports_api.router)
    app.include_router(ws.router)  # WS /ws (OP-303)
    app.include_router(priority.router)  # GET /api/priority (OP-310)

    @app.get("/health", tags=["ops"])
    def health() -> Health:
        """Liveness check for Render and the CI deploy job, plus the data mode."""
        return Health(status="ok", version=version, mode=mode)

    return app


app = create_app()
