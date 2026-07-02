# AGENTS.md

Guidance for AI agents working in the LANgusta codebase. Read this before making changes.

## What this is

LANgusta is a local-first, single-binary **asset registry + network scanner + lightweight monitoring** tool for small networks (≤250 devices). Python 3.12+, packaged with `uv`, AGPL-3.0. The product's differentiator is *institutional memory*: every asset carries an append-only timeline of scan changes, manual notes, and monitoring events.

## Essential commands

```bash
uv sync --all-extras                                  # install all deps (incl. dev)
uv run pytest                                         # full test suite
uv run pytest tests/unit                              # one tier (also: integration, property, snapshots)
uv run pytest -m "not slow"                           # skip slow/integration markers
uv run pytest --snapshot-update                       # regenerate Textual TUI snapshots (see Testing)
uv run ruff check src tests scripts                   # lint
uv run python -m scripts.lint_boundaries              # architectural boundary lint (CI-enforced, see below)
uv run langusta --version                             # smoke
LANGUSTA_HOME=$(mktemp -d) uv run langusta init       # init into a throwaway home
```

CI (`.github/workflows/ci.yml`) runs on **ubuntu-latest AND macos-latest** and executes, in order: `ruff check` → `lint_boundaries` → `pytest -v --tb=short` → `--version` smoke → end-to-end CLI walkthrough. All must pass on both OSes.

## The three invariants — do not break these

These are the product's core promises, each guarded by tests. Any change touching these paths must keep them green.

| Invariant | Where enforced | How it's tested |
|---|---|---|
| **Immutable timeline** — entries are append-only; corrections are *new* entries referencing the original. | SQL triggers on `timeline_entries` (`db/migrations/001`). Insert-only DAL functions in `db/timeline.py`. | `tests/unit/db/test_timeline_immutability.py` |
| **Scanner proposes, human disposes** — a scan observation that conflicts with a `manual`- or `imported`-provenance field goes to the review queue, never silently overwrites. | `core/provenance.merge_scan_result()` (the single authority) + `db/writer.apply_scan_observation` (the single write path). | `tests/unit/core/test_provenance.py` (Hypothesis) + `tests/property/test_writer_idempotency.py` |
| **No data loss across upgrade** — `uv tool upgrade` never requires "delete your db". | Forward-only, checksum-protected migrations + mandatory pre-migration backup. | `tests/property/test_migration_checksum.py`, `tests/unit/db/test_migrate.py`, `tests/property/test_export_roundtrip.py` |

## Architectural boundaries (CI-enforced — violations fail the build)

`scripts/lint_boundaries.py` encodes three ADRs as mechanical AST checks. There is **no `# noqa` escape** — crossing a boundary requires a new ADR first.

1. **`src/langusta/core/` imports only stdlib** (plus `langusta.core.*`). This keeps the domain layer unit-testable with zero deps. (ADR-0001)
2. **`sys.platform` / `platform.system()` appear only inside `src/langusta/platform/`.** All OS-specific behaviour routes through `platform.get_backend()` / the `PlatformBackend` abstraction. Never sprinkle OS checks elsewhere. (ADR-0004)
3. **Raw SQL string literals live only inside `src/langusta/db/`.** Other layers call DAL functions; they never write SQL. (ADR-0001)

## Architecture & control flow

Layered, with a strict dependency direction:

```
core/        # stdlib-only domain: dataclasses (Asset), provenance, identity resolution, net utils
  ↑
db/          # data-access layer (DAL): raw sqlite3 + SQL. One module per aggregate
  ↑          #   (assets.py, timeline.py, scans.py, credentials.py, monitoring.py, ...)
cli.py       # Typer entry point (src/langusta/cli.py — one big module, subcommands grow per milestone)
tui/         # Textual TUI (app.py root; screens/ + widgets/)
scan/        # network scanner: orchestrator.py ties ICMP+ARP+rDNS+TCP+mDNS+SNMP together
monitor/     # monitoring: runner.py (single cycle), checks/ (icmp/tcp/http/snmp_oid/ssh_command), ssh/
platform/    # the ONLY place OS dispatch lives (linux/macos/windows backends)
crypto/      # AES-256-GCM vault + Argon2id KDF + master-password flow
```

Key integration points an agent must know:

- **`db/connection.connect()`** is the *only* sanctioned way to open a SQLite connection. It applies WAL + `synchronous=NORMAL` + `foreign_keys=ON` + `busy_timeout=5000` + `temp_store=MEMORY` centrally. In write mode it commits on context-exit and rolls back on exception; `readonly=True` sets `PRAGMA query_only=1`. Never open `sqlite3.connect()` directly elsewhere.
- **`db/writer.apply_scan_observation()`** is the single write path for every scan observation (orchestrator, SNMP, monitor events all route through it). It composes identity resolution (`core.identity.resolve`) + provenance merge (`core.provenance.merge_scan_result`) + asset upsert + MAC binding + timeline + proposed_changes into one transaction.
- **`paths.py`** is the single source of truth for filesystem locations. Honour `LANGUSTA_HOME` (must be absolute) instead of hardcoding `~/.langusta`.
- **Two-process model** (ADR-0002): the TUI is one process; `langusta monitor` is a separately-supervised daemon (systemd/launchd, never self-daemonised). The scanner runs in-TUI via Textual `@work`. **SQLite (WAL) is the sole IPC channel** — there is no in-memory/shared-state IPC between them.
- **Identity resolution never auto-merges.** If a MAC points at asset A and a hostname at asset B, `core.identity.resolve` returns `Ambiguous` → review queue. This is deliberate (the documented Lansweeper failure mode was auto-merge).

## Schema migrations — immutable, forward-only

Migrations live in `src/langusta/db/migrations/NNN_description.sql` and are applied by `db/migrate.py` (driving `PRAGMA user_version`).

- **Never edit a shipped migration file.** Each one's SHA-256 checksum is recorded in `_migrations` at apply time; the runner refuses to start if an on-disk file's checksum differs. Correct mistakes with a *new* numbered file.
- **There is a permanent gap at `004`** (id burned during development). Don't reuse it; new migrations count up from the current max. The runner tolerates gaps.
- Every migration that touches a DB with user data triggers an **automatic pre-migration backup** (SQLite online-backup API → `~/.langusta/backups/`). Restore-from-old-backup-into-newer-binary is a tested contract.
- Adding a new migration = add the `.sql` file; no separate schema-version constant to bump.

## Testing

### Tiers

`tests/` is split into `unit/`, `integration/`, `property/`, and `snapshots/` (TUI). pytest config is in `pyproject.toml`: `asyncio_mode = "auto"` (so async tests need **no** `@pytest.mark.asyncio`), `testpaths = ["tests"]`, `pythonpath = ["src"]`.

### Critical fixtures & conventions

- **`conftest.py::_offline_scan_enrichments`** is an `autouse=True` fixture. By default it monkeypatches rDNS, TCP-probe, and mDNS enrichment to return empty — **every test is offline by default.** Tests that exercise the scanner inject their own `ping_fn`/fake backends rather than un-patching these. If a scanner test behaves as if enrichment found nothing, this is why.
- **`tmp_langusta_home`** redirects `~/.langusta` to a per-test tmp dir (via `$HOME`). Always use it for anything that touches the DB/backups/config so nothing leaks to the real home.
- Integration tests drive the CLI via `typer.testing.CliRunner`, redirecting `$HOME` (e.g. `runner.invoke(app, [...], env={"HOME": str(home)})`).
- **Hypothesis** guards the invariants. Shared strategies (MAC/IPv4/hostname generators) live in `tests/strategies.py` — reuse them. Property tests use `@settings(max_examples=20, deadline=None)`.

### TUI snapshot tests

`tests/snapshots/` uses `pytest-textual-snapshot`. Each screen has a `_*_app.py` harness script that instantiates a minimal seeded app; `snap_compare(script, terminal_size=(100,24))` diffs against a committed `.raw`/`.svg` under `__snapshots__/`. **When the UI intentionally changes, run `uv run pytest tests/snapshots --snapshot-update` and commit the regenerated artefacts.** The `_*_app.py` harness scripts are auto-rewritten each run and are excluded from ruff (`extend-exclude` in `pyproject.toml`).

### Warnings are errors

`filterwarnings = ["error", ...]` means an unhandled `DeprecationWarning`/`ResourceWarning` fails the suite. Targeted ignores exist for `apscheduler`, `pysnmp`, and socket/`ResourceWarning` leaks from those libs in the test runner. If you add a dep that emits warnings, expect to add a scoped ignore.

### Secrets hygiene

`tests/integration/test_secret_hygiene.py` enforces that credentials never appear in CLI output, logs, raw DB bytes, or help text. **Never log a credential, community string, or master password.** When new code handles secrets, extend these checks. Credential decryption in the monitor runner is cached per `credential_id` per cycle — don't decrypt redundantly.

## Credentials & crypto

- AES-256-GCM; key derived from the master password via **Argon2id** (~500ms on prod params). Master password minimum **12 chars**.
- `crypto/vault.Vault.unlock(password, salt, params)` is the production constructor; **`Vault.for_tests(...)`** uses fast KDF params — always use it in tests to avoid ~500ms × N slowdowns.
- Credentials are encrypted at rest and decrypted only at use. File modes: `db.sqlite` → `0600`, `~/.langusta/` and `backups/` → `0700` (enforced by `platform` backends on init).

## Environment variables

| Variable | Purpose |
|---|---|
| `LANGUSTA_HOME` | Override `~/.langusta` (must be absolute). Set this in tests and CI smoke runs. |
| `LANGUSTA_MASTER_PASSWORD` | Non-interactive master password (CI/scripts). |
| `LANGUSTA_CRED_SECRET` | Secret for `cred add --kind snmp_v2c` (community string). |
| `LANGUSTA_CRED_V3_*` | The five SNMPv3 fields (`_USER`, `_AUTH_PROTO`, `_AUTH_PASS`, `_PRIV_PROTO`, `_PRIV_PASS`). |
| `LANGUSTA_NETBOX_TOKEN` | NetBox API token for `import netbox`. |
| `LANGUSTA_SMTP_USERNAME` / `LANGUSTA_SMTP_PASSWORD` | SMTP notification auth. |
| `LANGUSTA_KEYBINDINGS` | `=vim` enables vim navigation aliases (j/k/g/G/ctrl+d/ctrl+u) in the TUI. |

**Gotcha:** when `cli.py` spawns the monitor daemon subprocess, it explicitly strips `LANGUSTA_MASTER_PASSWORD` from the inherited env (the daemon reads its own). Preserve this if touching process spawning.

## Conventions

- **Test-first discipline is mandatory** (per `CONTRIBUTING.md` and the development plan): write a failing test describing the desired behaviour, watch it fail for the right reason, implement the minimum to pass, then refactor.
- **Conventional commit subjects** (`feat:`, `fix:`, `docs:`, `test:`, `chore:`). Small, focused commits per logical change.
- **`from __future__ import annotations`** at the top of every module (the codebase targets 3.12 but uses this consistently).
- **Frozen, slotted dataclasses** for value/domain types (`Asset`, `FieldValue`, `Observation`, `Outcome` variants). Follow this for new types.
- **One DAL module per aggregate** (`db/assets.py`, `db/timeline.py`, …) — don't let `db/*.py` sprawl into a grab-bag (ADR-0001 negative consequence, enforced in review).
- **MAC addresses are normalised lowercase** via `core/models.normalize_mac()`. Every persistence/lookup site routes through it; preserve this for any new MAC-handling code.
- **Module docstrings reference spec sections and ADRs** (e.g. `Spec: docs/specs/02-... §7`, `ADR: docs/adr/0002-...`). When adding a module that implements an ADR decision, cite it.
- **`monitor/runner.DEFAULT_REGISTRY`** is a `MappingProxyType` (read-only). Tests swap the whole module attribute via `monkeypatch`; never mutate it in place.
- ruff config (`pyproject.toml`): line-length 100, `E501` ignored (format handles wrapping), `tests/**` allow `N802`/`N803` so test names can mirror public API. Lint scope is `src tests scripts`.

## Platform support

Linux (x86_64, arm64) and macOS are first-class and CI-tested. **Native Windows is not supported in v1** — run under WSL2. Issues tagged `platform: windows-native` are `wontfix` for v1 (ADR-0004). Do not add Windows-specific code paths outside `platform/windows.py`.

## Where to look for context

- `docs/specs/01-functionality-and-moscow.md`, `02-tech-stack-and-architecture.md` — the fixed v1 scope.
- `docs/adr/` — Architecture Decision Records (0001 data layer, 0002 process model, 0003 SNMP lib, 0004 platform, 0005 migrations, 0006 monitor PID/daemonisation). Read the relevant ADR before changing a system it decided.
- `docs/development-plan.md` — milestone plan (M0–M8); CLI subcommands grew per milestone.
- `docs/daemon.md` — systemd/launchd wiring for the monitor.
- `CONTRIBUTING.md` — the canonical dev-setup + boundary-lint reference.
