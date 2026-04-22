"""Monitor-check config validation + heartbeat freshness, stdlib-only.

The CLI's `monitor enable` and any future programmatic config surface
share these kind/field-interaction rules from here. DAL-level guards
in `db.monitoring.enable_check` remain a last-line-of-defence for
contract violations; this module is the single place that enumerates
ALL validation errors up front so a user-facing prompt can surface
them together instead of failing one at a time. Wave-3 TEST-A-017.

Also hosts `is_heartbeat_stale`, a pure function that was previously
misfiled in `db/monitoring.py` beside the SQL helpers (Wave-3 A-006);
the logic doesn't touch the DB and belongs next to the rest of the
monitor-domain pure code.
"""

from __future__ import annotations

from datetime import datetime, timedelta

VALID_CHECK_KINDS: frozenset[str] = frozenset({
    "icmp", "tcp", "http", "snmp_oid", "ssh_command",
})
VALID_COMPARATORS: frozenset[str] = frozenset({
    "eq", "neq", "contains", "gt", "lt",
})


def validate_check_config(
    kind: str,
    *,
    oid: str | None = None,
    comparator: str | None = None,
    expected: str | None = None,
    command: str | None = None,
    username: str | None = None,
    credential_label: str | None = None,
) -> list[str]:
    """Return a list of validation error strings for the given config.
    Empty list == valid. Output ordering is stable for a given input
    (errors are appended in a fixed sequence), so the list can be used
    directly by a CLI or TUI surface that prints them in order.

    Rules:
      - `kind` must be in `VALID_CHECK_KINDS`.
      - When `comparator` is set it must be in `VALID_COMPARATORS` AND
        `expected` must also be set (the two fields only make sense
        together — comparing against nothing is ill-defined).
      - `snmp_oid` requires `oid` + `credential_label`.
      - `ssh_command` requires `command` + `credential_label` + `username`.
    """
    errors: list[str] = []
    if kind not in VALID_CHECK_KINDS:
        errors.append(
            f"unknown kind {kind!r}; valid: {sorted(VALID_CHECK_KINDS)}"
        )
    if comparator is not None:
        if comparator not in VALID_COMPARATORS:
            errors.append(
                f"unknown comparator {comparator!r}; "
                f"valid: {sorted(VALID_COMPARATORS)}"
            )
        if expected is None:
            errors.append("--comparator requires --expected")
    if kind == "snmp_oid":
        if not oid:
            errors.append("snmp_oid checks require --oid")
        if not credential_label:
            errors.append("snmp_oid checks require a --credential-label")
    if kind == "ssh_command":
        if not command:
            errors.append("ssh_command checks require --command")
        if not credential_label:
            errors.append("ssh_command checks require a --credential-label")
        if not username:
            errors.append("ssh_command checks require --user")
    return errors


def is_heartbeat_stale(
    heartbeat: datetime | None,
    *,
    now: datetime,
    tolerance_seconds: int,
) -> bool:
    """Return True when the monitor daemon's last heartbeat is too
    old — either never recorded (`heartbeat is None`) or older than
    `tolerance_seconds`. Pure function; takes its inputs explicitly
    so tests don't need a DB fixture."""
    if heartbeat is None:
        return True
    return (now - heartbeat) > timedelta(seconds=tolerance_seconds)
