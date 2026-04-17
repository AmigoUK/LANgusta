"""Integration tests for `langusta monitor install-service`."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from langusta.cli import app

runner = CliRunner()

PW = "master-password-for-install-service-tests"
POSIX_ONLY = pytest.mark.skipif(sys.platform == "win32", reason="POSIX hosts only in v1")


def _env(home: Path) -> dict[str, str]:
    return {"HOME": str(home), "LANGUSTA_MASTER_PASSWORD": PW}


@pytest.fixture
def home(tmp_path: Path) -> Path:
    h = tmp_path / "home"
    h.mkdir(parents=True)
    runner.invoke(app, ["init"], env=_env(h))
    return h


@POSIX_ONLY
def test_install_service_dry_run_prints_to_stdout(home: Path) -> None:
    r = runner.invoke(
        app, ["monitor", "install-service", "--dry-run"], env=_env(home),
    )
    assert r.exit_code == 0, r.stdout
    # Linux: unit; macOS: plist.
    if sys.platform.startswith("linux"):
        assert "[Unit]" in r.stdout
        assert "ExecStart=" in r.stdout
    else:
        assert "<plist" in r.stdout


@POSIX_ONLY
def test_install_service_writes_recipe_file(home: Path) -> None:
    r = runner.invoke(app, ["monitor", "install-service"], env=_env(home))
    assert r.exit_code == 0, r.stdout
    if sys.platform.startswith("linux"):
        install_path = home / ".config" / "systemd" / "user" / "langusta-monitor.service"
    else:
        install_path = home / "Library" / "LaunchAgents" / "uk.attv.langusta.monitor.plist"
    assert install_path.exists()


@POSIX_ONLY
def test_install_service_prints_start_hint(home: Path) -> None:
    r = runner.invoke(app, ["monitor", "install-service"], env=_env(home))
    assert r.exit_code == 0
    if sys.platform.startswith("linux"):
        assert "systemctl --user" in r.stdout
    else:
        assert "launchctl" in r.stdout


@POSIX_ONLY
def test_install_service_refuses_to_overwrite(home: Path) -> None:
    runner.invoke(app, ["monitor", "install-service"], env=_env(home))
    r = runner.invoke(app, ["monitor", "install-service"], env=_env(home))
    assert r.exit_code != 0
    assert "exist" in (r.stdout + (r.stderr or "")).lower()


@POSIX_ONLY
def test_install_service_force_overwrites(home: Path) -> None:
    runner.invoke(app, ["monitor", "install-service"], env=_env(home))
    r = runner.invoke(
        app, ["monitor", "install-service", "--force"], env=_env(home),
    )
    assert r.exit_code == 0
