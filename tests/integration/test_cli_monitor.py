"""Integration tests for `langusta monitor ...` subcommands."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from langusta.cli import app
from langusta.db import assets as assets_dal
from langusta.db import monitoring as mon_dal
from langusta.db import timeline as tl_dal
from langusta.db.connection import connect

runner = CliRunner()

PW = "master-password-for-monitor-tests-ok"


def _env(home: Path) -> dict[str, str]:
    return {"HOME": str(home), "LANGUSTA_MASTER_PASSWORD": PW}


@pytest.fixture
def home(tmp_path: Path) -> Path:
    h = tmp_path / "home"
    h.mkdir(parents=True)
    runner.invoke(app, ["init"], env=_env(h))
    runner.invoke(
        app, ["add", "--hostname", "router", "--ip", "10.0.0.1"], env=_env(h),
    )
    return h


def _asset_id(home: Path) -> int:
    with connect(home / ".langusta" / "db.sqlite") as conn:
        [asset] = assets_dal.list_all(conn)
    return asset.id


# ---------------------------------------------------------------------------
# enable / list / disable
# ---------------------------------------------------------------------------


def test_monitor_enable_icmp(home: Path) -> None:
    aid = _asset_id(home)
    r = runner.invoke(
        app,
        ["monitor", "enable", "--asset", str(aid), "--kind", "icmp", "--interval", "60"],
        env=_env(home),
    )
    assert r.exit_code == 0, r.stdout
    with connect(home / ".langusta" / "db.sqlite") as conn:
        checks = mon_dal.list_checks(conn, asset_id=aid)
    assert len(checks) == 1
    assert checks[0].kind == "icmp"
    assert checks[0].interval_seconds == 60


def test_monitor_enable_http_with_port(home: Path) -> None:
    aid = _asset_id(home)
    r = runner.invoke(
        app,
        [
            "monitor", "enable",
            "--asset", str(aid), "--kind", "http",
            "--interval", "300",
            "--port", "443", "--path", "/healthz",
        ],
        env=_env(home),
    )
    assert r.exit_code == 0, r.stdout
    with connect(home / ".langusta" / "db.sqlite") as conn:
        [check] = mon_dal.list_checks(conn, asset_id=aid)
    assert check.kind == "http"
    assert check.port == 443
    assert check.path == "/healthz"


def test_monitor_list_shows_checks(home: Path) -> None:
    aid = _asset_id(home)
    runner.invoke(
        app,
        ["monitor", "enable", "--asset", str(aid), "--kind", "icmp", "--interval", "60"],
        env=_env(home),
    )
    r = runner.invoke(app, ["monitor", "list"], env=_env(home))
    assert r.exit_code == 0
    assert "icmp" in r.stdout


def test_monitor_list_empty_is_friendly(home: Path) -> None:
    r = runner.invoke(app, ["monitor", "list"], env=_env(home))
    assert r.exit_code == 0
    assert "no checks" in r.stdout.lower() or "none" in r.stdout.lower()


def test_monitor_disable_flips_enabled(home: Path) -> None:
    aid = _asset_id(home)
    runner.invoke(
        app,
        ["monitor", "enable", "--asset", str(aid), "--kind", "icmp", "--interval", "60"],
        env=_env(home),
    )
    with connect(home / ".langusta" / "db.sqlite") as conn:
        [check] = mon_dal.list_checks(conn, asset_id=aid)
    r = runner.invoke(app, ["monitor", "disable", str(check.id)], env=_env(home))
    assert r.exit_code == 0
    with connect(home / ".langusta" / "db.sqlite") as conn:
        [check2] = mon_dal.list_checks(conn, asset_id=aid)
    assert check2.enabled is False


# ---------------------------------------------------------------------------
# run (single-shot)
# ---------------------------------------------------------------------------


def test_monitor_run_executes_due_checks(
    home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    aid = _asset_id(home)
    runner.invoke(
        app,
        ["monitor", "enable", "--asset", str(aid), "--kind", "icmp", "--interval", "60"],
        env=_env(home),
    )
    # Patch the IcmpCheck to a stub that always reports OK.
    from langusta.monitor.checks.base import CheckResult

    class _StubIcmp:
        async def run(self, *, target: str, **_: object):
            return CheckResult(status="ok", latency_ms=1.0, detail=None)

    monkeypatch.setattr(
        "langusta.monitor.runner.DEFAULT_REGISTRY",
        {"icmp": _StubIcmp(), "tcp": _StubIcmp(), "http": _StubIcmp()},
    )
    r = runner.invoke(app, ["monitor", "run"], env=_env(home))
    assert r.exit_code == 0, r.stdout
    # Output mentions the one executed check.
    assert "1" in r.stdout or "executed" in r.stdout.lower()


def test_monitor_run_timeline_shows_failure_on_first_fail(
    home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    aid = _asset_id(home)
    runner.invoke(
        app,
        ["monitor", "enable", "--asset", str(aid), "--kind", "icmp", "--interval", "60"],
        env=_env(home),
    )
    from langusta.monitor.checks.base import CheckResult

    class _StubDown:
        async def run(self, *, target: str, **_: object):
            return CheckResult(status="fail", latency_ms=None, detail="unreachable")

    monkeypatch.setattr(
        "langusta.monitor.runner.DEFAULT_REGISTRY",
        {"icmp": _StubDown(), "tcp": _StubDown(), "http": _StubDown()},
    )
    r = runner.invoke(app, ["monitor", "run"], env=_env(home))
    assert r.exit_code == 0, r.stdout
    with connect(home / ".langusta" / "db.sqlite") as conn:
        entries = tl_dal.list_by_asset(conn, aid)
    assert any(e.kind == "monitor_event" for e in entries)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_monitor_status_no_heartbeat(home: Path) -> None:
    r = runner.invoke(app, ["monitor", "status"], env=_env(home))
    assert r.exit_code == 0
    assert "never" in r.stdout.lower() or "no heartbeat" in r.stdout.lower()


def test_monitor_status_after_run(
    home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    aid = _asset_id(home)
    runner.invoke(
        app,
        ["monitor", "enable", "--asset", str(aid), "--kind", "icmp", "--interval", "60"],
        env=_env(home),
    )
    from langusta.monitor.checks.base import CheckResult

    class _Stub:
        async def run(self, *, target: str, **_: object):
            return CheckResult(status="ok", latency_ms=1.0, detail=None)

    monkeypatch.setattr(
        "langusta.monitor.runner.DEFAULT_REGISTRY",
        {"icmp": _Stub(), "tcp": _Stub(), "http": _Stub()},
    )
    runner.invoke(app, ["monitor", "run"], env=_env(home))
    r = runner.invoke(app, ["monitor", "status"], env=_env(home))
    assert r.exit_code == 0
    # Should show a recent heartbeat timestamp.
    assert "heartbeat" in r.stdout.lower()


# ---------------------------------------------------------------------------
# monitor start / stop / daemon PID-file lifecycle
# (Wave-3 TEST-M-006 + TEST-M-007)
# ---------------------------------------------------------------------------


class _FakePopenProc:
    """Minimal stand-in for `subprocess.Popen`'s return value."""

    def __init__(self, pid: int) -> None:
        self.pid = pid


def test_monitor_start_does_not_leak_master_password_to_daemon_env(
    home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LANGUSTA_MASTER_PASSWORD must not be inherited into the detached
    daemon's environment. The daemon doesn't need it -- it unlocks the
    vault at start via `_unlock_vault()` (which re-reads the env var
    inside its own process) -- and leaving it in /proc/<pid>/environ
    exposes it to any local process that can stat that pid. Wave-3
    finding S-005."""
    import subprocess

    captured: dict[str, object] = {}

    def spy_popen(*_args: object, **kwargs: object) -> _FakePopenProc:
        captured.update(kwargs)
        return _FakePopenProc(pid=4242)

    monkeypatch.setattr(subprocess, "Popen", spy_popen)

    env = {**_env(home), "LANGUSTA_MASTER_PASSWORD": "super-secret-pw"}
    r = runner.invoke(app, ["monitor", "start"], env=env)
    assert r.exit_code == 0, r.stdout

    passed_env = captured.get("env")
    assert isinstance(passed_env, dict), (
        "monitor start must pass env= explicitly to Popen — otherwise "
        "the subprocess inherits the caller's entire environment, "
        f"including LANGUSTA_MASTER_PASSWORD. Got env={passed_env!r}"
    )
    assert "LANGUSTA_MASTER_PASSWORD" not in passed_env, (
        "master password leaked into daemon environment"
    )


def test_monitor_start_writes_pid_file_on_first_invocation(
    home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First `monitor start` with no existing PID file spawns the daemon
    and records its PID in `~/.langusta/monitor.pid`."""
    import subprocess

    def fake_popen(*_args: object, **_kwargs: object) -> _FakePopenProc:
        return _FakePopenProc(pid=12345)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    r = runner.invoke(app, ["monitor", "start"], env=_env(home))

    assert r.exit_code == 0, r.stdout
    pid_file = home / ".langusta" / "monitor.pid"
    assert pid_file.exists()
    assert pid_file.read_text().strip() == "12345"


def test_monitor_start_refuses_when_daemon_already_running(
    home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A live PID file must block a second `monitor start` — not silently
    fork a rival daemon."""
    import os
    import subprocess

    pid_file = home / ".langusta" / "monitor.pid"
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(f"{os.getpid()}\n")

    popen_called: list[int] = []

    def spy_popen(*_args: object, **_kwargs: object) -> _FakePopenProc:
        popen_called.append(1)
        return _FakePopenProc(pid=999)

    monkeypatch.setattr(subprocess, "Popen", spy_popen)

    r = runner.invoke(app, ["monitor", "start"], env=_env(home))

    assert r.exit_code != 0
    combined = (r.stdout or "") + (r.stderr or "")
    assert "already running" in combined.lower()
    assert popen_called == [], "must not spawn a second daemon"


def test_monitor_start_clears_stale_pid_file_before_spawning(
    home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale PID file (recorded PID not running) must not block start —
    it should be cleared and the new daemon's PID recorded instead."""
    import subprocess

    pid_file = home / ".langusta" / "monitor.pid"
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    # A very high PID value that is practically guaranteed not to be in use.
    pid_file.write_text("9999999\n")

    def fake_popen(*_args: object, **_kwargs: object) -> _FakePopenProc:
        return _FakePopenProc(pid=54321)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    r = runner.invoke(app, ["monitor", "start"], env=_env(home))

    assert r.exit_code == 0, r.stdout
    assert pid_file.read_text().strip() == "54321", (
        "stale PID must be replaced with the newly-spawned daemon's PID"
    )


def test_monitor_daemon_clears_pid_file_on_keyboardinterrupt(
    home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulate SIGTERM during the sleep between cycles. The `finally:
    clear_pid_file` in `monitor_daemon` must run, or `monitor stop` would
    see a stale PID file forever."""
    import time as _time

    def tripwire_sleep(_interval: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(_time, "sleep", tripwire_sleep)

    runner.invoke(
        app,
        ["monitor", "daemon", "--foreground", "--interval", "60"],
        env=_env(home),
    )

    pid_file = home / ".langusta" / "monitor.pid"
    assert not pid_file.exists(), (
        "PID file leaked after KeyboardInterrupt; monitor stop would "
        "report stale forever"
    )


def test_monitor_daemon_refuses_to_overwrite_running_pid_file(
    home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second `monitor daemon --foreground` against a live PID file must
    exit 1 and leave the recorded PID untouched — silently clobbering
    the file would orphan the original daemon in `monitor stop`."""
    import os

    pid_file = home / ".langusta" / "monitor.pid"
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(f"{os.getpid()}\n")
    original = pid_file.read_text()

    # Belt-and-braces: if the refusal logic isn't in place yet, make sure
    # the loop can't run indefinitely by tripping on the first sleep.
    import time as _time

    def tripwire_sleep(_interval: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(_time, "sleep", tripwire_sleep)

    r = runner.invoke(
        app,
        ["monitor", "daemon", "--foreground", "--interval", "60"],
        env=_env(home),
    )

    assert r.exit_code == 1
    assert pid_file.read_text() == original, (
        "PID file was clobbered by a second daemon invocation"
    )


# ---------------------------------------------------------------------------
# Wave-3 TEST-T-002 — monitor stop exit codes across the 3 branches
# ---------------------------------------------------------------------------


def test_monitor_stop_exits_1_when_pid_file_is_missing(home: Path) -> None:
    """No PID file → exit 1 with a clear "no monitor daemon" message."""
    r = runner.invoke(app, ["monitor", "stop"], env=_env(home))
    assert r.exit_code == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    combined = (r.stdout + (r.stderr or "")).lower()
    assert "no monitor daemon" in combined


def test_monitor_stop_exits_0_and_clears_stale_pid_file(home: Path) -> None:
    """A stale PID file (recorded PID not running) is a no-op success
    — it gets cleared and the command exits 0."""
    pid_file = home / ".langusta" / "monitor.pid"
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text("9999999\n")

    r = runner.invoke(app, ["monitor", "stop"], env=_env(home))

    assert r.exit_code == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert not pid_file.exists(), "stale PID file was not cleared"


def test_monitor_stop_exits_2_on_graceful_shutdown_timeout(
    home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the daemon doesn't exit within --timeout, stop returns
    exit code 2 (distinct from 1) so scripts can tell a missing PID
    apart from a stuck daemon."""
    from langusta.monitor import daemon_control

    class _LiveState:
        path = home / ".langusta" / "monitor.pid"
        pid = 123
        alive = True

    class _StuckResult:
        path = _LiveState.path
        pid = 123
        alive = True  # still alive after the timeout -> stop returns 2

    monkeypatch.setattr(
        daemon_control, "read_pid_file", lambda _p: _LiveState(),
    )
    monkeypatch.setattr(
        daemon_control, "stop_via_pid_file",
        lambda _p, *, timeout_seconds: _StuckResult(),
    )

    r = runner.invoke(app, ["monitor", "stop"], env=_env(home))
    assert r.exit_code == 2, f"stdout={r.stdout!r} stderr={r.stderr!r}"
