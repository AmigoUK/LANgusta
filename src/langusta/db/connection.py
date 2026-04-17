"""The sanctioned way to open a SQLite connection.

Every call site in LANgusta routes here. Pragmas are applied once, centrally,
so WAL + foreign_keys + busy_timeout + synchronous + temp_store can never
silently diverge between processes (TUI, scanner, monitor daemon).

Spec reference: docs/specs/02-tech-stack-and-architecture.md §3.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

DbPath = Path | str


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA temp_store = MEMORY")


@contextmanager
def connect(path: DbPath) -> Iterator[sqlite3.Connection]:
    """Open a SQLite connection with LANgusta pragmas and sqlite3.Row factory."""
    if path != ":memory:":
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        target: str = str(p)
    else:
        target = ":memory:"
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    try:
        _apply_pragmas(conn)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
