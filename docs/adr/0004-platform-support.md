# ADR-0004: Platform support — Linux + macOS first-class, Windows via WSL2

- **Status:** Accepted
- **Date:** 2026-04-17
- **Deciders:** maintainer, synthesized from 3-lens council (Pragmatist / Architect / Ecosystem)
- **Supersedes:** —
- **Superseded by:** —

## Context

Spec `docs/specs/02-tech-stack-and-architecture.md §17` names Windows-support scope as an open question. Target user (spec doc 1 §2) is Linux-comfortable and SSH-native. Platform-sensitive code includes:

- ARP reading (`ip neigh` Linux, `arp -a` macOS/Windows)
- ICMP (`icmplib` cross-platform unprivileged mode)
- mDNS (`zeroconf` cross-platform)
- File permissions (chmod 0600/0700 vs Windows ACLs)
- Daemon persistence (systemd / launchd / Windows Service)

Textual works natively on Windows 10+ (ConPTY / Windows Terminal). `uv tool install` works on Windows but adds PowerShell PATH-shim friction for non-developer users.

## Options considered

### Option A — Linux + macOS + Windows all first-class

Triples CI cost, requires MSI/winget/signed-binary discipline, demands parallel support for WSL2-NAT mDNS quirks and native Win32 ICMP raw-socket handling.

### Option B — Linux + macOS native; Windows = WSL2 only (no Win32 tolerance)

Clean scope. Rejects any Win32-defensive code.

### Option C — Linux only for v1

Cleanest scope. Cuts macOS, which the primary developer uses daily (`arp -a` already costs nothing to add).

### Option D — Linux + macOS fully supported; Windows "best effort, WSL2 recommended"

Full CI + bug-triage for Linux+macOS. Core code avoids gratuitous Win32 breakage (pathlib, guarded chmod, platform-dispatched ARP). Native Win32 is not tested and not a release blocker.

## Decision

**Option D — Linux + macOS first-class; Windows via WSL2 is the documented path; code stays Win32-tolerant but native Win32 is explicitly not tested or supported.** All three lenses agreed — unanimous consensus.

Implemented on top of a **Protocol-based `platform/` module from day one**: `PlatformBackend` protocol exposing capabilities (`arp_table()`, `icmp_probe()`, `mdns_browser()`, `perm_model`, `daemon_install()`, `term_caps()`). `core/` contains zero `if sys.platform` checks. Backends live in `platform/linux.py`, `platform/macos.py`, and a nominal `platform/windows.py` stub that raises `NotImplementedCapability` where Win32 genuinely diverges.

The tipping consideration: "best effort" only rots into fiction if the Protocol has no second backend actually exercised. Shipping macOS as first-class from day one keeps the seam honest. Windows-native support can be upgraded later by fleshing out the stub — without it, the protocol decays into Linux-shaped assumptions.

## Consequences

### Positive

- Target audience (SSH-native sysadmins on Linux, MSP techs on macOS laptops) gets first-class support.
- No MSI, no winget, no signed-binary discipline, no AV false-positive triage.
- Protocol-based `platform/` module is the correct long-term abstraction whether or not Windows ever becomes first-class — we build it right from the start.
- CI matrix stays manageable: Linux + macOS runners only for v1.

### Negative

- Lose ~15–20% of pure-Windows homelabbers who won't touch WSL2 (accepted — not the ICP).
- "Best effort" Windows invites bug reports we will close as `wontfix / use WSL2`. Need a clear triage policy in `CONTRIBUTING.md`.
- macOS `arp -a` output format drifts between versions; budget real test time there since it's a first-class target.
- WSL2 has documented sharp edges that will bite users: ICMP to host LAN requires mirrored networking mode (Win11 22H2+), mDNS across default NAT is historically flaky. **Document explicitly, early, on the install page.**

### Follow-up work

- Write `platform/base.py` (Protocol) **before** any scanner or daemon code depends on platform-specific calls.
- Windows stub: all capabilities raise `NotImplementedCapability("Windows native not supported in v1; use WSL2")` so failures are explicit, not silent.
- `chmod 0600` on secrets files: guard with a `perm_model.enforce_private()` capability; warn loudly if the backend can't enforce (Win32 without ACL fallback).
- README install section: two tabs — Linux/macOS one-liner via `uv`, and a WSL2 one-liner for Windows users. No native PowerShell instructions.
- CI: GitHub Actions matrix with `ubuntu-latest` + `macos-latest`. No `windows-latest` runner in v1.
- Triage policy in `CONTRIBUTING.md`: "Issues tagged `platform: windows-native` are `wontfix` for the v1 cycle. Reproduce in WSL2 or they're closed."

## Dissent / unresolved concerns

The Pragmatist steelman: the Purist is right that "sort of supports Windows" is the worst of both worlds — if native Win32 ever slips into the supported tier via user pressure, the maintenance bill compounds fast. Option B's hard "WSL2 only" line is cleaner. We accept Option D over B specifically on the strength of the Architect's point that a Protocol-based backend is the correct shape regardless, and a nominal Windows stub is a 50-line cost with real optionality upside. **Revisit if Windows-native issues grow to >5% of incoming bug volume** — at that point either commit to full support (Option A) or close the door (Option B).

## References

- `docs/specs/02-tech-stack-and-architecture.md §6` (ARP wrapper), `§10` (`platform/` module), `§16` (security defaults including file permissions), `§17` (open question)
- `docs/specs/01-functionality-and-moscow.md §2` (target user)
