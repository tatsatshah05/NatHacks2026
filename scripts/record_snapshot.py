"""Record a replay snapshot of every data source (OP-305). ``make record`` runs this.

    python scripts/record_snapshot.py                       # snapshots/<UTC time>/
    python scripts/record_snapshot.py --out snapshots/demo --cameras-from http://localhost:8000
    python scripts/record_snapshot.py --weather-archive 2025-11-05 --sources weather

The snapshot folder has the layout ``cloud/app/sources.py`` reads in replay mode
(one sub-folder per source, as in ``cloud/tests/fixtures/``), so it is a drop-in
``REPLAY_SNAPSHOT``. ``manifest.json`` records when it was taken, every file with
its URL, row count and SHA-256, the clock a replay should start at and any source
that failed. Each source is fetched once, through the same official API queries
the live adapters send; a failed source is reported and the others still record.

Only JSON is stored: City open data rows, weather responses and the camera
*readings* a live API serves on ``GET /api/cameras``. No image is ever requested
or written. The Socrata token (``--token`` or ``SOCRATA_APP_TOKEN``) travels in a
header and is never written anywhere.

``--weather-archive YYYY-MM-DD`` records a real past night from Open-Meteo's
historical archive instead of today's weather: the local date the night begins
on, so the response covers that day and the next in UTC. The recorder runs the
black-ice rule over every hour and refuses a night on which the watch never fires.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))  # `from cloud.app import ...` when run as a script

from cloud.app import sources as src
from cloud.app import weather_replay

log = logging.getLogger("openpath.record")

SOCRATA_HOST = "data.edmonton.ca"
# Non-Socrata JSON responses the recorder stores, by host.
WEATHER_FILES = {
    "api.weather.gc.ca": "weather/eccc.json",
    "api.open-meteo.com": "weather/open_meteo.json",
}
TIMEOUT_S = 30
USER_AGENT = "OpenPath-OP305-recorder"


class RecordingError(RuntimeError):
    """A response the recorder refuses to store."""


def iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def public_url(request: httpx.Request) -> str:
    """The request URL without credentials (none of ours travel in the URL)."""
    return str(request.url.copy_with(username=None, password=None))


def require_json(response: httpx.Response) -> None:
    """Refuse a body that is not JSON, so an image can never be stored."""
    content_type = response.headers.get("content-type", "")
    if "json" not in content_type.lower():
        raise RecordingError(
            f"{response.request.url.host} answered "
            f"{content_type or 'with no content type'}; only JSON is recorded"
        )


@dataclass
class Recorder:
    """Collects JSON responses and writes them in the replay layout.

    ``transport`` replaces the network in tests (an ``httpx.MockTransport`` serves
    both the sync and the async clients).
    """

    out: Path
    transport: Any = None
    files: dict[str, dict[str, Any]] = field(default_factory=dict)
    _pages: dict[str, list[tuple[int, list[Any], str]]] = field(default_factory=dict)
    _raw: dict[str, tuple[bytes, str]] = field(default_factory=dict)
    # Set by the weather archive step: the hour of the night a replay should show.
    weather_demo_at: datetime | None = None

    # -- capturing ---------------------------------------------------------------

    def capture(self, request: httpx.Request, response: httpx.Response) -> None:
        """Remember one successful JSON response from a source the replay reads."""
        if response.status_code != 200:
            return  # the adapter raises on it (or retries); nothing to keep
        require_json(response)
        body = response.content
        host = request.url.host
        if host == SOCRATA_HOST:
            dataset = request.url.path.rsplit("/", 1)[-1].removesuffix(".json")
            offset = int(request.url.params.get("$offset", "0") or 0)
            rows = json.loads(body)
            if not isinstance(rows, list):
                raise RecordingError(f"{dataset}: expected a JSON array of rows")
            self._pages.setdefault(dataset, []).append(
                (offset, rows, public_url(request))
            )
        elif host in WEATHER_FILES:
            self._raw[WEATHER_FILES[host]] = (body, public_url(request))
        else:
            raise RecordingError(f"unexpected host {host}; nothing recorded")

    def sync_hook(self, response: httpx.Response) -> None:
        response.read()
        self.capture(response.request, response)

    async def async_hook(self, response: httpx.Response) -> None:
        await response.aread()
        self.capture(response.request, response)

    def sync_client(self) -> httpx.Client:
        """A client whose JSON responses are captured for the snapshot."""
        return httpx.Client(
            timeout=TIMEOUT_S,
            headers={"User-Agent": USER_AGENT},
            event_hooks={"response": [self.sync_hook]},
            transport=self.transport,
        )

    def async_client(self, *, capture: bool = True) -> httpx.AsyncClient:
        """An async client; ``capture=False`` for requests a step stores itself."""
        return httpx.AsyncClient(
            timeout=TIMEOUT_S,
            headers={"User-Agent": USER_AGENT},
            event_hooks={"response": [self.async_hook] if capture else []},
            transport=self.transport,
        )

    # -- writing -----------------------------------------------------------------

    def write_json(
        self, name: str, data: Any, *, url: str | None, rows: int | None
    ) -> dict[str, Any]:
        """Write one recorded file and remember its manifest entry."""
        path = self.out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(data, ensure_ascii=False, indent=1) + "\n"
        path.write_text(text, encoding="utf-8")
        entry = {
            "url": url,
            "rows": rows,
            "bytes": len(text.encode("utf-8")),
            "sha256": sha256(text.encode("utf-8")),
        }
        self.files[name] = entry
        return entry

    def write_dataset(self, dataset: str, name: str) -> dict[str, Any]:
        """Concatenate the captured pages of one dataset into its recorded file."""
        pages = self._pages.pop(dataset, None)
        if not pages:
            raise RecordingError(f"{dataset}: no page was captured")
        pages.sort(key=lambda page: page[0])
        rows = [row for _, page, _ in pages for row in page]
        return self.write_json(name, rows, url=pages[0][2], rows=len(rows))

    def write_raw(self, name: str) -> dict[str, Any]:
        body, url = self._raw.pop(name)
        data = json.loads(body)
        return self.write_json(name, data, url=url, rows=None)


# ------------------------------------------------------------------- the sources


@dataclass(frozen=True)
class Options:
    now: datetime
    token: str | None
    latitude: float
    longitude: float
    cameras_from: str | None
    weather_archive: date | None


Step = Callable[[Recorder, Options], Awaitable[dict[str, Any]]]


async def record_lrt(rec: Recorder, opts: Options) -> dict[str, Any]:
    """The three LRT datasets, read as the adapter reads them (every page)."""
    from cloud.app.adapters import lrt_devices

    reader = lrt_devices.SocrataReader(opts.token, timeout_s=TIMEOUT_S)
    out: dict[str, Any] = {}
    for dataset in (lrt_devices.OUTAGES, lrt_devices.DEVICES, lrt_devices.STATIONS):
        rows = await asyncio.to_thread(reader.read, dataset)
        name = src.REPLAY_FILES[dataset]
        out[name] = rec.write_json(
            name,
            rows,
            url=f"https://{SOCRATA_HOST}/resource/{dataset}.json",
            rows=len(rows),
        )
    return out


def city_step(adapter_name: str) -> Step:
    """One of the async City adapters (311 snow complaints, trail closures)."""

    async def step(rec: Recorder, opts: Options) -> dict[str, Any]:
        from cloud.app.adapters import city_advisories

        cls = getattr(city_advisories, adapter_name)
        async with rec.async_client() as client:
            adapter = cls(client, app_token=opts.token)
            await adapter.fetch(opts.now)
        name = src.REPLAY_FILES[adapter.dataset]
        return {name: rec.write_dataset(adapter.dataset, name)}

    return step


async def record_snow_clearing(rec: Recorder, opts: Options) -> dict[str, Any]:
    from cloud.app.adapters import snow_clearing

    with rec.sync_client() as client:
        adapter = snow_clearing.SnowClearingAdapter(client, app_token=opts.token)
        await asyncio.to_thread(adapter.fetch, opts.now)
    name = src.REPLAY_FILES[snow_clearing.DATASET]
    return {name: rec.write_dataset(snow_clearing.DATASET, name)}


async def record_traffic(rec: Recorder, opts: Options) -> dict[str, Any]:
    from cloud.app.adapters import traffic_disruptions as td

    with rec.sync_client() as client:
        adapter = td.TrafficDisruptionsAdapter(
            td.SocrataRowReader(client, app_token=opts.token)
        )
        await asyncio.to_thread(adapter.fetch, opts.now)
    name = src.REPLAY_FILES[td.DATASET]
    return {name: rec.write_dataset(td.DATASET, name)}


async def record_construction(rec: Recorder, opts: Options) -> dict[str, Any]:
    """Active pedestrian-affecting permits for today in Edmonton, every page."""
    from cloud.app.adapters import construction

    reader = construction.ConstructionReader(opts.token, timeout_s=TIMEOUT_S)
    today = opts.now.astimezone(src.EDMONTON).date()
    rows = await asyncio.to_thread(reader.read, today)
    name = src.CONSTRUCTION_FILE
    return {
        name: rec.write_json(
            name,
            rows,
            url=f"https://{SOCRATA_HOST}/resource/{construction.DATASET}.json",
            rows=len(rows),
        )
    }


async def record_weather(rec: Recorder, opts: Options) -> dict[str, Any]:
    """Today's weather (ECCC and Open-Meteo), or a past night from the archive."""
    if opts.weather_archive is not None:
        return await record_weather_archive(rec, opts)
    from cloud.app.adapters.weather import WeatherSources

    async with rec.async_client() as client:
        await WeatherSources(client, opts.latitude, opts.longitude)(opts.now)
    return {name: rec.write_raw(name) for name in WEATHER_FILES.values()}


async def record_weather_archive(rec: Recorder, opts: Options) -> dict[str, Any]:
    """One archive response for the night that begins on ``--weather-archive``."""
    assert opts.weather_archive is not None
    start = opts.weather_archive
    params = weather_replay.archive_params(
        opts.latitude, opts.longitude, start, start + timedelta(days=1)
    )
    async with rec.async_client(capture=False) as client:
        response = await client.get(weather_replay.ARCHIVE_URL, params=params)
        response.raise_for_status()
        require_json(response)
        archive = response.json()
        url = public_url(response.request)
    table = weather_replay.watch_table(archive)
    demo_at = weather_replay.demo_hour(table)  # refuses a night without a watch
    active = [hour for hour in table if hour.active]
    name = f"weather/{src.WEATHER_ARCHIVE_FILE}"
    entry = rec.write_json(name, archive, url=url, rows=len(archive["hourly"]["time"]))
    demo = next(hour for hour in table if hour.at == demo_at)
    rec.weather_demo_at = demo_at
    rec.write_json(
        "weather/manifest.json",
        {
            "source": "Open-Meteo historical weather archive (ERA5 reanalysis)",
            "url": url,
            "file": src.WEATHER_ARCHIVE_FILE,
            "sha256": entry["sha256"],
            "attribution": (
                "Weather data by Open-Meteo.com (CC BY 4.0); contains modified "
                "Copernicus Climate Change Service information (ERA5)"
            ),
            "night_begins_local": start.isoformat(),
            "recorded_at": iso(opts.now),
            "replay_at": iso(demo_at),
            "ground_field": weather_replay.ground_field(archive),
            "ground_note": (
                "The archive gives a 0 to 7 cm soil layer average, used as ground_c; "
                "the live service uses the forecast API's 0 cm surface value."
            ),
            "watch_active_hours": [iso(hour.at) for hour in active],
            "demo_hour": {
                "at": iso(demo.at),
                "air_c": demo.air_c,
                "dew_c": demo.dew_c,
                "ground_c": demo.ground_c,
                "precipitation_last_6h_mm": demo.precipitation_last_6h_mm,
                "max_air_last_12h_c": demo.max_air_last_12h_c,
                "reason": demo.reason,
            },
        },
        url=None,
        rows=None,
    )
    print(
        f"  weather archive: {len(table)} hours checked, watch active for "
        f"{len(active)}; demo hour {iso(demo_at)} ({demo.reason})"
    )
    return {name: entry}


async def record_cameras(rec: Recorder, opts: Options) -> dict[str, Any]:
    """Copy the camera *readings* (labels and times, never frames) of a live API."""
    assert opts.cameras_from is not None
    base = opts.cameras_from.rstrip("/")
    async with rec.async_client(capture=False) as client:
        response = await client.get(f"{base}/api/cameras")
        response.raise_for_status()
        require_json(response)
        data = response.json()
    features = data.get("features") if isinstance(data, dict) else None
    if data.get("type") != "FeatureCollection" or not isinstance(features, list):
        raise RecordingError("/api/cameras did not return a GeoJSON FeatureCollection")
    # Readings only: labels, scores and times. A reading never carries a frame, but
    # refuse anything that looks like embedded image data rather than trust that.
    if "data:image" in response.text:
        raise RecordingError("/api/cameras returned embedded image data; refused")
    name = src.CAMERAS_FILE
    return {
        name: rec.write_json(name, data, url=f"{base}/api/cameras", rows=len(features))
    }


STEPS: dict[str, Step] = {
    "lrt": record_lrt,
    "snow_complaints": city_step("SnowComplaintsAdapter"),
    "trail_closures": city_step("TrailClosuresAdapter"),
    "snow_clearing": record_snow_clearing,
    "construction": record_construction,
    "traffic_disruptions": record_traffic,
    "weather": record_weather,
}


# ---------------------------------------------------------------- replay check


async def replay_check(out: Path, now: datetime, keys: set[str]) -> dict[str, Any]:
    """Build the recorded sources from the new snapshot, offline, and count items.

    Only the ``config/sources.json`` keys in ``keys`` (the ones that recorded) are
    checked; the network is refused, exactly as in replay mode.
    """

    def refuse(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"replay check tried the network: {request.url}")

    results: dict[str, Any] = {}
    async with httpx.AsyncClient(transport=httpx.MockTransport(refuse)) as a:
        with httpx.Client(transport=httpx.MockTransport(refuse)) as s:
            weather = None
            if "weather" in keys:
                try:
                    weather = src.build_weather_service("replay", out, a)
                except Exception as error:  # noqa: BLE001 - reported, not fatal
                    results["weather_watch"] = f"error: {error}"
            built = src.build_sources(
                mode="replay",
                snapshot=out,
                app_token=None,
                async_client=a,
                sync_client=s,
                weather_service=weather,
                configs=[cfg for cfg in src.load_config() if cfg.key in keys],
            )
            for source in built:
                try:
                    items = await source.fetch(now)
                except Exception as error:  # noqa: BLE001 - reported, not fatal
                    message = f"{type(error).__name__}: {error}".splitlines()[0]
                    results[source.name] = f"error: {message}"
                    continue
                results[source.name] = None if items is None else len(list(items))
            if weather is not None and weather.last_good is not None:
                results["weather_watch"] = weather.last_good.model_dump(mode="json")
    return results


# ------------------------------------------------------------------------ main


def default_out() -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return ROOT / "snapshots" / stamp


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="snapshot folder (default snapshots/<UTC time>/)",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="Socrata app token (default: SOCRATA_APP_TOKEN from the environment)",
    )
    parser.add_argument(
        "--cameras-from",
        metavar="API_URL",
        help="copy GET /api/cameras readings from this live API (JSON only)",
    )
    parser.add_argument(
        "--weather-archive",
        metavar="YYYY-MM-DD",
        type=date.fromisoformat,
        help="record a past night from Open-Meteo's archive instead of today's weather",
    )
    parser.add_argument(
        "--sources",
        help="comma-separated keys from config/sources.json to record (default: all enabled)",
    )
    parser.add_argument(
        "--force", action="store_true", help="write into a folder that is not empty"
    )
    parser.add_argument(
        "--no-check", action="store_true", help="skip the offline replay check"
    )
    return parser.parse_args(argv)


async def run(
    args: argparse.Namespace,
    *,
    now: datetime | None = None,
    transport: Any = None,
) -> int:
    """Record every chosen source; 0 when all succeeded, 1 when any failed.

    Tests pass a fixed ``now`` and a mock ``transport`` (no network).
    """
    now = (now or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    out = args.out or default_out()
    if out.exists() and any(out.iterdir()) and not args.force:
        print(f"{out} is not empty; choose --out or pass --force", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    token = args.token or os.environ.get("SOCRATA_APP_TOKEN") or None
    opts = Options(
        now=now,
        token=token,
        latitude=float(os.environ.get("WEATHER_LAT", "53.57")),
        longitude=float(os.environ.get("WEATHER_LNG", "-113.52")),
        cameras_from=args.cameras_from,
        weather_archive=args.weather_archive,
    )
    wanted = (
        {key.strip() for key in args.sources.split(",") if key.strip()}
        if args.sources
        else None
    )
    keys = [cfg.key for cfg in src.load_config() if cfg.enabled]
    if wanted is not None:
        unknown = wanted - set(STEPS)
        if unknown:
            print(f"unknown sources: {', '.join(sorted(unknown))}", file=sys.stderr)
            return 2
        keys = [key for key in keys if key in wanted]
    steps = [(key, STEPS[key]) for key in keys if key in STEPS]
    if args.cameras_from:
        steps.append(("cameras", record_cameras))

    print(f"Recording {len(steps)} sources into {out} at {iso(now)}")
    rec = Recorder(out, transport=transport)
    report: dict[str, Any] = {}
    failed = 0
    for key, step in steps:
        print(f"- {key} ...", end=" ", flush=True)
        try:
            files = await step(rec, opts)
        except Exception as error:  # noqa: BLE001 - one source must not stop the others
            failed += 1
            message = f"{type(error).__name__}: {error}".splitlines()[0]
            report[key] = {"status": "failed", "error": message}
            print(f"FAILED: {message}")
            continue
        report[key] = {"status": "ok", "files": files}
        print(
            ", ".join(
                f"{name} ({entry['rows']} rows)" if entry["rows"] is not None else name
                for name, entry in files.items()
            )
        )

    recorded = {key for key, entry in report.items() if entry["status"] == "ok"}
    # The clock a replay should start at (REPLAY_CLOCK=running or frozen): the
    # recording time, or the archive night's demo hour when only weather recorded.
    replay_at = now
    if rec.weather_demo_at is not None and recorded <= {"weather"}:
        replay_at = rec.weather_demo_at
    manifest: dict[str, Any] = {
        "recorder": "scripts/record_snapshot.py (OP-305)",
        "recorded_at": iso(now),
        "replay_at": iso(replay_at),
        "socrata_token_used": token is not None,
        "no_images": True,
        "sources": report,
        "files": rec.files,
    }
    if not args.no_check:
        try:
            manifest["replay_check"] = await replay_check(out, replay_at, recorded)
        except Exception as error:  # noqa: BLE001 - the recording is still written
            manifest["replay_check"] = f"error: {type(error).__name__}: {error}"
        print(f"Replay check: {json.dumps(manifest['replay_check'], default=str)}")
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    total = sum(entry["bytes"] for entry in rec.files.values())
    print(
        f"Wrote {len(rec.files)} files ({total / 1024:.0f} KB) and manifest.json; "
        f"{failed} source(s) failed. Replay with:\n"
        f"  DATA_MODE=replay REPLAY_SNAPSHOT={out} REPLAY_CLOCK=running"
    )
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    return asyncio.run(run(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
