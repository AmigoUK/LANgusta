"""Notification-sinks DAL tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from langusta.db import notifications as notif_dal
from langusta.db.connection import connect
from langusta.db.migrate import migrate

NOW = datetime(2026, 4, 17, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    p = tmp_path / "notif.sqlite"
    migrate(p)
    return p


def test_create_webhook_sink(db: Path) -> None:
    with connect(db) as conn:
        sid = notif_dal.create(
            conn, label="slack", kind="webhook",
            config={"url": "https://hooks.slack.com/services/TEST"},
            now=NOW,
        )
        rows = notif_dal.list_all(conn)
    assert len(rows) == 1
    assert rows[0].id == sid
    assert rows[0].label == "slack"
    assert rows[0].kind == "webhook"
    assert rows[0].config == {"url": "https://hooks.slack.com/services/TEST"}
    assert rows[0].enabled is True


def test_create_smtp_sink(db: Path) -> None:
    with connect(db) as conn:
        notif_dal.create(
            conn, label="oncall", kind="smtp",
            config={
                "host": "smtp.example.com", "port": 587,
                "from": "langusta@example.com", "to": "oncall@example.com",
                "starttls": True,
            },
            now=NOW,
        )
        [sink] = notif_dal.list_all(conn)
    assert sink.kind == "smtp"
    assert sink.config["host"] == "smtp.example.com"


def test_duplicate_label_rejected(db: Path) -> None:
    with connect(db) as conn:
        notif_dal.create(
            conn, label="slack", kind="webhook",
            config={"url": "https://a.example.com"}, now=NOW,
        )
        with pytest.raises(notif_dal.DuplicateLabel):
            notif_dal.create(
                conn, label="slack", kind="webhook",
                config={"url": "https://b.example.com"}, now=NOW,
            )


def test_create_rejects_unknown_kind(db: Path) -> None:
    with connect(db) as conn, pytest.raises(ValueError, match="unknown"):
        notif_dal.create(
            conn, label="bad", kind="carrier-pigeon",
            config={}, now=NOW,
        )


def test_disable_and_list_enabled(db: Path) -> None:
    with connect(db) as conn:
        a = notif_dal.create(
            conn, label="a", kind="webhook",
            config={"url": "https://a"}, now=NOW,
        )
        b = notif_dal.create(
            conn, label="b", kind="webhook",
            config={"url": "https://b"}, now=NOW,
        )
        notif_dal.disable(conn, a)
        enabled_only = notif_dal.list_all(conn, enabled_only=True)
    assert [s.id for s in enabled_only] == [b]


def test_delete_removes_row(db: Path) -> None:
    with connect(db) as conn:
        sid = notif_dal.create(
            conn, label="bye", kind="webhook",
            config={"url": "https://x"}, now=NOW,
        )
        notif_dal.delete(conn, sid)
        assert notif_dal.list_all(conn) == []


def test_get_by_label(db: Path) -> None:
    with connect(db) as conn:
        notif_dal.create(
            conn, label="slack", kind="webhook",
            config={"url": "https://h"}, now=NOW,
        )
        sink = notif_dal.get_by_label(conn, "slack")
    assert sink is not None
    assert sink.kind == "webhook"


def test_get_by_label_missing_returns_none(db: Path) -> None:
    with connect(db) as conn:
        assert notif_dal.get_by_label(conn, "nope") is None
