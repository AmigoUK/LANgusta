"""Shared Hypothesis strategies for LANgusta test modules."""

from __future__ import annotations

from hypothesis import strategies as st

# Hex pairs joined with colons — aa:bb:cc:dd:ee:ff — normalised lowercase.
_MAC_HEX = st.text(alphabet="0123456789abcdef", min_size=2, max_size=2)
macs = st.builds(
    lambda parts: ":".join(parts),
    st.lists(_MAC_HEX, min_size=6, max_size=6),
)

# IPv4 address as dotted-quad string.
_OCTET = st.integers(min_value=0, max_value=255).map(str)
ipv4 = st.builds(
    lambda parts: ".".join(parts),
    st.lists(_OCTET, min_size=4, max_size=4),
)

# Non-empty hostnames with printable alphabet.
hostnames = st.text(
    alphabet=st.characters(
        whitelist_categories=("Ll", "Lu", "Nd"),
        whitelist_characters="-._",
    ),
    min_size=1,
    max_size=30,
)

mac_sets = st.sets(macs, min_size=0, max_size=3)
