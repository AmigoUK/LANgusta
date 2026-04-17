"""Tests for db/proposed_changes.py — the review queue's field-conflict table.

When a scan observes a value that would overwrite a `manual`- or `imported`-
provenance field, the observation lands here instead of mutating the asset.
The human resolves each row via M4's review screen; v1 ships CLI resolution
via `langusta review`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from langusta.core.provenance import FieldProvenance
from langusta.db import assets as assets_dal
from langusta.db import proposed_changes as pc_dal
from langusta.db import scans as scans_dal
from langusta.db.connection import connect
from langusta.db.migrate import migrate

NOW = datetime(2026, 4, 17, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def seeded_db(tmp_path: Path) -> tuple[Path, int, int]:
    """Return (db_path, asset_id, scan_id) with an asset and a scan ready."""
    db = tmp_path / "pc.sqlite"
    migrate(db)
    with connect(db) as conn:
        aid = assets_dal.insert_manual(
            conn, hostname="router", primary_ip="10.0.0.1", now=NOW,
        )
        sid = scans_dal.start_scan(conn, target="10.0.0.0/30", now=NOW)
    return db, aid, sid


# ---------------------------------------------------------------------------
# insert
# ---------------------------------------------------------------------------


def test_insert_returns_new_id(seeded_db) -> None:
    db, aid, sid = seeded_db
    with connect(db) as conn:
        pc_id = pc_dal.insert(
            conn,
            asset_id=aid,
            field="hostname",
            current_value="router",
            current_provenance=FieldProvenance.MANUAL,
            proposed_value="scanner-guess",
            observed_at=NOW,
            scan_id=sid,
        )
    assert isinstance(pc_id, int) and pc_id > 0


def test_insert_stores_all_fields(seeded_db) -> None:
    db, aid, sid = seeded_db
    with connect(db) as conn:
        pc_id = pc_dal.insert(
            conn,
            asset_id=aid,
            field="hostname",
            current_value="router",
            current_provenance=FieldProvenance.MANUAL,
            proposed_value="scanner-guess",
            observed_at=NOW,
            scan_id=sid,
        )
        row = pc_dal.get_by_id(conn, pc_id)
    assert row is not None
    assert row.asset_id == aid
    assert row.field == "hostname"
    assert row.current_value == "router"
    assert row.current_provenance is FieldProvenance.MANUAL
    assert row.proposed_value == "scanner-guess"
    assert row.observed_at == NOW
    assert row.scan_id == sid
    assert row.resolution is None


# ---------------------------------------------------------------------------
# list_open — the review queue front door
# ---------------------------------------------------------------------------


def test_list_open_returns_unresolved_only(seeded_db) -> None:
    db, aid, sid = seeded_db
    with connect(db) as conn:
        a = pc_dal.insert(
            conn, asset_id=aid, field="hostname",
            current_value="a", current_provenance=FieldProvenance.MANUAL,
            proposed_value="b", observed_at=NOW, scan_id=sid,
        )
        b = pc_dal.insert(
            conn, asset_id=aid, field="description",
            current_value="c", current_provenance=FieldProvenance.MANUAL,
            proposed_value="d", observed_at=NOW, scan_id=sid,
        )
        pc_dal.reject(conn, b, now=NOW)
        open_rows = pc_dal.list_open(conn)
    assert [r.id for r in open_rows] == [a]


def test_list_open_filter_by_asset_id(seeded_db) -> None:
    db, aid, sid = seeded_db
    with connect(db) as conn:
        other = assets_dal.insert_manual(conn, hostname="b", now=NOW)
        my_pc = pc_dal.insert(
            conn, asset_id=aid, field="hostname",
            current_value="x", current_provenance=FieldProvenance.MANUAL,
            proposed_value="y", observed_at=NOW, scan_id=sid,
        )
        pc_dal.insert(
            conn, asset_id=other, field="hostname",
            current_value="p", current_provenance=FieldProvenance.MANUAL,
            proposed_value="q", observed_at=NOW, scan_id=sid,
        )
        mine = pc_dal.list_open(conn, asset_id=aid)
    assert [r.id for r in mine] == [my_pc]


# ---------------------------------------------------------------------------
# accept / reject / edit_override
# ---------------------------------------------------------------------------


def test_accept_applies_proposed_value_and_flips_provenance(seeded_db) -> None:
    db, aid, sid = seeded_db
    with connect(db) as conn:
        pc_id = pc_dal.insert(
            conn, asset_id=aid, field="hostname",
            current_value="router", current_provenance=FieldProvenance.MANUAL,
            proposed_value="scanner-guess", observed_at=NOW, scan_id=sid,
        )
        pc_dal.accept(conn, pc_id, now=NOW)
        asset = assets_dal.get_by_id(conn, aid)
        prov = assets_dal.get_provenance(conn, aid)
    assert asset is not None
    assert asset.hostname == "scanner-guess"
    assert prov["hostname"].provenance is FieldProvenance.SCANNED


def test_reject_keeps_original_value(seeded_db) -> None:
    db, aid, sid = seeded_db
    with connect(db) as conn:
        pc_id = pc_dal.insert(
            conn, asset_id=aid, field="hostname",
            current_value="router", current_provenance=FieldProvenance.MANUAL,
            proposed_value="scanner-guess", observed_at=NOW, scan_id=sid,
        )
        pc_dal.reject(conn, pc_id, now=NOW)
        asset = assets_dal.get_by_id(conn, aid)
        prov = assets_dal.get_provenance(conn, aid)
    assert asset is not None
    assert asset.hostname == "router"
    assert prov["hostname"].provenance is FieldProvenance.MANUAL


def test_resolution_is_recorded(seeded_db) -> None:
    db, aid, sid = seeded_db
    with connect(db) as conn:
        pc_id = pc_dal.insert(
            conn, asset_id=aid, field="description",
            current_value="old", current_provenance=FieldProvenance.MANUAL,
            proposed_value="new", observed_at=NOW, scan_id=sid,
        )
        pc_dal.accept(conn, pc_id, now=NOW)
        row = pc_dal.get_by_id(conn, pc_id)
    assert row is not None
    assert row.resolution == "accepted"
    assert row.resolved_at == NOW


# ---------------------------------------------------------------------------
# Disposition timeline entries (M4)
# ---------------------------------------------------------------------------


def test_accept_writes_disposition_timeline_entry(seeded_db) -> None:
    from langusta.db import timeline as tl_dal

    db, aid, sid = seeded_db
    with connect(db) as conn:
        pc_id = pc_dal.insert(
            conn, asset_id=aid, field="hostname",
            current_value="router", current_provenance=FieldProvenance.MANUAL,
            proposed_value="scanner-guess", observed_at=NOW, scan_id=sid,
        )
        pc_dal.accept(conn, pc_id, now=NOW)
        entries = tl_dal.list_by_asset(conn, aid)
    disp = [e for e in entries if e.kind == "disposition"]
    assert len(disp) == 1
    assert "accepted" in disp[0].body.lower()
    assert "hostname" in disp[0].body
    assert "scanner-guess" in disp[0].body


def test_reject_writes_disposition_timeline_entry(seeded_db) -> None:
    from langusta.db import timeline as tl_dal

    db, aid, sid = seeded_db
    with connect(db) as conn:
        pc_id = pc_dal.insert(
            conn, asset_id=aid, field="hostname",
            current_value="router", current_provenance=FieldProvenance.MANUAL,
            proposed_value="scanner-guess", observed_at=NOW, scan_id=sid,
        )
        pc_dal.reject(conn, pc_id, now=NOW)
        entries = tl_dal.list_by_asset(conn, aid)
    disp = [e for e in entries if e.kind == "disposition"]
    assert len(disp) == 1
    assert "rejected" in disp[0].body.lower()


def test_edit_writes_disposition_with_override_value(seeded_db) -> None:
    from langusta.db import timeline as tl_dal

    db, aid, sid = seeded_db
    with connect(db) as conn:
        pc_id = pc_dal.insert(
            conn, asset_id=aid, field="description",
            current_value="old", current_provenance=FieldProvenance.MANUAL,
            proposed_value="new", observed_at=NOW, scan_id=sid,
        )
        pc_dal.edit_override(conn, pc_id, value="my-override", now=NOW)
        entries = tl_dal.list_by_asset(conn, aid)
    disp = [e for e in entries if e.kind == "disposition"]
    assert len(disp) == 1
    assert "edited" in disp[0].body.lower() or "override" in disp[0].body.lower()
    assert "my-override" in disp[0].body


def test_accept_on_resolved_proposal_raises(seeded_db) -> None:
    db, aid, sid = seeded_db
    with connect(db) as conn:
        pc_id = pc_dal.insert(
            conn, asset_id=aid, field="description",
            current_value="old", current_provenance=FieldProvenance.MANUAL,
            proposed_value="new", observed_at=NOW, scan_id=sid,
        )
        pc_dal.accept(conn, pc_id, now=NOW)
        with pytest.raises(pc_dal.AlreadyResolvedError):
            pc_dal.accept(conn, pc_id, now=NOW)
