"""OP-106 / PR #98: keep lift outages from cutting ground-level junctions.

Use tiny synthetic networks through the real OP-104 implementation. These
tests exercise review requirements and do not certify real device matches.
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy.sparse import csgraph

from cloud.app import elevator_mapping as mapping
from cloud.app import network as net


def make_network(
    *, primary_way: int = 999, vertical: bool = True, second_lift: bool = False
) -> net.WalkNetwork:
    nodes = [
        net.RawNode(1, 53.54, -113.49),
        net.RawNode(2, 53.5401, -113.49, highway="elevator"),
        net.RawNode(3, 53.5402, -113.49),
        net.RawNode(4, 53.54, -113.491),
    ]
    edges = [
        net.RawEdge(1, 2, 5, highway="footway", osmid=101),
        net.RawEdge(
            2,
            3,
            1,
            highway=["footway", "elevator"] if vertical else "footway",
            osmid=[primary_way, 999],
        ),
        net.RawEdge(1, 4, 5, highway="footway", osmid=103),
        net.RawEdge(4, 3, 5, highway="footway", osmid=104),
        net.RawEdge(2, 4, 4, highway="footway", osmid=105),
    ]
    if second_lift:
        edges.append(net.RawEdge(2, 4, 1, highway="elevator", osmid=106))
    return net.WalkNetwork.from_data(
        net.export_network(nodes, edges, source="OP-106 synthetic review regression")
    )


def mappings(
    *,
    ref: mapping.OsmRef | None = None,
    anchor: int | None = None,
    pairs: list[list[int]] | None = None,
    status: str = "reviewed",
) -> dict[str, mapping.ElevatorMapping]:
    devices = [
        {
            "device_type": "Elevator",
            "device_id": "1",
            "lrt_station_name": "Synthetic Station",
            "lrt_device_location": "Platform",
        }
    ]
    document = mapping.prepare_inventory(devices)
    ref = ref or mapping.OsmRef("node", 2)
    document["elevators"][0].update(
        status=status,
        osm_refs=[{"type": ref.type, "id": ref.id}],
        matched_by="test-matcher",
        reviewed_by="test-reviewer" if status == "reviewed" else None,
        evidence=["Synthetic test edge only"],
        anchor_node_id=anchor,
        edge_pairs=pairs or [],
    )
    return mapping.parse_mappings(document, devices)


def outage(source_ref: str | None = "1") -> mapping.Row:
    return {
        "source": "city_lrt_devices",
        "source_ref": source_ref,
        "kind": "elevator_out",
        "effect": "blocked",
    }


@pytest.mark.parametrize(
    "profile", ["wheelchair", "walker_cane", "low_vision", "standard"]
)
def test_node_outage_removes_vertical_link_but_preserves_ground_junction(
    profile: str,
) -> None:
    network = make_network()
    reviewed = mappings()
    views = mapping.network_elevator_edges(network, reviewed)
    result = mapping.block_elevator_outages(reviewed, views, [outage()])
    assert result.blocked_edge_indices == {1}, profile
    matrix = network.length_matrix(
        mapping.masked_edge_weights(network, result.blocked_edge_indices)
    )
    assert matrix[0, 1] == matrix[1, 0] == 5, profile
    assert matrix[1, 3] == matrix[3, 1] == 4, profile
    assert matrix[1, 2] == matrix[2, 1] == 0, profile
    distance = csgraph.dijkstra(matrix, directed=False, indices=0)
    assert distance[1] == 5 and distance[2] == 10, profile


def test_unflagged_node_incident_paths_need_an_exact_reviewed_vertical_pair() -> None:
    network = make_network(vertical=False)
    without_pair = mappings()
    assert mapping.network_elevator_edges(network, without_pair) == []
    reviewed = mappings(pairs=[[2, 3]])
    views = mapping.network_elevator_edges(network, reviewed)
    result = mapping.block_elevator_outages(reviewed, views, [outage()])
    assert result.blocked_edge_indices == {1}
    assert {view.index for view in views} == {1}


def test_candidate_edge_pairs_cannot_activate_vertical_links() -> None:
    network = make_network(vertical=False)
    candidate = mappings(pairs=[[2, 3]], status="candidate")
    assert mapping.network_elevator_edges(network, candidate) == []


@pytest.mark.parametrize("primary_way", [11, 0])
def test_hidden_way_id_uses_flagged_link_and_reviewed_node_anchor(
    primary_way: int,
) -> None:
    network = make_network(primary_way=primary_way)
    assert network.edge_osmid[1] != 999
    reviewed = mappings(ref=mapping.OsmRef("way", 999), anchor=2)
    views = mapping.network_elevator_edges(network, reviewed)
    assert mapping.OsmRef("way", 999) not in {
        ref for view in views for ref in view.osm_refs
    }
    result = mapping.block_elevator_outages(reviewed, views, [outage()])
    assert result.blocked_edge_indices == {1}
    assert not result.station_warnings


def test_ambiguous_anchor_needs_reviewed_edge_pair() -> None:
    network = make_network(primary_way=11, second_lift=True)
    ambiguous = mappings(ref=mapping.OsmRef("way", 999), anchor=2)
    result = mapping.block_elevator_outages(
        ambiguous, mapping.network_elevator_edges(network, ambiguous), [outage()]
    )
    assert not result.blocked_edge_indices
    assert result.station_warnings
    precise = mappings(ref=mapping.OsmRef("way", 999), anchor=2, pairs=[[2, 3]])
    result = mapping.block_elevator_outages(
        precise, mapping.network_elevator_edges(network, precise), [outage()]
    )
    assert result.blocked_edge_indices == {1}


@pytest.mark.parametrize("bad_id", ["", "  ", None])
def test_bad_outage_row_is_logged_while_valid_outages_still_mask(
    bad_id: str | None, caplog: pytest.LogCaptureFixture
) -> None:
    network = make_network()
    reviewed = mappings()
    result = mapping.block_elevator_outages(
        reviewed,
        mapping.network_elevator_edges(network, reviewed),
        [outage(bad_id), outage()],
    )
    assert result.blocked_edge_indices == {1}
    assert "Skipping elevator outage row 1" in caplog.text
    assert "source_ref" in caplog.text


def test_numpy_indices_and_float64_copy_are_supported() -> None:
    network = make_network()
    original = np.array(network.edge_length_m, dtype=np.float32)
    masked = mapping.masked_edge_weights(
        network, [np.int32(1), np.int64(3), np.uint64(4)], original
    )
    assert isinstance(masked, np.ndarray) and masked.dtype == np.float64
    assert masked[1] == masked[3] == masked[4] == 0
    assert original[1] == 1 and original[3] == 5
    assert not np.shares_memory(masked, original)


@pytest.mark.parametrize("bad_index", [True, np.bool_(True), -1, np.int64(100)])
def test_invalid_indices_do_not_modify_shared_weights(bad_index: object) -> None:
    network = make_network()
    before = network.edge_length_m.copy()
    with pytest.raises(mapping.MappingError):
        mapping.masked_edge_weights(network, [bad_index])
    assert np.array_equal(before, network.edge_length_m)


@pytest.mark.parametrize(
    "pairs", [[[1]], [[1, 1]], [[True, 2]], [[1, 2], [2, 1]], ["12"]]
)
def test_invalid_reviewed_edge_pairs_are_rejected(pairs: list[object]) -> None:
    with pytest.raises(mapping.MappingError):
        mappings(pairs=pairs)
