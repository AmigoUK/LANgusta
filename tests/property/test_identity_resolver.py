"""Hypothesis property tests for core/identity.resolve.

The Lansweeper-failure invariant (no silent auto-merge on conflicting signals)
must hold for arbitrary mixes of existing state and candidate observations.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from langusta.core.identity import (
    Ambiguous,
    AssetIdentity,
    Candidate,
    Insert,
    Update,
    resolve,
)
from tests.strategies import hostnames, ipv4, mac_sets

# Strategies
# ---------------------------------------------------------------------------


@st.composite
def asset_identities(draw: st.DrawFn) -> AssetIdentity:
    return AssetIdentity(
        asset_id=draw(st.integers(min_value=1, max_value=10_000)),
        hostname=draw(st.one_of(st.none(), hostnames)),
        primary_ip=draw(st.one_of(st.none(), ipv4)),
        macs=draw(mac_sets),
    )


@st.composite
def candidates(draw: st.DrawFn) -> Candidate:
    return Candidate(
        hostname=draw(st.one_of(st.none(), hostnames)),
        primary_ip=draw(st.one_of(st.none(), ipv4)),
        macs=draw(mac_sets),
    )


existing_pool = st.lists(asset_identities(), max_size=6, unique_by=lambda a: a.asset_id)


# Properties
# ---------------------------------------------------------------------------


@given(candidate=candidates(), existing=existing_pool)
def test_property_resolution_is_deterministic(
    candidate: Candidate, existing: list[AssetIdentity]
) -> None:
    """Same input ⇒ same output. Pure function, no hidden state."""
    assert resolve(candidate, existing) == resolve(candidate, existing)


@given(candidate=candidates(), existing=existing_pool)
def test_property_confidence_in_unit_interval_for_every_outcome(
    candidate: Candidate, existing: list[AssetIdentity]
) -> None:
    result = resolve(candidate, existing)
    if isinstance(result, Update):
        assert 0.0 <= result.confidence <= 1.0
    elif isinstance(result, Ambiguous):
        for _, conf in result.candidates:
            assert 0.0 <= conf <= 1.0


@given(candidate=candidates(), existing=existing_pool)
def test_property_no_auto_merge_when_ambiguous_mac_and_hostname(
    candidate: Candidate, existing: list[AssetIdentity]
) -> None:
    """The ADR-anchor invariant: if the candidate's MAC matches one existing
    asset AND the candidate's hostname matches a *different* existing asset,
    the outcome MUST be Ambiguous — never a silent Update pointing at one of
    them."""
    if not candidate.macs or not candidate.hostname:
        return
    mac_hits = {a.asset_id for a in existing if a.macs & candidate.macs}
    host_hits = {
        a.asset_id for a in existing if a.hostname and a.hostname == candidate.hostname
    }
    # If MAC hits one asset and hostname hits a DIFFERENT asset, ambiguous.
    if mac_hits and host_hits and not (mac_hits & host_hits):
        result = resolve(candidate, existing)
        assert isinstance(result, Ambiguous), (
            f"expected Ambiguous for conflicting MAC={mac_hits}, "
            f"hostname={host_hits}, got {type(result).__name__}"
        )


@given(candidate=candidates(), existing=existing_pool)
def test_property_update_asset_id_is_one_of_existing(
    candidate: Candidate, existing: list[AssetIdentity]
) -> None:
    """Update() must never point at a non-existent asset_id."""
    result = resolve(candidate, existing)
    if isinstance(result, Update):
        assert result.asset_id in {a.asset_id for a in existing}
    elif isinstance(result, Ambiguous):
        for aid, _ in result.candidates:
            assert aid in {a.asset_id for a in existing}


@given(candidate=candidates(), existing=existing_pool)
def test_property_insert_when_candidate_has_no_signals(
    candidate: Candidate, existing: list[AssetIdentity]
) -> None:
    """Candidates with no hostname, no IP, and no MACs can't match anything —
    they become Insert regardless of existing state."""
    if candidate.hostname is None and candidate.primary_ip is None and not candidate.macs:
        assert resolve(candidate, existing) == Insert()


@given(candidate=candidates(), existing=existing_pool)
def test_property_two_distinct_mac_matches_produce_ambiguous(
    candidate: Candidate, existing: list[AssetIdentity]
) -> None:
    """If the candidate's MAC set overlaps TWO distinct existing assets,
    the outcome is Ambiguous (no auto-pick)."""
    mac_hit_ids = {a.asset_id for a in existing if a.macs & candidate.macs}
    if len(mac_hit_ids) >= 2:
        result = resolve(candidate, existing)
        assert isinstance(result, Ambiguous), (
            f"MAC hits {mac_hit_ids} should produce Ambiguous, got {result!r}"
        )
