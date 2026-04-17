"""Integration tests for `langusta review` — CLI for the review queue.

M2 scope: list pending proposed_changes, accept/reject by id.
The TUI review screen is M4; this is the scripting surface.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from langusta.cli import app
from langusta.core.provenance import FieldProvenance
from langusta.db import assets as assets_dal
from langusta.db import proposed_changes as pc_dal
from langusta.db import scans as scans_dal
from langusta.db.connection import connect
from langusta.db.migrate import migrate

runner = CliRunner()

NOW = datetime(2026, 4, 17, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def seeded(tmp_path: Path) -> Path:
    """Home with one asset and one open proposed_change."""
    h = tmp_path / "home"
    h.mkdir()
    langusta_dir = h / ".langusta"
    langusta_dir.mkdir()
    db = langusta_dir / "db.sqlite"
    migrate(db)
    with connect(db) as conn:
        aid = assets_dal.insert_manual(
            conn, hostname="router", primary_ip="10.0.0.1",
            mac="aa:bb:cc:dd:ee:ff", now=NOW,
        )
        sid = scans_dal.start_scan(conn, target="10.0.0.0/30", now=NOW)
        pc_dal.insert(
            conn, asset_id=aid, field="hostname",
            current_value="router", current_provenance=FieldProvenance.MANUAL,
            proposed_value="scanner-guess", observed_at=NOW, scan_id=sid,
        )
    return h


def _review(home: Path, *args: str):
    return runner.invoke(app, ["review", *args], env={"HOME": str(home)})


def test_review_lists_open_proposals(seeded: Path) -> None:
    r = _review(seeded)
    assert r.exit_code == 0, r.stdout
    assert "hostname" in r.stdout
    assert "router" in r.stdout
    assert "scanner-guess" in r.stdout


def test_review_lists_empty_on_clean_db(tmp_path: Path) -> None:
    h = tmp_path / "home"
    h.mkdir()
    runner.invoke(app, ["init"], env={"HOME": str(h)})
    r = _review(h)
    assert r.exit_code == 0
    assert "no pending" in r.stdout.lower() or "empty" in r.stdout.lower()


def test_review_accept_applies_proposed_value(seeded: Path) -> None:
    # First discover the id.
    with connect(seeded / ".langusta" / "db.sqlite") as conn:
        [pc] = pc_dal.list_open(conn)
    r = _review(seeded, "accept", str(pc.id))
    assert r.exit_code == 0, r.stdout
    with connect(seeded / ".langusta" / "db.sqlite") as conn:
        asset = assets_dal.get_by_id(conn, pc.asset_id)
        prov = assets_dal.get_provenance(conn, pc.asset_id)
    assert asset is not None
    assert asset.hostname == "scanner-guess"
    assert prov["hostname"].provenance is FieldProvenance.SCANNED


def test_review_reject_keeps_current_value(seeded: Path) -> None:
    with connect(seeded / ".langusta" / "db.sqlite") as conn:
        [pc] = pc_dal.list_open(conn)
    r = _review(seeded, "reject", str(pc.id))
    assert r.exit_code == 0, r.stdout
    with connect(seeded / ".langusta" / "db.sqlite") as conn:
        asset = assets_dal.get_by_id(conn, pc.asset_id)
    assert asset is not None
    assert asset.hostname == "router"


def test_review_on_unknown_id_fails_cleanly(seeded: Path) -> None:
    r = _review(seeded, "accept", "999")
    assert r.exit_code != 0
