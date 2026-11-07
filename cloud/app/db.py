"""Database engine: Postgres on Render, SQLite locally and in tests."""

from __future__ import annotations

import os

from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import create_engine

DEFAULT_DATABASE_URL = "sqlite:///./openpath.db"


def normalize_url(url: str) -> str:
    """Render gives ``postgres://`` URLs; SQLAlchemy needs ``postgresql+psycopg://``."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


def make_engine(url: str | None = None) -> Engine:
    """Create the engine from ``url`` or ``DATABASE_URL``."""
    url = normalize_url(url or os.environ.get("DATABASE_URL") or DEFAULT_DATABASE_URL)
    if url.startswith("sqlite"):
        in_memory = url in ("sqlite://", "sqlite:///:memory:")
        return create_engine(
            url,
            connect_args={"check_same_thread": False},
            # One shared connection keeps an in-memory database alive across threads.
            poolclass=StaticPool if in_memory else None,
        )
    return create_engine(url, pool_pre_ping=True)
