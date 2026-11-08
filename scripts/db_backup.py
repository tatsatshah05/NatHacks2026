"""Back up the OpenPath database to one timestamped file (OP-405).

    python scripts/db_backup.py --yes                     # DATABASE_URL, into backups/
    python scripts/db_backup.py --yes --out DIR --database-url URL

- **Postgres** (Render): ``pg_dump --format=custom`` through ``subprocess``, into
  ``openpath-<UTC time>.dump``. Use a ``pg_dump`` of the server's major version;
  the nightly workflow (``.github/workflows/backup.yml``) installs a matching one.
- **SQLite** (``make dev``): Python's ``sqlite3`` backup API, which makes a
  consistent copy even while the API writes, into ``openpath-<UTC time>.sqlite3``.

Beside each backup it writes ``<backup>.json``: when it was made, the kind, the
size, the SHA-256 and the row count of every table. ``scripts/db_restore.py``
checks the SHA-256 before it restores and the row counts after. For Postgres the
counts are taken just before ``pg_dump`` starts, so a write in between can change
one by a few rows; for SQLite they are counted in the backup itself.

The password in ``DATABASE_URL`` is never printed and never put on a command line
(``pg_dump`` reads it from ``PGPASSWORD``). Without ``--yes`` the script only says
what it would do, so a stray run never connects to production.

Only the standard library, plus ``psycopg`` (``cloud/requirements.txt``) to count
Postgres rows.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "backups"
# The same default as cloud/app/db.py: a SQLite file where the app runs.
DEFAULT_DATABASE_URL = "sqlite:///./openpath.db"
POSTGRES_SCHEMES = (
    "postgres",
    "postgresql",
    "postgresql+psycopg",
    "postgresql+psycopg2",
)
SQLITE_HEADER = b"SQLite format 3\x00"
PG_CUSTOM_HEADER = b"PGDMP"

Kind = Literal["sqlite", "postgres"]
Runner = Callable[..., subprocess.CompletedProcess[str]]
Counter = Callable[["Database"], dict[str, int]]


class BackupError(Exception):
    """A backup or restore that cannot go ahead; the message says why."""


@dataclass(frozen=True)
class Database:
    """Where ``DATABASE_URL`` points, split so the password never leaves ``password``."""

    kind: Kind
    shown: str  # the URL with the password replaced by ***, for messages
    sqlite_path: Path | None = None
    conninfo: str | None = None  # a libpq URI without the password
    password: str | None = None


def parse_database_url(url: str) -> Database:
    """Split ``url``; in-memory SQLite and unknown schemes are refused."""
    url = url.strip()
    if url.startswith("sqlite"):
        if url in ("sqlite://", "sqlite:///:memory:") or ":memory:" in url:
            raise BackupError("an in-memory SQLite database cannot be backed up")
        if not url.startswith("sqlite:///"):
            raise BackupError(f"expected sqlite:///<path>, not {url!r}")
        path = Path(url[len("sqlite:///") :].split("?", 1)[0])
        return Database(kind="sqlite", shown=url, sqlite_path=path)
    parts = urlsplit(url)
    if parts.scheme not in POSTGRES_SCHEMES:
        raise BackupError(
            "DATABASE_URL must be postgres://..., postgresql://... or sqlite:///<path>"
        )
    host = parts.hostname or ""
    if ":" in host:  # an IPv6 address goes back in brackets
        host = f"[{host}]"
    port = f":{parts.port}" if parts.port else ""
    user = (
        parts.netloc.rpartition("@")[0].partition(":")[0] if "@" in parts.netloc else ""
    )
    netloc = f"{user}@{host}{port}" if user else f"{host}{port}"
    conninfo = urlunsplit(("postgresql", netloc, parts.path, parts.query, ""))
    password = unquote(parts.password) if parts.password is not None else None
    shown_netloc = f"{user}:***@{host}{port}" if password is not None else netloc
    shown = urlunsplit(("postgresql", shown_netloc, parts.path, parts.query, ""))
    return Database(kind="postgres", shown=shown, conninfo=conninfo, password=password)


def utc_stamp(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_kind(path: Path) -> Kind | None:
    """``sqlite`` or ``postgres`` (custom format) from the file's first bytes."""
    with path.open("rb") as handle:
        head = handle.read(len(SQLITE_HEADER))
    if head.startswith(SQLITE_HEADER):
        return "sqlite"
    if head.startswith(PG_CUSTOM_HEADER):
        return "postgres"
    return None


def pg_env(db: Database, environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment for pg_dump and pg_restore: the password goes in PGPASSWORD."""
    env = dict(os.environ if environ is None else environ)
    if db.password is not None:
        env["PGPASSWORD"] = db.password
    return env


def require_tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise BackupError(
            f"{name} is not installed. Install the PostgreSQL client tools with the "
            "same major version as the server (docs/RUNBOOK.md, Backups)"
        )
    return path


def run_tool(cmd: Sequence[str], env: Mapping[str, str], run: Runner) -> str:
    """Run pg_dump or pg_restore; raise with its error text if it fails."""
    done = run(list(cmd), env=dict(env), capture_output=True, text=True, check=False)
    if done.returncode != 0:
        message = (done.stderr or done.stdout or "").strip()
        raise BackupError(f"{Path(cmd[0]).name} failed ({done.returncode}): {message}")
    return (done.stdout or "").strip()


# ------------------------------------------------------------------ row counts


def read_only_uri(path: Path) -> str:
    """A SQLite URI that opens ``path`` read-only and never creates it."""
    return f"{path.resolve().as_uri()}?mode=ro"


def sqlite_counts(path: Path) -> dict[str, int]:
    """Rows in every table of the SQLite file at ``path``."""
    with closing(sqlite3.connect(read_only_uri(path), uri=True)) as conn:
        names = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        quoted = {name: '"' + name.replace('"', '""') + '"' for name in names}
        return {
            name: int(
                conn.execute(f"SELECT COUNT(*) FROM {quoted[name]}").fetchone()[0]
            )
            for name in names
        }


def postgres_counts(db: Database) -> dict[str, int]:
    """Rows in every table outside the system schemas (``schema.table``)."""
    import psycopg
    from psycopg import sql

    assert db.conninfo is not None
    with psycopg.connect(db.conninfo, password=db.password) as conn:
        tables = conn.execute(
            "SELECT table_schema, table_name FROM information_schema.tables "
            "WHERE table_type = 'BASE TABLE' "
            "AND table_schema NOT IN ('pg_catalog', 'information_schema') "
            "ORDER BY table_schema, table_name"
        ).fetchall()
        counts: dict[str, int] = {}
        for schema, table in tables:
            query = sql.SQL("SELECT COUNT(*) FROM {}.{}").format(
                sql.Identifier(schema), sql.Identifier(table)
            )
            row = conn.execute(query).fetchone()
            counts[f"{schema}.{table}"] = int(row[0]) if row else 0
        return counts


def count_rows(db: Database) -> dict[str, int]:
    if db.kind == "sqlite":
        assert db.sqlite_path is not None
        return sqlite_counts(db.sqlite_path)
    return postgres_counts(db)


# ---------------------------------------------------------------------- backup


def backup_sqlite(db: Database, out: Path) -> dict[str, int]:
    assert db.sqlite_path is not None
    if not db.sqlite_path.is_file():
        raise BackupError(f"no SQLite database at {db.sqlite_path}")
    # mode=ro: never create or change the database being backed up.
    source = sqlite3.connect(read_only_uri(db.sqlite_path), uri=True)
    with closing(source), closing(sqlite3.connect(out)) as target:
        source.backup(target)
    return sqlite_counts(out)


def backup_postgres(
    db: Database,
    out: Path,
    run: Runner = subprocess.run,
    counts: Counter | None = None,
) -> dict[str, int]:
    assert db.conninfo is not None
    pg_dump = require_tool("pg_dump")
    tables = (counts or count_rows)(db)  # just before the dump; see the docstring
    run_tool(
        [
            pg_dump,
            "--format=custom",
            "--no-password",  # never prompt; the password is in PGPASSWORD
            f"--file={out}",
            f"--dbname={db.conninfo}",
        ],
        pg_env(db),
        run,
    )
    return tables


def write_manifest(
    backup: Path, db: Database, tables: Mapping[str, int], created_at: datetime
) -> Path:
    manifest = backup.with_name(backup.name + ".json")
    manifest.write_text(
        json.dumps(
            {
                "created_at": created_at.isoformat(timespec="seconds").replace(
                    "+00:00", "Z"
                ),
                "kind": db.kind,
                "database": db.shown,
                "file": backup.name,
                "size_bytes": backup.stat().st_size,
                "sha256": sha256(backup),
                "tables": dict(sorted(tables.items())),
                "counts": (
                    "counted in the backup"
                    if db.kind == "sqlite"
                    else "counted just before pg_dump; a write during the dump can "
                    "change a count"
                ),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return manifest


def backup(
    db: Database,
    out_dir: Path,
    run: Runner = subprocess.run,
    counts: Counter | None = None,
    now: datetime | None = None,
) -> tuple[Path, Path, dict[str, int]]:
    """Back ``db`` up into ``out_dir``. Returns the backup, its manifest and counts."""
    created_at = now or datetime.now(UTC)
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = ".sqlite3" if db.kind == "sqlite" else ".dump"
    out = out_dir / f"openpath-{utc_stamp(created_at)}{suffix}"
    if out.exists():
        raise BackupError(f"{out} already exists")
    try:
        if db.kind == "sqlite":
            tables = backup_sqlite(db, out)
        else:
            tables = backup_postgres(db, out, run=run, counts=counts)
    except BaseException:
        out.unlink(missing_ok=True)  # never leave half a backup behind
        raise
    return out, write_manifest(out, db, tables, created_at), tables


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Back up the database named by DATABASE_URL to a timestamped file."
    )
    parser.add_argument(
        "--yes", action="store_true", help="really connect and write the backup"
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output folder")
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL") or DEFAULT_DATABASE_URL,
        help="defaults to DATABASE_URL (never printed with its password)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None, run: Runner = subprocess.run) -> int:
    args = parse_args(argv)
    try:
        db = parse_database_url(args.database_url)
    except BackupError as error:
        print(f"BACKUP refused: {error}", file=sys.stderr)
        return 2
    if not args.yes:
        print(
            f"BACKUP not started: would back up {db.shown} into {args.out}. "
            "Run again with --yes."
        )
        return 2
    started = time.monotonic()
    try:
        out, manifest, tables = backup(db, args.out, run=run)
    except (BackupError, OSError, sqlite3.Error) as error:
        print(f"BACKUP failed: {db.shown}: {error}", file=sys.stderr)
        return 1
    except Exception as error:  # noqa: BLE001 - psycopg errors, without a traceback
        print(
            f"BACKUP failed: {db.shown}: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return 1
    rows = sum(tables.values())
    print(
        f"BACKUP {db.kind} {db.shown} -> {out} ({out.stat().st_size} bytes, "
        f"{len(tables)} tables, {rows} rows, sha256 {sha256(out)[:12]}) "
        f"in {time.monotonic() - started:.1f} s; manifest {manifest.name}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
