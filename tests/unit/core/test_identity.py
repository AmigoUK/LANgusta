"""Identity resolution contract — specific edges.

Spec: docs/specs/01-functionality-and-moscow.md §4 Pillar B.
The Lansweeper-failure rule is encoded here: when the scanner produces an
observation that *could* match an existing asset with conflicting signals,
the answer is Ambiguous, never a silent auto-merge.
"""

from __future__ import annotations

from langusta.core.identity import (
    Ambiguous,
    AssetIdentity,
    Candidate,
    Insert,
    Update,
    resolve,
)

# ---------------------------------------------------------------------------
# Insert — no matching existing asset
# ---------------------------------------------------------------------------


def test_no_existing_assets_returns_insert() -> None:
    candidate = Candidate(hostname="router", primary_ip="192.168.1.1", macs={"aa:bb:cc:dd:ee:ff"})
    assert resolve(candidate, existing=[]) == Insert()


def test_no_signal_overlap_returns_insert() -> None:
    existing = [
        AssetIdentity(
            asset_id=1, hostname="other", primary_ip="10.0.0.1",
            macs={"11:22:33:44:55:66"},
        )
    ]
    candidate = Candidate(hostname="router", primary_ip="192.168.1.1",
                          macs={"aa:bb:cc:dd:ee:ff"})
    assert resolve(candidate, existing) == Insert()


# ---------------------------------------------------------------------------
# Update — unambiguous match
# ---------------------------------------------------------------------------


def test_mac_exact_match_returns_update_high_confidence() -> None:
    existing = [
        AssetIdentity(asset_id=7, hostname="router", primary_ip="192.168.1.1",
                      macs={"aa:bb:cc:dd:ee:ff"}),
    ]
    candidate = Candidate(hostname="router", primary_ip="192.168.1.1",
                          macs={"aa:bb:cc:dd:ee:ff"})
    result = resolve(candidate, existing)
    assert isinstance(result, Update)
    assert result.asset_id == 7
    assert result.confidence >= 0.9  # MAC is a strong signal


def test_mac_match_plus_different_hostname_returns_update_still_high() -> None:
    """A MAC match outweighs a hostname mismatch: devices get renamed often."""
    existing = [
        AssetIdentity(asset_id=7, hostname="old-name", primary_ip="192.168.1.1",
                      macs={"aa:bb:cc:dd:ee:ff"}),
    ]
    candidate = Candidate(hostname="new-name", primary_ip="192.168.1.1",
                          macs={"aa:bb:cc:dd:ee:ff"})
    result = resolve(candidate, existing)
    assert isinstance(result, Update)
    assert result.asset_id == 7


def test_ip_plus_hostname_match_without_mac_returns_update_medium() -> None:
    """If we can't see a MAC but IP and hostname agree, medium confidence."""
    existing = [
        AssetIdentity(asset_id=3, hostname="printer", primary_ip="10.0.0.5",
                      macs=set()),
    ]
    candidate = Candidate(hostname="printer", primary_ip="10.0.0.5", macs=set())
    result = resolve(candidate, existing)
    assert isinstance(result, Update)
    assert result.asset_id == 3
    assert 0.4 <= result.confidence <= 0.9


# ---------------------------------------------------------------------------
# Ambiguous — the Lansweeper-failure rule
# ---------------------------------------------------------------------------


def test_mac_matches_one_asset_but_hostname_matches_another_is_ambiguous() -> None:
    """The documented Lansweeper failure mode: MAC says A, hostname says B,
    we don't pick one automatically."""
    existing = [
        AssetIdentity(asset_id=1, hostname="alpha", primary_ip="10.0.0.1",
                      macs={"aa:bb:cc:dd:ee:ff"}),
        AssetIdentity(asset_id=2, hostname="bravo", primary_ip="10.0.0.2",
                      macs={"11:22:33:44:55:66"}),
    ]
    candidate = Candidate(hostname="bravo", primary_ip="10.0.0.3",
                          macs={"aa:bb:cc:dd:ee:ff"})
    result = resolve(candidate, existing)
    assert isinstance(result, Ambiguous)
    asset_ids = [cid for cid, _ in result.candidates]
    assert 1 in asset_ids and 2 in asset_ids


def test_mac_overlap_against_two_different_assets_is_ambiguous() -> None:
    """Shouldn't happen under normal MAC uniqueness, but handle it — e.g.,
    duplicated MAC from a misbehaving VM or a previous scan bug."""
    mac = "aa:bb:cc:dd:ee:ff"
    existing = [
        AssetIdentity(asset_id=1, hostname="a", primary_ip="10.0.0.1", macs={mac}),
        AssetIdentity(asset_id=2, hostname="b", primary_ip="10.0.0.2", macs={mac}),
    ]
    candidate = Candidate(hostname="c", primary_ip="10.0.0.3", macs={mac})
    result = resolve(candidate, existing)
    assert isinstance(result, Ambiguous)


# ---------------------------------------------------------------------------
# Confidence scoring
# ---------------------------------------------------------------------------


def test_confidence_is_between_0_and_1() -> None:
    existing = [
        AssetIdentity(asset_id=1, hostname="x", primary_ip="10.0.0.1",
                      macs={"aa:bb:cc:dd:ee:ff"}),
    ]
    candidate = Candidate(hostname="x", primary_ip="10.0.0.1",
                          macs={"aa:bb:cc:dd:ee:ff"})
    result = resolve(candidate, existing)
    assert isinstance(result, Update)
    assert 0.0 <= result.confidence <= 1.0


def test_empty_candidate_against_existing_returns_insert() -> None:
    """Degenerate: no signals to match on — treat as new asset."""
    existing = [
        AssetIdentity(asset_id=1, hostname="x", primary_ip="10.0.0.1",
                      macs={"aa:bb:cc:dd:ee:ff"}),
    ]
    candidate = Candidate(hostname=None, primary_ip=None, macs=set())
    assert resolve(candidate, existing) == Insert()
