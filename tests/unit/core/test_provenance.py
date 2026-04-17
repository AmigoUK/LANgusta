"""Tests for core/provenance.merge_scan_result.

The load-bearing rule of LANgusta (spec §4 Pillar A; ADR-0005):

  The scanner never silently overwrites a human-set field. Observations that
  would modify a field with `manual` provenance land in a `proposed_changes`
  queue; they do NOT mutate the asset.

This test suite uses Hypothesis to prove the rule holds for arbitrary mixes of
existing state and incoming observations, and unit tests to pin down the
specific contract edges.
"""

from __future__ import annotations

from datetime import UTC, datetime

from hypothesis import given
from hypothesis import strategies as st

from langusta.core.provenance import (
    FieldProvenance,
    FieldValue,
    ProposedChange,
    merge_scan_result,
)

NOW = datetime(2026, 4, 17, 12, 0, 0, tzinfo=UTC)
EARLIER = datetime(2026, 4, 1, 9, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Unit tests — specific contract edges
# ---------------------------------------------------------------------------


def test_new_field_from_scan_is_applied() -> None:
    existing: dict[str, FieldValue] = {}
    incoming = {"hostname": "router"}
    applied, proposed = merge_scan_result(existing, incoming, now=NOW)
    assert applied == {
        "hostname": FieldValue(value="router", provenance=FieldProvenance.SCANNED, set_at=NOW),
    }
    assert proposed == []


def test_scan_overwrites_prior_scanned_value() -> None:
    existing = {
        "hostname": FieldValue("old-name", FieldProvenance.SCANNED, EARLIER),
    }
    incoming = {"hostname": "new-name"}
    applied, proposed = merge_scan_result(existing, incoming, now=NOW)
    assert applied == {
        "hostname": FieldValue("new-name", FieldProvenance.SCANNED, NOW),
    }
    assert proposed == []


def test_scan_never_overwrites_manual_field_proposes_instead() -> None:
    existing = {
        "hostname": FieldValue("human-set", FieldProvenance.MANUAL, EARLIER),
    }
    incoming = {"hostname": "scanner-guess"}
    applied, proposed = merge_scan_result(existing, incoming, now=NOW)
    assert applied == {}  # MANUAL field preserved
    assert proposed == [
        ProposedChange(
            field="hostname",
            current_value="human-set",
            current_provenance=FieldProvenance.MANUAL,
            proposed_value="scanner-guess",
            observed_at=NOW,
        ),
    ]


def test_scan_with_same_value_as_manual_does_not_propose() -> None:
    """If the scanner 'observes' exactly what the human already set, no noise."""
    existing = {
        "hostname": FieldValue("router", FieldProvenance.MANUAL, EARLIER),
    }
    incoming = {"hostname": "router"}
    applied, proposed = merge_scan_result(existing, incoming, now=NOW)
    assert applied == {}
    assert proposed == []


def test_scan_with_same_value_as_prior_scan_refreshes_timestamp_only() -> None:
    """Same-value observation updates set_at so last_seen-style queries work."""
    existing = {
        "hostname": FieldValue("router", FieldProvenance.SCANNED, EARLIER),
    }
    incoming = {"hostname": "router"}
    applied, _ = merge_scan_result(existing, incoming, now=NOW)
    assert "hostname" in applied
    assert applied["hostname"].set_at == NOW


def test_multiple_fields_mixed_provenance() -> None:
    existing = {
        "hostname": FieldValue("mine", FieldProvenance.MANUAL, EARLIER),
        "vendor": FieldValue("Cisco", FieldProvenance.SCANNED, EARLIER),
    }
    incoming = {
        "hostname": "scanner-guess",     # must not apply, must propose
        "vendor": "Cisco Systems",        # scanned → scanned, applies
        "os": "IOS 15.2",                 # new, applies
    }
    applied, proposed = merge_scan_result(existing, incoming, now=NOW)
    assert set(applied.keys()) == {"vendor", "os"}
    assert applied["vendor"].value == "Cisco Systems"
    assert applied["os"].value == "IOS 15.2"
    assert [c.field for c in proposed] == ["hostname"]


def test_imported_fields_are_treated_as_manual_for_overwrite_protection() -> None:
    """Imported-from-Lansweeper/NetBox fields carry trust; scanner can't overwrite."""
    existing = {
        "description": FieldValue("imported", FieldProvenance.IMPORTED, EARLIER),
    }
    incoming = {"description": "scanner-guess"}
    applied, proposed = merge_scan_result(existing, incoming, now=NOW)
    assert applied == {}
    assert len(proposed) == 1
    assert proposed[0].current_provenance == FieldProvenance.IMPORTED


# ---------------------------------------------------------------------------
# Hypothesis property tests — the invariant holds for ANY input
# ---------------------------------------------------------------------------


# Plausible-looking string field values (keeps search space small and readable).
field_values = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"),
    min_size=0,
    max_size=40,
)

field_names = st.sampled_from(
    ["hostname", "description", "vendor", "os", "location", "owner", "criticality"]
)


@st.composite
def existing_state(draw: st.DrawFn) -> dict[str, FieldValue]:
    fields = draw(st.lists(field_names, unique=True, max_size=5))
    state: dict[str, FieldValue] = {}
    for name in fields:
        state[name] = FieldValue(
            value=draw(field_values),
            provenance=draw(st.sampled_from(list(FieldProvenance))),
            set_at=EARLIER,
        )
    return state


@st.composite
def incoming_observations(draw: st.DrawFn) -> dict[str, str]:
    fields = draw(st.lists(field_names, unique=True, max_size=5))
    return {name: draw(field_values) for name in fields}


@given(existing=existing_state(), incoming=incoming_observations())
def test_property_manual_fields_never_overwritten(
    existing: dict[str, FieldValue],
    incoming: dict[str, str],
) -> None:
    """The load-bearing invariant: for ANY state x ANY observation,
    every MANUAL-provenance field's value remains exactly as it was."""
    applied, _ = merge_scan_result(existing, incoming, now=NOW)
    for field_name, prior in existing.items():
        # Either not in applied (preserved) OR in applied but value unchanged
        # (same-value observations may refresh timestamps — see separate test).
        if prior.provenance is FieldProvenance.MANUAL and field_name in applied:
            assert applied[field_name].value == prior.value
            assert applied[field_name].provenance is FieldProvenance.MANUAL


@given(existing=existing_state(), incoming=incoming_observations())
def test_property_imported_fields_never_overwritten_by_scan(
    existing: dict[str, FieldValue],
    incoming: dict[str, str],
) -> None:
    applied, _ = merge_scan_result(existing, incoming, now=NOW)
    for field_name, prior in existing.items():
        if prior.provenance is FieldProvenance.IMPORTED and field_name in applied:
            assert applied[field_name].value == prior.value


@given(existing=existing_state(), incoming=incoming_observations())
def test_property_conflicting_observation_always_surfaces(
    existing: dict[str, FieldValue],
    incoming: dict[str, str],
) -> None:
    """If a scan proposes a DIFFERENT value on a protected field, it must
    appear in the proposed-changes list. No silent drops, ever."""
    _, proposed = merge_scan_result(existing, incoming, now=NOW)
    protected = {FieldProvenance.MANUAL, FieldProvenance.IMPORTED}
    proposed_fields = {c.field for c in proposed}
    for field_name, observed in incoming.items():
        prior = existing.get(field_name)
        if prior is None:
            continue
        if prior.provenance in protected and prior.value != observed:
            assert field_name in proposed_fields, (
                f"Conflicting observation on protected field {field_name!r} "
                f"({prior.provenance.name}: {prior.value!r} vs {observed!r}) "
                f"was silently dropped — this is the Lansweeper-failure mode."
            )


@given(existing=existing_state(), incoming=incoming_observations())
def test_property_applied_scan_fields_carry_scanned_provenance(
    existing: dict[str, FieldValue],
    incoming: dict[str, str],
) -> None:
    applied, _ = merge_scan_result(existing, incoming, now=NOW)
    for field_name, new_value in applied.items():
        # Only values introduced *by this scan* get SCANNED provenance; we don't
        # let a scanner flip a MANUAL field to SCANNED (that would defeat the
        # rule). This assertion covers both: applied values are either new
        # (no prior), or were SCANNED before, but never flipping MANUAL/IMPORTED.
        prior = existing.get(field_name)
        if prior is not None and prior.provenance in {
            FieldProvenance.MANUAL,
            FieldProvenance.IMPORTED,
        }:
            # Preserved under its original provenance (not flipped).
            assert new_value.provenance is prior.provenance
        else:
            assert new_value.provenance is FieldProvenance.SCANNED


@given(existing=existing_state(), incoming=incoming_observations())
def test_property_no_proposed_change_points_to_same_value(
    existing: dict[str, FieldValue],
    incoming: dict[str, str],
) -> None:
    _, proposed = merge_scan_result(existing, incoming, now=NOW)
    for change in proposed:
        assert change.proposed_value != change.current_value, (
            "Proposed change proposes the same value it already has — noise."
        )
