"""Lansweeper CSV import tests.

Spec §7 Should-Have: "Import from Lansweeper CSV export — this is the
migration on-ramp and directly targets the two biggest user-flight
populations."

Fields we pull from the Lansweeper CSV export:
  AssetName    -> hostname
  IPAddress    -> primary_ip
  Mac          -> mac
  Description  -> description
  Manufacturer -> vendor
  Model        -> device_type (best-effort; preferred when Type is blank)
  Type         -> device_type (if present)

Imported fields get provenance 'imported' — subsequent scans can't
silently overwrite them (they become proposed_changes if a conflict
arises, same as manual fields).
"""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path

import pytest

from langusta.core.provenance import FieldProvenance
from langusta.db import assets as assets_dal
from langusta.db.connection import connect
from langusta.db.import_lansweeper import (
    ImportReport,
    import_lansweeper_csv,
)
from langusta.db.migrate import migrate

NOW = datetime(2026, 4, 17, 12, 0, 0, tzinfo=UTC)


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    if not rows:
        path.write_text("AssetName,IPAddress,Mac,Description\n")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    p = tmp_path / "import.sqlite"
    migrate(p)
    return p


# ---------------------------------------------------------------------------
# Basic happy path
# ---------------------------------------------------------------------------


def test_imports_rows_with_imported_provenance(db: Path, tmp_path: Path) -> None:
    csv_path = tmp_path / "ls.csv"
    _write_csv(csv_path, [
        {
            "AssetName": "ls-router-01",
            "IPAddress": "192.168.1.1",
            "Mac": "AA:BB:CC:DD:EE:FF",
            "Description": "Core router",
            "Manufacturer": "Cisco",
            "Model": "C3750",
        },
    ])
    with connect(db) as conn:
        report = import_lansweeper_csv(conn, csv_path=csv_path, now=NOW)
        rows = assets_dal.list_all(conn)

    assert isinstance(report, ImportReport)
    assert report.imported == 1
    assert report.skipped == 0
    assert len(rows) == 1

    asset = rows[0]
    assert asset.hostname == "ls-router-01"
    assert asset.primary_ip == "192.168.1.1"
    assert asset.macs == ["aa:bb:cc:dd:ee:ff"]
    assert asset.description == "Core router"
    assert asset.vendor == "Cisco"
    assert asset.source == "imported"

    with connect(db) as conn:
        prov = assets_dal.get_provenance(conn, asset.id)
    assert prov["hostname"].provenance is FieldProvenance.IMPORTED
    assert prov["primary_ip"].provenance is FieldProvenance.IMPORTED
    assert prov["description"].provenance is FieldProvenance.IMPORTED
    assert prov["vendor"].provenance is FieldProvenance.IMPORTED


def test_imports_multiple_rows(db: Path, tmp_path: Path) -> None:
    csv_path = tmp_path / "ls.csv"
    _write_csv(csv_path, [
        {"AssetName": "a", "IPAddress": "10.0.0.1", "Mac": "aa:bb:cc:00:00:01"},
        {"AssetName": "b", "IPAddress": "10.0.0.2", "Mac": "aa:bb:cc:00:00:02"},
        {"AssetName": "c", "IPAddress": "10.0.0.3", "Mac": "aa:bb:cc:00:00:03"},
    ])
    with connect(db) as conn:
        report = import_lansweeper_csv(conn, csv_path=csv_path, now=NOW)
        rows = assets_dal.list_all(conn)
    assert report.imported == 3
    assert {r.hostname for r in rows} == {"a", "b", "c"}


def test_type_column_fills_device_type(db: Path, tmp_path: Path) -> None:
    csv_path = tmp_path / "ls.csv"
    _write_csv(csv_path, [
        {
            "AssetName": "printer-01",
            "IPAddress": "10.0.0.5",
            "Mac": "aa:bb:cc:00:00:05",
            "Type": "Printer",
        },
    ])
    with connect(db) as conn:
        import_lansweeper_csv(conn, csv_path=csv_path, now=NOW)
        [asset] = assets_dal.list_all(conn)
    assert asset.device_type == "Printer"


# ---------------------------------------------------------------------------
# Idempotent / collision handling
# ---------------------------------------------------------------------------


def test_duplicate_mac_skipped_not_crash(db: Path, tmp_path: Path) -> None:
    """Re-importing the same CSV twice must not duplicate assets or crash."""
    csv_path = tmp_path / "ls.csv"
    _write_csv(csv_path, [
        {"AssetName": "a", "IPAddress": "10.0.0.1", "Mac": "aa:bb:cc:00:00:01"},
    ])
    with connect(db) as conn:
        first = import_lansweeper_csv(conn, csv_path=csv_path, now=NOW)
        second = import_lansweeper_csv(conn, csv_path=csv_path, now=NOW)
        rows = assets_dal.list_all(conn)
    assert first.imported == 1
    assert second.imported == 0
    assert second.skipped == 1
    assert len(rows) == 1


def test_row_without_identifying_fields_is_skipped(db: Path, tmp_path: Path) -> None:
    """A row with no AssetName/IP/MAC is meaningless — skip, don't crash."""
    csv_path = tmp_path / "ls.csv"
    _write_csv(csv_path, [
        {"AssetName": "", "IPAddress": "", "Mac": "", "Description": "stray"},
    ])
    with connect(db) as conn:
        report = import_lansweeper_csv(conn, csv_path=csv_path, now=NOW)
        rows = assets_dal.list_all(conn)
    assert report.imported == 0
    assert report.skipped == 1
    assert rows == []


def test_ip_collision_without_mac_skipped(db: Path, tmp_path: Path) -> None:
    """If an asset already exists at the same primary_ip but no MAC to
    disambiguate, skip — don't merge blindly."""
    # Seed an asset.
    with connect(db) as conn:
        assets_dal.insert_manual(
            conn, hostname="existing", primary_ip="10.0.0.1", now=NOW,
        )
    csv_path = tmp_path / "ls.csv"
    _write_csv(csv_path, [
        {"AssetName": "lanweeper-ghost", "IPAddress": "10.0.0.1", "Mac": ""},
    ])
    with connect(db) as conn:
        report = import_lansweeper_csv(conn, csv_path=csv_path, now=NOW)
        rows = assets_dal.list_all(conn)
    assert report.skipped == 1
    assert len(rows) == 1  # existing untouched


# ---------------------------------------------------------------------------
# Header + format robustness
# ---------------------------------------------------------------------------


def test_missing_optional_columns_ok(db: Path, tmp_path: Path) -> None:
    """Users may export a minimal set of columns. We work with what's there."""
    csv_path = tmp_path / "ls.csv"
    # Only AssetName and IPAddress — no Mac, no Description.
    _write_csv(csv_path, [
        {"AssetName": "only-hostname", "IPAddress": "192.168.1.1"},
    ])
    with connect(db) as conn:
        report = import_lansweeper_csv(conn, csv_path=csv_path, now=NOW)
        [asset] = assets_dal.list_all(conn)
    assert report.imported == 1
    assert asset.hostname == "only-hostname"
    assert asset.primary_ip == "192.168.1.1"
    assert asset.macs == []


def test_empty_csv_is_zero_import(db: Path, tmp_path: Path) -> None:
    csv_path = tmp_path / "ls.csv"
    _write_csv(csv_path, [])
    with connect(db) as conn:
        report = import_lansweeper_csv(conn, csv_path=csv_path, now=NOW)
    assert report.imported == 0
    assert report.skipped == 0


def test_missing_csv_file_raises(tmp_path: Path, db: Path) -> None:
    with connect(db) as conn, pytest.raises(FileNotFoundError):
        import_lansweeper_csv(conn, csv_path=tmp_path / "nope.csv", now=NOW)


def test_header_case_insensitive(db: Path, tmp_path: Path) -> None:
    """Some Lansweeper exports use ASSETNAME all caps."""
    csv_path = tmp_path / "ls.csv"
    _write_csv(csv_path, [
        {"ASSETNAME": "caps", "ipaddress": "10.0.0.9", "mac": "aa:bb:cc:00:00:09"},
    ])
    with connect(db) as conn:
        report = import_lansweeper_csv(conn, csv_path=csv_path, now=NOW)
        rows = assets_dal.list_all(conn)
    assert report.imported == 1
    assert rows[0].hostname == "caps"
