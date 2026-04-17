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
