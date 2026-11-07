"""OpenPath API entry point: `uvicorn cloud.app.main:app` from the repository root."""

from __future__ import annotations

import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from cloud.app.db import init_db

LOCAL_VERSION = "dev"


class Health(BaseModel):
    status: Literal["ok"]
    version: str


def app_version() -> str:
    """The deployed git commit (Render sets RENDER_GIT_COMMIT), or `dev` locally.

    The CI deploy job polls /health until this matches the commit it just merged.
    """
    return os.environ.get("RENDER_GIT_COMMIT") or LOCAL_VERSION


def cors_settings(raw: str | None) -> tuple[list[str], str | None]:
    """Split a comma-separated `CORS_ORIGINS` value into exact origins and one regex.

    An entry may contain `*` to match one DNS label fragment, for Vercel preview
    domains such as `https://openpath-*.vercel.app`.
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
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    init_db()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="OpenPath API", version="0.1.0", lifespan=lifespan)

    origins, origin_regex = cors_settings(os.environ.get("CORS_ORIGINS"))
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_origin_regex=origin_regex,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @app.get("/health", tags=["ops"])
    def health() -> Health:
        """Liveness check used by Render and uptime monitoring."""
        return Health(status="ok", version=app_version())

    return app


app = create_app()
