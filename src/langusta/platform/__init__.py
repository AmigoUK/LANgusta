"""Platform backend registry.

This file is the ONLY place in the codebase that may branch on `sys.platform`.
Everywhere else calls `get_backend()` and uses the `PlatformBackend` Protocol
interface.
"""

from __future__ import annotations

import sys
from functools import lru_cache

from langusta.platform.base import NotImplementedCapability, PlatformBackend
from langusta.platform.linux import LinuxBackend
from langusta.platform.macos import MacOSBackend
from langusta.platform.windows import WindowsStubBackend

__all__ = [
    "NotImplementedCapability",
    "PlatformBackend",
    "get_backend",
]


@lru_cache(maxsize=1)
def get_backend() -> PlatformBackend:
    """Return the `PlatformBackend` implementation for the current OS."""
    if sys.platform.startswith("linux"):
        return LinuxBackend()
    if sys.platform == "darwin":
        return MacOSBackend()
    return WindowsStubBackend()
