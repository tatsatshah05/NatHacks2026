"""Build the scheduled sources for live or replay mode (OP-103).

This is the one place that knows how to construct every adapter. ``main.py`` calls
``build_sources`` in the app lifespan and hands the result to the ``Scheduler``.

Live mode
    Each adapter gets a real HTTP client and the ``SOCRATA_APP_TOKEN``.

Replay mode (``DATA_MODE=replay``)
    Each adapter reads recorded responses from the ``REPLAY_SNAPSHOT`` folder, which
    has one sub-folder per source (``cloud/tests/fixtures`` is the default used by
    ``make dev``). HTTP-based adapters get an ``httpx`` client whose transport serves
    those files, so no adapter code changes between modes and nothing touches the
    network. The web app shows a Replay badge whenever this mode is on.

The replay clock (OP-305)
    ``REPLAY_AT`` freezes the app's clock at one instant. ``REPLAY_CLOCK=running``
    starts the clock at ``REPLAY_AT``, or at the snapshot's ``manifest.json``
    ``replay_at`` (what ``scripts/record_snapshot.py`` writes), and lets it advance
    in real time from app start, so freshness ages and expiries behave as they
    would live. Recorded camera readings (``cameras/readings.json``) are loaded
    into the camera store at startup, re-labelled ``mode: replay``.

Configuration
    ``config/sources.json`` lists the sources by key, with ``enabled`` and optional
    ``cadence_s`` / ``timeout_s`` / ``retries`` overrides. The adapters themselves
    define the default cadence. An adapter that is not merged yet (its module is
    missing) is skipped with a log line, so the API still starts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from pydantic import ValidationError

from .adapters._quality import QualityReport
from .scheduler import Source

log = logging.getLogger("openpath.sources")

EDMONTON = ZoneInfo("America/Edmonton")
CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "sources.json"
# Recorded files that are not Socrata datasets (see scripts/record_snapshot.py).
CONSTRUCTION_FILE = "construction/permits.json"
CAMERAS_FILE = "cameras/readings.json"
WEATHER_ARCHIVE_FILE = "open_meteo_archive.json"  # inside the weather folder
REPLAY_CLOCKS = ("frozen", "running")

# Socrata dataset id -> recorded file inside the replay snapshot folder.
# Serving these through a fake HTTP transport means the live adapters run unchanged.
REPLAY_FILES: dict[str, str] = {
    "snws-u3zx": "lrt/outages.json",
    "javh-pjj2": "lrt/devices.json",
    "fhxi-cnhe": "lrt/stations.json",
    "9i6v-cqzn": "city_advisories/snow.json",
    "k4mi-dkvi": "city_advisories/trails.json",
    "8pdx-hfxi": "snow_clearing/na.json",
    "k4tx-5k8p": "traffic_disruptions/current.json",
}


@dataclass(frozen=True)
class SourceConfig:
    key: str
    enabled: bool = True
    cadence_s: float | None = None
    timeout_s: float = 10.0
    retries: int = 3
    stale_after: int = 3


def load_config(path: Path = CONFIG_PATH) -> list[SourceConfig]:
    """Read ``config/sources.json``. Defaults apply to every source unless overridden."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    defaults = raw.get("defaults", {})
    configs = []
    for key, entry in raw["sources"].items():
        merged = {**defaults, **entry}
        configs.append(
            SourceConfig(
                key=key,
                enabled=bool(merged.get("enabled", True)),
                cadence_s=merged.get("cadence_s"),
                timeout_s=float(merged.get("timeout_s", 10.0)),
                retries=int(merged.get("retries", 3)),
                stale_after=int(merged.get("stale_after_failures", 3)),
            )
        )
    return configs


# ------------------------------------------------------------------ replay plumbing


def replay_transport(snapshot: Path) -> httpx.MockTransport:
    """An httpx transport that answers City API requests from recorded files.

    Only the first page (``$offset=0``) returns rows; later pages are empty, so the
    adapters' pagination loops end exactly as they do against the real API.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        dataset = request.url.path.rsplit("/", 1)[-1].removesuffix(".json")
        name = REPLAY_FILES.get(dataset)
        if name is None:
            return httpx.Response(
                404, json={"error": f"no recorded file for {dataset}"}
            )
        path = snapshot / name
        if not path.exists():
            return httpx.Response(404, json={"error": f"missing {path}"})
        if int(request.url.params.get("$offset", "0") or 0) > 0:
            return httpx.Response(200, json=[])
        return httpx.Response(
            200, content=path.read_bytes(), headers={"content-type": "application/json"}
        )

    return httpx.MockTransport(handler)


class ReplayDatasetReader:
    """``DatasetReader`` for Kaleb's LRT adapter: dataset id -> recorded file."""

    def __init__(self, snapshot: Path) -> None:
        self.snapshot = snapshot

    def read(self, dataset: str) -> list[dict[str, Any]]:
        name = REPLAY_FILES.get(dataset)
        if name is None:
            raise ValueError(f"no recorded file for dataset {dataset}")
        return json.loads((self.snapshot / name).read_text(encoding="utf-8"))


class ReplayPermitReader:
    """``PermitReader`` for the construction adapter: every recorded permit file."""

    def __init__(self, snapshot: Path) -> None:
        self.folder = snapshot / "construction"

    def read(self, today: Any) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for path in sorted(self.folder.glob("*.json")):
            if path.name == "manifest.json":
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                rows.extend(row for row in data if isinstance(row, dict))
        return rows


def parse_replay_time(raw: str | None, name: str = "REPLAY_AT") -> datetime | None:
    """An ISO 8601 instant with a time zone, or None when unset or empty."""
    if not raw:
        return None
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must include a time zone, e.g. 2026-11-10T18:00:00Z")
    return parsed.astimezone(UTC)


def read_manifest(snapshot: Path | None) -> dict[str, Any]:
    """The snapshot's ``manifest.json`` (what the recorder writes), or ``{}``."""
    if snapshot is None:
        return {}
    path = Path(snapshot) / "manifest.json"
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def manifest_time(snapshot: Path | None) -> datetime | None:
    """The clock a recording should start at: ``replay_at``, else ``recorded_at``."""
    manifest = read_manifest(snapshot)
    for key in ("replay_at", "recorded_at"):
        value = manifest.get(key)
        if isinstance(value, str) and value:
            return parse_replay_time(value, f"manifest.json {key}")
    return None


def running_clock(
    start: datetime, *, monotonic: Callable[[], float] | None = None
) -> Callable[[], datetime]:
    """A clock that reads ``start`` now and advances in real time from here.

    It counts on the monotonic clock, so a laptop's clock adjustment during a demo
    cannot make replay time jump or run backwards.
    """
    tick = monotonic or time.monotonic
    origin = tick()
    return lambda: start + timedelta(seconds=tick() - origin)


def replay_clock(snapshot: Path | None) -> Callable[[], datetime]:
    """The app's clock in replay mode (scheduler, map and route hazards).

    ``REPLAY_AT`` (ISO 8601 with a zone) freezes time so date-bound records
    (permits, closures) stay in force during a demo. ``REPLAY_CLOCK=running`` starts
    at ``REPLAY_AT``, or at the snapshot manifest's ``replay_at``, and advances in
    real time from app start. ``REPLAY_CLOCK=frozen`` freezes at the manifest time
    when ``REPLAY_AT`` is unset. Without either, replay uses the real clock
    (``make dev`` with the test fixtures).
    """
    kind = (os.environ.get("REPLAY_CLOCK") or "").strip().lower()
    if kind and kind not in REPLAY_CLOCKS:
        raise ValueError(f"REPLAY_CLOCK must be 'frozen' or 'running', not {kind!r}")
    start = parse_replay_time(os.environ.get("REPLAY_AT"))
    if start is None and kind:
        start = manifest_time(snapshot)
        if start is None:
            raise ValueError(
                f"REPLAY_CLOCK={kind} needs REPLAY_AT or a manifest.json with "
                f"replay_at in the REPLAY_SNAPSHOT folder ({snapshot})"
            )
    if start is None:
        return lambda: datetime.now(UTC)
    if kind == "running":
        log.info("REPLAY clock running from %s", start.isoformat())
        return running_clock(start)
    log.info("REPLAY clock frozen at %s", start.isoformat())
    return lambda: start


def load_replay_cameras(snapshot: Path, camera_store: Any, now: datetime) -> int:
    """Load recorded camera readings (``cameras/readings.json``) into the store.

    The recorder copies ``GET /api/cameras`` of a live API. Each feature becomes
    the reading the worker would have posted, labelled ``mode: replay`` so the map
    and the ice rule never mistake it for a live reading. Times stay as recorded,
    so the readings show only while the replay clock is inside their lifetime.
    Returns how many were stored; a bad feature is skipped with a log line.
    """
    from .camera_readings import CameraReadingIn

    path = Path(snapshot) / CAMERAS_FILE
    if not path.is_file():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        # A bad recording must never stop the demo: serve the rest without cameras.
        log.warning("REPLAY cameras unreadable, none loaded: %s (%s)", path, error)
        return 0
    features = data.get("features", []) if isinstance(data, dict) else []
    stored = skipped = 0
    for feature in features:
        try:
            props = dict(feature["properties"])
            props.pop("age_s", None)
            lng, lat = feature["geometry"]["coordinates"]
            reading = CameraReadingIn(
                **{**props, "latitude": lat, "longitude": lng, "mode": "replay"}
            )
        except (KeyError, TypeError, ValueError, ValidationError) as error:
            skipped += 1
            log.warning("SKIP replay camera reading: %s", str(error).splitlines()[0])
            continue
        camera_store.upsert(reading, now)
        stored += 1
    log.info("REPLAY cameras loaded=%d skipped=%d from %s", stored, skipped, path)
    return stored


# ------------------------------------------------------------------- the weather


def replay_weather_folder(snapshot: Path) -> Path:
    """``<snapshot>/weather``, or a named recording under it chosen by ``WEATHER_REPLAY``.

    ``WEATHER_REPLAY=freeze_thaw`` with the test fixtures serves the recorded
    freeze-thaw night in ``cloud/tests/fixtures/weather/freeze_thaw/``.
    """
    folder = Path(snapshot) / "weather"
    variant = (os.environ.get("WEATHER_REPLAY") or "").strip()
    if not variant:
        return folder
    if not re.fullmatch(r"[a-z0-9_]+", variant):
        raise ValueError(
            f"WEATHER_REPLAY must name a folder under weather/ (letters, digits, "
            f"underscores), not {variant!r}"
        )
    folder = folder / variant
    if not folder.is_dir():
        raise ValueError(f"WEATHER_REPLAY={variant}: {folder} is not a folder")
    return folder


def build_weather_service(
    mode: str,
    snapshot: Path | None,
    client: httpx.AsyncClient,
    clock: Callable[[], datetime] | None = None,
) -> Any:
    """The weather watch (OP-109), live from ECCC and Open-Meteo or from recorded files.

    Replay reads the weather folder of the snapshot. A recorded live pair
    (``eccc.json`` and ``open_meteo.json``) is served at its observation time, as
    before. A recorded archive night (``open_meteo_archive.json``, OP-305) is served
    at the hour the app's ``clock`` points at, or at the recording's documented demo
    hour when the clock is outside the night; see ``weather_replay``.
    """
    from .adapters.weather import WeatherSources, normalize_weather, timestamp
    from .weather_service import WeatherService

    if mode == "replay":
        if snapshot is None:
            raise ValueError("DATA_MODE=replay requires REPLAY_SNAPSHOT")
        folder = replay_weather_folder(snapshot)
        archive_path = folder / WEATHER_ARCHIVE_FILE
        if archive_path.is_file():
            from . import weather_replay

            archive = json.loads(archive_path.read_text(encoding="utf-8"))
            manifest_path = folder / "manifest.json"
            manifest = (
                json.loads(manifest_path.read_text(encoding="utf-8"))
                if manifest_path.is_file()
                else {}
            )
            demo_at = parse_replay_time(
                manifest.get("replay_at"), "weather manifest.json replay_at"
            ) or weather_replay.demo_hour(weather_replay.watch_table(archive))
            log.info(
                "REPLAY weather from archive %s, demo hour %s",
                archive_path,
                demo_at.isoformat(),
            )
            return WeatherService(
                weather_replay.archive_fetch(archive, demo_at),
                mode="replay",
                clock=clock or (lambda: demo_at),
            )
        eccc = json.loads((folder / "eccc.json").read_text(encoding="utf-8"))
        meteo = json.loads((folder / "open_meteo.json").read_text(encoding="utf-8"))
        recorded = max(
            timestamp(f["properties"]["obs_date_tm"]) for f in eccc["features"]
        )

        async def fetch(now: datetime) -> dict[str, Any]:
            return normalize_weather(eccc, meteo, now)

        return WeatherService(fetch, mode="replay", clock=lambda: recorded)
    return WeatherService(
        WeatherSources(
            client,
            latitude=float(os.environ.get("WEATHER_LAT", "53.57")),
            longitude=float(os.environ.get("WEATHER_LNG", "-113.52")),
        )
    )


# ------------------------------------------------------------------- the sources


def _threaded(fetch: Callable[..., Any]) -> Callable[[datetime], Any]:
    """Run a synchronous adapter's fetch in a worker thread so the event loop stays free."""

    async def run(now: datetime) -> Any:
        return await asyncio.to_thread(fetch, now)

    return run


def build_sources(
    *,
    mode: str,
    snapshot: Path | None,
    app_token: str | None,
    async_client: httpx.AsyncClient,
    sync_client: httpx.Client,
    weather_service: Any | None,
    configs: Sequence[SourceConfig] | None = None,
) -> list[Source]:
    """Create every enabled source for the given mode. Missing adapters are skipped."""
    if mode == "replay" and snapshot is None:
        raise ValueError("DATA_MODE=replay requires REPLAY_SNAPSHOT")
    configs = list(configs) if configs is not None else load_config()
    sources: list[Source] = []
    for cfg in configs:
        if not cfg.enabled:
            continue
        builder = _BUILDERS.get(cfg.key)
        if builder is None:
            log.warning(
                "SOURCE %s unknown key in config/sources.json; skipped", cfg.key
            )
            continue
        try:
            built = builder(
                mode, snapshot, app_token, async_client, sync_client, weather_service
            )
        except (
            ImportError
        ) as error:  # the adapter's story has not merged yet; the API still starts
            log.warning("SOURCE %s not available yet (%s); skipped", cfg.key, error)
            continue
        if built is None:
            continue
        name, cadence_s, fetch, url, provides_items, quality = built
        sources.append(
            Source(
                name=name,
                cadence_s=cfg.cadence_s or cadence_s,
                fetch=fetch,
                timeout_s=cfg.timeout_s,
                retries=cfg.retries,
                stale_after=cfg.stale_after,
                url=url,
                provides_items=provides_items,
                quality=quality,
            )
        )
    return sources


# What a builder returns: source name, default cadence, fetch, official URL,
# whether it provides items, and the adapter's OP-407 row-quality reader (None
# for a source with no rows to check).
Built = (
    tuple[
        str,
        float,
        Callable[[datetime], Any],
        str | None,
        bool,
        Callable[[], QualityReport] | None,
    ]
    | None
)
Builder = Callable[
    [str, Path | None, str | None, httpx.AsyncClient, httpx.Client, Any], Built
]


def _lrt(
    mode: str,
    snapshot: Path | None,
    token: str | None,
    _a: httpx.AsyncClient,
    _s: httpx.Client,
    _w: Any,
) -> Built:
    from .adapters import lrt_devices

    reader = (
        lrt_devices.SocrataReader(token)
        if mode == "live"
        else ReplayDatasetReader(snapshot)
    )  # type: ignore[arg-type]
    adapter = lrt_devices.LrtDevicesAdapter(reader, source_timezone=EDMONTON)
    url = f"https://data.edmonton.ca/d/{lrt_devices.OUTAGES}"
    return (
        adapter.source,
        adapter.cadence_s,
        _threaded(adapter.fetch),
        url,
        True,
        adapter.quality.report,
    )


def _city(adapter_name: str) -> Builder:
    """Estella's async City adapters (311 snow complaints, trail closures)."""

    def build(
        mode: str,
        snapshot: Path | None,
        token: str | None,
        async_client: httpx.AsyncClient,
        _s: httpx.Client,
        _w: Any,
    ) -> Built:
        from .adapters import city_advisories

        cls = getattr(city_advisories, adapter_name)
        client = (
            async_client
            if mode == "live"
            else httpx.AsyncClient(transport=replay_transport(snapshot))
        )  # type: ignore[arg-type]
        adapter = cls(client, app_token=token)
        url = f"{city_advisories.CITY_BASE}/d/{adapter.dataset}"
        return (
            adapter.source,
            adapter.poll_interval_seconds,
            adapter.fetch,
            url,
            True,
            adapter.quality.report,
        )

    return build


def _snow_clearing(
    mode: str,
    snapshot: Path | None,
    token: str | None,
    _a: httpx.AsyncClient,
    sync_client: httpx.Client,
    _w: Any,
) -> Built:
    from .adapters import snow_clearing

    client = (
        sync_client
        if mode == "live"
        else httpx.Client(transport=replay_transport(snapshot))
    )  # type: ignore[arg-type]
    adapter = snow_clearing.SnowClearingAdapter(client, app_token=token)
    url = f"https://data.edmonton.ca/d/{snow_clearing.DATASET}"
    return (
        adapter.source,
        adapter.cadence_s,
        _threaded(adapter.fetch),
        url,
        True,
        adapter.quality.report,
    )


def _construction(
    mode: str,
    snapshot: Path | None,
    token: str | None,
    _a: httpx.AsyncClient,
    _s: httpx.Client,
    _w: Any,
) -> Built:
    from .adapters import construction

    reader = (
        construction.ConstructionReader(token)
        if mode == "live"
        else ReplayPermitReader(snapshot)
    )  # type: ignore[arg-type]
    adapter = construction.ConstructionAdapter(reader, local_timezone=EDMONTON)

    def fetch(now: datetime) -> Any:
        result = adapter.fetch(now=now)
        # The adapter returns a small record of items plus warnings; the items are what we store.
        return getattr(result, "obstructions", result)

    return (
        construction.SOURCE,
        construction.POLL_INTERVAL.total_seconds(),
        _threaded(fetch),
        construction.SOURCE_URL,
        True,
        adapter.quality.report,
    )


def _traffic(
    mode: str,
    snapshot: Path | None,
    token: str | None,
    _a: httpx.AsyncClient,
    sync_client: httpx.Client,
    _w: Any,
) -> Built:
    from .adapters import traffic_disruptions as td

    reader = (
        td.SocrataRowReader(sync_client, app_token=token)
        if mode == "live"
        else td.FixtureRowReader(snapshot / REPLAY_FILES[td.DATASET])  # type: ignore[operator]
    )
    adapter = td.TrafficDisruptionsAdapter(reader)
    return (
        adapter.source,
        adapter.cadence_s,
        _threaded(adapter.fetch),
        adapter.url,
        True,
        adapter.quality.report,
    )


def _weather(
    _m: str,
    _p: Path | None,
    _t: str | None,
    _a: httpx.AsyncClient,
    _s: httpx.Client,
    weather_service: Any,
) -> Built:
    """The weather watch keeps its own last-good reading; the scheduler only drives refreshes."""
    if weather_service is None:
        return None
    from .weather_service import REFRESH_SECONDS

    async def fetch(now: datetime) -> None:
        ok = await weather_service.refresh()
        if not ok:
            raise RuntimeError(weather_service.last_error or "weather refresh failed")

    return (
        "weather_watch",
        REFRESH_SECONDS,
        fetch,
        "https://api.weather.gc.ca/",
        False,
        None,  # no rows to check: the weather watch is its own reading
    )


_BUILDERS: dict[str, Builder] = {
    "lrt": _lrt,
    "snow_complaints": _city("SnowComplaintsAdapter"),
    "trail_closures": _city("TrailClosuresAdapter"),
    "snow_clearing": _snow_clearing,
    "construction": _construction,
    "traffic_disruptions": _traffic,
    "weather": _weather,
}

__all__ = [
    "CAMERAS_FILE",
    "CONSTRUCTION_FILE",
    "REPLAY_FILES",
    "WEATHER_ARCHIVE_FILE",
    "SourceConfig",
    "build_sources",
    "build_weather_service",
    "load_config",
    "load_replay_cameras",
    "manifest_time",
    "parse_replay_time",
    "read_manifest",
    "replay_clock",
    "replay_transport",
    "replay_weather_folder",
    "running_clock",
]
