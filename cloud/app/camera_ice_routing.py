"""OP-205: connect stored camera evidence to routing and the route report.

``CameraIceHazardProvider`` is ``app.state.hazard_provider`` (``main.py``). It wraps
OP-206's matcher (``base``) and adds camera ice risks on the exact crossing edges
that ``CrossingIndex`` found. Camera risks never pass through the obstruction store
or the generic matcher, so the matcher's area rule (every ``weather`` item is
city-wide and changes no edges) never applies to them.

Each route request reads the camera store's unexpired readings (one small query)
and the weather service's cached watch. The rule runs again only when those inputs
change, a risk expires or ``RECHECK_S`` has passed (for time-dependent checks such
as the watch's age), and the merge with the matcher's map runs again only when
either side changed. Crossing matches are cached per camera position.

Nothing here may break routing: a failed camera read keeps the unexpired risks it
had, a failed matcher keeps the matcher's last good map, and any other error is
logged and the matcher's map is returned unchanged.

At shutdown ``stop()`` stops the camera reads (calls keep the last camera map) and
stops the matcher, so neither touches the database after the lifespan closes it.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime, timedelta

import numpy as np

from cloud.app.camera_ice import (
    CameraIceResult,
    CrossingIndex,
    MatchedIceRisk,
    Mode,
    match_camera_ice,
    weather_is_stale,
)
from cloud.app.network import WalkNetwork
from cloud.app.report import ItemIndex
from cloud.app.routing import EdgeHazard, HazardMap
from cloud.app.schemas import Effect, as_utc
from cloud.app.weather_service import WeatherService

log = logging.getLogger(__name__)

# Unchanged readings are evaluated again at least this often, so time-dependent
# checks (weather age, a reading that was a few seconds in the future) catch up.
RECHECK_S = 10.0
# What the rule reads from a stored reading; any change means evaluating again.
EVIDENCE_FIELDS = (
    "source",
    "source_ref",
    "label",
    "latitude",
    "longitude",
    "observed_at",
    "expires_at",
    "mode",
    "attribution",
)
UNAVAILABLE = "Camera evidence is unavailable; keeping unexpired prior risks."


def stored_camera_payload(row: object) -> dict[str, object]:
    """Accept older projections without source; preserve explicit source and credit.

    Keep explicit source values if supplied, so another source cannot silently
    become camera evidence. Other columns remain subject to evidence validation.
    """
    if isinstance(row, Mapping):
        return {"source": "camera_model", **row}
    dump = getattr(row, "model_dump", None)
    if callable(dump):
        return {"source": "camera_model", **dump()}
    fields = (
        "source_ref",
        "label",
        "latitude",
        "longitude",
        "observed_at",
        "expires_at",
        "mode",
        "attribution",
    )
    data = {name: getattr(row, name) for name in fields if hasattr(row, name)}
    return {"source": getattr(row, "source", "camera_model"), **data}


def evidence_key(payloads: Sequence[Mapping[str, object]]) -> tuple | None:
    """The fields the rule reads from every row, or None when a row cannot be keyed."""
    try:
        key = tuple(tuple(p.get(name) for name in EVIDENCE_FIELDS) for p in payloads)
        hash(key)
    except TypeError:  # an odd, unhashable value: evaluate on every call instead
        return None
    return key


def camera_ref(risk: MatchedIceRisk) -> str:
    return risk.obstruction.source_ref.removesuffix(":ice_risk")


class CameraIceHazardProvider:
    """``app.state.hazard_provider``: OP-206's hazards plus camera ice risks.

    Pass ``camera_store.latest`` as ``readings``, the app's ``WeatherService`` as
    ``weather`` (None when the scheduler is off: ``ice`` readings still count, ``wet``
    ones need the watch) and the matcher as ``base``. Construct once per network.

    ``latest`` holds the current risks with their full ``Obstruction`` records and
    the diagnostics; ``items`` indexes those records for the route report (OP-208).
    """

    def __init__(
        self,
        network: WalkNetwork,
        readings: Callable[[datetime], Iterable[object]],
        *,
        weather: WeatherService | None,
        base: Callable[[datetime], HazardMap] | None = None,
        mode: Mode = "live",
        recheck_s: float = RECHECK_S,
    ) -> None:
        if mode not in {"live", "replay"}:
            raise ValueError("mode must be live or replay")
        self.network = network
        self.crossings = CrossingIndex(network)
        self.readings, self.weather, self.base, self.mode = (
            readings,
            weather,
            base,
            mode,
        )
        self.recheck = timedelta(seconds=recheck_s)
        self.latest = CameraIceResult(mode, (), (), frozenset(), frozenset())
        self.items: ItemIndex | None = None  # report records of latest.risks
        self._retained: dict[str, MatchedIceRisk] = {}
        self._camera: HazardMap = {}
        self._key: tuple | None = None
        self._checked_at: datetime | None = None
        self._valid_until: datetime | None = None
        self._base_map: HazardMap = {}  # the matcher's last good map
        self._merged: HazardMap = {}
        self._merged_from: tuple[object, object] | None = None
        self._lock = threading.Lock()
        self._stopped = False  # set by stop(): no more camera reads

    def stop(self, timeout_s: float | None = None) -> bool:
        """Shutdown: read no more camera readings, then stop the matcher (``base``).

        Its own reads run in the caller's thread (requests, the push watcher, the
        live hub), which the lifespan has stopped first. Returns the matcher's
        ``stop()`` result: False if a rebuild is still running after ``timeout_s``.
        """
        self._stopped = True
        stop = getattr(self.base, "stop", None)
        if not callable(stop):
            return True
        return bool(stop() if timeout_s is None else stop(timeout_s))

    def __call__(self, now: datetime) -> HazardMap:
        now = as_utc(now)
        base = self._read_base(now)
        try:
            camera = self._camera_hazards(now)
            with self._lock:
                merged_from = self._merged_from
                if (
                    merged_from is None
                    or merged_from[0] is not base
                    or merged_from[1] is not camera
                ):
                    self._merged = merge(base, camera)
                    self._merged_from = (base, camera)
                return self._merged
        except Exception:  # routing must keep working without camera risks
            log.exception("CAMERA ice risks failed; routing with the matcher's map")
            return base

    def _read_base(self, now: datetime) -> HazardMap:
        if self.base is None:
            return self._base_map
        try:
            self._base_map = self.base(now)
        except Exception:  # the matcher never raises, but keep its last good map
            log.exception("MATCH provider failed; keeping its last good hazard map")
        return self._base_map

    def _camera_hazards(self, now: datetime) -> HazardMap:
        """The camera hazard map at ``now``; the same object while nothing changed."""
        if self._stopped:  # shutting down: the last map, never a database read
            return self._camera
        failed: Exception | None = None
        payloads: list[dict[str, object]] = []
        try:
            payloads = [stored_camera_payload(row) for row in self.readings(now)]
        except Exception as error:  # noqa: BLE001 - logged below, by type only
            failed = error  # a database error must not break routing
        weather = self.weather
        watch = weather.last_good if weather is not None else None
        stale = weather_is_stale(weather)
        rows = None if failed is not None else evidence_key(payloads)
        key = None if rows is None else (rows, watch, stale)
        with self._lock:
            if (
                key is not None
                and key == self._key
                and self._checked_at is not None
                and self._valid_until is not None
                and self._checked_at <= now < self._valid_until
            ):
                return self._camera
            self._retained = {
                ref: risk
                for ref, risk in self._retained.items()
                if risk.obstruction.expires_at > now
            }
            if failed is not None:
                # Database errors may include connection details: log the type only,
                # expose a fixed diagnostic, and keep the original, expiring evidence.
                log.warning(
                    "CAMERA readings unavailable (%s); keeping unexpired ice risks",
                    type(failed).__name__,
                )
                result = CameraIceResult(
                    self.mode,
                    (),
                    (UNAVAILABLE,),
                    frozenset(),
                    frozenset(self._retained),
                )
            else:
                result = match_camera_ice(
                    payloads,
                    watch,
                    self.crossings,
                    now=now,
                    mode=self.mode,
                    weather_stale=stale,
                )
            for ref in result.clear_refs:
                self._retained.pop(ref, None)
            for risk in result.risks:
                self._retained[camera_ref(risk)] = risk
            risks = tuple(self._retained[ref] for ref in sorted(self._retained))
            latest = CameraIceResult(
                result.mode,
                risks,
                result.warnings,
                result.clear_refs,
                result.uncertain_refs,
            )
            if risks != self.latest.risks:
                self._log_changes(self.latest.risks, risks)
                self._camera = latest.hazards()
                self.items = self._index(risks)
            self.latest = latest
            self._key = key
            self._checked_at = now
            self._valid_until = min(
                [now + self.recheck, *(r.obstruction.expires_at for r in risks)]
            )
            return self._camera

    def _index(self, risks: Sequence[MatchedIceRisk]) -> ItemIndex | None:
        """Report records for the risks; the report falls back to hazards without it."""
        if not risks:
            return None
        try:
            return ItemIndex.build([risk.obstruction for risk in risks], self._to_xy)
        except Exception:
            log.exception("REPORT camera index failed; listing camera hazards only")
            return None

    def _to_xy(self, positions: Sequence[Sequence[float]]) -> np.ndarray:
        """(lng, lat) positions in the network's metres, as the matcher projects them."""
        array = np.asarray(positions, dtype=np.float64).reshape(-1, 2)
        return self.network._to_xy(array[:, 1], array[:, 0])

    @staticmethod
    def _log_changes(
        before: Sequence[MatchedIceRisk], after: Sequence[MatchedIceRisk]
    ) -> None:
        old = {risk.obstruction.id: risk for risk in before}
        new = {risk.obstruction.id: risk for risk in after}
        for item_id in sorted(new.keys() - old.keys()):
            log.info(
                "CHANGE %s new ice_risk on %d crossings",
                item_id,
                len(new[item_id].edge_indices),
            )
        for item_id in sorted(old.keys() - new.keys()):
            log.info("CHANGE %s cleared", item_id)


def merge(
    base: HazardMap, camera: Mapping[int, Sequence[EdgeHazard]]
) -> dict[int, list[EdgeHazard]]:
    """The matcher's hazards plus the camera ones, without mutating either map.

    A camera ``ice_risk`` that reached the matcher as a generic point is dropped: only
    the exact crossing matches count. Blocked barriers and every other hazard stay;
    routing applies its strongest-effect rule per profile (OP-207).
    """
    hazards = {
        int(edge): [
            hazard
            for hazard in items
            if not (
                hazard.item_id.startswith("camera_model:")
                and hazard.effect == Effect.ICE_RISK
            )
        ]
        for edge, items in base.items()
    }
    hazards = {edge: items for edge, items in hazards.items() if items}
    for edge, items in camera.items():
        existing = hazards.setdefault(edge, [])
        known = {previous.item_id for previous in existing}
        existing.extend(item for item in items if item.item_id not in known)
    return hazards
