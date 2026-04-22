"""Hand-rolled migration runner — forward-only, checksum-protected.

ADR: docs/adr/0005-schema-migration-discipline.md.

Responsibilities:
  - Discover `db/migrations/NNN_*.sql` files shipped with the package.
  - Track applied migrations in the `_migrations` table (id, checksum, ts).
  - Refuse to run when a prior migration's checksum on disk differs from what
    was stored (shipped migrations are immutable — edits require a new
    numbered file).
  - Refuse to run when `PRAGMA user_version` is ahead of the newest shipped
    migration (binary downgrade).
  - Write a pre-migration backup via SQLite's online backup API before any
    DDL runs on a DB that already has user data.
  - Apply each pending migration in a single transaction, then advance
    `PRAGMA user_version` atomically.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

from langusta.db.connection import DbPath, connect

MIGRATIONS_PACKAGE = "langusta.db.migrations"
_FILENAME_RE = re.compile(r"^(?P<id>\d{3,4})_(?P<desc>[A-Za-z0-9_]+)\.sql$")


def _iter_migration_entries(
    migrations_dir: Path | None,
) -> list[tuple[int, str, str]]:
    """Yield (id, description, sql) triples from either a filesystem dir (for
    tests) or the packaged `langusta.db.migrations` resource (production)."""
    triples: list[tuple[int, str, str]] = []
    if migrations_dir is not None:
        for entry in migrations_dir.iterdir():
            match = _FILENAME_RE.match(entry.name)
            if not match:
                continue
            triples.append(
                (
                    int(match.group("id")),
                    match.group("desc").replace("_", " "),
                    entry.read_text(encoding="utf-8"),
                )
            )
    else:
        pkg = files(MIGRATIONS_PACKAGE)
        for entry in pkg.iterdir():
            match = _FILENAME_RE.match(entry.name)
            if not match:
                continue
            triples.append(
                (
                    int(match.group("id")),
                    match.group("desc").replace("_", " "),
                    entry.read_text(encoding="utf-8"),
                )
            )
    return triples


class MigrationChecksumError(RuntimeError):
    """A prior migration's file has been modified since it was applied."""


_BOOTSTRAP_SQL = """
CREATE TABLE IF NOT EXISTS _migrations (
    id           INTEGER PRIMARY KEY,
    description  TEXT    NOT NULL,
    checksum     TEXT    NOT NULL,
    applied_at   TEXT    NOT NULL
);
"""


@dataclass(frozen=True, slots=True)
class Migration:
    id: int
    description: str
    sql: str
    checksum: str


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def discover_migrations(migrations_dir: Path | None = None) -> list[Migration]:
    """Return all migration files sorted by numeric id.

    Production callers pass no argument and read from the packaged
    `langusta.db.migrations` resource. Tests pass a `migrations_dir` to work
    with a fabricated migration chain under `tmp_path`.
    """
    out: list[Migration] = []
    for mig_id, description, sql in _iter_migration_entries(migrations_dir):
        out.append(
            Migration(
                id=mig_id,
                description=description,
                sql=sql,
                checksum=hashlib.sha256(sql.encode("utf-8")).hexdigest(),
            )
        )
    out.sort(key=lambda m: m.id)
    ids = [m.id for m in out]
    if len(set(ids)) != len(ids):
        raise RuntimeError(f"duplicate migration ids found: {ids}")
    return out


def latest_schema_version(migrations_dir: Path | None = None) -> int:
    found = discover_migrations(migrations_dir)
    return found[-1].id if found else 0


# ---------------------------------------------------------------------------
# State inspection
# ---------------------------------------------------------------------------


def current_schema_version(db_path: DbPath) -> int:
    """Return `PRAGMA user_version`, or 0 for a missing DB."""
    p = Path(db_path) if db_path != ":memory:" else None
    if p is not None and not p.exists():
        return 0
    with connect(db_path) as conn:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _applied_migrations(conn: sqlite3.Connection) -> dict[int, str]:
    """Return {id: checksum} for migrations recorded in `_migrations`.

    Returns an empty dict if the `_migrations` table does not yet exist.
    """
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='_migrations'"
    ).fetchone()
    if row is None:
        return {}
    rows = conn.execute("SELECT id, checksum FROM _migrations").fetchall()
    return {int(r["id"]): str(r["checksum"]) for r in rows}


# ---------------------------------------------------------------------------
# Pre-migration backup
# ---------------------------------------------------------------------------


def _has_user_data(conn: sqlite3.Connection) -> bool:
    """Is there anything worth backing up before DDL?

    A fresh install has no tables yet (or only empty foundation tables) — no
    point in littering ~/.langusta/backups/ with empty files. Heuristic: if
    any user-facing table exists AND has rows, back up.
    """
    tables = [
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' AND name != '_migrations'"
        ).fetchall()
    ]
    for table in tables:
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        if count > 0:
            return True
    return False


def _write_backup(src_path: Path, backups_dir: Path, current_version: int) -> Path:
    backups_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dst = backups_dir / f"db-pre-migration-{current_version:04d}-{ts}.sqlite"
    # Use SQLite's online backup API — safe while the DB is in use (WAL-friendly).
    # `with sqlite3.connect(...)` only commits; it does not close. Wrap with
    # `closing` so the fds are released when this returns.
    with (
        closing(sqlite3.connect(str(src_path))) as src,
        closing(sqlite3.connect(str(dst))) as dst_conn,
    ):
        src.backup(dst_conn)
    return dst


# ---------------------------------------------------------------------------
# Migration runner
# ---------------------------------------------------------------------------


def migrate(
    db_path: DbPath,
    *,
    backups_dir: Path | None = None,
    migrations_dir: Path | None = None,
) -> None:
    """Bring `db_path` forward to the latest shipped schema version.

    Idempotent. Safe to call on every `langusta` invocation.

    `migrations_dir` is for tests — production callers omit it and read the
    shipped migrations from the package resource.
    """
    shipped = discover_migrations(migrations_dir)
    if not shipped:
        return

    latest = shipped[-1].id

    with connect(db_path) as conn:
        # Runner's own bookkeeping table exists independently of the migration
        # chain; this makes the runner testable with arbitrary fake migrations.
        conn.executescript(_BOOTSTRAP_SQL)

        current = int(conn.execute("PRAGMA user_version").fetchone()[0])

        if current > latest:
            raise RuntimeError(
                f"database schema_version={current} is ahead of this "
                f"binary's latest={latest} — downgrade detected. Refusing to run."
            )

        applied = _applied_migrations(conn)

        # Checksum check: every applied migration's checksum must still match
        # what we ship, or someone edited a shipped migration file.
        for mig in shipped:
            if mig.id in applied and applied[mig.id] != mig.checksum:
                raise MigrationChecksumError(
                    f"migration {mig.id} has been modified since it was applied "
                    f"(stored checksum={applied[mig.id]}, "
                    f"shipped checksum={mig.checksum}). "
                    "Shipped migrations are immutable — create a new numbered "
                    "migration to amend."
                )

        pending = [m for m in shipped if m.id > current]
        if not pending:
            return

        # Pre-migration backup only when there's user data to protect.
        # If the caller didn't specify a backups_dir, fall back to
        # `paths.backups_dir()` so the ADR-0005 safety rail is on by
        # default -- every production migrate() call gets the backup,
        # without each call site having to remember the kwarg.
        effective_backups_dir = backups_dir
        if effective_backups_dir is None:
            from langusta import paths
            effective_backups_dir = paths.backups_dir()
        if _has_user_data(conn):
            # Close the migrate-time connection temporarily? Not needed —
            # online backup API is WAL-safe with a live writer.
            path = Path(db_path) if db_path != ":memory:" else None
            if path is not None and path.exists():
                _write_backup(path, effective_backups_dir, current)

        # Disable FK enforcement across the pending chain. SQLite performs an
        # implicit DELETE FROM on DROP TABLE when foreign_keys=ON, which
        # cascades to any child rows — that silently destroys data on any
        # rebuild-via-swap migration (007 is the in-tree example). The
        # canonical 12-step "other kinds of schema change" recipe calls for
        # FK off around the rebuild; we do it once around the whole chain so
        # individual migrations don't have to repeat the pragma dance.
        # `foreign_key_check` after the chain catches any genuine orphans the
        # migrations themselves produced.
        conn.execute("PRAGMA foreign_keys = OFF")
        # Hand transaction control to us so DDL doesn't implicit-commit under
        # our feet. Python's LEGACY isolation inserts an implicit COMMIT
        # before every non-DML non-query statement (CREATE, DROP, PRAGMA);
        # that would commit half a migration if the process died between
        # the DDL and the INSERT INTO _migrations bookkeeping row. With
        # isolation_level=None we wrap each migration in an explicit
        # BEGIN/COMMIT so DDL + bookkeeping + user_version advance together
        # or not at all.
        saved_isolation = conn.isolation_level
        conn.isolation_level = None
        try:
            for mig in pending:
                conn.execute("BEGIN")
                try:
                    for stmt in _split_statements(mig.sql):
                        conn.execute(stmt)
                    conn.execute(
                        "INSERT INTO _migrations "
                        "(id, description, checksum, applied_at) "
                        "VALUES (?, ?, ?, ?)",
                        (
                            mig.id,
                            mig.description,
                            mig.checksum,
                            datetime.now(UTC).isoformat(timespec="seconds"),
                        ),
                    )
                    # Advance user_version atomically with the migration.
                    conn.execute(f"PRAGMA user_version = {mig.id}")
                    conn.execute("COMMIT")
                except BaseException:
                    conn.execute("ROLLBACK")
                    raise
            violations = conn.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise RuntimeError(
                    "migration chain left dangling foreign-key references: "
                    f"{[tuple(v) for v in violations]}"
                )
        finally:
            conn.isolation_level = saved_isolation
            conn.execute("PRAGMA foreign_keys = ON")


def _split_statements(script: str) -> list[str]:
    """Split a multi-statement SQL script into individual statements.
    Uses `sqlite3.complete_statement` so quoted strings and `--` comments
    are respected — unlike a naive split on `;`.
    """
    statements: list[str] = []
    buf = ""
    for line in script.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            stripped = buf.strip()
            if stripped:
                statements.append(stripped)
            buf = ""
    tail = buf.strip()
    if tail:
        statements.append(tail)
    return statements
