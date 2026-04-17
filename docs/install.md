# Installing LANgusta

LANgusta ships as a Python package on PyPI, installed via [`uv`](https://docs.astral.sh/uv/) into an isolated tool environment.

## Linux

### 1. Install uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
# then restart your shell, or source the shims file uv printed
```

### 2. Install LANgusta

```bash
uv tool install langusta
langusta --version        # should print 0.1.0rc1 or later
```

### 3. Enable unprivileged ICMP (required for `scan`)

LANgusta uses [icmplib](https://github.com/ValentinBELYN/icmplib) in unprivileged mode so it doesn't require root. Most modern distros allow this by default; if your kernel doesn't, enable it once:

```bash
sudo sysctl -w net.ipv4.ping_group_range="0 2147483647"
# persist across reboots:
echo "net.ipv4.ping_group_range=0 2147483647" | sudo tee /etc/sysctl.d/99-langusta.conf
```

If you skip this step, `langusta scan` will exit with a friendly message pointing at this page.

### 4. First run

```bash
langusta init    # prompts for master password (>=12 chars), sets up encrypted vault
langusta add --hostname router --ip 192.168.1.1
langusta scan 192.168.1.0/24
langusta ui      # Textual TUI
```

## macOS

Unprivileged ICMP is on by default on macOS. Install steps mirror Linux:

```bash
# uv:
curl -LsSf https://astral.sh/uv/install.sh | sh
# then:
uv tool install langusta
langusta init
```

## Windows (via WSL2)

Native Windows is not supported in v1. Use WSL2:

```powershell
wsl --install Ubuntu-24.04
# Restart Windows; the WSL2 shell opens automatically on first launch. Then:
curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install langusta
langusta init
```

### Known WSL2 gotchas

- **ICMP to host LAN** requires mirrored networking mode on Windows 11 22H2+. Without it, WSL2's default NAT hides devices from ICMP. See the WSL2 networking docs.
- **mDNS** across the default NAT is flaky. Devices that announce themselves will often be invisible from within WSL2. Use mirrored networking or run LANgusta on a Linux host.
- **`chmod 0600`** is enforced by LANgusta's Linux backend inside WSL2 — Windows-native chmod semantics are NOT used. If you move `~/.langusta/` across `/mnt/c/...`, permissions may not round-trip cleanly.

## Permissions

After `langusta init` the layout is:

```
~/.langusta/           0700
├── db.sqlite          0600
└── backups/           0700
```

`langusta init` re-applies these permissions on every run.

## Uninstalling

```bash
uv tool uninstall langusta
# removes the binary; your data stays at ~/.langusta/
rm -rf ~/.langusta     # when you're sure you don't want the data
```
