# LANgusta v1 — synthesized implementation plan

**Status:** Accepted (2026-04-17). Synthesized from 3 parallel Plan-agent perspectives (wedge-first, foundations-first, TDD-vertical-slice) plus the five accepted ADRs.

## Context

This plan sequences the implementation of the v1 **Must-Have** scope from [`specs/01-functionality-and-moscow.md §7`](specs/01-functionality-and-moscow.md) onto the tech stack and ADR decisions already accepted. It is organised into nine numbered milestones (M0–M8). Each milestone:

- States a single, demoable outcome.
- Lists entry and exit criteria. Exit criteria are **concrete user-visible behaviour**, not "tests pass".
- Names the specific files touched (paths under `src/langusta/` per `specs/02-tech-stack-and-architecture.md §10`).
- Names the tests written **first**, before the implementation.
- Declares explicit deferrals to named later milestones.

Three **structural decisions** from the council synthesis:

1. **Invariants are encoded before they can be violated.** Schema-level triggers, CI lints, and pure-function provenance land in M0–M1, before any scanner or TUI code. This is the foundations-first lens's load-bearing contribution.
2. **The wedge demo arrives at M2**, not at M5. An ICMP+ARP scanner populating a real TUI inventory is the 30-second moment the product hinges on; deferring it past M2 is a motivation and adoption risk we decline. This is the wedge-first lens's load-bearing contribution.
3. **Every milestone lands test-first, with Hypothesis and Textual snapshot tests from M1 onward.** CI structural rails (boundary lints, snapshot-diff review, migration-chain replay) prevent TDD discipline from eroding under deadline pressure. This is the TDD lens's load-bearing contribution.

## Non-negotiable invariants

Enforced from day one. Every milestone that adds a new write path must ship a test that exercises it against each invariant.

| Invariant | Where enforced | How tested |
|---|---|---|
| **Immutable timeline** — entries are append-only; corrections are new entries that reference originals. | SQL `BEFORE UPDATE`/`BEFORE DELETE` triggers on `timeline_entries`, introduced in M0's `001_initial_schema.sql`. DAL has no `update_*` function for timeline. | `tests/unit/db/test_timeline_immutability.py` — UPDATE raises IntegrityError; DELETE raises; `tests/unit/db/test_migrate.py::test_checksum_mismatch_refuses_to_run`. Re-run on every PR touching `db/`. |
| **Scanner proposes, human disposes** — observations that conflict with `manual`-provenance fields go to the review queue, never silently overwrite. | Pure function `core/provenance.py::merge_scan_result(existing, incoming) -> (applied, proposed)`, introduced in M0 as stdlib-only. Every write path — scan (M2), SNMP (M5), import (M6), monitor (never writes asset fields) — must call through it. | Hypothesis property tests in M0 (`test_manual_field_never_overwritten`) extended at every new write path. |
| **No data loss across upgrade** — `uv tool upgrade langusta` never requires "delete your db". A 0.2 backup restored into 0.5 migrates cleanly. | Forward-only migrations from `db/migrations/001_initial_schema.sql`. Migration runner takes a pre-migration backup before every DDL run (ADR-0005 safety rail). | `tests/integration/test_migration_replay.py` — apply-all-from-empty + replay-from-each-tagged-snapshot-to-latest. CI matrix re-runs on every PR touching `db/migrations/`. |

See [`adr/0001-data-layer-orm-choice.md`](adr/0001-data-layer-orm-choice.md), [`adr/0002-process-architecture.md`](adr/0002-process-architecture.md), and [`adr/0005-schema-migration-discipline.md`](adr/0005-schema-migration-discipline.md) for the decisions that bind these.

---

## Milestones

### M0 — Foundations, invariants-as-code, CI spine

**Outcome:** `uv run langusta init` creates `~/.langusta/db.sqlite` with WAL, triggers, and provenance rules active. CI matrix green on Linux + macOS. Boundary lints enforce the architecture.

- **Entry:** ADRs 0001–0005 accepted; no code yet.
- **Exit:**
  - `uv run langusta init` is idempotent and creates the DB at mode 0600 with `PRAGMA user_version=1`.
  - `sqlite3 ~/.langusta/db.sqlite "UPDATE timeline_entries SET body='x' WHERE 1=1"` returns a trigger error.
  - `uv run pytest` green on ubuntu-latest + macos-latest.
  - `scripts/lint_boundaries.py` fails CI for raw SQL outside `db/`, for `if sys.platform` outside `platform/`, or for third-party imports inside `core/`.
- **Files:**
  - `pyproject.toml`, `uv.lock`, `ruff.toml`, `.github/workflows/ci.yml`, `CONTRIBUTING.md`
  - `src/langusta/__init__.py`, `src/langusta/__main__.py`, `src/langusta/cli.py` (Typer root + `init`)
  - `src/langusta/db/connection.py` — single `connect()` helper applying WAL + synchronous=NORMAL + foreign_keys=ON + busy_timeout=5000 + temp_store=MEMORY (spec §3)
  - `src/langusta/db/migrate.py` — ~100-line runner, `PRAGMA user_version`, `_migrations` table, pre-migration backup via online-backup API (ADR-0005)
  - `src/langusta/db/migrations/001_initial_schema.sql` — **full v1 schema** (assets, mac_addresses, timeline_entries with immutability triggers, field_provenance, proposed_changes, review_queue, scans, credentials, monitoring_checks, check_results, tags, asset_tags, meta, _migrations). Schema is shipped as migrations only — no parallel `schema.sql` baseline.
  - `src/langusta/core/models.py` — `Asset`, `MacAddress`, `TimelineEntry`, `ProposedChange`, `CheckResult`, `Provenance` as stdlib dataclasses
  - `src/langusta/core/provenance.py` — `merge_scan_result(existing, incoming) -> (applied_fields, proposed_changes)` pure function
  - `src/langusta/platform/base.py` — `PlatformBackend` Protocol + `NotImplementedCapability` exception
  - `src/langusta/platform/{linux,macos,windows}.py` — Linux + macOS stubs with the minimal `arp_table()` + `enforce_private()`; Windows raises `NotImplementedCapability`
  - `scripts/lint_boundaries.py`
- **Tests written first:**
  - `tests/unit/db/test_migrate.py::test_fresh_db_applies_all_migrations`
  - `tests/unit/db/test_migrate.py::test_migration_writes_pre_migration_backup`
  - `tests/unit/db/test_migrate.py::test_checksum_mismatch_refuses_to_run`
  - `tests/unit/db/test_connection.py::test_wal_and_pragmas_applied`
  - `tests/unit/db/test_timeline_immutability.py::test_update_raises` / `test_delete_raises`
  - `tests/unit/core/test_provenance.py::test_manual_field_never_overwritten` (Hypothesis property)
  - `tests/unit/core/test_provenance.py::test_output_never_loses_a_proposed_change` (Hypothesis)
  - `tests/unit/test_core_is_stdlib_only.py` — imports under `src/langusta/core/` touch only stdlib
  - `tests/unit/platform/test_windows_stub_raises.py`
- **Deferrals:** everything user-visible (M1+). No TUI, no scanner, no monitor, no credentials.

### M1 — Manual asset vertical slice + TUI shell + identity resolver v1

**Outcome:** `langusta add` + `langusta ui` — a user can create an asset manually and see it in a Textual inventory screen; identity resolution exists as a pure function with Hypothesis property tests.

- **Entry:** M0 exit.
- **Exit:**
  - `langusta add --hostname router --ip 192.168.1.1 --mac aa:bb:cc:dd:ee:ff` inserts an asset; all three fields carry `manual` provenance with the current timestamp.
  - `langusta list` prints the row; `langusta ui` shows a Textual inventory DataTable with the row.
  - Textual snapshot tests exist for `inventory_empty` and `inventory_one_asset` screens.
  - Hypothesis property tests for `core/identity.py` pass 200+ examples without failure.
- **Files:**
  - `src/langusta/core/identity.py` — `resolve(candidate, existing) -> Resolution` with confidence score, returns `Insert | Update | Ambiguous`
  - `src/langusta/db/assets.py` — `insert_manual`, `list_all`, `get_by_id`
  - `src/langusta/cli.py` — `add`, `list`, `ui`
  - `src/langusta/tui/app.py` — Textual App subclass, Screen stack, declarative keybindings
  - `src/langusta/tui/screens/inventory.py` — DataTable bound to `db.assets.list_all()`
  - `src/langusta/tui/styles.tcss`
  - `tests/strategies.py` — shared Hypothesis strategies (`assets_with_ambiguous_macs`, `populated_inventories`)
- **Tests written first:**
  - `tests/property/test_identity_resolver.py::test_resolution_is_deterministic`
  - `tests/property/test_identity_resolver.py::test_resolution_never_loses_a_mac`
  - `tests/property/test_identity_resolver.py::test_no_auto_merge_when_ambiguous` (the Lansweeper-failure invariant from spec §4)
  - `tests/unit/core/test_identity.py::test_mac_exact_match_returns_existing`
  - `tests/unit/core/test_identity.py::test_hostname_match_but_different_mac_returns_ambiguous`
  - `tests/unit/db/test_assets.py::test_insert_manual_roundtrip`
  - `tests/snapshots/test_inventory_screen.py::test_inventory_empty`
  - `tests/snapshots/test_inventory_screen.py::test_inventory_one_asset`
  - `tests/integration/test_cli_add_list.py::test_add_then_list_roundtrip`
- **Deferrals:** scanner (M2), asset detail (M3), search (M4), SNMP (M5).

### M2 — ICMP + ARP scanner writes through identity resolver (THE WEDGE)

**Outcome:** `langusta scan 192.168.1.0/24` populates the inventory with real devices from a real LAN in under a minute. The 30-second magic moment.

- **Entry:** M1 exit.
- **Exit:**
  - On a real /24, `langusta scan` finds ≥5 hosts in <60s, prints "Found N devices in M seconds", then `langusta ui` shows them with `first_seen` and `last_seen` timestamps.
  - Re-running `langusta scan` over the same range: asset count unchanged; `last_seen` updated; `first_seen` unchanged; **no human-set field ever mutated** (property-tested).
  - A seeded ambiguity fixture produces exactly one `proposed_changes` row visible via `langusta review` (CLI for now; screen comes in M4).
  - Hypothesis property `test_scanner_never_overwrites_manual_field` passes on every combination of prior state × scan observation.
- **Files:**
  - `src/langusta/scan/icmp.py` — wrap `icmplib.async_multiping(privileged=False)`
  - `src/langusta/scan/arp.py` — calls `PlatformBackend.arp_table()`
  - `src/langusta/scan/orchestrator.py` — runs discovery stages concurrently, merges via `core/provenance.merge_scan_result`, writes via `TimelineWriter`
  - `src/langusta/core/timeline_writer.py` — the **single write path** for scan results; atomically writes asset upserts + field_provenance + timeline diffs + proposed_changes in one transaction
  - `src/langusta/db/scans.py` — scan record lifecycle
  - `src/langusta/db/proposed_changes.py` — list/accept/reject DAL
  - `src/langusta/cli.py` — `scan [subnet]`, `review` (CLI for v1 of the review flow)
  - `src/langusta/platform/{linux,macos}.py` — real `arp_table()` implementations (parse `ip neigh` / `arp -a`)
- **Tests written first:**
  - `tests/property/test_identity_resolver.py::test_rescan_is_idempotent`
  - `tests/property/test_identity_resolver.py::test_scanner_never_overwrites_manual_field`
  - `tests/unit/scan/test_arp_parsing.py::test_parse_linux_ip_neigh` (golden-fixture)
  - `tests/unit/scan/test_arp_parsing.py::test_parse_macos_arp_a`
  - `tests/unit/scan/test_arp_parsing.py::test_parse_macos_14_format_drift`
  - `tests/unit/scan/test_icmp.py::test_mocked_multiping_returns_expected_hosts`
  - `tests/integration/test_scan_loopback.py::test_scan_finds_loopback_and_rescan_only_updates_last_seen`
  - `tests/integration/test_scan_proposed_changes.py::test_conflicting_rdns_observation_lands_in_review`
- **Deferrals:** TCP port probe, rDNS, OUI (M3); mDNS, universal search, review UX polish (M4); SNMP (M5).

### M3 — Asset detail + immutable timeline + scanner breadth (rDNS + TCP + OUI + mDNS)

**Outcome:** Opening an asset shows a timeline with scan events and editable human fields; journal entries land as immutable rows; scanner enriches hosts with vendor (OUI), hostname (rDNS), open ports (TCP), and .local names (mDNS).

- **Entry:** M2 exit.
- **Exit:**
  - After a scan, selecting any host in the TUI opens a detail screen showing: hostname (from rDNS or human edit), MAC (ARP), vendor (OUI), open ports (TCP top-100), and a chronological timeline with scan-diff entries.
  - Press `n` → markdown journal editor opens → write "Replaced PSU" → entry lands at top of timeline, timestamped, immutable. Attempting to edit a prior entry is not offered in UI and raises at DAL if called directly.
  - mDNS/Bonjour scan resolves `.local` names for supported devices.
  - Timeline renders scan-diff entries ("IP changed 10.0.0.5 → 10.0.0.8", "New open port 443") as distinct row types.
- **Files:**
  - `src/langusta/db/migrations/002_fts5_and_oui.sql` — FTS5 virtual table mirroring searchable fields
  - `src/langusta/db/timeline.py` — insert-only DAL with `append_entry` and `append_correction_of(entry_id, body)`
  - `src/langusta/scan/tcp.py` — stdlib `asyncio.open_connection`, top-100 ports configurable
  - `src/langusta/scan/rdns.py` — `socket.gethostbyaddr` behind `asyncio.wait_for`
  - `src/langusta/scan/mdns.py` — thin wrapper around `zeroconf`
  - `src/langusta/scan/oui.py` + packaged DB at `src/langusta/data/oui.csv` (+ `langusta update-oui` later)
  - `src/langusta/tui/screens/asset_detail.py`
  - `src/langusta/tui/widgets/{timeline_view,provenance_marker,journal_editor}.py`
- **Tests written first:**
  - `tests/unit/db/test_timeline.py::test_append_entry_returns_id_and_ts`
  - `tests/unit/db/test_timeline.py::test_correction_references_original`
  - `tests/unit/scan/test_oui.py::test_prefix_resolves_to_vendor`
  - `tests/property/test_timeline.py::test_entries_ordered_within_ms_tiebreaker`
  - `tests/integration/test_scan_writes_timeline_diff.py::test_ip_change_writes_entry`
  - `tests/integration/test_scan_writes_timeline_diff.py::test_new_open_port_writes_entry`
  - `tests/snapshots/test_asset_detail_screen.py::{test_empty_timeline, test_with_scan_and_note, test_with_correction_entry}`
- **Deferrals:** universal-search screen + review-queue UX (M4); SNMP enrichment (M5); monitoring events on timeline (M7).

### M4 — Universal search + review-queue screen

**Outcome:** `/` from any screen opens fuzzy search across hostname, IP, MAC, description, and notes. Proposed changes land in a visual review queue where the user approves / rejects / edits each one.

- **Entry:** M3 exit.
- **Exit:**
  - Press `/` → type `recep` → a seeded "reception switch" asset ranks in top 3 within 200ms on a 500-asset DB.
  - Manually edit asset #7's hostname → rescan a conflicting observation → the change appears in the review queue with [Accept / Reject / Edit] buttons, *not* in the asset. Accepting flips provenance to `scanned` and writes a timeline disposition entry.
  - Review queue screen snapshot covers empty, one-item, and many-items states.
- **Files:**
  - `src/langusta/db/search.py` — FTS5 wrapper with ranking
  - `src/langusta/db/review.py` — list / accept / reject / edit_override
  - `src/langusta/core/identity.py` — extended composite identity + confidence scoring refinement
  - `src/langusta/tui/screens/search.py`
  - `src/langusta/tui/screens/review_queue.py`
- **Tests written first:**
  - `tests/property/test_search.py::test_search_finds_asset_by_any_field`
  - `tests/property/test_search.py::test_search_is_case_insensitive`
  - `tests/unit/db/test_review.py::test_accept_flips_provenance`
  - `tests/unit/db/test_review.py::test_reject_does_not_modify_asset`
  - `tests/unit/db/test_review.py::test_edit_override_writes_manual_provenance`
  - `tests/snapshots/test_review_queue_screen.py::{test_empty, test_one_item, test_many_items}`
  - `tests/snapshots/test_search_screen.py::{test_empty, test_one_hit, test_many_hits_ranking}`
- **Deferrals:** timeline-content full-text search (Should-Have, post-v1 — FTS5 mirrors text; UI polish deferred); bulk actions (post-v1); tag filters (post-v1).

### M5 — Credential vault + SNMP v2c behind `SnmpClient` interface

**Outcome:** User stores an SNMP v2c community (encrypted at rest); opting a scan into SNMP enriches detected hosts with sysDescr + interface table; hosts that don't answer are marked `snmp: unavailable` without failing the scan.

- **Entry:** M4 exit.
- **Exit:**
  - `langusta init` prompts for master password (Argon2id, ≥12 chars, ~500ms KDF).
  - `langusta cred add --label office-ro --kind snmp_v2c` stores ciphertext; `langusta cred list` shows only `id | label | kind`.
  - `langusta scan --snmp 192.168.1.0/24` enriches responding hosts; `asset_detail` shows `sysDescr` and `snmp:ok` badge; unresponsive hosts show `snmp:unavailable`; no scan ever fails because SNMP didn't answer (ADR-0003).
  - Credentials never appear in logs, Textual snapshot output, or default `langusta export` output (test enforces via log scraping).
  - `~/.langusta/db.sqlite` mode `0600`; `~/.langusta/backups/` mode `0700` (via `PlatformBackend.enforce_private`).
- **Files:**
  - `src/langusta/crypto/vault.py` — AES-256-GCM envelope
  - `src/langusta/crypto/kdf.py` — Argon2id parameters tuned to target device
  - `src/langusta/db/migrations/003_credentials_and_snmp_fields.sql`
  - `src/langusta/db/credentials.py`
  - `src/langusta/scan/snmp/client.py` — `SnmpClient` Protocol (ADR-0003)
  - `src/langusta/scan/snmp/pysnmp_backend.py` — only backend shipped in v1
  - `src/langusta/scan/snmp/transcript_backend.py` — test-only, replays recorded PDUs
  - `tests/fixtures/snmp_transcripts/{cisco_ios.json, mikrotik.json, unreachable.json}`
  - `src/langusta/cli.py` — `cred {add, list, rm}`, extend `scan` with `--snmp`
  - `src/langusta/tui/screens/unlock.py` — master-password prompt on TUI launch
- **Tests written first:**
  - `tests/property/test_vault.py::test_encrypt_decrypt_any_plaintext_roundtrip`
  - `tests/unit/crypto/test_vault.py::test_wrong_password_raises_invalid_tag`
  - `tests/unit/crypto/test_kdf.py::test_argon2_params_meet_security_floor`
  - `tests/unit/scan/snmp/test_client_contract.py::test_transcript_backend_replays_get`
  - `tests/unit/scan/snmp/test_client_contract.py::test_transcript_backend_replays_bulk_walk`
  - `tests/integration/test_snmp_unavailable_does_not_fail_scan.py`
  - `tests/unit/test_log_hygiene.py::test_credential_never_in_logs` — scrapes caplog across all modules
  - `tests/integration/test_permissions.py::test_db_file_mode_0600`
- **Deferrals:** SNMP v3 authPriv (Should-Have, post-v1); `NetSnmpSubprocessBackend` (reserved, ADR-0003, ship when a real user bug requires it); external secret stores (v1.5 per spec §8).

### M6 — Backups + JSON/YAML export/import

**Outcome:** Automatic backups run on scan completion + daily timer with 1h dedupe + 30-retention. `langusta export --format json > dump.json` + `langusta import dump.json` round-trips cleanly on a fresh DB. Restore-from-old-backup is a CI contract.

- **Entry:** M5 exit.
- **Exit:**
  - Three scans in quick succession produce one deduped backup (1h window); daily timer produces another; retention prunes to 30; each backup passes `PRAGMA integrity_check`.
  - `langusta export --format json > dump.json` → `langusta import dump.json` on a clean install produces identical `SELECT COUNT(*)` across every table and identical timeline ordering.
  - `--include-secrets` export re-encrypts credentials with a user-provided export password and never writes plaintext (log scraper enforces).
  - CI integration test restores a seeded 0.1 snapshot into the current binary, applies all migrations forward, and survives a full scan + timeline round-trip (ADR-0005 contract).
- **Files:**
  - `src/langusta/backup.py` — online-backup API, retention, dedupe window, integrity check
  - `src/langusta/export.py` — JSON + YAML (uses stdlib json + PyYAML already transitive), schema-versioned envelope
  - `src/langusta/db/migrations/004_export_metadata.sql` (if needed for export version pinning on roundtrip)
  - `src/langusta/cli.py` — `export`, `import`, `backup {now, verify, list, prune}`
  - `tests/fixtures/snapshots/0.1.0/db.sqlite` — committed seeded snapshot for replay-forward tests
- **Tests written first:**
  - `tests/unit/test_backup.py::test_uses_online_api_not_raw_copy`
  - `tests/unit/test_backup.py::test_dedup_within_1h_window`
  - `tests/unit/test_backup.py::test_retention_prunes_to_n`
  - `tests/unit/test_backup.py::test_integrity_check_runs_post_backup`
  - `tests/property/test_export_import.py::test_roundtrip_preserves_assets` (Hypothesis: random populated DBs survive round-trip)
  - `tests/property/test_export_import.py::test_roundtrip_preserves_timeline_order_and_immutability`
  - `tests/integration/test_export_omits_secrets_by_default.py`
  - `tests/integration/test_migration_replay.py::test_restore_0_1_snapshot_into_current`
- **Deferrals:** Lansweeper CSV + NetBox API importers (Should-Have, post-v1 — they are the migration on-ramp for competitor users); CSV export (post-v1).

### M7 — Monitor daemon (ICMP + TCP + HTTP) with timeline events

**Outcome:** `langusta monitor start` runs as a detached process surviving TUI exit, executes scheduled ICMP/TCP/HTTP checks, and writes failure/recovery events directly onto each asset's timeline. The TUI footer shows daemon health.

- **Entry:** M6 exit. Pre-migration backups must exist before we let a second process write.
- **Exit:**
  - Promote an asset to monitored (ICMP every 60s + HTTP :443 every 5min); disconnect target → within one cycle a `monitoring.failure` event appears on its timeline and in `~/.langusta/notifications.log`. Restore → `monitoring.recovery` appears.
  - Kill the TUI → daemon keeps running. Kill the daemon → TUI footer shows `⚠ daemon stale` within 2 minutes.
  - Schema-version mismatch between binary and DB: daemon refuses to start with a clear error (ADR-0002 + ADR-0005 cross-process coordination).
  - APScheduler uses `coalesce=True`, explicit `misfire_grace_time`, pinned tzdata (per ADR-0002 ecosystem warning).
- **Files:**
  - `src/langusta/db/migrations/005_monitoring.sql` — monitoring_checks, check_results, `meta.daemon_heartbeat`
  - `src/langusta/monitor/scheduler.py` — APScheduler with SQLite job store
  - `src/langusta/monitor/worker.py` — detachment (start_new_session), exclusive DB lock, heartbeat loop
  - `src/langusta/monitor/checks/{base,icmp,tcp,http}.py` — each implements `Check` Protocol (spec §7)
  - `src/langusta/cli.py` — `monitor {start, stop, status, tail, run, install-service}`
  - `src/langusta/tui/widgets/footer.py` — reads `meta.daemon_heartbeat`
  - `src/langusta/tui/screens/monitor_config.py` — enable checks, set intervals
- **Tests written first:**
  - `tests/unit/monitor/test_check_contract.py::test_icmp_check_returns_result`
  - `tests/unit/monitor/test_check_contract.py::test_http_check_detects_404`
  - `tests/unit/monitor/test_scheduler.py::test_coalesce_true_and_misfire_grace_set`
  - `tests/integration/test_daemon_lifecycle.py::test_daemon_refuses_schema_mismatch`
  - `tests/integration/test_daemon_lifecycle.py::test_daemon_writes_heartbeat`
  - `tests/integration/test_daemon_lifecycle.py::test_daemon_survives_tui_exit`
  - `tests/integration/test_monitoring_writes_timeline.py::test_failed_check_appears_in_asset_timeline` (the cross-pillar link from spec §4 Pillar C)
  - `tests/perf/test_write_contention.py::test_250_assets_60s_checks_no_lock_errors` (mitigates risk R2)
  - `tests/snapshots/test_monitor_config_screen.py`
- **Deferrals:** SSH-command check (documented foot-gun; Should-Have, post-v1 — explicit-enable toggle); SNMP-OID check (post-v1); webhook + SMTP notifications (Should-Have, post-v1); real systemd unit / launchd plist generation (stub in M7, polish in M8).

### M8 — v1 release hardening

**Outcome:** `uv tool install langusta` on a clean Linux or macOS machine → `langusta init && langusta scan && langusta monitor start` works end-to-end. README with install tabs (Linux/macOS + WSL2). `langusta 0.1.0` shipped to PyPI.

- **Entry:** M7 exit; zero open P0 bugs against the three invariants.
- **Exit:**
  - Fresh ubuntu-latest and macos-latest CI runners execute the install-init-scan-monitor walkthrough and terminate green.
  - `uv tool upgrade langusta` from a seeded 0.0.x-rc build to 0.1.0 preserves a DB that has real timeline entries (migration rehearsal per ADR-0005).
  - `langusta monitor install-service` lays down a valid systemd user unit on Linux and a valid launchd plist on macOS (golden-file lint passes).
  - README install section has Linux/macOS one-liner via `uv` and a WSL2 one-liner for Windows users. No native PowerShell instructions (ADR-0004).
  - `CONTRIBUTING.md` documents the `platform: windows-native = wontfix for v1` triage policy.
- **Files:**
  - `src/langusta/platform/linux.py` + `macos.py` — real `daemon_install_recipe()` returning an `InstallRecipe` dataclass (no branching in `core/`)
  - `README.md`, `docs/install.md`, `docs/upgrading.md`, `docs/daemon.md`
  - `pyproject.toml` — classifiers, entrypoint, version bumped to 0.1.0
  - `.github/workflows/release.yml` + `smoke.yml`
  - `CHANGELOG.md` — 0.1.0 entry
- **Tests written first:**
  - `tests/integration/test_fresh_install.py::test_uv_tool_install_then_init_then_scan` (`@pytest.mark.slow`, CI-only)
  - `tests/unit/platform/test_systemd_unit_rendering.py::test_rendered_unit_validates`
  - `tests/unit/platform/test_launchd_plist_rendering.py::test_rendered_plist_is_valid_xml`
  - `tests/unit/test_first_run.py::test_scanner_off_by_default_until_user_opts_in` (spec §16 security default)
  - Full-suite snapshot-diff review with human sign-off
- **Deferrals:** everything in Should-Have / Could-Have not yet shipped → post-v1 backlog below.

---

## Risk mapping (spec §9)

| Risk | Mitigation milestone(s) |
|---|---|
| **R1 — TUI-only commercial narrowness.** | Not engineering-level. Structural mitigation: foundations (`core/`, DAL, `TimelineWriter`, `PlatformBackend`) are UI-agnostic from M0, so a future web UI reuses everything without rewrites. Revisit at 6-month adoption checkpoint post-M8. |
| **R2 — SQLite + concurrent monitoring write contention.** | M0 (WAL + pragmas from day one). M7 (single scheduler owner in daemon; exclusive DB lock; `tests/perf/test_write_contention.py::test_250_assets_60s_checks_no_lock_errors`). M6 (backup while in use via online API). |
| **R3 — Scanner accuracy / identity resolution.** | M0 (`core/provenance.merge_scan_result` Hypothesis-tested). M1 (`core/identity.resolve` Hypothesis suite — the largest test investment in the plan). M2 (`test_scanner_never_overwrites_manual_field` + `test_rescan_is_idempotent`). M4 (review queue is the human check; Hypothesis catches silent merges). M5 (SNMP enrichment goes through same merge path). |
| **R4 — Institutional memory slow to demonstrate.** | M3 ships the timeline as the primary asset view with ergonomic journal-entry capture — users experience the memory-value loop during their first session, not at month 6. M7 makes monitoring events first-class timeline citizens (the cross-pillar link). |
| **R5 — "No AI" defensibility.** | M0 CI dependency allowlist prevents accidental LLM dep addition. Policy, not code. |

---

## Post-v1 backlog (Should-Have from MoSCoW, parked)

In rough priority order, once v1.0 is in users' hands:

1. **Lansweeper CSV + NetBox API import.** The migration on-ramp — highest-leverage post-v1 item; targets the biggest user-flight populations (spec §7).
2. **SNMP v3 authPriv.** Protocol seam ready at M5; afternoon of engine-ID + USM tuning per ADR-0003.
3. **Webhook + SMTP notifications.** Extends M7's `notifications.log`.
4. **Global search over timeline content.** FTS5 index exists from M4; UI polish deferred.
5. **SSH-based config backup** for Cisco IOS / Juniper / MikroTik / FortiGate. Paramiko or asyncssh.
6. **LLDP/CDP neighbour ingestion** and L2 topology derivation.
7. **Tag system + bulk actions.** Schema is already present from M0.
8. **External secret-store integration** (1Password CLI, Bitwarden CLI, HashiCorp Vault).
9. **Keyboard-customisable vim-style keybindings.**
10. **SSH-command and SNMP-OID monitor check types.**
11. **Real `langusta monitor install-service` polish** (beyond M8 stub).

Could-Have / Won't-Have items remain per MoSCoW (`specs/01-functionality-and-moscow.md §7`).

**Revisit triggers:**

- Windows-native support (ADR-0004): if >5% of bugs are tagged `platform: windows-native`.
- SQLAlchemy + Alembic (ADR-0001): if schema passes 15 tables OR a second maintainer joins OR hand-rolled migrations produce bugs at a measurable rate.
- `NetSnmpSubprocessBackend` (ADR-0003): ship when the first real vendor-agent bug report arrives; don't pre-build.

---

## Biggest risk of this synthesized sequencing

**The compressed M2 demo is honest only if M3 follows within a short, committed window.** M2's wedge (ICMP + ARP only) produces rows that are IP + timestamp + MAC + vendor-by-OUI — useful, but thin. The full 30-second-magic moment from the README crystallises at M3 once rDNS hostnames, TCP ports, and mDNS names fill the table. If M2 ships as `0.0.1` and M3 slips, early evaluators see a thin demo and churn before the memory-value pillar ever loads. Mitigation: **M2 and M3 ship as one release (`0.1.0-rc1`)**, not as two separate pre-releases. Tag 0.0.1 internally as a developer checkpoint only, not a public announcement. The "wedge" in the marketing sense is M2+M3 bundled; the engineering milestone split exists for CI and review discipline, not for external communication.

Secondary risk: the test-suite structural rails (boundary lints, snapshot-diff review, Hypothesis seed budgets) are themselves code that must be maintained. If they become flaky or noisy, they will be disabled — and the TDD discipline collapses silently. Treat CI lints as production code, not infrastructure scripts: every lint has a test proving it fails on the right violations and passes on clean code.
