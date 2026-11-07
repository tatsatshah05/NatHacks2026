"""OP-106 / issue #34: check candidate distances and honest review labels."""

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from cloud.app.network import DEFAULT_NETWORK_PATH, WalkNetwork
from scripts.elevator_candidates import distances, distinct_candidates

ROOT = Path(__file__).resolve().parents[2]


def test_distance_uses_nearest_of_multiple_station_points() -> None:
    actual = distances(
        np.array([53.5, 53.6]),
        np.array([-113.5, -113.5]),
        [(53.5, -113.5), (53.6, -113.5)],
    )
    assert np.allclose(actual, [0, 0])


def test_distance_is_in_metres_and_handles_longitude() -> None:
    actual = distances(np.array([0.0]), np.array([1.0]), [(0.0, 0.0)])
    assert actual[0] == pytest.approx(111_195.08, abs=0.1)


def test_committed_candidate_report_covers_inventory_without_claiming_matches() -> None:
    report = json.loads((ROOT / "docs/elevator-candidates.json").read_text("utf-8"))
    inventory = json.loads((ROOT / "docs/elevators.proposed.json").read_text("utf-8"))
    assert {row["device_id"] for row in report["entries"]} == {
        row["device_id"] for row in inventory["elevators"]
    }
    assert all(
        row["review_status"] == "unreviewed; no active match"
        for row in report["entries"]
    )
    assert (
        report["network_sha256"]
        == hashlib.sha256(DEFAULT_NETWORK_PATH.read_bytes()).hexdigest()
    )
    assert report["network_manifest"] == WalkNetwork.load().manifest
    for entry in report["entries"]:
        refs = [(item["type"], item["id"]) for item in entry["elevator_candidates"]]
        assert len(refs) == len(set(refs))
        for candidate in entry["elevator_candidates"]:
            assert candidate["distance_m"] <= report["search_radius_m"]
            assert candidate["url"].startswith("https://www.openstreetmap.org/")


def test_candidate_objects_are_deduplicated_using_the_closest_segment() -> None:
    candidates = [
        {"type": "way", "id": 12, "distance_m": 25.0},
        {"type": "way", "id": 12, "distance_m": 10.0},
        {"type": "node", "id": 12, "distance_m": 5.0},
    ]
    assert distinct_candidates(candidates) == [candidates[2], candidates[1]]


def test_proposed_device_candidates_have_recorded_osm_evidence() -> None:
    inventory = json.loads((ROOT / "docs/elevators.proposed.json").read_text("utf-8"))
    for entry in inventory["elevators"]:
        if entry["status"] != "candidate":
            continue
        assert entry["reviewed_by"] is None
        for ref in entry["osm_refs"]:
            path = (
                ROOT
                / "cloud/tests/fixtures/elevator_mapping/osm"
                / f"{ref['type']}-{ref['id']}.json"
            )
            source = json.loads(path.read_text("utf-8"))["elements"][0]
            assert (source["type"], source["id"]) == (ref["type"], ref["id"])
            assert source["tags"]["highway"] == "elevator"
