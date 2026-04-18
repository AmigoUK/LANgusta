"""SNMP-OID monitor check kind.

Polls an arbitrary OID on the target. If an `expected_value` + `comparator`
are configured, the check fails when the comparator evaluates false;
otherwise any non-None response counts as ok.

Follows the existing `Check` protocol contract: on any transport or
protocol error, returns `CheckResult(status='fail', ...)`; never raises.
"""

from __future__ import annotations

import time

from langusta.monitor.checks.base import CheckResult
from langusta.scan.snmp.auth import SnmpV2cAuth, SnmpV3Auth
from langusta.scan.snmp.client import SnmpClient

_DETAIL_MAX = 200


class SnmpOidCheck:
    async def run(self, *, target: str, **config: object) -> CheckResult:
        oid = _required_str(config, "oid")
        snmp_auth = config.get("snmp_auth")
        snmp_client = config.get("snmp_client")
        if not isinstance(snmp_auth, (SnmpV2cAuth, SnmpV3Auth)):
            return CheckResult(status="fail", latency_ms=None, detail="snmp_auth missing")
        if not isinstance(snmp_client, SnmpClient):
            return CheckResult(status="fail", latency_ms=None, detail="snmp_client missing")

        expected_value = _optional_str(config, "expected_value")
        comparator = _optional_str(config, "comparator")
        timeout = float(config.get("timeout_seconds") or 2.0)

        start = time.monotonic()
        try:
            value = await snmp_client.get(target, oid, auth=snmp_auth, timeout=timeout)
        except Exception as exc:  # safety net — the Protocol says never raise
            return CheckResult(status="fail", latency_ms=None, detail=str(exc))
        latency_ms = (time.monotonic() - start) * 1000.0

        if value is None:
            return CheckResult(
                status="fail", latency_ms=None,
                detail="snmp no response / timeout",
            )

        truncated = _truncate(value)
        if comparator is None:
            return CheckResult(status="ok", latency_ms=latency_ms, detail=truncated)

        if expected_value is None:
            return CheckResult(
                status="fail", latency_ms=latency_ms,
                detail="comparator set but expected_value missing",
            )

        matched = _apply_comparator(value, expected_value, comparator)
        if matched:
            return CheckResult(status="ok", latency_ms=latency_ms, detail=truncated)
        return CheckResult(
            status="fail", latency_ms=latency_ms,
            detail=f"value {truncated!r} not {comparator} {expected_value!r}",
        )


def _required_str(config: dict[str, object], key: str) -> str:
    v = config.get(key)
    if not isinstance(v, str) or not v:
        raise ValueError(f"snmp_oid check requires config[{key!r}]")
    return v


def _optional_str(config: dict[str, object], key: str) -> str | None:
    v = config.get(key)
    return v if isinstance(v, str) and v else None


def _truncate(value: str) -> str:
    if len(value) <= _DETAIL_MAX:
        return value
    return value[: _DETAIL_MAX - 1] + "…"


def _apply_comparator(actual: str, expected: str, comparator: str) -> bool:
    if comparator == "eq":
        return actual == expected
    if comparator == "neq":
        return actual != expected
    if comparator == "contains":
        return expected in actual
    if comparator in ("gt", "lt"):
        try:
            a = float(actual)
            e = float(expected)
        except ValueError:
            return False
        return a > e if comparator == "gt" else a < e
    raise ValueError(f"unknown comparator {comparator!r}")
