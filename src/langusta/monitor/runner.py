"""Single-cycle monitor runner.

`run_once(conn, *, now)`:
  - Loads due `monitoring_checks`.
  - Executes each via the appropriate Check implementation, concurrently.
  - Persists `check_results` and updates `monitoring_checks.last_*`.
  - On state transitions (ok↔fail), writes a `monitor_event` timeline entry
    via the insert-only DAL — so monitoring events become first-class
    citizens of the asset's immutable history (spec §4 Pillar C).
  - Updates `meta.daemon_heartbeat`.

The function is called both by `langusta monitor run` (single-shot) and
by the long-running daemon loop (M7+).

Checks that need credentials (`snmp_oid`, `ssh_command`) require a
`vault` argument. Per-cycle credential decrypts are cached by
`credential_id` so that N checks sharing a label cost one decrypt, not N.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any

from langusta.crypto.vault import Vault
from langusta.db import assets as assets_dal
from langusta.db import credentials as cred_dal
from langusta.db import monitoring as mon_dal
from langusta.db import notifications as notif_dal
from langusta.db import timeline as tl_dal
from langusta.monitor.checks.base import Check, CheckResult
from langusta.monitor.checks.http import HttpCheck
from langusta.monitor.checks.icmp import IcmpCheck
from langusta.monitor.checks.snmp_oid import SnmpOidCheck
from langusta.monitor.checks.ssh_command import SshCommandCheck
from langusta.monitor.checks.tcp import TcpCheck
from langusta.monitor.notifications import MonitorEvent
from langusta.monitor.notifications import dispatch as dispatch_event
from langusta.monitor.ssh.asyncssh_backend import AsyncsshBackend
from langusta.monitor.ssh.auth import cred_to_ssh_auth
from langusta.scan.snmp.credentials import cred_to_snmp_auth
from langusta.scan.snmp.pysnmp_backend import PysnmpBackend

DEFAULT_MAX_CONCURRENCY = 32


def _default_registry() -> dict[str, Check]:
    return {
        "icmp": IcmpCheck(),
        "tcp": TcpCheck(),
        "http": HttpCheck(),
        "snmp_oid": SnmpOidCheck(),
        "ssh_command": SshCommandCheck(),
    }


# `MappingProxyType` makes the default registry read-only — a stray
# `DEFAULT_REGISTRY[...] = ...` at runtime would raise TypeError instead
# of silently mutating the shared module-level state every cycle uses.
# Test code still replaces the whole module attribute via monkeypatch,
# which is how swap-out is supposed to work. Wave-3 C-012.
DEFAULT_REGISTRY: Mapping[str, Check] = MappingProxyType(_default_registry())


@dataclass(frozen=True, slots=True)
class RunSummary:
    executed: int
    ok_count: int
    fail_count: int
    transitions: int


async def run_once(
    conn: sqlite3.Connection,
    *,
    now: datetime,
    check_registry: Mapping[str, Check] | None = None,
    notifications_logfile: Path | None = None,
    vault: Vault | None = None,
    ssh_client: Any | None = None,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
) -> RunSummary:
    registry = check_registry if check_registry is not None else DEFAULT_REGISTRY
    due = mon_dal.list_due(conn, now=now)

    # Load sinks up-front so in-flight inserts don't affect this cycle's
    # notification targets.
    sinks = notif_dal.list_all(conn, enabled_only=True)

    # Per-cycle credential cache: credential_id -> resolved context object.
    cred_cache: dict[int, Any] = {}

    # Stateless clients: construct only the ones any due check actually
    # needs. A loop with nothing but ICMP/TCP/HTTP pays nothing for
    # pysnmp / asyncssh import + socket setup. Wave-3 A-016.
    needs_snmp = any(c.kind == "snmp_oid" for c in due)
    needs_ssh = any(c.kind == "ssh_command" for c in due)
    snmp_client = PysnmpBackend() if needs_snmp else None
    if ssh_client is not None:
        active_ssh_client: Any = ssh_client
    elif needs_ssh:
        active_ssh_client = AsyncsshBackend()
    else:
        active_ssh_client = None

    # Cap in-flight checks so a fleet of long-running SSH/SNMP probes can't
    # open hundreds of sockets at once.
    semaphore = asyncio.Semaphore(max_concurrency)

    # Dispatch all due checks concurrently.
    tasks = []
    for check in due:
        impl = registry.get(check.kind)
        if impl is None:
            continue
        try:
            config = _resolve_config(
                check, conn=conn, vault=vault,
                snmp_client=snmp_client, ssh_client=active_ssh_client,
                cred_cache=cred_cache,
            )
        except _ConfigError as exc:
            # Pre-run config failures still write a result + surface as a fail.
            mon_dal.record_result(
                conn, check_id=check.id, asset_id=check.asset_id,
                status="fail", latency_ms=None, detail=str(exc), now=now,
            )
            continue
        tasks.append(
            _run_one(
                check, impl, conn, now,
                config=config,
                sinks=sinks,
                notifications_logfile=notifications_logfile,
                semaphore=semaphore,
            )
        )
    # `return_exceptions=True` so that one task blowing up in its
    # record_result / event-write path doesn't cancel siblings or
    # prevent the heartbeat write below. Unexpected errors are
    # surfaced to stderr (the service manager picks them up) and
    # counted as failures in the summary.
    raw_outcomes = await asyncio.gather(*tasks, return_exceptions=True) if tasks else []
    outcomes: list[_Outcome] = []
    for o in raw_outcomes:
        if isinstance(o, BaseException):
            import sys
            print(
                f"monitor check raised during run_once: {o!r}",
                file=sys.stderr,
            )
            outcomes.append(_Outcome(status="fail", transitioned=False))
        else:
            outcomes.append(o)

    # Heartbeat must land even on a partially-failed cycle -- otherwise
    # a single bad row wedges the operator's liveness signal.
    mon_dal.set_heartbeat(conn, now=now)

    executed = len(outcomes)
    ok_count = sum(1 for o in outcomes if o.status == "ok")
    fail_count = sum(1 for o in outcomes if o.status == "fail")
    transitions = sum(1 for o in outcomes if o.transitioned)

    return RunSummary(
        executed=executed, ok_count=ok_count, fail_count=fail_count,
        transitions=transitions,
    )


class _ConfigError(RuntimeError):
    """Raised before the check runs when its configuration is unusable."""


def _resolve_config(
    check: mon_dal.MonitoringCheck,
    *,
    conn: sqlite3.Connection,
    vault: Vault | None,
    snmp_client: Any,
    ssh_client: Any,
    cred_cache: dict[int, Any],
) -> dict[str, Any]:
    """Assemble the **config kwargs dict passed to Check.run for `check`."""
    if check.kind == "icmp":
        return {}
    if check.kind == "tcp":
        config: dict[str, Any] = {}
        if check.port is not None:
            config["port"] = check.port
        if check.timeout_seconds is not None:
            config["timeout"] = check.timeout_seconds
        return config
    if check.kind == "http":
        config = {}
        if check.port is not None:
            config["port"] = check.port
        if check.path is not None:
            config["path"] = check.path
        if check.timeout_seconds is not None:
            config["timeout"] = check.timeout_seconds
        return config
    if check.kind == "snmp_oid":
        snmp_auth = _resolve_credential(
            check, conn=conn, vault=vault, cred_cache=cred_cache,
            expected_kinds={"snmp_v2c", "snmp_v3"}, decoder=cred_to_snmp_auth,
            label="snmp_oid",
        )
        return {
            "oid": check.oid,
            "expected_value": check.expected_value,
            "comparator": check.comparator,
            "timeout_seconds": check.timeout_seconds,
            "snmp_auth": snmp_auth,
            "snmp_client": snmp_client,
        }
    if check.kind == "ssh_command":
        ssh_auth = _resolve_credential(
            check, conn=conn, vault=vault, cred_cache=cred_cache,
            expected_kinds={"ssh_key", "ssh_password"}, decoder=cred_to_ssh_auth,
            label="ssh_command",
        )
        return {
            "command": check.command,
            "username": check.username,
            "port": check.port,
            "timeout_seconds": check.timeout_seconds,
            "success_exit_code": check.success_exit_code,
            "stdout_pattern": check.stdout_pattern,
            "ssh_auth": ssh_auth,
            "ssh_client": ssh_client,
        }
    raise _ConfigError(f"unknown check kind {check.kind!r}")


def _resolve_credential(
    check: mon_dal.MonitoringCheck,
    *,
    conn: sqlite3.Connection,
    vault: Vault | None,
    cred_cache: dict[int, Any],
    expected_kinds: set[str],
    decoder,
    label: str,
) -> Any:
    if check.credential_id is None:
        raise _ConfigError(f"{label} check has no credential_id")
    if vault is None:
        raise _ConfigError(f"{label} check requires unlocked vault")
    cached = cred_cache.get(check.credential_id)
    if cached is not None:
        return cached
    info = cred_dal.get_by_id(conn, check.credential_id)
    if info is None:
        raise _ConfigError(f"credential id={check.credential_id} not found")
    if info.kind not in expected_kinds:
        raise _ConfigError(
            f"credential {info.label!r} is {info.kind}, "
            f"{label} requires one of {sorted(expected_kinds)}"
        )
    secret = cred_dal.get_secret(conn, credential_id=info.id, vault=vault)
    resolved = decoder(info, secret)
    cred_cache[check.credential_id] = resolved
    return resolved


@dataclass(frozen=True, slots=True)
class _Outcome:
    status: str
    transitioned: bool


async def _run_one(
    check: mon_dal.MonitoringCheck,
    impl: Check,
    conn: sqlite3.Connection,
    now: datetime,
    *,
    config: dict[str, Any],
    sinks: list,
    notifications_logfile: Path | None,
    semaphore: asyncio.Semaphore,
) -> _Outcome:
    asset = assets_dal.get_by_id(conn, check.asset_id)
    target = check.target or (asset.primary_ip if asset is not None else None)
    if target is None:
        # No target? record a fail without spamming the timeline.
        mon_dal.record_result(
            conn, check_id=check.id, asset_id=check.asset_id,
            status="fail", latency_ms=None, detail="no target configured",
            now=now,
        )
        return _Outcome(status="fail", transitioned=False)

    async with semaphore:
        try:
            result: CheckResult = await impl.run(target=target, **config)
        except Exception as exc:
            result = CheckResult(status="fail", latency_ms=None, detail=str(exc))

    prior_status = check.last_status
    mon_dal.record_result(
        conn,
        check_id=check.id, asset_id=check.asset_id,
        status=result.status, latency_ms=result.latency_ms,
        detail=result.detail, now=now,
    )

    transitioned = False
    became_ok = False
    # First result for a check (prior_status=None) counts as a transition
    # only when it's a failure — we don't spam "came up" entries for every
    # newly-enabled check that happens to be reachable.
    if (prior_status is None and result.status == "fail") or (prior_status == "ok" and result.status == "fail"):
        transitioned = True
        _write_monitor_event(conn, check, result, now, became_ok=False)
    elif prior_status == "fail" and result.status == "ok":
        transitioned = True
        became_ok = True
        _write_monitor_event(conn, check, result, now, became_ok=True)

    if transitioned and notifications_logfile is not None:
        event = MonitorEvent(
            asset_id=check.asset_id,
            asset_hostname=asset.hostname if asset is not None else None,
            asset_ip=target,
            kind="recovery" if became_ok else "failure",
            check_kind=check.kind,
            detail=result.detail,
            occurred_at=now,
        )
        await dispatch_event(event, sinks=sinks, logfile_path=notifications_logfile)

    return _Outcome(status=result.status, transitioned=transitioned)


def _write_monitor_event(
    conn: sqlite3.Connection,
    check: mon_dal.MonitoringCheck,
    result: CheckResult,
    now: datetime,
    *,
    became_ok: bool,
) -> None:
    if became_ok:
        body = f"Monitor {check.kind} recovered (ok)"
    else:
        body = f"Monitor {check.kind} failed"
        if result.detail:
            body += f": {result.detail}"
    tl_dal.append_entry(
        conn,
        asset_id=check.asset_id,
        kind="monitor_event",
        body=body,
        now=now,
        author="monitor",
    )
