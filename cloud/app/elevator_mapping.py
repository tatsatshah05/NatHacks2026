"""OP-106: help routes avoid broken LRT elevators with reviewed map matches.

Check the City elevator inventory, require a second person's review of each
OSM match, and identify the elevator edges to remove for every mobility profile.
Keep unmatched outages available as station warnings instead of guessing a match.

The network adapter uses OP-104's existing arrays and flags without
changing its format. The mapping document in docs/ is a proposal; shared
config/ remains unchanged until the format and matches are reviewed.
"""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from numbers import Integral
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import numpy as np

if TYPE_CHECKING:
    from cloud.app.network import WalkNetwork

Row = dict[str, Any]
LOGGER = logging.getLogger(__name__)
UNMAPPED_REASON = (
    "Device-to-OSM match needs independent verification against the OP-104 network."
)


class MappingError(ValueError):
    """The inventory or proposed matches cannot safely be used for routing."""


@dataclass(frozen=True, order=True)
class OsmRef:
    """Keep node and way IDs separate, even when their numbers are equal."""

    type: Literal["node", "way"]
    id: int

    def __post_init__(self) -> None:
        if self.type not in {"node", "way"}:
            raise MappingError("OSM references must identify a node or way")
        if type(self.id) is not int or self.id <= 0:
            raise MappingError("OSM IDs must be positive integers")


@dataclass(frozen=True)
class ElevatorMapping:
    device_id: str
    station: str
    location: str
    status: Literal["unmapped", "candidate", "reviewed"]
    osm_refs: frozenset[OsmRef]
    reason: str
    matched_by: str | None
    reviewed_by: str | None
    evidence: tuple[str, ...]
    anchor_node_id: int | None = None
    edge_pairs: frozenset[tuple[int, int]] = frozenset()


@dataclass(frozen=True)
class ElevatorEdge:
    """A vertical-link edge with its OSM references and endpoint IDs.

    network_elevator_edges() builds these from the existing OP-104 flags.
    Ground-level paths are excluded unless an exact pair is independently
    reviewed as the vertical link. Merely touching an elevator node is not enough.
    """

    index: int
    osm_refs: frozenset[OsmRef]
    node_pair: tuple[int, int] | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.index, bool)
            or not isinstance(self.index, Integral)
            or self.index < 0
        ):
            raise MappingError("Graph edge indices must be nonnegative integers")
        if self.node_pair is not None:
            if not isinstance(self.node_pair, tuple) or len(self.node_pair) != 2:
                raise MappingError("A vertical link needs two endpoint IDs")
            for endpoint in self.node_pair:
                OsmRef("node", endpoint)
            if self.node_pair[0] >= self.node_pair[1]:
                raise MappingError(
                    "Vertical link endpoint IDs must be distinct and sorted"
                )


@dataclass
class StationWarning:
    device_id: str
    station: str
    reason: str
    obstruction: Row


@dataclass
class BlockingResult:
    blocked_edge_indices: frozenset[int]
    station_warnings: list[StationWarning]


def _text(row: Mapping[str, Any], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise MappingError(f"Missing or invalid {key}")
    return value.strip()


def _elevators(devices: Sequence[Row]) -> dict[str, Row]:
    """Use the master list for identity only; it is not the live outage feed."""
    result: dict[str, Row] = {}
    for device in devices:
        if not isinstance(device, dict):
            raise MappingError("City device records must be objects")
        if device.get("device_type") != "Elevator":
            continue
        device_id = _text(device, "device_id")
        if device_id in result:
            raise MappingError(f"Duplicate City elevator {device_id}")
        result[device_id] = device
    if not result:
        raise MappingError("City master list contains no elevators")
    return result


def prepare_inventory(devices: Sequence[Row]) -> Row:
    """List every official elevator as unverified until real evidence is reviewed."""
    entries = []
    for device_id, device in sorted(
        _elevators(devices).items(), key=lambda x: int(x[0])
    ):
        entries.append(
            {
                "device_id": device_id,
                "station": _text(device, "lrt_station_name"),
                "location": _text(device, "lrt_device_location"),
                "status": "unmapped",
                "osm_refs": [],
                "reason": UNMAPPED_REASON,
                "matched_by": None,
                "reviewed_by": None,
                "evidence": [],
                "anchor_node_id": None,
                "edge_pairs": [],
            }
        )
    return {"schema_version": 1, "elevators": entries}


def parse_mappings(document: Row, devices: Sequence[Row]) -> dict[str, ElevatorMapping]:
    """Check full coverage and review evidence before accepting the proposal."""
    if not isinstance(document, dict):
        raise MappingError("Elevator mapping document must be an object")
    if (
        type(document.get("schema_version")) is not int
        or document["schema_version"] != 1
    ):
        raise MappingError("Unsupported elevator mapping schema_version")
    entries = document.get("elevators")
    if not isinstance(entries, list):
        raise MappingError("elevators must be a list")
    city = _elevators(devices)
    result: dict[str, ElevatorMapping] = {}
    reviewed_refs: dict[OsmRef, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise MappingError("Elevator mappings must be objects")
        device_id = _text(entry, "device_id")
        if device_id not in city or device_id in result:
            raise MappingError(f"Unknown or duplicate mapped elevator {device_id}")
        station, location = _text(entry, "station"), _text(entry, "location")
        if (
            station != city[device_id]["lrt_station_name"]
            or location != city[device_id]["lrt_device_location"]
        ):
            raise MappingError(f"City metadata changed for elevator {device_id}")
        status = entry.get("status")
        if status not in {"unmapped", "candidate", "reviewed"}:
            raise MappingError(f"Invalid mapping status for {device_id}")
        raw_refs = entry.get("osm_refs")
        if not isinstance(raw_refs, list) or any(
            not isinstance(ref, dict) for ref in raw_refs
        ):
            raise MappingError("osm_refs must be a list of node/way references")
        refs = frozenset(OsmRef(ref.get("type"), ref.get("id")) for ref in raw_refs)
        if len(refs) != len(raw_refs):
            raise MappingError(f"Duplicate OSM reference for {device_id}")
        evidence = entry.get("evidence")
        if not isinstance(evidence, list) or any(
            not isinstance(item, str) or not item.strip() for item in evidence
        ):
            raise MappingError(
                "evidence must be a list of nonempty descriptions or URLs"
            )
        reason = entry.get("reason", "")
        if not isinstance(reason, str):
            raise MappingError("Mapping reasons must be text")
        reason = reason.strip()
        anchor_node_id = entry.get("anchor_node_id")
        if anchor_node_id is not None:
            OsmRef("node", anchor_node_id)
        raw_pairs = entry.get("edge_pairs", [])
        if not isinstance(raw_pairs, list):
            raise MappingError("edge_pairs must be a list of OSM node pairs")
        pairs = set()
        for pair in raw_pairs:
            if not isinstance(pair, list) or len(pair) != 2:
                raise MappingError("Each edge pair needs two OSM node IDs")
            first, second = (OsmRef("node", value).id for value in pair)
            if first == second:
                raise MappingError("An elevator edge cannot be a self-loop")
            pairs.add(tuple(sorted((first, second))))
        if len(pairs) != len(raw_pairs):
            raise MappingError("Duplicate elevator edge pair")
        matched_by, reviewed_by = entry.get("matched_by"), entry.get("reviewed_by")
        if status == "unmapped":
            if (
                refs
                or pairs
                or anchor_node_id is not None
                or not reason
                or matched_by is not None
                or reviewed_by is not None
            ):
                raise MappingError(
                    "Unmapped elevators need a reason and no claimed match"
                )
        else:
            if (
                not refs
                or not evidence
                or not isinstance(matched_by, str)
                or not matched_by.strip()
            ):
                raise MappingError(
                    "OSM matches need references, evidence and a matcher"
                )
            if status == "candidate" and reviewed_by is not None:
                raise MappingError("Candidate matches cannot claim a completed review")
            if status == "reviewed":
                if not isinstance(reviewed_by, str) or not reviewed_by.strip():
                    raise MappingError("Reviewed matches need a second reviewer")
                if reviewed_by.strip().casefold() == matched_by.strip().casefold():
                    raise MappingError("An elevator match cannot be self-reviewed")
                # One OSM object must not silently stand in for two different lifts.
                for ref in refs:
                    if ref in reviewed_refs:
                        raise MappingError(
                            f"OSM reference assigned to multiple elevators: {ref}"
                        )
                    reviewed_refs[ref] = device_id
        result[device_id] = ElevatorMapping(
            device_id=device_id,
            station=station,
            location=location,
            status=status,
            osm_refs=refs,
            reason=reason,
            matched_by=matched_by,
            reviewed_by=reviewed_by,
            evidence=tuple(evidence),
            anchor_node_id=anchor_node_id,
            edge_pairs=frozenset(pairs),
        )
    if result.keys() != city.keys():
        missing = ", ".join(sorted(city.keys() - result.keys()))
        raise MappingError(f"Elevators missing from mapping inventory: {missing}")
    return result


def block_elevator_outages(
    mappings: Mapping[str, ElevatorMapping],
    edges: Sequence[ElevatorEdge],
    active_obstructions: Sequence[Row],
) -> BlockingResult:
    """Identify unusable elevator edges before any profile-specific routing.

    Pass current, active obstruction dictionaries from the shared store. This
    helper does not fetch status or decide obstruction freshness itself.
    """
    by_ref: dict[OsmRef, set[int]] = {}
    seen_indices: set[int] = set()
    for edge in edges:
        if edge.index in seen_indices:
            raise MappingError(f"Duplicate graph edge index {edge.index}")
        seen_indices.add(edge.index)
        for ref in edge.osm_refs:
            by_ref.setdefault(ref, set()).add(edge.index)
    blocked: set[int] = set()
    warnings: list[StationWarning] = []
    for row_number, obstruction in enumerate(active_obstructions, start=1):
        if not isinstance(obstruction, Mapping):
            LOGGER.warning(
                "Skipping elevator outage row %s: expected an object", row_number
            )
            continue
        # A City escalator caution or another source's report cannot close a lift.
        if (
            obstruction.get("source") != "city_lrt_devices"
            or obstruction.get("kind") != "elevator_out"
            or obstruction.get("effect") != "blocked"
        ):
            continue
        try:
            device_id = _text(obstruction, "source_ref")
        except MappingError as exc:
            LOGGER.warning("Skipping elevator outage row %s: %s", row_number, exc)
            continue
        mapping = mappings.get(device_id)
        reason = "No device-to-OSM mapping exists for this outage."
        if mapping is not None and mapping.status == "reviewed":
            allowed = {
                edge.index
                for edge in edges
                if not mapping.edge_pairs or edge.node_pair in mapping.edge_pairs
            }
            missing = set()
            matched = set()
            for ref in mapping.osm_refs:
                found = by_ref.get(ref, set()) & allowed
                if found:
                    matched.update(found)
                else:
                    missing.add(ref)
            # A simplified edge may retain another way's ID. A reviewed nearest
            # link endpoint still identifies its flagged vertical connection.
            if (
                missing
                and all(ref.type == "way" for ref in missing)
                and mapping.anchor_node_id is not None
            ):
                anchored = (
                    by_ref.get(OsmRef("node", mapping.anchor_node_id), set()) & allowed
                )
                link_pairs = {
                    edge.node_pair for edge in edges if edge.index in anchored
                }
                if anchored and len(link_pairs) == 1 and None not in link_pairs:
                    matched.update(anchored)
                    missing.clear()
            blocked.update(matched)
            if not missing:
                continue
            reason = (
                "A reviewed match has no unambiguous vertical link; "
                "verify its anchor or edge_pairs."
            )
        elif mapping is not None:
            reason = (
                mapping.reason or "The OSM match still needs a second person's review."
            )
        # Preserve the original station geometry/detail for the route report.
        warnings.append(
            StationWarning(
                device_id=device_id,
                station=mapping.station if mapping else "See outage station details",
                reason=reason,
                obstruction=deepcopy(obstruction),
            )
        )
    return BlockingResult(frozenset(blocked), warnings)


def network_elevator_edges(
    network: WalkNetwork, mappings: Mapping[str, ElevatorMapping] | None = None
) -> list[ElevatorEdge]:
    """Translate OP-104's flagged nodes/ways without changing the shared graph.

    Include flagged elevator ways and exact reviewed vertical edge pairs. Never
    infer that all edges at an elevator node are vertical. Build once per network
    or reviewed mapping update; Python only visits the small candidate subset.
    """
    from cloud.app.network import EDGE_ELEVATOR, NODE_ELEVATOR

    elevator_nodes = np.flatnonzero(network.node_flags & NODE_ELEVATOR)
    flagged = (network.edge_flags & EDGE_ELEVATOR) != 0
    touches_node = np.isin(network.edge_u, elevator_nodes) | np.isin(
        network.edge_v, elevator_nodes
    )
    reviewed_pairs: dict[tuple[int, int], set[OsmRef]] = {}
    for entry in (mappings or {}).values():
        if entry.status == "reviewed":
            for pair in entry.edge_pairs:
                reviewed_pairs.setdefault(pair, set()).update(entry.osm_refs)
    pair_nodes = [node for pair in reviewed_pairs for node in pair]
    pair_node_indices = np.flatnonzero(np.isin(network.node_osmid, pair_nodes))
    touches_pair = np.isin(network.edge_u, pair_node_indices) | np.isin(
        network.edge_v, pair_node_indices
    )
    candidates = np.flatnonzero(flagged | touches_node | touches_pair)
    result = []
    for index in candidates:
        pair = tuple(
            sorted(
                (
                    int(network.node_osmid[network.edge_u[index]]),
                    int(network.node_osmid[network.edge_v[index]]),
                )
            )
        )
        explicit_refs = reviewed_pairs.get(pair, set())
        if not flagged[index] and not explicit_refs:
            continue
        refs = {OsmRef("node", endpoint) for endpoint in pair}
        refs.update(explicit_refs)
        if flagged[index]:
            way_id = int(network.edge_osmid[index])
            if way_id > 0:
                refs.add(OsmRef("way", way_id))
        if refs:
            result.append(ElevatorEdge(int(index), frozenset(refs), pair))
    return result


def masked_edge_weights(
    network: WalkNetwork,
    blocked_indices: Iterable[Integral],
    edge_weights: Sequence[float] | None = None,
) -> np.ndarray:
    """Copy profile weights and remove failed elevators from both directions.

    OP-104's length_matrix() drops zero-weight edges from its symmetric sparse
    matrix. Copying keeps the shared graph and other profiles unchanged.
    """
    source = network.edge_length_m if edge_weights is None else edge_weights
    weights = np.array(source, dtype=np.float64, copy=True)
    if weights.shape != (network.edge_count,):
        raise MappingError("Profile weights do not match the network edge count")
    indices = list(blocked_indices)
    for index in indices:
        if (
            isinstance(index, bool)
            or not isinstance(index, Integral)
            or not 0 <= index < len(weights)
        ):
            raise MappingError("Blocked edge index is outside this network")
    weights[np.asarray(indices, dtype=np.intp)] = 0.0
    return weights


def main() -> None:
    """Audit a proposed mapping inventory without changing shared config."""
    parser = argparse.ArgumentParser(description="OP-106 elevator mapping audit")
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--devices", type=Path, required=True)
    args = parser.parse_args()
    document = json.loads(args.inventory.read_text(encoding="utf-8-sig"))
    devices = json.loads(args.devices.read_text(encoding="utf-8-sig"))
    mappings = parse_mappings(document, devices)
    print(
        json.dumps(
            {
                "story": "OP-106",
                "elevator_count": len(mappings),
                "reviewed": sum(m.status == "reviewed" for m in mappings.values()),
                "candidates": sum(m.status == "candidate" for m in mappings.values()),
                "unmapped": sum(m.status == "unmapped" for m in mappings.values()),
                "entries": [
                    {
                        "device_id": m.device_id,
                        "station": m.station,
                        "status": m.status,
                        "reason": m.reason,
                    }
                    for m in mappings.values()
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
