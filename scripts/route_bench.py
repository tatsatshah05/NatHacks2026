"""Measure ``GET /api/route`` latency: 20 fixed Edmonton trips, four profiles (OP-404).

Usage (from the repository root; needs only the standard library and no keys)::

    python scripts/route_bench.py                      # the local API on :8000
    python scripts/route_bench.py --base-url https://openpath-api-w6ql.onrender.com \
        --client "Edmonton, home Wi-Fi" --json bench.json

What it does
    1. One ``GET /health`` warms the instance and records its ``version`` and
       ``mode``. The time it took is reported; a free Render instance that was
       asleep answers its first request after a wake-up of about a minute, so a
       warm-up far slower than the later health calls suggests a cold start. The
       script reports the number and the heuristic, never a verdict.
    2. Five more ``/health`` calls give the network and framework floor: what a
       request costs before any routing happens.
    3. For each concurrency level (``--concurrency``, default ``1,4``) it requests
       every trip for every profile ``--repeat`` times, through one persistent
       connection per worker, at most ``--rate`` requests per second in total. The
       live API is on Render's free plan and shared with the demo, so the default
       rate is polite: four requests per second.
    4. It prints p50, p95, max and the error count per profile and overall as a
       Markdown table, and writes every sample to ``--json`` for the record.

Percentiles use the nearest-rank method on the successful samples: p95 of 20
samples is the 19th value in sorted order. A response that is not HTTP 200, or
that times out, counts as an error and is left out of the percentiles.

The trips are real places from ``config/places.json`` (the shared place list),
spread across downtown, the university and Whyte Avenue, the LRT line to the
north, and the river crossings, from about 300 m to about 3 km. They are fixed by
name so every run measures the same work; a renamed place stops the script.
"""

from __future__ import annotations

import argparse
import http.client
import json
import math
import platform
import socket
import statistics
import sys
import threading
import time
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

REPO = Path(__file__).resolve().parents[1]
PLACES_PATH = REPO / "config" / "places.json"

PROFILES = ("wheelchair", "walker_cane", "low_vision", "standard")

# (from, to) by place name. Twenty trips; see the module docstring.
TRIPS: tuple[tuple[str, str], ...] = (
    # Downtown and the Ice District
    ("Rogers Place", "Sir Winston Churchill Square"),
    ("Stanley A. Milner Library", "Royal Alberta Museum"),
    ("Edmonton City Centre", "Fairmont Hotel Macdonald"),
    ("JW Marriott Edmonton Ice District", "Citadel Theatre"),
    ("NorQuest College", "Art Gallery of Alberta"),
    ("MacEwan University", "Alberta Legislature"),
    ("Edmonton Convention Centre", "Muttart Conservatory"),
    # The university and Whyte Avenue
    ("University of Alberta", "Old Strathcona (Whyte Avenue)"),
    ("University of Alberta Hospital", "Garneau Theatre"),
    ("Cross Cancer Institute", "University of Alberta"),
    ("Garneau Theatre", "Old Strathcona (Whyte Avenue)"),
    ("Old Strathcona (Whyte Avenue)", "Kinsmen Sports Centre"),
    # The LRT line to the north
    ("Royal Alexandra Hospital", "Kingsway Mall"),
    ("NAIT Main Campus", "Kingsway Mall"),
    ("Glenrose Rehabilitation Hospital", "Royal Alexandra Hospital"),
    ("Alberta Aviation Museum", "NAIT Main Campus"),
    ("Commonwealth Stadium", "Royal Alberta Museum"),
    # Across the river
    ("Alberta Legislature", "University of Alberta"),
    ("Sir Winston Churchill Square", "Old Strathcona (Whyte Avenue)"),
    ("Muttart Conservatory", "Bonnie Doon Shopping Centre"),
)

HEALTH_FLOOR_CALLS = 5
# A warm-up this much slower than the later health calls is reported as a suspected
# cold start. Render says a wake-up takes about a minute; this is a heuristic, not a
# measurement of the instance's state.
COLD_START_FACTOR = 10.0
COLD_START_MIN_MS = 3000.0


# ------------------------------------------------------------------ the trips


@dataclass(frozen=True)
class Trip:
    name_from: str
    name_to: str
    from_lat: float
    from_lng: float
    to_lat: float
    to_lng: float

    @property
    def label(self) -> str:
        return f"{self.name_from} to {self.name_to}"


def load_trips(places_path: Path = PLACES_PATH, limit: int | None = None) -> list[Trip]:
    """Resolve ``TRIPS`` against ``config/places.json``; a missing name is an error."""
    places = {
        place["name"]: place
        for place in json.loads(places_path.read_text(encoding="utf-8"))["places"]
    }
    trips: list[Trip] = []
    for name_from, name_to in TRIPS[:limit]:
        try:
            a, b = places[name_from], places[name_to]
        except KeyError as error:
            raise SystemExit(
                f"{error.args[0]!r} is not in {places_path}; update TRIPS in {__file__}"
            ) from None
        trips.append(Trip(name_from, name_to, a["lat"], a["lng"], b["lat"], b["lng"]))
    return trips


# ---------------------------------------------------------------- the client


class RateGate:
    """Lets at most ``rate`` requests start per second, across every worker."""

    def __init__(self, rate: float) -> None:
        self.interval = 1.0 / rate if rate > 0 else 0.0
        self._next = time.monotonic()
        self._lock = threading.Lock()

    def wait(self) -> None:
        if not self.interval:
            return
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next)
            self._next = start + self.interval
        delay = start - time.monotonic()
        if delay > 0:
            time.sleep(delay)


@dataclass
class Sample:
    """One request as measured: wall time from sending to the last byte received."""

    profile: str
    trip: str
    concurrency: int
    repeat: int
    ms: float
    status: int | None  # None when the request failed before a response
    routes: int | None = None  # routes in a 200 body
    bytes: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == 200 and self.error is None


class Client:
    """One persistent HTTP connection to the API, as a browser would keep."""

    def __init__(self, base_url: str, timeout_s: float) -> None:
        parts = urlsplit(base_url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise SystemExit(
                f"--base-url must be http(s)://host[:port], not {base_url!r}"
            )
        self.scheme, self.host, self.port = parts.scheme, parts.hostname, parts.port
        self.prefix = parts.path.rstrip("/")
        self.timeout_s = timeout_s
        self._conn: http.client.HTTPConnection | None = None

    def _connect(self) -> http.client.HTTPConnection:
        if self._conn is None:
            cls = (
                http.client.HTTPSConnection
                if self.scheme == "https"
                else http.client.HTTPConnection
            )
            self._conn = cls(self.host, self.port, timeout=self.timeout_s)
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def get(self, path: str) -> tuple[float, int | None, bytes, str | None]:
        """(milliseconds, status or None, body, error or None) for one GET."""
        started = time.perf_counter()
        try:
            conn = self._connect()
            conn.request(
                "GET", self.prefix + path, headers={"Accept": "application/json"}
            )
            response = conn.getresponse()
            body = response.read()
        except (OSError, http.client.HTTPException) as error:
            self.close()  # a broken connection is not reused
            return (
                (time.perf_counter() - started) * 1000,
                None,
                b"",
                f"{type(error).__name__}: {error}",
            )
        return (time.perf_counter() - started) * 1000, response.status, body, None


def route_path(trip: Trip, profile: str) -> str:
    query = urlencode(
        {
            "from": f"{trip.from_lat},{trip.from_lng}",
            "to": f"{trip.to_lat},{trip.to_lng}",
            "profile": profile,
        }
    )
    return f"/api/route?{query}"


# ------------------------------------------------------------------- the runs


def health(client: Client) -> tuple[float, dict[str, Any] | None, str | None]:
    """(milliseconds, parsed body or None, error or None) for one GET /health."""
    ms, status, body, error = client.get("/health")
    if error is not None:
        return ms, None, error
    if status != 200:
        return ms, None, f"HTTP {status}"
    try:
        return ms, json.loads(body), None
    except ValueError:
        return ms, None, "the /health body is not JSON"


def run_level(
    base_url: str,
    trips: Sequence[Trip],
    profiles: Sequence[str],
    *,
    concurrency: int,
    repeat: int,
    gate: RateGate,
    timeout_s: float,
) -> list[Sample]:
    """Every trip for every profile, ``repeat`` times, on ``concurrency`` workers."""
    jobs = [
        (trip, profile, n)
        for n in range(repeat)
        for trip in trips
        for profile in profiles
    ]
    local = threading.local()

    def client() -> Client:
        if not hasattr(local, "client"):
            local.client = Client(base_url, timeout_s)
        return local.client

    def one(job: tuple[Trip, str, int]) -> Sample:
        trip, profile, n = job
        gate.wait()
        ms, status, body, error = client().get(route_path(trip, profile))
        sample = Sample(
            profile, trip.label, concurrency, n, round(ms, 1), status, error=error
        )
        sample.bytes = len(body)
        if status == 200 and error is None:
            try:
                sample.routes = len(json.loads(body)["routes"])
            except (ValueError, KeyError, TypeError):
                sample.error = "the body is not a route response"
        elif error is None:
            sample.error = f"HTTP {status}: {body[:120].decode('utf-8', 'replace')}"
        return sample

    if concurrency <= 1:
        return [one(job) for job in jobs]
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        return list(pool.map(one, jobs))


# ------------------------------------------------------------------ the stats


def percentile(sorted_values: Sequence[float], p: float) -> float:
    """Nearest-rank percentile of already sorted values (p in 0..100)."""
    if not sorted_values:
        return math.nan
    rank = max(1, math.ceil(p / 100.0 * len(sorted_values)))
    return sorted_values[rank - 1]


@dataclass
class Summary:
    label: str
    count: int
    errors: int
    p50_ms: float
    p95_ms: float
    max_ms: float
    min_ms: float
    mean_ms: float

    @classmethod
    def of(cls, label: str, samples: Iterable[Sample]) -> Summary:
        samples = list(samples)
        ok = sorted(s.ms for s in samples if s.ok)
        return cls(
            label,
            len(samples),
            sum(1 for s in samples if not s.ok),
            round(percentile(ok, 50), 1),
            round(percentile(ok, 95), 1),
            round(ok[-1], 1) if ok else math.nan,
            round(ok[0], 1) if ok else math.nan,
            round(statistics.fmean(ok), 1) if ok else math.nan,
        )


def summarise(samples: Sequence[Sample], profiles: Sequence[str]) -> list[Summary]:
    rows = [Summary.of(p, (s for s in samples if s.profile == p)) for p in profiles]
    rows.append(Summary.of("all profiles", samples))
    return rows


def markdown_table(rows: Sequence[Summary]) -> str:
    def cell(value: float) -> str:
        return "n/a" if math.isnan(value) else f"{value:.0f}"

    lines = [
        "| Profile | Requests | Errors | p50 ms | p95 ms | Max ms | Min ms |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for r in rows:
        lines.append(
            f"| {r.label} | {r.count} | {r.errors} | {cell(r.p50_ms)} | {cell(r.p95_ms)} "
            f"| {cell(r.max_ms)} | {cell(r.min_ms)} |"
        )
    return "\n".join(lines)


# ------------------------------------------------------------------- the main


@dataclass
class Report:
    base_url: str
    started_at: str
    finished_at: str = ""
    client: dict[str, Any] = field(default_factory=dict)
    health: dict[str, Any] = field(default_factory=dict)
    settings: dict[str, Any] = field(default_factory=dict)
    trips: list[dict[str, Any]] = field(default_factory=list)
    levels: list[dict[str, Any]] = field(default_factory=list)
    samples: list[dict[str, Any]] = field(default_factory=list)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        help="the API to measure (default: the local API)",
    )
    parser.add_argument(
        "--concurrency",
        default="1,4",
        help="comma-separated worker counts, each run in turn (default: 1,4)",
    )
    parser.add_argument(
        "--repeat", type=int, default=1, help="passes over every trip and profile"
    )
    parser.add_argument(
        "--rate",
        type=float,
        default=4.0,
        help="most requests per second across all workers (default: 4; 0 = no limit)",
    )
    parser.add_argument(
        "--timeout", type=float, default=90.0, help="seconds per request (default: 90)"
    )
    parser.add_argument(
        "--trips", type=int, default=None, help="use only the first N trips (tests)"
    )
    parser.add_argument(
        "--profiles", default=",".join(PROFILES), help="comma-separated profile keys"
    )
    parser.add_argument(
        "--client",
        default="",
        help='where and how the client ran, for the record ("Edmonton, home Wi-Fi")',
    )
    parser.add_argument(
        "--json", type=Path, default=None, help="write every sample here"
    )
    parser.add_argument(
        "--markdown", type=Path, default=None, help="also write the tables here"
    )
    parser.add_argument(
        "--places", type=Path, default=PLACES_PATH, help="an alternative places.json"
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    levels = [int(c) for c in args.concurrency.split(",") if c.strip()]
    profiles = [p for p in args.profiles.split(",") if p.strip()]
    trips = load_trips(args.places, args.trips)
    base_url = args.base_url.rstrip("/")
    report = Report(
        base_url=base_url,
        started_at=datetime.now(UTC).isoformat(timespec="seconds"),
        client={
            "note": args.client,
            "host": socket.gethostname(),
            "platform": platform.platform(),
            "python": platform.python_version(),
        },
        settings={
            "concurrency": levels,
            "repeat": args.repeat,
            "rate_per_s": args.rate,
            "timeout_s": args.timeout,
            "profiles": profiles,
            "percentile_method": "nearest rank on successful samples",
        },
        trips=[asdict(t) for t in trips],
    )
    out: list[str] = []
    say = out.append

    # 1. Warm up and identify the instance.
    probe = Client(base_url, args.timeout)
    warm_ms, body, error = health(probe)
    if error is not None:
        print(
            f"GET {base_url}/health failed after {warm_ms:.0f} ms: {error}",
            file=sys.stderr,
        )
        return 1
    floor = []
    for _ in range(HEALTH_FLOOR_CALLS):
        ms, _, floor_error = health(probe)
        if floor_error is None:
            floor.append(ms)
    probe.close()
    floor_median = statistics.median(floor) if floor else math.nan
    cold = warm_ms >= COLD_START_MIN_MS and (
        not floor or warm_ms >= COLD_START_FACTOR * floor_median
    )
    report.health = {
        "version": body.get("version") if body else None,
        "mode": body.get("mode") if body else None,
        "warmup_ms": round(warm_ms, 1),
        "floor_ms": [round(ms, 1) for ms in floor],
        "floor_median_ms": round(floor_median, 1) if floor else None,
        "cold_start_suspected": cold,
    }
    say(f"# Route latency: {base_url}")
    say("")
    say(f"- Started {report.started_at}")
    say(
        f"- /health version `{report.health['version']}`, mode `{report.health['mode']}`"
    )
    say(
        f"- Warm-up /health took {warm_ms:.0f} ms; the next {len(floor)} took "
        f"{', '.join(f'{ms:.0f}' for ms in floor)} ms (median {floor_median:.0f} ms)"
    )
    say(
        "- Cold start suspected: "
        + (
            f"yes (warm-up at least {COLD_START_FACTOR:g} times the later calls and over "
            f"{COLD_START_MIN_MS:.0f} ms; a heuristic)"
            if cold
            else "no (by the heuristic above)"
        )
    )
    say(
        f"- Client: {args.client or 'not stated'}; {report.client['host']}, "
        f"{report.client['platform']}, Python {report.client['python']}"
    )
    say(
        f"- {len(trips)} trips x {len(profiles)} profiles x {args.repeat} pass(es) per level; "
        f"at most {args.rate:g} requests/s; one keep-alive connection per worker"
    )

    # 2. The runs.
    gate = RateGate(args.rate)
    total_errors = 0
    for concurrency in levels:
        samples = run_level(
            base_url,
            trips,
            profiles,
            concurrency=concurrency,
            repeat=args.repeat,
            gate=gate,
            timeout_s=args.timeout,
        )
        rows = summarise(samples, profiles)
        errors = rows[-1].errors
        total_errors += errors
        report.levels.append(
            {"concurrency": concurrency, "summary": [asdict(r) for r in rows]}
        )
        report.samples.extend(asdict(s) for s in samples)
        say("")
        say(f"## Concurrency {concurrency}")
        say("")
        say(markdown_table(rows))
        failures = [s for s in samples if not s.ok]
        if failures:
            say("")
            say("Errors:")
            for s in failures[:10]:
                say(f"- {s.profile}, {s.trip}: {s.error}")
            if len(failures) > 10:
                say(f"- and {len(failures) - 10} more")

    report.finished_at = datetime.now(UTC).isoformat(timespec="seconds")
    say("")
    say(f"Finished {report.finished_at}. Errors: {total_errors}.")
    text = "\n".join(out) + "\n"
    print(text, end="")
    if args.markdown:
        args.markdown.write_text(text, encoding="utf-8")
    if args.json:
        args.json.write_text(
            json.dumps(asdict(report), indent=2) + "\n", encoding="utf-8"
        )
        print(f"Samples written to {args.json}")
    return 1 if total_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
