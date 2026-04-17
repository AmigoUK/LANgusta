"""Timeline DAL tests — the insert-only API that sits on top of
timeline_entries (whose SQL triggers already block UPDATE/DELETE).

Corrections are new rows that reference the original via `corrects_id`;
they are NOT edits.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from langusta.db import assets as assets_dal
from langusta.db import timeline as tl_dal
from langusta.db.connection import connect
from langusta.db.migrate import migrate

NOW = datetime(2026, 4, 17, 12, 0, 0, tzinfo=UTC)
LATER = NOW + timedelta(minutes=5)


@pytest.fixture
def seeded(tmp_path: Path) -> tuple[Path, int]:
    db = tmp_path / "tl.sqlite"
    migrate(db)
    with connect(db) as conn:
        aid = assets_dal.insert_manual(conn, hostname="r", primary_ip="10.0.0.1", now=NOW)
    return db, aid


# ---------------------------------------------------------------------------
# append_entry
# ---------------------------------------------------------------------------


def test_append_entry_returns_new_id(seeded) -> None:
    db, aid = seeded
    with connect(db) as conn:
        tid = tl_dal.append_entry(
            conn, asset_id=aid, kind="note", body="hello", now=NOW, author="alice",
        )
    assert isinstance(tid, int) and tid > 0


def test_append_entry_populates_all_columns(seeded) -> None:
    db, aid = seeded
    with connect(db) as conn:
        tid = tl_dal.append_entry(
            conn, asset_id=aid, kind="note", body="hello", now=NOW, author="alice",
        )
        row = tl_dal.get_by_id(conn, tid)
    assert row is not None
    assert row.asset_id == aid
    assert row.kind == "note"
    assert row.body == "hello"
    assert row.occurred_at == NOW
    assert row.author == "alice"
    assert row.corrects_id is None


def test_append_entry_rejects_unknown_kind(seeded) -> None:
    db, aid = seeded
    with connect(db) as conn, pytest.raises(tl_dal.InvalidTimelineKind):
        tl_dal.append_entry(
            conn, asset_id=aid, kind="banana", body="x", now=NOW, author="a",
        )


# ---------------------------------------------------------------------------
# append_correction_of — the ONLY sanctioned edit path
# ---------------------------------------------------------------------------


def test_append_correction_links_to_original(seeded) -> None:
    db, aid = seeded
    with connect(db) as conn:
        original = tl_dal.append_entry(
            conn, asset_id=aid, kind="note", body="wrong", now=NOW, author="alice",
        )
        corr = tl_dal.append_correction_of(
            conn, original_id=original, body="actually right", now=LATER, author="alice",
        )
        corr_row = tl_dal.get_by_id(conn, corr)
    assert corr_row is not None
    assert corr_row.kind == "correction"
    assert corr_row.corrects_id == original
    assert corr_row.body == "actually right"


def test_append_correction_of_missing_original_raises(seeded) -> None:
    db, _ = seeded
    with connect(db) as conn, pytest.raises(tl_dal.OriginalNotFound):
        tl_dal.append_correction_of(
            conn, original_id=999, body="x", now=NOW, author="a",
        )


def test_correction_inherits_asset_id(seeded) -> None:
    """The correction must reference the SAME asset as the original."""
    db, aid = seeded
    with connect(db) as conn:
        orig = tl_dal.append_entry(
            conn, asset_id=aid, kind="note", body="x", now=NOW, author="a",
        )
        corr = tl_dal.append_correction_of(
            conn, original_id=orig, body="y", now=LATER, author="a",
        )
        row = tl_dal.get_by_id(conn, corr)
    assert row is not None
    assert row.asset_id == aid


# ---------------------------------------------------------------------------
# list_by_asset — chronological ordering
# ---------------------------------------------------------------------------


def test_list_by_asset_returns_chronological(seeded) -> None:
    db, aid = seeded
    with connect(db) as conn:
        tl_dal.append_entry(
            conn, asset_id=aid, kind="note", body="first", now=NOW, author="a",
        )
        tl_dal.append_entry(
            conn, asset_id=aid, kind="note", body="second", now=LATER, author="a",
        )
        rows = tl_dal.list_by_asset(conn, aid)
    assert [r.body for r in rows] == ["first", "second"]


def test_list_by_asset_ties_broken_by_id_for_same_occurred_at(seeded) -> None:
    db, aid = seeded
    with connect(db) as conn:
        a = tl_dal.append_entry(
            conn, asset_id=aid, kind="note", body="a", now=NOW, author="x",
        )
        b = tl_dal.append_entry(
            conn, asset_id=aid, kind="note", body="b", now=NOW, author="x",
        )
        rows = tl_dal.list_by_asset(conn, aid)
    assert [r.id for r in rows] == [a, b]


def test_list_by_asset_filters_by_asset(seeded) -> None:
    db, aid = seeded
    with connect(db) as conn:
        other = assets_dal.insert_manual(conn, hostname="other", now=NOW)
        tl_dal.append_entry(
            conn, asset_id=aid, kind="note", body="mine", now=NOW, author="x",
        )
        tl_dal.append_entry(
            conn, asset_id=other, kind="note", body="theirs", now=NOW, author="x",
        )
        mine = tl_dal.list_by_asset(conn, aid)
    assert [r.body for r in mine] == ["mine"]


# ---------------------------------------------------------------------------
# The storage-layer invariant still holds (regression)
# ---------------------------------------------------------------------------


def test_update_still_rejected_by_trigger(seeded) -> None:
    """DAL has no update path; raw UPDATE still fails the SQL trigger."""
    import sqlite3

    db, aid = seeded
    with connect(db) as conn:
        tid = tl_dal.append_entry(
            conn, asset_id=aid, kind="note", body="x", now=NOW, author="a",
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE timeline_entries SET body='tampered' WHERE id=?", (tid,))
