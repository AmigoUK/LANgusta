"""Tests for scan/arp.py — ARP-table enrichment of scan targets.

The M0 parser tests (tests/unit/platform/test_platform.py) cover
`ip neigh` / `arp -a` format parsing. These tests cover the scanner-facing
layer: given a PlatformBackend, return {ip: mac} for a target IP set.
"""

from __future__ import annotations

from collections.abc import Iterable

from langusta.platform.base import ArpEntry
from langusta.scan.arp import arp_lookup


class _StubBackend:
    """Minimal PlatformBackend stand-in for tests."""

    def __init__(self, entries: list[ArpEntry]) -> None:
        self._entries = entries

    def arp_table(self) -> Iterable[ArpEntry]:
        return iter(self._entries)

    def enforce_private(self, path) -> None:  # pragma: no cover — unused here
        ...


def test_arp_lookup_returns_mac_for_known_ip() -> None:
    backend = _StubBackend([("10.0.0.1", "aa:bb:cc:dd:ee:ff")])
    result = arp_lookup({"10.0.0.1"}, backend=backend)
    assert result == {"10.0.0.1": "aa:bb:cc:dd:ee:ff"}


def test_arp_lookup_filters_to_target_set() -> None:
    backend = _StubBackend([
        ("10.0.0.1", "aa:bb:cc:dd:ee:01"),
        ("10.0.0.2", "aa:bb:cc:dd:ee:02"),
        ("10.0.0.3", "aa:bb:cc:dd:ee:03"),
    ])
    result = arp_lookup({"10.0.0.1", "10.0.0.3"}, backend=backend)
    assert result == {
        "10.0.0.1": "aa:bb:cc:dd:ee:01",
        "10.0.0.3": "aa:bb:cc:dd:ee:03",
    }


def test_arp_lookup_missing_ip_is_omitted() -> None:
    """An IP we don't have an ARP entry for simply doesn't appear in the result."""
    backend = _StubBackend([("10.0.0.1", "aa:bb:cc:dd:ee:ff")])
    result = arp_lookup({"10.0.0.1", "10.0.0.2"}, backend=backend)
    assert result == {"10.0.0.1": "aa:bb:cc:dd:ee:ff"}


def test_arp_lookup_empty_target_set_returns_empty() -> None:
    backend = _StubBackend([("10.0.0.1", "aa:bb:cc:dd:ee:ff")])
    assert arp_lookup(set(), backend=backend) == {}


def test_arp_lookup_empty_arp_table_returns_empty() -> None:
    backend = _StubBackend([])
    assert arp_lookup({"10.0.0.1"}, backend=backend) == {}


def test_arp_lookup_normalises_mac_to_lowercase() -> None:
    backend = _StubBackend([("10.0.0.1", "AA:BB:CC:DD:EE:FF")])
    result = arp_lookup({"10.0.0.1"}, backend=backend)
    assert result["10.0.0.1"] == "aa:bb:cc:dd:ee:ff"


def test_arp_lookup_duplicate_ip_keeps_first_seen() -> None:
    """If the backend reports two entries for the same IP (broken host or
    parser quirk), keep the first and ignore the rest — arbitrary but
    deterministic."""
    backend = _StubBackend([
        ("10.0.0.1", "aa:bb:cc:dd:ee:01"),
        ("10.0.0.1", "aa:bb:cc:dd:ee:02"),
    ])
    result = arp_lookup({"10.0.0.1"}, backend=backend)
    assert result == {"10.0.0.1": "aa:bb:cc:dd:ee:01"}
