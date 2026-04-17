# Running the monitor daemon

Per [ADR-0002](adr/0002-process-architecture.md), LANgusta does NOT fork, double-fork, detach, or write a PID file of its own. The monitor loop is an idempotent process; your system's service manager owns its lifecycle. This way, daemon supervision comes for free (restart policy, log rotation, boot-time start, journal integration) and LANgusta doesn't carry the failure modes of hand-rolled detachment.

## Quickstart

```bash
langusta monitor install-service       # writes a unit / plist under ~/
```

Linux lays down a systemd user unit at `~/.config/systemd/user/langusta-monitor.service`; macOS lays down a launchd agent at `~/Library/LaunchAgents/uk.attv.langusta.monitor.plist`. Both invoke `langusta monitor daemon --foreground`, which runs a simple loop of `run_once()` every `--interval` seconds (default 60).

`install-service` refuses to overwrite an existing file by default. Pass `--force` if you've changed the binary path and want to regenerate. Use `--dry-run` to print to stdout without writing.

## Linux (systemd user)

```bash
# First-time install:
langusta monitor install-service
systemctl --user daemon-reload
systemctl --user enable --now langusta-monitor.service

# Check status:
systemctl --user status langusta-monitor.service
journalctl --user -u langusta-monitor.service -f     # follow logs
```

To survive logout:

```bash
sudo loginctl enable-linger $USER
```

Without `linger`, the user bus shuts down when you log out and the monitor stops.

## macOS (launchd)

```bash
langusta monitor install-service
launchctl load ~/Library/LaunchAgents/uk.attv.langusta.monitor.plist

# Check status:
launchctl list | grep langusta
tail -f /tmp/langusta-monitor.out
```

## Status from LANgusta

Regardless of which supervisor runs the daemon, `langusta monitor status` prints the last heartbeat and whether it's `fresh` or `STALE` (2-minute tolerance from the spec):

```bash
$ langusta monitor status
heartbeat 2026-04-17T12:04:31+00:00  (34s ago, fresh)
```

## Running without a supervisor (not recommended)

For ad-hoc use you can run the loop in the foreground:

```bash
langusta monitor daemon --foreground --interval 60
```

This is what the systemd unit / launchd plist invokes. It is **not** what you want in production — use the service manager, not `nohup` or `&`.

## Schema-version coordination

The daemon and the TUI share one SQLite file. If you upgrade the binary without restarting the daemon, a schema-version mismatch eventually surfaces (the daemon refuses to write through stale assumptions). Restart the daemon after `uv tool upgrade langusta`:

```bash
systemctl --user restart langusta-monitor.service
```

ADR-0002 documents this as accepted; a future polish pass will surface the mismatch more prominently in `monitor status`.
