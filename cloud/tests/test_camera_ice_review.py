"""OP-205 / PR #113: verify real store rows, cache age and exact router hazards.

Use the merged camera store and small synthetic graphs without external requests.
These regressions cover Tatsat's requested integration and freshness changes.
"""

from datetime import timedelta

import pytest
from pydantic import ValidationError

from cloud.app.camera_ice import (
    POSITION_CACHE_SIZE,
    CameraEvidence,
    CrossingIndex,
    evaluate_camera_ice,
    match_camera_ice,
    match_service_camera_ice,
)
from cloud.app.camera_ice_routing import CameraIceHazardProvider
from cloud.app.camera_readings import CameraReadingIn, CameraReadingStore
from cloud.app.db import make_engine
from cloud.app.profiles import load_routing_config
from cloud.app.routing import profile_weights
from cloud.tests.test_camera_ice import LAT, LNG, NOW, evidence, watch
from cloud.tests.test_camera_ice_routing import diamond_network, weather_service


def test_real_store_row_projects_directly_and_reaches_router() -> None:
    engine = make_engine("sqlite://")
    try:
        store = CameraReadingStore(engine)
        store.create_tables()
        payload = CameraReadingIn.model_validate(
            {
                **evidence(
                    label="wet", attribution="Official camera credit"
                ).model_dump(),
                "camera_id": 9001,
                "view_id": 1,
                "model_version": "a" * 64,
            }
        )
        store.upsert(payload, NOW)
        row = store.latest(NOW)[0]
        projected = CameraEvidence.model_validate(row)
        assert projected.attribution == "Official camera credit"
        # Older store projections omit source; explicit wrong sources still fail.
        without_source = row.model_dump(exclude={"source"})
        assert CameraEvidence.model_validate(without_source).source == "camera_model"
        network = diamond_network()
        result = match_service_camera_ice(
            [row], weather_service(), CrossingIndex(network), now=NOW
        )
        assert result.risks[0].obstruction.category == "weather"
        hazards = result.hazards()
        assert set(hazards) == {1}
        profiles = load_routing_config().profiles
        assert profile_weights(network, profiles["walker_cane"], hazards)[1] == 0
        assert (
            profile_weights(network, profiles["wheelchair"], hazards)[1]
            == 3 * network.edge_length_m[1]
        )
        provider = CameraIceHazardProvider(
            network, store.latest, weather=weather_service()
        )
        assert provider(NOW) == {edge: list(items) for edge, items in hazards.items()}
    finally:
        engine.dispose()


@pytest.mark.parametrize("age,active", [(1800, True), (1801, False)])
def test_failed_weather_refresh_uses_age_not_last_error(age: int, active: bool) -> None:
    network = diamond_network()
    service = weather_service()
    service.last_error = "unavailable"
    service.last_success = NOW - timedelta(seconds=age)
    result = match_service_camera_ice(
        [evidence(label="wet")], service, CrossingIndex(network), now=NOW
    )
    assert bool(result.risks) is active
    service.last_success = NOW
    service.last_good = watch(observed_at=NOW - timedelta(hours=2))
    assert not match_service_camera_ice(
        [evidence(label="wet")], service, CrossingIndex(network), now=NOW
    ).risks


@pytest.mark.parametrize("seconds,active", [(5, True), (6, False)])
def test_camera_and_weather_allow_only_shared_five_second_skew(
    seconds: int, active: bool
) -> None:
    future = NOW + timedelta(seconds=seconds)
    camera = evidence(label="wet", observed_at=future)
    assert (
        evaluate_camera_ice(camera, watch(observed_at=future), now=NOW).active is active
    )


def test_unusable_is_not_an_ingest_label() -> None:
    with pytest.raises(ValidationError):
        evidence(label="unusable")


def test_long_camera_credit_is_preserved_without_discarding_wet_risk() -> None:
    camera = evidence(label="wet", attribution="C" * 200)
    result = match_camera_ice(
        [camera], watch(), CrossingIndex(diamond_network()), now=NOW
    )
    item = result.risks[0].obstruction
    assert item.attribution == camera.attribution
    assert "Environment and Climate Change Canada" in item.detail


def test_crossing_positions_reuse_empty_and_nonempty_matches_with_bounded_cache(
    monkeypatch,
) -> None:
    index = CrossingIndex(diamond_network())
    original = index._nearby_uncached
    calls = []

    def counted(**position):
        calls.append(position)
        return original(**position)

    monkeypatch.setattr(index, "_nearby_uncached", counted)
    for lat in [LAT, LAT + 1]:
        first = index.nearby(latitude=lat, longitude=LNG)
        assert index.nearby(latitude=lat, longitude=LNG) == first
    assert len(calls) == 2
    for number in range(POSITION_CACHE_SIZE):
        index.nearby(latitude=LAT + 2 + number / 10000, longitude=LNG)
    assert len(index._cache) == POSITION_CACHE_SIZE
    index.nearby(latitude=LAT, longitude=LNG)
    assert len(calls) == POSITION_CACHE_SIZE + 3  # oldest position was evicted
