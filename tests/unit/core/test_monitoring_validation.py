"""`core.monitoring.validate_check_config` unit + property tests.

Wave-3 TEST-A-017. The validation moved out of the CLI and out of
`db.monitoring.enable_check` to a single owner; these tests pin its
contract so future callers (TUI, alternative frontends) can rely on
the shape.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from langusta.core.monitoring import (
    VALID_CHECK_KINDS,
    VALID_COMPARATORS,
    validate_check_config,
)

# ---------------------------------------------------------------------------
# Happy paths per kind
# ---------------------------------------------------------------------------


def test_valid_icmp_has_no_errors() -> None:
    assert validate_check_config("icmp") == []


def test_valid_tcp_has_no_errors() -> None:
    assert validate_check_config("tcp") == []


def test_valid_http_has_no_errors() -> None:
    assert validate_check_config("http") == []


def test_valid_snmp_oid_has_no_errors() -> None:
    assert validate_check_config(
        "snmp_oid",
        oid="1.3.6.1.2.1.1.1.0",
        credential_label="snmp-lab",
    ) == []


def test_valid_ssh_command_has_no_errors() -> None:
    assert validate_check_config(
        "ssh_command",
        command="uptime",
        username="ops",
        credential_label="ssh-lab",
    ) == []


# ---------------------------------------------------------------------------
# Individual error paths
# ---------------------------------------------------------------------------


def test_rejects_unknown_kind() -> None:
    errs = validate_check_config("banana")
    assert any("banana" in e for e in errs)


def test_comparator_without_expected_is_rejected() -> None:
    errs = validate_check_config("snmp_oid", comparator="eq", expected=None)
    assert any("--expected" in e for e in errs)


def test_unknown_comparator_is_rejected() -> None:
    errs = validate_check_config(
        "snmp_oid", comparator="≈", expected="x",
        oid="1.3.6.1", credential_label="lab",
    )
    assert any("comparator" in e for e in errs)


def test_snmp_oid_requires_oid() -> None:
    errs = validate_check_config("snmp_oid", credential_label="lab")
    assert any("--oid" in e for e in errs)


def test_snmp_oid_requires_credential() -> None:
    errs = validate_check_config("snmp_oid", oid="1.3.6.1")
    assert any("credential" in e.lower() for e in errs)


def test_ssh_command_requires_command() -> None:
    errs = validate_check_config(
        "ssh_command", username="ops", credential_label="lab",
    )
    assert any("--command" in e for e in errs)


def test_ssh_command_requires_username() -> None:
    errs = validate_check_config(
        "ssh_command", command="uptime", credential_label="lab",
    )
    assert any("--user" in e for e in errs)


# ---------------------------------------------------------------------------
# Hypothesis: determinism + "--comparator requires --expected" exactly once
# ---------------------------------------------------------------------------


@settings(max_examples=50, deadline=None)
@given(
    kind=st.sampled_from(sorted(VALID_CHECK_KINDS | {"banana"})),
    comparator=st.one_of(
        st.none(),
        st.sampled_from(sorted(VALID_COMPARATORS | {"≈"})),
    ),
    expected=st.one_of(st.none(), st.text(max_size=20)),
    oid=st.one_of(st.none(), st.text(max_size=20)),
    command=st.one_of(st.none(), st.text(max_size=20)),
    username=st.one_of(st.none(), st.text(max_size=20)),
    credential_label=st.one_of(st.none(), st.text(max_size=20)),
)
def test_output_is_deterministic_for_a_given_input(
    kind: str,
    comparator: str | None,
    expected: str | None,
    oid: str | None,
    command: str | None,
    username: str | None,
    credential_label: str | None,
) -> None:
    """Two calls with the same inputs produce the same list — no hidden
    globals, no set-order leaks, no dict-iteration surprises."""
    kwargs = {
        "oid": oid,
        "comparator": comparator,
        "expected": expected,
        "command": command,
        "username": username,
        "credential_label": credential_label,
    }
    assert validate_check_config(kind, **kwargs) == validate_check_config(
        kind, **kwargs,
    )


@settings(max_examples=50, deadline=None)
@given(
    kind=st.sampled_from(sorted(VALID_CHECK_KINDS)),
    comparator=st.sampled_from(sorted(VALID_COMPARATORS)),
    expected=st.one_of(st.none(), st.text(max_size=20)),
)
def test_comparator_requires_expected_surfaces_exactly_once(
    kind: str, comparator: str, expected: str | None,
) -> None:
    """If comparator is set and expected is None, exactly one error
    names --expected. (Multiple emissions would indicate the rule
    was duplicated across branches.)"""
    errs = validate_check_config(kind, comparator=comparator, expected=expected)
    expected_msgs = [e for e in errs if "--expected" in e]
    if comparator is not None and expected is None:
        assert len(expected_msgs) == 1
    else:
        assert expected_msgs == []


def test_cli_no_longer_ships_duplicate_validation() -> None:
    """Wave-3 A-017's single-owner claim. cli.py's `monitor enable` used
    to inline its own `if comparator is not None and expected is None`
    check — this test guards against it reappearing."""
    import inspect

    from langusta import cli

    source = inspect.getsource(cli.monitor_enable)
    # The helper is the single owner; no more hand-rolled branches.
    assert "comparator is not None and expected is None" not in source, (
        "cli.monitor_enable is doing its own comparator/expected check "
        "again — delegate to core.monitoring.validate_check_config"
    )
