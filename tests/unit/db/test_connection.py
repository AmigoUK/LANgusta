"""Tests for the db/connection.py single-helper — pragma discipline (spec §3).

The `connect()` helper is the only sanctioned way to open a SQLite connection
in LANgusta. It must apply WAL, synchronous=NORMAL, foreign_keys=ON,
busy_timeout=5000, temp_store=MEMORY to *every* connection. Call sites must
not repeat pragma setup; if the helper regresses, everything regresses.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from langusta.db.connection import connect


def _pragma(conn, name: str):
    return conn.execute(f"PRAGMA {name}").fetchone()[0]


def test_connect_creates_parent_directory(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "dir" / "langusta.db"
    with connect(target) as conn:
        conn.execute("SELECT 1")
    assert target.exists()
    assert target.parent.is_dir()


def test_connect_sets_wal_mode(tmp_path: Path) -> None:
    with connect(tmp_path / "db.sqlite") as conn:
        assert _pragma(conn, "journal_mode") == "wal"


def test_connect_sets_foreign_keys_on(tmp_path: Path) -> None:
    with connect(tmp_path / "db.sqlite") as conn:
        assert _pragma(conn, "foreign_keys") == 1


def test_connect_sets_busy_timeout(tmp_path: Path) -> None:
    with connect(tmp_path / "db.sqlite") as conn:
        assert _pragma(conn, "busy_timeout") == 5000


def test_connect_sets_synchronous_normal(tmp_path: Path) -> None:
    # SQLite returns an int: 0=off, 1=normal, 2=full, 3=extra.
    with connect(tmp_path / "db.sqlite") as conn:
        assert _pragma(conn, "synchronous") == 1


def test_connect_sets_temp_store_memory(tmp_path: Path) -> None:
    # 0=default, 1=file, 2=memory.
    with connect(tmp_path / "db.sqlite") as conn:
        assert _pragma(conn, "temp_store") == 2


def test_connect_returns_row_factory_for_named_access(tmp_path: Path) -> None:
    with connect(tmp_path / "db.sqlite") as conn:
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO t (name) VALUES ('x')")
        row = conn.execute("SELECT id, name FROM t").fetchone()
    # sqlite3.Row supports both index and name access.
    assert row["name"] == "x"
    assert row[1] == "x"


def test_connect_in_memory_uses_shared_uri(tmp_path: Path) -> None:
    # ":memory:" is permitted for tests; pragmas still apply.
    with connect(":memory:") as conn:
        assert _pragma(conn, "foreign_keys") == 1


def test_connect_context_manager_closes_connection(tmp_path: Path) -> None:
    import sqlite3

    path = tmp_path / "db.sqlite"
    with connect(path) as conn:
        conn.execute("SELECT 1")
    # After exit, reusing the connection must fail (ProgrammingError on closed conn).
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")
