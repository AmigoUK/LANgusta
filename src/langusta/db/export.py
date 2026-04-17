"""Portable JSON export of the user-owned tables.

Spec: docs/specs/01-functionality-and-moscow.md §6.

The envelope:
  {
    "export_format_version": 1,
    "schema_version": 3,
    "exported_at": "ISO-8601",
    "tables": { "assets": [rows...], ... }
  }

Credentials are excluded by default. `_migrations`, FTS5 shadow tables,
and other internal storage are excluded too — they're rebuilt on import
by migrations and triggers.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from langusta.db.migrate import latest_schema_version

EXPORT_FORMAT_VERSION = 1


class ImportRefused(RuntimeError):  # noqa: N818 — RuntimeError subclass
    """Import cannot proceed safely (non-empty target, unknown envelope, etc.)."""


class SchemaMismatch(ImportRefused):
    """Dump's schema_version is incompatible with the current binary."""


# Tables we export by default. These contain user-created data; everything
# else is infrastructure owned by migrations or triggers.
_USER_TABLES = (
    "meta",
    "assets",
    "mac_addresses",
    "field_provenance",
    "scans",
    "proposed_changes",
    "review_queue",
    "timeline_entries",
)


# Tables whose presence would be a red flag in a dump — we neither export
# them nor accept them on import.
_INTERNAL_TABLES = frozenset(
    {"_migrations", "sqlite_sequence", "credentials"},
)


def export_to_dict(conn: sqlite3.Connection) -> dict:
    """Collect every user-owned table row into a JSON-serializable dict."""
    tables: dict[str, list[dict]] = {}
    for name in _USER_TABLES:
        # Only export tables that actually exist at this schema version.
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()
        if exists is None:
            continue
        rows = conn.execute(f"SELECT * FROM {name} ORDER BY rowid").fetchall()
        tables[name] = [_serialise_row(r) for r in rows]
    return {
        "export_format_version": EXPORT_FORMAT_VERSION,
        "schema_version": latest_schema_version(),
        "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "tables": tables,
    }


def _serialise_row(row: sqlite3.Row) -> dict:
    """Convert a sqlite3.Row to a plain dict; bytes → hex for JSON."""
    out: dict = {}
    for key in row.keys():  # noqa: SIM118 — sqlite3.Row.keys() is the API
        value = row[key]
        if isinstance(value, bytes):
            out[key] = {"__bytes_hex__": value.hex()}
        else:
            out[key] = value
    return out


def _deserialise_row(row: dict) -> dict:
    out: dict = {}
    for key, value in row.items():
        if isinstance(value, dict) and "__bytes_hex__" in value:
            out[key] = bytes.fromhex(value["__bytes_hex__"])
        else:
            out[key] = value
    return out


def import_from_dict(conn: sqlite3.Connection, dump: dict) -> None:
    """Populate `conn` from a previously exported dict. Target must be empty
    at the user-owned level — refuse to merge rather than risk duplicates.
    """
    fmt_version = dump.get("export_format_version")
    if fmt_version != EXPORT_FORMAT_VERSION:
        raise ImportRefused(
            f"unknown export_format_version={fmt_version}; "
            f"this binary reads v{EXPORT_FORMAT_VERSION}"
        )
    schema_version = dump.get("schema_version")
    if schema_version != latest_schema_version():
        raise SchemaMismatch(
            f"dump schema_version={schema_version} does not match binary "
            f"schema_version={latest_schema_version()}; "
            "upgrade or downgrade LANgusta before importing."
        )

    # Target must be empty of user data — refuse to merge.
    for name in _USER_TABLES:
        if name == "meta":
            # meta holds vault salt + verifier set by `init`; ignore.
            continue
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()
        if row is None:
            continue
        count = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        if count > 0:
            raise ImportRefused(
                f"target DB is not empty (table {name!r} has rows); "
                "import refused to avoid merging"
            )

    tables = dump.get("tables") or {}
    for table_name in _USER_TABLES:
        if table_name not in tables:
            continue
        if table_name in _INTERNAL_TABLES:
            continue
        rows = tables[table_name]
        if not rows:
            continue
        for row in rows:
            row = _deserialise_row(row)
            if table_name == "meta":
                # Upsert — don't overwrite the target's own salt/verifier.
                conn.execute(
                    "INSERT INTO meta (key, value, set_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(key) DO NOTHING",
                    (row["key"], row["value"], row["set_at"]),
                )
            else:
                cols = list(row.keys())
                placeholders = ", ".join("?" for _ in cols)
                col_names = ", ".join(cols)
                conn.execute(
                    f"INSERT INTO {table_name} ({col_names}) VALUES ({placeholders})",
                    tuple(row[c] for c in cols),
                )
