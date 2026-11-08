"""OP-205: verify camera/weather ice rules and crossing-only spatial matches.

Use synthetic confirmed readings and small OP-104 networks, plus a check against
the committed city network. No camera frames or network requests are needed.
"""

from __future__ import annotations

import asyncio
import math
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import numpy as np
import pytest
from pydantic import ValidationError

from cloud.app.camera_ice import (
    CameraEvidence,
    CrossingIndex,
    evaluate_camera_ice,
    match_camera_ice,
    match_service_camera_ice,
)
from cloud.app.network import (
    EARTH_RADIUS_M,
    EDGE_CROSSING,
    RawEdge,
    RawNode,
    WalkNetwork,
    export_network,
)
from cloud.app.schemas import Confidence, Effect, Kind
from cloud.app.weather_service import WeatherResponse, WeatherService

NOW = datetime(2026, 11, 8, 3, tzinfo=UTC)
LAT, LNG = 53.544, -113.49


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("OP-205 tests must not call the network")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked)


def evidence(**updates: object) -> CameraEvidence:
    return CameraEvidence.model_validate(
        {
            "source": "camera_model",
            "source_ref": "511:9001:1",
            "label": "ice",
            "latitude": LAT,
            "longitude": LNG,
            "observed_at": NOW - timedelta(minutes=5),
            "expires_at": NOW + timedelta(minutes=10),
            "mode": "live",
            **updates,
        }
    )


def watch(**updates: object) -> WeatherResponse:
    return WeatherResponse.model_validate(
        {
            "active": True,
            "reason": "Freezing after precipitation.",
            "air_c": -1,
            "ground_c": -2,
            "dew_c": -2,
            "observed_at": NOW - timedelta(minutes=5),
            "mode": "live",
            **updates,
        }
    )


def coordinate(x_m: float, y_m: float) -> tuple[float, float]:
    return (
        LNG + math.degrees(x_m / EARTH_RADIUS_M / math.cos(math.radians(LAT))),
        LAT + math.degrees(y_m / EARTH_RADIUS_M),
    )


def network_for(paths: list[tuple[list[tuple[float, float]], object]]) -> WalkNetwork:
    nodes, edges = [], []
    for index, (points, footway) in enumerate(paths):
        positions = [coordinate(x, y) for x, y in points]
        u, v = 2 * index + 1, 2 * index + 2
        nodes.extend(
            [
                RawNode(u, positions[0][1], positions[0][0]),
                RawNode(v, positions[-1][1], positions[-1][0]),
            ]
        )
        edges.append(
            RawEdge(
                u,
                v,
                200,
                highway="footway",
                footway=footway,
                osmid=1000 + index,
                geometry=positions,
            )
        )
    return WalkNetwork.from_data(
        export_network(nodes, edges, source="OP-205 synthetic tests", built_at=NOW)
    )


@pytest.fixture
def crossings() -> CrossingIndex:
    return CrossingIndex(
        network_for(
            [
                ([(-100, 0), (100, 0)], "crossing"),
                ([(-100, 10), (100, 10)], "sidewalk"),
                ([(-100, 90), (100, 90)], "crossing"),
                ([(10, -20), (10, 20)], ["sidewalk", "crossing"]),
            ]
        )
    )


@pytest.mark.parametrize(
    "weather", [None, watch(active=False, air_c=10, ground_c=10), watch()]
)
def test_camera_ice_activates_even_without_a_weather_watch(
    weather: WeatherResponse | None,
) -> None:
    camera = evidence()
    decision = evaluate_camera_ice(camera, weather, now=NOW, weather_stale=True)
    assert decision.active
    assert decision.expires_at == camera.expires_at


@pytest.mark.parametrize("air,ground", [(-1, 2), (2, -1), (0, 2), (2, 0), (-1, -2)])
def test_wet_with_active_watch_and_freezing_air_or_ground_activates(
    air: float, ground: float
) -> None:
    decision = evaluate_camera_ice(
        evidence(label="wet"), watch(air_c=air, ground_c=ground), now=NOW
    )
    assert decision.active
    assert not decision.uncertain


def test_wet_without_freezing_never_activates_even_if_active_flag_is_wrong() -> None:
    decision = evaluate_camera_ice(
        evidence(label="wet"), watch(air_c=2, ground_c=3), now=NOW
    )
    assert not decision.active
    assert decision.uncertain


def test_wet_with_inactive_watch_is_not_ice_risk() -> None:
    decision = evaluate_camera_ice(evidence(label="wet"), watch(active=False), now=NOW)
    assert not decision.active
    assert not decision.uncertain


@pytest.mark.parametrize("label", ["dry", "snow_partial", "snow_covered"])
def test_weather_watch_alone_never_creates_local_ice_risk(label: str) -> None:
    assert not evaluate_camera_ice(evidence(label=label), watch(), now=NOW).active


@pytest.mark.parametrize("label", ["unknown", "unusable"])
def test_unusable_camera_is_uncertain_instead_of_clear(label: str) -> None:
    decision = evaluate_camera_ice(evidence(label=label), watch(), now=NOW)
    assert not decision.active and decision.uncertain


@pytest.mark.parametrize(
    "weather",
    [
        None,
        WeatherResponse(),
        watch(observed_at=None),
        watch(air_c=None),
        watch(ground_c=None),
    ],
)
def test_wet_with_missing_weather_is_uncertain(weather: WeatherResponse | None) -> None:
    decision = evaluate_camera_ice(evidence(label="wet"), weather, now=NOW)
    assert not decision.active and decision.uncertain


@pytest.mark.parametrize(
    "update",
    [
        {"observed_at": NOW - timedelta(hours=2)},
        {"observed_at": NOW + timedelta(seconds=1)},
        {"mode": "replay"},
    ],
)
def test_wet_does_not_use_expired_future_or_wrong_mode_weather(
    update: dict[str, object],
) -> None:
    decision = evaluate_camera_ice(evidence(label="wet"), watch(**update), now=NOW)
    assert not decision.active and decision.uncertain


def test_service_stale_flag_prevents_using_a_recent_cached_watch() -> None:
    assert not evaluate_camera_ice(
        evidence(label="wet"), watch(), now=NOW, weather_stale=True
    ).active


@pytest.mark.parametrize(
    "update",
    [
        {"expires_at": NOW},
        {"observed_at": NOW + timedelta(seconds=1)},
        {"mode": "replay"},
    ],
)
def test_ice_camera_must_itself_be_current_and_in_application_mode(
    update: dict[str, object],
) -> None:
    decision = evaluate_camera_ice(evidence(**update), watch(), now=NOW)
    assert not decision.active and decision.uncertain


def test_replay_evidence_can_only_produce_replay_mode_results(
    crossings: CrossingIndex,
) -> None:
    camera, weather = evidence(mode="replay"), watch(mode="replay")
    assert not match_camera_ice([camera], weather, crossings, now=NOW).risks
    assert match_camera_ice([camera], weather, crossings, now=NOW, mode="replay").risks


def test_wet_risk_expires_with_its_oldest_expiring_evidence() -> None:
    camera = evidence(label="wet", observed_at=NOW - timedelta(seconds=5))
    decision = evaluate_camera_ice(
        camera, watch(observed_at=NOW - timedelta(hours=1, minutes=59)), now=NOW
    )
    assert decision.expires_at == NOW + timedelta(minutes=1)
    assert camera.observed_at == NOW - timedelta(seconds=5)


def test_matching_uses_crossing_polyline_interior_and_crossing_flag(
    crossings: CrossingIndex,
) -> None:
    # Both endpoints are over 100 m away; the camera is close to the edge interior.
    assert crossings.nearby(latitude=LAT, longitude=LNG) == frozenset({0, 3})
    assert 1 not in crossings.nearby(latitude=LAT, longitude=LNG)  # adjacent sidewalk


def test_curved_crossing_uses_all_segments_not_endpoint_chord() -> None:
    network = network_for([([(-100, 100), (0, 0), (100, 100)], "crossing")])
    assert CrossingIndex(network).nearby(latitude=LAT, longitude=LNG) == frozenset({0})


@pytest.mark.parametrize("distance,included", [(49, True), (50, True), (51, False)])
def test_fifty_metre_boundary(distance: float, included: bool) -> None:
    network = network_for([([(-100, 0), (100, 0)], "crossing")])
    lat = float(network.geom_coords[0, 1]) + math.degrees(distance / EARTH_RADIUS_M)
    lng = float(network.geom_coords[:, 0].mean(dtype=np.float64))
    assert bool(CrossingIndex(network).nearby(latitude=lat, longitude=lng)) is included


def test_repeated_geometry_positions_are_safe() -> None:
    network = network_for([([(-10, 0), (-10, 0), (10, 0)], "crossing")])
    assert CrossingIndex(network).nearby(latitude=LAT, longitude=LNG) == frozenset({0})


def test_network_with_no_crossings_has_no_matches() -> None:
    index = CrossingIndex(network_for([([(-10, 0), (10, 0)], "sidewalk")]))
    assert not index.nearby(latitude=LAT, longitude=LNG)


def test_medium_confidence_shared_obstructions_and_explicit_edge_map(
    crossings: CrossingIndex,
) -> None:
    camera = evidence()
    result = match_camera_ice([camera], watch(), crossings, now=NOW)
    assert not result.warnings
    risk = result.risks[0]
    assert risk.edge_indices == frozenset({0, 3})
    item = risk.obstruction
    assert item.kind == Kind.ICE_RISK and item.effect == Effect.ICE_RISK
    assert item.confidence == Confidence.MEDIUM
    assert (
        item.observed_at == camera.observed_at and item.expires_at == camera.expires_at
    )
    assert item.geometry.coordinates == [camera.longitude, camera.latitude]
    assert "black ice detected" not in (item.title + str(item.detail)).lower()
    assert result.by_edge() == {0: (item,), 3: (item,)}


def test_valid_warm_wet_camera_has_no_risk_or_false_error(
    crossings: CrossingIndex,
) -> None:
    result = match_camera_ice(
        [evidence(label="wet")],
        watch(active=False, air_c=5, ground_c=5),
        crossings,
        now=NOW,
    )
    assert not result.risks and not result.warnings


def test_uncertain_camera_or_weather_is_reported_without_fabricating_hazards(
    crossings: CrossingIndex,
) -> None:
    result = match_camera_ice([evidence(label="wet")], None, crossings, now=NOW)
    assert not result.risks and result.warnings


def test_no_nearby_crossing_reports_unmatched_signal(crossings: CrossingIndex) -> None:
    result = match_camera_ice(
        [evidence(latitude=LAT + 0.1)], watch(), crossings, now=NOW
    )
    assert not result.risks
    assert "no mapped crossing" in result.warnings[0]


@pytest.mark.parametrize("bad", [None, {}, {"source": "wrong"}, {"latitude": False}])
def test_bad_camera_does_not_discard_valid_neighbor(
    bad: object, crossings: CrossingIndex
) -> None:
    result = match_camera_ice([bad, evidence()], watch(), crossings, now=NOW)
    assert len(result.risks) == 1 and result.warnings


@pytest.mark.parametrize(
    "update",
    [
        {"source_ref": " "},
        {"latitude": float("nan")},
        {"longitude": -181},
        {"latitude": True},
        {"observed_at": NOW.replace(tzinfo=None)},
        {"expires_at": NOW - timedelta(hours=1)},
    ],
)
def test_invalid_evidence_is_rejected(update: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        evidence(**update)


def test_projection_accepts_worker_objects_and_ignores_unrelated_payload_fields(
    crossings: CrossingIndex,
) -> None:
    fields = evidence().model_dump()
    obj = SimpleNamespace(
        **fields, score=0.95, camera_id=9001, model_version="synthetic"
    )
    payload = {**fields, "view_id": 1, "model_version": "synthetic"}
    assert match_camera_ice([obj], watch(), crossings, now=NOW) == match_camera_ice(
        [payload], watch(), crossings, now=NOW
    )


def test_newer_dry_reading_supersedes_older_ice_in_either_order(
    crossings: CrossingIndex,
) -> None:
    old, new = evidence(), evidence(label="dry", observed_at=NOW)
    for readings in ([old, new], [new, old]):
        assert not match_camera_ice(readings, watch(), crossings, now=NOW).risks


def test_duplicate_and_conflicting_camera_timestamps_are_deterministic(
    crossings: CrossingIndex,
) -> None:
    first, conflicting = evidence(), evidence(label="dry")
    assert len(match_camera_ice([first, first], watch(), crossings, now=NOW).risks) == 1
    for readings in ([first, conflicting], [conflicting, first]):
        result = match_camera_ice(readings, watch(), crossings, now=NOW)
        assert not result.risks
        assert "conflicting" in result.warnings[0]
    newer = evidence(observed_at=NOW)
    assert match_camera_ice(
        [first, conflicting, newer], watch(), crossings, now=NOW
    ).risks


def test_risk_identity_is_stable_and_overlapping_cameras_keep_separate_evidence(
    crossings: CrossingIndex,
) -> None:
    first = evidence()
    second = evidence(source_ref="511:9002:1", label="wet")
    result = match_camera_ice([first, second], watch(), crossings, now=NOW)
    assert len(result.by_edge()[0]) == 2
    refreshed = evidence(observed_at=NOW, expires_at=NOW + timedelta(minutes=15))
    assert (
        result.risks[0].obstruction.id
        == match_camera_ice([refreshed], watch(), crossings, now=NOW)
        .risks[0]
        .obstruction.id
    )


def test_committed_network_matches_only_flagged_crossings_and_is_unchanged() -> None:
    network = WalkNetwork.load()
    original_flags = network.edge_flags.copy()
    original_lengths = network.edge_length_m.copy()
    crossing = int(np.flatnonzero(network.edge_flags & EDGE_CROSSING)[0])
    position = network.geom_coords[network.geom_offsets[crossing]]
    camera = evidence(latitude=float(position[1]), longitude=float(position[0]))
    result = match_camera_ice([camera], watch(), CrossingIndex(network), now=NOW)
    matched = result.risks[0].edge_indices
    assert crossing in matched
    assert all(network.edge_flags[edge] & EDGE_CROSSING for edge in matched)
    np.testing.assert_array_equal(network.edge_flags, original_flags)
    np.testing.assert_array_equal(network.edge_length_m, original_lengths)


def test_weather_service_wrapper_honours_refresh_failure_and_recovery(
    crossings: CrossingIndex,
) -> None:
    fail = False

    async def fetch(now: datetime) -> dict[str, object]:
        if fail:
            raise httpx.ConnectTimeout("synthetic unavailable weather")
        return {
            "air_c": -1,
            "ground_c": -2,
            "dew_c": -2,
            "precipitation_last_6h_mm": 1,
            "max_air_last_12h_c": 1,
            "observed_at": now,
        }

    service = WeatherService(fetch, clock=lambda: NOW)
    assert asyncio.run(service.refresh())
    cameras = [evidence(label="wet")]
    assert match_service_camera_ice(cameras, service, crossings, now=NOW).risks
    fail = True
    assert not asyncio.run(service.refresh())
    assert service.last_good.active is True  # retained cache is not fresh evidence
    failed = match_service_camera_ice(cameras, service, crossings, now=NOW)
    assert not failed.risks and failed.warnings
    assert match_service_camera_ice([evidence()], service, crossings, now=NOW).risks
    fail = False
    assert asyncio.run(service.refresh())
    assert match_service_camera_ice(cameras, service, crossings, now=NOW).risks


def test_service_wrapper_handles_missing_weather_and_includes_both_attributions(
    crossings: CrossingIndex,
) -> None:
    assert not match_service_camera_ice(
        [evidence(label="wet")], None, crossings, now=NOW
    ).risks
    result = match_camera_ice([evidence(label="wet")], watch(), crossings, now=NOW)
    attribution = result.risks[0].obstruction.attribution
    assert "511 Alberta" in attribution
    assert "Environment and Climate Change Canada" in attribution
    assert "Open-Meteo" in attribution


def test_result_explicitly_labels_its_replay_mode(crossings: CrossingIndex) -> None:
    result = match_service_camera_ice(
        [evidence(mode="replay")], None, crossings, now=NOW, mode="replay"
    )
    assert result.mode == "replay" and result.risks


def test_uncertainty_never_becomes_an_instruction_to_clear_a_saved_hazard(
    crossings: CrossingIndex,
) -> None:
    camera = evidence(label="wet")
    uncertain = match_camera_ice([camera], None, crossings, now=NOW)
    assert not uncertain.clear_refs
    assert uncertain.uncertain_refs == frozenset({camera.source_ref})
    clear = match_camera_ice(
        [camera], watch(active=False, air_c=5, ground_c=5), crossings, now=NOW
    )
    assert clear.clear_refs == frozenset({camera.source_ref})
    assert not clear.uncertain_refs
    empty = match_camera_ice([], watch(), crossings, now=NOW)
    assert not empty.clear_refs  # no bulk source reconciliation from missing readings


def test_expired_and_conflicting_readings_never_clear_unexpired_saved_data(
    crossings: CrossingIndex,
) -> None:
    for rows in ([evidence(expires_at=NOW)], [evidence(), evidence(label="dry")]):
        result = match_camera_ice(rows, watch(), crossings, now=NOW)
        assert not result.risks and not result.clear_refs
        assert result.uncertain_refs == frozenset({"511:9001:1"})
