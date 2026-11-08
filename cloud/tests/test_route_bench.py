"""OP-404: the route benchmark script, against a stub API on a local socket.

The script lives in ``scripts/`` (not a package), so it is loaded by path. No test
here touches the network: the stub server listens on 127.0.0.1 only.
"""

from __future__ import annotations

import http.server
import importlib.util
import json
import math
import sys
import threading
from pathlib import Path
from typing import Any, ClassVar

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "route_bench.py"


@pytest.fixture(scope="module")
def bench():
    spec = importlib.util.spec_from_file_location("route_bench", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["route_bench"] = module
    spec.loader.exec_module(module)
    return module


class StubHandler(http.server.BaseHTTPRequestHandler):
    """A tiny API: /health, and /api/route that fails for one profile."""

    hits: ClassVar[list[str]] = []

    def do_GET(self) -> None:
        type(self).hits.append(self.path)
        if self.path == "/health":
            body: dict[str, Any] = {"status": "ok", "version": "abc", "mode": "replay"}
            status = 200
        elif self.path.startswith("/api/route?") and "profile=walker_cane" in self.path:
            body, status = {"detail": "boom"}, 500
        elif self.path.startswith("/api/route?"):
            body, status = {"profile": "x", "routes": [{}, {}], "warnings": []}, 200
        else:
            body, status = {"detail": "not found"}, 404
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args: Any) -> None:  # quiet
        return


@pytest.fixture
def stub_api():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
    StubHandler.hits = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def test_every_trip_resolves_to_places_in_edmonton(bench) -> None:
    trips = bench.load_trips()
    assert len(trips) == 20 and len(bench.TRIPS) == 20
    for trip in trips:
        assert trip.name_from != trip.name_to
        for lat, lng in ((trip.from_lat, trip.from_lng), (trip.to_lat, trip.to_lng)):
            assert 53.3 < lat < 53.8 and -113.8 < lng < -113.2, trip.label
        # Same coordinates would make "the start and end are at the same place".
        assert (trip.from_lat, trip.from_lng) != (trip.to_lat, trip.to_lng), trip.label
    assert bench.load_trips(limit=3) == trips[:3]


def test_a_renamed_place_stops_the_script(bench, tmp_path: Path) -> None:
    places = {"places": [{"name": "Rogers Place", "lat": 53.5, "lng": -113.5}]}
    path = tmp_path / "places.json"
    path.write_text(json.dumps(places), encoding="utf-8")
    with pytest.raises(SystemExit, match="Churchill"):
        bench.load_trips(path)


def test_nearest_rank_percentiles(bench) -> None:
    values = [float(v) for v in range(1, 21)]  # 1..20
    assert bench.percentile(values, 50) == 10.0
    assert bench.percentile(values, 95) == 19.0
    assert bench.percentile(values, 100) == 20.0
    assert bench.percentile([7.0], 95) == 7.0
    assert math.isnan(bench.percentile([], 50))


def test_summary_leaves_errors_out_of_the_percentiles(bench) -> None:
    ok = [
        bench.Sample("standard", "t", 1, 0, ms, 200, routes=3)
        for ms in (10.0, 20.0, 30.0)
    ]
    bad = bench.Sample("standard", "t", 1, 0, 999.0, 500, error="HTTP 500")
    summary = bench.Summary.of("standard", [*ok, bad])
    assert (summary.count, summary.errors) == (4, 1)
    assert (summary.p50_ms, summary.p95_ms, summary.max_ms) == (20.0, 30.0, 30.0)
    table = bench.markdown_table([summary])
    assert table.splitlines()[0].startswith("| Profile | Requests | Errors | p50 ms")
    assert "| standard | 4 | 1 | 20 | 30 | 30 | 10 |" in table


def test_rate_gate_spaces_requests(bench, monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []
    clock = {"now": 100.0}
    monkeypatch.setattr(bench.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(bench.time, "sleep", lambda s: slept.append(s))
    gate = bench.RateGate(4.0)
    gate.wait()
    gate.wait()
    gate.wait()
    assert slept == pytest.approx([0.25, 0.5])
    bench.RateGate(0).wait()  # no limit: never sleeps
    assert len(slept) == 2


def test_run_against_a_stub_api(bench, stub_api, tmp_path: Path, capsys) -> None:
    """Sequential then 4 workers, one failing profile, Markdown and JSON written."""
    out_json = tmp_path / "bench.json"
    out_md = tmp_path / "bench.md"
    code = bench.main(
        [
            "--base-url",
            stub_api,
            "--trips",
            "3",
            "--rate",
            "0",
            "--concurrency",
            "1,4",
            "--client",
            "the test",
            "--json",
            str(out_json),
            "--markdown",
            str(out_md),
        ]
    )
    assert code == 1  # walker_cane answered 500: errors fail the run loudly
    text = capsys.readouterr().out
    assert "/health version `abc`, mode `replay`" in text
    assert "## Concurrency 1" in text and "## Concurrency 4" in text
    assert "HTTP 500" in text
    report = json.loads(out_json.read_text(encoding="utf-8"))
    assert report["health"]["version"] == "abc"
    assert report["health"]["cold_start_suspected"] is False
    assert report["client"]["note"] == "the test"
    assert len(report["trips"]) == 3
    assert len(report["samples"]) == 2 * 3 * 4  # two levels x 3 trips x 4 profiles
    levels = {level["concurrency"]: level["summary"] for level in report["levels"]}
    overall = levels[4][-1]
    assert overall["label"] == "all profiles"
    assert (overall["count"], overall["errors"]) == (12, 3)
    assert all(s["routes"] == 2 for s in report["samples"] if s["status"] == 200)
    assert out_md.read_text(encoding="utf-8") == text.split("Samples written")[0]
    # 1 warm-up + 5 floor calls, then 24 route requests.
    assert sum(1 for h in StubHandler.hits if h == "/health") == 6
    assert sum(1 for h in StubHandler.hits if h.startswith("/api/route")) == 24


def test_an_unreachable_api_fails_before_any_route_request(bench, capsys) -> None:
    code = bench.main(
        ["--base-url", "http://127.0.0.1:9", "--trips", "1", "--timeout", "2"]
    )
    assert code == 1
    assert "/health failed" in capsys.readouterr().err
