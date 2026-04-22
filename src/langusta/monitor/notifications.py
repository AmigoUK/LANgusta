"""Monitor notifications: log, webhook, SMTP.

Spec §4 Pillar C: "Notifications in v1 are deliberately minimal: a
notification log in the TUI, optional webhook POST, optional local email
via SMTP. No SMS, no PagerDuty, no mobile app."

`dispatch(event, sinks, logfile_path)`:
  - Always appends a JSON line to `logfile_path` (default
    ~/.langusta/notifications.log). That's the notification log.
  - For each enabled sink, calls the matching sender. Per-sink failures
    are swallowed and logged via stderr so one broken sink doesn't stop
    the others.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import smtplib
import sys
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

from langusta.db.notifications import NotificationSink


@dataclass(frozen=True, slots=True)
class MonitorEvent:
    """One state transition worth notifying about."""

    asset_id: int
    asset_hostname: str | None
    asset_ip: str | None
    kind: str              # 'failure' | 'recovery'
    check_kind: str        # 'icmp' | 'tcp' | 'http'
    detail: str | None
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class SmtpConfig:
    host: str
    port: int
    sender: str
    recipient: str
    starttls: bool = False
    username: str | None = None
    password: str | None = None


# ---------------------------------------------------------------------------
# Injection points (patched by tests)
# ---------------------------------------------------------------------------


async def _http_post(url: str, json: dict, *, timeout: float) -> int:
    import httpx

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, json=json)
    return resp.status_code


def _smtp_send_blocking(cfg: SmtpConfig, subject: str, body: str) -> None:
    """Sync sendmail — the caller wraps in asyncio.to_thread."""
    msg = EmailMessage()
    msg["From"] = cfg.sender
    msg["To"] = cfg.recipient
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP(cfg.host, cfg.port, timeout=10.0) as smtp:
        if cfg.starttls:
            smtp.starttls()
        if cfg.username and cfg.password:
            smtp.login(cfg.username, cfg.password)
        smtp.send_message(msg)


# ---------------------------------------------------------------------------
# Individual senders
# ---------------------------------------------------------------------------


def _event_to_dict(event: MonitorEvent) -> dict:
    return {
        "asset_id": event.asset_id,
        "asset_hostname": event.asset_hostname,
        "asset_ip": event.asset_ip,
        "kind": event.kind,
        "check_kind": event.check_kind,
        "detail": event.detail,
        "occurred_at": event.occurred_at.isoformat(timespec="seconds"),
    }


async def send_webhook(config: dict, event: MonitorEvent) -> bool:
    url = config.get("url")
    if not url:
        return False
    timeout = float(config.get("timeout", 5.0))
    try:
        status = await _http_post(url, _event_to_dict(event), timeout=timeout)
    except Exception as exc:
        # Slack/Discord-style webhooks encode the auth token in the URL
        # path; never echo the full URL on failure. Log the origin only
        # (scheme + host[:port]) so the operator can still identify the
        # failing sink without leaking the secret.
        print(
            f"webhook sink to {_origin_of(url)} failed: {exc}",
            file=sys.stderr,
        )
        return False
    return 200 <= status < 300


def _origin_of(url: str) -> str:
    """Return scheme://netloc from `url`, dropping path/query/fragment.
    Safe to log in failure paths where the path may contain a token."""
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return "<invalid-url>"
    return f"{parts.scheme}://{parts.netloc}"


async def send_smtp(config: dict, event: MonitorEvent) -> bool:
    cfg = SmtpConfig(
        host=config["host"],
        port=int(config["port"]),
        sender=config["from"],
        recipient=config["to"],
        starttls=bool(config.get("starttls", False)),
        username=config.get("username"),
        password=config.get("password"),
    )
    host = event.asset_hostname or event.asset_ip or f"#{event.asset_id}"
    subject = f"[LANgusta] {event.check_kind} {event.kind} on {host}"
    body_lines = [
        f"Asset: {host}",
        f"IP: {event.asset_ip}",
        f"Check: {event.check_kind}",
        f"Result: {event.kind}",
    ]
    if event.detail:
        body_lines.append(f"Detail: {event.detail}")
    body_lines.append(f"Time: {event.occurred_at.isoformat(timespec='seconds')}")
    body = "\n".join(body_lines) + "\n"

    try:
        await asyncio.to_thread(_smtp_send_blocking, cfg, subject, body)
    except Exception as exc:
        print(f"smtp sink to {cfg.host!r} failed: {exc}", file=sys.stderr)
        return False
    return True


async def _send_logfile(config: dict, event: MonitorEvent) -> bool:
    path = config.get("path")
    if not path:
        return False
    try:
        _append_log_line(Path(path), event)
    except OSError as exc:
        print(f"logfile sink to {path!r} failed: {exc}", file=sys.stderr)
        return False
    return True


# ---------------------------------------------------------------------------
# Always-on log file
# ---------------------------------------------------------------------------


def _append_log_line(path: Path, event: MonitorEvent) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(_event_to_dict(event)) + "\n")


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


_SENDERS = {
    "webhook": send_webhook,
    "smtp": send_smtp,
    "logfile": _send_logfile,
}


async def dispatch(
    event: MonitorEvent,
    *,
    sinks: list[NotificationSink],
    logfile_path: Path,
) -> None:
    """Write to the always-on log file, then fan out to every enabled sink.

    Individual sink failures are swallowed so one broken sink doesn't block
    the others — failures go to stderr for the service-manager to pick up.
    """
    with contextlib.suppress(OSError):
        _append_log_line(logfile_path, event)

    for sink in sinks:
        if not sink.enabled:
            continue
        sender = _SENDERS.get(sink.kind)
        if sender is None:
            continue
        try:
            await sender(sink.config, event)
        except Exception as exc:
            print(
                f"notification sink {sink.label!r} ({sink.kind}) "
                f"threw during dispatch: {exc}",
                file=sys.stderr,
            )
