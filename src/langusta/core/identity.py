"""Composite identity resolution.

Spec: docs/specs/01-functionality-and-moscow.md §4 Pillar B.
ADR: docs/adr/0001-data-layer-orm-choice.md (stdlib-only core/).

An observation (`Candidate`) is resolved against a set of known assets
(`AssetIdentity`) into one of three outcomes:

  - `Insert()`            — no match. Create a new asset.
  - `Update(asset_id, confidence)` — one clear match.
  - `Ambiguous(candidates)`  — multiple plausible matches. The review queue
                              handles these; the resolver NEVER picks one.

The load-bearing rule: if a MAC match points at asset A and a hostname match
points at a different asset B, we do NOT silently merge. Lansweeper's
documented failure mode was exactly this auto-merge; we refuse it.

Stdlib-only module; testable without any DB or network.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AssetIdentity:
    """Identity fingerprint of an existing asset — the subset of columns the
    resolver cares about. Whole Asset row not needed here."""

    asset_id: int
    hostname: str | None
    primary_ip: str | None
    macs: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        # Normalise `macs` to frozenset so dataclass equality and hashing work
        # regardless of caller passing set / list / tuple.
        if not isinstance(self.macs, frozenset):
            object.__setattr__(self, "macs", frozenset(self.macs))


@dataclass(frozen=True, slots=True)
class Candidate:
    """A single observation awaiting identity resolution."""

    hostname: str | None
    primary_ip: str | None
    macs: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not isinstance(self.macs, frozenset):
            object.__setattr__(self, "macs", frozenset(self.macs))


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Insert:
    """No existing asset matched. Create a new one."""


@dataclass(frozen=True, slots=True)
class Update:
    """One existing asset matched with the given confidence."""

    asset_id: int
    confidence: float


@dataclass(frozen=True, slots=True)
class Ambiguous:
    """Multiple existing assets could be the same device. Review queue only."""

    candidates: tuple[tuple[int, float], ...]  # (asset_id, confidence) pairs
    reason: str = ""


Resolution = Insert | Update | Ambiguous


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

# Confidence weights for individual signals. Sum is capped at 1.0.
_MAC_OVERLAP_WEIGHT = 0.9       # MAC is globally unique — strongest signal
_IP_MATCH_WEIGHT = 0.4          # IPs are reused (DHCP leases, re-homes)
_HOSTNAME_MATCH_WEIGHT = 0.3    # hostnames drift (human edits, DHCP names)


def _score_against(candidate: Candidate, existing: AssetIdentity) -> float:
    """Return a [0, 1] confidence score for candidate matching this asset."""
    score = 0.0
    if candidate.macs and (candidate.macs & existing.macs):
        score += _MAC_OVERLAP_WEIGHT
    if candidate.primary_ip and candidate.primary_ip == existing.primary_ip:
        score += _IP_MATCH_WEIGHT
    if (
        candidate.hostname
        and existing.hostname
        and candidate.hostname == existing.hostname
    ):
        score += _HOSTNAME_MATCH_WEIGHT
    return min(score, 1.0)


_AMBIGUITY_FLOOR = 0.2  # below this, we don't consider an asset a candidate at all


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


def resolve(candidate: Candidate, existing: list[AssetIdentity]) -> Resolution:
    """Resolve a candidate observation against a list of known assets.

    Rules (applied in order):
      1. Candidate has no signals at all → Insert.
      2. Score every existing asset; drop those below _AMBIGUITY_FLOOR.
      3. If the candidate's MAC set overlaps two or more distinct existing
         assets → Ambiguous (shouldn't happen under MAC uniqueness, but we
         handle it).
      4. If the candidate's MAC matches asset A and hostname matches a
         DIFFERENT asset B (the Lansweeper-failure case) → Ambiguous.
      5. If exactly one asset scored at all → Update(it).
      6. If multiple assets scored → Ambiguous.
      7. No hits → Insert.
    """
    # 1 — no signals to match
    if candidate.hostname is None and candidate.primary_ip is None and not candidate.macs:
        return Insert()

    mac_hits = {a.asset_id for a in existing if candidate.macs & a.macs}
    host_hits = {
        a.asset_id
        for a in existing
        if candidate.hostname and a.hostname == candidate.hostname
    }

    # 3 — MAC overlap against two+ distinct existing assets
    if len(mac_hits) >= 2:
        return Ambiguous(
            candidates=tuple(
                sorted(
                    ((a.asset_id, _score_against(candidate, a)) for a in existing if a.asset_id in mac_hits),
                    key=lambda pair: -pair[1],
                )
            ),
            reason="candidate MAC overlaps multiple existing assets",
        )

    # 4 — MAC says A, hostname says B
    if mac_hits and host_hits and not (mac_hits & host_hits):
        combined_ids = mac_hits | host_hits
        return Ambiguous(
            candidates=tuple(
                sorted(
                    ((a.asset_id, _score_against(candidate, a)) for a in existing if a.asset_id in combined_ids),
                    key=lambda pair: -pair[1],
                )
            ),
            reason="MAC points at one asset but hostname points at another",
        )

    # Score everyone; keep only those above the floor
    scored = [
        (a.asset_id, _score_against(candidate, a))
        for a in existing
    ]
    above = [pair for pair in scored if pair[1] >= _AMBIGUITY_FLOOR]

    if not above:
        # 7
        return Insert()

    if len(above) == 1:
        # 5
        aid, conf = above[0]
        return Update(asset_id=aid, confidence=conf)

    # 6 — multiple plausible matches, none exact-MAC-winner
    return Ambiguous(
        candidates=tuple(sorted(above, key=lambda pair: -pair[1])),
        reason="multiple existing assets partially match the observation",
    )
