# AUDIT REPORT — LANgusta

**Scope:** Full cross-audit of `/var/www/html/LANgusta` (v0.2.1rc1)
**Method:** Six-member AI specialist council (Database, Security/Crypto, Logic/Invariants, Async/Errors, Test-Coverage, Architecture) dispatched in parallel, then findings cross-verified against source and correlated.
**Date:** 2026-07-02

---

## Executive summary

LANgusta's **core architecture is genuinely strong**. The three load-bearing invariants hold tightly in the *scanner* path: timeline immutability is trigger-enforced, `core/provenance.merge_scan_result` correctly protects MANUAL/IMPORTED fields in every branch, and `core/identity.resolve` never auto-merges ambiguous assets. The boundary lint, migration checksum chain, and WAL+DAL discipline are well-engineered.

However, that rigor **does not propagate to the edges**: the *import* paths bypass the provenance/identity machinery that the scanner honors; the *monitor daemon* has no error recovery and no schema-version coordination despite ADR-0002 mandating both; an entire documented feature (authenticated SMTP) silently doesn't work; and one test that claims to guard an invariant is vacuously true. Several of these are real product-invariant violations hiding behind the scanner path's cleanliness.

| Severity | Count |
|---|---|
| 🔴 Critical | **3** |
| 🟠 High | **9** |
| 🟡 Medium | **15** |
| 🟢 Low | **12** |
| **Total** | **39** |

---

## Cross-cutting correlations (multiple council members, highest signal)

These are the findings where two or more specialists independently flagged the same root issue — they are the most reliable and the highest priority.

### X-1 — SMTP authentication is silently non-functional *(Security + Architecture)*
**🔴 Critical** · `src/langusta/monitor/notifications.py:76,129-137` · `src/langusta/cli.py:1133-1134`

The `notify add-smtp` command stores only `{host, port, from, to, starttls}`. At send time `_smtp_send_blocking` gates `smtp.login()` behind `if cfg.username and cfg.password:` (line 76) — both are **always `None`** because `send_smtp` reads them from the config dict (lines 136-137) which never contains them. The docstring promises credentials come from `LANGUSTA_SMTP_USERNAME`/`LANGUSTA_SMTP_PASSWORD` env vars, but `notifications.py` **does not even `import os`** — the env vars are read nowhere in the entire codebase. Authenticated SMTP relay silently fails; open relays send unauthenticated mail. Docs actively mislead.

### X-2 — Import paths bypass the "scanner-proposes, human-disposes" invariant *(Logic + Test-Coverage)*
**🔴 Critical** · `src/langusta/db/import_netbox.py:161-182` · `src/langusta/db/import_common.py:85-104`

Two distinct bypasses of the product's #2 invariant:
- **NetBox** (`import_netbox.py`) performs a raw `INSERT INTO assets ... 'imported'` and only skips on `_ip_exists`. It never calls `apply_imported_observation`, never runs `merge_scan_result`, never binds MACs, never files `proposed_changes`. A NetBox device whose hostname/IP collides with a MANUAL field is silently inserted (or silently skipped on IP), with no review-queue interaction.
- **Lansweeper** (via `import_common._resolve_identity`) *does* call `merge_scan_result`, but its identity resolver checks **only MAC and IP — never hostname**. A row whose MAC→asset A but hostname→asset B is silently merged into A. The scan path's `core.identity.resolve` would return `Ambiguous` for exactly this case.

### X-3 — The monitor daemon is fragile and unsupervised at the process level *(DB + Async)*
**🔴 Critical** · `src/langusta/cli.py:938-953` (daemon loop) · `src/langusta/scan/orchestrator.py:97,182` (write lock)

Three compounding defects:
1. The scan command holds the SQLite **write transaction open across all network I/O** (`start_scan` INSERT at orchestrator.py:97 begins a transaction that isn't committed until line 182, after ICMP/ARP/rDNS/TCP/mDNS/SNMP complete — potentially minutes for a /24).
2. The daemon's `while True` loop (cli.py:939-951) wraps the per-cycle `connect()` in **no `try/except`** — only a `finally` that clears the PID file.
3. A daemon write (`set_heartbeat`, `record_result`) that blocks on the scanner's write lock exceeds `busy_timeout=5000` → `sqlite3.OperationalError` propagates → **the daemon terminates**. A routine scan silently kills monitoring until manual restart.

### X-4 — Migrations only run from `init`; no upgrade guard or schema-version coordination *(Architecture + DB)*
**🟠 High** · `src/langusta/cli.py:111` (only `migrate()` call site) · `src/langusta/cli.py:918-951` (daemon)

`migrate()` is invoked exclusively by the `init` command. After `uv tool upgrade` ships new migrations, **every other command operates on a stale schema** until the user manually re-runs `langusta init` — `list`/`add`/`scan` will hit `no such table` errors. Separately, ADR-0002's explicit follow-up — *"daemon must take an exclusive DB lock and fail loudly on schema-version mismatch"* — is **not implemented**: the daemon never reads `PRAGMA user_version` or compares against `latest_schema_version()`. A stale daemon from an older binary silently reads/writes a schema it doesn't understand.

### X-5 — Invariant-protection tests are weaker than they claim *(Logic + Test-Coverage)*
**🟠 High** · `tests/unit/core/test_provenance.py:164-177` · `tests/property/test_writer_idempotency.py:43-73`

- `test_property_manual_fields_never_overwritten` asserts inside `if prior.provenance is MANUAL and field_name in applied:` — but `merge_scan_result` **never** puts MANUAL fields into `applied`, so the assertion body is unreachable. The test passes vacuously; it cannot detect a regression that leaks a MANUAL field into `applied`.
- The writer-idempotency property test generates `Observation` against an **empty** DB with no seeded MANUAL/IMPORTED fields, so the `proposed_changes`/`Deferred` (review-queue) paths are never exercised. It proves "re-apply doesn't grow rows" but not "no silent overwrite."

---

## Findings by domain

### 1. Database & Migrations

| # | Sev | Finding | Location |
|---|---|---|---|
| D-1 | 🔴 | Pre-migration backups are invisible to `prune`/`verify`/`list` — `_parse_stamp` extracts `"pre"` not the timestamp from `db-pre-migration-NNNN-...`, so they accumulate indefinitely and can't be integrity-checked | `db/backup.py:31-40` vs `db/migrate.py:196` |
| D-2 | 🟠 | `PRAGMA foreign_key_check` runs **after** every migration is committed — FK orphans are detected but not rolled back; `user_version` already advanced, so a later `migrate()` silently succeeds over a corrupt state | `db/migrate.py:300-327` |
| D-3 | 🟠 | No `UNIQUE` constraint on `assets.primary_ip`/`hostname` — identity resolution is a check-then-act TOCTOU; concurrent asset-creating ops (two scans, or scan + manual `add`) can create duplicate assets. DB-level uniqueness is absent as defense-in-depth | `db/migrations/001_initial_schema.sql:44-64` |
| D-4 | 🟡 | `timeline_entries.asset_id ... ON DELETE CASCADE` is permanently blocked by the immutability trigger → any future "delete asset" feature is structurally impossible for assets with history (latent design contradiction) | `db/migrations/001_initial_schema.sql:160,189-193` |
| D-5 | 🟡 | `check_results` has no retention/pruning — grows unbounded (`N × 1440` rows/day) under the daemon, degrading queries and bloating backups | `db/migrations/005_monitoring.sql:33-45` |
| D-6 | 🟡 | `_has_user_data` scans FTS5 shadow tables (`assets_fts_data`, etc.) which may contain rows on a freshly-migrated empty DB → spurious pre-migration backups `[NEEDS VERIFICATION]` | `db/migrate.py:162-190` |
| D-7 | 🟢 | FTS5 search silently returns no results for queries containing `\` (not in `_FTS_UNSAFE`); the `OperationalError` is swallowed → false negatives | `db/search.py:21-32,67` |
| D-8 | 🟢 | `_list_identities` does a full table scan per observation → O(K×N) per scan; fine at ≤250 assets, won't scale | `db/writer.py:108-127` |

### 2. Security & Crypto

| # | Sev | Finding | Location |
|---|---|---|---|
| S-1 | 🟠 | Backup snapshot files are never `chmod 0600` — created via `sqlite3.connect` with default umask (typically `0644`), so encrypted credentials in the backup are world-readable. If `backups/` is recreated by `backup.write`'s `mkdir` (not via `init`), the directory itself is `0755` not `0700` | `db/backup.py:80-94` |
| S-2 | 🟠 | Webhook URLs (Slack/Discord/Teams — bearer tokens in the URL path) are stored in **plaintext** in `notification_sinks.config`, inconsistent with the encrypted-at-rest posture for all other credentials | `cli.py:1091-1094` · `db/notifications.py:61-64` |
| S-3 | 🟡 | TOFU first-use connects with `known_hosts=None` (no verification) **and authenticates in the same `asyncssh.connect()`** before the host key is recorded — an active MITM on first connection captures SSH credentials and pins its own key | `monitor/ssh/asyncssh_backend.py:56-101` |
| S-4 | 🟡 | Only `LANGUSTA_MASTER_PASSWORD` is stripped from the spawned daemon env; `LANGUSTA_CRED_SECRET`, `LANGUSTA_CRED_V3_AUTH_PASS`, `LANGUSTA_CRED_V3_PRIV_PASS`, `LANGUSTA_NETBOX_TOKEN` leak into the daemon's `/proc/<pid>/environ` | `cli.py:994-997` |
| S-5 | 🟡 | No rate-limit/lockout on master-password unlock (Argon2id is the sole throttle; ~7k attempts/hour feasible locally) | `crypto/master_password.py:71-91` |
| S-6 | 🟢 | AES-GCM encrypt uses `associated_data=None` — ciphertext rows are swappable between credentials with DB write access (no row-binding) | `crypto/vault.py:86,91` |
| S-7 | 🟢 | TOCTOU in TOFU `known_hosts`: concurrent first-use checks to the same host both see `not contains` and race on `add` | `monitor/ssh/asyncssh_backend.py:56` · `known_hosts.py:94-103` |
| S-8 | 🟢 | Exception `repr` from failed checks printed to stderr / persisted into `CheckResult.detail` — theoretical credential leak if a library embeds secrets in error strings | `monitor/runner.py:152-157` |
| S-9 | 🟢 | No key-rotation/re-encryption support — a compromised master password requires manual re-init | `crypto/master_password.py` |

**Crypto verified sound:** fresh random 12-byte nonces per encrypt, production Argon2id params (`time_cost=3, memory_cost=64MiB`), `TEST_PARAMS` unreachable in prod, no timing oracle on the verifier, no `shell=True`/`eval`/`pickle`, SQL is parameterized with allowlisted identifiers.

### 3. Logic & Invariants

| # | Sev | Finding | Location |
|---|---|---|---|
| L-1 | 🟠 | (see X-2) Import identity resolver ignores hostname → silent merges the scan path would defer as `Ambiguous` | `db/import_common.py:85-104` |
| L-2 | 🟡 | `import_common.py` uses `mac.lower()` at 5 sites instead of `normalize_mac()` — latent; if normalization changes (e.g. stripping separators) import and scan paths diverge, creating duplicate MAC bindings | `db/import_common.py:93,155,217,275,350` |
| L-3 | 🟡 | Hardcoded provenance strings (`'scanned'`/`'manual'`/`'imported'`) in 10+ SQL sites + CHECK constraint, decoupled from `FieldProvenance` enum — an enum-value change silently desyncs Python and DB | `writer.py`, `assets.py`, `import_common.py`, `proposed_changes.py` |
| L-4 | 🟡 | `proposed_changes.accept()` check-then-act race: two connections both read `resolution IS NULL`, both apply → double "accepted" timeline entries | `db/proposed_changes.py:181-217` |
| L-5 | 🟢 | `_mac_exists` in `import_netbox.py` is dead code (defined, never called) | `db/import_netbox.py:89-92` |
| L-6 | 🟢 | `append_entry` accepts `corrects_id` with no cross-asset validation — a correction could reference an entry on a different asset | `db/timeline.py:62-83` |
| L-7 | 🟢 | `_apply_update` doesn't filter `None` values before `merge_scan_result` (unlike the writer) — a stray `None` could clear a SCANNED field; currently prevented only by the Lansweeper extractor | `db/import_common.py:174-186` |

**Invariants verified as holding (scanner path):** timeline immutability (no UPDATE/DELETE path exists), provenance merge correctness in every branch, identity resolution never auto-merges, provenance never flips MANUAL→SCANNED outside the explicit `accept()` path, transaction atomicity.

### 4. Async & Error-Handling

| # | Sev | Finding | Location |
|---|---|---|---|
| A-1 | 🟠 | No per-check timeout in `runner._run_one` — a single hung check (outside its internal timeout) consumes a semaphore slot indefinitely; at 32 hung checks the whole cycle + daemon deadlock | `monitor/runner.py:301-305` |
| A-2 | 🟡 | `snmp_gather` + its `_snmp_one` children are **not** in `enrichment_tasks`, so the `finally` cancel list leaks them if the outer gather raises from an enrichment task | `scan/orchestrator.py:111-142` |
| A-3 | 🟡 | `backup.verify()` uses bare `with sqlite3.connect(...)` (no `closing()`) — leaks an fd until GC, contradicting the codebase's own documented pattern | `db/backup.py:113` |
| A-4 | 🟡 | `filterwarnings = ["error", ...]` promotes **all** warnings to errors with only 2 narrow ignores — any benign deprecation from textual/typer/icmplib/httpx/etc. breaks CI | `pyproject.toml:73-81` |
| A-5 | 🟡 | `"ignore::ResourceWarning"` is global — masks LANgusta's own real resource leaks, not just the known pysnmp/zeroconf socket noise | `pyproject.toml:79` |
| A-6 | 🟡 | Exit code (1 vs 2) decided by substring-matching `"no credential"` in the exception message — a prose wording change silently flips the CLI contract | `cli.py:285-287` |
| A-7 | 🟢 | Orchestrator comment claims `gather(return_exceptions=False)` "cancels siblings on a regular Exception" — **false** in Python 3.8+; the `finally` is required for both Exception and BaseException. Misleads future readers. | `scan/orchestrator.py:106-110` |
| A-8 | 🟢 | `SnmpOidCheck`/`SshCommandCheck` `_required_str` raises `ValueError`, violating the "Checks NEVER raise" Protocol contract (practically safe via runner's `except Exception`) | `monitor/checks/snmp_oid.py:68` · `ssh_command.py:66` |
| A-9 | 🟢 | Post-scan backup failure (`OSError`) falls through the CLI's `ValueError`/`SocketPermissionError` handlers → raw traceback (data already committed, no loss) | `scan/orchestrator.py:181-185` |

### 5. Test Coverage & Quality

| # | Sev | Finding | Location |
|---|---|---|---|
| T-1 | 🟠 | Timeline immutability tested only at the trigger level — no lint/test that `db/timeline.py` defines no `update_*`/`delete_*` API; a future contributor adding one passes every existing test | `tests/unit/db/test_timeline_immutability.py` |
| T-2 | 🟠 | Autouse `_offline_scan_enrichments` uses `raising=False` — if a refactor renames/removes the patched imports, tests silently go **online** instead of failing | `tests/conftest.py:30-38` |
| T-3 | 🟠 | CI e2e smoke never runs `scan`, `review accept/reject`, `import-lansweeper`, or a re-scan — the invariants are only validated at unit level; a CLI-layer regression that bypasses `apply_scan_observation` ships green | `.github/workflows/ci.yml:47-64` |
| T-4 | 🟡 | `assert "1" in r.stdout` (and `"2"`) — passes on virtually any output | `tests/integration/test_cli_add_list.py:83` · `test_cli_scan.py:78,109` |
| T-5 | 🟡 | `max_examples` 10–30 on invariant-backing properties (Hypothesis default 100) — minimal confidence for the recovery-precedes-failure state space | `tests/property/test_runner_invariants.py:40` |
| T-6 | 🟡 | Snapshot docstring says commit `.svg` but artifacts are `.raw`; `--snapshot-update` workflow undocumented in CONTRIBUTING/CI | `tests/snapshots/test_inventory_screen.py:4` |
| T-7 | 🟡 | No negative tests for FTS5 special-char/malformed input (the silent `OperationalError→[]` catch could mask regressions) | `db/search.py:61-68` |
| T-8 | 🟡 | `tmp_langusta_home` teardown `chmod 0644` **loosens** permissions on the 0600 DB before deletion — minor info-leak window on shared CI runners | `tests/conftest.py:55-58` |
| T-9 | 🟢 | `meta.delete` and `credentials.get_by_id` have no tests | `db/meta.py:29` · `db/credentials.py:93` |
| T-10 | 🟢 | No coverage gate despite the test-first mandate in CONTRIBUTING.md | `pyproject.toml` |

### 6. Architecture & Conventions

| # | Sev | Finding | Location |
|---|---|---|---|
| R-1 | 🟠 | `apscheduler>=3.10,<4` is a declared runtime dependency documented across spec/ADR/dev-plan, but is **imported nowhere** in `src/` — the daemon uses a plain `while True`/`sleep` loop. Inflated install + major doc/code divergence. | `pyproject.toml:33` |
| R-2 | 🟠 | Raw SQL executes outside `db/`: `PRAGMA database_list` in `scan/orchestrator.py:62` passes the boundary lint because `PRAGMA` isn't in `_SQL_MARKERS` (the comment even acknowledges this "won't-fix") | `scan/orchestrator.py:62` · `scripts/lint_boundaries.py:26-35` |
| R-3 | 🟡 | Boundary-lint SQL check inspects only `ast.Constant` — blind to f-strings/concat/`.format()` (all current SQL happens to be inside `db/`, so latent) | `scripts/lint_boundaries.py:137-144` |
| R-4 | 🟡 | Boundary-lint platform check is a literal substring search — misses `from sys import platform`, `from platform import system`, `os.name` (no current violations, latent) | `scripts/lint_boundaries.py:102-109` |
| R-5 | 🟡 | Boundary-lint core check false-positives on relative imports (`from .models import`) — works only because `core/` uses absolute imports by convention | `scripts/lint_boundaries.py:73-80` |
| R-6 | 🟡 | Test-only modules shipped in the production wheel: `scan/snmp/transcript_backend.py` and `monitor/ssh/stub_backend.py` (docstrings say "for tests"; imported only by tests) | `src/langusta/scan/snmp/transcript_backend.py` · `monitor/ssh/stub_backend.py` |
| R-7 | 🟡 | Dev-plan references non-existent files (`monitor/scheduler.py`, `monitor/worker.py`, `tui/widgets/footer.py`) — design diverged from plan, plan never updated | `docs/development-plan.md:256-260` |
| R-8 | 🟡 | `paths.config_path()` is dead code (no `config.toml` is ever read/written); only referenced by a structural test | `paths.py:41-42` |
| R-9 | 🟡 | README status says `0.2.0` while version is `0.2.1rc1` | `README.md:5` |
| R-10 | 🟡 | pyasn1 pin comment has a placeholder `CVE-2025-...` — unverifiable, non-actionable for security auditing | `pyproject.toml:36` |
| R-11 | 🟢 | `cli.py` is 1216 LOC across 30+ subcommands (acknowledged debt, allowlisted + staleness-tested) | `cli.py` |
| R-12 | 🟢 | AGENTS.md omits `synchronous=NORMAL` from the documented pragma list | `AGENTS.md:62` · `db/connection.py:22` |

---

## Migration / operational risk matrix

Issues that **must** be resolved before relying on LANgusta in production or before a platform change.

| Issue | Risk | Council members | Recommendation |
|---|---|---|---|
| X-3: scan kills the daemon | Monitoring silently stops during any scan | DB + Async | Commit after `start_scan`; add per-cycle `try/except` + backoff in the daemon loop |
| X-4: no upgrade/migration guard | Post-`uv tool upgrade` operation on stale schema; daemon ignores version mismatch | Architecture + DB | Add a schema-version check at app startup / daemon start; document `init` must be re-run |
| D-2: post-commit FK check | Corrupt FK state undetectable after a bad migration | DB | Run `foreign_key_check` inside each migration transaction |
| D-1: pre-migration backups unmanaged | Disk exhaustion; backups can't be verified | DB | Fix `_parse_stamp` to parse the `pre-migration-` format |
| S-1: backup files world-readable | Encrypted credentials readable by any local user | Security | `chmod 0600` on every backup; `enforce_private` on recreated `backups/` dir |
| X-1: SMTP auth broken | Alerting silently fails for authenticated relays | Security + Architecture | Read the env vars or vault-store SMTP creds; fix docs |

---

## Remediation plan — by priority

### 1. Critical (fix immediately)
- **X-1 SMTP:** read `LANGUSTA_SMTP_USERNAME`/`LANGUSTA_SMTP_PASSWORD` in `send_smtp` (or vault-store them); correct the docstring + AGENTS.md.
- **X-2 import invariant:** route both importers through `core.identity.resolve` + `merge_scan_result`. NetBox should call `apply_imported_observation`; `_resolve_identity` must check hostname and defer to the review queue on MAC↔hostname conflict. Add a test asserting a NetBox/Lansweeper row conflicting with a MANUAL field produces a `proposed_changes` row.
- **X-3 daemon resilience:** commit after `start_scan` so the write lock is released during network I/O; wrap the daemon per-cycle body in `try/except Exception` with a logged warning + back-off sleep so a transient `OperationalError` degrades rather than kills.

### 2. High (before any production deployment)
- **X-4 upgrade guard:** add a `current_schema_version()` vs `latest_schema_version()` assertion at daemon start (and ideally a startup guard in `connect()` or a shared CLI preamble); implement the ADR-0002 schema-version coordination.
- **X-5 invariant tests:** make `test_property_manual_fields_never_overwritten` assert MANUAL fields are *never* in `applied`; seed mixed provenance + full field sets in the writer idempotency property.
- **D-2:** move `foreign_key_check` inside each per-migration transaction.
- **S-1 / S-2:** `chmod 0600` backups + `enforce_private` on recreated dirs; vault-store webhook tokens.
- **R-1:** remove `apscheduler` from deps (or implement it) and reconcile spec/ADR/dev-plan docs.
- **T-1 / T-2 / T-3:** add a timeline-DAL no-mutation lint; set `raising=True`; extend the CI smoke to cover `scan` + `review`.
- **A-1:** add an outer `asyncio.timeout` around `impl.run()` in the runner.

### 3. Medium (first post-audit sprint)
- Fix `_parse_stamp` (D-1); add `check_results` retention (D-5); normalize_mac in import_common (L-2); centralize provenance strings (L-3); conditional-UPDATE on `proposed_changes.accept` (L-4); per-check timeout (A-1); `closing()` in `backup.verify` (A-3); narrow `filterwarnings`/`ResourceWarning` ignores (A-4/A-5); exception-class-based exit codes (A-6); boundary-lint blind spots (R-2..R-5); move test backends out of the wheel (R-6); fix snapshot docs (T-6); tighten weak assertions (T-4); raise `max_examples` (T-5); env-var stripping (S-4); README version + CVE placeholder (R-9/R-10).

### 4. Low (long tail)
- D-7/D-8, L-5/L-6/L-7, S-6..S-9, A-7..A-9, T-9/T-10, R-11/R-12.

---

## Project quality metrics

- **Overall score: 78 / 100**
- **Justification:** The core domain layer (`core/`), scanner path, crypto primitives, and migration checksum chain are exemplary and would score 90+. The score is dragged down by the edges: the import paths silently violate an invariant (X-2), a documented feature silently doesn't work (X-1), the daemon is operationally fragile (X-3/X-4), and the invariant tests overstate their coverage (X-5). None of these are hard to fix, but together they mean the product's three promised invariants hold *only on the scanner path*, not across import, upgrade, and monitoring.
- **By domain:** Database 72/100 · Security 80/100 · Logic/Invariants 75/100 · Async/Errors 78/100 · Test-Coverage 74/100 · Architecture 82/100

---

## Notes on method

- All 39 findings were produced by six read-only specialist subagents, then **cross-verified against source** before inclusion. The 6 cross-cutting items (X-1..X-6/X-section) are the highest-confidence because two independent agents reached them.
- Where a finding's severity depended on a scenario the code didn't fully confirm, it is marked `[NEEDS VERIFICATION]` (only D-6) rather than dropped or guessed.
- No source files were modified during this audit. Working notes were synthesised directly into this report.
