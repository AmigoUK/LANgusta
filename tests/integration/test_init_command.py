"""Integration test: `langusta init` creates the DB with the full schema,
correct file permissions, and is idempotent.

Spec exit criterion for M0:
  - `uv run langusta init` is idempotent and creates the DB at mode 0600
    with `PRAGMA user_version=1`.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from langusta.cli import app
from langusta.db.connection import connect
from langusta.db.migrate import latest_schema_version

runner = CliRunner()

POSIX_ONLY = pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission semantics")


_TEST_MASTER_PW = "test-master-password-long-enough"


def _run(*args: str, env: dict[str, str] | None = None):
    # M5: init requires a master password; tests supply one via env var so
    # they never block on a prompt.
    merged = dict(env or {})
    merged.setdefault("LANGUSTA_MASTER_PASSWORD", _TEST_MASTER_PW)
    result = runner.invoke(app, list(args), env=merged)
    return result


def test_init_creates_db_file(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    result = _run("init", env={"HOME": str(home)})
    assert result.exit_code == 0, result.stdout
    db = home / ".langusta" / "db.sqlite"
    assert db.exists()


def test_init_db_has_latest_schema_version(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    result = _run("init", env={"HOME": str(home)})
    assert result.exit_code == 0, result.stdout
    db = home / ".langusta" / "db.sqlite"
    with connect(db) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == latest_schema_version()


@POSIX_ONLY
def test_init_db_file_mode_is_0600(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _run("init", env={"HOME": str(home)})
    db = home / ".langusta" / "db.sqlite"
    mode = stat.S_IMODE(db.stat().st_mode)
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"


@POSIX_ONLY
def test_init_langusta_dir_mode_is_0700(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _run("init", env={"HOME": str(home)})
    d = home / ".langusta"
    mode = stat.S_IMODE(d.stat().st_mode)
    assert mode == 0o700, f"expected 0700, got {oct(mode)}"


def test_init_is_idempotent(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    r1 = _run("init", env={"HOME": str(home)})
    r2 = _run("init", env={"HOME": str(home)})
    assert r1.exit_code == 0
    assert r2.exit_code == 0


def test_init_prints_path_to_created_db(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    result = _run("init", env={"HOME": str(home)})
    assert str(home / ".langusta" / "db.sqlite") in result.stdout


def test_init_creates_backups_directory(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _run("init", env={"HOME": str(home)})
    assert (home / ".langusta" / "backups").is_dir()
