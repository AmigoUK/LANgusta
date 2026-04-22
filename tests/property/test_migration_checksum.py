"""Hypothesis property: any mutation of a stored migration checksum
triggers `MigrationChecksumError` naming the mutated migration id.

Wave-3 TEST-T-023. The existing
`test_migrate_refuses_when_applied_migration_checksum_changes` covers
one hand-crafted mutated checksum; fuzzing confirms the refusal is not
sensitive to any particular checksum value.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from langusta.db.connection import connect
from langusta.db.migrate import (
    MigrationChecksumError,
    discover_migrations,
    migrate,
)


@settings(max_examples=20, deadline=None)
@given(
    new_checksum=st.text(
        alphabet="0123456789abcdef", min_size=64, max_size=64,
    ),
)
def test_any_checksum_mutation_triggers_refusal(
    tmp_path_factory, new_checksum: str
) -> None:
    shipped = discover_migrations()
    mig_id = shipped[-1].id
    # Skip the rare case where hypothesis happens to regenerate the
    # real checksum — that's not a mutation, and would false-negative.
    if new_checksum == shipped[-1].checksum:
        return

    db = tmp_path_factory.mktemp("ckt") / "db.sqlite"
    migrate(db)
    with connect(db) as conn:
        conn.execute(
            "UPDATE _migrations SET checksum = ? WHERE id = ?",
            (new_checksum, mig_id),
        )

    with pytest.raises(MigrationChecksumError, match=str(mig_id)):
        migrate(db)
