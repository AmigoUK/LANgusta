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
