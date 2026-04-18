"""Integration tests for `langusta monitor enable --kind snmp_oid` + run."""

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
            conn, hostname="sw", primary_ip="10.0.0.1", now=datetime.now(UTC),
        )


def test_enable_snmp_oid_requires_oid_and_credential(tmp_path: Path) -> None:
    h = _init_home(tmp_path)
    _insert_asset(h / ".langusta" / "db.sqlite")

    # Missing --oid
    r = runner.invoke(
        app,
        ["monitor", "enable", "--asset", "1", "--kind", "snmp_oid",
         "--credential-label", "lab"],
        env=_env(h),
    )
    assert r.exit_code != 0


def test_enable_snmp_oid_stores_row(tmp_path: Path) -> None:
    h = _init_home(tmp_path)
    _insert_asset(h / ".langusta" / "db.sqlite")

    # Seed credential.
    r = runner.invoke(
        app,
        ["cred", "add", "--label", "snmp", "--kind", "snmp_v2c"],
        env=_env(h, {"LANGUSTA_CRED_SECRET": "public"}),
    )
    assert r.exit_code == 0

    r = runner.invoke(
        app,
        ["monitor", "enable", "--asset", "1", "--kind", "snmp_oid",
         "--oid", "1.3.6.1.2.1.1.3.0",
         "--credential-label", "snmp",
         "--interval", "60"],
        env=_env(h),
    )
    assert r.exit_code == 0, r.stdout
    assert "enabled check" in r.stdout

    with connect(h / ".langusta" / "db.sqlite") as conn:
        [check] = mon_dal.list_checks(conn)
    assert check.kind == "snmp_oid"
    assert check.oid == "1.3.6.1.2.1.1.3.0"
    assert check.credential_id is not None


def test_enable_snmp_oid_with_comparator(tmp_path: Path) -> None:
    h = _init_home(tmp_path)
    _insert_asset(h / ".langusta" / "db.sqlite")
    runner.invoke(
        app,
        ["cred", "add", "--label", "snmp", "--kind", "snmp_v2c"],
        env=_env(h, {"LANGUSTA_CRED_SECRET": "public"}),
    )
    r = runner.invoke(
        app,
        ["monitor", "enable", "--asset", "1", "--kind", "snmp_oid",
         "--oid", "1.3.6.1.2.1.1.1.0",
         "--expected", "Cisco",
         "--comparator", "contains",
         "--credential-label", "snmp",
         "--interval", "60"],
        env=_env(h),
    )
    assert r.exit_code == 0, r.stdout

    with connect(h / ".langusta" / "db.sqlite") as conn:
        [check] = mon_dal.list_checks(conn)
    assert check.comparator == "contains"
    assert check.expected_value == "Cisco"


def test_enable_snmp_oid_comparator_requires_expected(tmp_path: Path) -> None:
    h = _init_home(tmp_path)
    _insert_asset(h / ".langusta" / "db.sqlite")
    runner.invoke(
        app,
        ["cred", "add", "--label", "snmp", "--kind", "snmp_v2c"],
        env=_env(h, {"LANGUSTA_CRED_SECRET": "public"}),
    )
    r = runner.invoke(
        app,
        ["monitor", "enable", "--asset", "1", "--kind", "snmp_oid",
         "--oid", "1.3.6.1.2.1.1.1.0",
         "--comparator", "eq",
         "--credential-label", "snmp"],
        env=_env(h),
    )
    assert r.exit_code != 0


def test_monitor_run_snmp_oid_writes_timeline_on_transition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: monitor run executes snmp_oid via the stubbed client and
    writes a monitor_event timeline entry when transitioning."""
    h = _init_home(tmp_path)
    _insert_asset(h / ".langusta" / "db.sqlite")
    runner.invoke(
        app,
        ["cred", "add", "--label", "snmp", "--kind", "snmp_v2c"],
        env=_env(h, {"LANGUSTA_CRED_SECRET": "public"}),
    )
    runner.invoke(
        app,
        ["monitor", "enable", "--asset", "1", "--kind", "snmp_oid",
         "--oid", "1.3.6.1.2.1.1.1.0",
         "--expected", "Cisco",
         "--comparator", "contains",
         "--credential-label", "snmp",
         "--interval", "60"],
        env=_env(h),
    )

    # Patch the runner's SNMP client to a scripted stub: returns MikroTik,
    # which should fail the "contains Cisco" comparator.
    from langusta.scan.snmp.transcript_backend import TranscriptBackend
    fake = TranscriptBackend.from_dict(
        {"10.0.0.1": {"oids": {"1.3.6.1.2.1.1.1.0": "MikroTik RouterOS"}}},
    )
    monkeypatch.setattr(
        "langusta.monitor.runner.PysnmpBackend",
        lambda: fake,
    )

    r = runner.invoke(app, ["monitor", "run"], env=_env(h))
    assert r.exit_code == 0, r.stdout
    assert "1 state transition" in r.stdout  # first fail counts as a transition

    from langusta.db import timeline as tl_dal
    with connect(h / ".langusta" / "db.sqlite") as conn:
        entries = tl_dal.list_by_asset(conn, asset_id=1)
    monitor_entries = [e for e in entries if e.kind == "monitor_event"]
    assert len(monitor_entries) == 1
    assert "snmp_oid" in monitor_entries[0].body
    assert "MikroTik" in monitor_entries[0].body


def test_monitor_run_snmp_oid_ok_when_comparator_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = _init_home(tmp_path)
    _insert_asset(h / ".langusta" / "db.sqlite")
    runner.invoke(
        app,
        ["cred", "add", "--label", "snmp", "--kind", "snmp_v2c"],
        env=_env(h, {"LANGUSTA_CRED_SECRET": "public"}),
    )
    runner.invoke(
        app,
        ["monitor", "enable", "--asset", "1", "--kind", "snmp_oid",
         "--oid", "1.3.6.1.2.1.1.1.0",
         "--expected", "Cisco",
         "--comparator", "contains",
         "--credential-label", "snmp",
         "--interval", "60"],
        env=_env(h),
    )
    from langusta.scan.snmp.transcript_backend import TranscriptBackend
    fake = TranscriptBackend.from_dict(
        {"10.0.0.1": {"oids": {"1.3.6.1.2.1.1.1.0": "Cisco IOS 15.2"}}},
    )
    monkeypatch.setattr("langusta.monitor.runner.PysnmpBackend", lambda: fake)
    r = runner.invoke(app, ["monitor", "run"], env=_env(h))
    assert r.exit_code == 0, r.stdout
    assert "1 ok" in r.stdout
