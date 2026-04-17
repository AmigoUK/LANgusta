"""Architectural boundary lints.

Encodes three ADR decisions as mechanical checks:

  1. `core/` imports only stdlib (ADR-0001: keeps the domain layer unit-
     testable without installing any package).
  2. `sys.platform` branching lives only in `platform/` (ADR-0004: a single
     dispatch point, no sprinkled OS checks across the codebase).
  3. Raw SQL strings live only in `db/` modules (ADR-0001: the DAL is the
     sole owner of SQL; other layers call DAL functions, they don't write SQL).

Run:   uv run python -m scripts.lint_boundaries [SRC_DIR]
Returns exit code 0 if clean, 1 if violations found.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

_STDLIB = frozenset(sys.stdlib_module_names)
_SELF_PACKAGE = "langusta"

# Substrings whose presence in a string literal suggests raw SQL.
_SQL_MARKERS = (
    "SELECT ",
    "INSERT INTO",
    "UPDATE ",
    "DELETE FROM",
    "CREATE TABLE",
    "CREATE INDEX",
    "ALTER TABLE",
    "DROP TABLE",
)


def _python_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]


def _top_level(module: str) -> str:
    return module.split(".")[0]


# ---------------------------------------------------------------------------
# Check 1 — core is stdlib-only
# ---------------------------------------------------------------------------


def check_core_is_stdlib_only(src_root: Path) -> list[str]:
    """Flag any `core/` file importing something outside stdlib and self."""
    core_dir = src_root / "core"
    if not core_dir.is_dir():
        return []

    violations: list[str] = []
    for file in _python_files(core_dir):
        try:
            tree = ast.parse(file.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            violations.append(f"{file}: syntax error: {exc}")
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = _top_level(alias.name)
                    if top not in _STDLIB and top != _SELF_PACKAGE:
                        violations.append(
                            f"{file}: core/ imports non-stdlib module {alias.name!r}"
                        )
            elif isinstance(node, ast.ImportFrom):
                if node.module is None:
                    continue  # `from . import x`
                top = _top_level(node.module)
                if top not in _STDLIB and top != _SELF_PACKAGE:
                    violations.append(
                        f"{file}: core/ imports non-stdlib module {node.module!r}"
                    )
    return violations


# ---------------------------------------------------------------------------
# Check 2 — sys.platform branching lives in platform/ only
# ---------------------------------------------------------------------------


def check_platform_dispatch(src_root: Path) -> list[str]:
    """Flag any `sys.platform` or `platform.system()` reference outside `platform/`."""
    violations: list[str] = []
    for file in _python_files(src_root):
        # Skip files inside platform/ — they're the one sanctioned place.
        try:
            rel = file.relative_to(src_root)
        except ValueError:
            rel = file
        if rel.parts and rel.parts[0] == "platform":
            continue

        text = file.read_text(encoding="utf-8")
        if "sys.platform" in text:
            violations.append(
                f"{file}: reference to sys.platform outside platform/ module"
            )
        if "platform.system()" in text:
            violations.append(
                f"{file}: reference to platform.system() outside platform/ module"
            )
    return violations


# ---------------------------------------------------------------------------
# Check 3 — raw SQL only in db/
# ---------------------------------------------------------------------------


def check_raw_sql_location(src_root: Path) -> list[str]:
    """Flag string literals containing SQL DDL/DML outside `db/`."""
    violations: list[str] = []
    for file in _python_files(src_root):
        try:
            rel = file.relative_to(src_root)
        except ValueError:
            rel = file
        if rel.parts and rel.parts[0] == "db":
            continue
        # Also skip boundary-lint module itself — it names SQL markers as data.
        if file.name == "lint_boundaries.py":
            continue

        try:
            tree = ast.parse(file.read_text(encoding="utf-8"))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                upper = node.value.upper()
                if any(marker in upper for marker in _SQL_MARKERS):
                    violations.append(
                        f"{file}: raw SQL string outside db/ "
                        f"({node.value[:60]!r})"
                    )
    return violations


# ---------------------------------------------------------------------------
# Aggregate + CLI
# ---------------------------------------------------------------------------


def run_all_checks(src_root: Path) -> list[str]:
    return [
        *check_core_is_stdlib_only(src_root),
        *check_platform_dispatch(src_root),
        *check_raw_sql_location(src_root),
    ]


def main() -> int:
    if len(sys.argv) > 1:
        src = Path(sys.argv[1])
    else:
        # Default: <repo>/src/langusta/
        repo = Path(__file__).resolve().parents[1]
        src = repo / "src" / "langusta"

    violations = run_all_checks(src)
    if violations:
        for v in violations:
            print(v, file=sys.stderr)
        print(f"\n{len(violations)} boundary violation(s) found.", file=sys.stderr)
        return 1
    print("All boundary checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
