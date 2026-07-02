# IMPROVEMENTS & FEATURE PROPOSALS — LANgusta

**Source:** AI Council analysis (Backend Architect, Network & Systems Engineer, UX & Product Engineer, DevOps & Reliability Engineer, Security & Data Engineer) — 6 specialists, 60+ proposals, cross-referenced and deduplicated.

**Date:** 2026-07-02

---

## Executive summary

LANgusta's v1 foundation is solid — the three invariants hold, the architecture is clean, and the codebase is well-tested. The council identified **60+ proposals** across five domains. After cross-referencing, these collapse to **34 unique proposals** organized in four priority tiers. The top tier (11 proposals, all Small effort) would transform the product from "functional alpha" to "genuinely useful daily tool" with minimal investment.

The five highest-leverage themes, agreed on by multiple council members independently:

1. **Operational hygiene** — `check_results` retention, WAL checkpointing, log rotation, `langusta doctor` (flagged by 3 of 5 members)
2. **Discovery depth** — SNMP sysName/ifTable, banner grabbing, TLS cert checks (fills the biggest functional gaps)
3. **Distribution & adoption** — PyPI publication, Docker, push notifications (removes the #1 adoption barrier)
4. **TUI/CLI UX** — dashboard, JSON output, tagging, asset retirement (makes the tool usable, not just functional)
5. **Security hardening** — backup encryption, key rotation, external secret stores (aligns with the "trust product" thesis)

---

## Cross-council consensus (proposals flagged by 2+ members)

These are the highest-confidence items — multiple specialists independently reached the same conclusion.

| Proposal | Members | Effort | Impact |
|----------|---------|--------|--------|
| **check_results retention + pruning** | Backend #2, DevOps #3, Security P1 | S | High |
| **SSH config backup for network gear** | Backend #7, Network #7 | M | High |
| **Tagging system** | Backend #4, UX #9 | M | High |
| **Dashboard / overview screen** | UX #2, DevOps #1 (doctor) | S–M | High |
| **WAL checkpoint scheduling** | Backend #8, DevOps #11 | S | Med |
| **Reporting & analytics** | Security P8, UX #5 | M | Med–High |
| **PyPI publication** | DevOps #4 | S–M | High |
| **Push notifications (ntfy/Gotify/Pushover)** | DevOps #6 | S | High |
| **Docker headless daemon** | DevOps #7 | M | High |

---

## Tier 1 — Ship First (Small effort, high impact)

### 1.1 `langusta doctor` diagnostics command
**Effort: S · Impact: High** · *DevOps*

A single command that checks: schema version vs binary, heartbeat freshness, PID-file state, DB + WAL file size, `check_results` row count, backup age + integrity, ICMP capability, notification sink reachability, log file sizes. Prints `[PASS]/[FAIL]` and exits 0/1 for CI/pre-upgrade scripting. Surfaces every other gap in one command.

**Files:** New `doctor` Typer subcommand; reuses existing `assert_schema_current`, `mon_dal.is_heartbeat_stale`, `backup.verify`, `daemon_control.read_pid_file`.

---

### 1.2 `check_results` retention + daily rollup
**Effort: S · Impact: High** · *Backend + DevOps + Security*

At 250 assets × 5 checks × 60s intervals, `check_results` grows ~18M rows/year. Add `prune_check_results(conn, retain_days=30)` that deletes rows older than the cutoff (with a daily rollup into `check_results_summary` for historical uptime queries). Call once per N daemon cycles.

**Files:** `db/monitoring.py` (new `prune_check_results`), migration `008` (rollup table), `monitor/runner.py` or `cli.py` daemon loop (periodic call).

---

### 1.3 SNMP sysName enrichment
**Effort: S · Impact: High** · *Network*

The scanner polls only `sysDescr` (OID `1.3.6.1.2.1.1.1.0`). Adding `sysName` (`1.3.6.1.2.1.1.5.0`) — the device's configured hostname — dramatically improves name resolution on managed network gear where rDNS is absent. One additional SNMP GET per host, reusing the existing `_snmp_one` closure.

**Files:** `scan/snmp/client.py` (add `SYS_NAME_OID`), `scan/orchestrator.py` (extend `_snmp_one` to poll both OIDs).

---

### 1.4 TLS certificate expiry monitor check
**Effort: S · Impact: High** · *Network*

A `cert_expiry` check kind using stdlib `ssl` — opens a TLS connection, extracts `notAfter` from the peer cert, fails when `days_remaining < threshold`. Zero new dependencies. Catches the single most preventable outage class.

**Files:** New `monitor/checks/cert_expiry.py`, migration `008` (add to `VALID_KINDS` CHECK), `monitor/runner.py` (register in `_default_registry`).

---

### 1.5 CLI `--json` output mode
**Effort: S · Impact: High** · *UX*

Add `--output json|text` global option. When `json`, emit `json.dumps([dataclasses.asdict(r) for r in rows])` instead of text tables. Unlocks scripting, piping to `jq`, feeding external dashboards. The DALs already return clean dataclasses.

**Files:** `cli.py` (global callback option, render-mode switch in each list command).

---

### 1.6 Shell completion
**Effort: S · Impact: Med** · *UX*

Flip `add_completion=True` on the Typer app (currently `False`). Users get bash/zsh/fish tab-completion for free — 30+ subcommands across nested groups.

**Files:** `cli.py:63` (one-line change), docs update.

---

### 1.7 Push notification sinks (ntfy / Gotify / Pushover)
**Effort: S · Impact: High** · *DevOps*

The target audience (homelab/MSP sysadmins) wants phone buzz on failures. Each is a thin `httpx` POST reusing the existing `_http_post` injection point. ntfy: `POST https://ntfy.sh/<topic>`. Gotify: `POST <server>/message?token=<app>`. Pushover: `POST https://api.pushover.net/1/messages.json`.

**Files:** `monitor/notifications.py` (extend `VALID_KINDS` + `_SENDERS`), `cli.py` (`notify add-ntfy/add-gotify/add-pushover`).

---

### 1.8 WAL checkpoint scheduling
**Effort: S · Impact: Med** · *Backend + DevOps*

WAL mode is set but `wal_checkpoint` is never called. Under heavy write load + a long-lived TUI reader, the `-wal` file grows unbounded. Add `PRAGMA wal_checkpoint(PASSIVE)` at the end of each monitor cycle and scan.

**Files:** `db/connection.py` (new `checkpoint()` function), `monitor/runner.py` + `scan/orchestrator.py` (call after write-heavy operations).

---

### 1.9 Log rotation for `monitor.log` + `notifications.log`
**Effort: S · Impact: Med** · *DevOps*

Both files append forever (ADR-0006 open follow-up). Swap to `logging.handlers.RotatingFileHandler` (10MB × 5 backups). Keep rotation in-process so it works identically under systemd, launchd, and `monitor start`.

**Files:** `cli.py` daemon loop (replace `typer.echo` with `logging`), `monitor/notifications.py` (`_append_log_line` size check + rotate).

---

### 1.10 PyPI publication + GitHub Release workflow
**Effort: S–M · Impact: High** · *DevOps*

`pyproject.toml` is complete; the `git+` install is the #1 adoption barrier. Add `.github/workflows/release.yml` with trusted-publishing OIDC (no PyPI token). Once on PyPI: `uv tool install langusta` (no URL), Homebrew/AUR/Nix unblock.

**Files:** `.github/workflows/release.yml`, `pyproject.toml` (bump `Development Status` to `4 - Beta`), `docs/install.md` update.

---

### 1.11 Banner grabbing on open ports
**Effort: S · Impact: Med** · *Network*

The TCP probe connects and immediately closes — it knows port 22 is open but not that it's OpenSSH 9.6. For ports in `{21, 22, 25, 110, 143, 3306, ...}` (servers that send first), `reader.read(256)` with a 0.3s timeout captures the banner. Improves `detected_os` and `device_type` accuracy dramatically.

**Files:** `scan/tcp.py` (optional `grab_banner` parameter), `db/writer.py` (`Observation.banners` field, timeline `scan_diff`).

---

## Tier 2 — Core Feature Gaps (Medium effort, high impact)

### 2.1 Tagging system
**Effort: M · Impact: High** · *Backend + UX*

The spec calls for tags in Pillar A and "Should-Have" §7, but zero implementation exists. Tags are the organizational backbone for audit-mode workflows. Migration: `tags` + `asset_tags` tables. CLI: `langusta tag add/rm/list`. TUI: tag column in inventory, tag filter.

**Files:** Migration `009`, new `db/tags.py`, `cli.py` (tag commands), `tui/screens/inventory.py`.

---

### 2.2 Dashboard / overview screen
**Effort: S–M · Impact: High** · *UX*

Replace the bare DataTable landing page with a situational-awareness screen: asset count by source, stale assets (>30d), open review queue items, monitoring up/down counts, recent timeline activity. Answers "what needs my attention?" in one glance.

**Files:** New `tui/screens/dashboard.py`, DAL aggregation queries in `db/assets.py` + `db/monitoring.py`.

---

### 2.3 SSH config backup for network gear
**Effort: M · Impact: High** · *Backend + Network*

Spec "Should-Have": *"SSH-based config backup for Cisco IOS, Juniper, MikroTik, FortiGate."* The entire SSH infrastructure exists (TOFU, auth, client Protocol). Config backup runs vendor-specific commands (`show running-config`, `/export`), stores snapshots, and writes timeline diffs when configs change — directly reinforcing Pillar D (institutional memory).

**Files:** New `backup/runner.py` + `backup/profiles.py`, migration (config snapshots table), CLI `backup-config`.

---

### 2.4 DNS resolution monitor check
**Effort: S · Impact: Med** · *Network*

A `dns` check verifying forward/reverse resolution. Pure stdlib (`socket.getaddrinfo` in `asyncio.to_thread`). Catches silent DNS breakage — a common operational issue.

**Files:** New `monitor/checks/dns.py`, migration `008` (add to `VALID_KINDS`).

---

### 2.5 Inventory sorting + stale-asset highlighting
**Effort: S · Impact: Med** · *UX*

Wire `DataTable.sort()` to header clicks / `s` binding. Compute staleness inline and color stale `last_seen` cells red. Directly serves audit mode.

**Files:** `tui/screens/inventory.py`.

---

### 2.6 Asset retirement / soft-delete
**Effort: M · Impact: High** · *UX*

Assets with timeline entries cannot be deleted (immutability trigger). MSPs need to mark decommissioned hardware as retired without losing history. A `retired_at` column + inventory filter defaulting to `WHERE retired_at IS NULL` solves this cleanly.

**Files:** Migration `008`, `db/assets.py` (`retire`/`unretire`), `cli.py` (`langusta retire`), TUI action.

---

### 2.7 CSV export & inventory report generation
**Effort: M · Impact: High** · *UX + Security*

`langusta export` produces JSON for migration. MSPs need human-readable reports — CSV inventory, stale-asset lists, compliance summaries. A `report` command group with `--format csv|html|json` and filter parameters (vendor, source, criticality, staleness).

**Files:** New `db/reports.py`, `cli.py` (`report_app` Typer group).

---

### 2.8 Config loader (`config.toml`)
**Effort: S · Impact: Med** · *Backend*

`paths.config_path()` exists but nothing reads it. Hardcoded defaults are scattered: `DEFAULT_TOP_PORTS`, `DEFAULT_MAX_CONCURRENCY=32`, `busy_timeout=5000`, `keep=30`. Python 3.11+ ships `tomllib` (stdlib, zero deps). Load `~/.langusta/config.toml` for scan ports, monitor concurrency, backup retention.

**Files:** New `src/langusta/config.py`, wire into `scan/orchestrator.py`, `monitor/runner.py`, `db/backup.py`.

---

### 2.9 Hoist identity resolution out of per-IP loop
**Effort: S · Impact: High** · *Backend*

`apply_scan_observation` calls `list_identities(conn)` (full table scan of assets + MACs) on every observation. For a /24 with 250 hosts, that's O(N²). Hoist the identity set computation to once-per-scan; pass it in. Reduces to O(N).

**Files:** `db/writer.py` (add `identities` parameter), `scan/orchestrator.py` (call `list_identities` once before the loop).

---

### 2.10 SQL-native `list_due()`
**Effort: S · Impact: Med** · *Backend*

`list_due()` loads every enabled check and filters due-ness in Python (parsing ISO timestamps per row per cycle). Replace with a single SQL `WHERE` using `julianday()` arithmetic.

**Files:** `db/monitoring.py` (`list_due` rewrite).

---

## Tier 3 — Strategic Features (Medium-Large effort, high impact)

### 3.1 Docker image + `docker-compose.yml`
**Effort: M · Impact: High** · *DevOps*

Multi-stage `python:3.12-slim` Dockerfile. `VOLUME /data; ENV LANGUSTA_HOME=/data`. Runs `monitor daemon --foreground` with Docker as supervisor. `healthcheck: langusta doctor`. The natural deployment for "always-on monitor on a NAS/NUC."

**Files:** `Dockerfile`, `docker-compose.yml`, `docs/daemon.md` (container deployment section).

---

### 3.2 Master password key rotation / re-encryption
**Effort: M · Impact: High** · *Security*

No `change_password` exists — a compromised master password requires full DB re-init. Add `langusta master rotate`: unlock with old password → decrypt all credentials → re-encrypt with new salt + key → update verifier. Single transaction, pre-rotation backup.

**Files:** `crypto/master_password.py` (new `rotate()`), `cli.py` (`master rotate` command).

---

### 3.3 Backup encryption
**Effort: S · Impact: High** · *Security*

Backups are raw SQLite copies — `assets`, `timeline`, `notification_sinks.config` are cleartext. Add AES-256-GCM encryption with a user-provided passphrase (Argon2id KDF, file-specific salt). Format: `LANG` magic + salt + nonce + ciphertext. Backward compatible (unencrypted if no passphrase set).

**Files:** New `crypto/backup_crypto.py`, `db/backup.py` (encrypt after `src.backup()`).

---

### 3.4 External secret store integration
**Effort: M · Impact: High** · *Security*

Spec v1.5 stretch goal: integrate 1Password CLI / Bitwarden CLI / HashiCorp Vault so LANgusta never persists plaintext at all. The `credentials` row stores a reference (vault path/item ID) instead of ciphertext. Resolution via `subprocess.run(["op", "item", "get", ref, ...])`.

**Files:** New `crypto/secret_providers.py`, migration (add `external_provider` + `external_ref` columns), `db/credentials.py` (route resolution).

---

### 3.5 Nmap XML importer
**Effort: M · Impact: High** · *Security*

Nmap is the most-used discovery tool in the target audience. `langusta import-nmap scan.xml` gives instant migration value. Parse `<host>` elements for IP, MAC, hostname, OS guess, open ports. Routes through `apply_imported_observation` — provenance machinery reused. Stdlib `xml.etree.ElementTree`, no new dependency.

**Files:** New `db/import_nmap.py`, `cli.py` (`import-nmap` command).

---

### 3.6 Statistical anomaly detection
**Effort: M · Impact: High** · *Security*

NOT AI — pure statistical comparisons against historical data. "New port appeared on a known host," "device hasn't been seen in 3 scans," "latency degraded 5× over baseline." Persist open_ports as structured data (`asset_ports` table). Compare during `apply_scan_observation`; write `scan_diff` timeline entries for anomalies.

**Files:** New `core/anomaly.py`, migration `009` (`asset_ports` table), `db/writer.py` (call anomaly checks).

---

### 3.7 SNMP ifTable walking (interface table)
**Effort: M · Impact: High** · *Network*

The `SnmpClient` Protocol only defines `get()` (single OID). Add `walk()` using `nextCmd` to iterate `ifTable` — interface names, MACs, operational status. Enables: multi-MAC binding, device-type detection (switch = many ports), future bandwidth monitoring, and ARP cache walking.

**Files:** `scan/snmp/client.py` (add `walk` to Protocol), `scan/snmp/pysnmp_backend.py` (implement via `nextCmd`), new `scan/snmp/enrich.py`.

---

### 3.8 Search faceted filters
**Effort: M · Impact: Med–High** · *UX*

Extend `search.search()` with optional filter kwargs: vendor, source, criticality, stale_days. Add a filter bar to `SearchScreen` with `Select` widgets. Turns search from "find one asset" into "build a live query."

**Files:** `db/search.py`, `tui/screens/search.py`.

---

### 3.9 Bulk operations (multi-select)
**Effort: M · Impact: High** · *UX*

Enable multi-row selection on the inventory DataTable (`space` to tag). Bottom bar: "3 selected — [M]onitor [T]ag [R]etire [C]lear". CLI: `langusta monitor enable --asset 1,2,3 --kind icmp` or `--asset-file assets.txt`. Reduces 80-step repetitive tasks to one action.

**Files:** `tui/screens/inventory.py`, `cli.py` (accept repeated `--asset` or `--asset-file`).

---

### 3.10 First-run wizard & guided scan
**Effort: M · Impact: High** · *UX*

Spec Flow 1 describes the 30-second magic moment, but `init` just creates the DB and stops. After setup: auto-detect local subnet (parse `ip route`), offer scan, optionally launch TUI. The difference between "magical on first run" and "requires RTFM."

**Files:** `core/net.py` (new `detect_local_subnet()`), `cli.py` (`init` post-setup prompts).

---

### 3.11 `langusta upgrade --check`
**Effort: S · Impact: Med** · *DevOps*

Pre-flight version check: query PyPI JSON API for latest vs installed, display changelog for the version window, preview pending migrations (`--migrate --dry-run`), detect live daemon and print restart hint. Opt-in, no auto-polling (respects no-telemetry).

**Files:** New `upgrade` Typer subcommand.

---

### 3.12 Remote backup sync (rclone / rsync)
**Effort: S · Impact: Med** · *Security + DevOps*

Backups are local-only. A disk failure destroys primary + backups. Add `backup sync --remote rclone:backups/langusta` that ships the newest snapshot to a remote after `backup.write`. Sync failures logged, never block the backup. rclone config is the user's own.

**Files:** `db/backup.py` (new `sync_to_remote`), `cli.py` (`backup sync`).

---

### 3.13 Maintenance windows
**Effort: S · Impact: Med** · *Security*

Suppress alert notifications during planned maintenance. Migration: `maintenance_windows` table. The runner checks active windows before evaluating transitions — results still recorded for history, but notifications suppressed. CLI: `monitor maintenance add <asset> --from --to --reason`.

**Files:** Migration `010`, `db/monitoring.py` (`in_maintenance`), `monitor/runner.py` (guard transitions).

---

### 3.14 Maintenance windows
**Effort: S · Impact: Med** · *Security*

Suppress alert notifications during planned maintenance. Migration: `maintenance_windows` table. The runner checks active windows before evaluating transitions — results still recorded for history, but notifications suppressed. CLI: `monitor maintenance add <asset> --from --to --reason`.

**Files:** Migration `010`, `db/monitoring.py` (`in_maintenance`), `monitor/runner.py` (guard transitions).

---

### 3.15 Reporting & analytics (uptime, MTTR, vendor distribution)
**Effort: M · Impact: Med** · *Security + UX*

The data is present — `check_results` has every outcome, `scans` has host counts, `assets` has vendor/OS. New `db/analytics.py` with SQL aggregation: `uptime_percentage(asset_id, since)`, `mttr_seconds(asset_id, since)`, `vendor_distribution()`, `scan_coverage(since)`. CLI: `langusta report` with `--json` for piping to external tools. Depends on check_results rollup (1.2).

**Files:** New `db/analytics.py`, `cli.py` (`report` command group).

---

### 3.16 Self-health monitoring
**Effort: M · Impact: High** · *DevOps*

The daemon catches per-cycle exceptions and retries silently. A sink failing for a week is invisible. Add `meta` counters for `notif_sink_failures` and `consecutive_check_failures`. Escalation after N consecutive failures. DB-size guard in `doctor`. `monitor status` shows sink health.

**Files:** `db/meta.py` (counters), `monitor/notifications.py` (track failures), `cli.py` (`monitor status` output).

---

### 3.17 AES-GCM AAD row-binding (S-6 remediation)
**Effort: S · Impact: Med** · *Security*

Credentials are encrypted with `associated_data=None` — ciphertext rows are swappable between credentials with DB write access. Bind each envelope to `f"{credential_id}:{kind}"` as AAD so a swap fails with `InvalidTag`.

**Files:** `crypto/vault.py` (add `aad` parameter), `db/credentials.py` (pass AAD).

---

## Tier 4 — Ambitious (Large effort, design now / build later)

### 4.1 LLDP/CDP topology auto-discovery
**Effort: L · Impact: High** · *Network*

Walk LLDP-MIB / CDP-MIB via SNMP to auto-discover L2 neighbor relationships. Enables auto-drawing topology graphs — the single most valuable feature for understanding an inherited network (onboarding use case). Requires `walk()` on `SnmpClient` (3.7), new `topology_edges` table, ASCII/graph renderer.

---

### 4.2 Plugin system for custom checks and scanners
**Effort: M · Impact: Med** · *Backend*

The `Check` Protocol is already `@runtime_checkable` with a clean contract. Use `importlib.metadata` entry points (stdlib). Users define custom checks (e.g., "ping a specific API endpoint and parse JSON") without forking. Major competitive differentiator.

---

### 4.3 Read-only JSON API (Unix socket)
**Effort: M · Impact: Med** · *Backend*

Local Unix-socket HTTP server serving read-only JSON over the existing DAL. Enables Grafana integration, CI checks, MSP reporting — all without violating the "no web UI" thesis. Uses `readonly=True` connections. Auth via Unix socket permissions (0600).

---

### 4.4 SNMP ARP cache walking (remote neighbor discovery)
**Effort: M · Impact: Med** · *Network*

Walk `ipNetToPhysicalTable` on SNMP-responsive routers to discover hosts invisible to the scanner's local ARP table (firewalled, sleeping, on remote VLANs). Transforms "what can I ping" into "what does the infrastructure know about."

---

### 4.5 NetBIOS/LLMNR name resolution
**Effort: S · Impact: Med** · *Network*

Spec §4 lists NetBIOS; no implementation exists. UDP 137 NBSTAT query (~40 bytes crafted binary). Fills the hostname-resolution gap on Windows-heavy networks. Spec says "~50 lines, roll our own."

---

### 4.6 Bandwidth / interface utilization monitoring
**Effort: L · Impact: Med** · *Network*

Poll SNMP `ifHCInOctets`/`ifHCOutOctets` counters at two points in time, compute delta → utilization %. Requires the stateful counter-tracking pattern (breaks current stateless check model). Counter-wrap handling for 32-bit vs 64-bit.

---

### 4.7 Homebrew formula + AUR PKGBUILD + Nix flake
**Effort: M · Impact: Med** · *DevOps*

Blocked on PyPI (1.10). Each is a packaging overlay: `brew install AmigoUK/tap/langusta`, `yay -S langusta`, `nix run github:AmigoUK/LANgusta`. Write-once, mostly-forget.

---

### 4.8 CLI config profiles (multi-site / multi-client)
**Effort: M · Impact: Med** · *UX*

MSPs managing multiple client sites need separate databases. A `--profile` flag maps to `~/.langusta-<profile>/db.sqlite`. `paths.py` is the single chokepoint — everything routes through it. Opens the multi-client use case without multi-tenancy.

---

### 4.9 Monitoring data visualization (uptime sparkline)
**Effort: M · Impact: Med** · *UX*

ASCII sparkline of latency over time + uptime percentage on the asset detail screen. Respectfully extends the spec's monitoring pillar without crossing into "observability platform." Uses Textual `Sparkline` widget or braille rendering.

---

### 4.10 Asset dependencies & relationships
**Effort: M · Impact: Med** · *Security*

No way to express "switch-A is the uplink for server-B" or "VM-host-1 hosts vm-1 through vm-5." Migration: `asset_relationships` table with `rel_type` (uplink, depends_on, hosts, lldp_neighbor). Enables topology view (4.1) and impact analysis.

---

### 4.11 Docker-based SNMP/SSH CI integration targets + nightly stress
**Effort: M · Impact: Med** · *DevOps*

CI uses stub backends only. Add `snmpsim` + `openssh-server` service containers for real backend testing. Nightly stress: 250 assets × 5 checks × 100 cycles, assert no fd leak / memory growth. Matrix-add Python 3.13.

---

## Explicitly deferred / rejected

| Proposal | Verdict | Rationale |
|----------|---------|-----------|
| SQLite → PostgreSQL migration | **Plan only** | Document the trigger conditions (>500 devices, contention); don't implement until needed |
| Kubernetes deployment | **Skip** | SQLite local-first is fundamentally at odds with RWX/multi-replica |
| Full sync mode (Litestream) | **Defer** | Single-writer limitation; use export/import round-trip instead |
| IPAM-lite | **Defer** | Scope-creep risk toward full IPAM; revisit on real demand |
| Web UI | **Won't have (v1)** | Deliberate strategic bet per spec §7; revisit if TUI thesis doesn't hold |
| AI/LLM features | **Won't have (v1)** | Spec §7: "shipping a hallucinating feature into a trust product kills it" |
| Multi-user/RBAC | **Won't have (v1)** | Explicitly out of scope; future commercial tier |

---

## Recommended roadmap

```
v0.3 — "Operational Hygiene + Discovery Depth"
  Tier 1: 1.1–1.11 (all small, no schema change beyond 008)
  + Tier 2: 2.1 (tagging), 2.9 (identity hoist), 2.10 (SQL list_due)

v0.4 — "Adoption + UX"
  1.10 (PyPI), 3.1 (Docker), 2.2 (dashboard), 2.5 (inventory sort),
  2.6 (asset retirement), 2.7 (CSV reports), 3.8 (search filters)

v0.5 — "Network Intelligence"
  3.7 (SNMP ifTable walk), 4.4 (SNMP ARP cache), 4.1 (LLDP/CDP topology),
  2.3 (SSH config backup), 3.6 (anomaly detection)

v0.6 — "Security Hardening"
  3.2 (key rotation), 3.3 (backup encryption), 3.4 (external secret stores),
  3.17 (AAD row-binding), 3.13 (maintenance windows)

v1.0 — "Release"
  3.10 (first-run wizard), 3.9 (bulk ops), 3.15 (reporting/analytics),
  3.16 (self-health), 4.9 (uptime viz)
  + polish, docs, packaging (4.7 Homebrew/AUR/Nix)
```

---

## Summary metrics

| Metric | Count |
|--------|-------|
| Total proposals (raw) | 60+ |
| Unique proposals (after dedup) | 34 |
| Tier 1 (ship first, S effort) | 11 |
| Tier 2 (core gaps, S–M effort) | 10 |
| Tier 3 (strategic, M effort) | 17 |
| Tier 4 (ambitious, L effort) | 11 |
| Explicitly deferred/rejected | 7 |
| Cross-council consensus (2+ members) | 9 |
