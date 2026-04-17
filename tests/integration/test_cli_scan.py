"""Integration tests for `langusta scan` — the wedge.

Exit criterion for M2:
  - `langusta scan 192.168.1.0/24` prints "Found N devices in M seconds".
  - Populates inventory; re-scan does not duplicate assets.
  - Conflicting observations land in `langusta review`.

Tests use the ICMP injection path via environment — see conftest.py for
the ping-stub wiring.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from langusta.cli import app
from langusta.db import assets as assets_dal
from langusta.db.connection import connect

runner = CliRunner()


def _init(home: Path):
    return runner.invoke(app, ["init"], env={"HOME": str(home)})


def _scan(home: Path, target: str):
    return runner.invoke(app, ["scan", target], env={"HOME": str(home)})


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Initialised home with a deterministic ICMP stub patched in."""
    h = tmp_path / "home"
    h.mkdir()

    # Patch ping_sweep to return a known set of alive hosts.
    from langusta.scan import icmp as _icmp

    async def fake_ping(targets, **_):
        alive = {"10.0.0.1", "10.0.0.2"}
        return [
            _icmp.PingResult(address=t, is_alive=True, rtt_ms=1.0)
            for t in targets
            if t in alive
        ]

    monkeypatch.setattr("langusta.scan.orchestrator.ping_sweep", fake_ping)

    # Patch the platform backend so arp_table returns a known map.
    class _StubBackend:
        def arp_table(self):
            return iter([
                ("10.0.0.1", "aa:bb:cc:00:00:01"),
                ("10.0.0.2", "aa:bb:cc:00:00:02"),
            ])

        def enforce_private(self, path):  # pragma: no cover
            import os
            mode = 0o700 if path.is_dir() else 0o600
            os.chmod(path, mode)

    # CLI binds `get_backend` at import time, so patch the CLI's local ref.
    monkeypatch.setattr("langusta.cli.get_backend", lambda: _StubBackend())

    _init(h)
    return h


def test_scan_prints_result_line(home: Path) -> None:
    r = _scan(home, "10.0.0.0/30")
    assert r.exit_code == 0, r.stdout
    assert "Found" in r.stdout
    # 2 alive hosts per the stub.
    assert "2" in r.stdout


def test_scan_inserts_alive_hosts_into_inventory(home: Path) -> None:
    r = _scan(home, "10.0.0.0/30")
    assert r.exit_code == 0, r.stdout
    with connect(home / ".langusta" / "db.sqlite") as conn:
        rows = assets_dal.list_all(conn)
    assert {r.primary_ip for r in rows} == {"10.0.0.1", "10.0.0.2"}
    mac_map = {r.primary_ip: r.macs for r in rows}
    assert mac_map["10.0.0.1"] == ["aa:bb:cc:00:00:01"]


def test_rescan_does_not_duplicate_assets(home: Path) -> None:
    _scan(home, "10.0.0.0/30")
    _scan(home, "10.0.0.0/30")
    with connect(home / ".langusta" / "db.sqlite") as conn:
        rows = assets_dal.list_all(conn)
    assert len(rows) == 2


def test_scan_source_is_recorded_as_scanned(home: Path) -> None:
    _scan(home, "10.0.0.0/30")
    with connect(home / ".langusta" / "db.sqlite") as conn:
        rows = assets_dal.list_all(conn)
    assert all(r.source == "scanned" for r in rows)


def test_scan_reports_counts(home: Path) -> None:
    r = _scan(home, "10.0.0.0/30")
    lowered = r.stdout.lower()
    assert "2" in lowered
    # first run should show 2 inserted
    assert "inserted" in lowered or "new" in lowered or "found" in lowered


def test_scan_with_invalid_target_is_user_error(home: Path) -> None:
    r = _scan(home, "not-a-subnet")
    assert r.exit_code != 0
