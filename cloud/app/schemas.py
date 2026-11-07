"""The shared obstruction contract (PLAN.md section 10).

Every data source (City open data, weather, cameras, community reports) turns its
items into ``Obstruction`` records. Routing, the route report, live updates and the
web app only ever see this one shape, so they never depend on where an item came
from. Change this file only after the team agrees (AGENTS.md, "Contracts").
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SOURCE_PATTERN = r"^[a-z][a-z0-9_]*$"


class Category(StrEnum):
    """Where the barrier is. Groups the route report."""

    INDOOR = "indoor"
    SIDEWALK = "sidewalk"
    ROAD = "road"
    WEATHER = "weather"
    TRANSIT = "transit"


class Kind(StrEnum):
    """What the barrier is."""

    ELEVATOR_OUT = "elevator_out"
    ESCALATOR_OUT = "escalator_out"
    SIDEWALK_CLOSED = "sidewalk_closed"
    ROAD_CLOSED = "road_closed"
    TRAIL_CLOSED = "trail_closed"
    TRAIL_CAUTION = "trail_caution"
    PLOW_PASSED = "plow_passed"
    SNOW_COMPLAINTS = "snow_complaints"
    SNOW = "snow"
    ICE_RISK = "ice_risk"
    BLOCKED = "blocked"


class Effect(StrEnum):
    """What the barrier does to routes (PLAN.md section 8)."""

    BLOCKED = "blocked"
    SNOW = "snow"
    ICE_RISK = "ice_risk"
    CAUTION = "caution"


class Confidence(StrEnum):
    """High for official status, medium for cameras and reports with a photo, low otherwise."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


GeometryType = Literal[
    "Point", "LineString", "MultiLineString", "Polygon", "MultiPolygon"
]

# How deeply positions are nested for each GeoJSON geometry type.
_POSITION_DEPTH: dict[str, int] = {
    "Point": 0,
    "LineString": 1,
    "MultiLineString": 2,
    "Polygon": 2,
    "MultiPolygon": 3,
}


class BBox(BaseModel):
    """A longitude/latitude box: west, south, east, north."""

    model_config = ConfigDict(frozen=True)

    min_lng: float = Field(ge=-180, le=180)
    min_lat: float = Field(ge=-90, le=90)
    max_lng: float = Field(ge=-180, le=180)
    max_lat: float = Field(ge=-90, le=90)

    @model_validator(mode="after")
    def _ordered(self) -> BBox:
        if self.min_lng > self.max_lng or self.min_lat > self.max_lat:
            raise ValueError(
                "bbox must be min_lng,min_lat,max_lng,max_lat with min <= max"
            )
        return self

    def intersects(self, other: BBox) -> bool:
        return not (
            other.min_lng > self.max_lng
            or other.max_lng < self.min_lng
            or other.min_lat > self.max_lat
            or other.max_lat < self.min_lat
        )


class Geometry(BaseModel):
    """A GeoJSON geometry in WGS84 longitude/latitude order."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: GeometryType
    coordinates: list[Any]

    @model_validator(mode="after")
    def _valid_positions(self) -> Geometry:
        positions = list(self.positions())
        if not positions:
            raise ValueError(f"{self.type} has no coordinates")
        return self

    def positions(self) -> Iterator[tuple[float, float]]:
        """Every [lng, lat] position in the geometry, validated."""
        yield from _walk(self.coordinates, _POSITION_DEPTH[self.type])

    def bbox(self) -> BBox:
        lngs, lats = zip(*self.positions(), strict=True)
        return BBox(
            min_lng=min(lngs), min_lat=min(lats), max_lng=max(lngs), max_lat=max(lats)
        )


def _walk(value: Any, depth: int) -> Iterator[tuple[float, float]]:
    if depth == 0:
        yield _position(value)
        return
    if not isinstance(value, list):
        # Pydantic only turns ValueError into a validation error, so not TypeError.
        raise ValueError("coordinates are nested too shallowly for this geometry type")  # noqa: TRY004
    for item in value:
        yield from _walk(item, depth - 1)


def _position(value: Any) -> tuple[float, float]:
    valid_shape = isinstance(value, list | tuple) and len(value) in (2, 3)
    if not valid_shape or not all(
        isinstance(n, int | float) and not isinstance(n, bool) for n in value
    ):
        raise ValueError(f"a position must be [lng, lat] numbers, got {value!r}")
    lng, lat = float(value[0]), float(value[1])
    if not (-180 <= lng <= 180 and -90 <= lat <= 90):
        raise ValueError(f"position {value!r} is outside longitude/latitude ranges")
    return lng, lat


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("datetimes must include a time zone; use UTC")
    return value.astimezone(UTC)


class Obstruction(BaseModel):
    """One barrier from any source. ``id`` is always ``"<source>:<source_ref>"``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(
        description='Always "<source>:<source_ref>"; filled in when omitted.'
    )
    source: str = Field(
        pattern=SOURCE_PATTERN, max_length=64, examples=["city_lrt_devices"]
    )
    source_ref: str = Field(
        min_length=1, max_length=200, description="The item's ID in its source."
    )
    category: Category
    kind: Kind
    effect: Effect
    title: str = Field(
        min_length=1, max_length=120, examples=["Elevator out of service"]
    )
    detail: str | None = Field(default=None, max_length=500)
    geometry: Geometry
    confidence: Confidence
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    observed_at: datetime
    expires_at: datetime
    url: str | None = Field(default=None, max_length=500)
    attribution: str = Field(min_length=1, max_length=200)

    @model_validator(mode="before")
    @classmethod
    def _fill_id(cls, data: Any) -> Any:
        if isinstance(data, dict) and not data.get("id"):
            data = {**data, "id": f"{data.get('source')}:{data.get('source_ref')}"}
        return data

    @field_validator("starts_at", "ends_at", "observed_at", "expires_at")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else as_utc(value)

    @field_validator("url")
    @classmethod
    def _http_url(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith(("https://", "http://")):
            raise ValueError("url must start with https:// or http://")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> Obstruction:
        if self.id != f"{self.source}:{self.source_ref}":
            raise ValueError('id must be "<source>:<source_ref>"')
        if self.expires_at <= self.observed_at:
            raise ValueError("expires_at must be after observed_at")
        if self.starts_at and self.ends_at and self.ends_at < self.starts_at:
            raise ValueError("ends_at must not be before starts_at")
        return self

    def is_active(self, now: datetime) -> bool:
        """True when the item has started and has not expired."""
        now = as_utc(now)
        started = self.starts_at is None or self.starts_at <= now
        return started and self.expires_at > now

    def content_key(self) -> tuple[Any, ...]:
        """Everything that makes a change worth announcing (not the refresh timestamps)."""
        return (
            self.category,
            self.kind,
            self.effect,
            self.title,
            self.detail,
            self.geometry.model_dump_json(),
            self.confidence,
            self.starts_at,
            self.ends_at,
            self.url,
            self.attribution,
        )

    def to_feature(self) -> ObstructionFeature:
        properties = ObstructionProperties(**self.model_dump(exclude={"geometry"}))
        return ObstructionFeature(
            id=self.id, geometry=self.geometry, properties=properties
        )


class ObstructionProperties(BaseModel):
    """The GeoJSON ``properties`` of an obstruction: every field except ``geometry``."""

    id: str
    source: str
    source_ref: str
    category: Category
    kind: Kind
    effect: Effect
    title: str
    detail: str | None
    confidence: Confidence
    starts_at: datetime | None
    ends_at: datetime | None
    observed_at: datetime
    expires_at: datetime
    url: str | None
    attribution: str


class ObstructionFeature(BaseModel):
    type: Literal["Feature"] = "Feature"
    id: str
    geometry: Geometry
    properties: ObstructionProperties


class ObstructionCollection(BaseModel):
    """Response of ``GET /api/obstructions``."""

    type: Literal["FeatureCollection"] = "FeatureCollection"
    generated_at: datetime
    features: list[ObstructionFeature]


@runtime_checkable
class SourceAdapter(Protocol):
    """One adapter per data source (``cloud/app/adapters/``).

    The scheduler (OP-103) calls ``fetch`` every ``cadence_s`` seconds. ``fetch`` returns
    the complete current set of items for ``source``: anything the store holds for that
    source and ``fetch`` no longer returns is removed. If the source cannot be read,
    ``fetch`` must raise instead of returning a partial list, so a failure never clears data.
    """

    source: str
    cadence_s: int

    def fetch(self, now: datetime) -> Sequence[Obstruction]: ...
