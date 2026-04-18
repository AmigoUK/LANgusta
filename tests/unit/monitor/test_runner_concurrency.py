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
