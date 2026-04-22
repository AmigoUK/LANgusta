"""Hypothesis property: every recovery `monitor_event` is preceded by
a `failure` event for the same check.

Wave-3 TEST-T-024 (single-lens test-gap, medium). The runner emits a
`recovered` timeline entry only when the prior status was `fail`; the
inverse claim — that every recovery has a matching earlier failure —
is what operators rely on when scanning an asset's timeline. Fuzzing
random status sequences catches any regression in that transition
logic.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from langusta.db import assets as assets_dal
from langusta.db import monitoring as mon_dal
from langusta.db.connection import connect
from langusta.db.migrate import migrate
from langusta.monitor.checks.base import CheckResult
from langusta.monitor.runner import run_once

_BASE = datetime(2026, 4, 20, 9, 0, 0, tzinfo=UTC)


class _ScriptedCheck:
    """Returns a pre-scripted ok/fail sequence, one per `run()` call."""

    def __init__(self, scripted: list[str]) -> None:
        self._queue = list(scripted)

    async def run(self, *, target: str, **_: object) -> CheckResult:
        status = self._queue.pop(0)
        return CheckResult(status=status, latency_ms=1.0, detail=None)


@settings(max_examples=10, deadline=None)
@given(
    statuses=st.lists(
        st.sampled_from(["ok", "fail"]), min_size=0, max_size=10,
    ),
)
def test_every_recovery_event_has_a_prior_failure_event(
    tmp_path_factory, statuses: list[str],
) -> None:
    """For any status sequence, each recovery timeline entry must be
    preceded by at least one failure entry for the same check."""
    db = tmp_path_factory.mktemp("rinv") / "db.sqlite"
    migrate(db)

    with connect(db) as conn:
        aid = assets_dal.insert_manual(
            conn, hostname="r", primary_ip="10.0.0.1", now=_BASE,
        )
        cid = mon_dal.enable_check(
            conn, asset_id=aid, kind="icmp", interval_seconds=60, now=_BASE,
        )

    scripted = _ScriptedCheck(statuses)

    with connect(db) as conn:
        import asyncio

        for i, _ in enumerate(statuses):
            # +90s per cycle so every check is due (interval=60s).
            now = _BASE + timedelta(seconds=90 * (i + 1))
            asyncio.run(_run_one_cycle(conn, now, scripted))

    with connect(db) as conn:
        rows = conn.execute(
            "SELECT body FROM timeline_entries "
            "WHERE asset_id = ? AND kind = 'monitor_event' ORDER BY id",
            (aid,),
        ).fetchall()

    seen_failure = False
    for row in rows:
        body = (row["body"] or "").lower()
        if "failed" in body:
            seen_failure = True
        if "recovered" in body:
            assert seen_failure, (
                f"recovery event with no prior failure for check {cid}: "
                f"body={body!r}"
            )


async def _run_one_cycle(conn, now, check_impl) -> None:
    await run_once(
        conn,
        now=now,
        check_registry={"icmp": check_impl},
        max_concurrency=1,
    )
