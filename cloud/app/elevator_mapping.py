"""OP-106: help routes avoid broken LRT elevators with reviewed map matches.

Check the City elevator inventory, require a second person's review of each
OSM match, and identify the elevator edges to remove for every mobility profile.
Keep unmatched outages available as station warnings instead of guessing a match.

The optional network adapter uses OP-104's existing arrays and flags without
changing its format. The mapping document in docs/ is a proposal; shared
config/ remains unchanged until the format and matches are reviewed.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from cloud.app.network import WalkNetwork

Row = dict[str, Any]
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


@dataclass(frozen=True)
class ElevatorEdge:
    """An OP-104 edge index with its OSM references and elevator flag.

    network_elevator_edges() builds these from the existing OP-104 flags.
    Paths through a node tagged as an elevator belong to that elevator's access.
    """

    index: int
    osm_refs: frozenset[OsmRef]
    is_elevator: bool

    def __post_init__(self) -> None:
        if type(self.index) is not int or self.index < 0:
            raise MappingError("Graph edge indices must be nonnegative integers")
        if type(self.is_elevator) is not bool:
            raise MappingError("Graph edges need an explicit elevator flag")


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
        matched_by, reviewed_by = entry.get("matched_by"), entry.get("reviewed_by")
        if status == "unmapped":
            if refs or not reason or matched_by is not None or reviewed_by is not None:
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
        if edge.is_elevator:
            for ref in edge.osm_refs:
                by_ref.setdefault(ref, set()).add(edge.index)
    blocked: set[int] = set()
    warnings: list[StationWarning] = []
    for obstruction in active_obstructions:
        # A City escalator caution or another source's report cannot close a lift.
        if (
            obstruction.get("source") != "city_lrt_devices"
            or obstruction.get("kind") != "elevator_out"
            or obstruction.get("effect") != "blocked"
        ):
            continue
        device_id = _text(obstruction, "source_ref")
        mapping = mappings.get(device_id)
        reason = "No device-to-OSM mapping exists for this outage."
        if mapping is not None and mapping.status == "reviewed":
            for ref in mapping.osm_refs:
                blocked.update(by_ref.get(ref, set()))
            if mapping.osm_refs.issubset(by_ref):
                continue
            reason = (
                "Some reviewed OSM references are absent from the elevator network."
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


def network_elevator_edges(network: WalkNetwork) -> list[ElevatorEdge]:
    """Translate OP-104's flagged nodes/ways without changing the shared graph.

    A node elevator closes all paths through that node. A way elevator closes
    that undirected edge. Ordinary paths elsewhere in the station stay available.
    Build this small index once after loading the network.
    """
    from cloud.app.network import EDGE_ELEVATOR, NODE_ELEVATOR

    elevator_nodes = {
        index: OsmRef("node", int(osmid))
        for index, osmid in enumerate(network.node_osmid)
        if int(network.node_flags[index]) & NODE_ELEVATOR
    }
    result = []
    for index in range(network.edge_count):
        refs = set()
        for endpoint in (int(network.edge_u[index]), int(network.edge_v[index])):
            if endpoint in elevator_nodes:
                refs.add(elevator_nodes[endpoint])
        if int(network.edge_flags[index]) & EDGE_ELEVATOR:
            way_id = int(network.edge_osmid[index])
            if way_id > 0:
                refs.add(OsmRef("way", way_id))
        if refs:
            result.append(ElevatorEdge(index, frozenset(refs), True))
    return result


def masked_edge_weights(
    network: WalkNetwork,
    blocked_indices: frozenset[int],
    edge_weights: Sequence[float] | None = None,
) -> list[float]:
    """Copy profile weights and remove failed elevators from both directions.

    OP-104's length_matrix() drops zero-weight edges from its symmetric sparse
    matrix. Copying keeps the shared graph and other profiles unchanged.
    """
    source = network.edge_length_m if edge_weights is None else edge_weights
    weights = [float(value) for value in source]
    if len(weights) != network.edge_count:
        raise MappingError("Profile weights do not match the network edge count")
    for index in blocked_indices:
        if type(index) is not int or not 0 <= index < len(weights):
            raise MappingError("Blocked edge index is outside this network")
        weights[index] = 0.0
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
