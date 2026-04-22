"""Hypothesis property tests for `backup.write`'s dedupe-window contract.

The invariant: given any sequence of `write()` calls each with the same
`dedupe_window_hours`, no two surviving snapshots are closer together
than `window_hours`. Wave-3 TEST-T-006 (single-lens test-gap, medium).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from langusta import backup as backup_mod
from langusta.db import assets as assets_dal
from langusta.db.connection import connect
from langusta.db.migrate import migrate

_BASE = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)


def _seed_tmp(tmp: Path) -> tuple[Path, Path]:
    db = tmp / "home" / ".langusta" / "db.sqlite"
    db.parent.mkdir(parents=True)
    migrate(db)
    with connect(db) as conn:
        assets_dal.insert_manual(
            conn, hostname="r", primary_ip="10.0.0.1", now=_BASE,
        )
    return db, tmp / "home" / ".langusta" / "backups"


@settings(max_examples=30, deadline=None)
@given(
    offsets_hours=st.lists(
        st.floats(min_value=0.0, max_value=240.0, allow_nan=False),
        max_size=10,
    ),
    window_hours=st.floats(
        min_value=0.1, max_value=24.0, allow_nan=False,
    ),
)
def test_no_two_snapshots_closer_than_dedupe_window(
    tmp_path_factory, offsets_hours, window_hours
):
    """For any sequence of `write()` calls with the same window, no two
    surviving snapshots end up closer than the window. Stamps are
    second-resolution so we allow an epsilon slack."""
    tmp = tmp_path_factory.mktemp("bk")
    db, backups = _seed_tmp(tmp)

    for off in offsets_hours:
        backup_mod.write(
            db, backups,
            now=_BASE + timedelta(hours=off),
            dedupe_window_hours=window_hours,
        )

    stamps = sorted(b.stamp for b in backup_mod.list_backups(backups))
    # Allow 1 second of slack for timestamp-string second resolution.
    slack = timedelta(seconds=1)
    for a, b in pairwise(stamps):
        gap = b - a
        assert gap + slack >= timedelta(hours=window_hours), (
            f"adjacent snapshots {a.isoformat()} / {b.isoformat()} are "
            f"{gap} apart, below window={window_hours}h"
        )
