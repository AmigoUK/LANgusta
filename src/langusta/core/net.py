"""Small TCP-connect helpers shared between the scanner and the
monitor's `tcp` check kind. Stdlib-only, as ADR-0001 requires for
`core/`. Wave-3 A-009 dedupe.
"""

from __future__ import annotations

import asyncio


async def open_tcp_connection(
    host: str, port: int, *, timeout: float,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Open a TCP connection with a hard deadline. Surfaces OSError /
    TimeoutError exactly like `asyncio.open_connection` + `wait_for` —
    callers decide how to map those to their own result types."""
    return await asyncio.wait_for(
        asyncio.open_connection(host, port), timeout=timeout,
    )
