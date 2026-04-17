"""TimelineWriter — the single atomic write path for scan observations.

Spec: docs/specs/01-functionality-and-moscow.md §4 Pillar B.
ADR: 0001 (single DAL entry point per aggregate).

Every scan observation flows through `apply_scan_observation`. It resolves
the observation via `core.identity.resolve`, then writes the DB atomically:

  - Insert     → new asset row, field_provenance rows (SCANNED), MAC
                 binding, timeline 'system' entry ("asset discovered").
  - Update     → apply `merge_scan_result` outputs: modified fields refresh
                 with SCANNED provenance, last_seen bumps, one timeline
                 'scan_diff' entry. Conflicts against MANUAL/IMPORTED fields
                 become proposed_changes rows.
  - Ambiguous  → review_queue row, no asset mutation.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from langusta.core.provenance import FieldProvenance
from langusta.db import assets as assets_dal
from langusta.db import proposed_changes as pc_dal
from langusta.db import scans as scans_dal
from langusta.db.connection import connect
from langusta.db.migrate import migrate
from langusta.db.writer import (
    Deferred,
    Inserted,
    Observation,
    Updated,
    apply_scan_observation,
)

NOW = datetime(2026, 4, 17, 12, 0, 0, tzinfo=UTC)
LATER = NOW + timedelta(hours=1)


@pytest.fixture
def scan_ctx(tmp_path: Path) -> tuple[Path, int]:
    db = tmp_path / "tl.sqlite"
    migrate(db)
    with connect(db) as conn:
        sid = scans_dal.start_scan(conn, target="10.0.0.0/30", now=NOW)
    return db, sid


# ---------------------------------------------------------------------------
# Insert — observation of a new host
# ---------------------------------------------------------------------------


def test_insert_creates_asset_with_scanned_provenance(scan_ctx) -> None:
    db, sid = scan_ctx
    obs = Observation(primary_ip="10.0.0.1", mac="aa:bb:cc:dd:ee:ff")
    with connect(db) as conn:
        result = apply_scan_observation(conn, obs, scan_id=sid, now=NOW)
    assert isinstance(result, Inserted)

    with connect(db) as conn:
        asset = assets_dal.get_by_id(conn, result.asset_id)
        prov = assets_dal.get_provenance(conn, result.asset_id)
    assert asset is not None
    assert asset.primary_ip == "10.0.0.1"
    assert asset.macs == ["aa:bb:cc:dd:ee:ff"]
    assert asset.source == "scanned"
    assert asset.first_seen == NOW
    assert asset.last_seen == NOW
    assert prov["primary_ip"].provenance is FieldProvenance.SCANNED


def test_insert_writes_system_timeline_entry(scan_ctx) -> None:
    db, sid = scan_ctx
    obs = Observation(primary_ip="10.0.0.1")
    with connect(db) as conn:
        result = apply_scan_observation(conn, obs, scan_id=sid, now=NOW)
        rows = conn.execute(
            "SELECT kind, body, author FROM timeline_entries "
            "WHERE asset_id = ? ORDER BY id",
            (result.asset_id,),
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["kind"] == "system"
    assert "discovered" in rows[0]["body"].lower()
    assert rows[0]["author"] == "scanner"


# ---------------------------------------------------------------------------
# Update — idempotent rescan
# ---------------------------------------------------------------------------


def test_rescan_same_values_refreshes_last_seen_and_writes_no_diff(scan_ctx) -> None:
    db, sid = scan_ctx
    obs = Observation(primary_ip="10.0.0.1", mac="aa:bb:cc:dd:ee:ff", hostname="r")
    with connect(db) as conn:
        first = apply_scan_observation(conn, obs, scan_id=sid, now=NOW)

    with connect(db) as conn:
        sid2 = scans_dal.start_scan(conn, target="10.0.0.0/30", now=LATER)
        second = apply_scan_observation(conn, obs, scan_id=sid2, now=LATER)

    assert isinstance(first, Inserted)
    assert isinstance(second, Updated)
    assert second.asset_id == first.asset_id
    assert second.applied_fields == ()  # nothing changed
    assert second.proposed_changes == 0

    with connect(db) as conn:
        asset = assets_dal.get_by_id(conn, second.asset_id)
        count = conn.execute(
            "SELECT COUNT(*) FROM timeline_entries WHERE asset_id = ?",
            (second.asset_id,),
        ).fetchone()[0]
    assert asset is not None
    assert asset.last_seen == LATER
    assert asset.first_seen == NOW
    # One 'system' entry from the insert; no scan_diff because nothing changed.
    assert count == 1


def test_rescan_with_changed_scanned_field_writes_scan_diff_entry(scan_ctx) -> None:
    db, sid = scan_ctx
    obs1 = Observation(primary_ip="10.0.0.1", hostname="old-name")
    with connect(db) as conn:
        inserted = apply_scan_observation(conn, obs1, scan_id=sid, now=NOW)

    obs2 = Observation(primary_ip="10.0.0.1", hostname="new-name",
                       mac="aa:bb:cc:dd:ee:ff")
    with connect(db) as conn:
        sid2 = scans_dal.start_scan(conn, target="10.0.0.0/30", now=LATER)
        result = apply_scan_observation(conn, obs2, scan_id=sid2, now=LATER)

    assert isinstance(result, Updated)
    assert result.asset_id == inserted.asset_id
    assert "hostname" in result.applied_fields
    # The MAC is new signal too; it lands as a new mac_addresses row.

    with connect(db) as conn:
        asset = assets_dal.get_by_id(conn, result.asset_id)
        rows = conn.execute(
            "SELECT kind, body FROM timeline_entries "
            "WHERE asset_id = ? ORDER BY id",
            (result.asset_id,),
        ).fetchall()
    assert asset is not None
    assert asset.hostname == "new-name"
    assert "aa:bb:cc:dd:ee:ff" in asset.macs
    kinds = [r["kind"] for r in rows]
    assert "scan_diff" in kinds


# ---------------------------------------------------------------------------
# Scanner-never-overwrites-manual invariant (integration check)
# ---------------------------------------------------------------------------


def test_scan_against_manual_field_creates_proposed_change_not_update(scan_ctx) -> None:
    db, _ = scan_ctx
    # 1. Human creates asset with manual hostname.
    with connect(db) as conn:
        aid = assets_dal.insert_manual(
            conn, hostname="set-by-human", primary_ip="10.0.0.1",
            mac="aa:bb:cc:dd:ee:ff", now=NOW,
        )

    # 2. Scan observes a different hostname on the same MAC.
    obs = Observation(primary_ip="10.0.0.1", hostname="scanner-guess",
                      mac="aa:bb:cc:dd:ee:ff")
    with connect(db) as conn:
        sid2 = scans_dal.start_scan(conn, target="10.0.0.0/30", now=LATER)
        result = apply_scan_observation(conn, obs, scan_id=sid2, now=LATER)

    assert isinstance(result, Updated)
    assert result.asset_id == aid
    # Conflict must surface as a proposed_change, NOT a silent overwrite.
    assert result.proposed_changes >= 1
    assert "hostname" not in result.applied_fields

    with connect(db) as conn:
        asset = assets_dal.get_by_id(conn, aid)
        prov = assets_dal.get_provenance(conn, aid)
        open_pcs = pc_dal.list_open(conn, asset_id=aid)
    assert asset is not None
    assert asset.hostname == "set-by-human"   # UNCHANGED
    assert prov["hostname"].provenance is FieldProvenance.MANUAL
    assert [p.field for p in open_pcs] == ["hostname"]
    assert open_pcs[0].proposed_value == "scanner-guess"


# ---------------------------------------------------------------------------
# Ambiguous — review queue path
# ---------------------------------------------------------------------------


def test_ambiguous_resolution_writes_review_queue_row(scan_ctx) -> None:
    """Seed two assets whose signals overlap the candidate. Resolver returns
    Ambiguous; TimelineWriter must deposit a review_queue row and NOT modify
    either asset."""
    db, _ = scan_ctx
    with connect(db) as conn:
        a1 = assets_dal.insert_manual(
            conn, hostname="alpha", primary_ip="10.0.0.1",
            mac="aa:bb:cc:dd:ee:ff", now=NOW,
        )
        a2 = assets_dal.insert_manual(
            conn, hostname="bravo", primary_ip="10.0.0.2",
            mac="11:22:33:44:55:66", now=NOW,
        )

    # Observation: MAC from a1, hostname from a2 — Lansweeper-failure case.
    obs = Observation(primary_ip="10.0.0.3",
                      hostname="bravo",
                      mac="aa:bb:cc:dd:ee:ff")
    with connect(db) as conn:
        sid2 = scans_dal.start_scan(conn, target="10.0.0.0/30", now=LATER)
        result = apply_scan_observation(conn, obs, scan_id=sid2, now=LATER)

    assert isinstance(result, Deferred)

    with connect(db) as conn:
        row = conn.execute(
            "SELECT id, observation, candidates, resolution "
            "FROM review_queue WHERE id = ?",
            (result.review_id,),
        ).fetchone()
    assert row is not None
    assert row["resolution"] is None

    # Neither existing asset was modified.
    with connect(db) as conn:
        a1_now = assets_dal.get_by_id(conn, a1)
        a2_now = assets_dal.get_by_id(conn, a2)
    assert a1_now is not None and a1_now.hostname == "alpha"
    assert a2_now is not None and a2_now.hostname == "bravo"
