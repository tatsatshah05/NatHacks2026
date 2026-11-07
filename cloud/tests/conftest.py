"""Shared test helpers for the cloud API.

pytest loads this file automatically and makes every ``@pytest.fixture`` below
available to all tests in ``cloud/tests/`` by name. A test that takes an argument
called ``store`` gets a fresh database; one that takes ``make_obstruction`` gets
the factory function.

Run the tests from the repository root::

    PYTHONPATH=.:cloud pytest -q cloud/tests
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from cloud.app.db import make_engine
from cloud.app.schemas import Obstruction
from cloud.app.store import ObstructionStore

# A fixed "now" so tests are deterministic: 20:10 UTC on November 7, 2026, the time
# the LRT outage feed was checked while writing the plan.
NOW = datetime(2026, 11, 7, 20, 10, tzinfo=UTC)


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
