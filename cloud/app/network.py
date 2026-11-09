"""The prebuilt walking network that routing runs on (OP-104).

Why this exists
---------------
Routing needs every walkable path in Edmonton: sidewalks, crossings, trails,
stairs and elevators. OpenStreetMap has them, and OSMnx can download them, but
the result is a networkx graph that needs about 2.8 GB of memory for the whole
city. The API runs on a small Render instance with a 300 MB budget (PLAN.md
section 8), so that graph is converted ONCE, on a laptop, into plain numeric
arrays that load in under a second and fit in a few tens of megabytes.

Two halves
----------
1. Build time (``scripts/build_graph.py``, laptop only): OSMnx graph ->
   ``RawNode`` / ``RawEdge`` records -> ``export_network()`` -> ``save_network()``
   writes ``cloud/data/network.npz``. That file is committed to the repository.
2. Run time (Render): ``WalkNetwork.load()`` reads the arrays, builds the
   adjacency structure and a KD-tree for snapping coordinates to nodes.
   OSMnx and networkx are never imported here.

The arrays (all indexed by node or edge position, not by OSM id)
-----------------------------------------------------------------
Nodes
    ``node_osmid`` int64, ``node_lat`` / ``node_lng`` float64, ``node_flags`` uint8
Edges (undirected: one record per physical path)
    ``edge_u`` / ``edge_v`` int32 node indices, ``edge_length_m`` float32,
    ``edge_flags`` uint8, ``edge_highway`` uint8 (index into ``highway_types``),
    ``edge_name`` int32 (index into ``names``, -1 if unnamed), ``edge_osmid`` int64
Geometry (for drawing routes on the map)
    ``geom_offsets`` int64 [E+1] and ``geom_coords`` float32 [P, 2] as (lng, lat):
    the points of edge ``e`` are ``geom_coords[geom_offsets[e]:geom_offsets[e+1]]``,
    always ordered from ``edge_u`` to ``edge_v`` and including both endpoints.
Tables
    ``highway_types`` and ``names`` (strings), ``manifest`` (JSON: counts, bbox,
    build time, measurements).

Flags are bit fields so several can apply to one edge (a sidewalk that is also a
crossing). Routing uses ``EDGE_STEPS`` to remove stairs for wheelchair and walker
profiles, ``EDGE_ELEVATOR`` / ``NODE_ELEVATOR`` to map LRT elevator outages
(OP-106), and ``EDGE_CROSSING`` to place ice risk on crossings (OP-205).
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from scipy.sparse import csr_matrix
from scipy.spatial import cKDTree

# Bump when the array layout changes, so an old file is refused instead of misread.
FORMAT_VERSION = 1

# Mean Earth radius; used to turn degrees into metres for snapping distances.
EARTH_RADIUS_M = 6_371_008.8

# Node flags (bit field).
NODE_ELEVATOR = 1 << 0  # OSM node tagged highway=elevator

# Edge flags (bit field).
EDGE_STEPS = 1 << 0  # highway=steps: impassable for wheelchair and walker profiles
EDGE_ELEVATOR = 1 << 1  # highway=elevator drawn as a way
EDGE_CROSSING = 1 << 2  # footway=crossing: a marked or unmarked street crossing
EDGE_SIDEWALK = 1 << 3  # footway=sidewalk

# Default path of the committed network file, relative to the repository root.
DEFAULT_NETWORK_PATH = Path(__file__).resolve().parents[1] / "data" / "network.npz"


# ---------------------------------------------------------------- raw records
# These are the build-time input: a plain description of nodes and edges that
# does not depend on networkx. scripts/build_graph.py fills them from OSMnx, and
# the tests fill them by hand.


@dataclass(frozen=True)
class RawNode:
    """One OSM node (an intersection or a bend in a path)."""

    osmid: int
    lat: float
    lng: float
    highway: str | None = None  # the node's own highway tag, e.g. "elevator"


@dataclass(frozen=True)
class RawEdge:
    """One walkable path between two nodes, identified by their OSM ids.

    OSMnx merges consecutive OSM ways into one edge when simplifying, so ``highway``,
    ``name``, ``footway`` and ``osmid`` may each be a single value or a list.
    ``geometry`` is the full polyline as (lng, lat) pairs, including both endpoints;
    None means a straight line between the two nodes.
    """

    u: int
    v: int
    length_m: float
    highway: str | Sequence[str] | None = None
    name: str | Sequence[str] | None = None
    footway: str | Sequence[str] | None = None
    osmid: int | Sequence[int] | None = None
    geometry: Sequence[tuple[float, float]] | None = None


@dataclass(frozen=True)
class NetworkData:
    """The exported arrays plus their manifest, ready to save."""

    arrays: dict[str, np.ndarray]
    manifest: dict[str, Any]


# ------------------------------------------------------------------- helpers


def _as_list(value: Any) -> list[str]:
    """Normalise a tag that may be missing, a single value or a list into a list of strings."""
    if value is None:
        return []
    if isinstance(value, str | int | float):
        return [str(value)]
    return [str(item) for item in value]


def _primary(value: Any) -> str | None:
    """The first value of a possibly-list tag, or None."""
    values = _as_list(value)
    return values[0] if values else None


def _display_name(value: Any) -> str | None:
    """Join a possibly-list street name into one label, keeping order, dropping repeats."""
    values = list(dict.fromkeys(_as_list(value)))
    return " / ".join(values) if values else None


def _squared_distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


# -------------------------------------------------------------------- export


def export_network(
    nodes: Sequence[RawNode],
    edges: Sequence[RawEdge],
    *,
    source: str,
    built_at: datetime | None = None,
) -> NetworkData:
    """Convert raw nodes and edges into the compact arrays described in the module docstring.

    Self-loops and zero-length edges are skipped (they cannot be part of a useful
    route) and counted in the manifest. An edge that refers to an unknown node is
    a build error, not something to skip silently.
    """
    built_at = built_at or datetime.now(UTC)

    # --- nodes: position in the arrays is the node index used everywhere else.
    index_of = {node.osmid: i for i, node in enumerate(nodes)}
    if len(index_of) != len(nodes):
        raise ValueError("duplicate node osmid in input")
    node_osmid = np.array([n.osmid for n in nodes], dtype=np.int64)
    node_lat = np.array([n.lat for n in nodes], dtype=np.float64)
    node_lng = np.array([n.lng for n in nodes], dtype=np.float64)
    node_flags = np.array(
        [NODE_ELEVATOR if n.highway == "elevator" else 0 for n in nodes], dtype=np.uint8
    )

    # --- edges: collect per-edge values in Python lists first, then convert.
    highway_table: dict[str, int] = {}
    name_table: dict[str, int] = {}
    edge_u: list[int] = []
    edge_v: list[int] = []
    edge_length: list[float] = []
    edge_flags: list[int] = []
    edge_highway: list[int] = []
    edge_name: list[int] = []
    edge_osmid: list[int] = []
    coords: list[
        tuple[float, float]
    ] = []  # every geometry point, all edges concatenated
    offsets: list[int] = [0]  # where each edge's points start in ``coords``
    skipped_self_loops = 0
    skipped_zero_length = 0

    for edge in edges:
        try:
            u, v = index_of[edge.u], index_of[edge.v]
        except KeyError as error:
            raise ValueError(f"edge refers to unknown node {error.args[0]}") from None
        if u == v:
            skipped_self_loops += 1
            continue
        if not edge.length_m > 0:
            skipped_zero_length += 1
            continue

        highways = _as_list(edge.highway)
        footways = _as_list(edge.footway)
        flags = 0
        if "steps" in highways:
            flags |= EDGE_STEPS
        if "elevator" in highways:
            flags |= EDGE_ELEVATOR
        if "crossing" in footways:
            flags |= EDGE_CROSSING
        if "sidewalk" in footways:
            flags |= EDGE_SIDEWALK

        primary = _primary(edge.highway) or "unknown"
        highway_code = highway_table.setdefault(primary, len(highway_table))
        name = _display_name(edge.name)
        name_code = -1 if name is None else name_table.setdefault(name, len(name_table))

        # Geometry: use the given polyline, or a straight line between the nodes.
        u_pos = (node_lng[u], node_lat[u])
        v_pos = (node_lng[v], node_lat[v])
        points = [tuple(p) for p in edge.geometry] if edge.geometry else [u_pos, v_pos]
        if len(points) < 2:
            raise ValueError(
                f"edge {edge.u}-{edge.v} geometry needs at least two points"
            )
        # Make every polyline run from u to v, whichever way OSM drew it, so the
        # route renderer never has to guess the direction.
        if _squared_distance(points[0], v_pos) < _squared_distance(points[0], u_pos):
            points.reverse()

        edge_u.append(u)
        edge_v.append(v)
        edge_length.append(edge.length_m)
        edge_flags.append(flags)
        edge_highway.append(highway_code)
        edge_name.append(name_code)
        edge_osmid.append(int(_primary(edge.osmid) or 0))
        coords.extend(points)
        offsets.append(len(coords))

    if len(highway_table) > 255:
        raise ValueError("more than 255 highway types; widen edge_highway")

    arrays: dict[str, np.ndarray] = {
        "node_osmid": node_osmid,
        "node_lat": node_lat,
        "node_lng": node_lng,
        "node_flags": node_flags,
        "edge_u": np.array(edge_u, dtype=np.int32),
        "edge_v": np.array(edge_v, dtype=np.int32),
        "edge_length_m": np.array(edge_length, dtype=np.float32),
        "edge_flags": np.array(edge_flags, dtype=np.uint8),
        "edge_highway": np.array(edge_highway, dtype=np.uint8),
        "edge_name": np.array(edge_name, dtype=np.int32),
        "edge_osmid": np.array(edge_osmid, dtype=np.int64),
        "geom_offsets": np.array(offsets, dtype=np.int64),
        # float32 is about 0.4 m at Edmonton's latitude: fine for drawing. Snapping
        # uses the float64 node coordinates.
        "geom_coords": np.array(coords, dtype=np.float32).reshape(-1, 2),
        "highway_types": np.array(list(highway_table), dtype=str),
        "names": np.array(list(name_table), dtype=str),
    }
    manifest: dict[str, Any] = {
        "format_version": FORMAT_VERSION,
        "source": source,
        "built_at": built_at.astimezone(UTC).isoformat(),
        "node_count": len(nodes),
        "edge_count": len(edge_u),
        "point_count": len(coords),
        "bbox": [
            float(node_lng.min()),
            float(node_lat.min()),
            float(node_lng.max()),
            float(node_lat.max()),
        ]
        if len(nodes)
        else None,
        "steps_edges": int(sum(1 for f in edge_flags if f & EDGE_STEPS)),
        "elevator_edges": int(sum(1 for f in edge_flags if f & EDGE_ELEVATOR)),
        "elevator_nodes": int((node_flags & NODE_ELEVATOR).astype(bool).sum()),
        "crossing_edges": int(sum(1 for f in edge_flags if f & EDGE_CROSSING)),
        "sidewalk_edges": int(sum(1 for f in edge_flags if f & EDGE_SIDEWALK)),
        "skipped_self_loops": skipped_self_loops,
        "skipped_zero_length": skipped_zero_length,
    }
    return NetworkData(arrays=arrays, manifest=manifest)


def save_network(data: NetworkData, path: Path) -> None:
    """Write the arrays and manifest to one compressed ``.npz`` file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # The manifest travels inside the file as a JSON string, so one file is complete.
    np.savez_compressed(
        path, manifest=np.array(json.dumps(data.manifest)), **data.arrays
    )


# ---------------------------------------------------------------------- load


class WalkNetwork:
    """The loaded network: arrays plus adjacency and a KD-tree, built once.

    Use ``WalkNetwork.load()`` at app startup and keep the one instance in
    ``app.state``. All methods are read-only, so it is safe to share across requests.
    """

    def __init__(self, arrays: dict[str, np.ndarray], manifest: dict[str, Any]) -> None:
        if manifest.get("format_version") != FORMAT_VERSION:
            raise ValueError(
                f"network file is format {manifest.get('format_version')}, "
                f"this code reads {FORMAT_VERSION}; rebuild with scripts/build_graph.py"
            )
        self.manifest = manifest
        self.node_osmid: np.ndarray = arrays["node_osmid"]
        self.node_lat: np.ndarray = arrays["node_lat"]
        self.node_lng: np.ndarray = arrays["node_lng"]
        self.node_flags: np.ndarray = arrays["node_flags"]
        self.edge_u: np.ndarray = arrays["edge_u"]
        self.edge_v: np.ndarray = arrays["edge_v"]
        self.edge_length_m: np.ndarray = arrays["edge_length_m"]
        self.edge_flags: np.ndarray = arrays["edge_flags"]
        self.edge_highway: np.ndarray = arrays["edge_highway"]
        self.edge_name: np.ndarray = arrays["edge_name"]
        self.edge_osmid: np.ndarray = arrays["edge_osmid"]
        self.geom_offsets: np.ndarray = arrays["geom_offsets"]
        self.geom_coords: np.ndarray = arrays["geom_coords"]
        self.highway_types: list[str] = [str(h) for h in arrays["highway_types"]]
        self.names: list[str] = [str(n) for n in arrays["names"]]

        # Adjacency in CSR form, built eagerly because routing needs it on the first
        # request. For node i, its neighbours are indices[indptr[i]:indptr[i+1]] and
        # the edge to each is edge_ids[indptr[i]:indptr[i+1]].
        self.indptr, self.indices, self.edge_ids = self._build_adjacency()

        # Equirectangular projection to metres around the network's centre, so the
        # KD-tree answers "nearest node" in metres. Accurate to well under 1% across
        # a city; routing does not need better.
        self._lat0 = (
            math.radians(float(self.node_lat.mean())) if len(self.node_lat) else 0.0
        )
        self._tree = cKDTree(self._to_xy(self.node_lat, self.node_lng))

    # -- construction -------------------------------------------------------

    @classmethod
    def load(cls, path: Path | str = DEFAULT_NETWORK_PATH) -> WalkNetwork:
        """Read a ``.npz`` written by ``save_network``."""
        with np.load(Path(path), allow_pickle=False) as file:
            manifest = json.loads(str(file["manifest"]))
            arrays = {key: file[key] for key in file.files if key != "manifest"}
        return cls(arrays, manifest)

    @classmethod
    def from_data(cls, data: NetworkData) -> WalkNetwork:
        """Build directly from ``export_network`` output (tests, or build-time checks)."""
        return cls(dict(data.arrays), dict(data.manifest))

    def _build_adjacency(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        n = len(self.node_osmid)
        e = len(self.edge_u)
        # Each undirected edge appears twice: u -> v and v -> u.
        src = np.concatenate([self.edge_u, self.edge_v])
        dst = np.concatenate([self.edge_v, self.edge_u])
        eid = np.concatenate([np.arange(e, dtype=np.int32)] * 2)
        order = np.argsort(src, kind="stable")
        src_sorted = src[order]
        indptr = np.searchsorted(src_sorted, np.arange(n + 1, dtype=np.int32)).astype(
            np.int64
        )
        return indptr, dst[order].astype(np.int32), eid[order].astype(np.int32)

    def _to_xy(self, lat: np.ndarray | float, lng: np.ndarray | float) -> np.ndarray:
        x = EARTH_RADIUS_M * np.radians(lng) * math.cos(self._lat0)
        y = EARTH_RADIUS_M * np.radians(lat)
        return np.column_stack([x, y])

    # -- facts --------------------------------------------------------------

    @property
    def node_count(self) -> int:
        return len(self.node_osmid)

    @property
    def edge_count(self) -> int:
        return len(self.edge_u)

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        """(min_lng, min_lat, max_lng, max_lat) of all nodes."""
        return (
            float(self.node_lng.min()),
            float(self.node_lat.min()),
            float(self.node_lng.max()),
            float(self.node_lat.max()),
        )

    @property
    def nbytes(self) -> int:
        """Memory held by the arrays and the adjacency (the KD-tree adds a little more)."""
        arrays = [
            self.node_osmid, self.node_lat, self.node_lng, self.node_flags,
            self.edge_u, self.edge_v, self.edge_length_m, self.edge_flags,
            self.edge_highway, self.edge_name, self.edge_osmid,
            self.geom_offsets, self.geom_coords, self.indptr, self.indices, self.edge_ids,
        ]  # fmt: skip
        return int(sum(a.nbytes for a in arrays))

    # -- queries ------------------------------------------------------------

    def neighbors(self, node: int) -> tuple[np.ndarray, np.ndarray]:
        """Neighbour node indices and the edge index leading to each."""
        start, end = self.indptr[node], self.indptr[node + 1]
        return self.indices[start:end], self.edge_ids[start:end]

    def nearest_node(self, lat: float, lng: float) -> tuple[int, float]:
        """The closest node to a coordinate and its distance in metres."""
        distance, index = self._tree.query(self._to_xy(lat, lng)[0])
        return int(index), float(distance)

    def nearest_nodes(
        self, lat: float, lng: float, k: int
    ) -> tuple[np.ndarray, np.ndarray]:
        """The k closest nodes to a coordinate (indices, distances in metres), nearest first."""
        k = max(1, min(k, self.node_count))
        distances, indices = self._tree.query(self._to_xy(lat, lng)[0], k=k)
        return np.atleast_1d(indices).astype(int), np.atleast_1d(distances).astype(
            float
        )

    def edge_coords(self, edge: int) -> np.ndarray:
        """The edge's polyline as an array of (lng, lat), ordered from edge_u to edge_v."""
        return self.geom_coords[self.geom_offsets[edge] : self.geom_offsets[edge + 1]]

    def edge_label(self, edge: int) -> str:
        """The street name, else the raw OpenStreetMap highway type (for logs and
        tools; routing's ``_steps`` turns an unnamed way into plain words)."""
        name_index = int(self.edge_name[edge])
        if name_index >= 0:
            return self.names[name_index]
        return self.highway_types[int(self.edge_highway[edge])]

    def length_matrix(self, edge_weights: np.ndarray | None = None) -> csr_matrix:
        """A symmetric sparse matrix of edge weights, for ``scipy.sparse.csgraph``.

        ``edge_weights`` defaults to the edge lengths. Routing (OP-207) passes
        profile-adjusted weights, with removed edges set to 0; those entries are
        dropped from the matrix, so SciPy sees no edge there at all.
        """
        weights = self.edge_length_m if edge_weights is None else edge_weights
        data = np.asarray(weights, dtype=np.float64)[self.edge_ids]
        n = self.node_count
        # Copy the index arrays: eliminate_zeros() edits the matrix in place, and the
        # originals must stay intact for neighbors().
        matrix = csr_matrix(
            (data, self.indices.copy(), self.indptr.copy()), shape=(n, n)
        )
        # SciPy's graph algorithms treat an explicitly stored 0 as a FREE edge, not a
        # missing one. Dropping zero entries makes "weight 0" mean "no edge".
        matrix.eliminate_zeros()
        return matrix


def measure_load(path: Path | str = DEFAULT_NETWORK_PATH) -> dict[str, float]:
    """Load the network and report wall time and array memory. Used by the build script."""
    start = time.perf_counter()
    network = WalkNetwork.load(path)
    seconds = time.perf_counter() - start
    return {
        "load_seconds": round(seconds, 3),
        "arrays_mb": round(network.nbytes / 1e6, 1),
        "file_mb": round(Path(path).stat().st_size / 1e6, 1),
    }
