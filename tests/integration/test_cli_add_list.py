"""Integration tests for `langusta add` and `langusta list`.

M1 exit criteria:
  - `langusta add --hostname X --ip Y --mac Z` inserts an asset; all three
    fields carry `manual` provenance with the current timestamp.
  - `langusta list` prints the inserted row.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from langusta.cli import app
from langusta.core.provenance import FieldProvenance
from langusta.db import assets as assets_dal
from langusta.db.connection import connect

runner = CliRunner()


def _run(*args: str, home: Path):
    return runner.invoke(app, list(args), env={"HOME": str(home)})


def _init_home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    _run("init", home=home)
    return home


def test_add_inserts_row_with_manual_provenance(tmp_path: Path) -> None:
    home = _init_home(tmp_path)
    r = _run(
        "add",
        "--hostname", "router",
        "--ip", "192.168.1.1",
        "--mac", "aa:bb:cc:dd:ee:ff",
        home=home,
    )
    assert r.exit_code == 0, r.stdout

    with connect(home / ".langusta" / "db.sqlite") as conn:
        rows = assets_dal.list_all(conn)
    assert len(rows) == 1
    asset = rows[0]
    assert asset.hostname == "router"
    assert asset.primary_ip == "192.168.1.1"
    assert asset.macs == ["aa:bb:cc:dd:ee:ff"]
    assert asset.source == "manual"

    with connect(home / ".langusta" / "db.sqlite") as conn:
        prov = assets_dal.get_provenance(conn, asset.id)
    assert prov["hostname"].provenance is FieldProvenance.MANUAL
    assert prov["primary_ip"].provenance is FieldProvenance.MANUAL


def test_add_accepts_description_and_location(tmp_path: Path) -> None:
    home = _init_home(tmp_path)
    r = _run(
        "add",
        "--hostname", "printer",
        "--ip", "10.0.0.5",
        "--description", "reception",
        "--location", "ground floor",
        home=home,
    )
    assert r.exit_code == 0, r.stdout

    with connect(home / ".langusta" / "db.sqlite") as conn:
        rows = assets_dal.list_all(conn)
    assert rows[0].description == "reception"
    assert rows[0].location == "ground floor"


def test_add_prints_new_asset_id(tmp_path: Path) -> None:
    home = _init_home(tmp_path)
    r = _run("add", "--hostname", "x", home=home)
    assert r.exit_code == 0
    # Some form of "id=1" or "added asset #1" must be in the output.
    assert "1" in r.stdout


def test_add_without_any_fields_is_a_user_error(tmp_path: Path) -> None:
    home = _init_home(tmp_path)
    r = _run("add", home=home)
    assert r.exit_code != 0
    lower = (r.stdout + (r.stderr or "")).lower()
    assert "hostname" in lower or "ip" in lower or "mac" in lower


def test_add_uppercase_mac_is_stored_lowercase(tmp_path: Path) -> None:
    home = _init_home(tmp_path)
    _run("add", "--hostname", "r", "--mac", "AA:BB:CC:DD:EE:FF", home=home)
    with connect(home / ".langusta" / "db.sqlite") as conn:
        rows = assets_dal.list_all(conn)
    assert rows[0].macs == ["aa:bb:cc:dd:ee:ff"]


def test_add_rejects_duplicate_mac_with_clear_error(tmp_path: Path) -> None:
    home = _init_home(tmp_path)
    _run("add", "--hostname", "a", "--mac", "aa:bb:cc:dd:ee:ff", home=home)
    r = _run("add", "--hostname", "b", "--mac", "aa:bb:cc:dd:ee:ff", home=home)
    assert r.exit_code != 0
    assert "aa:bb:cc:dd:ee:ff" in (r.stdout + (r.stderr or "")).lower()


def test_add_refuses_second_asset_at_same_ip(tmp_path: Path) -> None:
    """A user running `add --hostname X --ip Y` twice should NOT get two
    assets — the second call must surface the existing asset's id instead.
    """
    home = _init_home(tmp_path)
    first = _run("add", "--hostname", "router", "--ip", "192.168.1.1", home=home)
    assert first.exit_code == 0, first.stdout
    second = _run("add", "--hostname", "router", "--ip", "192.168.1.1", home=home)
    assert second.exit_code != 0
    err = (second.stdout + (second.stderr or "")).lower()
    assert "already" in err
    assert "192.168.1.1" in err

    with connect(home / ".langusta" / "db.sqlite") as conn:
        rows = assets_dal.list_all(conn)
    assert len(rows) == 1  # still only one asset


def test_add_refuses_second_asset_at_same_hostname(tmp_path: Path) -> None:
    home = _init_home(tmp_path)
    _run("add", "--hostname", "router", "--ip", "10.0.0.1", home=home)
    r = _run("add", "--hostname", "router", "--ip", "10.0.0.2", home=home)
    assert r.exit_code != 0
    err = (r.stdout + (r.stderr or "")).lower()
    assert "already" in err
    assert "'router'" in err or "router" in err

    with connect(home / ".langusta" / "db.sqlite") as conn:
        rows = assets_dal.list_all(conn)
    assert len(rows) == 1


def test_add_force_overrides_duplicate_ip_guard(tmp_path: Path) -> None:
    home = _init_home(tmp_path)
    _run("add", "--hostname", "router", "--ip", "192.168.1.1", home=home)
    r = _run(
        "add", "--hostname", "router-backup", "--ip", "192.168.1.1",
        "--force", home=home,
    )
    assert r.exit_code == 0, r.stdout

    with connect(home / ".langusta" / "db.sqlite") as conn:
        rows = assets_dal.list_all(conn)
    assert len(rows) == 2
    assert {r.hostname for r in rows} == {"router", "router-backup"}


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def test_list_empty_prints_friendly_message(tmp_path: Path) -> None:
    home = _init_home(tmp_path)
    r = _run("list", home=home)
    assert r.exit_code == 0
    assert "no assets" in r.stdout.lower() or "empty" in r.stdout.lower()


def test_list_shows_inserted_assets(tmp_path: Path) -> None:
    home = _init_home(tmp_path)
    _run("add", "--hostname", "alpha", "--ip", "10.0.0.1", home=home)
    _run("add", "--hostname", "bravo", "--ip", "10.0.0.2", home=home)
    r = _run("list", home=home)
    assert r.exit_code == 0
    assert "alpha" in r.stdout
    assert "bravo" in r.stdout
    assert "10.0.0.1" in r.stdout
    assert "10.0.0.2" in r.stdout


def test_list_includes_source_column(tmp_path: Path) -> None:
    home = _init_home(tmp_path)
    _run("add", "--hostname", "x", home=home)
    r = _run("list", home=home)
    assert "manual" in r.stdout.lower()
