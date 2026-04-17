"""Timeline immutability invariant — enforced at the SQL layer.

Spec: docs/specs/01-functionality-and-moscow.md §4 Pillar D; §8.
ADR: docs/adr/0005-schema-migration-discipline.md (institutional memory).

Users can add new timeline entries; they CANNOT edit or delete existing
entries. Corrections are new entries that reference the original. This is the
product promise and must hold even against a user who bypasses the DAL and
runs raw SQL against the SQLite file.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from langusta.db.connection import connect
from langusta.db.migrate import migrate


@pytest.fixture
def seeded_asset(tmp_path: Path) -> tuple[Path, int]:
    """Return (db_path, asset_id) with one asset and one timeline entry."""
    db = tmp_path / "timeline.sqlite"
    migrate(db)
    with connect(db) as conn:
        cur = conn.execute(
            "INSERT INTO assets (primary_ip, first_seen, last_seen, source) "
            "VALUES ('10.0.0.1', '2026-04-01T00:00:00Z', '2026-04-01T00:00:00Z', 'manual') "
            "RETURNING id"
        )
        asset_id = cur.fetchone()[0]
        conn.execute(
            "INSERT INTO timeline_entries (asset_id, kind, body, occurred_at) "
            "VALUES (?, 'note', 'first entry', '2026-04-01T00:00:00Z')",
            (asset_id,),
        )
    return db, asset_id


def test_insert_new_timeline_entry_succeeds(seeded_asset: tuple[Path, int]) -> None:
    db, asset_id = seeded_asset
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO timeline_entries (asset_id, kind, body, occurred_at) "
            "VALUES (?, 'note', 'second entry', '2026-04-02T00:00:00Z')",
            (asset_id,),
        )
        rows = conn.execute(
            "SELECT COUNT(*) FROM timeline_entries WHERE asset_id = ?", (asset_id,)
        ).fetchone()
    assert rows[0] == 2


def test_update_on_timeline_entries_is_rejected(seeded_asset: tuple[Path, int]) -> None:
    db, _ = seeded_asset
    with connect(db) as conn, pytest.raises(sqlite3.IntegrityError) as excinfo:
        conn.execute("UPDATE timeline_entries SET body = 'tampered'")
    assert "immutable" in str(excinfo.value).lower()


def test_delete_on_timeline_entries_is_rejected(seeded_asset: tuple[Path, int]) -> None:
    db, _ = seeded_asset
    with connect(db) as conn, pytest.raises(sqlite3.IntegrityError) as excinfo:
        conn.execute("DELETE FROM timeline_entries")
    assert "immutable" in str(excinfo.value).lower()


def test_update_rejected_even_when_addressed_to_one_row_by_id(
    seeded_asset: tuple[Path, int],
) -> None:
    db, asset_id = seeded_asset
    with connect(db) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE timeline_entries SET body = 'tampered' WHERE asset_id = ?",
            (asset_id,),
        )


def test_correction_entry_points_to_original(seeded_asset: tuple[Path, int]) -> None:
    """The sanctioned way to 'fix' an entry: insert a new correction entry
    that carries a `corrects_id` pointer to the original."""
    db, asset_id = seeded_asset
    with connect(db) as conn:
        original_id = conn.execute(
            "SELECT id FROM timeline_entries WHERE asset_id = ?", (asset_id,)
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO timeline_entries "
            "(asset_id, kind, body, occurred_at, corrects_id) "
            "VALUES (?, 'correction', 'the previous entry was wrong', "
            "'2026-04-02T00:00:00Z', ?)",
            (asset_id, original_id),
        )
        rows = conn.execute(
            "SELECT kind, corrects_id FROM timeline_entries "
            "WHERE asset_id = ? ORDER BY id", (asset_id,)
        ).fetchall()
    assert [(r["kind"], r["corrects_id"]) for r in rows] == [
        ("note", None),
        ("correction", original_id),
    ]
