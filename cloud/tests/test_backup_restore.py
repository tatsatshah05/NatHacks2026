"""OP-405: scripts/db_backup.py and scripts/db_restore.py. No network, no server.

SQLite is tested end to end on the app's real tables: seed, back up, wipe, restore,
and the same rows come back. Postgres needs pg_dump and pg_restore, which these
tests never call: they check the exact commands, that the password stays out of
every argument and message, and the refusals. The one real Postgres restore is the
lead's (docs/RUNBOOK.md, Restore) and the nightly workflow's restore drill.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from cloud.app.camera_readings import CameraReadingIn, CameraReadingStore
from cloud.app.db import make_engine
from cloud.app.saved_routes import SavedRouteStore
from cloud.app.schemas import Obstruction
from cloud.app.store import ObstructionStore

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 7, 20, 10, tzinfo=UTC)
PASSWORD = "s3cr@t pass"
RENDER_URL = (
    "postgres://openpath:s3cr%40t%20pass@dpg-abc123-a.oregon-postgres.render.com"
    "/openpath"
)


def load(name: str) -> ModuleType:
    """Import scripts/<name>.py by path (scripts/ is not a package)."""
    spec = importlib.util.spec_from_file_location(
        f"openpath_{name}", ROOT / "scripts" / f"{name}.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up here
    spec.loader.exec_module(module)
    return module


db_backup = load("db_backup")
db_restore = load("db_restore")


class FakeRun:
    """Stands in for subprocess.run: records each command and writes the dump."""

    def __init__(self, returncode: int = 0, stderr: str = "") -> None:
        self.calls: list[tuple[list[str], dict[str, str]]] = []
        self.returncode = returncode
        self.stderr = stderr

    def __call__(
        self, cmd: list[str], env: dict[str, str], **_: Any
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((cmd, env))
        for arg in cmd:
            if arg.startswith("--file=") and self.returncode == 0:
                Path(arg[len("--file=") :]).write_bytes(b"PGDMP fake custom archive")
        return subprocess.CompletedProcess(cmd, self.returncode, "", self.stderr)


def all_rows(path: Path) -> dict[str, list[tuple[Any, ...]]]:
    """Every row of every table, sorted, read straight from the SQLite file."""
    with closing(sqlite3.connect(path)) as conn:
        names = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        return {
            name: sorted(conn.execute(f'SELECT * FROM "{name}"').fetchall(), key=repr)
            for name in names
        }


def seed(url: str, make_obstruction: Callable[..., Obstruction]) -> None:
    """The app's real tables (obstructions, camera readings, saved routes), with rows."""
    engine = make_engine(url)
    try:
        store = ObstructionStore(engine)
        store.create_tables()
        store.upsert(
            [
                make_obstruction(),
                make_obstruction(
                    source="city_construction",
                    source_ref="C-77",
                    category="sidewalk",
                    kind="sidewalk_closed",
                    title="Sidewalk closed on 102 Avenue",
                    geometry={"type": "Point", "coordinates": [-113.495, 53.541]},
                ),
                make_obstruction(source_ref="5678", title="Escalator out of service"),
            ],
            now=NOW,
        )
        cameras = CameraReadingStore(engine)
        cameras.create_tables()
        cameras.upsert(
            CameraReadingIn(
                source_ref="511:12:3",
                camera_id=12,
                view_id=3,
                latitude=53.54,
                longitude=-113.49,
                label="wet",
                score=0.91,
                observed_at=NOW,
                expires_at=NOW + timedelta(minutes=15),
                model_version="a" * 64,
                mode="live",
            ),
            NOW,
        )
        SavedRouteStore(engine).create_tables()
    finally:
        engine.dispose()


@pytest.fixture
def sqlite_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'data' / 'openpath.db'}"


# ------------------------------------------------------- SQLite, end to end


def test_sqlite_backup_wipe_restore_gives_the_same_rows(
    tmp_path: Path,
    sqlite_url: str,
    make_obstruction: Callable[..., Obstruction],
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "data" / "openpath.db"
    db_file.parent.mkdir()
    seed(sqlite_url, make_obstruction)
    before = all_rows(db_file)
    assert len(before["obstruction"]) == 3
    assert len(before["camera_reading"]) == 1

    out = tmp_path / "backups"
    assert (
        db_backup.main(["--yes", "--out", str(out), "--database-url", sqlite_url]) == 0
    )
    backups = sorted(out.glob("openpath-*.sqlite3"))
    assert len(backups) == 1
    backup = backups[0]
    manifest = json.loads(backup.with_name(backup.name + ".json").read_text())
    assert manifest["kind"] == "sqlite"
    assert manifest["sha256"] == db_backup.sha256(backup)
    assert manifest["tables"] == {name: len(rows) for name, rows in before.items()}
    printed = capsys.readouterr().out
    assert printed.startswith("BACKUP sqlite ")
    assert "3 tables, 4 rows" in printed

    # Wipe: every row goes, and a row the backup never saw arrives.
    with closing(sqlite3.connect(db_file)) as conn:
        for name in before:
            conn.execute(f'DELETE FROM "{name}"')
        conn.commit()
    engine = make_engine(sqlite_url)
    ObstructionStore(engine).upsert(
        [make_obstruction(source_ref="9999", title="After the backup")], now=NOW
    )
    engine.dispose()
    assert len(all_rows(db_file)["obstruction"]) == 1

    assert db_restore.main(["--yes", str(backup), "--database-url", sqlite_url]) == 0
    assert all_rows(db_file) == before
    printed = capsys.readouterr().out
    assert " done in " in printed
    assert "obstruction 3" in printed

    # And the app reads the restored rows.
    engine = make_engine(sqlite_url)
    try:
        active = ObstructionStore(engine).active(NOW)
    finally:
        engine.dispose()
    assert sorted(item.id for item in active) == [
        "city_construction:C-77",
        "city_lrt_devices:1234",
        "city_lrt_devices:5678",
    ]


def test_sqlite_restore_into_a_missing_file_creates_it(
    tmp_path: Path, sqlite_url: str, make_obstruction: Callable[..., Obstruction]
) -> None:
    (tmp_path / "data").mkdir()
    seed(sqlite_url, make_obstruction)
    backup, _, _ = db_backup.backup(
        db_backup.parse_database_url(sqlite_url), tmp_path / "b", now=NOW
    )
    target = tmp_path / "fresh" / "restored.db"
    assert (
        db_restore.main(["--yes", str(backup), "--database-url", f"sqlite:///{target}"])
        == 0
    )
    assert all_rows(target) == all_rows(tmp_path / "data" / "openpath.db")


def test_backup_file_is_named_by_utc_time(tmp_path: Path, sqlite_url: str) -> None:
    (tmp_path / "data").mkdir()
    with closing(sqlite3.connect(tmp_path / "data" / "openpath.db")) as conn:
        conn.execute("CREATE TABLE t (x)")
    backup, manifest, tables = db_backup.backup(
        db_backup.parse_database_url(sqlite_url), tmp_path / "b", now=NOW
    )
    assert backup.name == "openpath-20261107T201000Z.sqlite3"
    assert manifest.name == "openpath-20261107T201000Z.sqlite3.json"
    assert tables == {"t": 0}
    with pytest.raises(db_backup.BackupError, match="already exists"):
        db_backup.backup(
            db_backup.parse_database_url(sqlite_url), tmp_path / "b", now=NOW
        )


# ---------------------------------------------------------------- refusals


def test_backup_without_yes_does_nothing(
    tmp_path: Path, sqlite_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "backups"
    assert db_backup.main(["--out", str(out), "--database-url", sqlite_url]) == 2
    assert not out.exists()
    assert "Run again with --yes" in capsys.readouterr().out


def test_restore_without_yes_leaves_the_target_alone(
    tmp_path: Path,
    sqlite_url: str,
    make_obstruction: Callable[..., Obstruction],
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "data" / "openpath.db"
    db_file.parent.mkdir()
    seed(sqlite_url, make_obstruction)
    backup, _, _ = db_backup.backup(
        db_backup.parse_database_url(sqlite_url), tmp_path / "b", now=NOW
    )
    with closing(sqlite3.connect(db_file)) as conn:
        conn.execute("DELETE FROM obstruction")
        conn.commit()
    assert db_restore.main([str(backup), "--database-url", sqlite_url]) == 2
    assert all_rows(db_file)["obstruction"] == []
    assert "Run again with --yes" in capsys.readouterr().out


def test_missing_sqlite_database_is_not_created(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'nothing.db'}"
    assert (
        db_backup.main(["--yes", "--out", str(tmp_path / "b"), "--database-url", url])
        == 1
    )
    assert not (tmp_path / "nothing.db").exists()
    assert list((tmp_path / "b").glob("*")) == []  # no half backup left behind


@pytest.mark.parametrize("url", ["sqlite://", "sqlite:///:memory:", "mysql://x@y/z"])
def test_unusable_urls_are_refused(url: str, tmp_path: Path) -> None:
    assert db_backup.main(["--yes", "--out", str(tmp_path), "--database-url", url]) == 2


def test_restore_refuses_a_damaged_backup(
    tmp_path: Path,
    sqlite_url: str,
    make_obstruction: Callable[..., Obstruction],
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "data").mkdir()
    seed(sqlite_url, make_obstruction)
    backup, _, _ = db_backup.backup(
        db_backup.parse_database_url(sqlite_url), tmp_path / "b", now=NOW
    )
    with backup.open("ab") as handle:
        handle.write(b"x")
    assert db_restore.main(["--yes", str(backup), "--database-url", sqlite_url]) == 1
    assert "SHA-256" in capsys.readouterr().err


def test_restore_refuses_the_wrong_kind(
    tmp_path: Path, sqlite_url: str, make_obstruction: Callable[..., Obstruction]
) -> None:
    (tmp_path / "data").mkdir()
    seed(sqlite_url, make_obstruction)
    backup, _, _ = db_backup.backup(
        db_backup.parse_database_url(sqlite_url), tmp_path / "b", now=NOW
    )
    run = FakeRun()
    assert (
        db_restore.main(["--yes", str(backup), "--database-url", RENDER_URL], run=run)
        == 1
    )
    assert run.calls == []  # pg_restore never ran
    with pytest.raises(
        db_restore.BackupError, match="sqlite backup, but the target is postgres"
    ):
        db_restore.check_backup(backup, db_backup.parse_database_url(RENDER_URL))
    other = tmp_path / "notes.txt"
    other.write_text("hello")
    with pytest.raises(db_restore.BackupError, match="neither"):
        db_restore.check_backup(other, db_backup.parse_database_url(sqlite_url))


def test_restore_reports_a_missing_table(tmp_path: Path, sqlite_url: str) -> None:
    (tmp_path / "data").mkdir()
    with closing(sqlite3.connect(tmp_path / "data" / "openpath.db")) as conn:
        conn.execute("CREATE TABLE t (x)")
    backup, manifest, _ = db_backup.backup(
        db_backup.parse_database_url(sqlite_url), tmp_path / "b", now=NOW
    )
    data = json.loads(manifest.read_text())
    data["tables"]["obstruction"] = 3  # a table the backup does not hold
    manifest.write_text(json.dumps(data))
    assert db_restore.main(["--yes", str(backup), "--database-url", sqlite_url]) == 1


# ---------------------------------------------------------------- Postgres


def test_render_url_is_split_without_the_password() -> None:
    db = db_backup.parse_database_url(RENDER_URL)
    assert db.kind == "postgres"
    assert db.password == PASSWORD
    assert db.conninfo == (
        "postgresql://openpath@dpg-abc123-a.oregon-postgres.render.com/openpath"
    )
    assert db.shown == (
        "postgresql://openpath:***@dpg-abc123-a.oregon-postgres.render.com/openpath"
    )
    assert "s3cr" not in db.conninfo and "s3cr" not in db.shown


@pytest.mark.parametrize(
    ("url", "conninfo"),
    [
        (
            "postgresql+psycopg://u:p@h:5433/d?sslmode=require",
            "postgresql://u@h:5433/d?sslmode=require",
        ),
        ("postgresql://u@h/d", "postgresql://u@h/d"),
        ("postgres://h/d", "postgresql://h/d"),
    ],
)
def test_postgres_url_forms(url: str, conninfo: str) -> None:
    assert db_backup.parse_database_url(url).conninfo == conninfo


def test_postgres_backup_runs_pg_dump_custom_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(db_backup.shutil, "which", lambda name: f"/usr/bin/{name}")
    run = FakeRun()
    db = db_backup.parse_database_url(RENDER_URL)
    backup, manifest, tables = db_backup.backup(
        db, tmp_path, run=run, counts=lambda _: {"public.obstruction": 12}, now=NOW
    )
    assert backup.name == "openpath-20261107T201000Z.dump"
    [(cmd, env)] = run.calls
    assert cmd == [
        "/usr/bin/pg_dump",
        "--format=custom",
        "--no-password",
        f"--file={backup}",
        "--dbname=postgresql://openpath@dpg-abc123-a.oregon-postgres.render.com/openpath",
    ]
    assert env["PGPASSWORD"] == PASSWORD
    assert all("s3cr" not in arg for arg in cmd)
    data = json.loads(manifest.read_text())
    assert data["kind"] == "postgres"
    assert data["tables"] == {"public.obstruction": 12}
    assert "s3cr" not in manifest.read_text()
    assert tables == {"public.obstruction": 12}


def test_postgres_restore_runs_pg_restore_clean_in_one_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(db_backup.shutil, "which", lambda name: f"/usr/bin/{name}")
    db = db_backup.parse_database_url(RENDER_URL)
    backup, _, _ = db_backup.backup(
        db,
        tmp_path,
        run=FakeRun(),
        counts=lambda _: {"public.obstruction": 12},
        now=NOW,
    )
    run = FakeRun()
    restored, problems, warnings = db_restore.restore(
        backup, db, run=run, counts=lambda _: {"public.obstruction": 13}
    )
    [(cmd, env)] = run.calls
    assert cmd == [
        "/usr/bin/pg_restore",
        "--clean",
        "--if-exists",
        "--no-owner",
        "--no-privileges",
        "--single-transaction",
        "--no-password",
        "--dbname=postgresql://openpath@dpg-abc123-a.oregon-postgres.render.com/openpath",
        str(backup),
    ]
    assert env["PGPASSWORD"] == PASSWORD
    assert restored == {"public.obstruction": 13}
    # Postgres counts were taken while the source was live: a warning, not a failure.
    assert problems == []
    assert warnings == ["public.obstruction has 13 rows, the backup counted 12"]


def test_missing_pg_dump_says_what_to_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(db_backup.shutil, "which", lambda name: None)
    run = FakeRun()
    code = db_backup.main(
        ["--yes", "--out", str(tmp_path), "--database-url", RENDER_URL], run=run
    )
    assert code == 1
    assert run.calls == []
    err = capsys.readouterr().err
    assert "pg_dump is not installed" in err
    assert "s3cr" not in err


def test_failed_pg_dump_leaves_no_file_and_no_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(db_backup.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(db_backup, "count_rows", lambda _: {})
    run = FakeRun(
        returncode=1,
        stderr="pg_dump: error: server version: 17.2; pg_dump version: 16.4",
    )
    code = db_backup.main(
        ["--yes", "--out", str(tmp_path), "--database-url", RENDER_URL], run=run
    )
    assert code == 1
    assert list(tmp_path.glob("*")) == []
    err = capsys.readouterr().err
    assert "server version: 17.2" in err
    assert "s3cr" not in err
