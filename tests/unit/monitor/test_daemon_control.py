"""Unit tests — PID-file-based daemon control helpers."""

from __future__ import annotations

import os
import signal
import sys
from pathlib import Path

import pytest

from langusta.monitor.daemon_control import (
    clear_pid_file,
    is_process_alive,
    read_pid_file,
    stop_via_pid_file,
    write_pid_file,
)


def test_read_pid_file_missing(tmp_path: Path) -> None:
    state = read_pid_file(tmp_path / "missing.pid")
    assert state.pid is None
    assert state.alive is False


def test_read_pid_file_empty(tmp_path: Path) -> None:
    path = tmp_path / "m.pid"
    path.write_text("", encoding="utf-8")
    assert read_pid_file(path).pid is None


def test_read_pid_file_garbage(tmp_path: Path) -> None:
    path = tmp_path / "m.pid"
    path.write_text("not a number", encoding="utf-8")
    assert read_pid_file(path).pid is None


def test_read_pid_file_stale_pid_returns_alive_false(tmp_path: Path) -> None:
    """An unused high PID reports as not alive."""
    # PID 9_999_999 is practically guaranteed to not be in use.
    path = tmp_path / "m.pid"
    write_pid_file(path, 9_999_999)
    state = read_pid_file(path)
    assert state.pid == 9_999_999
    assert state.alive is False


def test_read_pid_file_self_pid_returns_alive_true(tmp_path: Path) -> None:
    path = tmp_path / "m.pid"
    write_pid_file(path, os.getpid())
    state = read_pid_file(path)
    assert state.pid == os.getpid()
    assert state.alive is True


def test_write_pid_file_creates_parent(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "m.pid"
    write_pid_file(path, 1234)
    assert path.read_text(encoding="utf-8").strip() == "1234"


def test_clear_pid_file_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "m.pid"
    clear_pid_file(path)   # missing is fine
    write_pid_file(path, 1234)
    clear_pid_file(path)
    assert not path.exists()


def test_is_process_alive_rejects_zero_and_negative() -> None:
    assert is_process_alive(0) is False
    assert is_process_alive(-1) is False


def test_is_process_alive_for_current_process() -> None:
    assert is_process_alive(os.getpid()) is True


def test_write_pid_file_does_not_follow_symlink(tmp_path: Path) -> None:
    """Wave-3 TEST-S-006. If an attacker with local filesystem access
    plants a symlink at `~/.langusta/monitor.pid` pointing at some
    user-owned file elsewhere, write_pid_file() must NOT follow the
    symlink and clobber the target. The pid-file location is
    predictable and attacker-controllable via /tmp scoreboards."""
    target = tmp_path / "innocent.txt"
    target.write_text("IMPORTANT-USER-FILE", encoding="utf-8")
    link = tmp_path / "monitor.pid"
    os.symlink(target, link)

    with pytest.raises((OSError, FileExistsError)):
        write_pid_file(link, 1234)

    assert target.read_text(encoding="utf-8") == "IMPORTANT-USER-FILE", (
        "write_pid_file followed the symlink and clobbered the target"
    )


def test_stop_via_pid_file_noop_when_file_missing(tmp_path: Path) -> None:
    state = stop_via_pid_file(tmp_path / "absent.pid")
    assert state.pid is None
    assert state.alive is False


def test_stop_via_pid_file_cleans_stale_entry(tmp_path: Path) -> None:
    path = tmp_path / "m.pid"
    write_pid_file(path, 9_999_999)
    state = stop_via_pid_file(path)
    assert state.alive is False
    assert not path.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals only")
def test_stop_via_pid_file_sends_signal_to_live_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The helper must os.kill() the recorded PID.

    We can't easily test the full lifecycle in-process (orphan zombies
    keep `os.kill(pid, 0)` succeeding until reaped), so assert on the
    observable: `os.kill` was invoked with the recorded PID and signal.
    """
    calls: list[tuple[int, int]] = []
    real_kill = os.kill

    def _spy_kill(pid: int, sig: int) -> None:
        calls.append((pid, sig))
        if sig == 0:  # liveness probes still need real semantics
            real_kill(pid, sig)
        # Signal of actual termination is swallowed — we simulate death
        # by mutating the return of `is_process_alive` via the PID file
        # write/read path.

    path = tmp_path / "m.pid"
    write_pid_file(path, os.getpid())    # seed with "live" PID
    monkeypatch.setattr("langusta.monitor.daemon_control.os.kill", _spy_kill)

    # Patch is_process_alive to flip to False on second call (post-signal).
    state_box = {"alive_after_signal": False}

    def _fake_alive(pid: int) -> bool:
        if pid == os.getpid() and state_box.get("_invoked"):
            return state_box["alive_after_signal"]
        state_box["_invoked"] = True
        return True

    monkeypatch.setattr(
        "langusta.monitor.daemon_control.is_process_alive", _fake_alive,
    )

    result = stop_via_pid_file(
        path, sig=signal.SIGTERM, timeout_seconds=1.0, poll_interval=0.05,
    )

    # It sent SIGTERM to the recorded PID and, upon observing that the
    # process no longer responds, cleared the PID file.
    assert (os.getpid(), signal.SIGTERM) in calls
    assert result.alive is False
    assert not path.exists()
