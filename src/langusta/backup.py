"""SQLite backup via the online backup API.

Spec: docs/specs/02-tech-stack-and-architecture.md §9.

- `write`: snapshot the live DB into a timestamped file under `backups_dir`.
  Dedupes if the most recent snapshot is within the dedupe window.
- `list_backups`: enumerate, newest first.
- `prune`: retain only the most recent N (default 30).
- `verify`: open the backup and run `PRAGMA integrity_check`.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

_PREFIX = "db-"
_SUFFIX = ".sqlite"
_STAMP_FORMAT = "%Y%m%dT%H%M%SZ"


@dataclass(frozen=True, slots=True)
class BackupFile:
    path: Path
    stamp: datetime


def _parse_stamp(name: str) -> datetime | None:
    if not (name.startswith(_PREFIX) and name.endswith(_SUFFIX)):
        return None
    core = name[len(_PREFIX) : -len(_SUFFIX)]
    # Accept `<stamp>` and `<stamp>-<suffix>` forms (pre-migration variant).
    stamp_str = core.split("-", 1)[0]
    try:
        return datetime.strptime(stamp_str, _STAMP_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None


def list_backups(backups_dir: Path) -> list[BackupFile]:
    """Return all snapshots under `backups_dir`, newest first."""
    if not backups_dir.exists():
        return []
    results: list[BackupFile] = []
    for entry in backups_dir.iterdir():
        if not entry.is_file():
            continue
        stamp = _parse_stamp(entry.name)
        if stamp is None:
            continue
        results.append(BackupFile(path=entry, stamp=stamp))
    results.sort(key=lambda b: b.stamp, reverse=True)
    return results


def write(
    src_path: Path,
    backups_dir: Path,
    *,
    now: datetime,
    dedupe_window_hours: float = 1.0,
) -> Path | None:
    """Snapshot `src_path` into `backups_dir`. Returns the new backup path,
    or None if source is missing or the dedupe window suppresses the write.
    """
    if not Path(src_path).exists():
        return None

    if dedupe_window_hours > 0:
        existing = list_backups(backups_dir)
        if existing:
            most_recent = existing[0].stamp
            window = timedelta(hours=dedupe_window_hours)
            if now - most_recent < window:
                return None

    backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime(_STAMP_FORMAT)
    dst = backups_dir / f"{_PREFIX}{stamp}{_SUFFIX}"
    # If a snapshot already exists at this exact stamp (second-resolution),
    # just return the existing one.
    if dst.exists():
        return dst
    # `with sqlite3.connect(...)` only commits; it does not close. Wrap with
    # `closing` so the fds are released when this returns.
    with (
        closing(sqlite3.connect(str(src_path))) as src,
        closing(sqlite3.connect(str(dst))) as out,
    ):
        src.backup(out)
    return dst


def prune(backups_dir: Path, *, keep: int = 30) -> int:
    """Delete all but the newest `keep` snapshots. Returns count deleted."""
    import contextlib
    snapshots = list_backups(backups_dir)
    to_drop = snapshots[keep:]
    for b in to_drop:
        with contextlib.suppress(FileNotFoundError):
            b.path.unlink()
    return len(to_drop)


def verify(path: Path) -> bool:
    """PRAGMA integrity_check against a backup file. Returns True iff 'ok'."""
    if not Path(path).exists():
        return False
    try:
        with sqlite3.connect(str(path)) as conn:
            row = conn.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.DatabaseError:
        return False
    if row is None:
        return False
    return str(row[0]).lower() == "ok"
