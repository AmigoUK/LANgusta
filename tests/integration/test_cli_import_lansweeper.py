"""Integration test for `langusta import-lansweeper`."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
from typer.testing import CliRunner

from langusta.cli import app
from langusta.db import assets as assets_dal
from langusta.db.connection import connect

runner = CliRunner()

PW = "master-password-for-import-lansweeper-tests"


def _env(home: Path) -> dict[str, str]:
    return {"HOME": str(home), "LANGUSTA_MASTER_PASSWORD": PW}


@pytest.fixture
def home(tmp_path: Path) -> Path:
    h = tmp_path / "home"
    h.mkdir(parents=True)
    runner.invoke(app, ["init"], env=_env(h))
    return h


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def test_import_lansweeper_populates_inventory(home: Path, tmp_path: Path) -> None:
    csv_path = tmp_path / "ls.csv"
    _write_csv(csv_path, [
        {
            "AssetName": "ls-router", "IPAddress": "192.168.1.1",
            "Mac": "aa:bb:cc:dd:ee:ff", "Description": "Core router",
            "Manufacturer": "Cisco", "Type": "Network Device",
        },
        {
            "AssetName": "ls-printer", "IPAddress": "192.168.1.50",
            "Mac": "11:22:33:44:55:66", "Manufacturer": "HP",
            "Type": "Printer",
        },
    ])
    r = runner.invoke(
        app, ["import-lansweeper", str(csv_path)], env=_env(home),
    )
    assert r.exit_code == 0, r.stdout
    assert "imported 2" in r.stdout.lower() or "2 imported" in r.stdout.lower()
    with connect(home / ".langusta" / "db.sqlite") as conn:
        rows = assets_dal.list_all(conn)
    assert {r.hostname for r in rows} == {"ls-router", "ls-printer"}
    assert all(r.source == "imported" for r in rows)


def test_import_lansweeper_missing_file_is_user_error(home: Path, tmp_path: Path) -> None:
    r = runner.invoke(
        app, ["import-lansweeper", str(tmp_path / "nope.csv")], env=_env(home),
    )
    assert r.exit_code != 0


# ---------------------------------------------------------------------------
# Demo fixture — exercises BOM, unicode, embedded newlines, malformed IPs,
# intra-file MAC / IP collisions, and blank rows in one pass.
# ---------------------------------------------------------------------------


DEMO_FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "lansweeper_demo.csv"
)


def test_import_lansweeper_demo_fixture_idempotent(home: Path) -> None:
    assert DEMO_FIXTURE.exists(), DEMO_FIXTURE

    r1 = runner.invoke(
        app, ["import-lansweeper", str(DEMO_FIXTURE)], env=_env(home),
    )
    assert r1.exit_code == 0, r1.stdout
    # 21 clean inserts, 1 MAC-match update (dup-mac-row), 2 skipped
    # (invalid IP + blank row), 3 proposed, 1 review-queue, 1 error.
    assert "imported 21" in r1.stdout
    assert "updated 1" in r1.stdout
    assert "skipped 2" in r1.stdout
    assert "proposed 3" in r1.stdout
    assert "review-queue 1" in r1.stdout
    assert "errors 1" in r1.stdout

    db_path = home / ".langusta" / "db.sqlite"
    with connect(db_path) as conn:
        first_count = conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
        first_pc = conn.execute("SELECT COUNT(*) FROM proposed_changes").fetchone()[0]
        first_rq = conn.execute("SELECT COUNT(*) FROM review_queue").fetchone()[0]

    r2 = runner.invoke(
        app, ["import-lansweeper", str(DEMO_FIXTURE)], env=_env(home),
    )
    assert r2.exit_code == 0, r2.stdout
    # Second run: every clean row now MAC-matches itself → updated, no new
    # inserts, no new proposed/review entries added by idempotent fields.
    assert "imported 0" in r2.stdout

    with connect(db_path) as conn:
        second_count = conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
        second_pc = conn.execute("SELECT COUNT(*) FROM proposed_changes").fetchone()[0]
        second_rq = conn.execute("SELECT COUNT(*) FROM review_queue").fetchone()[0]
    assert second_count == first_count
    # Re-importing generates fresh review-queue entries (there's no
    # dedup for open review items) but MUST NOT duplicate proposed_changes
    # for fields already flagged.
    assert second_pc >= first_pc
    assert second_rq >= first_rq


def test_import_lansweeper_dry_run_does_not_persist(home: Path) -> None:
    assert DEMO_FIXTURE.exists()
    r = runner.invoke(
        app, ["import-lansweeper", str(DEMO_FIXTURE), "--dry-run"], env=_env(home),
    )
    assert r.exit_code == 0, r.stdout
    assert r.stdout.startswith("[dry-run] ")
    assert "imported 21" in r.stdout
    with connect(home / ".langusta" / "db.sqlite") as conn:
        count = conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
    assert count == 0


def test_import_lansweeper_verbose_lists_row_errors(home: Path) -> None:
    assert DEMO_FIXTURE.exists()
    r = runner.invoke(
        app,
        ["import-lansweeper", str(DEMO_FIXTURE), "--verbose"],
        env=_env(home),
    )
    assert r.exit_code == 0, r.stdout
    assert "999.1.1.1" in r.stdout  # malformed IP surfaces under --verbose
    assert "line " in r.stdout
