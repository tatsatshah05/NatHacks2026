"""OP-104: the walking network exports to compact arrays, saves, loads and answers queries.

The first group uses a tiny hand-built network (no download, no networkx) that
exercises every flag and the geometry rules. The last test loads the committed
Edmonton file, if present, and checks the measured facts that routing relies on.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

import numpy as np
import pytest

from cloud.app.network import (
    DEFAULT_NETWORK_PATH,
    EDGE_CROSSING,
    EDGE_ELEVATOR,
    EDGE_SIDEWALK,
    EDGE_STEPS,
    NODE_ELEVATOR,
    RawEdge,
    RawNode,
    WalkNetwork,
    export_network,
    save_network,
)

# Four nodes around Churchill Square, roughly 100 m apart.
A = RawNode(osmid=1, lat=53.5440, lng=-113.4900)  # south-west
B = RawNode(osmid=2, lat=53.5440, lng=-113.4885, highway="crossing")  # south-east
C = RawNode(osmid=3, lat=53.5449, lng=-113.4885, highway="elevator")  # north-east
D = RawNode(osmid=4, lat=53.5449, lng=-113.4900)  # north-west
NODES = [A, B, C, D]

EDGES = [
    # A-B: a named sidewalk, straight line (no geometry given).
    RawEdge(
        u=1,
        v=2,
        length_m=99.0,
        highway="footway",
        footway="sidewalk",
        name="102A Avenue NW",
        osmid=100,
    ),
    # B-C: stairs, with a list-valued highway tag as OSMnx produces after simplification.
    RawEdge(u=2, v=3, length_m=100.0, highway=["steps", "footway"], osmid=[101, 102]),
    # C-D: an elevator drawn as a way.
    RawEdge(u=3, v=4, length_m=5.0, highway="elevator", osmid=103),
    # A-C: a crossing with a bend, given in the WRONG direction (C first) on purpose.
    RawEdge(
        u=1,
        v=3,
        length_m=140.0,
        highway="footway",
        footway="crossing",
        name=["Rice Howard Way", "Rice Howard Way"],  # duplicate names collapse
        osmid=104,
        geometry=[(-113.4885, 53.5449), (-113.4893, 53.5446), (-113.4900, 53.5440)],
    ),
    # D-D: a self-loop, which must be skipped.
    RawEdge(u=4, v=4, length_m=10.0, highway="footway", osmid=105),
    # D-A: zero length, which must be skipped.
    RawEdge(u=4, v=1, length_m=0.0, highway="footway", osmid=106),
]

BUILT_AT = datetime(2026, 11, 7, 22, 0, tzinfo=UTC)


@pytest.fixture
def data():
    return export_network(NODES, EDGES, source="test", built_at=BUILT_AT)


@pytest.fixture
def network(data) -> WalkNetwork:
    return WalkNetwork.from_data(data)


# -------------------------------------------------------------------- export


def test_counts_and_skips(data) -> None:
    """Four real edges are kept; the self-loop and the zero-length edge are counted, not stored."""
    m = data.manifest
    assert (m["node_count"], m["edge_count"]) == (4, 4)
    assert (m["skipped_self_loops"], m["skipped_zero_length"]) == (1, 1)
    assert m["built_at"] == "2026-11-07T22:00:00+00:00"
    assert m["bbox"] == [-113.49, 53.544, -113.4885, 53.5449]


def test_flags_are_set_from_tags(data) -> None:
    """Each OSM tag lands in the right bit, including list-valued tags."""
    flags = data.arrays["edge_flags"]
    assert flags[0] == EDGE_SIDEWALK
    assert flags[1] == EDGE_STEPS
    assert flags[2] == EDGE_ELEVATOR
    assert flags[3] == EDGE_CROSSING
    assert data.arrays["node_flags"][2] == NODE_ELEVATOR  # node C
    assert data.manifest["steps_edges"] == 1
    assert data.manifest["elevator_nodes"] == 1


def test_lookup_tables(data, network) -> None:
    """Highway types and names are stored once each; edges hold indices into them."""
    assert set(network.highway_types) == {"footway", "steps", "elevator"}
    assert network.names == [
        "102A Avenue NW",
        "Rice Howard Way",
    ]  # duplicates collapsed
    assert data.arrays["edge_name"][1] == -1  # the stairs have no name
    assert network.edge_label(0) == "102A Avenue NW"
    assert network.edge_label(1) == "steps"  # unnamed: falls back to the path type
    assert data.arrays["edge_osmid"][1] == 101  # first of a list-valued osmid


def test_geometry_runs_from_u_to_v(network) -> None:
    """A straight edge gets its two endpoints; a bent edge given backwards is reversed."""
    straight = network.edge_coords(0)
    assert straight.shape == (2, 2)
    assert straight[0] == pytest.approx([A.lng, A.lat])
    assert straight[-1] == pytest.approx([B.lng, B.lat])

    bent = network.edge_coords(3)  # A-C, supplied as C -> A
    assert bent.shape == (3, 2)
    assert bent[0] == pytest.approx([A.lng, A.lat], abs=1e-4)
    assert bent[-1] == pytest.approx([C.lng, C.lat], abs=1e-4)


def test_unknown_node_is_an_error() -> None:
    with pytest.raises(ValueError, match="unknown node 99"):
        export_network(NODES, [RawEdge(u=1, v=99, length_m=1.0)], source="test")


def test_duplicate_node_is_an_error() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        export_network([A, A], [], source="test")


# --------------------------------------------------------------- save / load


def test_save_and_load_round_trip(data, tmp_path) -> None:
    """Everything survives the trip through the .npz file, including the manifest."""
    path = tmp_path / "network.npz"
    save_network(data, path)
    loaded = WalkNetwork.load(path)
    assert loaded.manifest == data.manifest
    assert loaded.node_count == 4 and loaded.edge_count == 4
    for key, array in data.arrays.items():
        if array.dtype.kind == "U":
            assert [str(x) for x in array] == [str(x) for x in getattr(loaded, key)]
        else:
            np.testing.assert_array_equal(getattr(loaded, key), array)


def test_wrong_format_version_is_refused(data) -> None:
    """A file from an older layout fails fast with a rebuild hint."""
    stale = dict(data.manifest, format_version=0)
    with pytest.raises(ValueError, match="rebuild"):
        WalkNetwork(dict(data.arrays), stale)


# -------------------------------------------------------------------- queries


def test_neighbors_are_symmetric(network) -> None:
    """Every undirected edge is reachable from both of its ends, with its edge index."""
    # B (index 1) connects to A via edge 0 and to C via edge 1.
    nodes, edges = network.neighbors(1)
    assert set(nodes.tolist()) == {0, 2}
    assert set(edges.tolist()) == {0, 1}
    # A (index 0) connects to B (edge 0) and C (edge 3). The skipped D-A edge is absent.
    nodes, edges = network.neighbors(0)
    assert set(nodes.tolist()) == {1, 2}
    assert set(edges.tolist()) == {0, 3}
    # D (index 3) only has the elevator to C.
    nodes, edges = network.neighbors(3)
    assert nodes.tolist() == [2] and edges.tolist() == [2]


def test_nearest_node_in_metres(network) -> None:
    """Snapping returns the closest node and a distance in metres (about 30 m here)."""
    index, distance = network.nearest_node(53.5440, -113.4889)  # just west of B
    assert index == 1
    assert 20 < distance < 40


def test_length_matrix_is_symmetric_sparse(network) -> None:
    """The SciPy matrix has one entry per direction and the edge length as weight."""
    matrix = network.length_matrix()
    assert matrix.shape == (4, 4)
    assert matrix.nnz == 8  # 4 edges x 2 directions
    assert matrix[0, 1] == pytest.approx(99.0) and matrix[1, 0] == pytest.approx(99.0)
    assert matrix[0, 3] == 0  # no direct A-D edge
    # Custom weights: removing the stairs (weight 0) drops that entry.
    weights = network.edge_length_m.copy()
    weights[1] = 0
    assert network.length_matrix(weights).nnz == 6


def test_nbytes_is_small_for_the_test_network(network) -> None:
    assert 0 < network.nbytes < 10_000


# ------------------------------------------------------ the committed city file


@pytest.mark.skipif(
    not DEFAULT_NETWORK_PATH.exists(), reason="cloud/data/network.npz not built"
)
def test_committed_edmonton_network_loads_within_budget() -> None:
    """The real file loads fast, covers Edmonton, flags stairs and elevators, and stays small."""
    start = time.perf_counter()
    network = WalkNetwork.load()
    load_seconds = time.perf_counter() - start

    assert network.node_count > 100_000
    assert network.edge_count > 150_000
    assert network.manifest["node_count"] == network.node_count
    min_lng, min_lat, max_lng, max_lat = network.bbox
    assert -113.8 < min_lng < -113.5 and -113.5 < max_lng < -113.2  # Edmonton's extent
    assert 53.3 < min_lat < 53.5 and 53.6 < max_lat < 53.8
    # Measured on November 7, 2026: 418 undirected stair edges (836 when counted per direction).
    assert int((network.edge_flags & EDGE_STEPS).astype(bool).sum()) > 300
    assert int((network.node_flags & NODE_ELEVATOR).astype(bool).sum()) > 0
    assert (
        network.nbytes < 150_000_000
    )  # PLAN.md section 8: well under the 300 MB budget
    assert load_seconds < 5

    # Churchill Square snaps to a node within a block.
    index, distance = network.nearest_node(53.5444, -113.4909)
    assert 0 <= index < network.node_count and distance < 150
