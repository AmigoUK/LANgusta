"""Property tests for `db.writer._build_scan_diff_body`.

Wave-3 TEST-A-011. The scan_diff body-builder was previously inline in
`_apply_update`; extracting it to a pure function made it possible to
fuzz the output format and assert structural invariants that were hard
to check through the DB-side integration tests.

Invariants:
  - Returns None iff there is nothing to report (empty fields + no MAC
    + no open ports).
  - Output is deterministic for a given input.
  - Every reported field name appears in the body.
  - Open ports are sorted numerically.
  - When a new MAC is present, the body names it verbatim.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from langusta.db.writer import _build_scan_diff_body

# Non-empty scannable field names + a sample of plausible values so we
# don't fuzz into ""-equal strings the writer would have filtered out.
_field_names = st.sampled_from(
    ["hostname", "primary_ip", "vendor", "detected_os", "device_type"],
)
_field_values = st.one_of(
    st.none(),
    st.text(min_size=1, max_size=20),
)


@settings(max_examples=40, deadline=None)
@given(
    fields=st.dictionaries(_field_names, _field_values, max_size=5),
    new_mac=st.one_of(
        st.none(),
        st.builds(
            lambda xs: ":".join(xs),
            st.lists(
                st.text(alphabet="0123456789abcdef", min_size=2, max_size=2),
                min_size=6, max_size=6,
            ),
        ),
    ),
    open_ports=st.sets(
        st.integers(min_value=1, max_value=65535), max_size=5,
    ),
)
def test_returns_none_iff_nothing_to_report(
    fields: dict[str, str | None],
    new_mac: str | None,
    open_ports: set[int],
) -> None:
    body = _build_scan_diff_body(
        changed_fields=fields, new_mac=new_mac, open_ports=open_ports,
    )
    has_something = bool(fields) or new_mac is not None or bool(open_ports)
    if has_something:
        assert body is not None, (
            f"expected a body for fields={fields!r}, mac={new_mac!r}, "
            f"ports={open_ports!r}"
        )
        assert body.startswith("Scan observed: ")
    else:
        assert body is None


@settings(max_examples=40, deadline=None)
@given(
    fields=st.dictionaries(_field_names, _field_values, max_size=5),
    new_mac=st.one_of(st.none(), st.just("aa:bb:cc:dd:ee:ff")),
    open_ports=st.sets(
        st.integers(min_value=1, max_value=65535), max_size=5,
    ),
)
def test_is_deterministic_for_a_given_input(
    fields: dict[str, str | None],
    new_mac: str | None,
    open_ports: set[int],
) -> None:
    """Two calls with equal inputs must yield equal outputs — no hidden
    global state, no set-iteration nondeterminism."""
    a = _build_scan_diff_body(
        changed_fields=fields, new_mac=new_mac, open_ports=open_ports,
    )
    b = _build_scan_diff_body(
        changed_fields=fields, new_mac=new_mac, open_ports=open_ports,
    )
    assert a == b


@settings(max_examples=30, deadline=None)
@given(
    fields=st.dictionaries(_field_names, _field_values, min_size=1, max_size=5),
)
def test_every_changed_field_appears_in_body(
    fields: dict[str, str | None],
) -> None:
    """Any caller-provided field must show up verbatim in the rendered
    body — otherwise a user reading the timeline loses information."""
    body = _build_scan_diff_body(
        changed_fields=fields, new_mac=None, open_ports=set(),
    )
    assert body is not None
    for name in fields:
        assert name in body, f"field {name!r} missing from {body!r}"


@settings(max_examples=30, deadline=None)
@given(
    open_ports=st.sets(
        st.integers(min_value=1, max_value=65535),
        min_size=1, max_size=8,
    ),
)
def test_open_ports_are_sorted_in_body(
    open_ports: set[int],
) -> None:
    body = _build_scan_diff_body(
        changed_fields={}, new_mac=None, open_ports=open_ports,
    )
    assert body is not None
    port_list = ", ".join(str(p) for p in sorted(open_ports))
    assert port_list in body, (
        f"ports not rendered in sorted order: body={body!r}, "
        f"expected {port_list!r}"
    )


def test_new_mac_appears_verbatim_in_body() -> None:
    body = _build_scan_diff_body(
        changed_fields={}, new_mac="aa:bb:cc:dd:ee:ff", open_ports=set(),
    )
    assert body is not None
    assert "aa:bb:cc:dd:ee:ff" in body
    assert "new MAC" in body


def test_empty_inputs_returns_none() -> None:
    assert _build_scan_diff_body(
        changed_fields={}, new_mac=None, open_ports=set(),
    ) is None
