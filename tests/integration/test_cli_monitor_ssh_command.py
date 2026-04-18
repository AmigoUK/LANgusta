"""Integration tests for `langusta monitor enable --kind ssh_command` + run."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from langusta.cli import app
from langusta.db import assets as assets_dal
from langusta.db import monitoring as mon_dal
from langusta.db.connection import connect

runner = CliRunner()

PW = "master-password-for-tests-long-enough"


def _init_home(tmp_path: Path) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    runner.invoke(app, ["init"], env={"HOME": str(h), "LANGUSTA_MASTER_PASSWORD": PW})
    return h


def _env(h: Path, extras: dict[str, str] | None = None) -> dict[str, str]:
    env = {"HOME": str(h), "LANGUSTA_MASTER_PASSWORD": PW}
    if extras:
        env.update(extras)
    return env


def _insert_asset(db: Path) -> int:
    from datetime import UTC, datetime
    with connect(db) as conn:
        return assets_dal.insert_manual(
            conn, hostname="srv", primary_ip="10.0.0.1", now=datetime.now(UTC),
        )


def test_enable_ssh_command_requires_command_user_and_credential(tmp_path: Path) -> None:
    h = _init_home(tmp_path)
    _insert_asset(h / ".langusta" / "db.sqlite")
    runner.invoke(
        app,
        ["cred", "add", "--label", "ssh", "--kind", "ssh_password"],
        env=_env(h, {"LANGUSTA_CRED_SECRET": "hunter2"}),
    )
    r = runner.invoke(
        app,
        ["monitor", "enable", "--asset", "1", "--kind", "ssh_command",
         "--credential-label", "ssh", "--user", "root"],
        env=_env(h),
    )
    # missing --command should fail
    assert r.exit_code != 0


def test_enable_ssh_command_stores_row(tmp_path: Path) -> None:
    h = _init_home(tmp_path)
    _insert_asset(h / ".langusta" / "db.sqlite")
    runner.invoke(
        app,
        ["cred", "add", "--label", "ssh", "--kind", "ssh_password"],
        env=_env(h, {"LANGUSTA_CRED_SECRET": "hunter2"}),
    )
    r = runner.invoke(
        app,
        ["monitor", "enable", "--asset", "1", "--kind", "ssh_command",
         "--command", "uptime", "--user", "root",
         "--credential-label", "ssh", "--interval", "60"],
        env=_env(h),
    )
    assert r.exit_code == 0, r.stdout
    with connect(h / ".langusta" / "db.sqlite") as conn:
        [check] = mon_dal.list_checks(conn)
    assert check.kind == "ssh_command"
    assert check.command == "uptime"
    assert check.username == "root"
    assert check.credential_id is not None


def test_monitor_run_ssh_command_stub_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = _init_home(tmp_path)
    _insert_asset(h / ".langusta" / "db.sqlite")
    runner.invoke(
        app,
        ["cred", "add", "--label", "ssh", "--kind", "ssh_password"],
        env=_env(h, {"LANGUSTA_CRED_SECRET": "hunter2"}),
    )
    runner.invoke(
        app,
        ["monitor", "enable", "--asset", "1", "--kind", "ssh_command",
         "--command", "uptime", "--user", "root",
         "--credential-label", "ssh", "--interval", "60"],
        env=_env(h),
    )

    from langusta.monitor.ssh.stub_backend import Response, StubBackend
    stub = StubBackend({("10.0.0.1", "uptime"): Response(exit_code=0, stdout="up 3 days")})
    monkeypatch.setattr("langusta.monitor.runner.AsyncsshBackend", lambda: stub)

    r = runner.invoke(app, ["monitor", "run"], env=_env(h))
    assert r.exit_code == 0, r.stdout
    assert "1 ok" in r.stdout


def test_monitor_run_ssh_command_stub_fails_writes_timeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = _init_home(tmp_path)
    _insert_asset(h / ".langusta" / "db.sqlite")
    runner.invoke(
        app,
        ["cred", "add", "--label", "ssh", "--kind", "ssh_password"],
        env=_env(h, {"LANGUSTA_CRED_SECRET": "hunter2"}),
    )
    runner.invoke(
        app,
        ["monitor", "enable", "--asset", "1", "--kind", "ssh_command",
         "--command", "false", "--user", "root",
         "--credential-label", "ssh", "--interval", "60"],
        env=_env(h),
    )
    from langusta.monitor.ssh.stub_backend import Response, StubBackend
    stub = StubBackend({("10.0.0.1", "false"): Response(exit_code=1, stderr="nope")})
    monkeypatch.setattr("langusta.monitor.runner.AsyncsshBackend", lambda: stub)

    r = runner.invoke(app, ["monitor", "run"], env=_env(h))
    assert r.exit_code == 0, r.stdout
    assert "1 fail" in r.stdout
    assert "1 state transition" in r.stdout

    from langusta.db import timeline as tl_dal
    with connect(h / ".langusta" / "db.sqlite") as conn:
        entries = tl_dal.list_by_asset(conn, asset_id=1)
    monitor_entries = [e for e in entries if e.kind == "monitor_event"]
    assert len(monitor_entries) == 1
    assert "ssh_command" in monitor_entries[0].body
