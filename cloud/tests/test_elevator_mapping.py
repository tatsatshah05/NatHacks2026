"""OP-106: Map LRT elevators to the walking network (issue #34).

Check inventory coverage, review requirements and edge removal for all four
profiles. OSM references and route edges below are synthetic test examples;
they do not claim a verified match in Edmonton's real walking network.
"""

from __future__ import annotations

import heapq
import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from cloud.app import elevator_mapping as mapping

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures" / "elevator_mapping"
SYNTHETIC_NODE = mapping.OsmRef("node", 900_000_001)
SYNTHETIC_WAY = mapping.OsmRef("way", 900_000_002)


@pytest.fixture
def devices() -> list[mapping.Row]:
    return json.loads((FIXTURES / "devices.json").read_text("utf-8-sig"))


@pytest.fixture
def inventory(devices: list[mapping.Row]) -> mapping.Row:
    return mapping.prepare_inventory(devices)


def review_entry(
    inventory: mapping.Row,
    device_id: str = "1",
    refs: tuple[mapping.OsmRef, ...] = (SYNTHETIC_NODE,),
) -> mapping.Row:
    entry = next(row for row in inventory["elevators"] if row["device_id"] == device_id)
    entry.update(
        status="reviewed",
        osm_refs=[{"type": ref.type, "id": ref.id} for ref in refs],
        reason="Synthetic test mapping only",
        matched_by="kalebpham",
        reviewed_by="test-reviewer",
        evidence=["Synthetic OSM object in the unit test; not a real-world match."],
    )
    return entry


def outage(device_id: str = "1") -> mapping.Row:
    return {
        "id": f"city_lrt_devices:{device_id}",
        "source": "city_lrt_devices",
        "source_ref": device_id,
        "category": "indoor",
        "kind": "elevator_out",
        "effect": "blocked",
        "title": "Elevator out of service",
        "detail": "Synthetic station outage for testing",
        "geometry": {"type": "Point", "coordinates": [-113.49, 53.54]},
    }


def test_recorded_city_inventory_is_complete_and_has_no_active_unreviewed_matches(
    devices: list[mapping.Row],
) -> None:
    document = json.loads((ROOT / "docs/elevators.proposed.json").read_text("utf-8"))
    parsed = mapping.parse_mappings(document, devices)
    expected = {row["device_id"] for row in devices if row["device_type"] == "Elevator"}
    assert set(parsed) == expected
    assert len(parsed) == 32  # Measured from this recorded City snapshot.
    assert {key for key, entry in parsed.items() if entry.status == "candidate"} == {
        "88",
        "89",
        "117",
        "118",
    }
    assert all(entry.status in {"unmapped", "candidate"} for entry in parsed.values())
    assert all(entry.reason for entry in parsed.values())
    assert all(
        not entry.osm_refs for entry in parsed.values() if entry.status == "unmapped"
    )
    assert all(entry.reviewed_by is None for entry in parsed.values())


def test_generated_inventory_ignores_device_status(
    devices: list[mapping.Row],
) -> None:
    initial = mapping.prepare_inventory(devices)
    for device in devices:
        device["device_op_status_code"] = "1"
        device["device_op_status"] = "Out of Service"
    assert mapping.prepare_inventory(devices) == initial


def test_missing_or_unknown_elevator_fails_audit(
    inventory: mapping.Row, devices: list[mapping.Row]
) -> None:
    inventory["elevators"].pop()
    with pytest.raises(mapping.MappingError, match="missing"):
        mapping.parse_mappings(inventory, devices)
    inventory["elevators"].append({"device_id": "unknown"})
    with pytest.raises(mapping.MappingError, match="Unknown"):
        mapping.parse_mappings(inventory, devices)


def test_duplicate_elevator_fails_audit(
    inventory: mapping.Row, devices: list[mapping.Row]
) -> None:
    inventory["elevators"].append(deepcopy(inventory["elevators"][0]))
    with pytest.raises(mapping.MappingError, match="duplicate"):
        mapping.parse_mappings(inventory, devices)


def test_city_metadata_drift_requires_review(
    inventory: mapping.Row, devices: list[mapping.Row]
) -> None:
    inventory["elevators"][0]["location"] = "Changed entrance"
    with pytest.raises(mapping.MappingError, match="metadata changed"):
        mapping.parse_mappings(inventory, devices)


@pytest.mark.parametrize("field", ["reason", "station", "location"])
def test_unmapped_entries_need_reason_and_location_details(
    inventory: mapping.Row, devices: list[mapping.Row], field: str
) -> None:
    inventory["elevators"][0][field] = ""
    with pytest.raises(mapping.MappingError):
        mapping.parse_mappings(inventory, devices)


@pytest.mark.parametrize("reviewer", [None, "", "kalebpham", " KALEBPHAM "])
def test_reviewed_match_requires_an_independent_named_reviewer(
    inventory: mapping.Row, devices: list[mapping.Row], reviewer: str | None
) -> None:
    review_entry(inventory)["reviewed_by"] = reviewer
    with pytest.raises(mapping.MappingError):
        mapping.parse_mappings(inventory, devices)


@pytest.mark.parametrize("field", ["evidence", "osm_refs", "matched_by"])
def test_reviewed_match_needs_supporting_evidence(
    inventory: mapping.Row, devices: list[mapping.Row], field: str
) -> None:
    entry = review_entry(inventory)
    entry[field] = None if field == "matched_by" else []
    with pytest.raises(mapping.MappingError):
        mapping.parse_mappings(inventory, devices)


def test_candidates_never_change_routes_until_reviewed(
    inventory: mapping.Row, devices: list[mapping.Row]
) -> None:
    entry = review_entry(inventory)
    entry.update(status="candidate", reviewed_by=None)
    parsed = mapping.parse_mappings(inventory, devices)
    edges = [mapping.ElevatorEdge(0, frozenset({SYNTHETIC_NODE}))]
    result = mapping.block_elevator_outages(parsed, edges, [outage()])
    assert not result.blocked_edge_indices
    assert result.station_warnings[0].device_id == "1"


def test_unmapped_outage_retains_original_station_warning(
    inventory: mapping.Row, devices: list[mapping.Row]
) -> None:
    parsed = mapping.parse_mappings(inventory, devices)
    original = outage()
    result = mapping.block_elevator_outages(parsed, [], [original])
    warning = result.station_warnings[0]
    assert not result.blocked_edge_indices
    assert warning.station == "Churchill"
    assert warning.reason == mapping.UNMAPPED_REASON
    assert warning.obstruction == original
    warning.obstruction["geometry"]["coordinates"][0] = 0
    assert original["geometry"]["coordinates"][0] != 0


def test_unknown_new_elevator_keeps_station_warning(
    inventory: mapping.Row, devices: list[mapping.Row]
) -> None:
    parsed = mapping.parse_mappings(inventory, devices)
    result = mapping.block_elevator_outages(parsed, [], [outage("new-device")])
    assert result.station_warnings[0].device_id == "new-device"
    assert "No device-to-OSM" in result.station_warnings[0].reason


def test_reviewed_outage_selects_only_its_vertical_edge_views(
    inventory: mapping.Row, devices: list[mapping.Row]
) -> None:
    review_entry(inventory)
    parsed = mapping.parse_mappings(inventory, devices)
    refs = frozenset({SYNTHETIC_NODE})
    edges = [
        mapping.ElevatorEdge(0, refs),
        mapping.ElevatorEdge(1, refs),
        mapping.ElevatorEdge(3, frozenset({SYNTHETIC_WAY})),
    ]
    result = mapping.block_elevator_outages(parsed, edges, [outage()])
    assert result.blocked_edge_indices == frozenset({0, 1})
    assert not result.station_warnings


def test_node_and_way_id_namespaces_are_separate(
    inventory: mapping.Row, devices: list[mapping.Row]
) -> None:
    review_entry(inventory)
    parsed = mapping.parse_mappings(inventory, devices)
    other_ref = mapping.OsmRef("way", SYNTHETIC_NODE.id)
    edges = [mapping.ElevatorEdge(0, frozenset({other_ref}))]
    result = mapping.block_elevator_outages(parsed, edges, [outage()])
    assert not result.blocked_edge_indices
    assert result.station_warnings


def test_way_match_blocks_its_elevator_edge(
    inventory: mapping.Row, devices: list[mapping.Row]
) -> None:
    review_entry(inventory, refs=(SYNTHETIC_WAY,))
    parsed = mapping.parse_mappings(inventory, devices)
    edges = [mapping.ElevatorEdge(4, frozenset({SYNTHETIC_WAY}))]
    assert mapping.block_elevator_outages(
        parsed, edges, [outage()]
    ).blocked_edge_indices == {4}


def test_partial_graph_match_blocks_known_edges_and_warns_about_missing_refs(
    inventory: mapping.Row, devices: list[mapping.Row]
) -> None:
    review_entry(inventory, refs=(SYNTHETIC_NODE, SYNTHETIC_WAY))
    parsed = mapping.parse_mappings(inventory, devices)
    edges = [mapping.ElevatorEdge(0, frozenset({SYNTHETIC_NODE}))]
    result = mapping.block_elevator_outages(parsed, edges, [outage()])
    assert result.blocked_edge_indices == {0}
    assert "vertical link" in result.station_warnings[0].reason


@pytest.mark.parametrize(
    ("field", "value"),
    [("source", "community_reports"), ("kind", "escalator_out"), ("effect", "caution")],
)
def test_other_sources_and_escalators_do_not_close_elevator_edges(
    inventory: mapping.Row, devices: list[mapping.Row], field: str, value: str
) -> None:
    review_entry(inventory)
    parsed = mapping.parse_mappings(inventory, devices)
    observation = outage()
    observation[field] = value
    edges = [mapping.ElevatorEdge(0, frozenset({SYNTHETIC_NODE}))]
    result = mapping.block_elevator_outages(parsed, edges, [observation])
    assert not result.blocked_edge_indices
    assert not result.station_warnings


def shortest_test_path(
    edges: list[tuple[int, str, str, float]], blocked: frozenset[int]
) -> tuple[int, ...]:
    """Find a path in the tiny synthetic network, not in the real app graph."""
    queue: list[tuple[float, str, tuple[int, ...]]] = [(0, "street", ())]
    seen: set[str] = set()
    while queue:
        distance, node, path = heapq.heappop(queue)
        if node == "platform":
            return path
        if node in seen:
            continue
        seen.add(node)
        for index, start, finish, length in edges:
            if start == node and index not in blocked:
                heapq.heappush(queue, (distance + length, finish, (*path, index)))
    return ()


@pytest.mark.parametrize(
    ("profile", "ramp_cost"),
    [("wheelchair", 6.0), ("walker_cane", 5.0), ("low_vision", 4.0), ("standard", 3.0)],
)
def test_outage_changes_synthetic_route_for_every_profile(
    inventory: mapping.Row, devices: list[mapping.Row], profile: str, ramp_cost: float
) -> None:
    review_entry(inventory)
    parsed = mapping.parse_mappings(inventory, devices)
    views = [mapping.ElevatorEdge(0, frozenset({SYNTHETIC_NODE}))]
    network = [
        (0, "street", "platform", 1.0),
        (1, "street", "ramp", ramp_cost),
        (2, "ramp", "platform", ramp_cost),
    ]
    # Costs are synthetic test values, not the app's real profile settings.
    # Apply the same physical outage mask before each profile's route search.
    baseline = mapping.block_elevator_outages(parsed, views, [])
    failed = mapping.block_elevator_outages(parsed, views, [outage()])
    assert shortest_test_path(network, baseline.blocked_edge_indices) == (0,), profile
    assert shortest_test_path(network, failed.blocked_edge_indices) == (1, 2), profile
    recovered = mapping.block_elevator_outages(parsed, views, [])
    assert shortest_test_path(network, recovered.blocked_edge_indices) == (0,), profile


@pytest.mark.parametrize("osm_type", ["relation", "", None])
def test_invalid_osm_reference_type_is_rejected(osm_type: str | None) -> None:
    with pytest.raises(mapping.MappingError):
        mapping.OsmRef(osm_type, 1)


@pytest.mark.parametrize("osm_id", [0, -1, True, "123", None])
def test_invalid_osm_ids_are_rejected(osm_id: object) -> None:
    with pytest.raises(mapping.MappingError):
        mapping.OsmRef("node", osm_id)


@pytest.mark.parametrize("index", [-1, True, "0"])
def test_invalid_graph_indices_are_rejected(index: object) -> None:
    with pytest.raises(mapping.MappingError):
        mapping.ElevatorEdge(index, frozenset())


def test_duplicate_reviewed_ref_for_different_devices_requires_resolution(
    inventory: mapping.Row, devices: list[mapping.Row]
) -> None:
    review_entry(inventory, "1")
    review_entry(inventory, "4")
    with pytest.raises(mapping.MappingError, match="multiple elevators"):
        mapping.parse_mappings(inventory, devices)


def test_duplicate_graph_edge_index_fails(
    inventory: mapping.Row, devices: list[mapping.Row]
) -> None:
    parsed = mapping.parse_mappings(inventory, devices)
    edges = [mapping.ElevatorEdge(0, frozenset())] * 2
    with pytest.raises(mapping.MappingError, match="Duplicate graph"):
        mapping.block_elevator_outages(parsed, edges, [])


def test_cli_audit_labels_unverified_inventory(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "elevator_mapping",
            "--inventory",
            str(ROOT / "docs/elevators.proposed.json"),
            "--devices",
            str(FIXTURES / "devices.json"),
        ],
    )
    mapping.main()
    result = json.loads(capsys.readouterr().out)
    assert result["story"] == "OP-106"
    assert result["elevator_count"] == 32
    assert result["reviewed"] == 0
    assert result["candidates"] == 4
    assert result["unmapped"] == 28
