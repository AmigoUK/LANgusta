"""Integration tests for `langusta notify ...`."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from langusta.cli import app
from langusta.db import notifications as notif_dal
from langusta.db.connection import connect

runner = CliRunner()

PW = "master-password-for-notify-tests-ok"


def _env(home: Path) -> dict[str, str]:
    return {"HOME": str(home), "LANGUSTA_MASTER_PASSWORD": PW}


@pytest.fixture
def home(tmp_path: Path) -> Path:
    h = tmp_path / "home"
    h.mkdir(parents=True)
    runner.invoke(app, ["init"], env=_env(h))
    return h


def test_notify_add_webhook(home: Path) -> None:
    r = runner.invoke(
        app, [
            "notify", "add-webhook",
            "--label", "slack",
            "--url", "https://hooks.slack.com/services/TEST",
        ],
        env=_env(home),
    )
    assert r.exit_code == 0, r.stdout
    with connect(home / ".langusta" / "db.sqlite") as conn:
        [sink] = notif_dal.list_all(conn)
    assert sink.kind == "webhook"
    assert sink.config["url"] == "https://hooks.slack.com/services/TEST"


def test_notify_add_smtp(home: Path) -> None:
    r = runner.invoke(
        app, [
            "notify", "add-smtp",
            "--label", "oncall",
            "--host", "smtp.example.com",
            "--port", "587",
            "--from", "langusta@example.com",
            "--to", "oncall@example.com",
            "--starttls",
        ],
        env=_env(home),
    )
    assert r.exit_code == 0, r.stdout
    with connect(home / ".langusta" / "db.sqlite") as conn:
        [sink] = notif_dal.list_all(conn)
    assert sink.kind == "smtp"
    assert sink.config["host"] == "smtp.example.com"
    assert sink.config["starttls"] is True


def test_notify_list_empty_is_friendly(home: Path) -> None:
    r = runner.invoke(app, ["notify", "list"], env=_env(home))
    assert r.exit_code == 0
    assert "always on" in r.stdout.lower() or "no" in r.stdout.lower()


def test_notify_list_after_add(home: Path) -> None:
    runner.invoke(
        app, ["notify", "add-webhook", "--label", "hooky", "--url", "https://x"],
        env=_env(home),
    )
    r = runner.invoke(app, ["notify", "list"], env=_env(home))
    assert r.exit_code == 0
    assert "hooky" in r.stdout
    assert "webhook" in r.stdout


def test_notify_rm(home: Path) -> None:
    runner.invoke(
        app, ["notify", "add-webhook", "--label", "bye", "--url", "https://x"],
        env=_env(home),
    )
    with connect(home / ".langusta" / "db.sqlite") as conn:
        [sink] = notif_dal.list_all(conn)
    r = runner.invoke(app, ["notify", "rm", str(sink.id)], env=_env(home))
    assert r.exit_code == 0
    with connect(home / ".langusta" / "db.sqlite") as conn:
        assert notif_dal.list_all(conn) == []


def test_notify_disable(home: Path) -> None:
    runner.invoke(
        app, ["notify", "add-webhook", "--label", "shh", "--url", "https://x"],
        env=_env(home),
    )
    with connect(home / ".langusta" / "db.sqlite") as conn:
        [sink] = notif_dal.list_all(conn)
    r = runner.invoke(app, ["notify", "disable", str(sink.id)], env=_env(home))
    assert r.exit_code == 0
    with connect(home / ".langusta" / "db.sqlite") as conn:
        [s] = notif_dal.list_all(conn)
    assert s.enabled is False


def test_notify_duplicate_label_is_user_error(home: Path) -> None:
    runner.invoke(
        app, ["notify", "add-webhook", "--label", "same", "--url", "https://a"],
        env=_env(home),
    )
    r = runner.invoke(
        app, ["notify", "add-webhook", "--label", "same", "--url", "https://b"],
        env=_env(home),
    )
    assert r.exit_code != 0
    assert "already" in (r.stdout + (r.stderr or "")).lower()


def test_notify_test_fires_at_one_sink(
    home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    hits: list[str] = []

    async def fake_post(url: str, json, *, timeout):
        hits.append(url)
        return 200

    monkeypatch.setattr("langusta.monitor.notifications._http_post", fake_post)
    runner.invoke(
        app, ["notify", "add-webhook", "--label", "t", "--url", "https://hooked"],
        env=_env(home),
    )
    with connect(home / ".langusta" / "db.sqlite") as conn:
        [sink] = notif_dal.list_all(conn)
    r = runner.invoke(app, ["notify", "test", str(sink.id)], env=_env(home))
    assert r.exit_code == 0, r.stdout
    assert hits == ["https://hooked"]
