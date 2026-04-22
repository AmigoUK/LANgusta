"""Tests for scripts/lint_boundaries.py.

Boundary lints encode architectural decisions from the ADRs as mechanical CI
checks. ADR-0001 (core is stdlib-only), ADR-0004 (platform branches only in
platform/), ADR-0001 (raw SQL only in db/).

Each check function is tested independently with fabricated source trees so
the tests stay fast and deterministic.
"""

from __future__ import annotations

from pathlib import Path

from scripts.lint_boundaries import (
    check_core_is_stdlib_only,
    check_platform_dispatch,
    check_raw_sql_location,
    run_all_checks,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


# ---------------------------------------------------------------------------
# check_core_is_stdlib_only
# ---------------------------------------------------------------------------


def test_core_stdlib_only_passes_on_stdlib_imports(tmp_path: Path) -> None:
    _write(tmp_path / "core" / "foo.py", "import datetime\nfrom pathlib import Path\n")
    assert check_core_is_stdlib_only(tmp_path) == []


def test_core_stdlib_only_flags_third_party_imports(tmp_path: Path) -> None:
    _write(tmp_path / "core" / "bad.py", "import textual\n")
    violations = check_core_is_stdlib_only(tmp_path)
    assert any("textual" in v for v in violations)
    assert any("bad.py" in v for v in violations)


def test_core_stdlib_only_flags_from_imports(tmp_path: Path) -> None:
    _write(tmp_path / "core" / "bad.py", "from typer import Option\n")
    violations = check_core_is_stdlib_only(tmp_path)
    assert violations


def test_core_stdlib_only_allows_langusta_core_internal_imports(tmp_path: Path) -> None:
    """`from langusta.core.foo import x` inside another core module is fine."""
    _write(tmp_path / "core" / "a.py", "from langusta.core.b import thing\n")
    _write(tmp_path / "core" / "b.py", "thing = 1\n")
    assert check_core_is_stdlib_only(tmp_path) == []


# ---------------------------------------------------------------------------
# check_platform_dispatch
# ---------------------------------------------------------------------------


def test_platform_dispatch_passes_when_sys_platform_only_in_platform_module(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path / "platform" / "__init__.py",
        "import sys\nif sys.platform.startswith('linux'): pass\n",
    )
    _write(tmp_path / "scan" / "icmp.py", "import asyncio\n")
    assert check_platform_dispatch(tmp_path) == []


def test_platform_dispatch_flags_sys_platform_outside_platform_module(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path / "scan" / "icmp.py",
        "import sys\nif sys.platform == 'linux':\n    pass\n",
    )
    violations = check_platform_dispatch(tmp_path)
    assert any("icmp.py" in v for v in violations)


# ---------------------------------------------------------------------------
# check_raw_sql_location
# ---------------------------------------------------------------------------


def test_raw_sql_passes_when_sql_only_in_db_module(tmp_path: Path) -> None:
    _write(tmp_path / "db" / "assets.py", 'conn.execute("SELECT * FROM assets")\n')
    _write(tmp_path / "tui" / "screen.py", "x = 1\n")
    assert check_raw_sql_location(tmp_path) == []


def test_raw_sql_flags_select_outside_db(tmp_path: Path) -> None:
    _write(tmp_path / "scan" / "bad.py", 'x = "SELECT * FROM assets"\n')
    violations = check_raw_sql_location(tmp_path)
    assert any("bad.py" in v for v in violations)


def test_raw_sql_flags_create_table_outside_db(tmp_path: Path) -> None:
    _write(tmp_path / "monitor" / "bad.py", '"CREATE TABLE foo (id INTEGER)"\n')
    violations = check_raw_sql_location(tmp_path)
    assert violations


def test_raw_sql_allows_sql_strings_in_db_module(tmp_path: Path) -> None:
    _write(tmp_path / "db" / "queries.py", '"INSERT INTO assets VALUES (?)"\n')
    _write(tmp_path / "db" / "migrations" / "001.sql", "CREATE TABLE x;")
    assert check_raw_sql_location(tmp_path) == []


# ---------------------------------------------------------------------------
# run_all_checks — against the actual codebase
# ---------------------------------------------------------------------------


def test_current_codebase_passes_all_boundary_checks() -> None:
    """The shipped code must pass every boundary check. If this test fails,
    either the code drifted OR the lint regressed; investigate both."""
    repo_root = Path(__file__).resolve().parents[2]
    src = repo_root / "src" / "langusta"
    assert src.is_dir(), f"expected langusta source at {src}"
    violations = run_all_checks(src)
    assert violations == [], "\n".join(violations)


def test_run_all_checks_aggregates_violations_from_every_check(tmp_path: Path) -> None:
    _write(tmp_path / "core" / "a.py", "import textual\n")
    _write(tmp_path / "scan" / "b.py", "import sys\nif sys.platform == 'linux': pass\n")
    _write(tmp_path / "scan" / "c.py", '"SELECT * FROM t"\n')
    violations = run_all_checks(tmp_path)
    # Each check should surface at least one violation in the aggregate list.
    assert any("textual" in v for v in violations)
    assert any("sys.platform" in v for v in violations)
    assert any("SELECT" in v or "raw SQL" in v.lower() for v in violations)


# ---------------------------------------------------------------------------
# Wave-3 TEST-A-001 — per-file size threshold
# ---------------------------------------------------------------------------

# Files we knowingly grandfather over the 600-LOC threshold. Each entry is
# a commitment to shrink the file (see Wave-2 finding A-001 against
# cli.py). Adding a new entry should come with an explicit rationale.
_OVERSIZED_ALLOWLIST: frozenset[str] = frozenset({
    "src/langusta/cli.py",
})

_MAX_LOC = 600


def test_no_source_file_exceeds_600_loc_without_allowlist() -> None:
    """Keeps module files small enough to read in one sitting. Anything
    over `_MAX_LOC` lines is a signal to split — either because we've
    accumulated multiple responsibilities or because a single flow grew
    unchecked. Enforced at test time; not a ruff rule because ruff
    doesn't have a LOC budget."""
    repo_root = Path(__file__).resolve().parents[2]
    src = repo_root / "src" / "langusta"
    assert src.is_dir(), f"expected langusta source at {src}"

    offenders: list[tuple[str, int]] = []
    for file in sorted(src.rglob("*.py")):
        loc = sum(1 for _ in file.read_text(encoding="utf-8").splitlines())
        rel = str(file.relative_to(repo_root))
        if loc > _MAX_LOC and rel not in _OVERSIZED_ALLOWLIST:
            offenders.append((rel, loc))

    assert not offenders, (
        f"source files exceed {_MAX_LOC} LOC without an allowlist entry: "
        f"{offenders}; either split the file or add an entry to "
        f"_OVERSIZED_ALLOWLIST with a rationale"
    )


def test_oversized_allowlist_is_not_stale() -> None:
    """Every entry in `_OVERSIZED_ALLOWLIST` must still point at a file
    that genuinely exceeds the threshold. Remove entries when the file
    drops under the budget — otherwise the allowlist loses signal."""
    repo_root = Path(__file__).resolve().parents[2]
    stale: list[str] = []
    for rel in _OVERSIZED_ALLOWLIST:
        p = repo_root / rel
        if not p.is_file():
            stale.append(f"{rel} (does not exist)")
            continue
        loc = sum(1 for _ in p.read_text(encoding="utf-8").splitlines())
        if loc <= _MAX_LOC:
            stale.append(f"{rel} ({loc} LOC — under budget, drop from list)")
    assert not stale, (
        "_OVERSIZED_ALLOWLIST entries are stale (remove them): "
        f"{stale}"
    )
