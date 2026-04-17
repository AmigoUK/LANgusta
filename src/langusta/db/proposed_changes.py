"""Proposed-changes DAL — the scanner-proposes-human-disposes queue.

When `core.provenance.merge_scan_result` declines to apply a scan observation
(because the field carries `manual` or `imported` provenance), the observation
is inserted here. The human resolves each row via `langusta review` (CLI in
M2) or the review-queue screen (M4 TUI).

Spec: docs/specs/01-functionality-and-moscow.md §4 Pillar A.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from langusta.core.provenance import FieldProvenance
from langusta.db import timeline as tl_dal


class AlreadyResolvedError(RuntimeError):
    """accept/reject/edit_override called on a row already resolved."""


@dataclass(frozen=True, slots=True)
class ProposedChangeRow:
    id: int
    asset_id: int
    field: str
    current_value: str | None
    current_provenance: FieldProvenance
    proposed_value: str | None
    observed_at: datetime
    scan_id: int | None
    resolution: str | None
    resolved_at: datetime | None
    resolved_override: str | None


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _parse_iso(raw: str | None) -> datetime | None:
    if raw is None:
        return None
    return datetime.fromisoformat(raw)


def _row_to_pc(row: sqlite3.Row) -> ProposedChangeRow:
    return ProposedChangeRow(
        id=int(row["id"]),
        asset_id=int(row["asset_id"]),
        field=row["field"],
        current_value=row["current_value"],
        current_provenance=FieldProvenance(row["current_provenance"]),
        proposed_value=row["proposed_value"],
        observed_at=datetime.fromisoformat(row["observed_at"]),
        scan_id=row["scan_id"],
        resolution=row["resolution"],
        resolved_at=_parse_iso(row["resolved_at"]),
        resolved_override=row["resolved_override"],
    )


# ---------------------------------------------------------------------------
# insert
# ---------------------------------------------------------------------------


def insert(
    conn: sqlite3.Connection,
    *,
    asset_id: int,
    field: str,
    current_value: str | None,
    current_provenance: FieldProvenance,
    proposed_value: str | None,
    observed_at: datetime,
    scan_id: int | None,
) -> int:
    if current_provenance not in (FieldProvenance.MANUAL, FieldProvenance.IMPORTED):
        raise ValueError(
            f"proposed_changes only accept protected provenances, got {current_provenance}"
        )
    row = conn.execute(
        "INSERT INTO proposed_changes ("
        "asset_id, field, current_value, current_provenance, "
        "proposed_value, observed_at, scan_id"
        ") VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id",
        (
            asset_id, field, current_value, current_provenance.value,
            proposed_value, _iso(observed_at), scan_id,
        ),
    ).fetchone()
    return int(row[0])


# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------


_COLS = (
    "id, asset_id, field, current_value, current_provenance, "
    "proposed_value, observed_at, scan_id, resolution, resolved_at, "
    "resolved_override"
)


def get_by_id(conn: sqlite3.Connection, pc_id: int) -> ProposedChangeRow | None:
    row = conn.execute(
        f"SELECT {_COLS} FROM proposed_changes WHERE id = ?", (pc_id,),
    ).fetchone()
    return _row_to_pc(row) if row is not None else None


def list_open(
    conn: sqlite3.Connection, *, asset_id: int | None = None
) -> list[ProposedChangeRow]:
    if asset_id is None:
        rows = conn.execute(
            f"SELECT {_COLS} FROM proposed_changes "
            "WHERE resolution IS NULL ORDER BY id"
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT {_COLS} FROM proposed_changes "
            "WHERE resolution IS NULL AND asset_id = ? ORDER BY id",
            (asset_id,),
        ).fetchall()
    return [_row_to_pc(r) for r in rows]


# ---------------------------------------------------------------------------
# resolution — accept / reject / edit
# ---------------------------------------------------------------------------


def _assert_open(conn: sqlite3.Connection, pc_id: int) -> ProposedChangeRow:
    row = get_by_id(conn, pc_id)
    if row is None:
        raise AlreadyResolvedError(f"proposed change {pc_id} not found")
    if row.resolution is not None:
        raise AlreadyResolvedError(
            f"proposed change {pc_id} already resolved: {row.resolution}"
        )
    return row


def accept(conn: sqlite3.Connection, pc_id: int, *, now: datetime) -> None:
    """Apply the proposed value to the asset and flip provenance to SCANNED.

    The human has explicitly chosen to let the scanner's observation win.
    """
    row = _assert_open(conn, pc_id)
    now_iso = _iso(now)
    # Apply the proposed value to the asset.
    conn.execute(
        f"UPDATE assets SET {row.field} = ?, last_seen = ? WHERE id = ?",
        (row.proposed_value, now_iso, row.asset_id),
    )
    # Flip the field's provenance to SCANNED.
    conn.execute(
        "INSERT INTO field_provenance (asset_id, field, provenance, set_at) "
        "VALUES (?, ?, 'scanned', ?) "
        "ON CONFLICT(asset_id, field) DO UPDATE SET "
        "provenance = 'scanned', set_at = excluded.set_at",
        (row.asset_id, row.field, now_iso),
    )
    conn.execute(
        "UPDATE proposed_changes SET resolution = 'accepted', resolved_at = ? "
        "WHERE id = ?",
        (now_iso, pc_id),
    )
    tl_dal.append_entry(
        conn,
        asset_id=row.asset_id,
        kind="disposition",
        body=(
            f"Accepted scanner proposal: {row.field} -> {row.proposed_value!r} "
            f"(was {row.current_value!r})"
        ),
        now=now,
        author="user",
    )


def reject(conn: sqlite3.Connection, pc_id: int, *, now: datetime) -> None:
    """Discard the proposal; asset is unchanged."""
    row = _assert_open(conn, pc_id)
    conn.execute(
        "UPDATE proposed_changes SET resolution = 'rejected', resolved_at = ? "
        "WHERE id = ?",
        (_iso(now), pc_id),
    )
    tl_dal.append_entry(
        conn,
        asset_id=row.asset_id,
        kind="disposition",
        body=(
            f"Rejected scanner proposal: {row.field} -> {row.proposed_value!r} "
            f"(kept {row.current_value!r})"
        ),
        now=now,
        author="user",
    )


def edit_override(
    conn: sqlite3.Connection, pc_id: int, *, value: str | None, now: datetime
) -> None:
    """User chose a third option — neither current nor proposed. Applied with
    MANUAL provenance so subsequent scans treat it as protected again."""
    row = _assert_open(conn, pc_id)
    now_iso = _iso(now)
    conn.execute(
        f"UPDATE assets SET {row.field} = ?, last_seen = ? WHERE id = ?",
        (value, now_iso, row.asset_id),
    )
    conn.execute(
        "INSERT INTO field_provenance (asset_id, field, provenance, set_at) "
        "VALUES (?, ?, 'manual', ?) "
        "ON CONFLICT(asset_id, field) DO UPDATE SET "
        "provenance = 'manual', set_at = excluded.set_at",
        (row.asset_id, row.field, now_iso),
    )
    conn.execute(
        "UPDATE proposed_changes SET resolution = 'edited', resolved_at = ?, "
        "resolved_override = ? WHERE id = ?",
        (now_iso, value, pc_id),
    )
    tl_dal.append_entry(
        conn,
        asset_id=row.asset_id,
        kind="disposition",
        body=(
            f"Edited scanner proposal: {row.field} override -> {value!r} "
            f"(rejected scanner guess {row.proposed_value!r}, was {row.current_value!r})"
        ),
        now=now,
        author="user",
    )
