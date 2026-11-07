"""Database engine: Postgres on Render, SQLite locally.

Tables are defined by the stories that own them (the obstruction store is OP-101).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine
from sqlmodel import Session, SQLModel, create_engine

DEFAULT_DATABASE_URL = "sqlite:///./openpath.db"


def normalize_database_url(url: str) -> str:
    """Point Render's `postgres://` or `postgresql://` URL at the psycopg 3 driver."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url.removeprefix(prefix)
    return url


def database_url() -> str:
    return normalize_database_url(
        os.environ.get("DATABASE_URL") or DEFAULT_DATABASE_URL
    )


def make_engine(url: str) -> Engine:
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, pool_pre_ping=True, connect_args=connect_args)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return make_engine(database_url())


def init_db() -> None:
    """Create any tables that do not exist yet."""
    SQLModel.metadata.create_all(get_engine())


def get_session() -> Iterator[Session]:
    """FastAPI dependency that yields one session per request."""
    with Session(get_engine()) as session:
        yield session
