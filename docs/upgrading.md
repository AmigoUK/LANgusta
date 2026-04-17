# Upgrading LANgusta

LANgusta's upgrade contract is set in [ADR-0005](adr/0005-schema-migration-discipline.md):

> **`uv tool upgrade langusta` never requires "delete your db".**
>
> Every schema change is a numbered, forward-only migration. Before any DDL, the migration runner writes a pre-migration backup to `~/.langusta/backups/`. A prior-version SQLite snapshot restored into a newer binary migrates forward cleanly.

## Normal path

```bash
uv tool upgrade langusta
langusta --version     # new version
langusta init          # apply any pending migrations (idempotent)
```

On the first `init` after an upgrade:

- The runner opens the DB, compares `PRAGMA user_version` with the binary's `latest_schema_version()`.
- If migrations are pending AND user data exists, a pre-migration backup is written to `~/.langusta/backups/db-pre-migration-NNNN-<timestamp>.sqlite`.
- Each pending migration is applied inside a transaction.
- `PRAGMA user_version` advances atomically with each migration's recorded row in `_migrations`.

## Safety rails

### Pre-migration backup

Every non-empty DB is snapshotted before a DDL run. These files live under `~/.langusta/backups/` with the `db-pre-migration-` prefix. They're never pruned by `backup prune`.

### Checksum immutability

Migration files are immutable once shipped. If a previously-applied migration file on disk no longer matches the checksum recorded in `_migrations`, the runner refuses to run and surfaces `MigrationChecksumError`. This catches accidental local edits and package-manager weirdness.

### Downgrade detection

If the DB's `user_version` is ahead of the binary's `latest_schema_version()`, the runner refuses to run — the binary was downgraded and cannot guarantee the schema. Install a newer version or restore from an older backup.

## Restoring from a pre-migration backup

Each snapshot is a plain SQLite file. To restore:

```bash
# 1. Stop anything using the DB.
systemctl --user stop langusta-monitor.service   # if you're running the daemon

# 2. Copy the pre-migration backup back in place.
cp ~/.langusta/backups/db-pre-migration-0003-20260417T120000Z.sqlite \
   ~/.langusta/db.sqlite

# 3. Let the newer binary migrate it forward.
langusta init
```

## Portable export / import

If you ever need to move LANgusta to a new machine:

```bash
# On the source:
langusta export --output ~/lang-dump.json

# On the destination (must be freshly-initialised, no assets yet):
langusta init
langusta import ~/lang-dump.json
```

Credentials are **excluded** from the default export. Re-add them with `langusta cred add` on the destination. (An `--include-secrets` path with an export password is a post-v1 stretch.)

## Downgrading

If you need to roll back:

```bash
uv tool install 'langusta==0.1.0rc1'
# but if the newer version applied a schema migration, the old binary
# cannot open the DB. Restore a pre-migration backup first (see above).
```
