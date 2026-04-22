# Migrations

Hand-rolled forward-only SQL migrations; see `docs/adr/0005-schema-migration-discipline.md`
for the discipline.

## Numbering

Each file is `NNN_description.sql`. IDs are immutable once shipped —
never renumber or rewrite an applied migration (the runner
enforces this via the `_migrations.checksum` chain).

## The 004 gap

Wave-3 A-007: there is no `004_*.sql`. The id was burned during
development; `001_initial_schema.sql`, `002_fts_search.sql`,
`003_credentials.sql` then `005_monitoring.sql`. The runner tolerates
numeric gaps and uses `PRAGMA user_version` = the last applied id,
not a strict dense sequence — so the gap is harmless. Any future
migration keeps counting from the max-so-far; don't reuse `004`.
