"""OP-412: are the road cameras reporting, and where ice risk comes from right now.

``GET /api/status`` carries one ``cameras`` block and ``GET /api/cameras`` repeats it
as ``summary``. Both are built here from the camera readings the vision worker
posts (``camera_readings.py``) and from the weather service's cached watch.

The question the block answers
    Ice risk reaches a route in two ways: the black-ice weather watch, which covers
    the whole city (the ``weather_watch`` source), and camera readings, which OP-205
    turns into ``ice_risk`` on the crossings near a camera. When the worker stops
    posting (the laptop is off, 511 is down, the model is missing or Claude is
    unavailable) the camera path goes quiet without any error, so the web app has
    to be told. ``state`` says whether cameras are reporting and ``ice_source``
    says which of the two paths ice risk comes from; the web app turns a state
    other than ``live`` into one sentence (``web/src/lib/camera-status.ts``).

States
    ``live``   at least one usable reading (a label other than ``unknown``) is
               newer than ``LIVE_WITHIN``.
    ``stale``  usable readings exist but every one is older than ``LIVE_WITHIN``.
    ``none``   no usable unexpired reading. ``unknown`` readings (camera
               unavailable, frame unusable, model could not run) count in
               ``readings`` and ``labels`` but never make the cameras ``live``:
               a worker that posts ``unknown`` for every view is not reporting
               road conditions.

Everything is measured at the app's clock (``app.state.clock``), like the map and
route hazards, so a replay with a frozen clock describes the recorded moment.
"""

from __future__ import annotations

import logging
import statistics
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

if TYPE_CHECKING:  # annotations only: camera_readings imports this module
    from .camera_readings import CameraReadingRow, CameraReadingStore
    from .weather_service import WeatherService

log = logging.getLogger("openpath.cameras")

# A usable reading newer than this makes the cameras "live". The worker posts every
# 5 minutes (vision/worker.py DEFAULT_INTERVAL_S) and trusts a reading for three
# intervals, so 15 minutes is one full lifetime at the default interval: a worker
# that has missed three passes in a row has stopped. A chosen limit, not a measurement.
LIVE_WITHIN = timedelta(minutes=15)

# A camera reading with this label says nothing about the road (unusable frame,
# camera unavailable, model could not run). Same word as camera_readings.Condition.
UNKNOWN_LABEL = "unknown"

CameraState = Literal["live", "stale", "none"]
IceSource = Literal["cameras", "weather_watch"]


class CamerasStatus(BaseModel):
    """The ``cameras`` block of ``GET /api/status`` and ``summary`` of ``GET /api/cameras``."""

    state: CameraState
    # "cameras" while the state is live; otherwise ice risk comes from the city-wide
    # weather watch alone (which may itself be off: see watch_active).
    ice_source: IceSource
    readings: int  # unexpired readings, one per camera view, any label
    usable: int  # of those, readings with a label other than "unknown"
    views_total: int  # distinct camera views the table has ever held (purged a week after expiry)
    newest_observed_at: datetime | None  # the newest usable reading's frame time
    newest_age_s: float | None  # seconds since that frame, at the app's clock
    oldest_age_s: float | None  # seconds since the oldest usable reading's frame
    model_versions: list[
        str
    ]  # distinct model_version among unexpired readings (opaque)
    score_min: float | None  # model confidence over usable readings that carry a score
    score_median: float | None
    labels: dict[str, int]  # unexpired readings per label, including "unknown"
    # The weather service's cached watch: True active, False inactive, None unknown
    # (no reading yet, or the scheduler is off).
    watch_active: bool | None
    live_within_s: int  # LIVE_WITHIN, so a client can explain the state in words


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _age_s(observed_at: datetime, now: datetime) -> float:
    # A frame cannot be in the future; clock skew between the worker and the API
    # (or a replay clock behind a live posting) reads as "just now".
    return round(max(0.0, (now - _aware(observed_at)).total_seconds()), 1)


def summarize(
    rows: Iterable[CameraReadingRow],
    *,
    now: datetime,
    views_total: int,
    watch_active: bool | None,
) -> CamerasStatus:
    """Build the block from the unexpired readings (``CameraReadingStore.latest``)."""
    now = now.astimezone(UTC)
    labels: dict[str, int] = {}
    versions: set[str] = set()
    usable: list[CameraReadingRow] = []
    total = 0
    for row in rows:
        total += 1
        labels[row.label] = labels.get(row.label, 0) + 1
        versions.add(row.model_version)
        if row.label != UNKNOWN_LABEL:
            usable.append(row)
    scores = sorted(row.score for row in usable if row.score is not None)
    newest = max((_aware(row.observed_at) for row in usable), default=None)
    oldest = min((_aware(row.observed_at) for row in usable), default=None)
    newest_age_s = None if newest is None else _age_s(newest, now)
    if newest_age_s is not None and newest_age_s < LIVE_WITHIN.total_seconds():
        state: CameraState = "live"
    elif usable:
        state = "stale"
    else:
        state = "none"
    return CamerasStatus(
        state=state,
        ice_source="cameras" if state == "live" else "weather_watch",
        readings=total,
        usable=len(usable),
        views_total=views_total,
        newest_observed_at=newest,
        newest_age_s=newest_age_s,
        oldest_age_s=None if oldest is None else _age_s(oldest, now),
        model_versions=sorted(versions),
        score_min=scores[0] if scores else None,
        score_median=statistics.median(scores) if scores else None,
        labels=dict(sorted(labels.items())),
        watch_active=watch_active,
        live_within_s=int(LIVE_WITHIN.total_seconds()),
    )


def watch_state(weather: WeatherService | None) -> bool | None:
    """The cached watch's ``active`` flag, or None before the first successful refresh."""
    if weather is None or weather.last_good is None:
        return None
    return weather.last_good.active


def camera_status(
    store: CameraReadingStore | None,
    weather: WeatherService | None,
    now: datetime,
    rows: Callable[[datetime], Iterable[CameraReadingRow]] | None = None,
) -> CamerasStatus | None:
    """The block for the app's camera store, or None when it cannot be read.

    ``rows`` lets ``GET /api/cameras`` pass the readings it already loaded. A
    database error is logged and gives None: the status endpoint must keep
    answering for every other source, and the web app treats None as "unknown".
    """
    if store is None:
        return None
    try:
        latest = store.latest(now) if rows is None else rows(now)
        return summarize(
            latest,
            now=now,
            views_total=store.count_views(),
            watch_active=watch_state(weather),
        )
    except Exception:
        log.exception("WARN camera status unavailable; /api/status answers without it")
        return None
