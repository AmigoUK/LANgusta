"""Platform backend Protocol tests.

ADR reference: docs/adr/0004-platform-support.md.

The `platform/` module is the ONLY place in the codebase that may use
`sys.platform`. Everything else asks `get_backend()` for capabilities.
Windows has a stub backend that raises `NotImplementedCapability` — we do
not pretend to support native Win32 in v1.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest

from langusta.platform import get_backend
from langusta.platform.base import NotImplementedCapability, PlatformBackend
from langusta.platform.linux import LinuxBackend
from langusta.platform.macos import MacOSBackend
from langusta.platform.windows import WindowsStubBackend


def test_get_backend_returns_matching_platform() -> None:
    backend = get_backend()
    if sys.platform.startswith("linux"):
        assert isinstance(backend, LinuxBackend)
    elif sys.platform == "darwin":
        assert isinstance(backend, MacOSBackend)
    else:
        # Tests exercised on Windows should instantiate the stub explicitly;
        # we don't dispatch it automatically.
        pytest.skip(f"platform {sys.platform!r} not covered by get_backend() in v1")


def test_windows_stub_raises_on_every_capability() -> None:
    be = WindowsStubBackend()
    with pytest.raises(NotImplementedCapability) as excinfo:
        be.arp_table()
    assert "windows" in str(excinfo.value).lower()
    assert "wsl2" in str(excinfo.value).lower()


def test_windows_stub_enforce_private_also_raises(tmp_path: Path) -> None:
    be = WindowsStubBackend()
    target = tmp_path / "x"
    target.touch()
    with pytest.raises(NotImplementedCapability):
        be.enforce_private(target)


def test_linux_and_macos_backends_implement_protocol() -> None:
    # Structural typing check: both must satisfy PlatformBackend.
    # (Protocol isinstance works because we mark it runtime_checkable.)
    assert isinstance(LinuxBackend(), PlatformBackend)
    assert isinstance(MacOSBackend(), PlatformBackend)


@pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX-only permission check"
)
def test_enforce_private_sets_mode_0600_on_a_file(tmp_path: Path) -> None:
    be = get_backend()
    f = tmp_path / "secret.txt"
    f.write_text("s3cret")
    be.enforce_private(f)
    mode = stat.S_IMODE(f.stat().st_mode)
    assert mode == 0o600


@pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX-only permission check"
)
def test_enforce_private_sets_mode_0700_on_a_directory(tmp_path: Path) -> None:
    be = get_backend()
    d = tmp_path / "backups"
    d.mkdir()
    be.enforce_private(d)
    mode = stat.S_IMODE(d.stat().st_mode)
    assert mode == 0o700


@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="Linux-only parser"
)
def test_linux_arp_table_parser_handles_ip_neigh_format() -> None:
    sample = (
        "192.168.1.1 dev eth0 lladdr aa:bb:cc:dd:ee:ff REACHABLE\n"
        "192.168.1.2 dev eth0  FAILED\n"
        "192.168.1.3 dev eth0 lladdr 11:22:33:44:55:66 STALE\n"
        "fe80::1 dev eth0 lladdr aa:bb:cc:dd:ee:ff router REACHABLE\n"
    )
    be = LinuxBackend()
    entries = list(be._parse_ip_neigh(sample))
    # IPv4 reachable + stale entries survive; FAILED is dropped.
    assert ("192.168.1.1", "aa:bb:cc:dd:ee:ff") in entries
    assert ("192.168.1.3", "11:22:33:44:55:66") in entries
    assert all(ip != "192.168.1.2" for ip, _ in entries)
    # IPv6 link-local entries are skipped (scanner only cares about IPv4 in v1).
    assert all(":" not in ip for ip, _ in entries)


def test_macos_arp_table_parser_handles_arp_a_format() -> None:
    sample = (
        "? (192.168.1.1) at aa:bb:cc:dd:ee:ff on en0 ifscope [ethernet]\n"
        "router.local (192.168.1.1) at aa:bb:cc:dd:ee:ff on en0 ifscope [ethernet]\n"
        "? (192.168.1.99) at (incomplete) on en0 ifscope [ethernet]\n"
    )
    be = MacOSBackend()
    entries = list(be._parse_arp_a(sample))
    assert ("192.168.1.1", "aa:bb:cc:dd:ee:ff") in entries
    # 'incomplete' entries are dropped.
    assert all(ip != "192.168.1.99" for ip, _ in entries)
