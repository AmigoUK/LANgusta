# ADR-0005: Schema migration discipline — full discipline from 0.1.0, hand-rolled

- **Status:** Accepted
- **Date:** 2026-04-17
- **Deciders:** maintainer, synthesized from 3-lens council (Pragmatist / Architect / Ecosystem)
- **Supersedes:** —
- **Superseded by:** —

## Context

Spec `docs/specs/02-tech-stack-and-architecture.md §17` open question 5 recommends "ship a migration for every change from 0.1 onward, even in pre-1.0." The product promise (doc 1 §4 Pillar D, §8) is **institutional memory** — users will put years of timeline entries into the database, and losing that on `uv tool upgrade langusta` is reputation-fatal.

v1 schema is ~10–15 tables and will evolve during alpha. Single developer likely. ADR-0001 chose raw `sqlite3`, so Alembic is off the table (Alembic without SQLAlchemy is awkward).

## Options considered

### Option A — Full migration discipline from 0.1.0

Numbered forward-only migrations, auto-applied on startup. No "delete your db" in release notes ever. Backup-before-migrate mandatory.

### Option B — Breaking changes OK during 0.x

Release notes say "export → reinstall → import" between 0.x minors. Simpler for alpha velocity.

### Option C — Hybrid

Forward-only migrations from day 1, but with an explicit `--i-accept-data-loss` / `--unsafe-delete-db` flag for genuinely hostile pre-1.0 changes.

### Tool choice (secondary)

- Hand-rolled runner driving `PRAGMA user_version` (~80 lines).
- `yoyo-migrations` (lightweight, SQLAlchemy-free).
- Alembic (requires SQLAlchemy — ruled out by ADR-0001).

## Decision

**Option A — full migration discipline from 0.1.0, hand-rolled runner driving `PRAGMA user_version`, auto-applied on startup, with mandatory pre-migration backup and an absolute no-data-loss contract.**

Adopt two non-negotiable safety rails from the Ecosystem lens's hybrid proposal:

1. **Automatic pre-migration backup.** Before any migration runs, copy `langusta.db` to `langusta.db.pre-NNNN.bak` (retained for the last 3 upgrades). Protects against mid-migration crashes.
2. **Restore-from-old-backup must work.** A 0.2 backup restored into a 0.5 binary must migrate cleanly to 0.5 — not "undefined behaviour." Tests enforce this.

Migration files live at `db/migrations/NNN_description.sql` (DDL) with an optional paired `NNN_description.py` (data migration) hook. Forward-only, no down-migrations. Mistakes are corrected by new migrations, never by edits to shipped ones.

The tipping consideration: the product IS institutional memory. Any policy that ever loses user data during normal upgrade is incompatible with the pitch. The hand-rolled runner is ~80 lines; Alembic is ruled out by ADR-0001; `yoyo-migrations` is a reasonable alternative if we later outgrow hand-rolled.

## Consequences

### Positive

- `uv tool upgrade langusta` Just Works — matches the 2025/2026 self-hosted Python baseline (Paperless-ngx, Immich, Home Assistant, Dagster).
- Every schema delta is a replayable, testable artefact. Restore-from-old-backup is a tested contract, not "undefined."
- No dependency added (stays within ADR-0001's zero-data-deps posture).
- Clear migration history in `db/migrations/` — also a de facto changelog for schema evolution.

### Negative

- Pre-1.0 velocity slows: every schema tweak costs a numbered file and a test.
- No `--i-accept-data-loss` escape hatch. If a pre-1.0 rethink genuinely needs to rebuild from scratch, the path is "write the migration that rebuilds the table via `CREATE new / INSERT SELECT / DROP / RENAME`" — tedious, not impossible.
- SQLite's limited `ALTER TABLE` (pre-3.35 drop-column, limited rename, no ALTER CONSTRAINT) forces table-rebuild patterns for non-trivial changes. These must run inside a single transaction with FK deferral or backups corrupt mid-migrate.
- Solo-dev failure mode: skipping "just this once" under deadline pressure breaks the chain.

### Follow-up work

- Ship `db/migrations/001_initial_schema.sql` as the first migration (not as a `schema.sql` baseline) so the runner applies it from scratch on a fresh DB. No parallel "baseline schema" file to drift against.
- Migration runner contract (`db/migrate.py`):
  - Takes exclusive DB lock. Fails loudly on mismatch between binary `schema_version` and DB `PRAGMA user_version` that cannot be migrated forward.
  - Always writes `langusta.db.pre-NNNN.bak` before any DDL, using SQLite's online backup API (safe with WAL).
  - Applies each migration in a single transaction with `PRAGMA foreign_keys=OFF` during table-rebuild, reapplying after.
  - Records each applied migration (id, checksum, applied_at) in a `_migrations` table.
- CI test matrix:
  - Apply all migrations on an empty DB → final schema matches expected.
  - Apply migrations N..latest on a snapshot from version N, for every shipped N ≥ 0.1.0.
  - Test checksum-mismatch detection (shipped migrations are immutable).
- CI lint: every PR touching schema must include a new numbered migration file; `schema_version` bumped iff a new migration exists.
- Cross-process coordination (ADR-0002): daemon and TUI check `schema_version` on start; mismatch = refuse to run with a clear error, don't auto-migrate from two places.
- README: document the pre-migration backup location (`~/.langusta/backups/`) prominently.

## Dissent / unresolved concerns

**Ecosystem lens recommended Option C (hybrid with escape hatch)**, arguing that pre-1.0 with zero real users, every hour spent on migration plumbing is an hour not spent proving the tool is worth migrating *to*, and that an explicit `--i-accept-data-loss` flag is safer than the inevitable "just this once" skipped migration under deadline pressure. The counter-argument carried on the product-promise axis: institutional memory is the moat, and any sanctioned data-loss path poisons the pitch at the exact moment early adopters are deciding whether to trust us with their years of notes.

The Ecosystem lens's two *safety rails* (pre-migration backup, restore-from-old-backup tests) were adopted wholesale — they close the failure modes that the hybrid would have handled via the escape hatch, without needing the escape hatch itself.

**Revisit if** during 0.x a fundamental rethink genuinely requires more than 6 steps of table-rebuild choreography — at that point honestly evaluate whether a one-time sanctioned reset is cheaper than the migration chain, and if so, amend this ADR with the explicit exception.

## References

- `docs/specs/02-tech-stack-and-architecture.md §3` (data layer), `§9` (backup strategy — dovetails with pre-migration backups), `§17` open question 5
- `docs/specs/01-functionality-and-moscow.md §4` Pillar D (institutional memory), `§6` (backup/portability)
- Related: [ADR-0001](0001-data-layer-orm-choice.md) (ruled out Alembic), [ADR-0002](0002-process-architecture.md) (cross-process schema-version coordination)
