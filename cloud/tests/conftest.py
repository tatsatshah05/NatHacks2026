"""Shared test helpers for the cloud API.

pytest loads this file automatically and makes every ``@pytest.fixture`` below
available to all tests in ``cloud/tests/`` by name. A test that takes an argument
called ``store`` gets a fresh database; one that takes ``make_obstruction`` gets
the factory function.

Run the tests from the repository root::

    PYTHONPATH=.:cloud pytest -q cloud/tests
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from cloud.app.db import make_engine
from cloud.app.schemas import Obstruction
from cloud.app.store import ObstructionStore

# A fixed "now" so tests are deterministic: 20:10 UTC on November 7, 2026, the time
# the LRT outage feed was checked while writing the plan.
NOW = datetime(2026, 11, 7, 20, 10, tzinfo=UTC)

REPO = Path(__file__).resolve().parents[2]
IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic"})
# Dependency and cache folders: not ours, and large.
SKIP_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        ".venv",
        "venv",
        ".next",
        "__pycache__",
        ".cache",
        ".pytest_cache",
        ".ruff_cache",
    }
)


def image_files(root: Path) -> set[Path]:
    """Every image file under ``root``, skipping dependency and cache folders."""
    found: set[Path] = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in SKIP_DIRS]
        for name in filenames:
            if os.path.splitext(name)[1].lower() in IMAGE_SUFFIXES:
                found.add(Path(dirpath, name))
    return found


@pytest.fixture(autouse=True)
def rate_limits_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """OP-401: the per-connection write limits are off unless a test turns them on.

    Every request a ``TestClient`` sends comes from the same address, so a test that
    posts many times would otherwise meet the 30-a-minute default.
    ``test_rate_limit.py`` sets ``RATE_LIMIT_PER_MIN`` itself.
    """
    monkeypatch.setenv("RATE_LIMIT_PER_MIN", "0")


@pytest.fixture(scope="session", autouse=True)
def no_image_is_written_under_the_repo() -> Iterator[None]:
    """OP-401: a test run never leaves an image file anywhere in the repository.

    AGENTS.md: no stored images. Photos and camera frames are handled in memory, so
    a new ``.jpg``, ``.png`` or ``.webp`` after the run means something kept one.
    """
    before = image_files(REPO)
    yield
    added = sorted(str(path.relative_to(REPO)) for path in image_files(REPO) - before)
    assert not added, f"image files were written under the repository: {added}"


def obstruction(**overrides: Any) -> Obstruction:
    """Build a valid Obstruction, overriding any field by keyword.

    The default is a real-looking elevator outage at Central Station (the example
    from PLAN.md section 10). Tests change only the fields they care about, e.g.
    ``obstruction(source_ref="99", expires_at=NOW)``.
    """
    data: dict[str, Any] = {
        "source": "city_lrt_devices",
        "source_ref": "1234",
        "category": "indoor",
        "kind": "elevator_out",
        "effect": "blocked",
        "title": "Elevator out of service",
        "detail": "Central Station, West Platform",
        "geometry": {"type": "Point", "coordinates": [-113.4900, 53.5440]},
        "confidence": "high",
        "starts_at": NOW - timedelta(hours=6),
        "ends_at": None,
        "observed_at": NOW,
        "expires_at": NOW + timedelta(minutes=10),
        "url": "https://data.edmonton.ca/d/snws-u3zx",
        "attribution": "City of Edmonton Open Data",
    }
    data.update(overrides)
    # model_validate runs every validator, so a bad override fails here, not later.
    return Obstruction.model_validate(data)


@pytest.fixture
def now() -> datetime:
    """The frozen test clock."""
    return NOW


@pytest.fixture
def make_obstruction() -> Callable[..., Obstruction]:
    """The ``obstruction`` factory, exposed as a fixture so tests can import nothing."""
    return obstruction


@pytest.fixture
def store() -> ObstructionStore:
    """A store on a brand-new in-memory SQLite database. Each test gets its own."""
    store = ObstructionStore(make_engine("sqlite://"))
    store.create_tables()
    return store
