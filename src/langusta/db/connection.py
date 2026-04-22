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
def connect(
    path: DbPath, *, readonly: bool = False,
) -> Iterator[sqlite3.Connection]:
    """Open a SQLite connection with LANgusta pragmas and sqlite3.Row factory.

    `readonly=True` opts into two things:
      - `PRAGMA query_only = 1` rejects any write-attempting statement
        with "attempt to write a readonly database" rather than
        surprising the caller with side effects.
      - Skips the commit-on-exit dance. Commit is a no-op when no
        transaction is pending (the default LEGACY isolation mode's
        state on a SELECT-only block) but setting `query_only` makes
        the read-only intent structural. Wave-3 C-022.

    Default (`readonly=False`) preserves the original behaviour so the
    many existing write-path callers don't need auditing.
    """
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
        if readonly:
            conn.execute("PRAGMA query_only = 1")
        yield conn
        if not readonly:
            conn.commit()
    except Exception:
        if not readonly:
            conn.rollback()
        raise
    finally:
        conn.close()
