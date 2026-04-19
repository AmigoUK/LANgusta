"""PID-file-based daemon control for `langusta monitor start/stop`.

Per ADR-0002 the supervised path (systemd / launchd via
`monitor install-service`) is the recommended deployment. These helpers
provide a light-weight alternative for users who just want a backgrounded
daemon without touching the service manager — they spawn the daemon in a
new session (`subprocess.Popen(start_new_session=True)`) rather than
double-forking, which is simpler, testable, and behaves identically for
the "survive terminal close" use case.

The PID file at `~/.langusta/monitor.pid` is the single source of truth
for "is my LANgusta monitor running".
"""

from __future__ import annotations

import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class PidFileState:
    """Result of inspecting a PID file."""

    path: Path
    pid: int | None
    alive: bool  # is the recorded PID a live process?


def read_pid_file(path: Path) -> PidFileState:
    """Read and validate the PID file.

    Cases:
      - file missing           → PidFileState(path, None, False)
      - file present, parses   → PidFileState(path, pid, is_alive)
      - file present, garbage  → PidFileState(path, None, False)
    """
    if not path.exists():
        return PidFileState(path=path, pid=None, alive=False)
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return PidFileState(path=path, pid=None, alive=False)
    if not raw:
        return PidFileState(path=path, pid=None, alive=False)
    try:
        pid = int(raw)
    except ValueError:
        return PidFileState(path=path, pid=None, alive=False)
    return PidFileState(path=path, pid=pid, alive=is_process_alive(pid))


def is_process_alive(pid: int) -> bool:
    """True iff `pid` is a currently-running process on this host."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we're not allowed to signal it — still alive.
        return True
    return True


def write_pid_file(path: Path, pid: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{pid}\n", encoding="utf-8")


def clear_pid_file(path: Path) -> None:
    """Remove the PID file if it exists (idempotent)."""
    try:
        path.unlink()
    except FileNotFoundError:
        return


def stop_via_pid_file(
    path: Path,
    *,
    sig: int = signal.SIGTERM,
    timeout_seconds: float = 5.0,
    poll_interval: float = 0.1,
) -> PidFileState:
    """Signal the daemon recorded at `path` and wait for it to exit.

    Returns the final state of the PID file. Callers inspect `.alive`:
    if still True after the timeout, the process didn't exit and the PID
    file is left in place for diagnosis.
    """
    state = read_pid_file(path)
    if state.pid is None:
        return state
    if not state.alive:
        clear_pid_file(path)
        return PidFileState(path=path, pid=state.pid, alive=False)

    try:
        os.kill(state.pid, sig)
    except ProcessLookupError:
        clear_pid_file(path)
        return PidFileState(path=path, pid=state.pid, alive=False)

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not is_process_alive(state.pid):
            clear_pid_file(path)
            return PidFileState(path=path, pid=state.pid, alive=False)
        time.sleep(poll_interval)

    return PidFileState(path=path, pid=state.pid, alive=True)
