"""Blocking calls in worker threads that shutdown can wait for.

Why this exists
    ``asyncio.to_thread`` hands a call to a thread and forgets it. Cancelling the
    task that awaits it does not stop the thread, so a store write could still be
    running when the lifespan closed the database. With in-memory SQLite (one shared
    connection, ``db.py``) that crashed the test process in CI with a segmentation
    fault. ``WorkerThreads`` runs calls in its own pool and remembers them, so
    ``wait()`` returns only once every call that has started is finished.

Who uses it
    The scheduler (store writes and purges) and the live hub (hazard reads and
    re-routes). ``main.py`` stops both before it disposes the engine.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, TypeVar

log = logging.getLogger("openpath.threads")

T = TypeVar("T")


class WorkerThreads:
    """A small thread pool whose running calls can be waited for.

    Cancelling the task that awaits ``run`` drops a call that has not started yet;
    a call that has started always runs to the end, and ``wait`` waits for it.
    """

    def __init__(self, name: str, *, max_workers: int = 1) -> None:
        self.name = name
        self.max_workers = max_workers
        self._lock = threading.Lock()
        self._pool: ThreadPoolExecutor | None = None
        self._calls: set[Future[Any]] = set()

    async def run(self, fn: Callable[..., T], /, *args: Any) -> T:
        """Run ``fn(*args)`` in a worker thread and return its result."""
        with self._lock:
            if self._pool is None:  # first use, or the first use after wait()
                self._pool = ThreadPoolExecutor(
                    self.max_workers, thread_name_prefix=self.name
                )
            future = self._pool.submit(fn, *args)
            self._calls.add(future)
        future.add_done_callback(self._forget)
        return await asyncio.wrap_future(future)

    def busy(self) -> int:
        """How many calls are queued or running."""
        with self._lock:
            return sum(1 for future in self._calls if not future.done())

    async def wait(self, timeout_s: float) -> bool:
        """Wait up to ``timeout_s`` for every call handed over so far, then close the pool.

        Returns False, and logs, if a call is still running at the deadline. The
        pool is closed either way, so its threads exit when they finish; a later
        ``run`` opens a new one.
        """
        with self._lock:
            pending = [future for future in self._calls if not future.done()]
            pool, self._pool = self._pool, None
        finished = True
        if pending:
            waiters = [asyncio.wrap_future(future) for future in pending]
            _, late = await asyncio.wait(waiters, timeout=timeout_s)
            if late:
                finished = False
                log.warning(
                    "THREADS %s: %d calls still running after %g s",
                    self.name,
                    len(late),
                    timeout_s,
                )
                for waiter in late:  # stop waiting; the call itself keeps running
                    waiter.cancel()
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)
        return finished

    def _forget(self, future: Future[Any]) -> None:
        with self._lock:
            self._calls.discard(future)
