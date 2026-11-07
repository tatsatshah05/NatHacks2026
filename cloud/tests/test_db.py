import pytest
from sqlalchemy import text
from sqlmodel import Session

from cloud.app.db import database_url, make_engine, normalize_database_url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("postgres://u:p@host/db", "postgresql+psycopg://u:p@host/db"),
        ("postgresql://u:p@host/db", "postgresql+psycopg://u:p@host/db"),
        ("postgresql+psycopg://u:p@host/db", "postgresql+psycopg://u:p@host/db"),
        ("sqlite:///./openpath.db", "sqlite:///./openpath.db"),
    ],
)
def test_normalize_database_url(raw: str, expected: str) -> None:
    assert normalize_database_url(raw) == expected


def test_database_url_defaults_to_sqlite(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert database_url() == "sqlite:///./openpath.db"


def test_sqlite_engine_connects() -> None:
    with Session(make_engine("sqlite://")) as session:
        assert session.exec(text("SELECT 1")).one() == (1,)
