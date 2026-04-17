"""Export / import tests — portable JSON roundtrip of the whole asset DB.

Spec: docs/specs/01-functionality-and-moscow.md §6; ADR-0005.

Credentials are EXCLUDED by default — users who want to migrate them use
--include-secrets with a separate export password (post-v1). In v1 the
default export is safe to share or commit to a backup server.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from langusta.crypto.vault import Vault
from langusta.db import assets as assets_dal
from langusta.db import credentials as cred_dal
from langusta.db import timeline as tl_dal
from langusta.db.connection import connect
from langusta.db.export import (
    EXPORT_FORMAT_VERSION,
    ImportRefused,
    SchemaMismatch,
    export_to_dict,
    import_from_dict,
)
from langusta.db.migrate import latest_schema_version, migrate

NOW = datetime(2026, 4, 17, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Envelope shape
# ---------------------------------------------------------------------------


def test_export_envelope_has_metadata(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite"
    migrate(db)
    with connect(db) as conn:
        dump = export_to_dict(conn)
    assert dump["export_format_version"] == EXPORT_FORMAT_VERSION
    assert dump["schema_version"] == latest_schema_version()
    assert "exported_at" in dump
    assert "tables" in dump and isinstance(dump["tables"], dict)


def test_export_excludes_credentials_by_default(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite"
    migrate(db)
    with connect(db) as conn:
        vault = Vault.for_tests(password="master-password-here")
        cred_dal.create(
            conn, label="x", kind="snmp_v2c",
            secret=b"public", vault=vault, now=NOW,
        )
        dump = export_to_dict(conn)
    tables = dump["tables"]
    # `credentials` table is NOT included in default export.
    assert "credentials" not in tables


def test_export_includes_user_tables(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite"
    migrate(db)
    with connect(db) as conn:
        assets_dal.insert_manual(conn, hostname="r", primary_ip="10.0.0.1", now=NOW)
        dump = export_to_dict(conn)
    tables = dump["tables"]
    assert "assets" in tables
    assert "mac_addresses" in tables
    assert "field_provenance" in tables
    assert "timeline_entries" in tables
    assert "scans" in tables


def test_export_excludes_internal_tables(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite"
    migrate(db)
    with connect(db) as conn:
        dump = export_to_dict(conn)
    tables = dump["tables"]
    # Internal; rebuilt by migrations or triggers.
    assert "_migrations" not in tables
    assert "assets_fts" not in tables
    assert "assets_fts_data" not in tables


# ---------------------------------------------------------------------------
# Roundtrip
# ---------------------------------------------------------------------------


def test_roundtrip_on_empty_db_yields_empty_tables(tmp_path: Path) -> None:
    src = tmp_path / "src.sqlite"
    dst = tmp_path / "dst.sqlite"
    migrate(src)
    migrate(dst)
    with connect(src) as conn:
        dump = export_to_dict(conn)
    with connect(dst) as conn:
        import_from_dict(conn, dump)
        rows = assets_dal.list_all(conn)
    assert rows == []


def test_roundtrip_preserves_asset_fields_and_counts(tmp_path: Path) -> None:
    src = tmp_path / "src.sqlite"
    dst = tmp_path / "dst.sqlite"
    migrate(src)
    migrate(dst)

    with connect(src) as conn:
        assets_dal.insert_manual(
            conn, hostname="router", primary_ip="10.0.0.1",
            mac="aa:bb:cc:dd:ee:ff",
            description="core router", now=NOW,
        )
        assets_dal.insert_manual(
            conn, hostname="printer", primary_ip="10.0.0.5", now=NOW,
        )
        dump = export_to_dict(conn)

    with connect(dst) as conn:
        import_from_dict(conn, dump)
        rows = assets_dal.list_all(conn)

    assert len(rows) == 2
    by_host = {r.hostname: r for r in rows}
    assert by_host["router"].primary_ip == "10.0.0.1"
    assert by_host["router"].macs == ["aa:bb:cc:dd:ee:ff"]
    assert by_host["router"].description == "core router"


def test_roundtrip_preserves_timeline_order(tmp_path: Path) -> None:
    src = tmp_path / "src.sqlite"
    dst = tmp_path / "dst.sqlite"
    migrate(src)
    migrate(dst)

    with connect(src) as conn:
        aid = assets_dal.insert_manual(conn, hostname="r", now=NOW)
        from datetime import timedelta
        tl_dal.append_entry(
            conn, asset_id=aid, kind="note", body="first",
            now=NOW, author="a",
        )
        tl_dal.append_entry(
            conn, asset_id=aid, kind="note", body="second",
            now=NOW + timedelta(minutes=5), author="a",
        )
        tl_dal.append_entry(
            conn, asset_id=aid, kind="note", body="third",
            now=NOW + timedelta(minutes=10), author="a",
        )
        dump = export_to_dict(conn)

    with connect(dst) as conn:
        import_from_dict(conn, dump)
        [asset] = assets_dal.list_all(conn)
        entries = tl_dal.list_by_asset(conn, asset.id)
    bodies = [e.body for e in entries]
    assert bodies == ["first", "second", "third"]


def test_roundtrip_preserves_provenance(tmp_path: Path) -> None:
    from langusta.core.provenance import FieldProvenance

    src = tmp_path / "src.sqlite"
    dst = tmp_path / "dst.sqlite"
    migrate(src)
    migrate(dst)

    with connect(src) as conn:
        assets_dal.insert_manual(
            conn, hostname="r", description="human-set", now=NOW,
        )
        dump = export_to_dict(conn)

    with connect(dst) as conn:
        import_from_dict(conn, dump)
        [asset] = assets_dal.list_all(conn)
        prov = assets_dal.get_provenance(conn, asset.id)
    assert prov["description"].provenance is FieldProvenance.MANUAL


# ---------------------------------------------------------------------------
# Import refuses unsafe targets
# ---------------------------------------------------------------------------


def test_import_refused_when_target_not_empty(tmp_path: Path) -> None:
    src = tmp_path / "src.sqlite"
    dst = tmp_path / "dst.sqlite"
    migrate(src)
    migrate(dst)

    with connect(src) as conn:
        assets_dal.insert_manual(conn, hostname="a", now=NOW)
        dump = export_to_dict(conn)

    with connect(dst) as conn:
        assets_dal.insert_manual(conn, hostname="existing", now=NOW)
        with pytest.raises(ImportRefused, match="not empty"):
            import_from_dict(conn, dump)


def test_import_refuses_newer_schema_dump(tmp_path: Path) -> None:
    src = tmp_path / "src.sqlite"
    migrate(src)
    with connect(src) as conn:
        dump = export_to_dict(conn)
    dump["schema_version"] = latest_schema_version() + 5
    dst = tmp_path / "dst.sqlite"
    migrate(dst)
    with connect(dst) as conn, pytest.raises(SchemaMismatch):
        import_from_dict(conn, dump)


def test_import_refuses_unknown_envelope_version(tmp_path: Path) -> None:
    src = tmp_path / "src.sqlite"
    migrate(src)
    with connect(src) as conn:
        dump = export_to_dict(conn)
    dump["export_format_version"] = 999
    dst = tmp_path / "dst.sqlite"
    migrate(dst)
    with connect(dst) as conn, pytest.raises(ImportRefused):
        import_from_dict(conn, dump)
