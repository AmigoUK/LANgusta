# ADR-0003: SNMP — hybrid behind a `SnmpClient` interface

- **Status:** Accepted
- **Date:** 2026-04-17
- **Deciders:** maintainer, synthesized from 3-lens council (Pragmatist / Architect / Ecosystem)
- **Supersedes:** —
- **Superseded by:** —

## Context

Spec `docs/specs/02-tech-stack-and-architecture.md §6` names `pysnmp-lextudio` as the chosen library with a documented fallback to shelling out to `net-snmp` if pysnmp proves flaky. v1 Must-Have: SNMP v2c (`docs/specs/01-functionality-and-moscow.md §7`). Should-Have: SNMP v3 authPriv.

The pysnmp ecosystem fractured after original-author deprecation drama (2024); the LeXtudio fork is the community continuation but still inherits long-tail bugs around v3 engine-ID discovery, contextEngineID caching, and some Counter64/OCTET STRING encoding edge cases. `net-snmp` is ubiquitous on Linux and macOS but weak on Windows, BSD-licensed (AGPL-compatible), and battle-tested against quirky vendor agents.

## Options considered

### Option A — `pysnmp-lextudio` only

Pure Python, preserves single-binary-like install on all platforms, one code path.

### Option B — Shell out to `net-snmp` only

Battle-tested, fast for bulk walks (`snmpbulkwalk`), but breaks "no external system packages required" — silently works on Debian, silently fails on fresh Fedora/macOS/Windows.

### Option C — Hybrid behind a `SnmpClient` interface

Define a protocol (`get`, `walk`, `bulk_walk`, async), ship `pysnmp-lextudio` as the default backend, allow a `NetSnmpSubprocessBackend` as an opt-in fallback when pysnmp misbehaves against a specific vendor agent.

### Option D — Roll our own

Minimal SNMPv2c implementation targeted at the specific OIDs we need. Rejected outright: SNMPv3 is specified, and the attack surface of hand-rolled ASN.1 / SNMP decoding is not one a two-person team should take on.

## Decision

**Option C — hybrid behind a `SnmpClient` interface, with `pysnmp-lextudio` as the only backend shipped in v1, and a documented extension point for a `NetSnmpSubprocessBackend` to be added when/if concrete user-reported vendor incompatibilities appear.**

The tipping consideration: the abstraction is mandatory either way. Once an `SnmpClient` protocol exists, testing becomes tractable (a `TranscriptSnmpClient` replays recorded PDU responses from fixtures) and swapping backends is cheap. Shipping only one backend in v1 keeps the install story clean; reserving the seam costs nothing and gives us an escape hatch.

## Consequences

### Positive

- Single-binary-like install preserved in v1 (only `pysnmp-lextudio` is a runtime dep).
- Scanner module depends on an interface, not on pysnmp's engine internals.
- Unit tests run without any SNMP target via the transcript backend.
- If `pysnmp` gets deprecated *again* in year 2, we swap one backend, not the scanner.

### Negative

- Designing the `SnmpClient` protocol correctly from day one costs real thought — auth/priv config, error type, capability flags for v3, async semantics.
- Users reporting vendor-specific SNMP bugs in v1 will hear "that backend isn't shipped yet" until we add the net-snmp backend.

### Follow-up work

- Write `scan/snmp/client.py` (protocol), `scan/snmp/pysnmp_backend.py` (default backend), `tests/fixtures/snmp_transcripts/` before touching any scanner orchestration code.
- Pin `pysnmp-lextudio` and `pyasn1` versions tightly; the Ecosystem lens flagged recent PyPI naming churn — lock and smoke-test on a clean `uv tool install` in CI.
- SNMP must be **genuinely optional**: hosts with no SNMP response mark the asset `snmp: unavailable` and continue ICMP/ARP/mDNS — never fail a scan because SNMP didn't answer.
- Budget a real afternoon for v3 authPriv when it lands; engine-ID and USM timing are documented sharp edges.
- Document: "v1 ships with the pure-Python backend; if you hit a vendor agent pysnmp can't parse, file an issue — the net-snmp fallback backend is a planned extension."

## Dissent / unresolved concerns

**Pragmatist lens recommended Option A** (pysnmp-lextudio only, no hybrid abstraction), arguing that for ≤250 devices the abstraction is dead weight carrying a second-backend deprecation that may never come, and that shipping the wedge fast matters more than preserving future optionality. The counter-argument carried: even if we only ever ship one backend, the `SnmpClient` interface is the only way to get the scanner unit-testable without live SNMP targets, so the cost is sunk whether or not a second backend ever lands. **Revisit if year-1 telemetry shows zero pysnmp-parsing bug reports** — then the second-backend extension point can be deleted as YAGNI without losing the testability abstraction.

## References

- `docs/specs/02-tech-stack-and-architecture.md §6` (scanning libraries)
- `docs/specs/01-functionality-and-moscow.md §4` Pillar B (scanner)
- [pysnmp-lextudio on GitHub](https://github.com/lextudio/pysnmp)
