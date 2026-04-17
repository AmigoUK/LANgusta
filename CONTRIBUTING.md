# Contributing to LANgusta

Thanks for helping. LANgusta is pre-alpha and the v1 scope is fixed in [`docs/specs/`](docs/specs/) and [`docs/adr/`](docs/adr/). Please read the development plan at [`docs/development-plan.md`](docs/development-plan.md) before opening a significant PR.

## Development setup

Prerequisites: Linux or macOS, Python 3.12+, [`uv`](https://github.com/astral-sh/uv).

```bash
git clone git@github.com:AmigoUK/LANgusta.git
cd LANgusta
uv sync --all-extras

# Run the test suite
uv run pytest

# Lint (ruff + architectural boundaries)
uv run ruff check src tests scripts
uv run python -m scripts.lint_boundaries

# Smoke the CLI
uv run langusta --version
LANGUSTA_HOME=$(mktemp -d) uv run langusta init
```

## Test-first discipline

Per the development plan, every production change lands test-first. That means:

1. Write (or extend) a failing test that describes the desired behaviour.
2. Watch it fail for the expected reason. If it passes immediately, it isn't testing what you think.
3. Write the minimum production code to make it pass.
4. Refactor.

No exceptions without a maintainer's sign-off.

## Architectural boundaries

`scripts/lint_boundaries.py` enforces three rules from the ADRs:

| Rule | Source |
|---|---|
| `src/langusta/core/` imports only stdlib (plus `langusta.core.*`) | [ADR-0001](docs/adr/0001-data-layer-orm-choice.md) |
| `sys.platform` / `platform.system()` appears only inside `src/langusta/platform/` | [ADR-0004](docs/adr/0004-platform-support.md) |
| Raw SQL string literals live only inside `src/langusta/db/` | [ADR-0001](docs/adr/0001-data-layer-orm-choice.md) |

Violations fail CI. If you genuinely need to cross a boundary, open an ADR revisiting the relevant decision first — don't bolt on a `# noqa`.

## Commit / PR discipline

- Small, focused commits per logical change. Conventional commit subjects (`docs:`, `feat:`, `fix:`, `test:`, `chore:`).
- Every PR must pass `ruff check`, `python -m scripts.lint_boundaries`, and `pytest` on Linux and macOS (CI enforces).
- Every PR touching `src/langusta/db/migrations/` must add a new numbered file — never edit a shipped migration. Checksums are verified at runtime ([ADR-0005](docs/adr/0005-schema-migration-discipline.md)).

## Platform support

| Platform | Status |
|---|---|
| Linux (x86_64, arm64) | First-class, tested in CI |
| macOS (arm64, x86_64) | First-class, tested in CI |
| Windows native | **Not supported in v1.** Please use WSL2. Issues tagged `platform: windows-native` are `wontfix` for the v1 cycle. |

See [ADR-0004](docs/adr/0004-platform-support.md) for the rationale and revisit trigger.

## Reporting bugs

- File at <https://github.com/AmigoUK/LANgusta/issues> with the exact `langusta` version (`langusta --version`), OS, and minimal reproduction steps.
- Do not include credentials, SNMP community strings, or contents of `~/.langusta/db.sqlite` — we can't help you any faster with them and they're sensitive.
