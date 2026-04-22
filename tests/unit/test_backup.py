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


# ---------------------------------------------------------------------------
# Wave-3 TEST-C-003 — backup.write must not leak sqlite connections
# (batch-1 M-003 fixed this at the source; this locks the behaviour in).
# ---------------------------------------------------------------------------


def test_write_closes_both_sqlite_connections(
    seeded, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`sqlite3.connect` used as a context manager commits-but-does-not-
    close. `backup.write` opens two connections (src and dst) per call;
    without an explicit close each call leaks two file descriptors. The
    fix (batch-1 M-003 companion) wrapped both in contextlib.closing.
    This test asserts the contract directly so a future refactor can't
    silently revert it."""
    db, backups = seeded

    tracked: list[sqlite3.Connection] = []
    real_connect = sqlite3.connect

    def tracking_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        conn = real_connect(*args, **kwargs)  # type: ignore[arg-type]
        tracked.append(conn)
        return conn

    monkeypatch.setattr(backup_mod.sqlite3, "connect", tracking_connect)

    backup_mod.write(db, backups, now=NOW, dedupe_window_hours=0)

    assert len(tracked) == 2, (
        f"expected backup.write to open 2 sqlite connections, got {len(tracked)}"
    )
    for leaked in tracked:
        with pytest.raises(sqlite3.ProgrammingError):
            leaked.execute("SELECT 1")
        leaked.close()


def test_write_does_not_leak_descriptors_across_many_calls(
    seeded, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Belt-and-braces: count unique connection objects left alive after
    many `write` calls. Not using /proc/self/fd so this runs on any
    POSIX host without special privileges."""
    db, backups = seeded

    live_conns: list[sqlite3.Connection] = []
    real_connect = sqlite3.connect

    def tracking_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        conn = real_connect(*args, **kwargs)  # type: ignore[arg-type]
        # Probe whether the connection is still usable AFTER this call
        # returns; if backup.write properly closed them, they won't be.
        live_conns.append(conn)
        return conn

    monkeypatch.setattr(backup_mod.sqlite3, "connect", tracking_connect)

    for i in range(25):
        backup_mod.write(
            db, backups, now=NOW + timedelta(hours=i + 1),
            dedupe_window_hours=0,
        )

    still_alive = 0
    for c in live_conns:
        try:
            c.execute("SELECT 1")
            still_alive += 1
            c.close()
        except sqlite3.ProgrammingError:
            pass

    assert still_alive == 0, (
        f"{still_alive} of {len(live_conns)} sqlite connections left "
        "open after backup.write returned — fd leak"
    )
