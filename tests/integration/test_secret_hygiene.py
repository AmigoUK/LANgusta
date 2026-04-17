"""Secret hygiene — credentials must never leak in output, logs, or files.

Spec §16: "No default credentials, ever. [...] Credentials are never logged,
never exported in backups in plaintext, and never displayed in the TUI
after entry."
"""

from __future__ import annotations

import logging
import stat
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from langusta.cli import app

runner = CliRunner()

PW = "master-password-for-secret-hygiene-tests"
SECRET = "uniq-secret-token-must-not-leak-aaaa1234"

POSIX_ONLY = pytest.mark.skipif(sys.platform == "win32", reason="POSIX perms")


def _env(home: Path) -> dict[str, str]:
    return {
        "HOME": str(home),
        "LANGUSTA_MASTER_PASSWORD": PW,
    }


def _init_and_add(home: Path, label: str) -> None:
    runner.invoke(app, ["init"], env=_env(home))
    env = {**_env(home), "LANGUSTA_CRED_SECRET": SECRET}
    runner.invoke(app, ["cred", "add", "--label", label, "--kind", "snmp_v2c"], env=env)


# ---------------------------------------------------------------------------
# Output scraping
# ---------------------------------------------------------------------------


def test_secret_not_in_cred_list_output(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _init_and_add(home, "a")
    r = runner.invoke(app, ["cred", "list"], env=_env(home))
    assert SECRET not in r.stdout
    assert SECRET not in (r.stderr or "")


def test_secret_not_in_list_assets_output(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _init_and_add(home, "a")
    r = runner.invoke(app, ["list"], env=_env(home))
    assert SECRET not in r.stdout


def test_secret_not_in_help_output(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _init_and_add(home, "a")
    for args in (["--help"], ["cred", "--help"], ["scan", "--help"]):
        r = runner.invoke(app, args, env=_env(home))
        assert SECRET not in r.stdout


def test_secret_not_in_logs_on_cred_add(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """If any module logs during `cred add`, the secret must NOT appear."""
    home = tmp_path / "home"
    home.mkdir()
    runner.invoke(app, ["init"], env=_env(home))
    caplog.set_level(logging.DEBUG)
    env = {**_env(home), "LANGUSTA_CRED_SECRET": SECRET}
    runner.invoke(app, ["cred", "add", "--label", "x", "--kind", "snmp_v2c"], env=env)
    for record in caplog.records:
        msg = record.getMessage()
        assert SECRET not in msg


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


def test_secret_not_in_raw_db_bytes(tmp_path: Path) -> None:
    """Reading the db.sqlite file as bytes must not reveal the plaintext."""
    home = tmp_path / "home"
    home.mkdir()
    _init_and_add(home, "a")
    raw = (home / ".langusta" / "db.sqlite").read_bytes()
    # Check both utf-8 and raw ascii encodings.
    assert SECRET.encode("utf-8") not in raw
    assert SECRET.encode("ascii") not in raw


# ---------------------------------------------------------------------------
# File permissions
# ---------------------------------------------------------------------------


@POSIX_ONLY
def test_db_file_mode_is_0600(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    runner.invoke(app, ["init"], env=_env(home))
    db_mode = stat.S_IMODE((home / ".langusta" / "db.sqlite").stat().st_mode)
    assert db_mode == 0o600


@POSIX_ONLY
def test_backups_dir_mode_is_0700(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    runner.invoke(app, ["init"], env=_env(home))
    mode = stat.S_IMODE((home / ".langusta" / "backups").stat().st_mode)
    assert mode == 0o700


@POSIX_ONLY
def test_langusta_home_mode_is_0700(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    runner.invoke(app, ["init"], env=_env(home))
    mode = stat.S_IMODE((home / ".langusta").stat().st_mode)
    assert mode == 0o700


# ---------------------------------------------------------------------------
# Log scrape across a bunch of invocations
# ---------------------------------------------------------------------------


def test_secret_not_in_any_output_across_workflow(tmp_path: Path) -> None:
    """Run an end-to-end workflow and confirm the secret never appears."""
    home = tmp_path / "home"
    home.mkdir()
    env = _env(home)
    _init_and_add(home, "workflow")

    combined = ""
    for args in (
        ["--version"],
        ["list"],
        ["cred", "list"],
        ["review"],
    ):
        r = runner.invoke(app, args, env=env)
        combined += r.stdout + (r.stderr or "")

    assert SECRET not in combined
