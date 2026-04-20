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


def test_duplicate_mac_reimport_is_idempotent(db: Path, tmp_path: Path) -> None:
    """Re-importing the same CSV twice must not duplicate assets or crash.

    The second pass finds an exact MAC match with no field diffs, so it's an
    Updated-with-zero-changes: counted under `updated`, no proposed_changes.
    """
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
    assert second.updated == 1
    assert second.proposed_changes_created == 0
    assert len(rows) == 1


def test_row_without_identifying_fields_is_skipped(db: Path, tmp_path: Path) -> None:
    """A row with no AssetName/IP/MAC is meaningless — skip, don't crash.

    A stray Description alone is not an identity anchor.
    """
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


def test_ip_collision_without_mac_defers_to_review_queue(
    db: Path, tmp_path: Path,
) -> None:
    """An IP-only identity match can't be resolved automatically — the
    importer routes the row to the review queue instead of merging blindly
    or dropping it silently."""
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
        review_count = conn.execute(
            "SELECT COUNT(*) FROM review_queue",
        ).fetchone()[0]
    assert report.review_queue_entries == 1
    assert review_count == 1
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


# ---------------------------------------------------------------------------
# --dry-run + per-row error handling
# ---------------------------------------------------------------------------


def test_dry_run_rolls_back_all_writes(db: Path, tmp_path: Path) -> None:
    csv_path = tmp_path / "ls.csv"
    _write_csv(csv_path, [
        {"AssetName": "dry-1", "IPAddress": "10.0.0.1", "Mac": "aa:bb:cc:00:00:01"},
        {"AssetName": "dry-2", "IPAddress": "10.0.0.2", "Mac": "aa:bb:cc:00:00:02"},
    ])
    with connect(db) as conn:
        report = import_lansweeper_csv(
            conn, csv_path=csv_path, now=NOW, dry_run=True,
        )
        rows = assets_dal.list_all(conn)
    assert report.imported == 2
    assert rows == []


def test_invalid_ip_is_recorded_as_row_error(db: Path, tmp_path: Path) -> None:
    csv_path = tmp_path / "ls.csv"
    _write_csv(csv_path, [
        {"AssetName": "bad", "IPAddress": "999.1.1.1", "Mac": "aa:bb:cc:00:00:01"},
    ])
    with connect(db) as conn:
        report = import_lansweeper_csv(conn, csv_path=csv_path, now=NOW)
        rows = assets_dal.list_all(conn)
    assert report.imported == 0
    assert report.skipped == 1
    assert len(report.row_errors) == 1
    assert "999.1.1.1" in report.row_errors[0].reason
    assert rows == []


def test_row_error_includes_line_number(db: Path, tmp_path: Path) -> None:
    csv_path = tmp_path / "ls.csv"
    _write_csv(csv_path, [
        {"AssetName": "good", "IPAddress": "10.0.0.1", "Mac": "aa:bb:cc:00:00:01"},
        {"AssetName": "bad",  "IPAddress": "bogus",    "Mac": "aa:bb:cc:00:00:02"},
    ])
    with connect(db) as conn:
        report = import_lansweeper_csv(conn, csv_path=csv_path, now=NOW)
    assert len(report.row_errors) == 1
    # Header on line 1, first data row on line 2, bad row on line 3.
    assert report.row_errors[0].line_number == 3


# ---------------------------------------------------------------------------
# MAC-match merge path
# ---------------------------------------------------------------------------


def test_mac_collision_creates_proposed_changes_not_skip(
    db: Path, tmp_path: Path,
) -> None:
    # Seed a manually-curated asset with a bound MAC.
    with connect(db) as conn:
        asset_id = assets_dal.insert_manual(
            conn,
            hostname="legacy-router",
            primary_ip="10.0.0.1",
            vendor="Cisco",
            mac="aa:bb:cc:00:00:01",
            now=NOW,
        )

    csv_path = tmp_path / "ls.csv"
    _write_csv(csv_path, [
        {
            "AssetName": "different-name",
            "IPAddress": "10.0.0.1",
            "Mac": "aa:bb:cc:00:00:01",
            "Manufacturer": "Juniper",
        },
    ])
    with connect(db) as conn:
        report = import_lansweeper_csv(conn, csv_path=csv_path, now=NOW)
        open_rows = conn.execute(
            "SELECT field, current_value, proposed_value FROM proposed_changes "
            "WHERE asset_id = ? ORDER BY field", (asset_id,),
        ).fetchall()
    assert report.updated == 1
    assert report.skipped == 0
    # hostname and vendor both conflict with the manual values.
    assert report.proposed_changes_created == 2
    fields = {r["field"] for r in open_rows}
    assert fields == {"hostname", "vendor"}


def test_mac_collision_with_no_field_diffs_creates_no_proposed_changes(
    db: Path, tmp_path: Path,
) -> None:
    with connect(db) as conn:
        asset_id = assets_dal.insert_manual(
            conn,
            hostname="same",
            primary_ip="10.0.0.1",
            vendor="Acme",
            mac="aa:bb:cc:00:00:01",
            now=NOW,
        )

    csv_path = tmp_path / "ls.csv"
    _write_csv(csv_path, [
        {
            "AssetName": "same", "IPAddress": "10.0.0.1",
            "Mac": "aa:bb:cc:00:00:01", "Manufacturer": "Acme",
        },
    ])
    with connect(db) as conn:
        report = import_lansweeper_csv(conn, csv_path=csv_path, now=NOW)
        pc_count = conn.execute(
            "SELECT COUNT(*) FROM proposed_changes WHERE asset_id = ?",
            (asset_id,),
        ).fetchone()[0]
    assert report.updated == 1
    assert report.proposed_changes_created == 0
    assert pc_count == 0


def test_mac_collision_refreshes_mac_last_seen(
    db: Path, tmp_path: Path,
) -> None:
    from datetime import timedelta

    earlier = NOW
    later = NOW + timedelta(hours=3)

    with connect(db) as conn:
        assets_dal.insert_manual(
            conn,
            hostname="h", primary_ip="10.0.0.1",
            mac="aa:bb:cc:00:00:01", now=earlier,
        )

    csv_path = tmp_path / "ls.csv"
    _write_csv(csv_path, [
        {"AssetName": "h", "IPAddress": "10.0.0.1", "Mac": "aa:bb:cc:00:00:01"},
    ])
    with connect(db) as conn:
        import_lansweeper_csv(conn, csv_path=csv_path, now=later)
        last_seen = conn.execute(
            "SELECT last_seen FROM mac_addresses WHERE mac = ?",
            ("aa:bb:cc:00:00:01",),
        ).fetchone()["last_seen"]
    assert last_seen == later.isoformat(timespec="seconds")


def test_imported_field_overwrites_scanned_field_with_imported_provenance(
    db: Path, tmp_path: Path,
) -> None:
    """A SCANNED field should be overwritten by an import and escalated to
    IMPORTED provenance — imports are human-curated truth."""
    from langusta.core.provenance import FieldProvenance
    from langusta.db.writer import Observation, apply_scan_observation

    # Create a scan-observed asset.
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO scans (target, started_at) VALUES ('test', ?)",
            (NOW.isoformat(timespec="seconds"),),
        )
        scan_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        outcome = apply_scan_observation(
            conn,
            Observation(
                primary_ip="10.0.0.1",
                hostname="scanned-host",
                mac="aa:bb:cc:00:00:01",
                vendor="ScanVendor",
            ),
            scan_id=int(scan_id),
            now=NOW,
        )
    asset_id = outcome.asset_id  # type: ignore[union-attr]

    csv_path = tmp_path / "ls.csv"
    _write_csv(csv_path, [
        {
            "AssetName": "imported-host",
            "IPAddress": "10.0.0.1",
            "Mac": "aa:bb:cc:00:00:01",
            "Manufacturer": "ImportVendor",
        },
    ])
    with connect(db) as conn:
        report = import_lansweeper_csv(conn, csv_path=csv_path, now=NOW)
        asset = assets_dal.get_by_id(conn, asset_id)
        prov = assets_dal.get_provenance(conn, asset_id)
    assert asset is not None
    assert asset.hostname == "imported-host"
    assert asset.vendor == "ImportVendor"
    assert prov["hostname"].provenance is FieldProvenance.IMPORTED
    assert prov["vendor"].provenance is FieldProvenance.IMPORTED
    # Neither change was "conflicting" from merge's PoV (prior was SCANNED),
    # so both apply cleanly, no proposed_changes.
    assert report.proposed_changes_created == 0


def test_existing_imported_field_with_conflict_creates_proposed_change(
    db: Path, tmp_path: Path,
) -> None:
    """Import-on-import conflicts still go through proposed_changes: the
    current import should not silently overwrite an earlier import's value.
    """
    csv_v1 = tmp_path / "v1.csv"
    _write_csv(csv_v1, [
        {
            "AssetName": "box-01", "IPAddress": "10.0.0.1",
            "Mac": "aa:bb:cc:00:00:01", "Manufacturer": "V1Vendor",
        },
    ])
    csv_v2 = tmp_path / "v2.csv"
    _write_csv(csv_v2, [
        {
            "AssetName": "box-01", "IPAddress": "10.0.0.1",
            "Mac": "aa:bb:cc:00:00:01", "Manufacturer": "V2Vendor",
        },
    ])
    with connect(db) as conn:
        import_lansweeper_csv(conn, csv_path=csv_v1, now=NOW)
        report = import_lansweeper_csv(conn, csv_path=csv_v2, now=NOW)
    assert report.updated == 1
    assert report.proposed_changes_created == 1


# ---------------------------------------------------------------------------
# Review-queue path
# ---------------------------------------------------------------------------


def test_ip_collision_review_queue_observation_json_round_trips(
    db: Path, tmp_path: Path,
) -> None:
    import json as _json

    with connect(db) as conn:
        assets_dal.insert_manual(
            conn, hostname="existing", primary_ip="10.0.0.1", now=NOW,
        )
    csv_path = tmp_path / "ls.csv"
    _write_csv(csv_path, [
        {
            "AssetName": "ghost", "IPAddress": "10.0.0.1",
            "Mac": "", "Manufacturer": "GhostCorp",
        },
    ])
    with connect(db) as conn:
        import_lansweeper_csv(conn, csv_path=csv_path, now=NOW)
        row = conn.execute(
            "SELECT observation, candidates FROM review_queue",
        ).fetchone()
    obs = _json.loads(row["observation"])
    cands = _json.loads(row["candidates"])
    assert obs["hostname"] == "ghost"
    assert obs["primary_ip"] == "10.0.0.1"
    assert obs["vendor"] == "GhostCorp"
    assert cands[0]["reason"] == "ip_match"
    assert cands[0]["score"] == 80


def test_mac_and_ip_point_to_different_assets_goes_to_review_queue(
    db: Path, tmp_path: Path,
) -> None:
    with connect(db) as conn:
        a1 = assets_dal.insert_manual(
            conn, hostname="a1", primary_ip="10.0.0.1",
            mac="aa:bb:cc:00:00:01", now=NOW,
        )
        a2 = assets_dal.insert_manual(
            conn, hostname="a2", primary_ip="10.0.0.2",
            mac="aa:bb:cc:00:00:02", now=NOW,
        )
    csv_path = tmp_path / "ls.csv"
    _write_csv(csv_path, [
        # MAC matches a1 but IP matches a2 → ambiguous, both candidates.
        {
            "AssetName": "mystery", "IPAddress": "10.0.0.2",
            "Mac": "aa:bb:cc:00:00:01",
        },
    ])
    with connect(db) as conn:
        report = import_lansweeper_csv(conn, csv_path=csv_path, now=NOW)
        row = conn.execute(
            "SELECT candidates FROM review_queue",
        ).fetchone()
    import json as _json
    cands = _json.loads(row["candidates"])
    asset_ids = {int(c["asset_id"]) for c in cands}
    reasons = {c["reason"] for c in cands}
    assert report.review_queue_entries == 1
    assert asset_ids == {a1, a2}
    assert reasons == {"mac_match", "ip_match"}


# ---------------------------------------------------------------------------
# New column mappings + format robustness
# ---------------------------------------------------------------------------


def test_new_columns_map_detected_os_location_owner_url(
    db: Path, tmp_path: Path,
) -> None:
    csv_path = tmp_path / "ls.csv"
    _write_csv(csv_path, [
        {
            "AssetName": "srv-01",
            "IPAddress": "10.0.0.10",
            "Mac": "aa:bb:cc:00:00:10",
            "OperatingSystem": "Ubuntu 24.04",
            "Location": "DC-01 Rack 5",
            "Owner": "ops@example.com",
            "URL": "https://wiki.example.com/srv-01",
        },
    ])
    with connect(db) as conn:
        import_lansweeper_csv(conn, csv_path=csv_path, now=NOW)
        [asset] = assets_dal.list_all(conn)
    assert asset.detected_os == "Ubuntu 24.04"
    assert asset.location == "DC-01 Rack 5"
    assert asset.owner == "ops@example.com"
    assert asset.management_url == "https://wiki.example.com/srv-01"


def test_bom_in_first_header_is_stripped(db: Path, tmp_path: Path) -> None:
    """Excel exports frequently prepend U+FEFF to the first column header."""
    csv_path = tmp_path / "ls.csv"
    csv_path.write_bytes(
        "\ufeffAssetName,IPAddress,Mac\n"
        "bom-host,10.0.0.1,aa:bb:cc:00:00:01\n".encode("utf-8"),
    )
    with connect(db) as conn:
        report = import_lansweeper_csv(conn, csv_path=csv_path, now=NOW)
        [asset] = assets_dal.list_all(conn)
    assert report.imported == 1
    assert asset.hostname == "bom-host"


def test_unicode_hostname_round_trips(db: Path, tmp_path: Path) -> None:
    csv_path = tmp_path / "ls.csv"
    _write_csv(csv_path, [
        {
            "AssetName": "büro-lápiz-01",
            "IPAddress": "10.0.0.1", "Mac": "aa:bb:cc:00:00:01",
        },
    ])
    with connect(db) as conn:
        import_lansweeper_csv(conn, csv_path=csv_path, now=NOW)
        [asset] = assets_dal.list_all(conn)
    assert asset.hostname == "büro-lápiz-01"


def test_embedded_newline_in_description_preserved(
    db: Path, tmp_path: Path,
) -> None:
    desc = "Line one\nLine two — embedded newline preserved"
    csv_path = tmp_path / "ls.csv"
    _write_csv(csv_path, [
        {
            "AssetName": "printer-01",
            "IPAddress": "10.0.0.1",
            "Mac": "aa:bb:cc:00:00:01",
            "Description": desc,
        },
    ])
    with connect(db) as conn:
        import_lansweeper_csv(conn, csv_path=csv_path, now=NOW)
        [asset] = assets_dal.list_all(conn)
    assert asset.description == desc


def test_blank_identifying_row_skipped_without_error(
    db: Path, tmp_path: Path,
) -> None:
    """A row with every cell blank is a silent skip — no RowError."""
    csv_path = tmp_path / "ls.csv"
    _write_csv(csv_path, [
        {"AssetName": "", "IPAddress": "", "Mac": "", "Description": ""},
    ])
    with connect(db) as conn:
        report = import_lansweeper_csv(conn, csv_path=csv_path, now=NOW)
    assert report.skipped == 1
    assert report.row_errors == ()


def test_intra_file_mac_dup_second_row_creates_proposed_changes(
    db: Path, tmp_path: Path,
) -> None:
    """The second row in the same file that re-uses an earlier row's MAC
    merges into the newly-inserted asset via the review-queue rules."""
    csv_path = tmp_path / "ls.csv"
    _write_csv(csv_path, [
        {
            "AssetName": "first",
            "IPAddress": "10.0.0.1",
            "Mac": "aa:bb:cc:00:00:01",
            "Manufacturer": "VendorA",
        },
        {
            "AssetName": "second",  # different hostname, same MAC, same IP
            "IPAddress": "10.0.0.1",
            "Mac": "aa:bb:cc:00:00:01",
            "Manufacturer": "VendorB",
        },
    ])
    with connect(db) as conn:
        report = import_lansweeper_csv(conn, csv_path=csv_path, now=NOW)
    assert report.imported == 1
    assert report.updated == 1
    # hostname + vendor both conflict with the just-inserted values.
    assert report.proposed_changes_created == 2


def test_unhandled_db_error_rolls_back_full_import(
    db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A top-level unhandled exception (not per-row) reverts the whole pass."""
    csv_path = tmp_path / "ls.csv"
    _write_csv(csv_path, [
        {"AssetName": "a", "IPAddress": "10.0.0.1", "Mac": "aa:bb:cc:00:00:01"},
        {"AssetName": "b", "IPAddress": "10.0.0.2", "Mac": "aa:bb:cc:00:00:02"},
    ])

    from langusta.db import import_lansweeper as mod

    calls = {"n": 0}
    real_normalise = mod._normalise_headers

    def flaky(row: dict[str, str]) -> dict[str, str]:
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("synthetic outer failure")
        return real_normalise(row)

    monkeypatch.setattr(mod, "_normalise_headers", flaky)

    with connect(db) as conn, pytest.raises(RuntimeError, match="outer failure"):
        import_lansweeper_csv(conn, csv_path=csv_path, now=NOW)
    with connect(db) as conn:
        rows = assets_dal.list_all(conn)
    assert rows == []


def test_import_creates_kind_import_timeline_entry(
    db: Path, tmp_path: Path,
) -> None:
    csv_path = tmp_path / "ls.csv"
    _write_csv(csv_path, [
        {"AssetName": "a", "IPAddress": "10.0.0.1", "Mac": "aa:bb:cc:00:00:01"},
    ])
    with connect(db) as conn:
        import_lansweeper_csv(conn, csv_path=csv_path, now=NOW)
        kinds = [
            r["kind"] for r in conn.execute(
                "SELECT kind FROM timeline_entries",
            ).fetchall()
        ]
    assert "import" in kinds
