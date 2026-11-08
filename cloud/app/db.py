"""Database engine: Postgres on Render, SQLite locally and in tests.

The rest of the app never cares which database it is talking to. It calls
``make_engine()`` once at startup and hands the engine to ``ObstructionStore``.

- On Render, ``DATABASE_URL`` is set by the platform and points at Postgres.
- Locally, with no ``DATABASE_URL``, a SQLite file ``openpath.db`` is created in
  the current folder, so ``make dev`` needs no database server.
- In tests, ``make_engine("sqlite://")`` gives a fresh in-memory database per test.

An in-memory database is ONE pysqlite connection shared by every thread: request
threads, the scheduler, the push watcher and the hazard rebuilds. pysqlite breaks
when two threads use one connection at the same time (``InterfaceError: bad
parameter or other API misuse``, wrong rows, or a segmentation fault), so
``SharedMemoryPool`` lends that connection to one thread at a time. File SQLite and
Postgres give each thread its own connection and are unchanged.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, cast

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.pool import ConnectionPoolEntry, PoolResetState, StaticPool
from sqlmodel import create_engine

log = logging.getLogger("openpath.db")

# Used when DATABASE_URL is not set: a SQLite file next to where the app runs.
DEFAULT_DATABASE_URL = "sqlite:///./openpath.db"

# How long a thread waits for the shared in-memory connection. A store call takes
# milliseconds, so a 30 s wait means a deadlock: fail with a message, never hang CI.
MEMORY_LOCK_TIMEOUT_S = 30.0


def normalize_url(url: str) -> str:
    """Convert Render's Postgres URL into the form SQLAlchemy expects.

    Render (and Heroku-style platforms) provide ``postgres://user:pass@host/db``.
    SQLAlchemy dropped support for the bare ``postgres://`` scheme and needs the
    driver named explicitly, so we rewrite it to ``postgresql+psycopg://`` (psycopg 3).
    SQLite URLs pass through unchanged.
    """
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


def make_engine(url: str | None = None) -> Engine:
    """Create the SQLAlchemy engine from ``url``, else ``DATABASE_URL``, else SQLite."""
    url = normalize_url(url or os.environ.get("DATABASE_URL") or DEFAULT_DATABASE_URL)
    if url in ("sqlite://", "sqlite:///:memory:"):
        return _memory_engine(url)
    if url.startswith("sqlite"):
        # FastAPI handles requests on several threads; SQLite connections refuse
        # to be used from a thread other than the one that opened them unless
        # this is turned off. File databases use the normal pool.
        return create_engine(url, connect_args={"check_same_thread": False})
    # Postgres: pool_pre_ping checks a pooled connection is still alive before using
    # it, so a connection Render dropped overnight gives a reconnect, not an error.
    return create_engine(url, pool_pre_ping=True)


def _memory_engine(url: str) -> Engine:
    """An in-memory database: one shared connection, used by one thread at a time."""
    engine = create_engine(
        url,
        # The one connection is used from many threads, one at a time.
        connect_args={"check_same_thread": False},
        # An in-memory SQLite database lives only as long as its connection, so the
        # pool keeps one connection open and shares it, like StaticPool.
        poolclass=SharedMemoryPool,
        # SQLAlchemy's own rollback on return would run after the reset listener
        # has released the lock, while another thread may hold the connection. The
        # listener does the same rollback first, then releases.
        pool_reset_on_return=None,
    )
    lock = cast(SharedMemoryPool, engine.pool).lock
    event.listen(engine, "reset", lock.after_reset)
    return engine


class SharedConnectionTimeout(PoolTimeoutError):
    """A thread waited too long for the shared in-memory SQLite connection."""


class SharedConnectionLock:
    """Lets one thread at a time use the shared in-memory connection.

    Re-entrant per thread: a thread that already holds it (a Session opened inside
    another Session) goes one level deeper instead of waiting for itself. The depth
    is counted by hand, keyed by ``threading.get_ident()``, so a release from a
    thread that does not hold the lock (a connection returned by garbage collection
    on another thread) is logged and ignored instead of corrupting the count.
    """

    def __init__(self, timeout_s: float) -> None:
        self.timeout_s = timeout_s
        self._lock = threading.Lock()
        self._owner: int | None = None  # threading.get_ident() of the holder
        self._owner_name = ""  # the holder's thread name, for the timeout message
        self._depth = 0
        # Releases that wait until SQLAlchemy has checked the connection back in
        # (see after_reset). Per thread: only the holder sets and reads its own.
        self._deferred = threading.local()

    @property
    def owner(self) -> int | None:
        """The ``threading.get_ident()`` of the holder, or None when free."""
        return self._owner

    @property
    def depth(self) -> int:
        """How many connections the holder has open (0 when free)."""
        return self._depth

    def held_here(self) -> bool:
        """True when the calling thread holds the lock."""
        return self._owner == threading.get_ident()

    def acquire(self) -> None:
        """Take the lock, or go one level deeper if this thread already holds it."""
        if self.held_here():
            self._depth += 1
            return
        if not self._lock.acquire(timeout=self.timeout_s):
            raise SharedConnectionTimeout(
                f"Waited {self.timeout_s:g} s for the in-memory SQLite connection, "
                f"which thread {self._owner_name!r} still holds: a database call is "
                "stuck, a connection was never closed, or two threads are waiting "
                "for each other (see cloud/app/db.py)"
            )
        self._owner = threading.get_ident()
        self._owner_name = threading.current_thread().name
        self._depth = 1

    def release(self) -> None:
        """Go one level up; free the lock at the top. Ignored from another thread."""
        if not self.held_here():
            log.debug(
                "DB in-memory connection released by thread %r, which does not "
                "hold it; ignored",
                threading.current_thread().name,
            )
            return
        self._depth -= 1
        if self._depth == 0:
            self._owner = None
            self._owner_name = ""
            self._lock.release()

    def after_reset(
        self,
        dbapi_connection: DBAPIConnection,
        connection_record: ConnectionPoolEntry | None,
        reset_state: PoolResetState,
    ) -> None:
        """Pool ``reset`` listener: roll back as SQLAlchemy would, then release.

        SQLAlchemy fires ``reset`` once for every connection handed back. When it
        will check the connection in right after (the usual case), the release
        waits for ``returned`` so nothing touches the connection once it is free.
        """
        if connection_record is None:
            return  # detached: it was released when it was detached
        if not self.held_here():
            # Garbage collection on another thread: no rollback, no release, since
            # the holder may still be using the connection.
            log.debug(
                "DB in-memory connection reset by thread %r, which does not hold "
                "it; ignored",
                threading.current_thread().name,
            )
            return
        if reset_state.asyncio_safe and not reset_state.transaction_was_reset:
            dbapi_connection.rollback()  # SQLAlchemy's default reset_on_return
        if connection_record.in_use:
            self._deferred.count = getattr(self._deferred, "count", 0) + 1
        else:
            self.release()  # no checkin follows (overlapping connections, one thread)

    def returned(self, record: ConnectionPoolEntry) -> None:
        """The pool has the connection back: the last step of a checkin."""
        deferred = getattr(self._deferred, "count", 0)
        if deferred:
            self._deferred.count = deferred - 1
            self.release()
        elif record.dbapi_connection is None:
            # Invalidated after an error: SQLAlchemy skipped the reset, so this is
            # the only release this connection gets.
            self.release()


class SharedMemoryPool(StaticPool):
    """StaticPool that lends its one connection to one thread at a time.

    The lock is taken in ``_do_get``, before SQLAlchemy wraps the connection, and
    given back from the ``reset`` event and ``_do_return_conn``:

    - "checkout acquires, checkin releases" pool events do not work here. StaticPool
      hands every caller the same connection record, and when two checkouts overlap
      SQLAlchemy checks that record in only once, so a level would leak and the
      next thread would wait forever. ``reset`` fires once per returned connection.
    - Raising a timeout from a ``checkout`` listener makes SQLAlchemy invalidate the
      connection, which closes it and wipes the in-memory database. Raised from
      ``_do_get``, the timeout leaves everything as it was.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.lock = SharedConnectionLock(MEMORY_LOCK_TIMEOUT_S)

    def recreate(self) -> SharedMemoryPool:
        # engine.dispose() swaps in a new pool. Keep one lock per engine: the reset
        # listener, and connections still out from the old pool, use it.
        pool = cast(SharedMemoryPool, super().recreate())
        pool.lock = self.lock
        return pool

    def dispose(self) -> None:
        # Closing the connection under a running call crashed CI: wait for it.
        self.lock.acquire()
        try:
            super().dispose()
        finally:
            self.lock.release()

    def _do_get(self) -> ConnectionPoolEntry:
        self.lock.acquire()
        try:
            return super()._do_get()
        except BaseException:
            self.lock.release()
            raise

    def _do_return_conn(self, record: ConnectionPoolEntry) -> None:
        self.lock.returned(record)
