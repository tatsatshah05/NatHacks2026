"""OpenPath API entry point: ``uvicorn cloud.app.main:app`` from the repository root.

``create_app()`` reads its settings from environment variables (all listed in
``.env.example``):

- ``DATABASE_URL``: Postgres on Render, otherwise a local SQLite file (``db.py``).
- ``DATA_MODE``: ``live`` (the default) or ``replay``. ``/health`` reports it, so
  the web app can show the Replay badge.
- ``CORS_ORIGINS``: the web origins allowed to call the API from a browser.
- ``RENDER_GIT_COMMIT``: set by Render. ``/health`` reports it as ``version``.
"""

from __future__ import annotations

import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from cloud.app import obstructions_api
from cloud.app.db import make_engine
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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open the database and the obstruction store at startup, and close them at shutdown."""
    store = ObstructionStore(make_engine())
    store.create_tables()
    app.state.store = store  # read by obstructions_api.get_store
    try:
        yield
    finally:
        store.engine.dispose()


def create_app() -> FastAPI:
    """Build the API. Reads the environment once, so a bad ``DATA_MODE`` fails here."""
    version = app_version()
    mode = data_mode(os.environ.get("DATA_MODE"))

    app = FastAPI(title="OpenPath API", version="0.1.0", lifespan=lifespan)

    origins, origin_regex = cors_settings(os.environ.get("CORS_ORIGINS"))
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_origin_regex=origin_regex,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    app.include_router(obstructions_api.router)

    @app.get("/health", tags=["ops"])
    def health() -> Health:
        """Liveness check for Render and the CI deploy job, plus the data mode."""
        return Health(status="ok", version=version, mode=mode)

    return app


app = create_app()
