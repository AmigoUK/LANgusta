"""OUI vendor lookup.

Given a MAC address, return the vendor organisation that registered the
first 24 bits (OUI) with the IEEE. The packaged CSV is a small curated
subset; `langusta update-oui` (post-v1) will fetch the full IEEE registry
on demand.

Spec: docs/specs/02-tech-stack-and-architecture.md §6 (bundled OUI DB).
"""

from __future__ import annotations

import csv
import re
from functools import lru_cache
from importlib.resources import files


class InvalidMac(ValueError):  # noqa: N818 — ValueError subclass
    """Input did not contain 12 hex digits."""


# Accept colons, dashes, dots as separators. Cisco's 4-group form ("aabb.ccdd.eeff")
# reduces to 12 hex chars after stripping.
_SEP_RE = re.compile(r"[\s:\-.]")
_HEX12_RE = re.compile(r"^[0-9a-f]{12}$")


def _normalise(mac: str) -> str:
    stripped = _SEP_RE.sub("", mac).lower()
    if not _HEX12_RE.fullmatch(stripped):
        raise InvalidMac(f"expected 12 hex digits after stripping separators, got {mac!r}")
    return stripped


@lru_cache(maxsize=1)
def _registry() -> dict[str, str]:
    """Load the packaged OUI CSV once per process."""
    resource = files("langusta.data").joinpath("oui.csv")
    with resource.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        # Later rows win for duplicate OUIs (curation convention).
        return {row["oui"].strip().lower(): row["vendor"].strip() for row in reader}


def lookup(mac: str) -> str | None:
    """Return the vendor name for a MAC, or None if the OUI isn't in the DB."""
    oui = _normalise(mac)[:6]
    return _registry().get(oui)
