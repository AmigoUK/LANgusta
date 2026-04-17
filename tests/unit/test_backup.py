"""Backup module tests.

Spec: docs/specs/02-tech-stack-and-architecture.md §9.
ADR-0005: pre-migration backup is mandatory; post-scan backup is the
ongoing snapshot mechanism.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from langusta import backup as backup_mod
from langusta.db import assets as assets_dal
from langusta.db.connection import connect
from langusta.db.migrate import migrate

NOW = datetime(2026, 4, 17, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def seeded(tmp_path: Path) -> tuple[Path, Path]:
    """Return (db_path, backups_dir) with one asset already inserted."""
    db = tmp_path / "home" / ".langusta" / "db.sqlite"
    db.parent.mkdir(parents=True)
    migrate(db)
    with connect(db) as conn:
        assets_dal.insert_manual(conn, hostname="r", primary_ip="10.0.0.1", now=NOW)
    return db, tmp_path / "home" / ".langusta" / "backups"


# ---------------------------------------------------------------------------
# write
# ---------------------------------------------------------------------------


def test_write_creates_snapshot_file(seeded) -> None:
    db, backups = seeded
    path = backup_mod.write(db, backups, now=NOW)
    assert path is not None
    assert path.exists()
    assert path.parent == backups
    # Filename format: db-YYYYMMDDTHHMMSSZ.sqlite
    assert path.name.startswith("db-")
    assert path.name.endswith(".sqlite")


def test_write_backup_contains_same_data(seeded) -> None:
    db, backups = seeded
    path = backup_mod.write(db, backups, now=NOW)
    assert path is not None
    with sqlite3.connect(str(path)) as b:
        rows = b.execute("SELECT hostname FROM assets").fetchall()
    assert rows == [("r",)]


def test_write_dedup_within_1h_window(seeded) -> None:
    """A second write within the dedup window is a no-op (returns None)."""
    db, backups = seeded
    first = backup_mod.write(db, backups, now=NOW, dedupe_window_hours=1)
    soon = NOW + timedelta(minutes=30)
    second = backup_mod.write(db, backups, now=soon, dedupe_window_hours=1)
    assert first is not None
    assert second is None
    assert len(list(backups.glob("db-*.sqlite"))) == 1


def test_write_outside_dedup_window_creates_new_snapshot(seeded) -> None:
    db, backups = seeded
    backup_mod.write(db, backups, now=NOW, dedupe_window_hours=1)
    later = NOW + timedelta(hours=2)
    second = backup_mod.write(db, backups, now=later, dedupe_window_hours=1)
    assert second is not None
    assert len(list(backups.glob("db-*.sqlite"))) == 2


def test_write_missing_db_returns_none(tmp_path: Path) -> None:
    # Non-existent source — don't crash, just skip.
    backups = tmp_path / "backups"
    assert backup_mod.write(tmp_path / "does-not-exist.sqlite", backups, now=NOW) is None


# ---------------------------------------------------------------------------
# list_backups
# ---------------------------------------------------------------------------


def test_list_returns_snapshots_newest_first(seeded) -> None:
    db, backups = seeded
    a = backup_mod.write(db, backups, now=NOW, dedupe_window_hours=0)
    b = backup_mod.write(db, backups, now=NOW + timedelta(hours=1), dedupe_window_hours=0)
    c = backup_mod.write(db, backups, now=NOW + timedelta(hours=2), dedupe_window_hours=0)
    listed = backup_mod.list_backups(backups)
    assert [bp.path for bp in listed] == [c, b, a]


def test_list_empty_dir_returns_empty(tmp_path: Path) -> None:
    assert backup_mod.list_backups(tmp_path / "nope") == []


def test_list_ignores_non_backup_files(seeded) -> None:
    db, backups = seeded
    backup_mod.write(db, backups, now=NOW, dedupe_window_hours=0)
    (backups / "unrelated.txt").write_text("not a backup")
    listed = backup_mod.list_backups(backups)
    assert all(bp.path.suffix == ".sqlite" for bp in listed)


# ---------------------------------------------------------------------------
# prune
# ---------------------------------------------------------------------------


def test_prune_keeps_most_recent_n(seeded) -> None:
    db, backups = seeded
    for i in range(5):
        backup_mod.write(
            db, backups, now=NOW + timedelta(hours=i), dedupe_window_hours=0,
        )
    assert len(list(backups.glob("db-*.sqlite"))) == 5
    removed = backup_mod.prune(backups, keep=3)
    assert removed == 2
    assert len(list(backups.glob("db-*.sqlite"))) == 3


def test_prune_under_limit_is_noop(seeded) -> None:
    db, backups = seeded
    backup_mod.write(db, backups, now=NOW, dedupe_window_hours=0)
    assert backup_mod.prune(backups, keep=10) == 0


def test_prune_empty_dir_is_noop(tmp_path: Path) -> None:
    # Must not raise if dir doesn't exist.
    assert backup_mod.prune(tmp_path / "nope", keep=5) == 0


# ---------------------------------------------------------------------------
# verify (integrity check)
# ---------------------------------------------------------------------------


def test_verify_good_backup_returns_true(seeded) -> None:
    db, backups = seeded
    path = backup_mod.write(db, backups, now=NOW, dedupe_window_hours=0)
    assert path is not None
    assert backup_mod.verify(path) is True


def test_verify_corrupt_file_returns_false(seeded) -> None:
    db, backups = seeded
    path = backup_mod.write(db, backups, now=NOW, dedupe_window_hours=0)
    assert path is not None
    # Corrupt the file: truncate to half its size.
    path.write_bytes(path.read_bytes()[: path.stat().st_size // 2])
    assert backup_mod.verify(path) is False


def test_verify_nonexistent_file_returns_false(tmp_path: Path) -> None:
    assert backup_mod.verify(tmp_path / "nope.sqlite") is False
