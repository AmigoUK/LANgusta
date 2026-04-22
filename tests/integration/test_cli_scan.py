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


# ---------------------------------------------------------------------------
# scan --snmp — v2c and v3 credential paths
# ---------------------------------------------------------------------------

PW = "master-password-for-tests-long-enough"


def _init_with_password(home: Path):
    return runner.invoke(
        app, ["init"],
        env={"HOME": str(home), "LANGUSTA_MASTER_PASSWORD": PW},
    )


@pytest.fixture
def home_with_snmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Initialised home with master password and a patched SNMP client.

    Replaces `PysnmpBackend` with a TranscriptBackend that returns a known
    sys_descr for 10.0.0.1 regardless of auth — enough to verify the CLI
    plumbs credentials through without opening network sockets.
    """
    from langusta.scan import icmp as _icmp
    from langusta.scan.snmp.transcript_backend import TranscriptBackend

    h = tmp_path / "home"
    h.mkdir()

    async def fake_ping(targets, **_):
        alive = {"10.0.0.1"}
        return [
            _icmp.PingResult(address=t, is_alive=True, rtt_ms=1.0)
            for t in targets
            if t in alive
        ]

    monkeypatch.setattr("langusta.scan.orchestrator.ping_sweep", fake_ping)

    class _StubBackend:
        def arp_table(self):
            return iter([("10.0.0.1", "aa:bb:cc:00:00:01")])

        def enforce_private(self, path):  # pragma: no cover
            import os
            os.chmod(path, 0o700 if path.is_dir() else 0o600)

    monkeypatch.setattr("langusta.cli.get_backend", lambda: _StubBackend())

    _snmp_fixture = TranscriptBackend.from_dict({
        "10.0.0.1": {"sys_descr": "MikroTik RouterOS 7.11.2"},
    })
    monkeypatch.setattr(
        "langusta.scan.snmp.pysnmp_backend.PysnmpBackend",
        lambda: _snmp_fixture,
    )

    _init_with_password(h)
    return h


def _env_pw(home: Path, extras: dict[str, str] | None = None) -> dict[str, str]:
    env = {"HOME": str(home), "LANGUSTA_MASTER_PASSWORD": PW}
    if extras:
        env.update(extras)
    return env


def test_scan_with_snmp_v2c_credential(home_with_snmp: Path) -> None:
    h = home_with_snmp
    r = runner.invoke(
        app,
        ["cred", "add", "--label", "v2c", "--kind", "snmp_v2c"],
        env=_env_pw(h, {"LANGUSTA_CRED_SECRET": "public"}),
    )
    assert r.exit_code == 0, r.stdout

    r = runner.invoke(
        app, ["scan", "10.0.0.0/30", "--snmp", "v2c"],
        env=_env_pw(h),
    )
    assert r.exit_code == 0, r.stdout + (r.stderr or "")

    with connect(h / ".langusta" / "db.sqlite") as conn:
        [asset] = assets_dal.list_all(conn)
    assert asset.detected_os == "MikroTik RouterOS 7.11.2"


def test_scan_with_snmp_v3_credential(home_with_snmp: Path) -> None:
    h = home_with_snmp
    env = _env_pw(h, {
        "LANGUSTA_CRED_V3_USER": "admin",
        "LANGUSTA_CRED_V3_AUTH_PROTO": "SHA",
        "LANGUSTA_CRED_V3_AUTH_PASS": "authpass-long-enough",
        "LANGUSTA_CRED_V3_PRIV_PROTO": "AES-128",
        "LANGUSTA_CRED_V3_PRIV_PASS": "privpass-long-enough",
    })
    r = runner.invoke(
        app,
        ["cred", "add", "--label", "v3", "--kind", "snmp_v3"],
        env=env,
    )
    assert r.exit_code == 0, r.stdout

    r = runner.invoke(
        app, ["scan", "10.0.0.0/30", "--snmp", "v3"],
        env=_env_pw(h),
    )
    assert r.exit_code == 0, r.stdout + (r.stderr or "")

    with connect(h / ".langusta" / "db.sqlite") as conn:
        [asset] = assets_dal.list_all(conn)
    assert asset.detected_os == "MikroTik RouterOS 7.11.2"


def test_scan_with_snmp_rejects_non_snmp_credential(home_with_snmp: Path) -> None:
    h = home_with_snmp
    # Add an SSH credential, then try to use it with --snmp.
    r = runner.invoke(
        app,
        ["cred", "add", "--label", "ssh", "--kind", "ssh_key"],
        env=_env_pw(h, {"LANGUSTA_CRED_SECRET": "FAKE-KEY-PEM"}),
    )
    assert r.exit_code == 0

    r = runner.invoke(
        app, ["scan", "10.0.0.0/30", "--snmp", "ssh"],
        env=_env_pw(h),
    )
    assert r.exit_code != 0
    assert "snmp_v2c or snmp_v3" in (r.stdout + (r.stderr or ""))


def test_scan_with_snmp_unknown_label(home_with_snmp: Path) -> None:
    h = home_with_snmp
    r = runner.invoke(
        app, ["scan", "10.0.0.0/30", "--snmp", "nope"],
        env=_env_pw(h),
    )
    assert r.exit_code != 0
    assert "nope" in (r.stdout + (r.stderr or ""))


# ---------------------------------------------------------------------------
# Wave-3 TEST-T-016 — SocketPermissionError surfaces the capability hint
# ---------------------------------------------------------------------------


def test_scan_socket_permission_error_surfaces_ping_group_range_hint(
    home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unprivileged ICMP on Linux requires `net.ipv4.ping_group_range`
    to cover the runner's GID — a niche kernel knob that trips up
    first-time users. The CLI has a tailored hint for this case; this
    test exercises the rendering end-to-end so a future refactor of
    the CLI's try/except for SocketPermissionError can't silently drop
    or rename the hint."""
    from icmplib.exceptions import SocketPermissionError

    async def boom(*_args: object, **_kwargs: object) -> None:
        raise SocketPermissionError("raw socket requires CAP_NET_RAW")

    # `cli.scan` imports `run_scan` from this module at call time, so
    # patching the module attribute here is what the CLI actually sees.
    monkeypatch.setattr("langusta.scan.orchestrator.run_scan", boom)

    r = runner.invoke(
        app, ["scan", "10.0.0.0/30"], env={"HOME": str(home)},
    )

    assert r.exit_code == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    combined = (r.stdout or "") + (r.stderr or "")
    assert "ping_group_range" in combined, (
        "SocketPermissionError hint must name net.ipv4.ping_group_range "
        "so the user knows what to set; otherwise they get a bare "
        "traceback"
    )
    assert "sysctl" in combined, (
        "hint must name the `sysctl` command the user should run"
    )
