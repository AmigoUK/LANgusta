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
