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
from pathlib import Path
from typing import Protocol, runtime_checkable

# (ip_str, mac_str) pairs from the host's ARP table.
ArpEntry = tuple[str, str]


class NotImplementedCapability(NotImplementedError):  # noqa: N818 — inherits NotImplementedError naming
    """Raised by the Windows stub (and future partially-supported backends).

    Carry the recommendation ("use WSL2") in the message so users get a useful
    error rather than a cryptic traceback.
    """


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
