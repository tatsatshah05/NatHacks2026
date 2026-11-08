"""OP-205: connect stored camera evidence to the existing routing hazard hook.

Read the camera store and weather service, retain uncertain risks only until
their original expiry, and merge exact crossing matches with other hazards.
This module changes no endpoint, shared schema, profile or matcher contract.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime

from cloud.app.camera_ice import (
    CameraIceResult,
    CrossingIndex,
    MatchedIceRisk,
    Mode,
    match_service_camera_ice,
)
from cloud.app.network import WalkNetwork
from cloud.app.routing import EdgeHazard, HazardMap
from cloud.app.schemas import Effect, as_utc
from cloud.app.weather_service import WeatherService


def stored_camera_payload(row: object) -> object:
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


class CameraIceHazardProvider:
    """Callable for app.state.hazard_provider; compose with OP-206, not replace it.

    Pass camera_store.latest as readings, the current WeatherService as weather,
    and the existing ordinary HazardProvider as base. Construct once per graph.
    latest exposes diagnostics/full records for the route-report owner.
    """

    def __init__(
        self,
        network: WalkNetwork,
        readings: Callable[[datetime], Iterable[object]],
        *,
        weather: WeatherService | None,
        base: Callable[[datetime], HazardMap] | None = None,
        mode: Mode = "live",
    ) -> None:
        if mode not in {"live", "replay"}:
            raise ValueError("mode must be live or replay")
        self.crossings = CrossingIndex(network)
        self.readings, self.weather, self.base, self.mode = (
            readings,
            weather,
            base,
            mode,
        )
        self.latest = CameraIceResult(mode, (), (), frozenset(), frozenset())
        self._retained: dict[str, MatchedIceRisk] = {}
        self._lock = threading.Lock()

    def __call__(self, now: datetime) -> dict[int, list[EdgeHazard]]:
        now = as_utc(now)
        with self._lock:
            self._retained = {
                ref: risk
                for ref, risk in self._retained.items()
                if risk.obstruction.expires_at > now
            }
            try:
                payloads = [stored_camera_payload(row) for row in self.readings(now)]
                result = match_service_camera_ice(
                    payloads,
                    self.weather,
                    self.crossings,
                    now=now,
                    mode=self.mode,
                )
            except Exception:  # noqa: BLE001 - A failed camera store must not break other routing.
                # Database/provider errors may include connection details. Expose
                # a fixed diagnostic and retain the original, expiring evidence.
                result = CameraIceResult(
                    self.mode,
                    (),
                    ("Camera evidence is unavailable; keeping unexpired prior risks.",),
                    frozenset(),
                    frozenset(self._retained),
                )
            for ref in result.clear_refs:
                self._retained.pop(ref, None)
            for risk in result.risks:
                ref = risk.obstruction.source_ref.removesuffix(":ice_risk")
                self._retained[ref] = risk
            self.latest = CameraIceResult(
                result.mode,
                tuple(self._retained[ref] for ref in sorted(self._retained)),
                result.warnings,
                result.clear_refs,
                result.uncertain_refs,
            )
            # Copy OP-206's lists: never mutate its cached map or drop blocked
            # barriers. OP-207 still owns strongest-effect/profile composition.
            hazards = {
                int(edge): [
                    hazard
                    for hazard in items
                    if not (
                        hazard.item_id.startswith("camera_model:")
                        and hazard.effect == Effect.ICE_RISK
                    )
                ]
                for edge, items in (self.base(now).items() if self.base else [])
            }
            hazards = {edge: items for edge, items in hazards.items() if items}
            for edge, items in self.latest.hazards().items():
                existing = hazards.setdefault(edge, [])
                known = {previous.item_id for previous in existing}
                existing.extend(item for item in items if item.item_id not in known)
            return hazards
