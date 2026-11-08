"""OP-102: the database settings the deploy relies on (the code is OP-101's db.py)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import text

from cloud.app.db import make_engine, normalize_url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("postgres://u:p@host/db", "postgresql+psycopg://u:p@host/db"),
        ("postgresql://u:p@host/db", "postgresql+psycopg://u:p@host/db"),
        ("postgresql+psycopg://u:p@host/db", "postgresql+psycopg://u:p@host/db"),
        ("sqlite:///./openpath.db", "sqlite:///./openpath.db"),
    ],
)
def test_normalize_url(raw: str, expected: str) -> None:
    assert normalize_url(raw) == expected


def test_default_is_a_local_sqlite_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert str(make_engine().url) == "sqlite:///./openpath.db"


def test_render_url_uses_psycopg(monkeypatch: pytest.MonkeyPatch) -> None:
    """Render's postgres:// URL gets the psycopg 3 driver (no connection is made)."""
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@host:5432/openpath")
    assert make_engine().url.drivername == "postgresql+psycopg"


def test_in_memory_database_is_shared_across_threads() -> None:
    """A table made on one thread is visible on another, as FastAPI requests need."""
    engine = make_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE t (x INTEGER)"))
        connection.execute(text("INSERT INTO t VALUES (1)"))

    def read() -> int:
        with engine.connect() as connection:
            return connection.execute(text("SELECT x FROM t")).scalar_one()

    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(read).result() == 1
