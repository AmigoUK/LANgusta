"""Check Protocol + CheckResult dataclass.

Every monitor check type implements `run(target, **config) -> CheckResult`.
Checks NEVER raise — timeouts, refused connections, and unexpected errors
all surface as `status='fail'` with the error message in `detail`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class CheckResult:
    status: str  # 'ok' | 'fail'
    latency_ms: float | None
    detail: str | None


@runtime_checkable
class Check(Protocol):
    async def run(self, *, target: str, **config: object) -> CheckResult: ...
