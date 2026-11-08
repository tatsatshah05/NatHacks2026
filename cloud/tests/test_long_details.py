"""A City notice longer than the contract's 500-character ``detail`` is shortened, never dropped.

The scheduler skips any item that breaks the Obstruction contract, so before this
fix two recorded trail notices (3 and 484) never reached the map or routing. Every
adapter that copies free City text into ``detail`` now shortens it with
``clip_detail``. Items are still skipped for genuinely invalid data.
"""

from __future__ import annotations

import asyncio
import copy
import json
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient

from cloud.app import sources as src
from cloud.app.adapters import construction, traffic_disruptions
from cloud.app.adapters._text import DETAIL_MAX_CHARS, ELLIPSIS, clip_detail
from cloud.app.adapters.city_advisories import TrailClosuresAdapter
from cloud.app.main import create_app
from cloud.app.scheduler import Scheduler
from cloud.app.schemas import Obstruction

FIXTURES = Path(__file__).resolve().parent / "fixtures"
NOW = datetime(2026, 11, 7, 21, 30, tzinfo=UTC)
EDMONTON = ZoneInfo("America/Edmonton")
# Free City text in French and English with accents and a dash, about 1,700 characters.
LONG_NOTICE = "Sentier fermé près du pont – détour par la rue 105. " * 33


# ------------------------------------------------------------------ clip_detail


def test_the_limit_is_the_contracts() -> None:
    limits = [
        meta.max_length
        for meta in Obstruction.model_fields["detail"].metadata
        if hasattr(meta, "max_length")
    ]
    assert limits == [DETAIL_MAX_CHARS] == [500]


@pytest.mark.parametrize("text", ["word " * 100, "x" * 500, "é" * 500, "", "Short."])
def test_text_of_500_characters_or_fewer_is_unchanged(text: str) -> None:
    assert clip_detail(text) == text


def test_501_characters_are_shortened_at_the_last_whole_word() -> None:
    text = "word " * 100 + "x"
    assert len(text) == 501
    clipped = clip_detail(text)
    assert clipped == ("word " * 99).rstrip() + ELLIPSIS
    assert len(clipped) == 497


def test_a_cut_that_lands_on_a_space_keeps_the_last_word() -> None:
    text = "a" * 497 + " " + "b" * 10
    assert clip_detail(text) == "a" * 497 + ELLIPSIS
    assert len(clip_detail(text)) == 500


@pytest.mark.parametrize("length", [501, 900, 5000])
def test_text_without_spaces_is_cut_inside_the_word(length: int) -> None:
    assert clip_detail("x" * length) == "x" * 497 + ELLIPSIS


def test_unicode_is_counted_in_characters_not_bytes() -> None:
    assert clip_detail("é" * 501) == "é" * 497 + ELLIPSIS
    words = "日本語のテキスト 🚧 " * 60
    clipped = clip_detail(words)
    assert len(clipped) <= 500 and clipped.endswith(ELLIPSIS)
    kept = clipped.removesuffix(ELLIPSIS)
    assert words.startswith(kept) and words[len(kept)] == " "


def test_an_accent_is_never_cut_from_its_letter() -> None:
    # "e" followed by a combining acute accent: two characters, one letter.
    clipped = clip_detail("é" * 300)
    assert clipped == "é" * 248 + ELLIPSIS


def test_punctuation_before_the_cut_is_dropped() -> None:
    clipped = clip_detail("word, " * 100)
    assert clipped.endswith("word" + ELLIPSIS) and len(clipped) <= 500


def test_keep_stays_whole_after_the_shortened_text() -> None:
    end = " Reported end: Spring 2027."
    clipped = clip_detail("word " * 200, keep=end)
    assert len(clipped) <= 500
    assert clipped.endswith("word" + ELLIPSIS + end)
    # Text that fits alone is still shortened to make room for the ending.
    text = "word " * 97 + "last"
    assert len(text) == 489 and clip_detail(text) == text
    assert clip_detail(text, keep=end).endswith("word" + ELLIPSIS + end)
    assert clip_detail("Short.", keep=end) == "Short." + end


def test_a_long_keep_is_shortened_with_the_text() -> None:
    end = " Reported end: " + "until further notice " * 30
    clipped = clip_detail("Trail closed.", keep=end)
    assert len(clipped) <= 500 and clipped.endswith(ELLIPSIS)
    assert clipped.startswith("Trail closed. Reported end: until further notice")


@pytest.mark.parametrize("limit", range(4, 80))
def test_the_result_never_exceeds_the_limit(limit: int) -> None:
    for text, keep in (
        ("word " * 40, ""),
        ("x" * 200, ""),
        ("é, " * 90, " Ends soon."),
        ("é" * 90, " Reported end: TBD."),
        ("Closed.", " Reported end: " + "later " * 30),
    ):
        assert len(clip_detail(text, limit, keep=keep)) <= limit


def test_a_limit_with_no_room_for_text_is_refused() -> None:
    with pytest.raises(ValueError):
        clip_detail("word " * 10, limit=3)


# ------------------------------------------------- every adapter, every source


def refuse(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"live request attempted: {request.url}")


def test_every_replay_source_fits_the_contract() -> None:
    """Each recorded source yields only items the scheduler accepts."""

    async def scenario() -> dict[str, list[Obstruction]]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(refuse)) as a:
            with httpx.Client(transport=httpx.MockTransport(refuse)) as s:
                built = src.build_sources(
                    mode="replay",
                    snapshot=FIXTURES,
                    app_token=None,
                    async_client=a,
                    sync_client=s,
                    weather_service=None,
                )
                found = {}
                for source in built:
                    result = await source.fetch(NOW)
                    found[source.name] = [
                        Obstruction.model_validate(raw) for raw in result or []
                    ]
                return found

    found = asyncio.run(scenario())
    assert {"city_trail_closures", "city_construction", "city_traffic_disruptions"} <= (
        set(found)
    )
    for items in found.values():
        assert all(len(item.detail or "") <= DETAIL_MAX_CHARS for item in items)
    trails = {item.source_ref for item in found["city_trail_closures"]}
    assert {"3", "484"} <= trails and len(trails) == 46


def _trail_item(rows: list[dict]) -> list[dict]:
    row = copy.deepcopy(next(r for r in rows if r["id_number"] == "1"))
    row["details"] = LONG_NOTICE
    row["end_date"] = "Until further notice " * 30  # free text: shortened too
    return TrailClosuresAdapter(None).normalize([row], NOW)


def _construction_item(rows: list[dict]) -> list[dict]:
    row = copy.deepcopy(rows[0])
    row["work_reason"] = LONG_NOTICE
    row["road_segment_intersections"] = "104 Avenue NW & 109 Street NW; " * 30
    return construction.normalize_permits(
        [row], observed_at=NOW, local_timezone=EDMONTON
    ).obstructions


def _traffic_item(rows: list[dict]) -> list[Obstruction]:
    row = dict(next(r for r in rows if r["disruption_id"] == "1309"))
    row["details"] = LONG_NOTICE
    row["on_street"] = "124 Avenue NW " * 20
    later = datetime(2026, 11, 10, 18, 0, tzinfo=UTC)  # inside 1309's dates
    items, skipped = traffic_disruptions.normalize([row], later)
    assert skipped == []
    return items


@pytest.mark.parametrize(
    ("fixture", "build"),
    [
        ("city_advisories/trails.json", _trail_item),
        ("construction/pedestrian_points.json", _construction_item),
        ("traffic_disruptions/current.json", _traffic_item),
    ],
    ids=["trail_closures", "construction", "traffic_disruptions"],
)
def test_long_city_text_is_shortened_in_every_adapter(fixture: str, build) -> None:
    rows = json.loads((FIXTURES / fixture).read_text(encoding="utf-8"))
    items = build(rows)
    assert len(items) == 1
    item = Obstruction.model_validate(items[0])  # the scheduler would keep it
    assert item.detail is not None
    assert len(item.detail) <= DETAIL_MAX_CHARS and ELLIPSIS in item.detail
    assert item.url  # the full City text stays at the source


def test_construction_keeps_its_dates_when_the_permit_text_is_long() -> None:
    rows = json.loads(
        (FIXTURES / "construction/pedestrian_points.json").read_text(encoding="utf-8")
    )
    (item,) = _construction_item(rows)
    assert item["detail"].startswith(rows[0]["file_number"] + "; Sentier fermé")
    assert item["detail"].endswith("(inclusive)") and "; Active " in item["detail"]


def test_invalid_data_is_still_skipped() -> None:
    """Shortening a detail does not rescue an item that has no usable location."""
    rows = json.loads(
        (FIXTURES / "traffic_disruptions/current.json").read_text(encoding="utf-8")
    )
    row = dict(next(r for r in rows if r["disruption_id"] == "1309"))
    row.update(details=LONG_NOTICE, point=None)
    later = datetime(2026, 11, 10, 18, 0, tzinfo=UTC)
    items, skipped = traffic_disruptions.normalize([row], later)
    assert items == [] and len(skipped) == 1


# ------------------------------------------------------------ the running app


def test_replay_app_publishes_both_long_trail_notices(monkeypatch) -> None:
    """``/api/status`` reports no skipped trail items and the map shows all 46."""
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("SCHEDULER", "off")  # this test runs the one source itself
    monkeypatch.setenv("DATA_MODE", "replay")
    monkeypatch.setenv("REPLAY_SNAPSHOT", str(FIXTURES))
    monkeypatch.setenv("REPLAY_AT", NOW.isoformat())
    monkeypatch.delenv("REPLAY_CLOCK", raising=False)
    (config,) = [c for c in src.load_config() if c.key == "trail_closures"]
    app = create_app()

    async def run_trails() -> Scheduler:
        async with httpx.AsyncClient(transport=httpx.MockTransport(refuse)) as a:
            with httpx.Client(transport=httpx.MockTransport(refuse)) as s:
                built = src.build_sources(
                    mode="replay",
                    snapshot=FIXTURES,
                    app_token=None,
                    async_client=a,
                    sync_client=s,
                    weather_service=None,
                    configs=[config],
                )
                scheduler = Scheduler(app.state.store, built, clock=app.state.clock)
                assert await scheduler.run_once(scheduler.sources[0]) is True
                return scheduler

    with TestClient(app) as client:
        app.state.scheduler = asyncio.run(run_trails())
        status = client.get("/api/status").json()
        features = client.get("/api/obstructions").json()["features"]
    (trail,) = [s for s in status["sources"] if s["source"] == "city_trail_closures"]
    assert trail["skipped_items"] == 0 and trail["item_count"] == 46
    ids = {f["properties"]["id"] for f in features}
    assert len(ids) == 46
    assert {"city_trail_closures:3", "city_trail_closures:484"} <= ids
    assert all(len(f["properties"]["detail"] or "") <= 500 for f in features)
