"""Full-text search tests.

Spec (M4 exit): `/` typed into the TUI finds a seeded 'reception switch'
asset within the top 3 results. That requires:
  - FTS5 index over asset text fields
  - auto-updating via triggers
  - MAC search piggy-backed (MAC is 1-to-many, separate LIKE query)
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from langusta.db import assets as assets_dal
from langusta.db import search as search_dal
from langusta.db.connection import connect
from langusta.db.migrate import migrate

NOW = datetime(2026, 4, 17, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def seeded(tmp_path: Path) -> Path:
    db = tmp_path / "search.sqlite"
    migrate(db)
    with connect(db) as conn:
        assets_dal.insert_manual(
            conn, hostname="router-core",
            primary_ip="192.168.1.1",
            mac="aa:bb:cc:dd:ee:ff",
            description="reception switch",
            location="rack 1",
            vendor="Cisco",
            now=NOW,
        )
        assets_dal.insert_manual(
            conn, hostname="printer-marketing",
            primary_ip="192.168.1.50",
            description="HP LaserJet M428 on marketing floor",
            location="marketing",
            now=NOW,
        )
        assets_dal.insert_manual(
            conn, hostname="reception-phone",
            primary_ip="192.168.1.99",
            owner="reception desk",
            now=NOW,
        )
    return db


# ---------------------------------------------------------------------------
# Basic search
# ---------------------------------------------------------------------------


def test_search_finds_by_description_token(seeded: Path) -> None:
    with connect(seeded) as conn:
        results = search_dal.search(conn, "reception")
    hostnames = [h.hostname for h in results]
    assert "router-core" in hostnames
    assert "reception-phone" in hostnames


def test_search_finds_by_hostname_prefix(seeded: Path) -> None:
    with connect(seeded) as conn:
        results = search_dal.search(conn, "print")
    hostnames = [h.hostname for h in results]
    assert "printer-marketing" in hostnames


def test_search_is_case_insensitive(seeded: Path) -> None:
    with connect(seeded) as conn:
        results = search_dal.search(conn, "RECEPTION")
    hostnames = [h.hostname for h in results]
    assert "router-core" in hostnames


def test_search_finds_by_ip(seeded: Path) -> None:
    with connect(seeded) as conn:
        results = search_dal.search(conn, "192.168.1.50")
    assert any(h.hostname == "printer-marketing" for h in results)


def test_search_finds_by_mac_fragment(seeded: Path) -> None:
    with connect(seeded) as conn:
        results = search_dal.search(conn, "aa:bb")
    assert any(h.hostname == "router-core" for h in results)


def test_search_empty_query_returns_empty(seeded: Path) -> None:
    with connect(seeded) as conn:
        assert search_dal.search(conn, "") == []


def test_search_no_match_returns_empty(seeded: Path) -> None:
    with connect(seeded) as conn:
        assert search_dal.search(conn, "no-such-host-zzz") == []


# ---------------------------------------------------------------------------
# Update-then-search — triggers keep FTS in sync
# ---------------------------------------------------------------------------


def test_search_reflects_updated_description(seeded: Path) -> None:
    with connect(seeded) as conn:
        [asset] = [a for a in assets_dal.list_all(conn) if a.hostname == "router-core"]
        conn.execute("UPDATE assets SET description = 'core router downstairs' WHERE id = ?", (asset.id,))
        # After rebuild, "downstairs" should hit.
        results_after = search_dal.search(conn, "downstairs")
        results_old = search_dal.search(conn, "reception")
    assert any(h.id == asset.id for h in results_after)
    assert not any(h.id == asset.id for h in results_old)


def test_search_drops_deleted_asset(seeded: Path) -> None:
    with connect(seeded) as conn:
        [asset] = [a for a in assets_dal.list_all(conn) if a.hostname == "printer-marketing"]
        conn.execute("DELETE FROM assets WHERE id = ?", (asset.id,))
        results = search_dal.search(conn, "marketing")
    assert not any(h.id == asset.id for h in results)


# ---------------------------------------------------------------------------
# Ranking / limit
# ---------------------------------------------------------------------------


def test_search_limit_caps_result_count(seeded: Path) -> None:
    with connect(seeded) as conn:
        results = search_dal.search(conn, "reception", limit=1)
    assert len(results) == 1


def test_search_returns_asset_objects(seeded: Path) -> None:
    """Results should be full Asset records ready for TUI rendering."""
    with connect(seeded) as conn:
        results = search_dal.search(conn, "reception")
    for asset in results:
        assert asset.id > 0
        assert hasattr(asset, "hostname")
        assert hasattr(asset, "primary_ip")
