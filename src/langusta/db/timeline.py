"""Timeline DAL — insert-only API over `timeline_entries`.

SQL triggers on the table already block UPDATE and DELETE; the DAL reflects
that by offering no such functions. Corrections are new rows referencing
an original via `corrects_id`.

Spec: docs/specs/01-functionality-and-moscow.md §4 Pillar D, §8.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

VALID_KINDS = frozenset({
    "note", "scan_diff", "monitor_event", "disposition",
    "correction", "import", "system",
})


class InvalidTimelineKind(ValueError):  # noqa: N818 — ValueError subclass, name ≠ "Error"
    """Caller passed a kind not in VALID_KINDS."""


class OriginalNotFound(LookupError):  # noqa: N818 — LookupError subclass
    """append_correction_of called with an original_id that doesn't exist."""


@dataclass(frozen=True, slots=True)
class TimelineEntry:
    id: int
    asset_id: int
    kind: str
    body: str
    occurred_at: datetime
    corrects_id: int | None
    author: str | None


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _row(row: sqlite3.Row) -> TimelineEntry:
    return TimelineEntry(
        id=int(row["id"]),
        asset_id=int(row["asset_id"]),
        kind=row["kind"],
        body=row["body"],
        occurred_at=datetime.fromisoformat(row["occurred_at"]),
        corrects_id=row["corrects_id"],
        author=row["author"],
    )


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def append_entry(
    conn: sqlite3.Connection,
    *,
    asset_id: int,
    kind: str,
    body: str,
    now: datetime,
    author: str | None = None,
    corrects_id: int | None = None,
) -> int:
    """Append a new timeline entry; return its id."""
    if kind not in VALID_KINDS:
        raise InvalidTimelineKind(
            f"unknown kind {kind!r}; valid kinds are {sorted(VALID_KINDS)}"
        )
    row = conn.execute(
        "INSERT INTO timeline_entries "
        "(asset_id, kind, body, occurred_at, author, corrects_id) "
        "VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
        (asset_id, kind, body, _iso(now), author, corrects_id),
    ).fetchone()
    return int(row[0])


def append_correction_of(
    conn: sqlite3.Connection,
    *,
    original_id: int,
    body: str,
    now: datetime,
    author: str | None = None,
) -> int:
    """Add a correction entry that references an existing original.

    The new row's `asset_id` is copied from the original so the correction
    surfaces on the same timeline.
    """
    row = conn.execute(
        "SELECT asset_id FROM timeline_entries WHERE id = ?", (original_id,),
    ).fetchone()
    if row is None:
        raise OriginalNotFound(f"timeline entry {original_id} not found")
    return append_entry(
        conn,
        asset_id=int(row["asset_id"]),
        kind="correction",
        body=body,
        now=now,
        author=author,
        corrects_id=original_id,
    )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


_COLS = "id, asset_id, kind, body, occurred_at, corrects_id, author"


def get_by_id(conn: sqlite3.Connection, entry_id: int) -> TimelineEntry | None:
    row = conn.execute(
        f"SELECT {_COLS} FROM timeline_entries WHERE id = ?", (entry_id,),
    ).fetchone()
    return _row(row) if row is not None else None


def list_by_asset(
    conn: sqlite3.Connection, asset_id: int, *, limit: int | None = None
) -> list[TimelineEntry]:
    """Return an asset's timeline in chronological order (id breaks ties)."""
    sql = (
        f"SELECT {_COLS} FROM timeline_entries "
        "WHERE asset_id = ? ORDER BY occurred_at ASC, id ASC"
    )
    params: tuple = (asset_id,)
    if limit is not None:
        sql += " LIMIT ?"
        params = (asset_id, limit)
    rows = conn.execute(sql, params).fetchall()
    return [_row(r) for r in rows]
