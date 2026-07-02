"""Meta DAL tests — key/value store for instance-level state.

Covers get, set_value, and delete. Audit T-9: meta.delete was previously
untested.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from langusta.db import meta as meta_dal
from langusta.db.connection import connect
from langusta.db.migrate import migrate

NOW = datetime(2026, 7, 2, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    p = tmp_path / "meta.sqlite"
    migrate(p)
    return p


def test_set_and_get(db: Path) -> None:
    with connect(db) as conn:
        meta_dal.set_value(conn, "test-key", "test-value", now=NOW)
        assert meta_dal.get(conn, "test-key") == "test-value"


def test_get_missing_returns_none(db: Path) -> None:
    with connect(db) as conn:
        assert meta_dal.get(conn, "nope") is None


def test_set_value_upserts(db: Path) -> None:
    with connect(db) as conn:
        meta_dal.set_value(conn, "k", "v1", now=NOW)
        meta_dal.set_value(conn, "k", "v2", now=NOW)
        assert meta_dal.get(conn, "k") == "v2"


def test_delete_removes_key(db: Path) -> None:
    with connect(db) as conn:
        meta_dal.set_value(conn, "doomed", "val", now=NOW)
        meta_dal.delete(conn, "doomed")
        assert meta_dal.get(conn, "doomed") is None


def test_delete_missing_key_is_noop(db: Path) -> None:
    with connect(db) as conn:
        meta_dal.delete(conn, "never-existed")  # must not raise
