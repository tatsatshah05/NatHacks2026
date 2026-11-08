"""OP-108: road closures from the City's traffic disruptions feed become caution items."""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from cloud.app.adapters import traffic_disruptions as td
from cloud.app.adapters._text import DETAIL_MAX_CHARS
from cloud.app.schemas import SourceAdapter

FIXTURES = Path(__file__).parent / "fixtures" / "traffic_disruptions"
# Inside the recorded window: disruption 1309 (124 Avenue) runs Nov 9 to Nov 25.
NOW = datetime(2026, 11, 10, 18, 0, tzinfo=UTC)


@pytest.fixture
def rows() -> list[dict]:
    return json.loads((FIXTURES / "current.json").read_text())


def test_fixture_matches_its_manifest(rows) -> None:
    manifest = json.loads((FIXTURES / "manifest.json").read_text())
    assert manifest["dataset"] == td.DATASET
    assert manifest["rows"] == len(rows) == 30


def test_only_active_closures_become_items(rows) -> None:
    """Delays are skipped; closures whose dates cover 'now' are kept."""
    items, skipped = td.normalize(rows, NOW)
    assert skipped == []
    assert items  # the recorded sample has several closures in force on Nov 10
    assert all(item.title in ("Road closed", "Road partly closed") for item in items)
    closures_in_force = [
        r
        for r in rows
        if r["impact"] in td.CLOSURE_IMPACTS
        and r["start_date"][:10] <= "2026-11-10" <= r["finish_date"][:10]
    ]
    assert len(items) == len(closures_in_force)


def test_item_fields_follow_the_contract(rows) -> None:
    items, _ = td.normalize(rows, NOW)
    item = next(i for i in items if i.source_ref == "1309")
    assert item.id == "city_traffic_disruptions:1309"
    assert (
        item.category == "road"
        and item.kind == "road_closed"
        and item.effect == "caution"
    )
    assert item.confidence == "high"
    assert item.title == "Road closed"
    assert item.detail.startswith("124 Avenue NW from 66 Street NW to 67 Street NW.")
    assert item.detail.endswith("Sidewalks may be affected.")
    assert item.geometry.type == "Point"
    # Local midnight on Nov 9 and the end of Nov 25, whatever offset the zone database gives.
    assert item.starts_at == datetime(2026, 11, 9, tzinfo=td.EDMONTON).astimezone(UTC)
    assert item.ends_at == datetime(
        2026, 11, 25, 23, 59, 59, tzinfo=td.EDMONTON
    ).astimezone(UTC)
    assert item.observed_at == NOW
    assert item.expires_at == NOW.replace(hour=18, minute=45)  # 3 x 15 min
    assert item.url == td.PORTAL_URL


def test_sidewalk_infrastructure_changes_the_wording(rows) -> None:
    row = copy.deepcopy(next(r for r in rows if r["disruption_id"] == "1309"))
    row["infrastructure"] = "Road and Sidewalk"
    items, _ = td.normalize([row], NOW)
    assert items[0].detail.endswith("Sidewalk affected.")


def test_future_and_finished_closures_are_not_in_force(rows) -> None:
    before = datetime(2026, 10, 1, 12, tzinfo=UTC)
    after = datetime(2026, 12, 31, 12, tzinfo=UTC)
    assert td.normalize(rows, before)[0] == []
    assert td.normalize(rows, after)[0] == []


def test_finish_date_is_inclusive(rows) -> None:
    row = next(r for r in rows if r["disruption_id"] == "1309")  # finishes Nov 25
    late_on_the_last_day = datetime(
        2026, 11, 26, 5, 30, tzinfo=UTC
    )  # 22:30 Nov 25 in Edmonton
    assert len(td.normalize([row], late_on_the_last_day)[0]) == 1
    next_morning = datetime(2026, 11, 26, 15, 0, tzinfo=UTC)
    assert td.normalize([row], next_morning)[0] == []


@pytest.mark.parametrize("status", ["Complete", "Canceled", "Hold"])
def test_inactive_statuses_are_ignored(rows, status) -> None:
    row = dict(next(r for r in rows if r["disruption_id"] == "1309"), status=status)
    items, skipped = td.normalize([row], NOW)
    assert items == [] and skipped == []


def test_bad_rows_are_skipped_not_fatal(rows, caplog) -> None:
    """One broken row is reported and the others still come through."""
    good = next(r for r in rows if r["disruption_id"] == "1309")
    no_point = dict(good, disruption_id="9001", point=None)
    no_dates = dict(good, disruption_id="9002", start_date=None)
    reversed_dates = dict(
        good,
        disruption_id="9003",
        start_date="2026-11-20T00:00:00.000",
        finish_date="2026-11-10T00:00:00.000",
    )
    with caplog.at_level("WARNING"):
        items, skipped = td.normalize([good, no_point, no_dates, reversed_dates], NOW)
    assert [i.source_ref for i in items] == ["1309"]
    assert len(skipped) == 3
    assert "SKIP city_traffic_disruptions disruption 9001: missing point" in caplog.text


def test_detail_is_capped_at_the_contract_limit(rows) -> None:
    row = dict(next(r for r in rows if r["disruption_id"] == "1309"), details="x" * 900)
    items, _ = td.normalize([row], NOW)
    assert len(items[0].detail) <= DETAIL_MAX_CHARS
    assert items[0].detail.endswith("... Sidewalks may be affected.")


def test_long_details_are_shortened_at_a_word_and_keep_the_sidewalk_note(
    rows,
) -> None:
    notice = " ".join(["Northbound curb lane closed for water main work"] * 20)
    row = dict(next(r for r in rows if r["disruption_id"] == "1309"), details=notice)
    items, skipped = td.normalize([row], NOW)
    assert skipped == [] and len(items) == 1
    detail = items[0].detail
    assert len(detail) <= DETAIL_MAX_CHARS
    assert detail.startswith(
        "124 Avenue NW from 66 Street NW to 67 Street NW. Northbound curb lane"
    )
    assert detail.endswith("... Sidewalks may be affected.")
    kept = detail.removesuffix("... Sidewalks may be affected.")
    assert kept.rsplit(" ", 1)[-1] in notice.split()  # a whole word, not half of one


def test_adapter_fetch_and_failure_keep_last_good() -> None:
    adapter = td.TrafficDisruptionsAdapter(
        td.FixtureRowReader(FIXTURES / "current.json")
    )
    assert isinstance(adapter, SourceAdapter)
    assert adapter.cadence_s == 900 and adapter.source == "city_traffic_disruptions"
    items = adapter.fetch(NOW)
    assert items and adapter.last_success == NOW and adapter.last_error is None

    class Broken:
        def read(self) -> list[dict]:
            raise httpx.ConnectError("boom")

    failing = td.TrafficDisruptionsAdapter(Broken())
    failing.last_good = items
    with pytest.raises(httpx.ConnectError):
        failing.fetch(NOW)
    assert failing.last_good == items  # a failure never clears data
    assert failing.last_error == "ConnectError: boom"


def test_live_reader_pages_through_the_api(rows) -> None:
    """The live reader asks for active statuses and stops at the first short page."""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        offset = int(request.url.params["$offset"])
        return httpx.Response(200, json=rows if offset == 0 else [])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    reader = td.SocrataRowReader(client, app_token="token", page_size=30)
    assert len(reader.read()) == 30
    assert len(seen) == 2  # a full page, then the empty page that ends the read
    assert "status in ('Current', 'Emergency', 'Revised')" in seen[0]["$where"]


def test_live_reader_rejects_unexpected_payloads() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"oops": 1}))
    )
    with pytest.raises(ValueError, match="unexpected"):
        td.SocrataRowReader(client).read()
