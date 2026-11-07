"""OP-106 / issue #34: check candidate distances and honest review labels."""

import json
from pathlib import Path

import numpy as np
import pytest

from scripts.elevator_candidates import distances

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
    assert report["network_manifest"]["elevator_nodes"] == 9
    assert report["network_manifest"]["elevator_edges"] == 4
    assert report["network_sha256"]
    for entry in report["entries"]:
        for candidate in entry["elevator_candidates"]:
            assert candidate["distance_m"] <= report["search_radius_m"]
            assert candidate["url"].startswith("https://www.openstreetmap.org/")
