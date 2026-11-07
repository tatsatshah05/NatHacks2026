"""The shared obstruction contract (PLAN.md section 10).

What this file is
-----------------
OpenPath pulls barriers from many places: City of Edmonton open data (elevator
outages, construction, trail closures), weather services, public cameras and
community reports. Each of those sources has its own data format. This file
defines the ONE shape they are all converted into: the ``Obstruction``.

Everything downstream (routing, the route report, live updates, push alerts,
the web app) only ever sees ``Obstruction`` objects. That means none of those
parts need to know where an item came from, and a new data source can be added
by writing one adapter that produces ``Obstruction`` objects.

How to read this file
---------------------
1. The enums (``Category``, ``Kind``, ``Effect``, ``Confidence``) are the fixed
   vocabularies. Every value is documented in PLAN.md section 10.
2. ``BBox`` and ``Geometry`` handle locations (GeoJSON, longitude/latitude).
3. ``Obstruction`` is the record itself, with validation rules.
4. The ``Obstruction*`` response models are the GeoJSON shapes the API returns.
5. ``SourceAdapter`` is the interface every data-source adapter must satisfy.

Changing this file
------------------
This is a shared contract. Change it only after the team agrees (see
AGENTS.md, "Contracts"), because every adapter and the web app depend on it.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Source names are machine identifiers such as "city_lrt_devices" or
# "camera_model": lowercase letters, digits and underscores, starting with a
# letter. They appear in IDs, logs and the /api/status page, so they must be
# stable and URL-safe.
SOURCE_PATTERN = r"^[a-z][a-z0-9_]*$"


# --------------------------------------------------------------------------- enums
# StrEnum means each member compares equal to its string value, so
# ``Category.INDOOR == "indoor"`` is True and JSON serialisation is just the string.


class Category(StrEnum):
    """Where the barrier is. The route report groups items by category."""

    INDOOR = "indoor"  # inside a building or station (elevators, escalators)
    SIDEWALK = "sidewalk"  # sidewalks, paths and trails
    ROAD = "road"  # roads and crossings
    WEATHER = "weather"  # city-wide weather conditions (the black-ice watch)
    TRANSIT = "transit"  # transit service itself (reserved for LRT trips, OP-311)


class Kind(StrEnum):
    """What the barrier is. This is the specific thing the user sees labelled on the map."""

    ELEVATOR_OUT = "elevator_out"  # LRT elevator out of service
    ESCALATOR_OUT = "escalator_out"  # LRT escalator out of service
    SIDEWALK_CLOSED = "sidewalk_closed"  # construction permit affecting pedestrians
    ROAD_CLOSED = "road_closed"  # traffic disruption: closure or incident
    TRAIL_CLOSED = "trail_closed"  # river valley trail closure
    TRAIL_CAUTION = "trail_caution"  # river valley trail caution notice
    PLOW_PASSED = (
        "plow_passed"  # a plow cleared this road recently (snow ridges at curb ramps)
    )
    SNOW_COMPLAINTS = (
        "snow_complaints"  # neighbourhood with recent 311 snow-on-walk complaints
    )
    SNOW = "snow"  # snow on the path (camera reading or community report)
    ICE_RISK = "ice_risk"  # icy conditions likely (camera reading or community report)
    BLOCKED = "blocked"  # community report: path is blocked


class Effect(StrEnum):
    """What the barrier does to routes (PLAN.md section 8).

    The routing engine turns each effect into a cost multiplier per mobility profile,
    or removes the path entirely. ``caution`` never changes a route; it only appears
    in the route report.
    """

    BLOCKED = "blocked"  # impassable for everyone
    SNOW = "snow"  # impassable for wheelchairs, slower for others
    ICE_RISK = "ice_risk"  # impassable with a walker or cane, slower for others
    CAUTION = "caution"  # report only, no routing change


class Confidence(StrEnum):
    """How much to trust the item.

    - ``high``: official City status (an elevator the City says is out)
    - ``medium``: a camera reading, or a community report with a photo
    - ``low``: the weather watch or a neighbourhood-level 311 advisory
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# ------------------------------------------------------------------------ geometry

# The GeoJSON geometry types we accept. GeometryCollection is deliberately left out:
# nothing in our sources produces one, and it would complicate matching.
GeometryType = Literal[
    "Point", "LineString", "MultiLineString", "Polygon", "MultiPolygon"
]

# GeoJSON nests coordinates differently for each type. A Point is one position
# [lng, lat]; a LineString is a list of positions; a Polygon is a list of rings,
# each a list of positions; and so on. This table says how many list levels sit
# above the positions, so one walker function can validate every type.
_POSITION_DEPTH: dict[str, int] = {
    "Point": 0,
    "LineString": 1,
    "MultiLineString": 2,
    "Polygon": 2,
    "MultiPolygon": 3,
}


class BBox(BaseModel):
    """A longitude/latitude bounding box: west, south, east, north.

    Used for the ``?bbox=`` filter on ``GET /api/obstructions`` and stored with each
    row so the database can filter by area without parsing geometry.
    """

    model_config = ConfigDict(
        frozen=True
    )  # immutable, so it can be compared and hashed

    min_lng: float = Field(ge=-180, le=180)  # west edge
    min_lat: float = Field(ge=-90, le=90)  # south edge
    max_lng: float = Field(ge=-180, le=180)  # east edge
    max_lat: float = Field(ge=-90, le=90)  # north edge

    @model_validator(mode="after")
    def _ordered(self) -> BBox:
        # A box where min > max is almost always a typo (corners swapped), so reject it.
        if self.min_lng > self.max_lng or self.min_lat > self.max_lat:
            raise ValueError(
                "bbox must be min_lng,min_lat,max_lng,max_lat with min <= max"
            )
        return self

    def intersects(self, other: BBox) -> bool:
        """True when the two boxes overlap or touch."""
        # Two boxes are disjoint only if one is entirely to one side of the other.
        return not (
            other.min_lng > self.max_lng
            or other.max_lng < self.min_lng
            or other.min_lat > self.max_lat
            or other.max_lat < self.min_lat
        )


class Geometry(BaseModel):
    """A GeoJSON geometry in WGS84 longitude/latitude order.

    GeoJSON puts longitude FIRST: ``[-113.49, 53.54]`` is Edmonton. Mixing the order
    up puts items in the Indian Ocean, so every position is range-checked here.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: GeometryType
    # Nested lists of numbers; the nesting depends on ``type`` (see _POSITION_DEPTH).
    coordinates: list[Any]

    @model_validator(mode="after")
    def _valid_positions(self) -> Geometry:
        # Walking the positions validates every one of them (shape and ranges).
        # An empty geometry has no location, so it is rejected too.
        positions = list(self.positions())
        if not positions:
            raise ValueError(f"{self.type} has no coordinates")
        return self

    def positions(self) -> Iterator[tuple[float, float]]:
        """Every (lng, lat) position in the geometry, in order."""
        yield from _walk(self.coordinates, _POSITION_DEPTH[self.type])

    def bbox(self) -> BBox:
        """The smallest box that contains every position."""
        lngs, lats = zip(*self.positions(), strict=True)
        return BBox(
            min_lng=min(lngs), min_lat=min(lats), max_lng=max(lngs), max_lat=max(lats)
        )


def _walk(value: Any, depth: int) -> Iterator[tuple[float, float]]:
    """Descend ``depth`` list levels, then yield each position found."""
    if depth == 0:
        # We have reached a single position such as [lng, lat].
        yield _position(value)
        return
    if not isinstance(value, list):
        # For example a LineString given [lng, lat] instead of [[lng, lat], ...].
        # Pydantic only turns ValueError into a validation error, so not TypeError.
        raise ValueError("coordinates are nested too shallowly for this geometry type")  # noqa: TRY004
    for item in value:
        yield from _walk(item, depth - 1)


def _position(value: Any) -> tuple[float, float]:
    """Validate one GeoJSON position and return it as (lng, lat)."""
    # A position is [lng, lat] or [lng, lat, elevation]. We keep only lng and lat.
    valid_shape = isinstance(value, list | tuple) and len(value) in (2, 3)
    # bool is a subclass of int in Python, so ``True`` would otherwise pass as 1.0.
    if not valid_shape or not all(
        isinstance(n, int | float) and not isinstance(n, bool) for n in value
    ):
        raise ValueError(f"a position must be [lng, lat] numbers, got {value!r}")
    lng, lat = float(value[0]), float(value[1])
    if not (-180 <= lng <= 180 and -90 <= lat <= 90):
        raise ValueError(f"position {value!r} is outside longitude/latitude ranges")
    return lng, lat


# ----------------------------------------------------------------------- datetimes


def as_utc(value: datetime) -> datetime:
    """Return ``value`` in UTC, rejecting datetimes with no time zone.

    A "naive" datetime (no tzinfo) is ambiguous: 15:00 in Edmonton is 22:00 UTC in
    winter. Every datetime in OpenPath carries a zone and is normalised to UTC, so
    comparisons and expiry checks are never off by hours.
    """
    if value.tzinfo is None:
        raise ValueError("datetimes must include a time zone; use UTC")
    return value.astimezone(UTC)


# --------------------------------------------------------------------- the record


class Obstruction(BaseModel):
    """One barrier from any source.

    Identity
        ``id`` is always ``"<source>:<source_ref>"``, for example
        ``"city_lrt_devices:1234"``. The store upserts on it, so the same elevator
        reported again updates the existing row instead of creating a duplicate.

    Lifetime
        ``observed_at`` is when the source last confirmed the item. ``expires_at`` is
        when we stop trusting it if the source goes quiet (the store deletes expired
        rows). ``starts_at`` / ``ends_at`` are the source's own schedule, for example a
        construction permit's dates; they may be missing.

    Immutability
        The model is frozen: build a new one instead of mutating. ``extra="forbid"``
        rejects unknown fields, so a typo in an adapter fails loudly in tests rather
        than silently dropping data.
    """

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
    detail: str | None = Field(default=None, max_length=500)  # plain-language detail
    geometry: Geometry
    confidence: Confidence
    starts_at: datetime | None = None  # when the barrier begins, if the source says
    ends_at: datetime | None = None  # when the source expects it to end, if known
    observed_at: datetime  # when the source last confirmed it
    expires_at: datetime  # when we drop it without a fresh confirmation
    url: str | None = Field(default=None, max_length=500)  # link to the official source
    attribution: str = Field(
        min_length=1, max_length=200
    )  # licence credit shown in the UI

    @model_validator(mode="before")
    @classmethod
    def _fill_id(cls, data: Any) -> Any:
        # Adapters do not have to compute the id themselves: leave it out and it is
        # derived from source and source_ref. Runs before field validation.
        if isinstance(data, dict) and not data.get("id"):
            data = {**data, "id": f"{data.get('source')}:{data.get('source_ref')}"}
        return data

    @field_validator("starts_at", "ends_at", "observed_at", "expires_at")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        # Normalise every datetime to UTC (and reject naive ones). See as_utc.
        return None if value is None else as_utc(value)

    @field_validator("url")
    @classmethod
    def _http_url(cls, value: str | None) -> str | None:
        # The URL is rendered as a link in the web app, so only web URLs are allowed.
        if value is not None and not value.startswith(("https://", "http://")):
            raise ValueError("url must start with https:// or http://")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> Obstruction:
        # Cross-field rules that need more than one field to check.
        if self.id != f"{self.source}:{self.source_ref}":
            raise ValueError('id must be "<source>:<source_ref>"')
        if self.expires_at <= self.observed_at:
            # An item that expires the moment it is observed would never be visible.
            raise ValueError("expires_at must be after observed_at")
        if self.starts_at and self.ends_at and self.ends_at < self.starts_at:
            raise ValueError("ends_at must not be before starts_at")
        return self

    def is_active(self, now: datetime) -> bool:
        """True when the item has started (or has no start) and has not expired."""
        now = as_utc(now)
        started = self.starts_at is None or self.starts_at <= now
        return started and self.expires_at > now

    def content_key(self) -> tuple[Any, ...]:
        """The fields that matter to a user, used to detect real changes.

        Sources are re-fetched every few minutes, and each fetch moves ``observed_at``
        and ``expires_at`` forward. That is not news. A change in title, location,
        effect or schedule IS news, and should trigger live updates and push alerts.
        The store compares this key to tell the two apart.
        """
        return (
            self.category,
            self.kind,
            self.effect,
            self.title,
            self.detail,
            self.geometry.model_dump_json(),  # JSON string so the tuple stays hashable
            self.confidence,
            self.starts_at,
            self.ends_at,
            self.url,
            self.attribution,
        )

    def to_feature(self) -> ObstructionFeature:
        """Convert to a GeoJSON Feature for the API and the map."""
        # GeoJSON keeps geometry at the top level and everything else in properties.
        properties = ObstructionProperties(**self.model_dump(exclude={"geometry"}))
        return ObstructionFeature(
            id=self.id, geometry=self.geometry, properties=properties
        )


# ------------------------------------------------------------- API response shapes
# These mirror the GeoJSON spec so MapLibre can render the response directly.


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
    """One GeoJSON Feature. ``type`` is fixed so the JSON always says "Feature"."""

    type: Literal["Feature"] = "Feature"
    id: str
    geometry: Geometry
    properties: ObstructionProperties


class ObstructionCollection(BaseModel):
    """Response of ``GET /api/obstructions``: a GeoJSON FeatureCollection.

    ``generated_at`` tells the client how fresh the whole response is.
    """

    type: Literal["FeatureCollection"] = "FeatureCollection"
    generated_at: datetime
    features: list[ObstructionFeature]


# ------------------------------------------------------------ adapter interface


@runtime_checkable
class SourceAdapter(Protocol):
    """The interface every data-source adapter implements (``cloud/app/adapters/``).

    A Protocol is "structural": a class does not inherit from it, it just needs the
    same attributes and methods. ``@runtime_checkable`` lets tests assert
    ``isinstance(adapter, SourceAdapter)``.

    Contract
        - ``source``: the adapter's name (matches SOURCE_PATTERN), used in every id.
        - ``cadence_s``: how often the scheduler (OP-103) calls ``fetch``, in seconds.
        - ``fetch(now)``: returns the COMPLETE current set of items for this source.
          The store then removes anything it held for the source that is no longer
          returned. Because of that, ``fetch`` must RAISE when the source cannot be
          read, rather than return an empty or partial list. A failure must never
          look like "everything is fine now" and wipe real barriers off the map.
    """

    source: str
    cadence_s: int

    def fetch(self, now: datetime) -> Sequence[Obstruction]: ...
