"""OP-407: changed-schema fixtures.

``fixtures/schema_change/`` holds copies of three recorded City responses in which
one row has a required field renamed and every row has a field we do not know
(see its README). Through each adapter, and through the replay app, the good rows
still load, the renamed row is rejected and logged, the new names appear in
``unexpected_fields`` on ``GET /api/status``, the daily drop shows, and routing
still works.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient

from cloud.app import sources as src
from cloud.app.adapters import lrt_devices as lrt
from cloud.app.adapters import traffic_disruptions as td
from cloud.app.adapters._quality import QualityReport
from cloud.app.adapters.city_advisories import TrailClosuresAdapter
from cloud.app.main import create_app
from cloud.app.network import DEFAULT_NETWORK_PATH
from cloud.app.scheduler import Scheduler

FIXTURES = Path(__file__).parent / "fixtures"
CHANGED = FIXTURES / "schema_change"
# The frozen test clock: disruption 1309 and the first trail notice are in force.
NOW = datetime(2026, 11, 10, 18, 0, tzinfo=UTC)
EDMONTON = ZoneInfo("America/Edmonton")
TRAFFIC = "traffic_disruptions/current.json"
TRAILS = "city_advisories/trails.json"
OUTAGES = "lrt/outages.json"


def rows(root: Path, name: str) -> list[dict[str, Any]]:
    return json.loads((root / name).read_text(encoding="utf-8"))


def snapshot(tmp_path: Path) -> Path:
    """The changed files as a replay snapshot, plus the LRT lookups they need."""
    root = tmp_path / "snapshot"
    shutil.copytree(CHANGED, root)
    for name in ("devices.json", "stations.json"):
        shutil.copy(FIXTURES / "lrt" / name, root / "lrt" / name)
    return root


# ------------------------------------------------------- the fixtures themselves


@pytest.mark.parametrize(
    ("name", "key", "target", "renamed", "added"),
    [
        (
            TRAFFIC,
            "disruption_id",
            "1309",
            ("point", "location"),
            ("feed_version", "2"),
        ),
        (
            TRAILS,
            "id_number",
            "1",
            ("details", "description"),
            ("source_system", "ArcGIS"),
        ),
        (
            OUTAGES,
            "device_id",
            "43",
            ("since_time_stamp", "outage_since"),
            ("data_as_of", "2026-11-07T21:05:17Z"),
        ),
    ],
)
def test_each_copy_differs_from_its_original_only_as_documented(
    name: str,
    key: str,
    target: str,
    renamed: tuple[str, str],
    added: tuple[str, str],
) -> None:
    original, changed = rows(FIXTURES, name), rows(CHANGED, name)
    assert len(original) == len(changed) > 1
    old, new = renamed
    hits = 0
    for before, after in zip(original, changed, strict=True):
        expected = {**before, added[0]: added[1]}
        if before[key] == target:
            expected[new] = expected.pop(old)
            hits += 1
        assert after == expected
    assert hits == 1


# ------------------------------------------------------------- the adapters


def test_traffic_disruptions_keep_the_good_rows_and_count_the_bad_one(
    caplog: pytest.LogCaptureFixture,
) -> None:
    baseline = td.TrafficDisruptionsAdapter(td.FixtureRowReader(FIXTURES / TRAFFIC))
    assert len(baseline.fetch(NOW)) == 2
    assert baseline.quality.report() == QualityReport()  # the recorded shape is known
    adapter = td.TrafficDisruptionsAdapter(td.FixtureRowReader(CHANGED / TRAFFIC))
    with caplog.at_level(logging.WARNING):
        items = adapter.fetch(NOW)
    assert [item.source_ref for item in items] != ["1309"] and len(items) == 1
    assert adapter.quality.report() == QualityReport(
        1, ("feed_version", "location"), "disruption 1309: missing point"
    )
    assert "SKIP city_traffic_disruptions disruption 1309: missing point" in caplog.text


def test_trail_closures_keep_the_good_rows_and_count_the_bad_one(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    async def fetch(folder: Path) -> TrailClosuresAdapter:
        async with httpx.AsyncClient(transport=src.replay_transport(folder)) as client:
            adapter = TrailClosuresAdapter(client)
            adapter.items = await adapter.fetch(NOW)  # type: ignore[attr-defined]
            return adapter

    baseline = asyncio.run(fetch(FIXTURES))
    assert len(baseline.items) == 46  # type: ignore[attr-defined]
    assert baseline.quality.report() == QualityReport()
    with caplog.at_level(logging.WARNING):
        adapter = asyncio.run(fetch(snapshot(tmp_path)))
    assert len(adapter.items) == 45  # type: ignore[attr-defined]
    assert "city_trail_closures:1" not in {i["id"] for i in adapter.items}  # type: ignore[attr-defined]
    assert adapter.quality.report() == QualityReport(
        1, ("description", "source_system"), "missing or invalid details"
    )
    assert (
        "SKIP city_trail_closures row=1 reason=missing or invalid details"
        in caplog.text
    )


def test_lrt_outages_keep_the_good_rows_and_count_the_bad_one(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    baseline = lrt.LrtDevicesAdapter(
        src.ReplayDatasetReader(FIXTURES), source_timezone=EDMONTON
    )
    assert len(baseline.fetch(NOW)) == 7
    assert baseline.quality.report() == QualityReport()
    adapter = lrt.LrtDevicesAdapter(
        src.ReplayDatasetReader(snapshot(tmp_path)), source_timezone=EDMONTON
    )
    with caplog.at_level(logging.WARNING):
        items = adapter.fetch(NOW)
    assert len(items) == 6 and "43" not in {item["source_ref"] for item in items}
    assert adapter.quality.report() == QualityReport(
        1, ("data_as_of", "outage_since"), "LRT record missing since_time_stamp"
    )
    assert "Skipping snws-u3zx row 1 (device 43)" in caplog.text
    assert adapter.last_error == "Skipped 1 invalid row"  # unchanged adapter behaviour


# --------------------------------------------------------------- the whole app


def test_the_replay_app_shows_the_changes_on_status_and_still_routes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Replay mode, frozen clock: the three changed feeds, the drop and a route."""
    folder = snapshot(tmp_path)
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("SCHEDULER", "off")  # the test drives the fetches itself
    monkeypatch.setenv("DATA_MODE", "replay")
    monkeypatch.setenv("REPLAY_SNAPSHOT", str(folder))
    monkeypatch.setenv("REPLAY_AT", NOW.isoformat())
    monkeypatch.delenv("REPLAY_CLOCK", raising=False)
    configs = [
        src.SourceConfig(key=key)
        for key in ("lrt", "trail_closures", "traffic_disruptions")
    ]

    def refuse(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"live request attempted: {request.url}")

    app = create_app()
    with TestClient(app) as client:
        clock = app.state.clock
        assert clock() == NOW  # REPLAY_AT froze the app clock
        counts = app.state.daily_counts
        counts.record("city_traffic_disruptions", 12, clock() - timedelta(days=1))

        async def scenario() -> Scheduler:
            async with httpx.AsyncClient(transport=httpx.MockTransport(refuse)) as a:
                with httpx.Client(transport=httpx.MockTransport(refuse)) as s:
                    built = src.build_sources(
                        mode="replay",
                        snapshot=folder,
                        app_token=None,
                        async_client=a,
                        sync_client=s,
                        weather_service=None,
                        configs=configs,
                    )
                    scheduler = Scheduler(
                        app.state.store, built, clock=clock, counts=counts
                    )
                    for source in scheduler.sources:
                        assert source.quality is not None
                        assert await scheduler.run_once(source) is True
                    return scheduler

        with caplog.at_level(logging.WARNING):
            scheduler = asyncio.run(scenario())
        app.state.scheduler = scheduler
        body = client.get("/api/status").json()
        active = app.state.store.active(clock())
        route = client.get(
            "/api/route",
            params={
                "from": "53.5444,-113.4909",
                "to": "53.5468,-113.4973",
                "profile": "standard",
            },
        )
    assert body["mode"] == "replay"
    by_name = {entry["source"]: entry for entry in body["sources"]}
    traffic = by_name["city_traffic_disruptions"]
    assert traffic["item_count"] == 1 and traffic["rejected_rows"] == 1
    assert traffic["unexpected_fields"] == ["feed_version", "location"]
    assert traffic["last_validation_error"] == "disruption 1309: missing point"
    assert traffic["count_change"] == {"today": 1, "yesterday": 12, "drop": True}
    trails = by_name["city_trail_closures"]
    assert trails["item_count"] == 45 and trails["rejected_rows"] == 1
    assert trails["unexpected_fields"] == ["description", "source_system"]
    assert trails["count_change"] == {"today": 45, "yesterday": None, "drop": False}
    outages = by_name["city_lrt_devices"]
    assert outages["item_count"] == 6 and outages["rejected_rows"] == 1
    assert outages["unexpected_fields"] == ["data_as_of", "outage_since"]
    assert outages["last_validation_error"] == "LRT record missing since_time_stamp"
    assert outages["count_change"] == {"today": 6, "yesterday": None, "drop": False}
    assert all(entry["stale"] is False for entry in by_name.values())
    assert (
        "WARN quality city_traffic_disruptions: items fell from 12 to 1" in caplog.text
    )
    assert (
        "WARN quality city_trail_closures: new fields description, source_system"
        in caplog.text
    )
    assert len(active) == 1 + 45 + 6  # a drop and a changed shape never clear data
    if DEFAULT_NETWORK_PATH.exists():
        assert route.status_code == 200, route.text
        assert route.json()["routes"]
    else:
        assert route.status_code == 503  # no network file on a bare checkout
