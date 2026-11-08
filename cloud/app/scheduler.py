"""The source scheduler (OP-103): runs every data source on its cadence.

What it does
    For each ``Source`` it runs a loop: call the source's ``fetch`` with a timeout
    and a few retries, and on success replace that source's items in the
    ``ObstructionStore``. On failure it keeps the previous items (a failed source
    never clears data) and records the error. After ``stale_after`` failures in a
    row the source is marked stale, which ``GET /api/status`` shows and the web app
    can warn about. A second loop purges expired items every few seconds.

Who uses it
    ``main.py`` builds the sources (``sources.py``), creates one ``Scheduler`` in the
    app lifespan and keeps it on ``app.state.scheduler``. ``status.py`` reads
    ``statuses()``. Tests call ``run_once`` directly so nothing has to sleep.

Logging
    One line per state change, as AGENTS.md asks:
    ``SOURCE city_lrt_devices ok items=7 skipped=0``, ``SOURCE ... failed (2/3): ...``,
    ``SOURCE ... stale``, ``SOURCE ... recovered``.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ValidationError

from .schemas import Obstruction
from .store import ObstructionStore

log = logging.getLogger("openpath.scheduler")

# What a source may return: ready-made Obstruction objects, plain dict rows in the
# contract shape (what the adapters built before OP-101 produce), or None for a
# source that keeps its own state (the weather watch) and has no items to store.
ItemLike = Obstruction | Mapping[str, Any]
FetchFn = Callable[[datetime], Awaitable[Sequence[ItemLike] | None]]


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class Source:
    """One scheduled data source."""

    name: str  # the Obstruction.source value this source owns, e.g. "city_lrt_devices"
    cadence_s: float  # how often to fetch
    fetch: FetchFn  # async; raises on failure
    timeout_s: float = 10.0  # per attempt
    retries: int = 3  # extra attempts after the first
    stale_after: int = 3  # failures in a row before the source counts as stale
    url: str | None = None  # the official page, for the status screen
    provides_items: bool = (
        True  # False for status-only sources such as the weather watch
    )


class SourceStatus(BaseModel):
    """What ``GET /api/status`` reports for one source."""

    source: str
    cadence_s: float
    url: str | None = None
    last_attempt: datetime | None = None
    last_success: datetime | None = None
    last_error: str | None = None
    consecutive_failures: int = 0
    item_count: int = 0  # items stored after the last successful fetch
    skipped_items: int = 0  # items the last fetch returned that failed validation
    stale: bool = True  # True until the first success, and after stale_after failures
    age_s: float | None = (
        None  # seconds since the last success, at the time of the request
    )


@dataclass
class _State:
    status: SourceStatus
    task: asyncio.Task[None] | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class Scheduler:
    def __init__(
        self,
        store: ObstructionStore,
        sources: Sequence[Source],
        *,
        clock: Callable[[], datetime] = utcnow,
        purge_every_s: float = 5.0,
        backoff_s: Sequence[float] = (1.0, 2.0, 4.0),
    ) -> None:
        names = [source.name for source in sources]
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate source names: {names}")
        self.store = store
        self.sources = list(sources)
        self.clock = clock
        self.purge_every_s = purge_every_s
        self.backoff_s = tuple(backoff_s)
        self._states = {
            source.name: _State(
                SourceStatus(
                    source=source.name, cadence_s=source.cadence_s, url=source.url
                )
            )
            for source in self.sources
        }
        self._purge_task: asyncio.Task[None] | None = None
        self.running = False
        # Store calls run in worker threads. SQLite (local runs, tests) shares one
        # connection, which is not safe to use from two threads at once, so writes
        # and purges take turns. On Postgres this only serialises quick writes.
        self._store_lock = threading.Lock()

    # -- lifecycle ------------------------------------------------------------

    async def start(self) -> None:
        """Start one loop per source plus the purge loop. Safe to call once."""
        if self.running:
            return
        self.running = True
        for source in self.sources:
            self._states[source.name].task = asyncio.create_task(
                self._loop(source), name=f"source:{source.name}"
            )
        self._purge_task = asyncio.create_task(self._purge_loop(), name="purge")
        log.info("SCHEDULER started sources=%d", len(self.sources))

    async def stop(self) -> None:
        """Cancel every loop and wait for them to finish."""
        if not self.running:
            return
        self.running = False
        tasks = [state.task for state in self._states.values() if state.task]
        if self._purge_task:
            tasks.append(self._purge_task)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        log.info("SCHEDULER stopped")

    # -- reporting ------------------------------------------------------------

    def statuses(self) -> list[SourceStatus]:
        """A snapshot of every source, with ``age_s`` computed now."""
        now = self.clock()
        out = []
        for state in self._states.values():
            status = state.status.model_copy()
            if status.last_success is not None:
                status.age_s = round((now - status.last_success).total_seconds(), 1)
            out.append(status)
        return out

    def status(self, name: str) -> SourceStatus:
        return self._states[name].status

    # -- the work --------------------------------------------------------------

    async def run_once(self, source: Source) -> bool:
        """Fetch one source, with retries, and store the result. Returns True on success."""
        state = self._states[source.name]
        async with state.lock:  # never two fetches of the same source at once
            now = self.clock()
            state.status.last_attempt = now
            error: str | None = None
            for attempt in range(source.retries + 1):
                try:
                    result = await asyncio.wait_for(
                        source.fetch(now), timeout=source.timeout_s
                    )
                except TimeoutError:
                    error = f"timed out after {source.timeout_s:g} s"
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - any adapter failure is a source failure
                    error = f"{type(exc).__name__}: {exc}"
                else:
                    try:
                        await self._store_result(source, state, result, now)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001 - a store failure is a source failure
                        self._record_failure(
                            source, state, f"store: {type(exc).__name__}: {exc}"
                        )
                        return False
                    return True
                if attempt < source.retries:
                    delay = self.backoff_s[min(attempt, len(self.backoff_s) - 1)]
                    if delay:
                        await asyncio.sleep(delay)
            self._record_failure(source, state, error or "unknown error")
            return False

    async def _store_result(
        self,
        source: Source,
        state: _State,
        result: Sequence[ItemLike] | None,
        now: datetime,
    ) -> None:
        status = state.status
        skipped = 0
        if source.provides_items:
            items: list[Obstruction] = []
            for raw in result or []:
                try:
                    item = (
                        raw
                        if isinstance(raw, Obstruction)
                        else Obstruction.model_validate(raw)
                    )
                except ValidationError as exc:
                    skipped += 1
                    log.warning(
                        "SKIP %s invalid item: %s",
                        source.name,
                        exc.errors()[0].get("msg", exc),
                    )
                    continue
                if item.source != source.name:
                    skipped += 1
                    log.warning(
                        "SKIP %s item from another source: %s", source.name, item.id
                    )
                    continue
                items.append(item)
            # The store does blocking database work; keep it off the event loop, and
            # let only one thread touch the database at a time.
            await asyncio.to_thread(self._replace, source.name, items, now)
            status.item_count = len(items)
        was_stale, had_failures = status.stale, status.consecutive_failures
        status.last_success = now
        status.last_error = None
        status.consecutive_failures = 0
        status.skipped_items = skipped
        status.stale = False
        if was_stale or had_failures:
            log.info("SOURCE %s recovered", source.name)
        log.info(
            "SOURCE %s ok items=%d skipped=%d", source.name, status.item_count, skipped
        )

    def _record_failure(self, source: Source, state: _State, error: str) -> None:
        status = state.status
        status.last_error = error
        status.consecutive_failures += 1
        log.warning(
            "SOURCE %s failed (%d/%d): %s",
            source.name,
            status.consecutive_failures,
            source.stale_after,
            error,
        )
        if not status.stale and status.consecutive_failures >= source.stale_after:
            status.stale = True
            log.warning("SOURCE %s stale", source.name)

    async def _loop(self, source: Source) -> None:
        while True:
            try:
                await self.run_once(source)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("SOURCE %s loop error", source.name)
            await asyncio.sleep(source.cadence_s)

    def _replace(self, name: str, items: list[Obstruction], now: datetime) -> None:
        with self._store_lock:
            self.store.replace_source(name, items, now)

    def _purge(self, now: datetime) -> list[str]:
        with self._store_lock:
            return self.store.purge_expired(now)

    async def purge_once(self) -> list[str]:
        """Delete expired items. Returns their ids."""
        return await asyncio.to_thread(self._purge, self.clock())

    async def _purge_loop(self) -> None:
        while True:
            try:
                await self.purge_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("PURGE failed")
            await asyncio.sleep(self.purge_every_s)
