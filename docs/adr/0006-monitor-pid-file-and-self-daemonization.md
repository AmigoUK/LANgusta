# ADR-0006: Monitor PID file and opt-in self-daemonization via `monitor start`

- **Status:** Accepted
- **Date:** 2026-04-19
- **Deciders:** maintainer
- **Supersedes:** —
- **Superseded by:** —

## Context

ADR-0002 established that `langusta monitor daemon` is a separate long-running
process and that users arrange persistence via systemd/launchd — the
recommended path is `langusta monitor install-service` followed by the
platform's service manager. ADR-0002 also lists, under "Follow-up work",
a first-class `monitor {start,stop,status,tail}` command set.

Users without access to (or patience for) systemd/launchd need a lighter
backgrounding primitive. Two designs were considered:

### Option A — Traditional UNIX double-fork + setsid

Classic daemonization: fork, setsid, fork again, close standard fds,
write PID. Fully detaches from the parent terminal.

Drawbacks: extra platform complexity, harder to test, easy to get
wrong (signal masks, controlling terminal, zombie handling).

### Option B — `subprocess.Popen(start_new_session=True)`

Python's stdlib already provides the detachment primitive via
`start_new_session=True`, which internally performs `setsid()`. Combined
with a process group and redirected stdio, this achieves the same
"survives terminal close" property with far less code.

## Decision

**Option B**: `langusta monitor start` spawns
`langusta monitor daemon --foreground` in a new session via
`subprocess.Popen(start_new_session=True)` and writes the child's PID to
`~/.langusta/monitor.pid`. `langusta monitor stop` reads that file,
sends SIGTERM, waits for graceful exit, and clears the file.

We deliberately do NOT implement a traditional double-fork daemonizer.
ADR-0002's preferred path (systemd/launchd) remains the recommendation;
`monitor start` is a convenience for ad-hoc use.

## Consequences

### Positive

- Adds the `start` / `stop` half of the ADR-0002 follow-up list without
  new native code.
- `read_pid_file` / `is_process_alive` / `stop_via_pid_file` are trivially
  unit-testable (12 tests added).
- `monitor status` now reports both heartbeat freshness and PID-file
  state — answers "is my monitor running *right now*" even if the
  heartbeat hasn't been updated yet.

### Negative

- Not fully POSIX-daemonized: the spawned process inherits the session
  leader from the parent of `subprocess.Popen`, but is in its own
  session. In practice this is indistinguishable from a double-fork
  daemon for the "survive terminal close" goal.
- Windows is not supported by `monitor start` (no POSIX signals); users
  on Windows continue to be guided to WSL2 per ADR-0004.

### Follow-up work

- `monitor tail` to stream `~/.langusta/monitor.log`.
- Log rotation for `monitor.log` — currently appends forever.
