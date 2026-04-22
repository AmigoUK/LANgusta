"""Canonical filesystem paths for LANgusta.

Single source of truth for where the DB, backups, and config live, so tests
can redirect via $HOME and production callers don't sprinkle `~/.langusta`
string literals across the codebase.
"""

from __future__ import annotations

import os
from pathlib import Path


def langusta_home() -> Path:
    """Return `$LANGUSTA_HOME` if set, else `~/.langusta`.

    `LANGUSTA_HOME` must be an absolute path. Rejecting relative paths
    up front means a badly-set env (e.g. `LANGUSTA_HOME=.` in a script
    invoked from an unexpected CWD) can't silently create the
    LANgusta tree in the current directory instead of somewhere safe.
    """
    override = os.environ.get("LANGUSTA_HOME")
    if override:
        if not os.path.isabs(override):
            raise ValueError(
                f"LANGUSTA_HOME must be an absolute path; got "
                f"{override!r}"
            )
        return Path(override)
    return Path(os.path.expanduser("~")) / ".langusta"


def db_path() -> Path:
    return langusta_home() / "db.sqlite"


def backups_dir() -> Path:
    return langusta_home() / "backups"


def config_path() -> Path:
    return langusta_home() / "config.toml"


def known_hosts_path() -> Path:
    """SSH known_hosts file for monitor `ssh_command` checks (TOFU)."""
    return langusta_home() / "known_hosts"


def monitor_pid_path() -> Path:
    """PID file written by `langusta monitor start`."""
    return langusta_home() / "monitor.pid"


def monitor_log_path() -> Path:
    """stdout/stderr capture file for `langusta monitor start`."""
    return langusta_home() / "monitor.log"


def notifications_log_path() -> Path:
    """Always-on notification log the dispatcher appends to per event.
    Separate from any user-configured `logfile` sinks — those point at
    a path the operator chose via `notify add-logfile`.
    """
    return langusta_home() / "notifications.log"
