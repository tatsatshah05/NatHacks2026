"""Restore an OpenPath database backup made by ``scripts/db_backup.py`` (OP-405).

    python scripts/db_restore.py --yes backups/openpath-20261108T093000Z.dump
    python scripts/db_restore.py --yes BACKUP --database-url URL

It replaces what is in the target database (``DATABASE_URL`` unless
``--database-url`` is given) with the backup:

- **Postgres:** ``pg_restore --clean --if-exists --no-owner --no-privileges
  --single-transaction``. ``--clean --if-exists`` drops each table before it is
  recreated; ``--single-transaction`` means the restore either completes or
  changes nothing; ``--no-owner --no-privileges`` lets a backup of one Render
  database restore into another with a different user. Render's advice is to
  restore into an empty database (docs/RUNBOOK.md, Restore).
- **SQLite:** the ``sqlite3`` backup API copies the backup over the target file.
  Stop the API first, so nothing writes in between.

Before it starts, it checks that the backup's SHA-256 matches its ``.json``
manifest and that the backup's kind matches the target. After the restore it
counts the rows of every table and compares them with the manifest: a missing
table or, for SQLite, a different count is a failure; for Postgres a different
count is reported as a warning, because those counts were taken while the source
was live. Without ``--yes`` nothing happens.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from contextlib import closing
from pathlib import Path
from typing import Any

SCRIPTS = str(Path(__file__).resolve().parent)
if SCRIPTS not in sys.path:  # scripts/ is not a package; db_backup.py sits beside this
    sys.path.insert(0, SCRIPTS)

import db_backup
from db_backup import (
    DEFAULT_DATABASE_URL,
    BackupError,
    Counter,
    Database,
    Runner,
    file_kind,
    parse_database_url,
    pg_env,
    require_tool,
    run_tool,
    sha256,
)


def load_manifest(backup: Path) -> dict[str, Any] | None:
    path = backup.with_name(backup.name + ".json")
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def check_backup(backup: Path, db: Database) -> dict[str, Any] | None:
    """Refuse a missing, damaged or wrong-kind backup. Returns its manifest."""
    if not backup.is_file():
        raise BackupError(f"no backup file at {backup}")
    kind = file_kind(backup)
    if kind is None:
        raise BackupError(
            f"{backup.name} is neither a SQLite file nor a pg_dump custom-format file"
        )
    if kind != db.kind:
        raise BackupError(
            f"{backup.name} is a {kind} backup, but the target is {db.kind}"
        )
    manifest = load_manifest(backup)
    if manifest is not None and manifest.get("sha256") != sha256(backup):
        raise BackupError(
            f"{backup.name} does not match the SHA-256 in its manifest; "
            "it is damaged or not the file that was backed up"
        )
    return manifest


def restore_sqlite(backup: Path, db: Database) -> None:
    assert db.sqlite_path is not None
    db.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(db_backup.read_only_uri(backup), uri=True)
    with closing(source), closing(sqlite3.connect(db.sqlite_path)) as target:
        source.backup(target)  # replaces every page of the target


def restore_postgres(
    backup: Path,
    db: Database,
    run: Runner = subprocess.run,
    environ: Mapping[str, str] | None = None,
) -> None:
    assert db.conninfo is not None
    pg_restore = require_tool("pg_restore")
    run_tool(
        [
            pg_restore,
            "--clean",
            "--if-exists",
            "--no-owner",
            "--no-privileges",
            "--single-transaction",
            "--no-password",  # never prompt; the password is in PGPASSWORD
            f"--dbname={db.conninfo}",
            str(backup),
        ],
        pg_env(db, environ),
        run,
    )


def compare(
    expected: Mapping[str, int], actual: Mapping[str, int], kind: str
) -> tuple[list[str], list[str]]:
    """Problems (missing tables, SQLite count differences) and warnings."""
    problems: list[str] = []
    warnings: list[str] = []
    for table, rows in sorted(expected.items()):
        if table not in actual:
            problems.append(f"table {table} is missing")
        elif actual[table] != rows:
            note = f"{table} has {actual[table]} rows, the backup counted {rows}"
            (problems if kind == "sqlite" else warnings).append(note)
    return problems, warnings


def restore(
    backup: Path,
    db: Database,
    run: Runner = subprocess.run,
    counts: Counter | None = None,
) -> tuple[dict[str, int], list[str], list[str]]:
    """Restore ``backup`` into ``db``; returns the restored counts, problems, warnings."""
    manifest = check_backup(backup, db)
    if db.kind == "sqlite":
        restore_sqlite(backup, db)
    else:
        restore_postgres(backup, db, run=run)
    restored = (counts or db_backup.count_rows)(db)
    if manifest is None:
        return restored, [], ["no manifest beside the backup; row counts not compared"]
    problems, warnings = compare(manifest.get("tables") or {}, restored, db.kind)
    return restored, problems, warnings


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replace the database named by DATABASE_URL with a backup."
    )
    parser.add_argument("backup", type=Path, help="a file made by db_backup.py")
    parser.add_argument(
        "--yes", action="store_true", help="really replace the target database"
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL") or DEFAULT_DATABASE_URL,
        help="the target; defaults to DATABASE_URL (never printed with its password)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None, run: Runner = subprocess.run) -> int:
    args = parse_args(argv)
    try:
        db = parse_database_url(args.database_url)
        if not args.yes:
            check_backup(args.backup, db)
    except BackupError as error:
        print(f"RESTORE refused: {error}", file=sys.stderr)
        return 2
    if not args.yes:
        print(
            f"RESTORE not started: would replace every table in {db.shown} with "
            f"{args.backup}. Run again with --yes."
        )
        return 2
    started = time.monotonic()
    try:
        restored, problems, warnings = restore(args.backup, db, run=run)
    except BackupError as error:
        print(f"RESTORE refused or failed: {db.shown}: {error}", file=sys.stderr)
        return 1
    except Exception as error:  # noqa: BLE001 - sqlite3 and psycopg errors, no traceback
        print(
            f"RESTORE failed: {db.shown}: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return 1
    for warning in warnings:
        print(f"RESTORE warning: {warning}")
    for problem in problems:
        print(f"RESTORE problem: {problem}", file=sys.stderr)
    tables = ", ".join(f"{name} {rows}" for name, rows in sorted(restored.items()))
    verdict = "FAILED the row check" if problems else "done"
    print(
        f"RESTORE {db.kind} {args.backup} -> {db.shown} {verdict} in "
        f"{time.monotonic() - started:.1f} s; {len(restored)} tables: {tables}"
    )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
