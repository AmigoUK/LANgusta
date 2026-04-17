"""OUI vendor lookup tests.

The packaged OUI database is a tiny subset of the IEEE registry — enough to
prove the lookup path works and vendor names reach the inventory. Users who
need the full ~40k-entry list run `langusta update-oui` (post-v1) to fetch
and cache the current IEEE release.
"""

from __future__ import annotations

import pytest

from langusta.scan.oui import InvalidMac, lookup


def test_known_prefix_returns_vendor() -> None:
    # Cisco's OUI 00:1B:54 — in the shipped test fixture.
    assert lookup("00:1b:54:ab:cd:ef") == "Cisco Systems, Inc"


def test_unknown_prefix_returns_none() -> None:
    assert lookup("ff:ff:ff:ff:ff:ff") is None


def test_uppercase_mac_is_accepted() -> None:
    assert lookup("00:1B:54:AB:CD:EF") == "Cisco Systems, Inc"


def test_dashes_as_separators_are_accepted() -> None:
    assert lookup("00-1b-54-ab-cd-ef") == "Cisco Systems, Inc"


def test_dotted_cisco_format_is_accepted() -> None:
    """Cisco-style 001b.54ab.cdef."""
    assert lookup("001b.54ab.cdef") == "Cisco Systems, Inc"


def test_raw_12_hex_chars_is_accepted() -> None:
    assert lookup("001b54abcdef") == "Cisco Systems, Inc"


def test_invalid_mac_raises() -> None:
    with pytest.raises(InvalidMac):
        lookup("not-a-mac")


def test_short_input_raises() -> None:
    with pytest.raises(InvalidMac):
        lookup("00:1b")
