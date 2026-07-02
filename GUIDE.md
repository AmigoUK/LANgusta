# LANgusta — Guide

A tool for small IT teams who manage up to 250 devices on a local network.

It does three things in one program:

1. **Keeps a list of your devices** (the asset register)
2. **Scans your network** to find devices automatically
3. **Monitors devices** and tells you when something breaks

Everything is stored on your own machine. Nothing leaves your network unless you ask it to.

---

## Contents

- [Getting started](#getting-started)
- [Your first scan](#your-first-scan)
- [Adding devices by hand](#adding-devices-by-hand)
- [The review queue](#the-review-queue)
- [Encrypted credentials](#encrypted-credentials)
- [Monitoring](#monitoring)
- [Notifications](#notifications)
- [Backups and exports](#backups-and-exports)
- [Importing from other tools](#importing-from-other-tools)
- [The TUI (text interface)](#the-tui-text-interface)
- [Environment variables](#environment-variables)
- [Daily checklist](#daily-checklist)

---

## Getting started

### Install

You need Python 3.12 or newer and the `uv` tool.

**Linux:**

```bash
# Install uv (only once)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install LANgusta
uv tool install git+https://github.com/AmigoUK/LANgusta.git

# Check it worked
langusta --version
```

**macOS:** The same steps work. Unprivileged ICMP is already enabled, so scanning works out of the box.

**Windows:** Not supported on its own. Use WSL2 (Windows Subsystem for Linux). See the [install guide](docs/install.md) for details.

### Enable scanning (Linux only)

Most Linux systems allow unprivileged ICMP by default. If scanning fails, run this once:

```bash
sudo sysctl -w net.ipv4.ping_group_range="0 2147483647"
```

### First run

```bash
langusta init
```

This creates the `~/.langusta/` folder and the database. You will be asked to set a **master password** — make it at least 12 characters. This password protects your encrypted credentials (SNMP community strings, SSH keys, and so on).

> **Important:** If you forget the master password, there is no way to recover it. Your encrypted credentials are permanently lost.

You can also set the password without a prompt:

```bash
LANGUSTA_MASTER_PASSWORD="my-secure-password" langusta init
```

### File layout

After `init`, your data lives here:

```
~/.langusta/           (0700 — only your user can read it)
├── db.sqlite          (0600 — the main database)
├── backups/           (0700 — automatic snapshots)
├── known_hosts        (0600 — SSH host key pins)
├── monitor.pid        (daemon process ID)
├── monitor.log        (daemon output)
└── notifications.log  (always-on event log)
```

---

## Your first scan

```bash
langusta scan 192.168.1.0/24
```

This sweeps the subnet and finds live devices. For each device it finds, it tries to discover:

- **IP address** (via ICMP ping)
- **MAC address** (from the ARP table)
- **Hostname** (via reverse DNS)
- **Open ports** (via TCP probe)
- **Vendor** (from the MAC address OUI lookup)
- **mDNS name** (if the device advertises one)

### Scanning with SNMP

To also pull the system description (sysDescr) from SNMP-capable devices, first add a credential, then scan:

```bash
langusta cred add --label office --kind snmp_v2c
langusta scan 192.168.1.0/24 --snmp office
```

### Scanning a single host

```bash
langusta scan 10.0.0.5
```

### What happens to scanned data?

Each device that is found is either:

- **Inserted** as a new asset (if it is not already known)
- **Updated** (if it matches an existing asset and the fields are safe to change)
- **Deferred to the review queue** (if the scan found a different value for something a human set — see [The review queue](#the-review-queue))

Scans never silently overwrite a value that you entered by hand. This is the core rule of the tool.

---

## Adding devices by hand

```bash
langusta add --hostname router --ip 192.168.1.1 --mac aa:bb:cc:dd:ee:ff
```

You only need one of `--hostname`, `--ip`, or `--mac`. The rest are optional:

| Option | What it does |
|---|---|
| `--hostname` / `-n` | A human-readable name |
| `--ip` | Primary IPv4 address |
| `--mac` | MAC address (any case — stored lowercase) |
| `--description` / `-d` | Short description |
| `--location` | Where the device is |
| `--owner` | Who is responsible for it |
| `--url` | Management URL or SSH target |
| `--criticality` | How important this device is |
| `--force` / `-f` | Allow a duplicate IP or hostname |

Fields you set by hand are marked with `manual` provenance. Future scans will **never** overwrite them silently — they go to the review queue instead.

### Listing devices

```bash
langusta list
```

This prints a table of all known assets.

---

## The review queue

When a scan finds a value that differs from what a human set (or what was imported), the new value does **not** replace the old one. Instead, a **proposed change** is filed in the review queue.

### See pending proposals

```bash
langusta review
```

### Accept a proposal

```bash
langusta review accept 1
```

This applies the scanner's value and marks the field as `scanned`.

### Reject a proposal

```bash
langusta review reject 1
```

The scanner's value is discarded. Your original value stays.

---

## Encrypted credentials

Credentials (SNMP community strings, SSH keys, and so on) are encrypted at rest with AES-256-GCM. The encryption key is derived from your master password using Argon2id.

### Adding a credential

**SNMP v2c (community string):**

```bash
langusta cred add --label office --kind snmp_v2c
```

You will be prompted for the community string. You can also set it via the environment:

```bash
LANGUSTA_CRED_SECRET="public" langusta cred add --label office --kind snmp_v2c
```

**SNMP v3 (authPriv):**

```bash
langusta cred add --label office-v3 --kind snmp_v3
```

This prompts for five fields: username, auth protocol, auth passphrase, privacy protocol, and privacy passphrase. You can also set them via environment variables (see [Environment variables](#environment-variables)).

**SSH key:**

```bash
langusta cred add --label ssh-key --kind ssh_key
```

Paste the PEM private key when prompted.

**SSH password:**

```bash
langusta cred add --label ssh-pw --kind ssh_password
```

**API token:**

```bash
langusta cred add --label netbox --kind api_token
```

### Listing credentials

```bash
langusta cred list
```

This shows the label and type of each credential. Secrets are **never** displayed.

### Removing a credential

```bash
langusta cred rm 1
```

Use the ID from `cred list`.

---

## Monitoring

You can set up checks that run on a schedule. When a check fails or recovers, the event is written to the asset's timeline and notifications are sent.

### Enabling a check

**ICMP (ping):**

```bash
langusta monitor enable --asset 1 --kind icmp --interval 60
```

Checks every 60 seconds whether the device responds to ping.

**TCP (port open):**

```bash
langusta monitor enable --asset 1 --kind tcp --port 443 --interval 300
```

Checks every 5 minutes whether port 443 is open.

**HTTP (web health):**

```bash
langusta monitor enable --asset 1 --kind http --port 80 --path /health --interval 60
```

**SNMP OID (value check):**

```bash
langusta monitor enable --asset 1 --kind snmp_oid \
  --oid 1.3.6.1.2.1.1.3.0 --credential-label office --interval 60
```

You can also check that the OID value matches an expected result:

```bash
langusta monitor enable --asset 1 --kind snmp_oid \
  --oid 1.3.6.1.2.1.2.2.1.8.1 --expected 1 --comparator eq \
  --credential-label office --interval 60
```

**SSH command (run a script on a remote device):**

```bash
langusta monitor enable --asset 1 --kind ssh_command \
  --command "systemctl is-active nginx" --user monitor \
  --credential-label ssh-key --interval 120
```

### Listing checks

```bash
langusta monitor list
```

### Disabling a check

```bash
langusta monitor disable 3
```

Use the check ID from `monitor list`.

### Running one cycle manually

```bash
langusta monitor run
```

This runs all checks that are due, right now, and exits. Useful for cron jobs or testing.

### Running the daemon

The preferred way is to let your operating system's service manager supervise it.

**Install the service file:**

```bash
langusta monitor install-service
```

This writes a systemd unit (Linux) or a launchd plist (macOS). Then:

```bash
# Linux:
systemctl --user daemon-reload
systemctl --user enable --now langusta-monitor.service

# macOS:
launchctl load ~/Library/LaunchAgents/uk.attv.langusta.monitor.plist
```

**Quick start (without a service manager):**

```bash
langusta monitor start          # starts the daemon in the background
langusta monitor status         # shows whether it is alive
langusta monitor stop           # stops the daemon
```

---

## Notifications

When a monitored device fails or recovers, LANgusta can send notifications through one or more "sinks".

### Webhook (Slack, Discord, Teams, and similar)

```bash
langusta notify add-webhook --label slack --url https://hooks.slack.com/services/...
```

The URL is encrypted at rest so the token in the path is not visible in the database.

### Email (SMTP)

```bash
langusta notify add-smtp \
  --label oncall \
  --host smtp.example.com \
  --port 587 \
  --from langusta@example.com \
  --to oncall@example.com \
  --starttls
```

SMTP credentials (if your server needs a login) are read from environment variables at send time:

```bash
export LANGUSTA_SMTP_USERNAME="langusta@example.com"
export LANGUSTA_SMTP_PASSWORD="app-specific-password"
```

### Log file

```bash
langusta notify add-logfile --label ops --path /var/log/langusta-events.jsonl
```

Each event is appended as one JSON line. This is separate from the always-on `~/.langusta/notifications.log`.

### Managing sinks

```bash
langusta notify list              # show all sinks
langusta notify disable 1         # pause a sink (keeps config)
langusta notify rm 1              # remove a sink
langusta notify test 1            # send a fake event to test
```

---

## Backups and exports

### Take a snapshot

```bash
langusta backup now
```

Snapshots are written to `~/.langusta/backups/`. Pre-migration backups are taken automatically before any schema upgrade.

### List snapshots

```bash
langusta backup list
```

### Verify a snapshot

```bash
langusta backup verify ~/.langusta/backups/db-20260702T120000Z.sqlite
```

Runs an integrity check against the file.

### Prune old snapshots

```bash
langusta backup prune --keep 30
```

Keeps the 30 most recent snapshots, deletes the rest.

### Export to JSON

```bash
langusta export --output dump.json
```

Exports all user-owned data (assets, timelines, MACs, provenance). Credentials are **not** included.

### Import from JSON

```bash
langusta import dump.json
```

The target database must be empty (freshly initialised).

---

## Importing from other tools

### Lansweeper

Export your Lansweeper data as CSV, then:

```bash
langusta import-lansweeper export.csv
```

Add `--dry-run` to preview without writing, or `-v` to see per-row errors.

LANgusta maps these columns automatically (case-insensitive):

| LANgusta field | Lansweeper column(s) tried |
|---|---|
| hostname | assetname, name |
| primary_ip | ipaddress, ip, ipv4 |
| mac | mac, macaddress |
| vendor | manufacturer, vendor |
| device_type | type, assettype, model |
| detected_os | operatingsystem, os |
| location | location, site, building |

Rows whose MAC matches an existing asset will merge. Rows with conflicting hostnames or IPs go to the review queue.

### NetBox

```bash
export LANGUSTA_NETBOX_TOKEN="your-api-token"
langusta import-netbox --url https://netbox.example.com
```

Imports devices from the NetBox API. Imported devices are marked with `imported` provenance so scans cannot silently overwrite them.

---

## The TUI (text interface)

```bash
langusta ui
```

Opens the full-screen Textual interface where you can browse assets, search, and configure monitoring.

### Key bindings

| Key | Action |
|---|---|
| `q` | Quit |
| `/` | Search assets |
| `m` | Open the monitoring screen |
| `?` | Show help |
| `Enter` | Open asset detail |

### Vim mode

If you prefer vim-style navigation:

```bash
export LANGUSTA_KEYBINDINGS=vim
```

This enables `j`/`k` (down/up), `g`/`G` (top/bottom), `Ctrl+d`/`Ctrl+u` (half-page down/up).

---

## Environment variables

| Variable | What it does |
|---|---|
| `LANGUSTA_HOME` | Use a different folder instead of `~/.langusta/` (must be an absolute path) |
| `LANGUSTA_MASTER_PASSWORD` | Supply the master password without prompting |
| `LANGUSTA_CRED_SECRET` | Secret for `cred add` (single-secret kinds) |
| `LANGUSTA_CRED_V3_USER` | SNMPv3 username |
| `LANGUSTA_CRED_V3_AUTH_PROTO` | SNMPv3 auth protocol (default: SHA) |
| `LANGUSTA_CRED_V3_AUTH_PASS` | SNMPv3 auth passphrase |
| `LANGUSTA_CRED_V3_PRIV_PROTO` | SNMPv3 privacy protocol (default: AES-128) |
| `LANGUSTA_CRED_V3_PRIV_PASS` | SNMPv3 privacy passphrase |
| `LANGUSTA_NETBOX_TOKEN` | API token for `import-netbox` |
| `LANGUSTA_SMTP_USERNAME` | SMTP login username (read at send time) |
| `LANGUSTA_SMTP_PASSWORD` | SMTP login password (read at send time) |
| `LANGUSTA_KEYBINDINGS` | Set to `=vim` for vim navigation in the TUI |

---

## Daily checklist

A quick reference for common tasks:

```bash
# See what changed since the last scan
langusta review

# Run a scan
langusta scan 192.168.1.0/24

# Check monitoring is running
langusta monitor status

# Run one monitoring cycle now
langusta monitor run

# Take a backup
langusta backup now

# List everything
langusta list
```

---

## Uninstalling

```bash
uv tool uninstall langusta     # removes the program
rm -rf ~/.langusta             # removes your data (only when you are sure)
```

---

## Need more detail?

- [Functional specification](docs/specs/01-functionality-and-moscow.md)
- [Technical specification](docs/specs/02-tech-stack-and-architecture.md)
- [Architecture Decision Records](docs/adr/)
- [Install guide](docs/install.md)
- [Upgrade guide](docs/upgrading.md)
- [Daemon setup (systemd / launchd)](docs/daemon.md)

---

*LANgusta is licensed under AGPL-3.0. No telemetry, no crash reporting, no update checks. Your data stays on your machine.*
