"""Runner concurrency — semaphore caps in-flight checks at max_concurrency."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from langusta.db import assets as assets_dal
from langusta.db import monitoring as mon_dal
from langusta.db.connection import connect
from langusta.db.migrate import migrate
from langusta.monitor.checks.base import CheckResult
from langusta.monitor.runner import run_once

NOW = datetime(2026, 4, 18, 12, 0, 0, tzinfo=UTC)


class _SlowCheck:
    """Checks that record peak in-flight count and then sleep briefly."""

    def __init__(self) -> None:
        self.in_flight = 0
        self.peak = 0
        self._lock = asyncio.Lock()

    async def run(self, *, target: str, **config: object) -> CheckResult:
        async with self._lock:
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
        try:
            await asyncio.sleep(0.05)
        finally:
            async with self._lock:
                self.in_flight -= 1
        return CheckResult(status="ok", latency_ms=50.0, detail=None)


@pytest.mark.asyncio
async def test_semaphore_caps_in_flight_checks(tmp_path: Path) -> None:
    db = tmp_path / "mon.sqlite"
    migrate(db)
    slow = _SlowCheck()
    with connect(db) as conn:
        # 20 icmp checks due at once.
        for i in range(20):
            aid = assets_dal.insert_manual(
                conn, hostname=f"h{i}", primary_ip=f"10.0.0.{i + 1}", now=NOW,
            )
            mon_dal.enable_check(
                conn, asset_id=aid, kind="icmp", interval_seconds=60, now=NOW,
            )
        await run_once(
            conn, now=NOW + timedelta(seconds=1),
            check_registry={"icmp": slow},
            max_concurrency=4,
        )
    assert slow.peak <= 4, f"expected peak<=4, got {slow.peak}"


# ---------------------------------------------------------------------------
# Wave-3 TEST-C-011 — partial check failure still writes heartbeat
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_once_writes_heartbeat_even_when_a_check_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If a single check's downstream bookkeeping raises (for instance
    `record_result` hits a transient DB error), the rest of the cycle
    must still complete and the heartbeat must be updated -- otherwise
    a single bad row wedges the operator's "is the daemon alive?"
    signal indefinitely. Wave-3 finding C-011."""
    from langusta.db import monitoring as mon_dal_mod
    from langusta.monitor.checks.base import CheckResult

    db = tmp_path / "mon.sqlite"
    migrate(db)

    class _OkCheck:
        async def run(self, *, target: str, **_: object) -> CheckResult:
            return CheckResult(status="ok", latency_ms=1.0, detail=None)

    with connect(db) as conn:
        aid1 = assets_dal.insert_manual(
            conn, hostname="a", primary_ip="10.0.0.1", now=NOW,
        )
        aid2 = assets_dal.insert_manual(
            conn, hostname="b", primary_ip="10.0.0.2", now=NOW,
        )
        cid1 = mon_dal.enable_check(
            conn, asset_id=aid1, kind="icmp", interval_seconds=60, now=NOW,
        )
        cid2 = mon_dal.enable_check(
            conn, asset_id=aid2, kind="icmp", interval_seconds=60, now=NOW,
        )

        real_record = mon_dal_mod.record_result

        def flaky_record(
            conn, *, check_id, asset_id, status, latency_ms, detail, now,
        ):
            if check_id == cid2:
                raise RuntimeError("simulated DB transient")
            return real_record(
                conn,
                check_id=check_id, asset_id=asset_id,
                status=status, latency_ms=latency_ms,
                detail=detail, now=now,
            )

        monkeypatch.setattr(
            "langusta.monitor.runner.mon_dal.record_result",
            flaky_record,
        )

        await run_once(
            conn,
            now=NOW + timedelta(seconds=1),
            check_registry={"icmp": _OkCheck()},
            max_concurrency=4,
        )

        hb = mon_dal.get_heartbeat(conn)

    assert hb is not None, (
        "heartbeat was lost when a single check's record_result raised "
        "— the operator's liveness signal is now wedged until something "
        "else writes it"
    )
    # The unaffected check still wrote a result.
    with connect(db) as conn:
        rows = conn.execute(
            "SELECT check_id FROM check_results"
        ).fetchall()
    written = {int(r["check_id"]) for r in rows}
    assert cid1 in written, (
        "the good check's result was lost because a sibling raised"
    )
