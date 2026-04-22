# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Pre-1.0 versions may introduce breaking changes on any minor bump.

## [Unreleased]

### Fixed

- **Migration runner no longer cascade-deletes FK-referenced child rows during rebuild-via-swap migrations.** SQLite performs an implicit `DELETE FROM` on `DROP TABLE` when `foreign_keys=ON`, which cascaded through `check_results.check_id ON DELETE CASCADE` in migration 007 and silently destroyed all historic check results on the 0.1 → 0.2 upgrade path. The runner now disables FK enforcement across the pending-migration chain (SQLite's canonical "12-step schema surgery" recipe), runs `PRAGMA foreign_key_check` afterwards to catch any genuine orphans a migration produced, and re-enables FKs. Settles Wave-2 post-review open uncertainty **C-002** against `src/langusta/db/migrate.py` and `migrations/007_monitor_snmp_ssh.sql`; regression test at `tests/unit/db/test_migrate.py::test_migrate_007_preserves_check_results_across_table_rebuild`. Users who already upgraded to 0.2.0 and lost `check_results` history should restore from their pre-migration backup at `~/.langusta/backups/db-pre-migration-*.sqlite`.

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
