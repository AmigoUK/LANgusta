"""Field-level provenance and the scanner-proposes-human-disposes rule.

Spec reference: docs/specs/01-functionality-and-moscow.md §4 Pillar A; §8.
ADR: docs/adr/0001-data-layer-orm-choice.md (stdlib-only core/).

The `merge_scan_result` function is the single authority on what happens when
a scan observation meets existing asset state. Every write path (scan in M2,
SNMP in M5, import in M6, monitor NEVER writes asset fields) routes through
this function. If it's wrong, the product promise breaks.

This module is deliberately stdlib-only — no third-party imports — so it can
be unit-tested without touching the DB, the network, or the TUI.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class FieldProvenance(Enum):
    """Who last set the authoritative value of a field."""

    SCANNED = "scanned"      # observed by a scanner (ICMP, ARP, SNMP, ...)
    MANUAL = "manual"        # entered by a human via TUI/CLI
    IMPORTED = "imported"    # ingested from an external tool (Lansweeper, NetBox, ...)


# Provenance values the scanner is NOT allowed to silently overwrite.
PROTECTED_PROVENANCE = frozenset({FieldProvenance.MANUAL, FieldProvenance.IMPORTED})


@dataclass(frozen=True, slots=True)
class FieldValue:
    """A field's current value, plus who set it and when."""

    value: str
    provenance: FieldProvenance
    set_at: datetime


@dataclass(frozen=True, slots=True)
class ProposedChange:
    """A scan observation that would modify a protected field.

    Lives in the review queue until the human approves, rejects, or edits it.
    """

    field: str
    current_value: str
    current_provenance: FieldProvenance
    proposed_value: str
    observed_at: datetime


def merge_scan_result(
    existing: dict[str, FieldValue],
    incoming: dict[str, str],
    *,
    now: datetime,
) -> tuple[dict[str, FieldValue], list[ProposedChange]]:
    """Merge scan observations into an asset's field map.

    Returns:
        applied:  a dict of fields the caller should write to the asset. Each
                  entry carries the correct provenance — NEVER flips MANUAL or
                  IMPORTED to SCANNED. Timestamps refresh on same-value scans
                  so last-seen queries work.
        proposed: changes the scanner wanted to make against a protected field
                  but declined to apply. These go to the review queue.

    Invariants (property-tested in tests/unit/core/test_provenance.py):
      1. For every field in `existing` whose provenance is MANUAL or IMPORTED,
         the returned applied[field].value equals existing[field].value — no
         matter what `incoming` contains.
      2. Every conflicting observation on a protected field appears in
         `proposed`. No silent drops.
      3. A proposed change never proposes the value the field already has.
    """
    applied: dict[str, FieldValue] = {}
    proposed: list[ProposedChange] = []

    for field_name, observed in incoming.items():
        prior = existing.get(field_name)

        if prior is None:
            # New field — apply with SCANNED provenance.
            applied[field_name] = FieldValue(
                value=observed, provenance=FieldProvenance.SCANNED, set_at=now
            )
            continue

        if prior.provenance in PROTECTED_PROVENANCE:
            if observed == prior.value:
                # Scanner confirms what the human/import already knows. Don't
                # refresh set_at — that would rewrite the provenance of a
                # manual entry. Leave the field untouched.
                continue
            # Conflicting observation on a protected field: propose, never apply.
            proposed.append(
                ProposedChange(
                    field=field_name,
                    current_value=prior.value,
                    current_provenance=prior.provenance,
                    proposed_value=observed,
                    observed_at=now,
                )
            )
            continue

        # Prior was SCANNED: a newer scan wins; same-value observation refreshes
        # the timestamp (liveness signal).
        applied[field_name] = FieldValue(
            value=observed, provenance=FieldProvenance.SCANNED, set_at=now
        )

    return applied, proposed
