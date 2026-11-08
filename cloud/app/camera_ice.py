"""OP-205: attach local ice risk to crossings using camera and weather evidence.

An ice reading is sufficient; a wet reading also needs a fresh, active freezing
weather watch. Match only crossing polylines within 50 m. This helper prepares
existing Obstruction records and explicit edge matches without changing shared
schemas, the camera-ingest API, source configuration or routing weights.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import Lock
from typing import Literal

import numpy as np
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from cloud.app.adapters.weather import CLOCK_SKEW, MAX_OBSERVATION_AGE
from cloud.app.network import EARTH_RADIUS_M, EDGE_CROSSING, WalkNetwork
from cloud.app.routing import EdgeHazard, HazardMap
from cloud.app.schemas import Confidence, Effect, Geometry, Kind, Obstruction, as_utc
from cloud.app.weather_service import REFRESH_SECONDS, WeatherResponse, WeatherService

CROSSING_RADIUS_M = 50.0
POSITION_CACHE_SIZE = 512
WEATHER_CREDIT = "Weather: Environment and Climate Change Canada, Open-Meteo"
Mode = Literal["live", "replay"]


class CameraEvidence(BaseModel):
    """A local view of confirmed OP-204 readings, not a new shared API contract.

    Accept worker objects or their JSON dictionaries. Ignore unrelated fields
    such as model score/version; OP-204 has already applied its confidence gate
    and two-observation stability before emitting a reading.
    """

    model_config = ConfigDict(
        extra="ignore", from_attributes=True, frozen=True, allow_inf_nan=False
    )
    source: Literal["camera_model"] = "camera_model"
    source_ref: str = Field(min_length=1, max_length=190)
    label: Literal["dry", "wet", "snow_partial", "snow_covered", "ice", "unknown"]
    latitude: float = Field(ge=-90, le=90, strict=True)
    longitude: float = Field(ge=-180, le=180, strict=True)
    observed_at: datetime
    expires_at: datetime
    mode: Mode
    attribution: str = Field(
        default="Road camera data: 511 Alberta, Government of Alberta", max_length=200
    )

    @field_validator("source_ref")
    @classmethod
    def nonblank_ref(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Camera reference must not be blank")
        return value.strip()

    @field_validator("observed_at", "expires_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return as_utc(value)

    @model_validator(mode="after")
    def valid_lifetime(self) -> CameraEvidence:
        if self.expires_at <= self.observed_at:
            raise ValueError("Camera expiry must follow its observation")
        return self


@dataclass(frozen=True)
class IceDecision:
    active: bool
    reason: str
    expires_at: datetime | None = None
    uncertain: bool = False


def evaluate_camera_ice(
    camera: CameraEvidence,
    watch: WeatherResponse | None,
    *,
    now: datetime,
    mode: Mode = "live",
    weather_stale: bool = False,
) -> IceDecision:
    """Apply PLAN.md 7.4 without treating unavailable evidence as clear weather.

    Pass age-based weather freshness separately; one failed refresh does not
    invalidate the last good watch. OP-109 also bounds observation age.
    Camera timestamps are never refreshed by this calculation.
    """
    now = as_utc(now)
    if mode not in {"live", "replay"}:
        raise ValueError("mode must be live or replay")
    if camera.mode != mode:
        return IceDecision(
            False, "Camera mode differs from application mode.", uncertain=True
        )
    if camera.observed_at > now + CLOCK_SKEW or camera.expires_at <= now:
        return IceDecision(
            False, "Camera reading is future-dated or expired.", uncertain=True
        )
    if camera.label == "ice":
        return IceDecision(
            True,
            "Camera indicates an icy surface; ice risk at nearby crossings.",
            camera.expires_at,
        )
    if camera.label == "unknown":
        return IceDecision(False, "Camera conditions are unavailable.", uncertain=True)
    if camera.label != "wet":
        return IceDecision(False, "Camera does not indicate wet or icy conditions.")
    if watch is None or watch.active is None or watch.observed_at is None:
        return IceDecision(
            False, "Wet camera reading has no usable weather watch.", uncertain=True
        )
    if (
        weather_stale
        or watch.mode != mode
        or not (
            watch.observed_at <= now + CLOCK_SKEW
            and now < watch.observed_at + MAX_OBSERVATION_AGE
        )
    ):
        return IceDecision(
            False,
            "Wet camera reading has stale, future or mismatched weather.",
            uncertain=True,
        )
    if watch.air_c is None or watch.ground_c is None:
        return IceDecision(
            False, "Weather temperatures are incomplete.", uncertain=True
        )
    if not watch.active:
        return IceDecision(
            False, "Wet surface without an active freezing-weather watch."
        )
    if watch.air_c > 0 and watch.ground_c > 0:
        # Consistency guard for malformed cached watches, not a second ice rule.
        return IceDecision(
            False,
            "Active watch has no freezing air or ground temperature.",
            uncertain=True,
        )
    return IceDecision(
        True,
        "Wet camera surface with an active freezing-weather watch; ice risk at nearby crossings.",
        min(camera.expires_at, watch.observed_at + MAX_OBSERVATION_AGE),
    )


class CrossingIndex:
    """Build once from OP-104's crossing subset; query full polylines in NumPy.

    Each camera query uses a local metre projection around that camera. Distances
    use stored geometry, including interior bends, not only graph nodes. Float32
    network geometry limits location precision; no extra matching buffer is added.
    """

    def __init__(self, network: WalkNetwork) -> None:
        crossings = np.flatnonzero(network.edge_flags & EDGE_CROSSING)
        counts = np.diff(network.geom_offsets)[crossings] - 1
        if np.any(counts < 1):
            raise ValueError("Crossing geometry needs at least two positions")
        starts = (
            np.concatenate(
                [
                    np.arange(
                        network.geom_offsets[edge], network.geom_offsets[edge + 1] - 1
                    )
                    for edge in crossings
                ]
            ).astype(np.intp)
            if len(crossings)
            else np.array([], dtype=np.intp)
        )
        self._cache: OrderedDict[tuple[float, float], frozenset[int]] = OrderedDict()
        self._cache_lock = Lock()
        self._edges = np.repeat(crossings, counts)
        self._first = network.geom_coords[starts].astype(np.float64)
        self._second = network.geom_coords[starts + 1].astype(np.float64)

    def nearby(self, *, latitude: float, longitude: float) -> frozenset[int]:
        """Return only crossing edge indices at most 50 m from this camera."""
        if (
            isinstance(latitude, bool)
            or isinstance(longitude, bool)
            or not np.isfinite([latitude, longitude]).all()
            or not -90 <= latitude <= 90
            or not -180 <= longitude <= 180
        ):
            raise ValueError("Invalid camera coordinates")
        position = (latitude, longitude)
        with self._cache_lock:
            cached = self._cache.get(position)
            if cached is not None:
                self._cache.move_to_end(position)
                return cached
        matches = self._nearby_uncached(latitude=latitude, longitude=longitude)
        with self._cache_lock:
            self._cache[position] = matches
            self._cache.move_to_end(position)
            if len(self._cache) > POSITION_CACHE_SIZE:
                self._cache.popitem(last=False)
        return matches

    def _nearby_uncached(self, *, latitude: float, longitude: float) -> frozenset[int]:
        # Static camera positions reuse matches, including empty results. The
        # bounded cache belongs to this graph and is discarded with the index.
        scale = np.radians([1, 1]) * EARTH_RADIUS_M
        scale[0] *= np.cos(np.radians(latitude))
        origin = np.array([longitude, latitude])
        first = (self._first - origin) * scale
        second = (self._second - origin) * scale
        delta = second - first
        squared = np.einsum("ij,ij->i", delta, delta)
        numerator = -np.einsum("ij,ij->i", first, delta)
        fraction = np.divide(
            numerator, squared, out=np.zeros_like(squared), where=squared > 0
        )
        fraction = np.clip(fraction, 0, 1)
        closest = first + fraction[:, None] * delta
        distance = np.hypot(closest[:, 0], closest[:, 1])
        # Tiny arithmetic tolerance only, not a wider geographic matching radius.
        return frozenset(
            int(edge) for edge in self._edges[distance <= CROSSING_RADIUS_M + 1e-7]
        )


@dataclass(frozen=True)
class MatchedIceRisk:
    obstruction: Obstruction
    edge_indices: frozenset[int]


@dataclass(frozen=True)
class CameraIceResult:
    mode: Mode
    risks: tuple[MatchedIceRisk, ...]
    warnings: tuple[str, ...]
    clear_refs: frozenset[str]
    uncertain_refs: frozenset[str]

    def by_edge(self) -> dict[int, tuple[Obstruction, ...]]:
        """Explicit matches for OP-206/207; never rematch these as generic points."""
        grouped: dict[int, list[Obstruction]] = {}
        for risk in self.risks:
            for edge in sorted(risk.edge_indices):
                grouped.setdefault(edge, []).append(risk.obstruction)
        return {edge: tuple(items) for edge, items in sorted(grouped.items())}

    def hazards(self) -> HazardMap:
        """Use the existing routing contract with the exact crossing matches."""
        return {
            edge: tuple(
                EdgeHazard(o.id, o.effect, o.confidence, o.title) for o in items
            )
            for edge, items in self.by_edge().items()
        }


def match_camera_ice(
    readings: Iterable[object],
    watch: WeatherResponse | None,
    crossings: CrossingIndex,
    *,
    now: datetime,
    mode: Mode = "live",
    weather_stale: bool = False,
) -> CameraIceResult:
    """Prepare a snapshot of local risks; do not fetch, upsert, clear or reroute.

    Keep the newest reading per source_ref. Contradictory rows with the same
    timestamp are uncertain, not an input-order-dependent hazard decision.
    """
    now = as_utc(now)
    if mode not in {"live", "replay"}:
        raise ValueError("mode must be live or replay")
    latest: dict[str, CameraEvidence] = {}
    conflicts: set[str] = set()
    warnings: set[str] = set()
    for number, row in enumerate(readings, start=1):
        try:
            camera = CameraEvidence.model_validate(row)
        except ValidationError:
            warnings.add(f"Camera row {number}: invalid reading; skipped.")
            continue
        before = latest.get(camera.source_ref)
        if before is None or camera.observed_at > before.observed_at:
            latest[camera.source_ref] = camera
            conflicts.discard(camera.source_ref)
        elif camera.observed_at == before.observed_at and camera != before:
            conflicts.add(camera.source_ref)
    risks = []
    clear_refs: set[str] = set()
    uncertain_refs: set[str] = set()
    for ref, camera in sorted(latest.items()):
        if ref in conflicts:
            uncertain_refs.add(ref)
            warnings.add(
                f"{ref}: conflicting camera readings at the same time; skipped."
            )
            continue
        decision = evaluate_camera_ice(
            camera, watch, now=now, mode=mode, weather_stale=weather_stale
        )
        if not decision.active:
            if decision.uncertain:
                uncertain_refs.add(ref)
                warnings.add(f"{ref}: {decision.reason}")
            else:
                clear_refs.add(ref)
            continue
        edges = crossings.nearby(latitude=camera.latitude, longitude=camera.longitude)
        if not edges:
            uncertain_refs.add(ref)
            warnings.add(
                f"{ref}: ice-risk evidence has no mapped crossing within 50 m."
            )
            continue
        attribution = camera.attribution
        detail = decision.reason
        if camera.label == "wet":
            combined = f"{attribution}; {WEATHER_CREDIT}"
            if len(combined) <= 200:
                attribution = combined
            else:
                # Preserve the complete upstream credit within the shared limit.
                detail += f" {WEATHER_CREDIT}."
        obstruction = Obstruction(
            source="camera_model",
            source_ref=f"{ref}:ice_risk",
            category="weather",
            kind=Kind.ICE_RISK,
            effect=Effect.ICE_RISK,
            confidence=Confidence.MEDIUM,
            title="Ice risk at nearby crossings",
            detail=detail,
            geometry=Geometry(
                type="Point", coordinates=[camera.longitude, camera.latitude]
            ),
            observed_at=camera.observed_at,
            expires_at=decision.expires_at,
            attribution=attribution,
        )
        risks.append(MatchedIceRisk(obstruction, edges))
    return CameraIceResult(
        mode,
        tuple(risks),
        tuple(sorted(warnings)),
        frozenset(clear_refs),
        frozenset(uncertain_refs),
    )


def match_service_camera_ice(
    readings: Iterable[object],
    weather: WeatherService | None,
    crossings: CrossingIndex,
    *,
    now: datetime,
    mode: Mode = "live",
) -> CameraIceResult:
    """Use OP-109's real freshness state without changing its shared response."""
    return match_camera_ice(
        readings,
        weather.last_good if weather else None,
        crossings,
        now=now,
        mode=mode,
        weather_stale=(
            weather is None
            or weather.last_success is None
            or as_utc(now) - weather.last_success
            > timedelta(seconds=3 * REFRESH_SECONDS)
        ),
    )
