"""In-memory SQLite: one thread at a time uses the shared connection.

Every test, and every TestClient app with the scheduler on, runs on
``make_engine("sqlite://")``: ONE pysqlite connection shared by request threads,
scheduler workers, the push watcher and the hazard rebuilds. pysqlite is not safe
for concurrent use of one connection; a read during a write gave
``sqlite3.InterfaceError: bad parameter or other API misuse``. The first test
hammers the engine from many threads and checks that nothing breaks and no write
is lost; the rest check the lock itself: nesting, a release from the wrong thread,
the timeout and ``dispose()``.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy import func, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import PoolResetState
from sqlmodel import Session, select

from cloud.app import db
from cloud.app.db import (
    SharedConnectionLock,
    SharedConnectionTimeout,
    SharedMemoryPool,
    make_engine,
)
from cloud.app.schemas import Obstruction
from cloud.app.store import ObstructionRow, ObstructionStore

READERS = 8
WRITERS = 2
RUN_S = 1.0
JOIN_S = 30.0


def test_readers_and_writers_share_the_in_memory_database_safely(
    make_obstruction: Callable[..., Obstruction], now: Any
) -> None:
    """8 threads read while 2 write for about a second: no error, no lost write.

    Writers only ever add rows, so each reader must see every source's count stay
    the same or grow, and two reads in one session must agree with each other. At
    the end each source holds exactly the rows its writer reported as written.
    """
    store = ObstructionStore(make_engine("sqlite://"))
    store.create_tables()
    stop = threading.Event()
    errors: list[str] = []
    written = [0] * WRITERS
    reads = [0] * READERS

    def guarded(name: str, body: Callable[[], None]) -> Callable[[], None]:
        def run() -> None:
            try:
                body()
            except Exception as exc:  # noqa: BLE001 - any failure fails the test
                errors.append(f"{name}: {type(exc).__name__}: {exc}")
                stop.set()

        return run

    def writer(index: int) -> None:
        source = f"stress_{index}"
        items: list[Obstruction] = []
        while not stop.is_set():
            k = len(items)
            items.append(
                make_obstruction(source=source, source_ref=str(k), title=f"row {k}")
            )
            if k % 2 == 0:
                store.upsert(items[-1:], now)  # insert one row
            else:
                # Insert the newest row and rewrite every older one; deletes nothing.
                retitled = [i.model_copy(update={"title": f"rev {k}"}) for i in items]
                store.replace_source(source, retitled, now)
            written[index] = len(items)

    def reader(index: int) -> None:
        last: dict[str, int] = {}
        while not stop.is_set():
            with Session(store.engine) as session:
                grouped = session.exec(
                    select(ObstructionRow.source, func.count()).group_by(
                        ObstructionRow.source
                    )
                ).all()
                rows = session.exec(select(ObstructionRow)).all()
            counts = {source: count for source, count in grouped}
            assert len(rows) == sum(counts.values()), (
                f"reader {index}: two reads in one session disagree: "
                f"{len(rows)} rows vs counts {counts}"
            )
            for source, count in counts.items():
                assert count >= last.get(source, 0), (
                    f"reader {index}: {source} went from {last[source]} to {count} rows"
                )
            last = counts
            reads[index] += 1

    threads = [
        threading.Thread(target=guarded(f"writer {i}", lambda i=i: writer(i)))
        for i in range(WRITERS)
    ] + [
        threading.Thread(target=guarded(f"reader {i}", lambda i=i: reader(i)))
        for i in range(READERS)
    ]
    for thread in threads:
        thread.start()
    time.sleep(RUN_S)
    stop.set()
    for thread in threads:
        thread.join(JOIN_S)
    assert not any(thread.is_alive() for thread in threads), "a thread is stuck"

    assert errors == []
    with Session(store.engine) as session:
        final = dict(
            session.exec(
                select(ObstructionRow.source, func.count()).group_by(
                    ObstructionRow.source
                )
            ).all()
        )
    assert all(written), f"a writer never finished a write: {written}"
    assert all(reads), f"a reader never finished a read: {reads}"
    assert final == {f"stress_{i}": written[i] for i in range(WRITERS)}


# ------------------------------------------------------------- the lock itself


@pytest.fixture
def short_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """A deadlock fails these tests in 2 s instead of 30 s."""
    monkeypatch.setattr(db, "MEMORY_LOCK_TIMEOUT_S", 2.0)


def lock_of(engine: Engine) -> SharedConnectionLock:
    return cast(SharedMemoryPool, engine.pool).lock


def new_store() -> ObstructionStore:
    store = ObstructionStore(make_engine("sqlite://"))
    store.create_tables()
    return store


def hold_connection(
    engine: Engine, *, name: str, nested: bool = False
) -> tuple[threading.Thread, threading.Event]:
    """Start a thread that holds the connection until the returned event is set."""
    holding, done = threading.Event(), threading.Event()

    def hold() -> None:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            if nested:
                with engine.connect() as inner:
                    inner.execute(text("SELECT 1"))
                    holding.set()
                    done.wait(10)
            else:
                holding.set()
                done.wait(10)

    thread = threading.Thread(target=hold, name=name)
    thread.start()
    assert holding.wait(5), "the holder never got the connection"
    return thread, done


def test_only_in_memory_sqlite_shares_one_locked_connection(tmp_path: Path) -> None:
    assert isinstance(make_engine("sqlite://").pool, SharedMemoryPool)
    assert isinstance(make_engine("sqlite:///:memory:").pool, SharedMemoryPool)
    file_engine = make_engine(f"sqlite:///{tmp_path / 'openpath.db'}")
    postgres_engine = make_engine("postgres://u:p@host:5432/openpath")
    for engine in (file_engine, postgres_engine):
        assert not isinstance(engine.pool, SharedMemoryPool)
        assert not engine.pool.dispatch.reset  # SQLAlchemy's own reset, untouched


def test_nested_sessions_in_one_thread_do_not_deadlock(
    short_timeout: None, make_obstruction: Callable[..., Obstruction], now: Any
) -> None:
    store = new_store()
    lock = lock_of(store.engine)
    with Session(store.engine) as outer:
        assert outer.exec(select(ObstructionRow)).all() == []
        assert lock.held_here() and lock.depth == 1
        store.upsert([make_obstruction()], now)  # its own Session, one level deeper
        assert lock.depth == 1
        assert len(outer.exec(select(ObstructionRow)).all()) == 1
    assert (lock.owner, lock.depth) == (None, 0)

    # Two connections closed in the order they were opened, not nested.
    first = store.engine.connect()
    first.execute(text("SELECT 1"))
    second = store.engine.connect()
    second.execute(text("SELECT 1"))
    assert lock.depth == 2
    first.close()
    assert lock.held_here() and lock.depth == 1
    second.close()
    assert (lock.owner, lock.depth) == (None, 0)

    # The lock is really free: another thread gets the connection at once.
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert len(pool.submit(store.active, now).result(timeout=5)) == 1


class FakeDBAPIConnection:
    """Counts rollbacks, so a test can see whether the listener touched it."""

    def __init__(self) -> None:
        self.rollbacks = 0

    def rollback(self) -> None:
        self.rollbacks += 1


def test_a_checkin_from_a_thread_that_does_not_hold_the_lock_is_ignored(
    short_timeout: None, caplog: pytest.LogCaptureFixture
) -> None:
    """Garbage collection can return a connection on any thread; it changes nothing."""
    caplog.set_level(logging.DEBUG, logger="openpath.db")
    engine = make_engine("sqlite://")
    lock = lock_of(engine)
    state = PoolResetState(
        transaction_was_reset=False, terminate_only=False, asyncio_safe=True
    )
    fake = FakeDBAPIConnection()
    record: Any = SimpleNamespace(in_use=True, dbapi_connection=fake)
    invalidated: Any = SimpleNamespace(in_use=True, dbapi_connection=None)

    # Nobody holds it: every release path is a no-op and the lock still works.
    lock.after_reset(fake, record, state)
    lock.returned(invalidated)
    lock.release()
    assert (lock.owner, lock.depth, fake.rollbacks) == (None, 0, 0)

    thread, done = hold_connection(engine, name="holder", nested=True)
    owner = lock.owner
    assert owner is not None and lock.depth == 2
    lock.after_reset(fake, record, state)  # as the event fires it, from this thread
    lock.returned(invalidated)
    lock.release()
    assert (lock.owner, lock.depth) == (owner, 2)  # the holder's count is intact
    assert fake.rollbacks == 0  # and its connection was never touched
    assert "does not hold it; ignored" in caplog.text

    done.set()
    thread.join(5)
    assert (lock.owner, lock.depth) == (None, 0)
    with engine.connect() as connection:  # no wait: the lock is free
        assert connection.execute(text("SELECT 1")).scalar_one() == 1


def test_waiting_too_long_raises_a_clear_error(
    monkeypatch: pytest.MonkeyPatch,
    make_obstruction: Callable[..., Obstruction],
    now: Any,
) -> None:
    monkeypatch.setattr(db, "MEMORY_LOCK_TIMEOUT_S", 0.2)
    store = new_store()
    store.upsert([make_obstruction()], now)
    thread, done = hold_connection(store.engine, name="stuck-writer")
    started = time.monotonic()
    with pytest.raises(SharedConnectionTimeout, match=r"Waited 0.2 s .*'stuck-writer'"):
        store.active(now)
    assert time.monotonic() - started < 5
    done.set()
    thread.join(5)
    # The failed wait broke nothing: the data survived and the lock is free.
    assert [item.id for item in store.active(now)] == [make_obstruction().id]
    assert (lock_of(store.engine).owner, lock_of(store.engine).depth) == (None, 0)


def test_dispose_waits_for_the_thread_using_the_connection(short_timeout: None) -> None:
    """Closing the connection under a running call is what crashed CI."""
    engine = make_engine("sqlite://")
    events: list[str] = []
    holding = threading.Event()

    def use() -> None:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            holding.set()
            time.sleep(0.2)
            connection.execute(text("SELECT 1"))
            events.append("call finished")

    thread = threading.Thread(target=use)
    thread.start()
    assert holding.wait(5)
    engine.dispose()
    events.append("disposed")
    thread.join(5)
    assert events == ["call finished", "disposed"]
    assert lock_of(engine).depth == 0  # the recreated pool shares the same lock
