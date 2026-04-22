# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Pre-1.0 versions may introduce breaking changes on any minor bump.

## [Unreleased]

## [0.2.1rc1] — 2026-04-22

Rolls up the Wave-3 multi-agent code-review response (72 findings addressed across correctness, security, architecture, and test-coverage lenses) plus the Lansweeper importer polish. No schema change; schema stays at v7.

### ⚠ Upgrade note — data recovery for anyone already on 0.2.0

Migration 007 shipped in 0.2.0 had a latent bug: the `monitoring_checks` table rebuild ran under SQLite's `foreign_keys=ON`, and SQLite's documented "implicit DELETE FROM on DROP TABLE" semantics cascaded through `check_results.check_id ON DELETE CASCADE` — **every row in `check_results` was silently deleted on the 0.1 → 0.2 upgrade path**. 0.2.1rc1 fixes the runner (see below), but cannot un-delete rows that have already been lost.

**If you upgraded to 0.2.0 and want your check-result history back**, the pre-migration backup that ADR-0005 mandates has what you need. It's at `~/.langusta/backups/db-pre-migration-*.sqlite` — pick the most recent file timestamped before your 0.2.0 upgrade and copy it into `~/.langusta/db.sqlite` before running 0.2.1rc1 for the first time. Fresh installs and users who never ran 0.2.0 are unaffected.

### ⚠ Breaking behaviour

These are intentional changes that will flip on at upgrade time for existing users. Check your setup matches the new defaults before deploying.

- **`HttpCheck` now verifies TLS certificates by default.** Previously the `verify=False` kwarg was hardcoded, silently disabling certificate verification on every HTTPS probe. Any monitor check pointing at a self-signed or expired cert will flip to `fail` on the next cycle. Opt out per-call via `insecure_tls=True` kwarg to `HttpCheck.run`; storing the flag per-check (new column) is a follow-up migration. Closes Wave-3 finding M-001 (3-lens confirmed high).
- **`monitor start` no longer passes `LANGUSTA_MASTER_PASSWORD` into the detached daemon.** `subprocess.Popen` now receives an explicit `env=` with the password key stripped — the previous behaviour left the secret in `/proc/<pid>/environ`. If you have `snmp_oid` or `ssh_command` checks whose credentials live in the vault, deploy the daemon via `langusta monitor install-service` (ADR-0002) instead; the service manager supplies the password through its own credential mechanism. Closes Wave-3 finding S-005.
- **`import-netbox` refuses `next` URLs pointing at a different origin than the base URL.** Paginated responses used to follow `next` blindly, carrying the bearer token to whatever host NetBox (or a MITM) pointed at. Now raises `NetBoxNetworkError` on any scheme/host/port mismatch; the CLI surfaces that as a normal `error: network error: ...` exit 1. Closes Wave-3 finding S-007.
- **`monitor enable` prints every validation error up front instead of failing one at a time.** Users who previously ran `--kind snmp_oid` without `--oid` and `--credential-label` got one error per retry; now they see both and exit 2 once. Closes Wave-3 finding A-017.
- **`langusta_home()` rejects a non-absolute `LANGUSTA_HOME` env override** with a `ValueError`. Empty env var still falls back to `~/.langusta`. Closes Wave-3 finding S-013.

### Security

- **Migration 007 cascade-delete fix** (see upgrade note above). The runner now disables FK enforcement around the pending-migration chain per SQLite's canonical "12-step schema surgery" recipe, runs `PRAGMA foreign_key_check` afterwards, and re-enables. Settles Wave-2 open uncertainty C-002.
- **`langusta init` no longer leaves `~/.langusta` or `db.sqlite` world-readable mid-flight.** On a default-umask-022 host the tree was 0755/0644 during the window between DB creation and the final `enforce_private` — exposing the vault salt and verifier to any local user. `init()` now tightens umask to `0o077` across the whole setup block; the post-setup `enforce_private` calls remain as belt-and-braces for the re-init path. S-002.
- **`import_from_dict` allowlists column names per table** via `PRAGMA table_info` before interpolating them into the INSERT SQL. A crafted dump previously leaked a sqlite3 `OperationalError` on injection attempts; now raises `ImportRefused` cleanly, and also catches benign schema-drift typos. S-001.
- **`macOS` launchd plist routes daemon logs under `~/Library/Logs`** instead of `/tmp`. The previous `/tmp/langusta-monitor.{out,err}` path was world-readable (mode 1777) and symlink-attackable. S-003.
- **`send_webhook` failure-path logs scheme://netloc only**, no URL path or query — Slack/Discord webhooks encode the auth token in the path, and the failure `print` to stderr was leaking it. S-014.
- **`write_pid_file` refuses to follow symlinks** (swapped to `os.open` with `O_NOFOLLOW`), blocking a local attacker from using a planted symlink at `~/.langusta/monitor.pid` to redirect the write. S-006.
- **`KnownHostsStore.add` chmods the file to 0o600 on every write**, so TOFU pins don't inherit a loose umask. S-010.
- **`stop_via_pid_file` sniffs `/proc/<pid>/cmdline` for "langusta"** before signalling, guarding against SIGTERM'ing an unrelated process whose PID reused the slot after a daemon crash. S-012.
- **`SnmpV3Auth` emits `WeakSnmpv3ProtocolWarning`** when constructed with MD5 auth or DES/3DES priv. The protocols remain usable for legacy gear, but operators see the crypto risk at credential-create time. S-011.
- **`proposed_changes.accept` / `edit_override` allowlist field names** before interpolating them into the UPDATE SQL. Not reachable from any current writer, but closes the defence-in-depth gap against a future import or adversary that can append rows to `proposed_changes` directly. M-002.

### Bug fixes

- **Each migration is now atomic across DDL and bookkeeping.** Python sqlite3's LEGACY isolation level auto-commits before every DDL/PRAGMA, and `executescript` commits any pending transaction first — so a crash between `executescript(mig.sql)` and `INSERT INTO _migrations` left the DDL persisted without a bookkeeping row, wedging the next `migrate()` run on re-apply. Runner now hands transaction control to the app (`isolation_level = None`) during the pending chain, wraps each migration in explicit `BEGIN`/`COMMIT`/`ROLLBACK`, and splits SQL via `sqlite3.complete_statement`. C-001.
- **Monitor runner keeps writing the heartbeat when a single check raises.** `asyncio.gather`'s default `return_exceptions=False` re-raised on the first task error, aborting the cycle before `set_heartbeat` — a single flaky `record_result` wedged the "is the daemon alive?" signal. Switched to `return_exceptions=True`. C-011.
- **`HttpCheck` and `TcpCheck` now honour `check.timeout_seconds`.** The runner's `_resolve_config` only plumbed the value for `snmp_oid` and `ssh_command`; tcp and http silently used the backends' hardcoded 5 s default regardless of what the user configured. C-004.
- **`AsyncsshBackend` surfaces TOFU-record failures** in `SshResult.stderr` with exit_code = -1. Previously `_record_host_key` swallowed every exception; a failure to persist the pin left the backend stuck in first-use-unverified mode forever, with the command appearing to succeed. The concurrent-writer race (another worker already persisted the pin) is still tolerated via an explicit post-hoc check. C-008.
- **`dispatch()`'s always-on notifications log write surfaces failures to stderr.** Previously wrapped in `contextlib.suppress(OSError)` — EACCES, disk-full, and out-of-quota disappeared silently. C-010.
- **`monitor daemon --foreground` refuses to clobber a live PID file.** A second invocation used to overwrite the recorded PID unconditionally, orphaning the original daemon in `monitor stop`. M-007.
- **`migrate._write_backup` and `backup.write` close their SQLite connections** via `contextlib.closing`. `with sqlite3.connect(...) as c` only commits; without an explicit close each call leaked two fds. M-003 (+ knock-on fix to `backup.write` flagged by A-002 as duplicated logic).
- **`_has_user_data` uses `EXISTS` + `SELECT 1 … LIMIT 1`** instead of `COUNT(*)` across every user table — short-circuits on first row and gates the table-name interpolation behind a simple-identifier regex. C-021.
- **Scan-orchestrator enrichment tasks cancel cleanly on `BaseException`.** `asyncio.gather` already cancels siblings on a regular Exception; wrapping in try/finally + explicit cancel covers the Ctrl+C / CancelledError path too. C-018.
- **`_bind_mac` surfaces MAC collisions as a `system` timeline entry** on both assets, instead of silently returning False. Catches the DAL-level race that the identity resolver's Ambiguous-path is supposed to handle but can miss under concurrent scans. C-015.
- **`mdns_discover` logs browser-function exceptions to stderr** instead of swallowing. Operators couldn't previously tell mDNS enrichment was broken. C-020.
- **`monitor start` closes its parent-side log fd** after Popen dups it into the child. Was one fd leaked per invocation. M-004.
- **Migration runner's pre-migration backup is on by default.** `backups_dir` now defaults to `paths.backups_dir()` internally instead of silently skipping the ADR-0005 safety rail when the caller forgets the kwarg. A-019.
- **`Lansweeper` / `NetBox` importers route IP collisions through the review queue** instead of silently skipping. See "Features / changed behaviour" below for the full polish-pass summary.

### Features / changed behaviour

- **`langusta notify add-logfile --label X --path Y`** registers a logfile sink. `VALID_KINDS` included `"logfile"` but there was no CLI to add one. A-005.
- **Lansweeper importer polish** — the conservative "skip on collision" behaviour from 0.2.0rc2 is replaced with proper scanner-proposes-human-disposes semantics. MAC-matched rows merge through `core.provenance.merge_scan_result`; IP-only matches land in the `review_queue`; protected-field conflicts surface in `proposed_changes` instead of being silently dropped.
  - **`import-lansweeper --dry-run`** — parse, validate, report counts; single outer `SAVEPOINT` rolled back before the connection commits.
  - **`import-lansweeper --verbose`** — per-row errors with CSV line numbers. Each row is wrapped in `SAVEPOINT row_<n>` so a bad row rolls back independently.
  - **Expanded column mapping** — `detected_os`, `location`, `owner`, `management_url` now map from `OperatingSystem` / `Location` / `Owner` / `URL` (plus synonyms).
  - **Demo fixture** `tests/fixtures/lansweeper_demo.csv` — ~25 rows exercising BOM headers, unicode hostnames, embedded newlines, malformed IPs, intra-file MAC/IP collisions.
  - **`ImportReport`** now reports `imported`, `updated`, `skipped`, `proposed_changes_created`, `review_queue_entries`, `row_errors`.
  - **Every import row that touches an asset emits a `kind='import'` timeline entry** authored by `'importer'`.
  - **New shared module `src/langusta/db/import_common.py`** (`ImportOutcome`, `RowError`, `apply_imported_observation`, `insert_imported_asset`) — the NetBox importer will wire through the same semantics in a follow-up.

### Internal

Refactors with no user-facing behaviour change, listed for contributor context.

- **`db/writer._build_scan_diff_body`** extracted as a pure function + property-tested. A-011.
- **`core.monitoring.validate_check_config`** is the single owner for field-interaction validation (kind + comparator/expected + kind-specific requirements). A-017.
- **`scan.snmp.credentials.resolve_snmp_credential`** replaces the inline credential-lookup block in `cli scan --snmp`. A-010.
- **`langusta.backup` moved to `langusta.db.backup`** to sit next to the rest of the DB-lifecycle code. A-022.
- **Shared `langusta.core.net.open_tcp_connection`** replaces the twin `_open_connection` helpers in `scan/tcp` and `monitor/checks/tcp`. A-009.
- **`core.models.normalize_mac`** is the single point of MAC canonicalisation; the scattered `.lower()` calls across the DAL / writer / importers / ARP enrichment are gone. A-013.
- **`core.monitoring.is_heartbeat_stale`** moved out of `db.monitoring` to sit beside the other pure monitor-domain code (with a back-compat re-export). A-006.
- **`paths.notifications_log_path()`** is the single source of truth for the always-on log path; the two `langusta_home() / "notifications.log"` literals in `cli.py` are gone. A-003.
- **Monitor runner builds SNMP / SSH backends lazily** — pure ICMP/TCP/HTTP cycles no longer pay the pysnmp / asyncssh import+setup cost. A-016.
- **`monitor/runner.DEFAULT_REGISTRY`** is a `MappingProxyType`; stray `[...] = ...` mutation now raises `TypeError`. C-012.
- **`db.connection.connect(path, *, readonly=False)`** opts into `PRAGMA query_only = 1` and skips the commit-on-exit dance. Default unchanged. C-022.
- **`monitor.notifications.send_to_sink`** is the public helper `cli notify test` uses instead of reaching into private `_SENDERS`. A-004.
- **Function-local `import asyncio / json / pathlib.Path` re-imports** in `cli.py` and `scan/orchestrator.py` promoted to top-of-module. A-008, A-020.
- **`langusta.platform.NotImplementedCapability`** imported from the package root instead of `.base` in `cli.py`. A-023.
- Miscellaneous: `_unlock_vault` returns the Vault outside the `with connect()` block (C-019); `known_hosts.py` + `migrate.py` gap documented via README and ADR comments (A-007, A-015, A-021); ADR-0002's historical `TimelineWriter` reference updated (A-015).

### Tests and coverage

- **676 tests passing** (up from 536 at 0.2.0 — +140 net, including +21 from 0.2.1rc1's Lansweeper polish and +119 from the Wave-3 response).
- Every high/medium Wave-3 finding has a regression test; low-severity refactors rely on existing integration coverage.
- New property suites: `test_backup_dedupe.py`, `test_migration_checksum.py`, `test_export_roundtrip.py`, `test_writer_idempotency.py`, `test_runner_invariants.py`, `test_build_scan_diff_body.py`.
- New snapshot coverage: `test_timeline_widget.py` (mixed-kind + empty), frozen-fresh and stale heartbeat baselines, 12-asset inventory baseline.
- `ruff check src tests scripts` and `scripts.lint_boundaries` both clean.

### Deferred

- Per-check persistence of `HttpCheck` `insecure_tls` flag — plumbing is in, schema column is a follow-up migration.
- NetBox importer wire-through to `apply_imported_observation` (Lansweeper-parity).
- `Migration 008` for `serial_number` / `asset_tag` columns.
- Streaming / progress reporting for import of >10K-row files.
- True TOCTOU guard on `monitor start` (read-then-spawn-then-write is not atomic). Tests cover the single-invocation failure modes only; atomicity is a separate concurrency-safety PR.
- True crash-safety proof for `migrate()` (kill -9, OOM) via a fsck-style tool comparing `_migrations` / `user_version` / live schema.

## [0.2.0] — 2026-04-19

Stable 0.2.0. Consolidates rc1..rc6 into one minor release. No code change beyond the version bump.

### Rollup of new features since 0.1.0-rc1

- **Importer on-ramp** (rc2): `import-lansweeper` (CSV), `import-netbox` (API).
- **SNMP v3 authPriv + new check kinds** (rc2): `cred add --kind snmp_v3`; `monitor enable --kind snmp_oid`; `monitor enable --kind ssh_command`.
- **Notification sinks** (rc2): pluggable `log` / `webhook` / `smtp` sinks; `notify sink add/list/rm`.
- **TUI heartbeat indicator** (rc2): footer strip shows monitor daemon freshness.
- **Security fix** (rc2): pyasn1 bumped past DoS / unbounded-recursion CVE.
- **TUI monitor-config screen** (rc3): `m` on the inventory opens a screen to list and toggle configured checks; new DAL `set_check_enabled`.
- **Vim keybindings preset** (rc4): opt in with `LANGUSTA_KEYBINDINGS=vim`; j/k/g/G/ctrl+d/ctrl+u layered on top of defaults.
- **TOFU SSH host-key pinning** (rc5): `ssh_command` checks record host keys in `~/.langusta/known_hosts` on first connect, verify thereafter; rotated keys never auto-accepted.
- **`monitor start` / `monitor stop` + PID file** (rc6): detached daemon via `subprocess.Popen(start_new_session=True)`; `monitor status` now reports PID-file state alongside heartbeat. **ADR-0006** documents the design.

### Test count

536 tests passing. Ruff + boundary lint clean. Schema at v7.

### Dependencies

`asyncssh>=2.14` added; runtime deps now 10 / 15 budget.

## [0.2.0rc6] — 2026-04-19

### Added

- **`monitor start`** — spawn the daemon in a new session via `subprocess.Popen(start_new_session=True)`. stdout/stderr captured to `~/.langusta/monitor.log`; PID recorded in `~/.langusta/monitor.pid`. Refuses to start when a live PID is already recorded; cleans up stale PID files automatically.
- **`monitor stop`** — reads the PID file, sends SIGTERM, waits up to `--timeout` seconds, then clears the PID file. Reports cleanly when nothing is running.
- **`monitor status`** — now reports PID-file state (absent / running / stale) in addition to the existing heartbeat freshness.
- New module `monitor.daemon_control` with `read_pid_file`, `write_pid_file`, `clear_pid_file`, `is_process_alive`, `stop_via_pid_file`. 12 unit tests.
- `paths.monitor_pid_path()` and `paths.monitor_log_path()`.
- `monitor daemon --foreground` now writes + clears the PID file for its own process so `status` works for the supervised path too.
- **ADR-0006** documents the choice of `start_new_session=True` over a traditional double-fork, and the scope boundary against ADR-0002 (systemd/launchd via `monitor install-service` remains the recommended deployment).

Addresses deferred backlog #7 in spirit — LANgusta does not double-fork, but `monitor start` provides a fully-detached alternative for users who don't want to configure a service manager.

## [0.2.0rc5] — 2026-04-19

### Added

- **SSH host-key pinning (TOFU)** for `ssh_command` monitor checks. First connection to a given `host:port` records the server's host key in `~/.langusta/known_hosts`; subsequent connections verify against the recorded key and fail with a clear error on mismatch. LANgusta never auto-accepts a rotated key — operators must remove the entry from the file to re-pin. File format is OpenSSH-compatible (`[host]:port` bracket syntax for non-default ports, `host` bare for port 22). Clears deferred backlog #8.
- `paths.known_hosts_path()` — canonical location for the store (`~/.langusta/known_hosts`).
- New module `monitor.ssh.known_hosts` with `KnownHostsStore`, `HostKeyEntry`, `KeyNotPinnedError`, `KeyMismatchError`.

### Changed

- `AsyncsshBackend` no longer calls `asyncssh.connect` with `known_hosts=None` unconditionally. Pinned hosts use the known_hosts file; first-use hosts connect without verification just long enough to capture and record the server key, then verification is enforced on subsequent runs.

## [0.2.0rc4] — 2026-04-19

### Added

- **Vim-style keybindings preset** — opt in with `LANGUSTA_KEYBINDINGS=vim langusta ui`. Adds `j`/`k` (down/up), `g`/`G` (top/bottom), and `ctrl+d`/`ctrl+u` (page down/up) as non-priority aliases that layer on top of the default Textual arrow-key navigation. Additive — does not remove or remap defaults; unknown preset names degrade gracefully to `()`. Clears deferred backlog #6.

## [0.2.0rc3] — 2026-04-19

### Added

- **Monitor config TUI screen** — new `m` binding on the inventory screen opens `MonitorConfigScreen`, which lists every configured check (id, asset, kind, target, interval, enabled flag, last status). Press `e` to toggle enabled state for the highlighted check; `q` to go back. Clears deferred backlog #4 part 2.
- `db.monitoring.set_check_enabled(conn, check_id, enabled=bool)` — the DAL primitive used by the toggle action (unit-tested both directions).

## [0.2.0rc2] — 2026-04-19

Second alpha release candidate. Rolls up post-v1 work: importer on-ramp, SNMP v3, two new monitor check kinds, notification sinks, TUI heartbeat, and a security dependency bump.

### Added

**Importer on-ramp**
- `langusta import-lansweeper <csv>` — maps Lansweeper asset export columns to LANgusta's identity/provenance model; unknown columns go to `notes`.
- `langusta import-netbox --url --token` — pulls NetBox DCIM assets via API, preserves site/rack/role metadata.

**SNMP v3 + new check kinds**
- `cred add --kind snmp_v3` prompts for the 5 USM fields (user, authProtocol, authPass, privProtocol, privPass) or reads `LANGUSTA_CRED_V3_*` env vars.
- `langusta scan --snmp <label>` now accepts v3 credentials and performs authPriv walks.
- `monitor enable --kind snmp_oid` — walks a given OID and evaluates against `--expected` via `--comparator {eq,ne,lt,le,gt,ge,contains,regex}`.
- `monitor enable --kind ssh_command` — runs a command over SSH, asserts `--success-exit` and optional `--stdout-pattern` regex; concurrency capped to protect target hosts. Known limitation: `asyncssh` called with `known_hosts=None` (tech debt, to be addressed post-rc).

**Notifications**
- Pluggable sinks: `log` (default, timeline), `webhook` (JSON POST), `smtp`.
- `notify sink add/list/rm` CLI.
- Monitor state transitions fan out to configured sinks.

**TUI heartbeat indicator**
- New `Heartbeat` widget above the footer on inventory, asset-detail, search, and review-queue screens.
- Shows daemon freshness based on `meta.daemon_heartbeat` (last-seen seconds / minutes / hours).
- Hypothesis-tested formatter + widget + snapshot test.

### Fixed

- `pyasn1` bumped `0.6.0 → 0.6.3` past the DoS / unbounded-recursion CVE (transitive via `pysnmp`).

### Schema

- Migration 006 — notification sinks table.
- Migration 007 — `monitoring_checks` table-swap adds `oid`, `expected_value`, `comparator`, `command`, `success_exit_code`, `stdout_pattern`, `timeout_seconds`, `credential_id`, `username` columns. Schema now at v7.

### Dependencies

- Added `asyncssh>=2.14` (1 new runtime dep). Budget: 10 / 15.

### Test count

491 tests passing (was 357 at 0.1.0rc1). Ruff + boundary lint clean.

## [0.1.0rc1] — 2026-04-17

First alpha release candidate. Delivers the v1 Must-Have scope from the [development plan](docs/development-plan.md). Ready for early users who are comfortable reporting issues.

### Added

**Asset registry (M1)**
- `Asset` dataclass + DAL (`db.assets`) with `insert_manual` / `list_all` / `get_by_id` / `get_provenance`.
- Per-field provenance via `core.provenance.merge_scan_result` (stdlib-only, proved by 5 Hypothesis property tests).
- MAC normalisation to lowercase and global UNIQUE.
- CLI: `langusta add` / `langusta list` / `langusta ui`.

**Network scanner (M2 + M3)**
- `scan/icmp.py`, `scan/arp.py`, `scan/rdns.py`, `scan/tcp.py` (45-port curated top list), `scan/mdns.py`, `scan/oui.py` (packaged IEEE subset).
- Composite identity resolution (`core.identity.resolve`) returning `Insert | Update | Ambiguous`. The Lansweeper-failure rule (MAC-says-A, hostname-says-B → never silent merge) is Hypothesis-tested.
- `db.writer.apply_scan_observation` — the single atomic write path for scan results. Merges via `merge_scan_result`; conflicts with `manual`/`imported` fields become `proposed_changes` rows.
- Orchestrator runs ICMP → ARP → (rDNS ∥ TCP ∥ mDNS ∥ SNMP) concurrently.

**Asset detail + timeline (M3)**
- `db/timeline.py` insert-only DAL with `append_entry` and `append_correction_of`. SQL triggers on `timeline_entries` reject UPDATE and DELETE at the storage layer.
- Textual `AssetDetailScreen` with timeline widget, `JournalEditorScreen` modal (Ctrl+S to save a manual note).

**Universal search + review queue (M4)**
- Migration 002 adds an FTS5 virtual table over 8 asset text fields, kept in sync via INSERT/UPDATE/DELETE triggers.
- `db/search.py::search()` — FTS5 prefix matching + MAC substring LIKE.
- Textual `SearchScreen` (live input → DataTable results) and `ReviewQueueScreen` (accept / reject with disposition timeline entries).
- Inventory row-selection now pushes the asset detail screen; `/` opens search; `r` opens the review queue.

**Credential vault + SNMP (M5)**
- `crypto/vault.py` (AES-256-GCM) + `crypto/kdf.py` (Argon2id, `time_cost>=2`, `memory_cost>=32 MiB`).
- `crypto/master_password.py` — setup / unlock with a stored verifier; wrong password raises `WrongMasterPassword`.
- `db/credentials.py` — list_info never exposes secrets; `get_secret` is the sole decryption path.
- SNMP subsystem: `SnmpClient` Protocol + `PysnmpBackend` (pysnmp-lextudio) + `TranscriptBackend` (test fixtures).
- Orchestrator accepts `--snmp <label>` and populates `detected_os` from sysDescr without failing on unresponsive hosts.
- 9-test secret-hygiene suite proves the secret appears in zero stdout/stderr/log/db-bytes surfaces.

**Backups + portability (M6)**
- `backup.py` — online-backup API with 1h dedup window, `list_backups` (newest-first), `prune(keep=N)`, `verify` via `PRAGMA integrity_check`.
- `db/export.py` — JSON envelope with `export_format_version` + `schema_version`. Credentials excluded by default; internal tables (FTS, `_migrations`) skipped (rebuilt on import).
- Orchestrator writes a post-scan backup automatically when `backups_dir` is set.
- CLI: `backup now/list/verify/prune`, `export`, `import`.

**Monitoring (M7)**
- Migration 005 adds `monitoring_checks` + `check_results`. Heartbeat stored at `meta.daemon_heartbeat`.
- `monitor/checks/` — ICMP / TCP / HTTP implementations; each returns `CheckResult` and never raises.
- `monitor/runner.py::run_once` — finds due checks, dispatches concurrently, records results, writes `monitor_event` timeline entries on state transitions.
- CLI: `monitor enable/disable/list/run/status`.

**Release hardening (M8)**
- `platform/linux.py` and `platform/macos.py` ship `daemon_install_recipe()` returning an `InstallRecipe` (systemd user unit / launchd plist). Windows raises `NotImplementedCapability`.
- CLI: `langusta monitor install-service` writes the unit or plist to the correct XDG / LaunchAgents path; `--dry-run` prints; `--force` overwrites.
- CLI: `langusta monitor daemon --foreground` — the supervisor-friendly loop (refuses to background itself, per ADR-0002).
- README rewritten with install tabs; `docs/install.md`, `docs/upgrading.md`, `docs/daemon.md` added.

**Infrastructure (M0 throughout)**
- `db/connection.py` single-helper applying WAL + synchronous=NORMAL + foreign_keys=ON + busy_timeout=5000 + temp_store=MEMORY on every open.
- `db/migrate.py` hand-rolled migration runner — forward-only, checksum-immutable, pre-migration-backup mandatory.
- `scripts/lint_boundaries.py` — CI-enforced architectural lints: core/ is stdlib-only, sys.platform branches live only in platform/, raw SQL lives only in db/.
- Schema reaches v5 at 0.1.0-rc1.

### Test count

357 tests (including 11 Textual snapshots and 6 Hypothesis property tests) passing on Linux and macOS CI.

### Known limitations (deferred)

- Native Windows support — WSL2 is the v1 path (ADR-0004).
- Full detached daemon with PID file + log rotation — the service-manager does this; `monitor daemon --foreground` is the entry point.
- TUI footer heartbeat indicator + `monitor_config` screen polish.
- SNMP v3 authPriv, SNMP-OID check kind, SSH-command check kind.
- Lansweeper CSV / NetBox API import (the competitor on-ramp) — first post-v1 target.
- External secret-store integration (1Password CLI / Bitwarden CLI / Vault).

[Unreleased]: https://github.com/AmigoUK/LANgusta/compare/0.2.0...HEAD
[0.2.0]: https://github.com/AmigoUK/LANgusta/releases/tag/0.2.0
[0.2.0rc6]: https://github.com/AmigoUK/LANgusta/releases/tag/0.2.0rc6
[0.2.0rc5]: https://github.com/AmigoUK/LANgusta/releases/tag/0.2.0rc5
[0.2.0rc4]: https://github.com/AmigoUK/LANgusta/releases/tag/0.2.0rc4
[0.2.0rc3]: https://github.com/AmigoUK/LANgusta/releases/tag/0.2.0rc3
[0.2.0rc2]: https://github.com/AmigoUK/LANgusta/releases/tag/0.2.0rc2
[0.1.0rc1]: https://github.com/AmigoUK/LANgusta/releases/tag/0.1.0rc1
