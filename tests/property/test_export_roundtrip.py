"""Hypothesis property — export/import roundtrip is lossless for the
user-owned tables. Wave-3 TEST-T-004.

The example-based tests already cover empty DBs and happy-path
envelopes; this fuzzes realistic shapes of the asset/mac/timeline
state to catch column-drop or serialisation bugs that example tests
wouldn't notice.
"""

from __future__ import annotations

from datetime import UTC, datetime

from hypothesis import given, settings
from hypothesis import strategies as st

from langusta.db import assets as assets_dal
from langusta.db.connection import connect
from langusta.db.export import export_to_dict, import_from_dict
from langusta.db.migrate import migrate
from tests.strategies import hostnames, ipv4

_NOW = datetime(2026, 4, 20, 12, 0, 0, tzinfo=UTC)


@settings(max_examples=15, deadline=None)
@given(
    hosts=st.lists(
        st.tuples(hostnames, ipv4),
        min_size=0, max_size=4,
        unique_by=lambda pair: (pair[0], pair[1]),
    ),
)
def test_export_import_roundtrip_is_lossless_for_user_tables(
    tmp_path_factory, hosts: list[tuple[str, str]],
) -> None:
    """Seed DB A with arbitrary assets → export → import into empty DB B
    → every user-owned table matches row-for-row. Picks only assets +
    field_provenance because those are what the DAL-level
    assets_dal.insert_manual produces directly; timeline etc. are
    covered indirectly via existing example tests."""
    src = tmp_path_factory.mktemp("src") / "db.sqlite"
    dst = tmp_path_factory.mktemp("dst") / "db.sqlite"
    migrate(src)
    migrate(dst)

    with connect(src) as c:
        for hostname, ip in hosts:
            assets_dal.insert_manual(
                c, hostname=hostname, primary_ip=ip, now=_NOW,
            )

    with connect(src) as c:
        dump = export_to_dict(c)
    with connect(dst) as c:
        import_from_dict(c, dump)

    for table in ("assets", "field_provenance"):
        with connect(src) as c1:
            a = c1.execute(
                f"SELECT * FROM {table} ORDER BY rowid",
            ).fetchall()
        with connect(dst) as c2:
            b = c2.execute(
                f"SELECT * FROM {table} ORDER BY rowid",
            ).fetchall()
        assert [tuple(r) for r in a] == [tuple(r) for r in b], (
            f"roundtrip diverged at table {table!r}"
        )
