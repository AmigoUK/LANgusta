# LANgusta — Functionality, Logic, and MoSCoW Scope

**Document:** 1 of 2 — Functional specification
**Audience:** Developers implementing v1
**Status:** Draft for review before first line of code

---

## 1. What LANgusta is (in one paragraph)

LANgusta is a **local-first, self-hosted asset registry and documentation tool** for small IT teams and MSP technicians managing networks of up to 250 devices. It combines three things in a single TUI application: a CMMS-style asset registry (describe, document, and track every host and piece of equipment), a built-in network scanner that auto-populates that registry, and a lightweight recurring monitoring engine that turns any registered asset into a watched host. The core thesis is that **discovery, documentation, and monitoring should live on the same surface** — not in three separate tools that drift apart. The product's distinctive feature is **institutional memory**: every asset carries its full history of changes, incidents, upgrades, and resolutions in one view.

## 2. Target user and use modes

**Primary user:** a solo or small-team IT administrator (in-house) or MSP technician (external), Linux-comfortable, SSH-native, managing a single-site or small multi-site environment under ~250 devices.

**Not the target:** enterprise netops teams, Windows-only shops that live in RDP, or users who have never opened a terminal.

Four use modes shape the UI priorities:

1. **Onboarding mode** (long, exploratory) — first run, new site, inherited environment. *"What's on this network?"* The scanner earns its keep here.
2. **Incident mode** (short, urgent) — *"What is 192.168.4.137 and when did we last touch it?"* Search must be instant.
3. **Audit mode** (medium, cyclical) — *"List every device not scanned in 30 days."* Filter, export, done.
4. **Documentation mode** (short, frequent) — *"I just replaced the reception switch, let me log it."* Low-friction data entry.

The TUI front door is a universal search bar. Typing an IP, MAC, hostname fragment, description keyword, or note content resolves to a ranked hit list across all assets. This is the single highest-leverage interaction in the product and must be fast and fuzzy from day one.

## 3. Single-user authentication model (revised)

LANgusta is a **single-admin local tool**. It runs on one trusted workstation or a small on-prem VM. There is no multi-tenancy in v1, no remote access, no 2FA, no RBAC. Authentication is a local master password that unlocks the SQLite database, nothing more. If a second team member needs access, they run their own instance or share SSH access to the box.

This decision deliberately narrows scope. Multi-user, RBAC, and audit-log-who-did-what belong in a hypothetical v2 or the future commercial/enterprise tier — they are explicitly out of v1. Implementers should not build schema hooks for them; premature abstraction here will distort the data model.

## 4. Core functional pillars

There are four functional pillars. Everything in v1 belongs to one of them; anything that doesn't belong to one of them is out of scope.

### Pillar A: Asset registry (the CMMS core)

The asset registry is the product. An asset is a device plus its story. Every asset has:

- **Identity fields** (populated by scan or by hand): hostname, primary IP, MAC addresses (one-to-many), vendor/OUI, detected OS, detected device type.
- **Human fields** (always manual): human-readable description, physical location, owner/responsible party, notes (free-text, markdown-capable).
- **Operational fields**: management URL or SSH target, criticality level, tags.
- **System fields**: first-seen timestamp, last-seen timestamp, source of record (scanned / manual / imported), field-level provenance map.

**Field-level provenance is non-negotiable.** Every field knows whether its current value came from a scan, a human edit, or an import, and when. The UI surfaces this subtly (a small marker next to scanned-but-not-confirmed fields). This solves the scan-overwrites-human-notes problem by making the rule explicit: **scanner never overwrites human-set fields**; it appends to a "proposed changes" queue visible in the asset's history tab.

### Pillar B: Network scanner (the wedge)

The scanner is the feature that sells the product in 30 seconds. First-run experience: user enters a subnet (or the tool auto-detects from the host interface), runs `langusta scan`, and within a minute sees a populated inventory.

Discovery techniques, in order of reliability:

1. **ICMP sweep** — liveness baseline.
2. **ARP table ingestion** — from the local machine; cheap and accurate for the local broadcast domain.
3. **TCP port fingerprinting** — top-100 ports by default, configurable.
4. **SNMP v2c/v3 polling** — if community strings or credentials are provided; yields hostname, sysDescr, interface table, LLDP/CDP neighbors.
5. **mDNS/Bonjour and NetBIOS** — cheap name-resolution for consumer and Windows devices.
6. **Reverse DNS** — fallback for hostnames.

**Identity resolution** is explicit: a composite identity of (MAC addresses ∪ hostname ∪ management IP) with a confidence score. When a new scan result is ambiguous (two possible matches, or a MAC that matches an existing asset but a hostname that doesn't), the tool **does not auto-merge**; it creates an entry in the review queue. This is the single most important design rule of the scanner — quiet auto-merges destroy trust and this was a specific, documented failure in Lansweeper.

Credentials (SNMP communities, SSH keys for config backup, API tokens) are stored **encrypted at rest** in a separate SQLite table, unlocked by the master password. They are never logged, never exported in backups in plaintext, and never displayed in the TUI after entry — only referenced by ID. A v1.5 stretch goal is to integrate with external secret stores (1Password CLI, Bitwarden CLI, HashiCorp Vault) so the tool never persists the secret at all.

### Pillar C: Recurring monitoring (reuse the inventory)

Any registered asset can be promoted to a monitored asset. The user selects checks to run on a schedule:

- **Liveness**: ICMP, TCP port reachability.
- **Service**: HTTP/HTTPS status code + response time, custom TCP banner match.
- **SNMP**: interface up/down, CPU/memory OIDs, custom OID polling.
- **SSH command**: run an arbitrary command, compare output against expected pattern (off by default, requires explicit enable because it's a foot-gun).

Monitoring state lives in the same database. When a check fails, an event is written to the asset's history — **monitoring events are first-class history entries, same as manual notes and scan changes**. This is the link that makes the pillars reinforce each other: looking at a flaky switch three months later, you see *"12 monitoring failures between March 4 and March 19, resolved after firmware upgrade logged by admin on March 20."*

Notifications in v1 are deliberately minimal: a notification log in the TUI, optional webhook POST, optional local email via SMTP. No SMS, no PagerDuty, no mobile app. Integration-heavy alerting is explicitly a v2 concern.

### Pillar D: Institutional memory (the moat)

This is the feature nobody else does well and the reason the product exists as a distinct thing rather than as "another CMDB."

Every asset has a **unified history timeline** showing, in chronological order:

- Scan-detected changes (IP changed, new open port, OS fingerprint updated).
- Human-authored notes and journal entries (markdown, timestamped, never edited in place — append-only with edit-as-new-entry).
- Monitoring events (failures, recoveries, threshold crossings).
- Configuration/firmware changes (manually logged or ingested from SSH-pulled configs).
- Incidents (linked or manually logged: "replaced PSU," "firmware 15.2(4)E9 → 15.2(7)E4," "moved from rack 3 to rack 7").

The timeline is the primary view of an asset for the incident-mode use case. When a tech opens the asset during an outage, the timeline is what they see first — not the IP address, not the MAC, but *"here's what has ever happened to this thing."*

**Timeline entries are immutable.** You can add a correction as a new entry; you cannot rewrite history. This is deliberate and aligned with the institutional-memory pitch — the value of the log is that it's trustworthy.

## 5. Core user flows

### Flow 1: First run (the wedge)

```
$ langusta init
  → prompts for master password
  → creates ~/.langusta/db.sqlite
  → auto-detects local subnet from default interface

$ langusta scan
  → "Scanning 192.168.1.0/24..."
  → progress bar
  → "Found 47 devices in 38 seconds"
  → drops user into TUI, inventory view, devices grouped by vendor OUI
```

The first `langusta scan` is the 30-second moment. If this doesn't feel magical, nothing else matters.

### Flow 2: Incident search

```
User launches TUI, presses /
Types "reception"
Instantly sees: "HP LaserJet M428 — reception desk — Janet's printer"
Enters the asset
Sees timeline: last scan, last print job log, three prior jam incidents, firmware history
```

### Flow 3: Documenting a change

```
From asset view, press n (new entry)
Markdown editor opens
User writes: "Replaced PSU, old one was making grinding noise. New PSU serial ABCD1234."
Save. Timestamped. Attributed. Immutable.
```

### Flow 4: Promoting to monitored

```
From asset view, press m (monitor)
Checklist of check types
User enables ICMP every 60s, HTTP every 5min on port 443
Saved. Runs in background.
Next failure writes to timeline and notification log.
```

### Flow 5: Review queue after a scan

```
$ langusta scan
  → "Scan complete. 2 new devices, 3 proposed changes, 1 ambiguous match."
  → Enters review queue
  → User approves/rejects/edits each proposed change
```

## 6. Data backup and portability

Because the whole database is a single SQLite file, backups are trivially easy and must be **automatic and visible**. On every scan or on a daily timer (whichever comes first), LANgusta writes a timestamped copy to `~/.langusta/backups/` and prunes to N most recent (default 30). A `langusta export` command produces a portable JSON/YAML dump for migration or archival. Never put the user in a position where they can't get their data out — this is both ethical and strategically important (it's the anti-Lansweeper, anti-Kaseya pitch).

## 7. MoSCoW scope for v1

### Must have (v1 does not ship without these)

- **Local SQLite-backed asset registry** with the field set defined in Pillar A.
- **Field-level provenance** (scanned vs. human) recorded for every field.
- **Network scanner** doing ICMP + ARP + TCP fingerprinting + SNMP v2c + mDNS + reverse DNS.
- **Composite identity resolution** with confidence scoring and manual review queue for ambiguous matches.
- **Scanner never overwrites human-set fields** — proposes changes, doesn't apply silently.
- **TUI with universal search** (fuzzy, cross-field) as the front door.
- **Immutable timeline view** per asset with manual journal entries, scan diffs, and monitoring events unified.
- **Recurring monitoring** with ICMP + TCP port + HTTP(S) checks at minimum.
- **Encrypted credential storage** behind the master password.
- **Automatic timestamped SQLite backups** with pruning.
- **JSON/YAML export** for portability.
- **Single-user master-password auth** (no 2FA, no RBAC).
- **CLI companion** for scripting (`langusta scan`, `langusta list`, `langusta export`, `langusta add`).

### Should have (ship soon after v1; design must not preclude these)

- **SNMP v3** with authPriv.
- **SSH-based config backup** for common network gear (Cisco IOS, Juniper, MikroTik, FortiGate).
- **LLDP/CDP neighbor ingestion** for auto-drawing L2 topology.
- **Webhook and SMTP notifications** for monitoring events.
- **Tagging system** with tag-based filtering and bulk actions.
- **Global search** that also matches timeline content (not just asset fields).
- **Keyboard-customizable keybindings** (vim-style defaults).
- **Secret-store integration** with 1Password CLI / Bitwarden CLI / HashiCorp Vault as an alternative to internal credential storage.
- **Import from Lansweeper CSV export and NetBox API** — this is the migration on-ramp and directly targets the two biggest user-flight populations.

### Could have (nice, not essential; genuine scope creep risk)

- **Terminal-rendered network topology diagram** (ASCII/Braille-based).
- **Rack elevation view** in the TUI.
- **Plugin system** for custom checks and custom scanners.
- **Multi-subnet scanning** with scan profiles per subnet.
- **IPAM-lite** (prefix tracking, next-free-IP suggestion) — small version, not a NetBox replacement.
- **Diff view** for config backups.
- **Dark/light theme switcher.**
- **Sync mode** for an admin running LANgusta on a laptop that sometimes connects to the "real" database on a server (design carefully, easy to get wrong).

### Won't have (explicitly out of v1, and some out of v2 too)

- **Web UI** — *this is the most important "won't have" and a deliberate strategic bet. If the TUI-only thesis doesn't find traction within 6–12 months of launch, revisit.* Research strongly suggests a web UI would widen the addressable market significantly; the counter-bet is that a great TUI serves the Linux-heavy homelab/MSP-sovereignty niche better than a mediocre web UI would.
- **Multi-user accounts, RBAC, SSO, 2FA** — out of scope entirely for v1; revisit for a paid/enterprise tier.
- **Mobile app, responsive anything** — not the target user's workflow.
- **Ticketing system / helpdesk** — LANgusta is not GLPI. Integrate with external ticketing via webhooks if demanded.
- **AI-generated anything in v1** — no LLM summarization of timelines, no "ask your network a question." It's a trust product; shipping a hallucinating feature into a trust product kills it.
- **Agent-based endpoint inventory** (installed software, running processes on Windows machines) — this is Lansweeper's core competency and a different product. Stay agentless.
- **Vulnerability scanning, compliance reporting** — adjacent but a different discipline; resist the pull.
- **Cloud-hosted LANgusta** — violates the whole local-first thesis. A future commercial offering could be an on-prem-deployed multi-user version, not a SaaS.
- **Real-time collaboration, commenting, mentions** — single-user tool.
- **Graphing, dashboards, pretty charts** — monitoring is for *"was it up?"*, not observability.

## 8. Design logic and key decisions worth documenting

A few decisions implementers need to understand as load-bearing:

**Immutable history.** Users can add, never edit or delete, timeline entries. Corrections are new entries that reference the original. This is the foundation of the institutional-memory pitch and must not be compromised even when a user begs for an edit button.

**Scanner proposes, human disposes.** The scanner is additive. It finds new devices and flags changes on known ones, but it never silently overwrites a human-set field. When it must merge or match, ambiguous cases go to a review queue — they do not auto-resolve.

**One database, one file.** Everything lives in `~/.langusta/db.sqlite`. Backups are copies of that file. Migration is copying that file. This is a strategic simplification — no Postgres, no Redis, no message broker. SQLite handles the scale (<250 devices × reasonable history) comfortably. If scale demands change, revisit; do not speculatively over-engineer.

**TUI as primary, CLI as companion.** The TUI is the user interface. The CLI exists for automation, scripting, and CI integration. Both operate on the same database. Anything you can do in the TUI should be scriptable in the CLI within reason; this is what makes the tool feel like it belongs in the sysadmin toolchain.

**Search is the home screen.** Every TUI view is one keystroke away from global search. Do not bury search in a menu.

**No telemetry, no phone-home.** Local-first means local-only. The tool makes exactly zero outbound network connections the user did not explicitly configure (scan targets, webhooks, SMTP server). Not even for update checks. Update checks belong in the package manager.

## 9. Risks the team should name out loud

1. **TUI-only may be a commercially narrow bet.** Sector research is clear that the SMB/MSP admin demographic is Windows-majority and web-UI-native. The TUI bet is a deliberate targeting of a subsegment (Linux-comfortable, SSH-native, homelab/selfhost graduate, network-automation-adjacent). Monitor adoption; be willing to add a web UI in v2 if the TUI thesis doesn't hold.
2. **SQLite + concurrent monitoring writes** needs real benchmarking at the upper end of 250 devices with per-minute checks. WAL mode is mandatory; write contention is the realistic failure mode.
3. **Scanner accuracy is the reputation risk.** Bad identity resolution produces duplicates or silent merges — both are trust-killers. Over-invest here; it's cheaper than rebuilding trust after a bad release.
4. **Institutional memory is the moat, but also the slowest-to-demonstrate feature.** The scanner sells the tool in 30 seconds; the memory pays off in 6 months. Marketing and onboarding need to surface the memory value early, or users churn before they feel it.
5. **"No AI" is a defensible v1 position but a harder v2 position.** Competitors will ship LLM-summarized timelines; have a principled position on when/if LANgusta does, and keep it aligned with the trust product thesis.
