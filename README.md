# LANgusta

> Local-first, self-hosted asset registry + network scanner + lightweight monitoring, for small IT teams and MSP technicians managing networks up to 250 devices.

**Status:** 0.2.0-rc2 — alpha. v1 Must-Have shipped in 0.1.0-rc1. 0.2.0-rc1 added SNMP v3 authPriv, `snmp_oid`, and `ssh_command` check kinds; 0.2.0-rc2 adds a TUI heartbeat indicator above the footer so you can see daemon freshness at a glance.

## What it does

Three things that normally live in three tools, unified on one surface:

| | |
|---|---|
| **Asset registry** | A field-level-provenance CMDB. Every field knows whether it was set by a scanner, a human, or an import — and scans never silently overwrite a human-set value. |
| **Network scanner** | `langusta scan 192.168.1.0/24` — ICMP sweep + ARP + rDNS + TCP port probe + OUI vendor lookup + mDNS + optional SNMPv2c enrichment. |
| **Lightweight monitoring** | Promote any asset to monitored. ICMP / TCP / HTTP / SNMP-OID / SSH-command checks run on your schedule; failures and recoveries land on the asset's immutable timeline. |

The distinctive feature is **institutional memory** — every asset carries its full history of scan changes, manual notes, monitoring events, and corrections on one append-only timeline.

## The three invariants

| Invariant | Enforced by |
|---|---|
| **Immutable timeline** — entries are append-only; corrections are new entries referencing the original. | SQL triggers on `timeline_entries` at the storage layer. |
| **Scanner proposes, human disposes** — observations that conflict with `manual`-provenance fields go to the review queue, never overwrite silently. | `core/provenance.merge_scan_result()` + 6 Hypothesis property tests. |
| **No data loss across upgrade** — `uv tool upgrade langusta` never requires "delete your db". | Forward-only migrations + mandatory pre-migration backup + restore-from-old-backup CI contract. |

## Install

### Linux / macOS

```bash
uv tool install langusta
langusta init            # create ~/.langusta/, prompt for a master password
langusta add --hostname router --ip 192.168.1.1
langusta scan 192.168.1.0/24
langusta ui              # Textual TUI
```

Requires Python 3.12+. If you don't have `uv` yet: <https://docs.astral.sh/uv/getting-started/installation/>.

### Windows

**Native Windows is not supported in v1.** Run LANgusta under WSL2:

```powershell
wsl --install Ubuntu-24.04
# Then inside the WSL shell:
curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install langusta
langusta init
```

See [ADR-0004](docs/adr/0004-platform-support.md) for the rationale — TL;DR: native Win32 adds ~doubled CI / support cost for an addressable audience that already runs LANgusta-shaped tools from WSL. Revisit on real demand.

### Run the monitor as a service

Once you have checks configured, render a service unit for your OS's supervisor:

```bash
langusta monitor install-service     # writes ~/.config/systemd/user/langusta-monitor.service on Linux
                                     # or ~/Library/LaunchAgents/uk.attv.langusta.monitor.plist on macOS
# then (Linux):
systemctl --user daemon-reload && systemctl --user enable --now langusta-monitor.service
# or (macOS):
launchctl load ~/Library/LaunchAgents/uk.attv.langusta.monitor.plist
```

LANgusta never daemonises itself — systemd or launchd supervises, and you can pull logs with `journalctl --user -u langusta-monitor` or `log stream --predicate 'process == "langusta"'` (ADR-0002).

## At a glance

```bash
# Setup
langusta init                              # first run: prompts for master password

# Registry
langusta add --hostname X --ip Y --mac Z
langusta list

# Scan
langusta scan 192.168.1.0/24               # ICMP + ARP + rDNS + TCP + OUI + mDNS
langusta scan 192.168.1.0/24 --snmp office # + SNMPv2c or SNMPv3 enrichment (see `cred add`)

# Credentials (encrypted via AES-256-GCM + Argon2id)
langusta cred add --label office --kind snmp_v2c      # v2c community string
langusta cred add --label office-v3 --kind snmp_v3     # v3 authPriv (prompts for 5 fields)
langusta cred list

# Review queue
langusta review
langusta review accept <id>
langusta review reject <id>

# Monitor
langusta monitor enable --asset 7 --kind icmp --interval 60
langusta monitor enable --asset 7 --kind http --interval 300 --port 443 --path /
langusta monitor run                       # one cycle (cron-friendly)
langusta monitor status
langusta monitor install-service           # render unit / plist

# Backup + portability
langusta backup now
langusta backup list
langusta export --output dump.json
langusta import dump.json                  # target must be empty
```

## Documentation

- [`docs/specs/01-functionality-and-moscow.md`](docs/specs/01-functionality-and-moscow.md) — functional spec and MoSCoW scope.
- [`docs/specs/02-tech-stack-and-architecture.md`](docs/specs/02-tech-stack-and-architecture.md) — technical specification.
- [`docs/adr/`](docs/adr/) — Architecture Decision Records.
- [`docs/development-plan.md`](docs/development-plan.md) — milestone plan (M0–M8).
- [`docs/install.md`](docs/install.md) — detailed install notes + troubleshooting.
- [`docs/upgrading.md`](docs/upgrading.md) — upgrade + restore guidance.
- [`docs/daemon.md`](docs/daemon.md) — service-manager wiring for systemd and launchd.

## Triage policy

Issues tagged `platform: windows-native` are **`wontfix` for the v1 cycle**. Please reproduce in WSL2 or close. See [CONTRIBUTING.md](CONTRIBUTING.md) and [ADR-0004](docs/adr/0004-platform-support.md).

## License

AGPL-3.0. See [LICENSE](LICENSE). A commercial add-on tier (multi-user, RBAC, web UI) is a possible future; the core will remain AGPL.

## Security + privacy

- Credentials encrypted with AES-256-GCM, keys derived from the master password via Argon2id (~500ms on modern hardware).
- DB file created at mode `0600`; `~/.langusta/backups/` at `0700`.
- Scanner is **off by default** on install — nothing goes out the wire until you type `langusta scan <subnet>` (spec §16).
- Zero outbound traffic that you didn't configure. No telemetry, no update checks, no crash reporting.
