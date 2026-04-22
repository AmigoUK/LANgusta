"""Migration runner tests.

ADR: docs/adr/0005-schema-migration-discipline.md — forward-only migrations
from 0.1.0 onward, mandatory pre-migration backup, restore-from-old-backup is
a CI contract.

The runner is hand-rolled (~100 lines) driving `PRAGMA user_version`. It is
the only sanctioned way to advance the schema. Call sites must not run DDL
directly.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from langusta.db.connection import connect
from langusta.db.migrate import (
    MigrationChecksumError,
    current_schema_version,
    discover_migrations,
    latest_schema_version,
    migrate,
)

# ---------------------------------------------------------------------------
# discover_migrations — file layout contract
# ---------------------------------------------------------------------------


def test_discover_migrations_returns_shipped_files_sorted() -> None:
    """Migrations live at src/langusta/db/migrations/NNN_*.sql and are returned
    sorted by numeric id."""
    found = discover_migrations()
    assert found, "expected at least one shipped migration"
    ids = [m.id for m in found]
    assert ids == sorted(ids)
    assert all(m.id > 0 for m in found)
    assert all(m.sql.strip() for m in found)


def test_latest_schema_version_matches_last_migration() -> None:
    found = discover_migrations()
    assert latest_schema_version() == found[-1].id


# ---------------------------------------------------------------------------
# migrate() on a fresh DB
# ---------------------------------------------------------------------------


def test_migrate_007_preserves_existing_monitoring_rows(tmp_path: Path) -> None:
    """007_monitor_snmp_ssh rebuilds monitoring_checks via table-swap; any
    rows from the prior schema must survive the swap unchanged."""
    from datetime import UTC, datetime

    from langusta.db import assets as assets_dal
    from langusta.db import monitoring as mon_dal

    db = tmp_path / "mig.sqlite"
    # Apply only up through migration 006 (the pre-007 schema).
    migrations = discover_migrations()
    pre = [m for m in migrations if m.id < 7]
    assert pre, "expected migrations numbered < 7"
    with connect(db) as conn:
        conn.execute(
            "CREATE TABLE _migrations (id INTEGER PRIMARY KEY, description TEXT NOT NULL, "
            "checksum TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )
        for m in pre:
            conn.executescript(m.sql)
            conn.execute(
                "INSERT INTO _migrations (id, description, checksum, applied_at) "
                "VALUES (?, ?, ?, ?)",
                (m.id, m.description, m.checksum, datetime.now(UTC).isoformat()),
            )
            conn.execute(f"PRAGMA user_version = {m.id}")

        now = datetime(2026, 4, 18, 12, 0, 0, tzinfo=UTC)
        aid = assets_dal.insert_manual(
            conn, hostname="legacy", primary_ip="10.0.0.9", now=now,
        )
        # Insert using the pre-007 column set only.
        row = conn.execute(
            "INSERT INTO monitoring_checks ("
            "asset_id, kind, target, port, path, interval_seconds, enabled, created_at"
            ") VALUES (?, 'http', NULL, 8080, '/healthz', 60, 1, ?) RETURNING id",
            (aid, now.isoformat(timespec="seconds")),
        ).fetchone()
        pre_007_cid = int(row[0])

    # Now run migrate() to apply 007 on top of existing data.
    migrate(db)

    # Pre-existing http check survived with its config intact; new NULL
    # columns are readable via the new DAL.
    with connect(db) as conn:
        check = mon_dal.get_by_id(conn, pre_007_cid)
    assert check is not None
    assert check.kind == "http"
    assert check.port == 8080
    assert check.path == "/healthz"
    assert check.oid is None
    assert check.command is None
    assert check.credential_id is None


def test_migrate_007_preserves_check_results_across_table_rebuild(
    tmp_path: Path,
) -> None:
    """007 rebuilds monitoring_checks via DROP + RENAME. Historic
    `check_results` rows FK-reference `monitoring_checks(id)` with
    `ON DELETE CASCADE` — under `PRAGMA foreign_keys=ON` (which `connect()`
    always sets) a DROP TABLE on the parent performs an implicit
    `DELETE FROM` and cascades to the children. This test is the ADR-0005
    no-data-loss guard: every `check_results` row must survive the rebuild
    and still resolve to the rebuilt `monitoring_checks` row.

    Settles the Wave-2 open uncertainty on migration 007 (finding C-002).
    """
    from datetime import UTC, datetime

    from langusta.db import assets as assets_dal

    db = tmp_path / "mig.sqlite"
    migrations = discover_migrations()
    pre = [m for m in migrations if m.id < 7]
    assert pre, "expected migrations numbered < 7"

    now = datetime(2026, 4, 20, 12, 0, 0, tzinfo=UTC)

    with connect(db) as conn:
        conn.execute(
            "CREATE TABLE _migrations (id INTEGER PRIMARY KEY, description TEXT NOT NULL, "
            "checksum TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )
        for m in pre:
            conn.executescript(m.sql)
            conn.execute(
                "INSERT INTO _migrations (id, description, checksum, applied_at) "
                "VALUES (?, ?, ?, ?)",
                (m.id, m.description, m.checksum, now.isoformat()),
            )
            conn.execute(f"PRAGMA user_version = {m.id}")

        aid = assets_dal.insert_manual(
            conn, hostname="r", primary_ip="10.0.0.1", now=now,
        )
        row = conn.execute(
            "INSERT INTO monitoring_checks ("
            "asset_id, kind, target, port, path, interval_seconds, enabled, created_at"
            ") VALUES (?, 'http', NULL, 80, '/', 60, 1, ?) RETURNING id",
            (aid, now.isoformat()),
        ).fetchone()
        cid = int(row[0])

        # Three child rows — these are the rows ADR-0005 says must survive.
        for i in range(3):
            conn.execute(
                "INSERT INTO check_results "
                "(check_id, asset_id, status, latency_ms, detail, recorded_at) "
                "VALUES (?, ?, 'ok', 1.0, NULL, ?)",
                (cid, aid, now.replace(minute=i).isoformat()),
            )
        pre_count = int(
            conn.execute("SELECT COUNT(*) FROM check_results").fetchone()[0]
        )

    assert pre_count == 3, "arrange: three child rows seeded"

    # Act — apply migration 007 on top of existing data.
    migrate(db)

    # Assert — every check_result survived AND still resolves to the
    # rebuilt monitoring_checks row. If either assertion fails we have
    # real data loss on the 0.1 → 0.2 upgrade path.
    with connect(db) as conn:
        post_count = int(
            conn.execute("SELECT COUNT(*) FROM check_results").fetchone()[0]
        )
        orphaned = int(
            conn.execute(
                "SELECT COUNT(*) FROM check_results cr "
                "LEFT JOIN monitoring_checks mc ON mc.id = cr.check_id "
                "WHERE mc.id IS NULL"
            ).fetchone()[0]
        )

    assert post_count == pre_count, (
        f"check_results row count changed across 007 rebuild: "
        f"{pre_count} -> {post_count} (data loss; ADR-0005 violation)"
    )
    assert orphaned == 0, (
        f"{orphaned} check_results rows orphaned after rebuild"
    )


def test_migrate_fresh_db_applies_all_migrations(tmp_path: Path) -> None:
    db = tmp_path / "fresh.sqlite"
    migrate(db)

    with connect(db) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == latest_schema_version()


def test_migrate_records_applied_migrations_in_metadata_table(tmp_path: Path) -> None:
    db = tmp_path / "fresh.sqlite"
    migrate(db)

    with connect(db) as conn:
        rows = conn.execute(
            "SELECT id, description, checksum, applied_at FROM _migrations ORDER BY id"
        ).fetchall()

    shipped = discover_migrations()
    assert len(rows) == len(shipped)
    for row, mig in zip(rows, shipped, strict=True):
        assert row["id"] == mig.id
        assert row["description"] == mig.description
        assert row["checksum"] == mig.checksum
        assert row["applied_at"] is not None


def test_migrate_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "idem.sqlite"
    migrate(db)
    # Run twice; second pass must not raise, must not re-apply, must not
    # duplicate _migrations rows.
    migrate(db)
    with connect(db) as conn:
        count = conn.execute("SELECT COUNT(*) FROM _migrations").fetchone()[0]
    assert count == len(discover_migrations())


def test_current_schema_version_on_missing_db_returns_zero(tmp_path: Path) -> None:
    assert current_schema_version(tmp_path / "does-not-exist.sqlite") == 0


def test_current_schema_version_after_migrate_matches_latest(tmp_path: Path) -> None:
    db = tmp_path / "v.sqlite"
    migrate(db)
    assert current_schema_version(db) == latest_schema_version()


# ---------------------------------------------------------------------------
# Pre-migration backup contract (ADR-0005 safety rail)
# ---------------------------------------------------------------------------


def test_migrate_on_empty_db_does_not_need_backup(tmp_path: Path) -> None:
    """A fresh install has no user data to lose; no pre-migration backup required."""
    db = tmp_path / "fresh.sqlite"
    backups = tmp_path / "backups"
    migrate(db, backups_dir=backups)
    # backups dir may not even exist — creating it on a fresh install would be noise.
    if backups.exists():
        assert not list(backups.iterdir())


def _write_fake_migration(dir_: Path, mig_id: int, sql: str) -> None:
    dir_.mkdir(exist_ok=True)
    (dir_ / f"{mig_id:03d}_fake.sql").write_text(sql)


def test_migrate_writes_pre_migration_backup_even_without_explicit_kwarg(
    tmp_langusta_home: Path,
) -> None:
    """Wave-3 TEST-A-019. Every caller of migrate() should get the ADR-0005
    pre-migration backup by default — without having to remember to pass
    `backups_dir=paths.backups_dir()`. The runner now defaults the kwarg
    to `paths.backups_dir()` when unspecified."""
    from langusta import paths

    # Fabricated 2-step chain so only the last migration is pending,
    # mirroring the style of the explicit-kwarg backup test below.
    tmp_langusta_home.mkdir(parents=True, exist_ok=True)
    fake_migs = tmp_langusta_home / "migrations"
    _write_fake_migration(
        fake_migs, 1,
        "CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT);",
    )
    db = tmp_langusta_home / "db.sqlite"
    db.parent.mkdir(parents=True, exist_ok=True)
    migrate(db, migrations_dir=fake_migs)

    # User writes something to the DB at schema v1.
    with connect(db) as conn:
        conn.execute("INSERT INTO notes (body) VALUES ('must-survive')")

    # Ship step 2 and migrate WITHOUT specifying backups_dir.
    _write_fake_migration(
        fake_migs, 2,
        "ALTER TABLE notes ADD COLUMN tag TEXT;",
    )
    migrate(db, migrations_dir=fake_migs)

    snaps = list(paths.backups_dir().glob("db-pre-migration-*.sqlite"))
    assert snaps, (
        f"expected a pre-migration snapshot under {paths.backups_dir()} "
        "even though migrate() was called without backups_dir"
    )


def test_migrate_writes_pre_migration_backup_when_db_has_data(tmp_path: Path) -> None:
    """If the DB already has rows at a prior schema_version and the runner is
    advancing to a newer version, a timestamped backup must land before any
    DDL runs. Tested with a fabricated two-step migration chain so we avoid
    the rewind-and-pretend hack."""
    db = tmp_path / "user.sqlite"
    backups = tmp_path / "backups"
    fake_migs = tmp_path / "migrations"

    # Step 1: a minimal v1 schema.
    _write_fake_migration(
        fake_migs, 1,
        "CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT);",
    )
    migrate(db, migrations_dir=fake_migs)

    # User inserts data at v1.
    with connect(db) as conn:
        conn.execute("INSERT INTO notes (body) VALUES ('pre-existing')")

    # Step 2: ship a follow-up migration.
    _write_fake_migration(
        fake_migs, 2,
        "ALTER TABLE notes ADD COLUMN tag TEXT;",
    )
    migrate(db, migrations_dir=fake_migs, backups_dir=backups)

    assert backups.exists() and backups.is_dir()
    backup_files = list(backups.glob("db-pre-migration-*.sqlite"))
    assert backup_files, "expected at least one pre-migration backup"
    # Backup contains the pre-migration data at the pre-migration schema.
    with sqlite3.connect(str(backup_files[0])) as b:
        rows = b.execute("SELECT body FROM notes").fetchall()
    assert rows == [("pre-existing",)]
    # The original DB is now at v2 with the ALTERed column.
    with connect(db) as conn:
        rows2 = conn.execute("SELECT body, tag FROM notes").fetchall()
    assert [dict(r) for r in rows2] == [{"body": "pre-existing", "tag": None}]


# ---------------------------------------------------------------------------
# Checksum validation — shipped migrations are immutable
# ---------------------------------------------------------------------------


def test_migrate_refuses_when_applied_migration_checksum_changes(
    tmp_path: Path,
) -> None:
    """If a user has migration N applied but the code ships a different
    migration N, the runner must refuse — shipped migrations are immutable
    (ADR-0005). Simulate by tampering with the stored checksum."""
    db = tmp_path / "tamper.sqlite"
    migrate(db)

    with connect(db) as conn:
        conn.execute(
            "UPDATE _migrations SET checksum = 'beefbeefbeefbeefbeefbeefbeefbeef' "
            "WHERE id = 1"
        )

    with pytest.raises(MigrationChecksumError) as excinfo:
        migrate(db)
    assert "checksum" in str(excinfo.value).lower()
    assert "1" in str(excinfo.value)


# ---------------------------------------------------------------------------
# foreign_keys pragma discipline (Wave-3 TEST-C-002b — sentinel for the
# batch-1 fix that keeps migration-007 from cascade-deleting child rows)
# ---------------------------------------------------------------------------


def test_migrate_disables_foreign_keys_around_pending_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0005 table-rebuild safety: the runner must turn `foreign_keys`
    OFF before applying pending migrations and back ON afterwards. This is
    a sentinel — if a future refactor drops the pragma dance,
    monitoring_checks-style rebuilds will silently delete child rows again.

    Uses sqlite3's `set_trace_callback` to observe every statement the
    migrate-time connection executes (instance `.execute` is read-only and
    can't be monkeypatched directly).
    """
    import contextlib

    import langusta.db.migrate as mig

    pragmas_seen: list[str] = []
    original_connect = mig.connect

    @contextlib.contextmanager  # type: ignore[arg-type]
    def traced_connect(path: object):
        with original_connect(path) as conn:  # type: ignore[arg-type]
            conn.set_trace_callback(
                lambda stmt: pragmas_seen.append(stmt)
                if "foreign_keys" in stmt.lower()
                else None,
            )
            try:
                yield conn
            finally:
                conn.set_trace_callback(None)

    monkeypatch.setattr(mig, "connect", traced_connect)

    mig.migrate(tmp_path / "x.sqlite")

    joined = " ".join(p.lower().replace(" ", "") for p in pragmas_seen)
    assert "foreign_keys=off" in joined, (
        f"expected PRAGMA foreign_keys = OFF during migrate(); "
        f"observed pragmas: {pragmas_seen}"
    )
    assert "foreign_keys=on" in joined, (
        f"expected PRAGMA foreign_keys = ON after migrate(); "
        f"observed pragmas: {pragmas_seen}"
    )


# ---------------------------------------------------------------------------
# Migration atomicity (Wave-3 TEST-C-001)
# ---------------------------------------------------------------------------


class _InjectedCrashError(RuntimeError):
    """Marker exception used to simulate a crash mid-migration."""


def test_migrate_is_atomic_when_interrupted_between_ddl_and_bookkeeping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If a process dies after a migration's DDL runs but before its row
    is recorded in `_migrations`, a subsequent migrate() run must still
    succeed -- not blow up re-running the DDL against a schema that
    already carries it."""
    import langusta.db.migrate as mig

    # Fabricated 2-step chain: step 1 always succeeds, step 2 is the one
    # we sabotage. Both create tables that would fail re-create if the
    # runner weren't atomic. The plan's "bring the DB up to step 1, then
    # ship step 2" pattern is mirrored by first migrating against a dir
    # that only has step 1, then adding step 2 and migrating again.
    step1_only_dir = tmp_path / "migrations_step1"
    step1_only_dir.mkdir()
    _write_fake_migration(
        step1_only_dir, 1,
        "CREATE TABLE step1 (id INTEGER PRIMARY KEY, name TEXT NOT NULL);",
    )

    migs_dir = tmp_path / "migrations_full"
    migs_dir.mkdir()
    _write_fake_migration(
        migs_dir, 1,
        "CREATE TABLE step1 (id INTEGER PRIMARY KEY, name TEXT NOT NULL);",
    )
    _write_fake_migration(
        migs_dir, 2,
        "CREATE TABLE step2 (id INTEGER PRIMARY KEY, note TEXT);\n"
        "CREATE INDEX idx_step2_note ON step2(note);",
    )

    db = tmp_path / "x.sqlite"
    # First bring DB up to step 1 so only step 2 is pending against the
    # full dir.
    mig.migrate(db, migrations_dir=step1_only_dir)
    with connect(db) as conn:
        assert int(conn.execute("PRAGMA user_version").fetchone()[0]) == 1

    # Sabotage: intercept datetime.now() in the migrate namespace. It is
    # evaluated once per pending migration to build the _migrations INSERT
    # row -- which is the gap TEST-C-001 is targeting (between DDL and
    # bookkeeping). Raising there simulates a kill -9 at that exact point.
    from datetime import UTC
    from datetime import datetime as real_datetime

    class _Tripwire:
        @staticmethod
        def now(tz: object = None) -> object:
            raise _InjectedCrashError(
                "simulated crash between DDL and bookkeeping INSERT"
            )

    # Keep UTC etc. available; only override `.now`.
    class _Shim:
        now = _Tripwire.now

    monkeypatch.setattr(mig, "datetime", _Shim)

    with pytest.raises(_InjectedCrashError):
        mig.migrate(db, migrations_dir=migs_dir)

    # Remove the tripwire and retry -- the runner must recover cleanly.
    monkeypatch.setattr(mig, "datetime", real_datetime)
    mig.migrate(db, migrations_dir=migs_dir)

    # Both tables must exist and the version must be 2.
    with connect(db) as conn:
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        step2_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='step2'"
        ).fetchone() is not None
        index_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' "
            "AND name='idx_step2_note'"
        ).fetchone() is not None
    assert version == 2, f"user_version stuck at {version} after recovery"
    assert step2_exists, "step2 table missing after recovery"
    assert index_exists, "idx_step2_note missing after recovery"

    # sentinel not used; suppress linter
    del UTC


# ---------------------------------------------------------------------------
# _write_backup — connection lifecycle (Wave-3 TEST-M-003)
# ---------------------------------------------------------------------------


def test_write_backup_closes_both_sqlite_connections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`sqlite3.connect` used as a context manager commits-but-does-not-close.
    `_write_backup` opens two connections per call; without explicit .close()
    each call leaks two file descriptors. This asserts every connection the
    helper creates is closed by the time it returns.
    """
    from langusta.db import migrate as migrate_module

    # Seed a DB with data first, so _write_backup has something to snapshot,
    # without any of the seeding connections polluting our tracker.
    db = tmp_path / "src.sqlite"
    migrate(db)
    with connect(db) as conn:
        conn.execute("CREATE TABLE t (id INTEGER)")
        conn.execute("INSERT INTO t VALUES (1)")

    tracked: list[sqlite3.Connection] = []
    real_connect = sqlite3.connect

    def tracking_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        conn = real_connect(*args, **kwargs)  # type: ignore[arg-type]
        tracked.append(conn)
        return conn

    monkeypatch.setattr(migrate_module.sqlite3, "connect", tracking_connect)

    migrate_module._write_backup(db, tmp_path / "backups", current_version=1)

    assert len(tracked) == 2, (
        f"expected _write_backup to open 2 sqlite connections, got {len(tracked)}"
    )
    for idx, leaked in enumerate(tracked):
        with pytest.raises(sqlite3.ProgrammingError):
            leaked.execute("SELECT 1")
        # Quiet the test output; a genuinely-closed connection is idempotent.
        leaked.close()
        del idx


def test_migrate_refuses_when_db_ahead_of_code(tmp_path: Path) -> None:
    """A DB at user_version N+1 with no code at that level = the binary was
    downgraded. Refuse rather than guess."""
    db = tmp_path / "ahead.sqlite"
    migrate(db)
    with connect(db) as conn:
        conn.execute(f"PRAGMA user_version = {latest_schema_version() + 1}")
    with pytest.raises(RuntimeError) as excinfo:
        migrate(db)
    assert "ahead" in str(excinfo.value).lower() or "downgrade" in str(excinfo.value).lower()
