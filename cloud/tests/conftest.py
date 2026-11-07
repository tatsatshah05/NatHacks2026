"""Shared test helpers for the cloud API."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from cloud.app.db import make_engine
from cloud.app.schemas import Obstruction
from cloud.app.store import ObstructionStore

NOW = datetime(2026, 11, 7, 20, 10, tzinfo=UTC)


def obstruction(**overrides: Any) -> Obstruction:
    """A valid elevator outage at Central Station; override any field."""
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
    return Obstruction.model_validate(data)


@pytest.fixture
def now() -> datetime:
    return NOW


@pytest.fixture
def make_obstruction() -> Callable[..., Obstruction]:
    return obstruction


@pytest.fixture
def store() -> ObstructionStore:
    store = ObstructionStore(make_engine("sqlite://"))
    store.create_tables()
    return store
