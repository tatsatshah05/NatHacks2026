"""Database engine: Postgres on Render, SQLite locally and in tests.

The rest of the app never cares which database it is talking to. It calls
``make_engine()`` once at startup and hands the engine to ``ObstructionStore``.

- On Render, ``DATABASE_URL`` is set by the platform and points at Postgres.
- Locally, with no ``DATABASE_URL``, a SQLite file ``openpath.db`` is created in
  the current folder, so ``make dev`` needs no database server.
- In tests, ``make_engine("sqlite://")`` gives a fresh in-memory database per test.
"""

from __future__ import annotations

import os

from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import create_engine

# Used when DATABASE_URL is not set: a SQLite file next to where the app runs.
DEFAULT_DATABASE_URL = "sqlite:///./openpath.db"


def normalize_url(url: str) -> str:
    """Convert Render's Postgres URL into the form SQLAlchemy expects.

    Render (and Heroku-style platforms) provide ``postgres://user:pass@host/db``.
    SQLAlchemy dropped support for the bare ``postgres://`` scheme and needs the
    driver named explicitly, so we rewrite it to ``postgresql+psycopg://`` (psycopg 3).
    SQLite URLs pass through unchanged.
    """
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


def make_engine(url: str | None = None) -> Engine:
    """Create the SQLAlchemy engine from ``url``, else ``DATABASE_URL``, else SQLite."""
    url = normalize_url(url or os.environ.get("DATABASE_URL") or DEFAULT_DATABASE_URL)
    if url.startswith("sqlite"):
        in_memory = url in ("sqlite://", "sqlite:///:memory:")
        return create_engine(
            url,
            # FastAPI handles requests on several threads; SQLite connections refuse
            # to be used from a thread other than the one that opened them unless
            # this is turned off.
            connect_args={"check_same_thread": False},
            # An in-memory SQLite database lives only as long as its connection.
            # StaticPool keeps one shared connection open so the data survives across
            # the threads a test may use. File databases use the normal pool.
            poolclass=StaticPool if in_memory else None,
        )
    # Postgres: pool_pre_ping checks a pooled connection is still alive before using
    # it, so a connection Render dropped overnight gives a reconnect, not an error.
    return create_engine(url, pool_pre_ping=True)
