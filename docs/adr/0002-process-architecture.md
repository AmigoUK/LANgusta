# ADR-0002: Process architecture — TUI + separate monitor daemon

- **Status:** Accepted
- **Date:** 2026-04-17
- **Deciders:** maintainer, synthesized from 3-lens council (Pragmatist / Architect / Ecosystem)
- **Supersedes:** —
- **Superseded by:** —

## Context

Spec `docs/specs/02-tech-stack-and-architecture.md §7` proposes a default where the TUI is one process, the monitor daemon is separately launched, and the scanner runs in-TUI as a Textual `@work` thread. Three workloads with different shapes share one SQLite file (WAL mode enables concurrent writers):

- **TUI** — interactive, latency-sensitive, must never freeze.
- **Scanner** — bursty (seconds to minutes), user-initiated, mix of I/O and subprocess.
- **Monitor** — recurring (≥60s per check × ≤250 assets ≈ ~4 writes/sec steady-state), must survive TUI exit.

APScheduler is the spec's scheduling choice (§7), SQLite-backed job store.

## Options considered

### Option A — Single async process (TUI + scanner + scheduler all in one)

Simplest install; `ps` shows one line. Monitoring stops when the TUI is closed — honest, but weakens the product promise.

### Option B — TUI + monitor daemon split; scanner in-TUI

TUI runs the scanner via `@work`. `langusta monitor daemon` is a separately-launched long-running process. Both write to the same SQLite file. This is the spec's default recommendation.

### Option C — Full three-process

TUI, scanner, monitor each in their own process. Maximum isolation, triples the IPC surface, not justified at ≤250-device scale.

## Decision

**Option B — TUI one process, `langusta monitor daemon` a separate process, scanner in-TUI via Textual `@work(thread=True)`**. All three vote cast by the council agreed on this — unanimous consensus.

The tipping consideration: monitoring's correctness is defined by surviving TUI exit and crashes. That alone earns the daemon its own process. The scanner does not share that property — if the TUI dies mid-scan, the scan can be resumed cheaply. Promoting the scanner to its own process would triple IPC surface for no gain in the common case.

## Consequences

### Positive

- Monitoring survives TUI crashes and closures — the product promise holds.
- One scheduler owner (the daemon), no split-brain over the APScheduler job store.
- `TimelineWriter` becomes the single integration contract between processes; SQLite is the sole IPC channel.
- Failure isolation matches workload shape: TUI crash loses UI state, not monitoring state.

### Negative

- Two processes visible in `ps` after `langusta ui`. Documented plainly in README.
- Users must arrange daemon persistence themselves — `uv tool` gives no systemd/launchd hooks. The idiomatic pattern (per the Ecosystem lens: `syncthing`, `pgadmin`, `glances -s`) is "we ship the `daemon` subcommand; you pick your supervisor."
- Scanner panics can still crash the TUI. Accepted because the user is present and scan state is checkpointed in SQLite.
- Schema migrations must coordinate across two processes briefly on version mismatch — see follow-up.

### Follow-up work

- Ship `langusta monitor {start,stop,status,tail}` as a first-class subcommand set — not an afterthought.
- Generate user-level systemd unit and launchd plist via `langusta monitor install-service` (cross-platform recipe objects, not branching in `core/`).
- Daemon must take an exclusive DB lock and fail loudly on schema-version mismatch with the TUI binary (defence against mid-upgrade races, per Ecosystem's warning about `uv tool upgrade` landing a new binary while an old daemon still holds WAL locks).
- Add a `meta.daemon_heartbeat` row updated every 30s; TUI footer shows "⚠ daemon stale" when >2min old — users must not silently lose monitoring.
- APScheduler tuning: `coalesce=True`, explicit `misfire_grace_time`, pinned tzdata — per Ecosystem's note on known APScheduler SQLiteJobStore quirks.
- Document daemon detachment: `start_new_session=True` or double-fork, never parent the daemon to the TUI (avoids Textual SIGWINCH / Ctrl+C / terminal-handoff bugs).

## Dissent / unresolved concerns

None at the decision level — all three lenses agreed on Option B. The Pragmatist did steelman Option A as defensible *if* we renamed the feature "best-effort monitoring while TUI runs," which would be an honest scope cut; we rejected that because it undermines the "monitoring reinforces institutional memory" pillar from spec doc 1 §4 (Pillar C).

## References

- `docs/specs/02-tech-stack-and-architecture.md §7` (monitoring subsystem), `§10` (structure)
- `docs/specs/01-functionality-and-moscow.md §4` Pillar C (recurring monitoring)
- Related: [ADR-0001](0001-data-layer-orm-choice.md) (shared DAL across processes), [ADR-0005](0005-schema-migration-discipline.md) (cross-process migration coordination)
