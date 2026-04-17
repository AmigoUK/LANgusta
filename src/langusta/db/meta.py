"""Meta key/value DAL — thin wrapper over the `meta` table.

Used for instance-level state that doesn't belong to any aggregate: vault
salt, vault verifier, daemon heartbeat (M7), export version pin (M6).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime


def get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return None if row is None else str(row["value"])


def set_value(
    conn: sqlite3.Connection, key: str, value: str, *, now: datetime
) -> None:
    conn.execute(
        "INSERT INTO meta (key, value, set_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
        "set_at = excluded.set_at",
        (key, value, now.isoformat(timespec="seconds")),
    )


def delete(conn: sqlite3.Connection, key: str) -> None:
    conn.execute("DELETE FROM meta WHERE key = ?", (key,))
