"""OP-106 / issue #34: exercise the actual OP-104 network implementation.

The merged OP-104 network is required, so these checks cannot silently skip.
Test device matches are synthetic and do not certify any real City elevator.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from scipy.sparse import csgraph

from cloud.app import elevator_mapping as mapping
from cloud.app import network as network_module


@pytest.fixture
def network() -> network_module.WalkNetwork:
    nodes = [
        network_module.RawNode(101, 53.54, -113.49),
        network_module.RawNode(102, 53.5401, -113.49, highway="elevator"),
        network_module.RawNode(103, 53.5402, -113.49),
        network_module.RawNode(104, 53.5401, -113.491),
    ]
    edges = [
        network_module.RawEdge(101, 102, 1, osmid=201),
        network_module.RawEdge(102, 103, 1, osmid=202),
        network_module.RawEdge(101, 104, 4, osmid=203),
        network_module.RawEdge(104, 103, 4, osmid=204),
        network_module.RawEdge(101, 103, 1, highway="elevator", osmid=205),
    ]
    return network_module.WalkNetwork.from_data(
        network_module.export_network(
            nodes, edges, source="OP-106 synthetic integration test"
        )
    )


def test_existing_network_flags_translate_to_node_and_way_refs(
    network: network_module.WalkNetwork,
) -> None:
    views = mapping.network_elevator_edges(network)
    assert {view.index for view in views} == {0, 1, 4}
    assert views[0].osm_refs == frozenset({mapping.OsmRef("node", 102)})
    assert views[-1].osm_refs == frozenset({mapping.OsmRef("way", 205)})


@pytest.mark.parametrize(
    ("profile", "cost_scale"),
    [("wheelchair", 1.0), ("walker_cane", 1.1), ("low_vision", 1.2), ("standard", 1.3)],
)
def test_network_matrix_removes_outages_in_both_directions_and_recovers(
    network: network_module.WalkNetwork, profile: str, cost_scale: float
) -> None:
    original = network.edge_length_m.copy()
    # Both node- and way-based lifts are out. The ramp is the only usable path.
    # Synthetic weight scales exercise custom profile inputs, not real settings.
    weights = mapping.masked_edge_weights(
        network, frozenset({0, 1, 4}), original * cost_scale
    )
    matrix = network.length_matrix(weights)
    distance = csgraph.dijkstra(matrix, directed=False, indices=0)
    reverse = csgraph.dijkstra(matrix, directed=False, indices=2)
    assert distance[2] == pytest.approx(8.0 * cost_scale), profile
    assert reverse[0] == pytest.approx(8.0 * cost_scale), profile
    assert matrix[0, 1] == matrix[1, 0] == 0
    assert matrix[0, 2] == matrix[2, 0] == 0
    assert np.array_equal(network.edge_length_m, original)
    recovered = network.length_matrix(mapping.masked_edge_weights(network, frozenset()))
    assert csgraph.dijkstra(recovered, directed=False, indices=0)[2] == 1.0


def test_weight_and_edge_index_mismatches_are_rejected(
    network: network_module.WalkNetwork,
) -> None:
    with pytest.raises(mapping.MappingError, match="edge count"):
        mapping.masked_edge_weights(network, frozenset(), [1.0])
    with pytest.raises(mapping.MappingError, match="outside"):
        mapping.masked_edge_weights(network, frozenset({network.edge_count}))


def test_committed_city_network_exposes_all_flagged_elevators() -> None:
    path = Path(network_module.DEFAULT_NETWORK_PATH)
    assert path.is_file(), "The merged OP-104 network must be committed"
    actual = network_module.WalkNetwork.load(path)
    views = mapping.network_elevator_edges(actual)
    assert views
    assert len({view.index for view in views}) == len(views)
    assert all(0 <= view.index < actual.edge_count for view in views)
    expected_way_edges = {
        i
        for i in range(actual.edge_count)
        if int(actual.edge_flags[i]) & network_module.EDGE_ELEVATOR
        and int(actual.edge_osmid[i]) > 0
    }
    assert expected_way_edges <= {view.index for view in views}


def test_proposed_candidates_exist_in_merged_network_but_cannot_activate() -> None:
    root = Path(__file__).resolve().parents[2]
    inventory = json.loads((root / "docs/elevators.proposed.json").read_text("utf-8"))
    devices = json.loads(
        (root / "cloud/tests/fixtures/elevator_mapping/devices.json").read_text("utf-8")
    )
    parsed = mapping.parse_mappings(inventory, devices)
    actual = network_module.WalkNetwork.load()
    views = mapping.network_elevator_edges(actual)
    network_refs = {ref for view in views for ref in view.osm_refs}
    candidates = [entry for entry in parsed.values() if entry.status == "candidate"]
    assert len(candidates) == 4
    assert all(entry.osm_refs <= network_refs for entry in candidates)
    outages = [
        {
            "source": "city_lrt_devices",
            "source_ref": entry.device_id,
            "kind": "elevator_out",
            "effect": "blocked",
        }
        for entry in candidates
    ]
    result = mapping.block_elevator_outages(parsed, views, outages)
    assert not result.blocked_edge_indices
    assert len(result.station_warnings) == 4
