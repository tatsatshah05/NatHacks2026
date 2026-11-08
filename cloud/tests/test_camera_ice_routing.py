"""OP-205: stored camera readings change real OP-207 routes on exact crossings.

Verify the hazard-provider handoff, profile effects and last-good expiry without
the pending API branch, a trained model or external requests.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from cloud.app.camera_ice_routing import CameraIceHazardProvider
from cloud.app.network import RawEdge, RawNode, WalkNetwork, export_network
from cloud.app.profiles import load_routing_config
from cloud.app.routing import EdgeHazard, Router, profile_weights
from cloud.app.schemas import Confidence, Effect
from cloud.app.weather_service import WeatherService
from cloud.tests.test_camera_ice import NOW, coordinate, evidence, watch


def diamond_network() -> WalkNetwork:
    positions = [
        coordinate(x, y) for x, y in [(-100, 0), (-40, 0), (40, 0), (100, 0), (0, 60)]
    ]
    nodes = [RawNode(i + 1, lat, lng) for i, (lng, lat) in enumerate(positions)]
    edges = [
        RawEdge(u, v, length, highway="footway", footway=kind, osmid=100 + i)
        for i, (u, v, length, kind) in enumerate(
            [
                (1, 2, 60, "sidewalk"),
                (2, 3, 80, "crossing"),
                (3, 4, 60, "sidewalk"),
                (2, 5, 80, "sidewalk"),
                (5, 3, 80, "sidewalk"),
            ]
        )
    ]
    return WalkNetwork.from_data(
        export_network(
            nodes, edges, source="OP-205 synthetic integration", built_at=NOW
        )
    )


def weather_service() -> WeatherService:
    async def unused(now):
        raise AssertionError("Provider only reads the existing weather cache")

    service = WeatherService(unused, clock=lambda: NOW)
    service.last_good = watch()
    service.last_success = NOW
    return service


def stored_row(**updates: object) -> SimpleNamespace:
    fields = evidence(**updates).model_dump(exclude={"source"})
    return SimpleNamespace(**fields, received_at=NOW, changed_at=NOW)


@pytest.mark.parametrize(
    "profile,detours",
    [
        ("walker_cane", True),
        ("wheelchair", True),
        ("low_vision", True),
        ("standard", False),
    ],
)
def test_camera_store_rows_drive_profile_routes_and_route_report(
    profile: str, detours: bool
) -> None:
    network = diamond_network()
    provider = CameraIceHazardProvider(
        network, lambda now: [stored_row(label="wet")], weather=weather_service()
    )
    router = Router(network, load_routing_config())
    start, end = coordinate(-100, 0), coordinate(100, 0)
    baseline = router.route(start[1], start[0], end[1], end[0], profile).routes[0]
    hazards = provider(NOW)
    assert set(hazards) == {1}  # nearby ordinary sidewalks never receive ice risk
    route = router.route(start[1], start[0], end[1], end[0], profile, hazards).routes[0]
    assert (route.edges != baseline.edges) is detours
    report = route.hazards_avoided if detours else route.hazards_on_route
    assert any(
        h.effect == Effect.ICE_RISK and h.confidence == Confidence.MEDIUM
        for h in report
    )
    if detours:
        assert 1 not in route.edges and route.reason


def test_unknown_and_failed_weather_retain_risk_without_renewing_expiry() -> None:
    rows = [stored_row(label="wet")]
    service = weather_service()
    provider = CameraIceHazardProvider(
        diamond_network(), lambda now: rows, weather=service
    )
    assert provider(NOW)
    saved = provider.latest.risks[0]
    service.last_error = "synthetic source failure"
    assert provider(NOW + timedelta(seconds=1))
    assert provider.latest.risks[0] == saved
    rows[:] = [stored_row(label="unknown", observed_at=NOW)]
    assert provider(NOW + timedelta(seconds=2))
    assert provider.latest.risks[0] == saved
    assert provider.latest.warnings
    assert not provider(saved.obstruction.expires_at)
    assert not provider.latest.risks


def test_missing_readings_and_store_failure_preserve_original_unexpired_risk() -> None:
    rows = [stored_row()]
    failed = False

    def read(now):
        if failed:
            raise RuntimeError("private database connection detail")
        return rows

    provider = CameraIceHazardProvider(diamond_network(), read, weather=None)
    assert provider(NOW)
    saved = provider.latest.risks[0]
    rows.clear()
    assert provider(NOW + timedelta(seconds=1))
    assert provider.latest.risks[0] == saved
    failed = True
    assert provider(NOW + timedelta(seconds=2))
    assert provider.latest.risks[0] == saved
    assert all("private" not in message for message in provider.latest.warnings)


def test_current_warm_wet_or_dry_reading_clears_only_that_cameras_ice_risk() -> None:
    rows = [stored_row(label="wet"), stored_row(source_ref="511:9002:1")]
    service = weather_service()
    provider = CameraIceHazardProvider(
        diamond_network(), lambda now: rows, weather=service
    )
    assert len(provider(NOW)[1]) == 2
    service.last_good = watch(active=False, air_c=5, ground_c=5)
    assert len(provider(NOW + timedelta(seconds=1))[1]) == 1
    assert provider.latest.risks[0].obstruction.source_ref.startswith("511:9002:")
    rows[:] = [stored_row(source_ref="511:9002:1", label="dry", observed_at=NOW)]
    assert not provider(NOW + timedelta(seconds=2))


def test_composition_preserves_blocked_barriers_without_mutating_generic_matcher() -> (
    None
):
    blocked = EdgeHazard("city_construction:permit", Effect.BLOCKED, Confidence.HIGH)
    generic_ice = EdgeHazard(
        "camera_model:511:9001:1:ice_risk", Effect.ICE_RISK, Confidence.MEDIUM
    )
    base = {0: [generic_ice, blocked], 1: [blocked]}
    network = diamond_network()
    provider = CameraIceHazardProvider(
        network, lambda now: [stored_row()], weather=None, base=lambda now: base
    )
    hazards = provider(NOW)
    assert hazards[0] == [blocked]  # remove an incorrect generic Point match
    assert len(hazards[1]) == 2
    assert base == {0: [generic_ice, blocked], 1: [blocked]}
    for profile in load_routing_config().profiles.values():
        assert profile_weights(network, profile, hazards)[1] == 0


def test_explicit_non_camera_source_is_not_relabelled() -> None:
    row = {**evidence().model_dump(), "source": "community_report"}
    provider = CameraIceHazardProvider(
        diamond_network(), lambda now: [row], weather=None
    )
    assert not provider(NOW)
    assert provider.latest.warnings
