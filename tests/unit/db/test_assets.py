"""Assets DAL tests — insert_manual / list_all / get_by_id.

The manual-insert path is the M1 vertical slice: human types field values,
DAL records every one with `manual` provenance and the current timestamp,
round-trips faithfully through list_all / get_by_id.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from langusta.core.models import Asset
from langusta.core.provenance import FieldProvenance
from langusta.db import assets as assets_dal
from langusta.db.connection import connect
from langusta.db.migrate import migrate

NOW = datetime(2026, 4, 17, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def fresh_db(tmp_path: Path) -> Path:
    db = tmp_path / "assets.sqlite"
    migrate(db)
    return db


# ---------------------------------------------------------------------------
# insert_manual — the M1 wedge
# ---------------------------------------------------------------------------


def test_insert_manual_returns_asset_id(fresh_db: Path) -> None:
    with connect(fresh_db) as conn:
        asset_id = assets_dal.insert_manual(
            conn,
            hostname="router",
            primary_ip="192.168.1.1",
            mac="aa:bb:cc:dd:ee:ff",
            now=NOW,
        )
    assert isinstance(asset_id, int) and asset_id > 0


def test_insert_manual_stores_fields(fresh_db: Path) -> None:
    with connect(fresh_db) as conn:
        aid = assets_dal.insert_manual(
            conn,
            hostname="router",
            primary_ip="192.168.1.1",
            mac="aa:bb:cc:dd:ee:ff",
            description="reception switch",
            now=NOW,
        )
        asset = assets_dal.get_by_id(conn, aid)
    assert asset is not None
    assert asset.hostname == "router"
    assert asset.primary_ip == "192.168.1.1"
    assert asset.description == "reception switch"
    assert asset.source == "manual"
    assert asset.first_seen == NOW
    assert asset.last_seen == NOW
    assert asset.macs == ["aa:bb:cc:dd:ee:ff"]


def test_insert_manual_records_field_provenance_as_manual(fresh_db: Path) -> None:
    with connect(fresh_db) as conn:
        aid = assets_dal.insert_manual(
            conn,
            hostname="router",
            primary_ip="192.168.1.1",
            mac="aa:bb:cc:dd:ee:ff",
            description="reception",
            now=NOW,
        )
        prov = assets_dal.get_provenance(conn, aid)
    # Every field that received a value gets a `manual` provenance row.
    assert prov["hostname"].provenance is FieldProvenance.MANUAL
    assert prov["primary_ip"].provenance is FieldProvenance.MANUAL
    assert prov["description"].provenance is FieldProvenance.MANUAL
    # set_at reflects the insertion time.
    assert prov["hostname"].set_at == NOW


def test_insert_manual_omits_provenance_for_unspecified_fields(fresh_db: Path) -> None:
    with connect(fresh_db) as conn:
        aid = assets_dal.insert_manual(
            conn,
            hostname="router",
            now=NOW,
        )
        prov = assets_dal.get_provenance(conn, aid)
    assert "hostname" in prov
    assert "primary_ip" not in prov
    assert "description" not in prov


def test_insert_manual_lowercases_mac(fresh_db: Path) -> None:
    with connect(fresh_db) as conn:
        aid = assets_dal.insert_manual(
            conn,
            hostname="r",
            mac="AA:BB:CC:DD:EE:FF",
            now=NOW,
        )
        asset = assets_dal.get_by_id(conn, aid)
    assert asset is not None
    assert asset.macs == ["aa:bb:cc:dd:ee:ff"]


def test_insert_manual_rejects_duplicate_mac(fresh_db: Path) -> None:
    """MAC is globally unique — second manual insert with same MAC raises."""
    with connect(fresh_db) as conn:
        assets_dal.insert_manual(conn, hostname="a", mac="aa:bb:cc:dd:ee:ff", now=NOW)
        with pytest.raises(assets_dal.DuplicateMacError) as excinfo:
            assets_dal.insert_manual(conn, hostname="b", mac="aa:bb:cc:dd:ee:ff", now=NOW)
    assert "aa:bb:cc:dd:ee:ff" in str(excinfo.value)


# ---------------------------------------------------------------------------
# list_all + get_by_id
# ---------------------------------------------------------------------------


def test_list_all_returns_empty_on_fresh_db(fresh_db: Path) -> None:
    with connect(fresh_db) as conn:
        rows = assets_dal.list_all(conn)
    assert rows == []


def test_list_all_returns_inserted_assets(fresh_db: Path) -> None:
    with connect(fresh_db) as conn:
        a1 = assets_dal.insert_manual(conn, hostname="a", primary_ip="10.0.0.1", now=NOW)
        a2 = assets_dal.insert_manual(conn, hostname="b", primary_ip="10.0.0.2", now=NOW)
        rows = assets_dal.list_all(conn)
    assert {r.id for r in rows} == {a1, a2}
    assert all(isinstance(r, Asset) for r in rows)


def test_list_all_orders_by_id_ascending(fresh_db: Path) -> None:
    with connect(fresh_db) as conn:
        ids = [
            assets_dal.insert_manual(conn, hostname=f"h{i}", now=NOW)
            for i in range(5)
        ]
        rows = assets_dal.list_all(conn)
    assert [r.id for r in rows] == sorted(ids)


def test_get_by_id_returns_none_for_missing(fresh_db: Path) -> None:
    with connect(fresh_db) as conn:
        assert assets_dal.get_by_id(conn, 999) is None


def test_get_by_id_aggregates_multiple_macs(fresh_db: Path) -> None:
    """If an asset has many MACs (future M3 path), get_by_id should return
    them all sorted. M1 only inserts one MAC per call, so simulate directly."""
    with connect(fresh_db) as conn:
        aid = assets_dal.insert_manual(
            conn, hostname="dualnic", mac="aa:bb:cc:00:00:01", now=NOW,
        )
        # Add a second MAC directly through DAL helper.
        assets_dal._insert_mac(conn, aid, "aa:bb:cc:00:00:02", now=NOW)
        asset = assets_dal.get_by_id(conn, aid)
    assert asset is not None
    assert asset.macs == ["aa:bb:cc:00:00:01", "aa:bb:cc:00:00:02"]
