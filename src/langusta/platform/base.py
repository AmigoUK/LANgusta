"""Platform-abstraction protocol.

ADR: docs/adr/0004-platform-support.md.

Every OS-touching capability in LANgusta flows through this Protocol so
platform-specific branches live in exactly one place. `core/` never imports
anything from `platform/` — it calls into `db/` or `scan/`, which ask
`platform.get_backend()` when they need ARP data, permissions, or daemon
recipes.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

# (ip_str, mac_str) pairs from the host's ARP table.
ArpEntry = tuple[str, str]


class NotImplementedCapability(NotImplementedError):  # noqa: N818 — inherits NotImplementedError naming
    """Raised by the Windows stub (and future partially-supported backends).

    Carry the recommendation ("use WSL2") in the message so users get a useful
    error rather than a cryptic traceback.
    """


@dataclass(frozen=True, slots=True)
class InstallRecipe:
    """Where to write a service manager unit and what to tell the user after."""

    manager: str         # 'systemd-user' | 'launchd'
    install_path: Path   # absolute path the caller writes to
    content: str         # rendered unit / plist contents
    start_hint: str      # post-install one-liner to start the service


@runtime_checkable
class PlatformBackend(Protocol):
    """Capabilities that differ between Linux, macOS, and Windows.

    Implementations live in `platform/linux.py`, `platform/macos.py`,
    `platform/windows.py`. Add a method here only when a real caller needs it.
    """

    def arp_table(self) -> Iterable[ArpEntry]:
        """Return (ipv4, mac) pairs currently in the host's ARP cache."""
        ...

    def enforce_private(self, path: Path) -> None:
        """Ensure `path` is readable only by the current user.

        - Files: mode 0600.
        - Directories: mode 0700.
        Raises `NotImplementedCapability` on backends where enforcing this is
        unsafe (Windows without ACL handling).
        """
        ...

    def daemon_install_recipe(self, *, exec_path: str) -> InstallRecipe:
        """Return the service-manager recipe for the current OS.

        Linux → systemd user unit at ~/.config/systemd/user/.
        macOS → launchd plist at ~/Library/LaunchAgents/.
        Windows → raises NotImplementedCapability.
        """
        ...
