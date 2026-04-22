"""Tests for the notification sinks + dispatcher."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from langusta.monitor.notifications import (
    MonitorEvent,
    SmtpConfig,
    dispatch,
    send_smtp,
    send_webhook,
)

NOW = datetime(2026, 4, 17, 12, 0, 0, tzinfo=UTC)


def _event(*, kind: str = "failure") -> MonitorEvent:
    return MonitorEvent(
        asset_id=7,
        asset_hostname="router",
        asset_ip="10.0.0.1",
        kind=kind,
        check_kind="icmp",
        detail="no response",
        occurred_at=NOW,
    )


# ---------------------------------------------------------------------------
# Log sink (always on)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_writes_log_line(tmp_path: Path) -> None:
    logfile = tmp_path / "notifications.log"
    await dispatch(_event(), sinks=[], logfile_path=logfile)
    assert logfile.exists()
    lines = logfile.read_text().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["asset_id"] == 7
    assert entry["asset_hostname"] == "router"
    assert entry["kind"] == "failure"


@pytest.mark.asyncio
async def test_dispatch_appends_not_overwrites(tmp_path: Path) -> None:
    logfile = tmp_path / "notifications.log"
    await dispatch(_event(kind="failure"), sinks=[], logfile_path=logfile)
    await dispatch(_event(kind="recovery"), sinks=[], logfile_path=logfile)
    lines = logfile.read_text().splitlines()
    assert len(lines) == 2


# ---------------------------------------------------------------------------
# Webhook sink
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_webhook_posts_json(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: dict = {}

    async def fake_post(url: str, json: dict, *, timeout: float):
        sent["url"] = url
        sent["json"] = json
        sent["timeout"] = timeout
        return 200

    monkeypatch.setattr("langusta.monitor.notifications._http_post", fake_post)
    ok = await send_webhook(
        {"url": "https://hooks.example.com/x"}, _event(),
    )
    assert ok is True
    assert sent["url"] == "https://hooks.example.com/x"
    assert sent["json"]["asset_id"] == 7
    assert sent["json"]["kind"] == "failure"


@pytest.mark.asyncio
async def test_webhook_non_2xx_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_post(url, json, *, timeout):
        return 500

    monkeypatch.setattr("langusta.monitor.notifications._http_post", fake_post)
    assert await send_webhook({"url": "https://x"}, _event()) is False


@pytest.mark.asyncio
async def test_webhook_exception_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_post(url, json, *, timeout):
        raise OSError("connection refused")

    monkeypatch.setattr("langusta.monitor.notifications._http_post", fake_post)
    assert await send_webhook({"url": "https://x"}, _event()) is False


# ---------------------------------------------------------------------------
# SMTP sink
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_smtp_sends_email(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict = {}

    def fake_sendmail(cfg: SmtpConfig, subject: str, body: str) -> None:
        called["host"] = cfg.host
        called["subject"] = subject
        called["body"] = body

    monkeypatch.setattr(
        "langusta.monitor.notifications._smtp_send_blocking", fake_sendmail,
    )
    config = {
        "host": "smtp.example.com", "port": 587,
        "from": "langusta@example.com", "to": "oncall@example.com",
        "starttls": True,
    }
    ok = await send_smtp(config, _event())
    assert ok is True
    assert called["host"] == "smtp.example.com"
    assert "router" in called["subject"] or "10.0.0.1" in called["subject"]
    assert "failure" in called["subject"].lower()
    assert "no response" in called["body"]


@pytest.mark.asyncio
async def test_smtp_exception_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_sendmail(cfg, subject, body):
        raise ConnectionRefusedError("no mail server")

    monkeypatch.setattr(
        "langusta.monitor.notifications._smtp_send_blocking", fake_sendmail,
    )
    config = {
        "host": "smtp.x", "port": 25, "from": "x@x", "to": "y@y",
    }
    assert await send_smtp(config, _event()) is False


# ---------------------------------------------------------------------------
# Dispatch — sinks honoured, per-sink failure doesn't stop the rest
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_invokes_enabled_sinks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    hits: list[str] = []

    async def fake_post(url, json, *, timeout):
        hits.append(f"webhook:{url}")
        return 200

    def fake_smtp(cfg, subject, body):
        hits.append(f"smtp:{cfg.host}")

    monkeypatch.setattr("langusta.monitor.notifications._http_post", fake_post)
    monkeypatch.setattr(
        "langusta.monitor.notifications._smtp_send_blocking", fake_smtp,
    )

    from langusta.db.notifications import NotificationSink
    sinks = [
        NotificationSink(
            id=1, label="hook", kind="webhook",
            config={"url": "https://hooks.example.com/x"},
            enabled=True, created_at=NOW,
        ),
        NotificationSink(
            id=2, label="mail", kind="smtp",
            config={
                "host": "smtp.example.com", "port": 587,
                "from": "a@b", "to": "c@d",
            },
            enabled=True, created_at=NOW,
        ),
    ]

    await dispatch(_event(), sinks=sinks, logfile_path=tmp_path / "log")
    assert "webhook:https://hooks.example.com/x" in hits
    assert "smtp:smtp.example.com" in hits


@pytest.mark.asyncio
async def test_dispatch_one_sink_failure_doesnt_stop_others(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    hits: list[str] = []

    async def fake_post(url, json, *, timeout):
        hits.append("webhook")
        raise OSError("boom")

    def fake_smtp(cfg, subject, body):
        hits.append("smtp")

    monkeypatch.setattr("langusta.monitor.notifications._http_post", fake_post)
    monkeypatch.setattr(
        "langusta.monitor.notifications._smtp_send_blocking", fake_smtp,
    )

    from langusta.db.notifications import NotificationSink
    sinks = [
        NotificationSink(
            id=1, label="hook", kind="webhook",
            config={"url": "https://x"}, enabled=True, created_at=NOW,
        ),
        NotificationSink(
            id=2, label="mail", kind="smtp",
            config={"host": "smtp.x", "port": 25, "from": "a@b", "to": "c@d"},
            enabled=True, created_at=NOW,
        ),
    ]
    await dispatch(_event(), sinks=sinks, logfile_path=tmp_path / "log")
    assert "smtp" in hits  # SMTP still fired after webhook raised


@pytest.mark.asyncio
async def test_dispatch_skips_disabled_sinks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    hits: list[str] = []

    async def fake_post(url, json, *, timeout):
        hits.append("webhook")
        return 200

    monkeypatch.setattr("langusta.monitor.notifications._http_post", fake_post)

    from langusta.db.notifications import NotificationSink
    sinks = [
        NotificationSink(
            id=1, label="off", kind="webhook",
            config={"url": "https://x"}, enabled=False, created_at=NOW,
        ),
    ]
    await dispatch(_event(), sinks=sinks, logfile_path=tmp_path / "log")
    assert hits == []


# ---------------------------------------------------------------------------
# Wave-3 TEST-S-014 — webhook failure logs must not echo URL path/token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_webhook_failure_log_does_not_echo_token(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """On webhook delivery failure `send_webhook` writes to stderr. The
    URL is typically the only identifying info, but Slack/Discord-style
    webhooks encode the auth token right in the path. Logging the full
    URL on failure leaks that token to anyone who sees the daemon's
    stderr (log file, systemd journal, service manager). The sink must
    log an origin-only form."""

    async def boom(url: str, payload: object, *, timeout: float) -> int:
        raise RuntimeError("connection refused")

    monkeypatch.setattr(
        "langusta.monitor.notifications._http_post", boom,
    )

    webhook_url = (
        "https://hooks.slack.com/services/T01234/B56789/SUPER_SECRET_TOKEN"
    )
    ok = await send_webhook({"url": webhook_url}, _event())
    assert ok is False

    err = capsys.readouterr().err
    assert "SUPER_SECRET_TOKEN" not in err, (
        f"webhook failure log leaked the token: {err!r}"
    )
    assert "/services/T01234" not in err, (
        f"webhook failure log leaked the path segments: {err!r}"
    )
    # The host is fine to log — it's how the operator identifies which
    # sink failed without needing the secret suffix.
    assert "hooks.slack.com" in err, (
        f"failure log should still name the host: {err!r}"
    )


# ---------------------------------------------------------------------------
# Wave-3 TEST-T-022 — send_smtp builds subject/body and returns True on OK
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_smtp_constructs_expected_subject_and_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """send_smtp's subject+body construction was implicitly covered only
    by the dispatcher integration test — no direct assert on the
    rendered strings existed. If the template were rewritten to drop
    (e.g.) the host or the check kind, the operator would silently lose
    context in their inbox. This test locks the template in."""
    captured: dict[str, object] = {}

    def fake_send(cfg: SmtpConfig, subject: str, body: str) -> None:
        captured["cfg"] = cfg
        captured["subject"] = subject
        captured["body"] = body

    monkeypatch.setattr(
        "langusta.monitor.notifications._smtp_send_blocking",
        fake_send,
    )

    config = {
        "host": "smtp.example.com",
        "port": 25,
        "from": "langusta@example.com",
        "to": "ops@example.com",
        "starttls": False,
    }
    ok = await send_smtp(config, _event())

    assert ok is True
    assert isinstance(captured.get("subject"), str)
    assert "icmp" in captured["subject"], captured["subject"]
    assert "router" in captured["subject"], captured["subject"]
    assert "failure" in captured["subject"], captured["subject"]

    assert isinstance(captured.get("body"), str)
    body = captured["body"]
    assert "router" in body
    assert "10.0.0.1" in body
    assert "icmp" in body
    assert "failure" in body
    assert "no response" in body  # the detail line


# ---------------------------------------------------------------------------
# Wave-3 TEST-C-010 — logfile-write failures surface to stderr
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_surfaces_logfile_write_failure_to_stderr(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The always-on log-sink write used to be wrapped in
    `contextlib.suppress(OSError)` — a failure (disk full, EACCES on
    a symlink loop, out-of-quota) disappeared silently and the
    operator never learned the trail was empty. Failures must surface
    to stderr so the service manager picks them up.

    Monkeypatches the log-append helper to raise -- platform-level
    perms aren't reliable under the test runner (often root in CI)."""
    def boom(*_a: object, **_kw: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(
        "langusta.monitor.notifications._append_log_line", boom,
    )

    logfile = tmp_path / "notifications.log"
    await dispatch(_event(), sinks=[], logfile_path=logfile)

    err = capsys.readouterr().err
    assert str(logfile) in err, (
        f"logfile path should appear in the stderr message; got {err!r}"
    )
    assert "No space left" in err or "28" in err, (
        f"the underlying OSError reason should surface; got {err!r}"
    )
