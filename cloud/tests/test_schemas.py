"""OP-101: the Obstruction contract validates every field and derives its ID.

These tests pin down the contract in ``cloud/app/schemas.py``. If one of them
fails after a change, the change affects every adapter and the web app, so it
needs team agreement first (AGENTS.md, "Contracts").
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from cloud.app.schemas import BBox, Geometry, Obstruction, SourceAdapter

# The example from PLAN.md section 10, exactly as documented. If the plan and the
# code ever disagree, this test is what catches it.
PLAN_EXAMPLE = {
    "id": "city_lrt_devices:1234",
    "source": "city_lrt_devices",
    "source_ref": "1234",
    "category": "indoor",
    "kind": "elevator_out",
    "effect": "blocked",
    "title": "Elevator out of service",
    "detail": "Central Station, West Platform",
    "geometry": {"type": "Point", "coordinates": [-113.4900, 53.5440]},
    "confidence": "high",
    "starts_at": "2026-11-07T13:45:03Z",
    "ends_at": None,
    "observed_at": "2026-11-07T20:10:00Z",
    "expires_at": "2026-11-07T20:20:00Z",
    "url": "https://data.edmonton.ca/d/snws-u3zx",
    "attribution": "City of Edmonton Open Data",
}


# ------------------------------------------------------------------ identity


def test_plan_example_is_valid() -> None:
    """The documented example parses, and ISO strings become aware UTC datetimes."""
    item = Obstruction.model_validate(PLAN_EXAMPLE)
    assert item.id == "city_lrt_devices:1234"
    assert item.observed_at == datetime(2026, 11, 7, 20, 10, tzinfo=UTC)


def test_id_is_derived_when_omitted() -> None:
    """Adapters may leave out ``id``; it is built from source and source_ref."""
    data = {key: value for key, value in PLAN_EXAMPLE.items() if key != "id"}
    assert Obstruction.model_validate(data).id == "city_lrt_devices:1234"


def test_id_must_match_source_and_ref() -> None:
    """An explicit ``id`` that disagrees with source:source_ref is a bug, so reject it."""
    with pytest.raises(ValidationError, match="source_ref"):
        Obstruction.model_validate({**PLAN_EXAMPLE, "id": "something_else:1"})


# ------------------------------------------------------------------ datetimes


def test_naive_datetimes_are_rejected() -> None:
    """A datetime with no time zone is ambiguous and must not be accepted."""
    with pytest.raises(ValidationError, match="time zone"):
        Obstruction.model_validate(
            {**PLAN_EXAMPLE, "observed_at": "2026-11-07T20:10:00"}
        )


def test_other_time_zones_are_converted_to_utc(make_obstruction) -> None:
    """A datetime in the local zone (Edmonton) is stored as the same instant in UTC."""
    mountain = datetime(2026, 11, 7, 13, 10, tzinfo=UTC).astimezone()
    item = make_obstruction(
        observed_at=mountain, expires_at=mountain + timedelta(minutes=5)
    )
    assert item.observed_at.tzinfo == UTC


def test_expiry_must_follow_observation(make_obstruction, now) -> None:
    """An item that expires when it is observed would never be visible."""
    with pytest.raises(ValidationError, match="expires_at"):
        make_obstruction(observed_at=now, expires_at=now)


def test_end_must_not_precede_start(make_obstruction, now) -> None:
    """A schedule that ends before it starts is rejected."""
    with pytest.raises(ValidationError, match="ends_at"):
        make_obstruction(starts_at=now, ends_at=now - timedelta(seconds=1))


# ----------------------------------------------------------------- field rules


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source", "City LRT"),  # spaces and capitals are not allowed in source names
        ("category", "outdoors"),  # not in the Category enum
        ("kind", "pothole"),  # not in the Kind enum
        ("effect", "slow"),  # not in the Effect enum
        ("confidence", "certain"),  # not in the Confidence enum
        ("title", ""),  # titles must not be empty
        ("url", "ftp://example.com"),  # only web URLs are linkable
    ],
)
def test_invalid_values_are_rejected(make_obstruction, field, value) -> None:
    """Each enum and string rule rejects a value outside its vocabulary."""
    with pytest.raises(ValidationError):
        make_obstruction(**{field: value})


def test_unknown_fields_are_rejected() -> None:
    """``extra="forbid"``: a misspelled or leftover field (e.g. v1's severity) fails loudly."""
    with pytest.raises(ValidationError, match="extra"):
        Obstruction.model_validate({**PLAN_EXAMPLE, "severity": 0.7})


# ------------------------------------------------------------------- geometry


@pytest.mark.parametrize(
    "geometry",
    [
        {"type": "Point", "coordinates": [-200, 53.5]},  # longitude out of range
        {"type": "Point", "coordinates": [-113.5]},  # only one number
        {"type": "Point", "coordinates": [True, 53.5]},  # bool is not a coordinate
        {"type": "LineString", "coordinates": [-113.5, 53.5]},  # not nested enough
        {"type": "LineString", "coordinates": []},  # no positions at all
        {"type": "Circle", "coordinates": [-113.5, 53.5]},  # not a GeoJSON type
    ],
)
def test_bad_geometry_is_rejected(geometry) -> None:
    """Every malformed geometry is caught at validation time, not on the map."""
    with pytest.raises(ValidationError):
        Geometry.model_validate(geometry)


def test_geometry_bbox_covers_every_position() -> None:
    """The bounding box spans all positions, for lines and for polygon rings."""
    line = Geometry(type="LineString", coordinates=[[-113.52, 53.54], [-113.50, 53.55]])
    assert line.bbox() == BBox(
        min_lng=-113.52, min_lat=53.54, max_lng=-113.50, max_lat=53.55
    )
    polygon = Geometry(
        type="Polygon",
        coordinates=[[[-113.6, 53.5], [-113.4, 53.5], [-113.4, 53.6], [-113.6, 53.5]]],
    )
    assert polygon.bbox().max_lat == 53.6


def test_bbox_requires_ordered_corners() -> None:
    """Swapped corners (min > max) are almost always a typo, so BBox rejects them."""
    with pytest.raises(ValidationError, match="min <= max"):
        BBox(min_lng=-113.4, min_lat=53.5, max_lng=-113.6, max_lat=53.6)


# ------------------------------------------------------------------ behaviour


def test_is_active_respects_start_and_expiry(make_obstruction, now) -> None:
    """Active means: started (or no start) and not yet expired."""
    item = make_obstruction(starts_at=now + timedelta(hours=1))
    assert not item.is_active(now)  # starts in the future
    assert make_obstruction().is_active(now)  # started, not expired
    assert not make_obstruction().is_active(now + timedelta(hours=1))  # expired


def test_content_key_ignores_refresh_timestamps(make_obstruction, now) -> None:
    """A re-fetch that only moves observed_at/expires_at is not a material change."""
    first = make_obstruction()
    refreshed = make_obstruction(
        observed_at=now + timedelta(minutes=2), expires_at=now + timedelta(minutes=12)
    )
    assert first.content_key() == refreshed.content_key()
    # ...but a change the user would notice is.
    assert (
        first.content_key()
        != make_obstruction(detail="Central Station, East Platform").content_key()
    )


def test_to_feature_is_geojson(make_obstruction) -> None:
    """The API shape is a valid GeoJSON Feature with geometry outside properties."""
    feature = make_obstruction().to_feature().model_dump(mode="json")
    assert feature["type"] == "Feature"
    assert feature["id"] == "city_lrt_devices:1234"
    assert feature["geometry"] == {"type": "Point", "coordinates": [-113.49, 53.544]}
    assert feature["properties"]["kind"] == "elevator_out"
    assert feature["properties"]["observed_at"] == "2026-11-07T20:10:00Z"  # ISO, UTC
    assert "geometry" not in feature["properties"]


def test_source_adapter_protocol() -> None:
    """Any class with source, cadence_s and fetch() counts as a SourceAdapter."""

    class Example:
        source = "example"
        cadence_s = 60

        def fetch(self, now: datetime) -> list[Obstruction]:
            return []

    assert isinstance(Example(), SourceAdapter)
