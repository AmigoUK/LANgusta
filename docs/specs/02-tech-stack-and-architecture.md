# LANgusta — Tech Stack and Architecture

**Document:** 2 of 2 — Technical specification
**Audience:** Developers implementing v1
**Status:** Draft for review before first line of code

---

## 1. Guiding principles

Five principles constrain every technical decision. Implementers should check choices against this list before adding a dependency.

1. **Local-first, always.** Everything runs on the admin's machine or on a single on-prem VM. No cloud services are required for any core function. The tool must be usable on an isolated/air-gapped network.
2. **Single binary-like install.** The goal is `uv tool install langusta` (or equivalent) followed by `langusta init`. No Docker required for the user, no web server to configure, no database to provision.
3. **Minimize moving parts.** SQLite, not Postgres. Python stdlib where possible. Every dependency must justify its presence — "it would be convenient" is not sufficient.
4. **Portable data.** The database is a single file. Backups are file copies. Export is a well-specified JSON/YAML schema. Users can leave at any time with everything they put in.
5. **No telemetry, no phone-home.** The process makes zero outbound connections the user did not explicitly configure.

## 2. Language and runtime

**Language:** Python 3.12+

Python is the right choice because the target user demographic is Python-literate, the scanning/network-libraries ecosystem in Python is mature, and the TUI frameworks available in Python are among the best in any language today. It is not the fastest runtime, but for a <250-device tool the performance ceiling is far above what the product needs.

**Package/runtime manager:** [uv](https://github.com/astral-sh/uv)

uv handles Python installation, virtualenv management, and dependency resolution in one tool. Users install LANgusta with `uv tool install langusta` and get an isolated environment without touching their system Python. For development, `uv sync` gives contributors a reproducible environment in seconds. This is a better onboarding story than pip+venv and a dramatically better one than pipx or poetry for this user base.

**Minimum Python version:** 3.12 — we use `PEP 695` type syntax and `typing.override`. No 3.11 support; the maintenance cost of supporting multiple Python versions is not worth it for a tool that ships its own interpreter via uv.

## 3. Data layer

**Database:** SQLite 3.38+ (WAL mode mandatory)

Everything lives in one `~/.langusta/db.sqlite` file. WAL mode (`PRAGMA journal_mode=WAL`) is set on first open and is non-negotiable — it's what makes concurrent reads-during-writes viable and dramatically reduces write contention between the TUI process, the scanner process, and the monitoring worker.

Additional PRAGMA defaults:

- `synchronous=NORMAL` (safe with WAL, much faster than FULL)
- `foreign_keys=ON`
- `busy_timeout=5000` (5 seconds before a locked-db error)
- `temp_store=MEMORY`

**Migrations:** [Alembic](https://alembic.sqlalchemy.org/) (if we use SQLAlchemy) or a small hand-rolled migration table (if we don't). See ORM decision below.

**ORM decision — deliberately deferred:** There is an honest tension. SQLAlchemy is the Python ORM standard and integrates well with Alembic, but it's heavy and its query DSL is a learning tax. For a schema this small and this stable, raw `sqlite3` with carefully written SQL in a small data-access layer is defensible and produces simpler, more debuggable code.

Recommendation: **start with raw SQL + a thin data-access module** (`langusta/db/queries.py` with functions like `get_asset_by_id`, `insert_timeline_entry`). Add [SQLModel](https://sqlmodel.tiangolo.com/) only if schema complexity grows past ~15 tables. Do not pull in full SQLAlchemy for v1.

**Scale sanity check:** 250 devices × 10 timeline entries/day × 3 years ≈ 2.7M rows, all well-indexed — SQLite handles this comfortably. 250 devices monitored every 60s = ~4 writes/second steady-state; WAL mode handles this without breaking a sweat. If someone deploys against 2,500 devices with aggressive monitoring, they may hit contention; that is not v1's problem, but document the ceiling honestly.

## 4. TUI framework

**Choice: [Textual](https://github.com/Textualize/textual) (from Textualize/Rich)**

Textual is the most capable Python TUI framework available. It gives us:

- A reactive component model (feels like React but for terminals).
- Built-in widgets for lists, tables, inputs, modals, syntax-highlighted markdown.
- CSS-like styling, which means theming is cheap.
- First-class async support (important for running the scanner and monitoring without blocking the UI).
- Active development and a healthy community.

**Alternatives considered:** [Urwid](https://urwid.org/) (mature but dated API, less async-friendly), [prompt_toolkit](https://python-prompt-toolkit.readthedocs.io/) (more suited to input-focused apps than whole-screen layouts), [Rich](https://rich.readthedocs.io/) alone (rendering library, not an event-loop framework).

**Key Textual patterns to adopt early:**

- Use Textual's `@work(thread=True)` for scans and other blocking operations.
- Use `Screen` classes for modal workflows (review queue, new-asset form, monitoring configuration).
- Use Textual's built-in CSS for styling from day one — don't inline colors.
- Keybindings should be declarative (`Binding("ctrl+s", "save", "Save")`) so the help screen is auto-generated.

## 5. CLI companion

**Choice: [Typer](https://typer.tiangolo.com/)** (built on Click, with type-hint-driven command definitions)

Typer gives us a CLI that shares validation logic with the TUI and produces good `--help` output for free. The CLI subcommands cover the automation use cases:

```
langusta init              # create db, set master password
langusta scan [subnet]     # run a scan, write results to db
langusta list [--tag ...]  # list assets, filter by tag
langusta show <id>         # show asset detail, timeline
langusta add               # interactive asset creation
langusta export [--format json|yaml|csv]
langusta import <file>
langusta backup [--now]    # manual backup trigger
langusta monitor run       # run monitoring cycle once (for cron)
langusta ui                # launch the TUI (default if no args)
```

The TUI and CLI share the same data-access layer. They are two front-ends over one library.

## 6. Scanning and network libraries

| Function | Library | Notes |
|---|---|---|
| ICMP ping | [icmplib](https://github.com/ValentinBELYN/icmplib) | Pure-Python, no raw-socket privilege needed on most platforms with `privileged=False` mode |
| TCP port probe | stdlib `asyncio.open_connection` | No dependency needed |
| ARP table read | `ip neigh` subprocess on Linux, `arp -a` on macOS/Windows | Wrap in a platform-abstraction module |
| SNMP v2c/v3 | [pysnmp-lextudio](https://github.com/lextudio/pysnmp) | The maintained fork; original pysnmp is abandoned. Alternative: shell out to `snmpwalk` from net-snmp if pysnmp proves flaky |
| mDNS | [zeroconf](https://github.com/python-zeroconf/python-zeroconf) | Mature, well-maintained |
| NetBIOS | Custom UDP 137 query, ~50 lines | No good maintained library; roll our own |
| Reverse DNS | stdlib `socket.gethostbyaddr` | Time-box with asyncio wait_for |
| Fingerprinting | OUI lookup against IEEE OUI registry (cached locally) | Ship the OUI DB as a packaged asset, update via `langusta update-oui` |
| SSH (v1.5+) | [paramiko](https://www.paramiko.org/) or [asyncssh](https://asyncssh.readthedocs.io/) | asyncssh preferred for the async model fit |

**On nmap:** do not shell out to nmap for v1. It's a great tool but adds an external-binary dependency that breaks the "single-install, no system deps" story. If advanced users want nmap integration, expose it as a plugin in v2.

## 7. Monitoring subsystem

**Scheduler:** [APScheduler](https://apscheduler.readthedocs.io/) in-process, SQLite-backed job store.

APScheduler gives us cron-like scheduling with persistent jobs (so a reboot doesn't lose the schedule). The alternative — a separate cron job that runs `langusta monitor run` every minute — is simpler but loses per-asset scheduling flexibility. Start with APScheduler; if it proves too heavy, fall back to the cron approach.

**Worker model:** the monitoring worker runs as a separate Python process spawned by the TUI (or independently via `langusta monitor daemon`). It writes results to the same SQLite file. WAL mode is what makes this safe.

**Check plugins:** each check type (ICMP, TCP, HTTP, SNMP-OID, SSH-command) is a class implementing a `Check` protocol:

```python
class Check(Protocol):
    async def run(self, asset: Asset, config: dict) -> CheckResult: ...
```

This keeps the check types extensible without a full plugin system in v1.

## 8. Credential storage

**Encryption:** AES-256-GCM via [cryptography](https://cryptography.io/), key derived from the master password using Argon2id (via [argon2-cffi](https://argon2-cffi.readthedocs.io/)).

**Schema:**

```
credentials (
  id INTEGER PRIMARY KEY,
  label TEXT NOT NULL,
  kind TEXT NOT NULL,    -- 'snmp_v2c' | 'snmp_v3' | 'ssh_key' | 'api_token' | ...
  ciphertext BLOB NOT NULL,
  nonce BLOB NOT NULL,
  created_at TEXT NOT NULL
)
```

Credentials are **never** logged, never included in the JSON/YAML export unless `--include-secrets` is explicitly passed (and even then, re-encrypted with a user-provided export password), never displayed in the TUI after creation. Only the label and kind are visible in the UI; the secret is referenced by ID when a scanner or check needs it.

**v1.5 stretch:** support external secret stores as an alternative to internal storage. If the user has 1Password CLI (`op`), Bitwarden CLI (`bw`), or HashiCorp Vault configured, let them reference secrets by vault path instead of storing them locally. This is a significant differentiator against Lansweeper's "decrypt and spray" model.

## 9. Backup strategy

**What:** full copy of `db.sqlite` written to `~/.langusta/backups/db-<ISO8601>.sqlite`.

**When:** on every scan completion AND on a daily timer (whichever happens first), deduplicated within a 1-hour window to avoid backup spam during heavy scanning.

**Retention:** keep 30 most recent by default (configurable in `config.toml`). Total disk cost for a typical 250-device deployment: <500 MB.

**How:** use SQLite's online backup API (`sqlite3.Connection.backup()`) — this is safe while the DB is in use, unlike a raw file copy. Pure-Python, no external tools.

**Integrity:** after each backup, open the copy and run `PRAGMA integrity_check`. Log failures loudly; do not silently retain corrupted backups.

## 10. Project structure

```
langusta/
├── pyproject.toml
├── uv.lock
├── README.md
├── LICENSE                 # AGPL-3.0
├── src/langusta/
│   ├── __init__.py
│   ├── __main__.py         # `python -m langusta`
│   ├── cli.py              # Typer app
│   ├── tui/
│   │   ├── app.py          # Textual App subclass
│   │   ├── screens/
│   │   │   ├── inventory.py
│   │   │   ├── asset_detail.py
│   │   │   ├── search.py
│   │   │   ├── review_queue.py
│   │   │   └── ...
│   │   ├── widgets/
│   │   └── styles.tcss
│   ├── core/
│   │   ├── models.py       # dataclasses / pydantic models
│   │   ├── identity.py     # composite identity resolution
│   │   └── provenance.py   # field-level provenance logic
│   ├── db/
│   │   ├── schema.sql
│   │   ├── migrations/
│   │   └── queries.py      # data-access layer
│   ├── scan/
│   │   ├── icmp.py
│   │   ├── arp.py
│   │   ├── tcp.py
│   │   ├── snmp.py
│   │   ├── mdns.py
│   │   ├── netbios.py
│   │   ├── rdns.py
│   │   └── orchestrator.py # runs scanners, merges results
│   ├── monitor/
│   │   ├── scheduler.py
│   │   ├── checks/
│   │   └── worker.py
│   ├── crypto/
│   │   └── vault.py        # credential encryption
│   ├── backup.py
│   ├── export.py
│   ├── config.py           # config.toml loader
│   └── platform/           # OS-specific code (ARP reading, etc.)
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
└── docs/
```

Keep `core/` dependency-free (only stdlib) so it's trivially unit-testable. Everything network-y goes in `scan/` or `monitor/`. Everything UI-y goes in `tui/`.

## 11. Configuration

**File:** `~/.langusta/config.toml`

```toml
[scan]
default_subnet = "auto"    # or "192.168.1.0/24"
tcp_ports = [22, 80, 443, 3389, 8080]
snmp_timeout = 2.0
parallelism = 64

[monitor]
enabled = true
default_interval_seconds = 60

[backup]
retention = 30
interval_hours = 24

[ui]
theme = "dark"
keybindings = "default"    # or "vim"

[export]
default_format = "json"
```

TOML is the Python standard for config. Do not invent another format. Do not put secrets in this file.

## 12. Testing strategy

- **Unit tests:** core/, db/, crypto/ — pytest, fast, no network.
- **Integration tests:** full scanner runs against a dockerized test network (optional, opt-in via marker) — this one exception to the "no Docker for users" rule applies only to CI.
- **TUI tests:** Textual provides [snapshot testing](https://textual.textualize.io/guide/testing/) — use it for every screen.
- **Property-based tests:** Hypothesis for identity-resolution logic. This is the part of the code where weird inputs find weird bugs.
- **Coverage target:** 80% for `core/` and `db/`. Lower is acceptable for `tui/` (snapshots + manual QA do most of the work).

## 13. Packaging and distribution

**Primary channel:** PyPI. Users run `uv tool install langusta`.

**Secondary channel:** release binary wheels via PyPI with pre-built C extensions (cryptography, argon2-cffi) for common platforms.

**Do not distribute via:** Docker (violates the single-install story), Snap/Flatpak (adds packaging burden for zero user benefit at this stage), a curl-to-bash installer (users in this demographic rightly distrust these).

**Versioning:** SemVer. `0.x` until we're confident the schema and CLI surface are stable; 0.x minor versions may break things, 1.0 will not.

**Release cadence:** no fixed schedule. Ship when ready. Avoid the NetBox trap of shipping a major version every few months and forcing users into painful upgrade chains.

## 14. License

**AGPL-3.0.**

This is the load-bearing business decision. The research points clearly: the OSS tools with clean trust records (Zabbix, Snipe-IT, GLPI, LibreNMS) use copyleft licenses and monetize hosting and support; the ones with trust problems (Observium, NetBox Labs' newer add-ons) use source-available or proprietary add-on models. AGPL protects against a cloud provider repackaging LANgusta as a SaaS without contributing back, which is the scenario that most threatens the long-term community.

A commercial add-on tier (multi-user, RBAC, web UI, SSO) is compatible with an AGPL core via dual-licensing or via a separately-licensed add-on module. That design is deliberately deferred; it is not a v1 concern.

## 15. Dependencies at a glance

Target dependency count for v1: **≤15 direct dependencies.** Anything beyond that requires a review.

Direct dependencies (proposed):

- `textual` (TUI)
- `typer` (CLI)
- `icmplib` (ICMP)
- `pysnmp-lextudio` (SNMP)
- `zeroconf` (mDNS)
- `cryptography` (credential encryption)
- `argon2-cffi` (password-derived keys)
- `apscheduler` (monitoring scheduler)
- `httpx` (HTTP checks)
- `pydantic` (data validation, if not using stdlib dataclasses)
- `rich` (transitively via Textual — explicit for CLI formatting too)

Dev dependencies: `pytest`, `pytest-asyncio`, `hypothesis`, `ruff` (lint + format, replaces black + isort + flake8).

## 16. Security defaults worth naming

- SQLite database file permissions: `0600` on creation.
- Config file: `0600`.
- Backups directory: `0700`.
- Master password: min 12 chars, Argon2id params tuned for ~500ms on modern hardware.
- No default credentials, ever. First run requires the user to set a master password; there is no fallback.
- Scanner is **off by default on first install.** The user explicitly kicks off the first scan. This matters because scanning a network you don't own is a legally ambiguous action in some jurisdictions, and we should not have the tool start poking packets without consent.
- No outbound connections except user-configured scan targets, webhooks, and SMTP. No update checks. No analytics. No crash reporting (if crashes are a problem, users file GitHub issues).

## 17. Open technical questions for the team

1. **Raw SQL vs. SQLModel vs. full SQLAlchemy** — my recommendation is raw SQL + thin DAL, but a team with strong ORM preferences might push back. Decide before writing the schema.
2. **Single process vs. multi-process for scanner/monitor/TUI** — Textual's async model can host everything in one process, but process isolation has real debugging and crash-resilience benefits. Default recommendation: TUI is one process, monitor daemon is a separately-launched process, scanner runs in-process as a Textual `@work` thread.
3. **pysnmp-lextudio vs. shelling out to net-snmp** — pysnmp-lextudio is cleaner Python but historically flaky on edge-case OIDs. Have a fallback plan.
4. **Windows support?** The research suggests the target user is Linux-native, but SMB admins are often on Windows daily-drivers who SSH into a Linux box for this kind of tool. Decide whether v1 supports running the TUI on Windows directly (Textual does, but some scan libraries are Linux-specific) or whether Windows users are expected to run LANgusta under WSL.
5. **Schema stability pre-1.0** — how aggressive do we let schema changes be during 0.x? My recommendation: ship a migration for every change from 0.1 onward, even in pre-1.0. Pain now pays back later.

These are real decisions, not rhetorical questions. Resolve them in a short ADR (architecture decision record) per question before implementation.
