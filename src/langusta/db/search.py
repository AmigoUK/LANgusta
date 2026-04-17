"""Universal search over asset fields + MACs.

Uses the FTS5 virtual table `assets_fts` (kept in sync by triggers) for
text fields, and a direct LIKE on `mac_addresses` for MAC fragments.
Results are returned as full `Asset` objects ready for the TUI.

Spec: docs/specs/01-functionality-and-moscow.md §2 "incident mode" — the
front-door interaction, must be fast and fuzzy.
"""

from __future__ import annotations

import re
import sqlite3

from langusta.core.models import Asset
from langusta.db import assets as assets_dal

# Characters with special meaning to FTS5: strip to avoid syntax errors and
# deliberately avoid letting users run arbitrary MATCH queries.
_FTS_UNSAFE = re.compile(r'[\"\'\-()*:]')


def _fts_query(query: str) -> str:
    """Return an FTS5 MATCH query string that does prefix-match on each term."""
    cleaned = _FTS_UNSAFE.sub(" ", query).strip()
    terms = [t for t in cleaned.split() if t]
    if not terms:
        return ""
    # Each term gets wrapped in double quotes (treating as literal) and
    # suffixed with * for prefix search.
    return " ".join(f'"{t}"*' for t in terms)


def search(
    conn: sqlite3.Connection,
    query: str,
    *,
    limit: int = 20,
) -> list[Asset]:
    """Search asset text fields and MACs. Returns up to `limit` Asset records.

    Matching modes:
      - Text fields (hostname, IP string, description, vendor, location,
        owner, detected_os, device_type): FTS5 prefix match on every
        whitespace-separated term.
      - MACs: case-insensitive substring match.

    Results are union'd and de-duplicated; ordering is FTS5 rank first,
    then MAC hits not already present.
    """
    if not query.strip():
        return []

    fts_q = _fts_query(query)
    ordered_ids: list[int] = []
    seen: set[int] = set()

    # 1. FTS5 hits ordered by rank (bm25 by default).
    if fts_q:
        try:
            rows = conn.execute(
                "SELECT rowid FROM assets_fts WHERE assets_fts MATCH ? "
                "ORDER BY rank LIMIT ?",
                (fts_q, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        for r in rows:
            aid = int(r["rowid"])
            if aid not in seen:
                ordered_ids.append(aid)
                seen.add(aid)

    # 2. MAC substring hits.
    if len(ordered_ids) < limit:
        mac_like = f"%{query.strip().lower()}%"
        rows = conn.execute(
            "SELECT DISTINCT asset_id FROM mac_addresses "
            "WHERE mac LIKE ? LIMIT ?",
            (mac_like, limit),
        ).fetchall()
        for r in rows:
            aid = int(r["asset_id"])
            if aid not in seen:
                ordered_ids.append(aid)
                seen.add(aid)
            if len(ordered_ids) >= limit:
                break

    return [a for a in (assets_dal.get_by_id(conn, aid) for aid in ordered_ids) if a is not None]
