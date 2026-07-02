# REMEDIATION PLAN — LANgusta Audit Findings

**Reference:** `AUDIT_REPORT.md` (39 findings: 3 🔴 · 9 🟠 · 15 🟡 · 12 🟢)
**Goal:** Actionable, file-and-line-specific fix plan with dependency ordering, code sketches, test additions, and verification steps.
**Convention:** Each task is independently committable. Tasks within a wave are parallelizable; waves are ordered by dependency.

---

## Wave overview

| Wave | Theme | Tasks | Blocks |
|------|-------|-------|--------|
| 1 | Critical — product correctness | X-1, X-2, X-3 | Waves 3-4 |
| 2 | High — safety rails & invariant tests | X-4, X-5, D-1, D-2, S-1, S-2, R-1, A-1, T-1, T-2, T-3 | — |
| 3 | Medium — robustness & consistency | 15 tasks | — |
| 4 | Low — polish | 12 tasks | — |

---

## Wave 1 — Critical fixes

### Task X-1: Fix authenticated SMTP

**Problem:** `LANGUSTA_SMTP_USERNAME` / `LANGUSTA_SMTP_PASSWORD` documented but never read. `send_smtp` always gets `username=None, password=None`.

**Files to change:**

1. **`src/langusta/monitor/notifications.py:129-137`** — read env vars in `send_smtp`:

```python
async def send_smtp(config: dict, event: MonitorEvent) -> bool:
    import os
    cfg = SmtpConfig(
        host=config["host"],
        port=int(config["port"]),
        sender=config["from"],
        recipient=config["to"],
        starttls=bool(config.get("starttls", False)),
        username=config.get("username") or os.environ.get("LANGUSTA_SMTP_USERNAME"),
        password=config.get("password") or os.environ.get("LANGUSTA_SMTP_PASSWORD"),
    )
```

2. **`src/langusta/cli.py:1133`** — update the docstring to clarify env-var resolution happens at send time (not storage time). Current docstring is already accurate for the env-var approach; verify it matches.

3. **`AGENTS.md`** — the env-var table entry for `LANGUSTA_SMTP_*` is already listed; no change needed.

**Tests to add** (`tests/unit/monitor/test_notifications.py`):
- `test_smtp_uses_env_vars_for_credentials` — monkeypatch `LANGUSTA_SMTP_USERNAME`/`PASSWORD`, call `send_smtp` with a config dict lacking username/password, assert `smtp.login` is called with the env-var values.
- `test_smtp_skips_login_without_credentials` — no env vars set, config has no username → assert `smtp.login` is never called (open-relay path still works).

**Verification:**
```bash
uv run pytest tests/unit/monitor/test_notifications.py -v
uv run pytest tests/integration/test_cli_notify.py -v
```

**Effort:** ~1 hour. **Risk:** Low — additive; existing open-relay SMTP users are unaffected.

---

### Task X-2: Route import paths through the invariant-enforcing identity resolver

**Problem:** Two bypasses of the scanner-proposes invariant:
- **NetBox** (`import_netbox.py:161-182`) does raw `INSERT INTO assets` — no identity resolution, no provenance merge, no MAC binding.
- **Lansweeper** (via `import_common._resolve_identity:85-104`) resolves identity using MAC + IP only — **never checks hostname**. A row whose MAC→asset A but hostname→asset B is silently merged into A, whereas `core.identity.resolve` would return `Ambiguous`.

**Strategy:** Unify all import paths on `core.identity.resolve` + `merge_scan_result`.

#### Sub-task X-2a: Fix `import_common._resolve_identity` to use `core.identity.resolve`

**File:** `src/langusta/db/import_common.py:85-104`

Replace the hand-rolled `_resolve_identity` with a call to `core.identity.resolve`:

```python
from langusta.core.identity import (
    AssetIdentity, Candidate, Ambiguous, Insert, Update, resolve,
)
from langusta.db.writer import _list_identities  # reuse the writer's projection

def _resolve_via_core(
    conn: sqlite3.Connection,
    *,
    hostname: str | None,
    primary_ip: str | None,
    mac: str | None,
) -> Resolution:
    """Delegate to core.identity.resolve for hostname-aware matching."""
    identities = _list_identities(conn)
    macs = frozenset({normalize_mac(mac)}) if mac else frozenset()
    candidate = Candidate(hostname=hostname, primary_ip=primary_ip, macs=macs)
    return resolve(candidate, identities)
```

Then update `apply_imported_observation` (lines 291-337) to dispatch on `Insert | Update | Ambiguous`:

```python
def apply_imported_observation(conn, *, fields, mac, now) -> ImportOutcome:
    resolution = _resolve_via_core(
        conn, hostname=fields.get("hostname"),
        primary_ip=fields.get("primary_ip"), mac=mac,
    )
    if isinstance(resolution, Insert):
        asset_id = insert_imported_asset(conn, fields=fields, mac=mac, now=now)
        return Inserted(asset_id=asset_id)

    if isinstance(resolution, Update):
        return _apply_update(conn, asset_id=resolution.asset_id, fields=fields, mac=mac, now=now)

    # Ambiguous — defer to review queue
    return _defer_to_review(
        conn, fields=fields, mac=mac,
        candidates=[{"asset_id": aid, "score": conf, "reason": resolution.reason}
                     for aid, conf in resolution.candidates],
        now=now,
    )
```

**Note:** `_list_identities` in `writer.py:108-127` is currently prefixed `_` (private). Either make it public (rename to `list_identities`) or duplicate the projection logic. Prefer reusing to avoid divergence.

**Also fix:** `import_common.py:93,155,217,275,350` — replace all `mac.lower()` with `normalize_mac(mac)` (finding L-2, can be done in this task).

#### Sub-task X-2b: Route NetBox through `apply_imported_observation`

**File:** `src/langusta/db/import_netbox.py:114-188`

Replace the raw `INSERT` block (lines 161-182) with a call to `apply_imported_observation`:

```python
from langusta.db.import_common import apply_imported_observation, Inserted, Updated, Deferred

# Inside the device loop:
fields = {}
if hostname: fields["hostname"] = hostname
if ip: fields["primary_ip"] = ip
if vendor: fields["vendor"] = vendor
if model: fields["device_type"] = model

outcome = apply_imported_observation(conn, fields=fields, mac=None, now=now)
if isinstance(outcome, Inserted):
    imported += 1
elif isinstance(outcome, (Updated, Deferred)):
    skipped += 1  # merged into existing or deferred to review
```

Remove `_ip_exists` usage (line 154) — `apply_imported_observation` handles identity resolution including IP matches. Remove dead code `_mac_exists` (line 89-92, finding L-5).

**Tests to add:**

1. **`tests/unit/db/test_import_common.py`** (or extend existing):
   - `test_import_mac_hostname_conflict_defers_to_review` — seed asset A with MAC `aa:...`, asset B with hostname `bravo`. Import row with `mac=aa:...` + `hostname=bravo`. Assert outcome is `Deferred` and `review_queue` has a row.

2. **`tests/unit/db/test_import_netbox.py`**:
   - `test_netbox_import_conflicting_hostname_produces_proposed_change` — seed a MANUAL asset with hostname `router`. Import NetBox device with same IP, different hostname. Assert `proposed_changes` row created, not a silent insert.
   - `test_netbox_import_merges_by_mac` — seed asset with MAC `aa:...`. Import NetBox device with same MAC. Assert `Updated`, not `Inserted`.

**Verification:**
```bash
uv run pytest tests/unit/db/test_import_common.py tests/unit/db/test_import_netbox.py tests/integration/test_cli_import_lansweeper.py tests/integration/test_cli_import_netbox.py -v
```

**Effort:** ~4 hours. **Risk:** Medium — changes import semantics for Lansweeper (previously-silent merges will now defer). This is *correct* per the invariant, but could surprise users with existing workflows. Document in CHANGELOG.

**Dependency:** None. Can start immediately.

---

### Task X-3: Prevent scan from killing the monitor daemon

**Problem:** The scanner holds the SQLite write transaction open across all network I/O (`start_scan` at orchestrator.py:97 begins a transaction; it isn't committed until line 182). When the daemon tries to write (`set_heartbeat`, `record_result`) during a scan, it hits `busy_timeout=5000` → `OperationalError` → the unguarded `while True` loop exits → daemon dies.

Two independent fixes, both required:

#### Sub-task X-3a: Commit after `start_scan` so the write lock is released during network I/O

**File:** `src/langusta/scan/orchestrator.py:97-100`

```python
scan_id = scans_dal.start_scan(conn, target=target, now=start)
conn.commit()  # Release the write lock before network I/O begins.
```

The observation loop (lines 146-173) will re-acquire the write lock per `apply_scan_observation` call. Each of those is already atomic via the enclosing `connect()` context. The final commit at line 182 handles the post-scan backup and scan-row completion.

**Risk:** Low — `apply_scan_observation` writes are visible to the daemon sooner (each observation commits individually via the context manager). This is actually *better* for consistency.

#### Sub-task X-3b: Add per-cycle error recovery to the daemon loop

**File:** `src/langusta/cli.py:938-953`

Wrap the cycle body in `try/except` with back-off:

```python
try:
    while True:
        now = datetime.now(UTC)
        try:
            with connect(paths.db_path()) as conn:
                summary = asyncio.run(
                    run_once(conn, now=now, notifications_logfile=logfile, vault=vault),
                )
        except Exception as exc:
            typer.echo(
                f"[{now.isoformat(timespec='seconds')}] "
                f"monitor cycle failed: {exc}; retrying in {interval}s",
                err=True,
            )
            _time.sleep(interval)
            continue
        typer.echo(
            f"[{now.isoformat(timespec='seconds')}] "
            f"executed {summary.executed} "
            f"({summary.ok_count} ok, {summary.fail_count} fail, "
            f"{summary.transitions} transitions)"
        )
        _time.sleep(interval)
finally:
    daemon_control.clear_pid_file(pid_path)
```

**Tests to add** (`tests/unit/monitor/test_daemon_control.py` or new `test_daemon_loop.py`):
- `test_daemon_cycle_survives_database_locked` — mock `run_once` to raise `sqlite3.OperationalError("database is locked")` on first call, succeed on second. Assert the loop continues (test via a capped-iteration wrapper).

**Verification:**
```bash
uv run pytest tests/unit/scan/test_orchestrator.py tests/unit/monitor/ -v
# Manual integration test:
LANGUSTA_HOME=/tmp/test-home uv run langusta init
LANGUSTA_HOME=/tmp/test-home uv run langusta monitor enable --asset 1 --kind icmp --interval 5
LANGUSTA_HOME=/tmp/test-home uv run langusta monitor start &
LANGUSTA_HOME=/tmp/test-home uv run langusta scan 127.0.0.1/32  # should not kill daemon
LANGUSTA_HOME=/tmp/test-home uv run langusta monitor status
```

**Effort:** ~2 hours. **Risk:** Low.

---

## Wave 2 — High-priority fixes

### Task X-4: Add schema-version guard at daemon startup and after upgrade

**Problem:** `migrate()` is called only from `init` (cli.py:111). No other command checks schema version. ADR-0002 mandates daemon schema-version coordination; it's unimplemented.

**Files to change:**

1. **`src/langusta/db/migrate.py`** — add a public guard function:

```python
def assert_schema_current(db_path: DbPath) -> None:
    """Raise RuntimeError if the DB schema lags behind the binary."""
    current = current_schema_version(db_path)
    latest = latest_schema_version()
    if current < latest:
        raise RuntimeError(
            f"database schema version {current} is behind the binary's "
            f"latest migration ({latest}); run `langusta init` to migrate"
        )
```

2. **`src/langusta/cli.py:921`** (daemon startup, after vault unlock, before the loop):

```python
assert_schema_current(paths.db_path())  # refuse to start on stale schema
```

Import: `from langusta.db.migrate import assert_schema_current`

3. **`src/langusta/cli.py:91`** (top of `init`) — `init` already calls `migrate()`, so it's correct.

4. **Optional defense-in-depth:** add a guard in `_unlock_vault()` or a shared preamble for write commands. At minimum, add to `scan` and `add`:

```python
# At the top of scan(), add(), monitor enable, import commands:
from langusta.db.migrate import assert_schema_current
assert_schema_current(paths.db_path())
```

**Tests to add** (`tests/unit/db/test_migrate.py`):
- `test_assert_schema_current_raises_on_stale_db` — migrate to migration N-1, assert the guard raises.
- `test_assert_schema_current_passes_on_current_db` — full migrate, assert no raise.

**Verification:**
```bash
uv run pytest tests/unit/db/test_migrate.py -v
```

**Effort:** ~2 hours. **Risk:** Low — read-only check before any write.

---

### Task X-5: Fix vacuous and weak invariant tests

**Problem:** Two tests overstate their coverage.

#### Sub-task X-5a: Fix vacuous MANUAL-field property test

**File:** `tests/unit/core/test_provenance.py:164-177`

The assertion body is unreachable because `merge_scan_result` never puts MANUAL fields into `applied`. Rewrite to assert MANUAL fields are **never** in `applied`:

```python
@given(existing=existing_state(), incoming=incoming_observations())
def test_property_manual_fields_never_overwritten(
    existing: dict[str, FieldValue],
    incoming: dict[str, str],
) -> None:
    """The load-bearing invariant: for ANY state x ANY observation,
    MANUAL-provenance fields NEVER appear in the applied dict."""
    applied, _ = merge_scan_result(existing, incoming, now=NOW)
    for field_name, prior in existing.items():
        if prior.provenance is FieldProvenance.MANUAL:
            assert field_name not in applied, (
                f"MANUAL field {field_name!r} leaked into applied — "
                f"invariant violated"
            )
```

Apply the same fix to the IMPORTED variant at lines 180-188.

#### Sub-task X-5b: Strengthen writer idempotency property test

**File:** `tests/property/test_writer_idempotency.py:43-73`

Seed mixed-provenance assets before the property test, generate full `Observation` fields, and assert the `proposed_changes` path fires when expected:

```python
@settings(max_examples=50, deadline=None)
@given(
    obs_host=hostnames,
    obs_ip=ipv4,
    obs_mac=st.one_of(st.none(), macs),
    obs_vendor=st.one_of(st.none(), st.text(min_size=1, max_size=20)),
)
def test_apply_scan_observation_proposes_on_conflicting_manual(
    tmp_path_factory, obs_host, obs_ip, obs_mac, obs_vendor,
) -> None:
    db = tmp_path_factory.mktemp("wr") / "db.sqlite"
    migrate(db)
    now = datetime(2026, 4, 20, 12, 0, 0, tzinfo=UTC)

    # Seed an asset with MANUAL hostname at the same IP.
    with connect(db) as conn:
        assets_dal.insert_manual(
            conn, hostname="seeded-manual", primary_ip=obs_ip,
            mac="00:00:00:00:00:01", now=now,
        )

    obs = Observation(
        hostname=obs_host, primary_ip=obs_ip, mac=obs_mac, vendor=obs_vendor,
    )
    with connect(db) as conn:
        sid = scans_dal.start_scan(conn, target=f"{obs_ip}/32", now=now)
        outcome = apply_scan_observation(conn, obs, scan_id=sid, now=now)
        # If the scan observed a DIFFERENT hostname, it must go to proposed_changes.
        if obs_host != "seeded-manual":
            pc_rows = conn.execute("SELECT COUNT(*) FROM proposed_changes").fetchone()[0]
            assert pc_rows >= 1, "Conflicting hostname on MANUAL field should produce a proposed change"
```

**Verification:**
```bash
uv run pytest tests/unit/core/test_provenance.py tests/property/test_writer_idempotency.py -v
```

**Effort:** ~2 hours. **Risk:** None — test-only.

---

### Task D-1: Fix pre-migration backup naming/parser so prune/verify can manage them

**Problem:** `_parse_stamp` extracts `"pre"` from `db-pre-migration-0006-20260702T120000Z.sqlite`, not the timestamp. Pre-migration backups are invisible to `list_backups`, `prune`, and `verify`.

**Files to change:**

**Option A (preferred — rename files):** Change `migrate._write_backup` to produce parseable names:

**`src/langusta/db/migrate.py:196`**:
```python
dst = backups_dir / f"db-{ts}-pre-migration-{current_version:04d}.sqlite"
```

This puts the timestamp first (`db-20260702T120000Z-pre-migration-0006.sqlite`), which `_parse_stamp` already handles via `core.split("-", 1)[0]`.

**`src/langusta/db/backup.py:31-40`** — `_parse_stamp` already extracts `stamp_str = core.split("-", 1)[0]` which now correctly yields the timestamp. No change needed if Option A is chosen.

**Migration:** Existing pre-migration backups with the old name will be invisible (harmless — they just won't be pruned, which is the current behavior anyway). No data loss.

**Tests to add** (`tests/unit/db/test_backup.py`):
- `test_pre_migration_backup_is_listed_and_pruned` — create a file named `db-20260101T000000Z-pre-migration-0001.sqlite`, assert `list_backups` finds it, assert `prune(keep=0)` deletes it.

**Verification:**
```bash
uv run pytest tests/unit/db/test_backup.py tests/property/test_backup_dedupe.py -v
```

**Effort:** ~1 hour. **Risk:** Low.

---

### Task D-2: Run `foreign_key_check` inside each migration transaction

**Problem:** `PRAGMA foreign_key_check` runs after the entire chain commits (migrate.py:322). A migration that creates FK orphans is already committed with `user_version` advanced.

**File:** `src/langusta/db/migrate.py:300-327`

Move the FK check inside the per-migration loop, before `COMMIT`:

```python
for mig in pending:
    conn.execute("BEGIN")
    try:
        for stmt in _split_statements(mig.sql):
            conn.execute(stmt)
        # Check FK integrity BEFORE committing this migration.
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(
                f"migration {mig.id} produced dangling FK references: "
                f"{[tuple(v) for v in violations]}"
            )
        conn.execute(
            "INSERT INTO _migrations (id, description, checksum, applied_at) "
            "VALUES (?, ?, ?, ?)",
            (mig.id, mig.description, mig.checksum,
             datetime.now(UTC).isoformat(timespec="seconds")),
        )
        conn.execute(f"PRAGMA user_version = {mig.id}")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
```

Remove the post-loop FK check (lines 322-327).

**Tests to add** (`tests/unit/db/test_migrate.py`):
- `test_migration_with_fk_violation_rolls_back` — craft a fake migration dir with a migration that inserts an orphan FK row. Assert `migrate()` raises and `user_version` is NOT advanced.

**Verification:**
```bash
uv run pytest tests/unit/db/test_migrate.py tests/property/test_migration_checksum.py -v
```

**Effort:** ~1.5 hours. **Risk:** Low — strictly more conservative.

---

### Task S-1: Enforce 0600 on backup files

**Problem:** `backup.write` creates backup files with default umask (typically `0644`).

**File:** `src/langusta/db/backup.py:80-94`

After `src.backup(out)`, chmod the destination:

```python
import os
import stat

backups_dir.mkdir(parents=True, exist_ok=True)
# Ensure the directory itself is private (in case it was recreated outside init).
os.chmod(backups_dir, stat.S_IRWXU)  # 0700

# ... existing dedup / name logic ...

with (
    closing(sqlite3.connect(str(src_path))) as src,
    closing(sqlite3.connect(str(dst))) as out,
):
    src.backup(out)
os.chmod(dst, stat.S_IRUSR | stat.S_IWUSR)  # 0600
return dst
```

Also fix `verify()` (line 113) to use `closing()` (finding A-3):
```python
with closing(sqlite3.connect(str(path))) as conn:
    row = conn.execute("PRAGMA integrity_check").fetchone()
```

**Tests to add** (`tests/integration/test_cli_backup_export.py` or `tests/unit/db/test_backup.py`):
- `test_backup_file_mode_is_0600` — call `backup.write()`, assert `stat.S_IMODE(dst.stat().st_mode) == 0o600`.

**Verification:**
```bash
uv run pytest tests/unit/db/test_backup.py tests/integration/test_cli_backup_export.py -v
```

**Effort:** ~1 hour. **Risk:** Low.

---

### Task S-2: Encrypt webhook URLs

**Problem:** Webhook URLs (bearer tokens) stored in plaintext in `notification_sinks.config`.

**File:** `src/langusta/db/notifications.py:61-64` and `src/langusta/cli.py:1091-1094`

**Approach:** Store the webhook URL encrypted via the vault, keyed by the sink ID.

1. **`cli.py:1091-1094`** — at `notify add-webhook` creation time, encrypt the URL before storing:

```python
vault = _unlock_vault()
envelope = vault.encrypt(url.encode("utf-8"))
config = {
    "url_nonce": envelope.nonce.hex(),
    "url_ciphertext": envelope.ciphertext.hex(),
}
sid = notif_dal.create(conn, label=label, kind="webhook", config=config, now=now)
```

2. **`monitor/notifications.py:98-101`** — at send time, decrypt. The `dispatch` function needs access to the vault. Currently `dispatch(event, sinks, logfile_path)` doesn't receive a vault.

**Refactor needed:** Thread the vault through `run_once` → `dispatch` → `send_webhook`. Add a `vault: Vault | None` parameter to `dispatch` and `send_webhook`:

```python
async def send_webhook(config: dict, event: MonitorEvent, *, vault: Vault | None = None) -> bool:
    url = _decrypt_url(config, vault) if vault else config.get("url")
    if not url:
        return False
    # ... rest unchanged
```

Add `_decrypt_url(config, vault)` helper that reconstructs the `Envelope` from hex and decrypts.

3. **`monitor/runner.py`** — pass `vault` to `dispatch_event` calls (the vault is already available in `run_once`).

**Migration concern:** Existing plaintext webhook URLs need migration. Add migration `008_webhook_encryption.sql` (schema is JSON text, so no DDL change needed — the migration is data-only, or handle in code by falling back to `config.get("url")` if encrypted fields are absent).

**Tests to add:**
- `test_webhook_url_encrypted_at_rest` — add a webhook, read raw DB bytes, assert the URL string is not present.
- `test_webhook_url_decrypts_at_send_time` — add a webhook, trigger send, assert the POST goes to the correct URL.
- Add to `test_secret_hygiene.py`: `test_webhook_secret_not_in_raw_db_bytes`.

**Effort:** ~3 hours. **Risk:** Medium — changes the notification dispatch signature. Requires careful threading of vault through the call chain.

---

### Task R-1: Remove unused APScheduler dependency

**Problem:** `apscheduler>=3.10,<4` is declared and documented but never imported. The daemon uses a plain `while True`/`sleep` loop.

**Files to change:**

1. **`pyproject.toml:33`** — remove `"apscheduler>=3.10,<4"` from dependencies.
2. **`pyproject.toml:75`** — remove `"ignore::DeprecationWarning:apscheduler.*"` from filterwarnings.
3. **`docs/specs/02-tech-stack-and-architecture.md`** — update §7 to describe the sleep-loop design.
4. **`docs/adr/0002-process-architecture.md:17,61`** — add a note: *"APScheduler was the original spec choice; the shipped implementation uses a plain sleep loop, which is simpler and sufficient at ≤250-device scale."*
5. **`docs/development-plan.md:253,256`** — update to reflect shipped design.
6. **`uv.lock`** — regenerate via `uv lock`.

**Verification:**
```bash
uv lock
uv sync --all-extras
uv run pytest -v  # confirm no import errors
uv run langusta --version
```

**Effort:** ~1 hour. **Risk:** None — removing dead weight.

---

### Task A-1: Add per-check timeout in the runner

**Problem:** No outer `asyncio.timeout` around `impl.run()`. A single hung check can consume a semaphore slot indefinitely.

**File:** `src/langusta/monitor/runner.py:301-305`

```python
DEFAULT_CHECK_TIMEOUT_SECONDS = 30  # module-level constant

async with semaphore:
    try:
        async with asyncio.timeout(DEFAULT_CHECK_TIMEOUT_SECONDS):
            result: CheckResult = await impl.run(target=target, **config)
    except TimeoutError:
        result = CheckResult(
            status="fail", latency_ms=None,
            detail=f"check timed out after {DEFAULT_CHECK_TIMEOUT_SECONDS}s",
        )
    except Exception as exc:
        result = CheckResult(status="fail", latency_ms=None, detail=str(exc))
```

**Tests to add** (`tests/unit/monitor/test_runner.py`):
- `test_hung_check_times_out` — register a check whose `run()` sleeps 60s. Assert the runner returns a fail result within ~31s (use `DEFAULT_CHECK_TIMEOUT_SECONDS=1` via parameter injection to keep the test fast).

**Verification:**
```bash
uv run pytest tests/unit/monitor/test_runner.py -v
```

**Effort:** ~1 hour. **Risk:** Low — defense-in-depth.

---

### Task T-1: Add a lint that timeline DAL offers no mutation API

**Problem:** Timeline immutability is trigger-tested but nothing prevents a future contributor from adding `def update_entry()` to `db/timeline.py`.

**File:** `scripts/lint_boundaries.py` — add a new check:

```python
def check_timeline_dal_is_insert_only(src_root: Path) -> list[str]:
    """Flag UPDATE/DELETE function definitions in db/timeline.py."""
    timeline = src_root / "db" / "timeline.py"
    if not timeline.is_file():
        return []
    violations = []
    text = timeline.read_text(encoding="utf-8")
    for match in re.finditer(r'def\s+(update_|delete_|remove_|modify_)\w+', text):
        violations.append(
            f"{timeline}: mutable function defined on insert-only DAL: "
            f"{match.group()}"
        )
    return violations
```

Add to `run_all_checks`.

**Test:** `tests/unit/test_boundary_lints.py` — add a test that a synthetic timeline.py with `def update_entry` triggers the violation.

**Effort:** ~1 hour. **Risk:** None.

---

### Task T-2: Set `raising=True` on offline-scan fixture

**Problem:** `raising=False` silently no-ops if imports are renamed, potentially sending tests online.

**File:** `tests/conftest.py:30-38`

```python
monkeypatch.setattr(
    "langusta.scan.orchestrator.resolve_many", empty_rdns, raising=True,
)
monkeypatch.setattr(
    "langusta.scan.orchestrator.probe_ports_many", empty_tcp, raising=True,
)
monkeypatch.setattr(
    "langusta.scan.orchestrator.mdns_discover", empty_mdns, raising=True,
)
```

**Verification:**
```bash
uv run pytest -v  # confirm all tests still pass with strict patching
```

**Effort:** 15 min. **Risk:** None — if any test fails, it means the import was already wrong.

---

### Task T-3: Extend CI smoke to cover scan + review

**Problem:** CI e2e walkthrough never exercises the scanner or the review queue.

**File:** `.github/workflows/ci.yml:47-64`

Add after the existing smoke steps:

```yaml
      - name: CLI smoke (scan + review invariant)
        env:
          LANGUSTA_HOME: ${{ runner.temp }}/langusta
          LANGUSTA_MASTER_PASSWORD: ci-smoke-master-password-long-enough
        run: |
          uv run langusta add --hostname ci-manual --ip 10.0.0.99
          # Scan localhost (offline-safe; the conftest enrichment patches
          # don't apply here, but ICMP to 127.0.0.1 should succeed in CI).
          uv run langusta scan 127.0.0.1/32 || true
          uv run langusta list
          uv run langusta review
```

**Note:** ICMP may not work in all CI runners. If so, add `|| true` and focus on asserting the commands don't crash. The invariant is better tested at the unit level (which Wave 2/X-5 already strengthens).

**Effort:** ~30 min. **Risk:** Low.

---

## Wave 3 — Medium fixes

| ID | Task | File(s) | Effort |
|----|------|---------|--------|
| D-3 | Add `UNIQUE` constraint on `assets.primary_ip` (migration `008_*`) | `db/migrations/` + `db/assets.py` | 2h |
| D-4 | Document `ON DELETE CASCADE` vs immutability trigger as intentional | `db/migrations/001_initial_schema.sql` (comment) | 15min |
| D-5 | Add `check_results` retention pruning in daemon cycle | `monitor/runner.py` + `db/monitoring.py` | 2h |
| L-3 | Centralize provenance strings via `FieldProvenance.X.value` in SQL | All `db/*.py` SQL sites | 2h |
| L-4 | Conditional UPDATE on `proposed_changes.accept` | `db/proposed_changes.py:190-206` | 1h |
| A-2 | Cancel `snmp_gather` in orchestrator `finally` | `scan/orchestrator.py:140-142` | 30min |
| A-4 | Narrow `filterwarnings` to langusta-specific errors | `pyproject.toml:73-81` | 1h |
| A-5 | Narrow `ResourceWarning` ignore to pysnmp/zeroconf | `pyproject.toml:79` | 30min |
| A-6 | Replace string-match exit code with exception subclasses | `scan/snmp/credentials.py` + `cli.py:285` | 1h |
| R-2 | Move `PRAGMA database_list` to a `db/` DAL function | `scan/orchestrator.py:52-66` → `db/connection.py` | 1h |
| R-3 | Document boundary-lint blind spots (f-string, import variants) | `scripts/lint_boundaries.py` (docstring) | 30min |
| R-6 | Move test backends out of production wheel | `hatch.build.targets.wheel` exclude in `pyproject.toml` | 1h |
| S-3 | Split TOFU first-use into key-record + auth phases | `monitor/ssh/asyncssh_backend.py` | 4h |
| S-4 | Strip all `LANGUSTA_*` secret env vars from daemon subprocess | `cli.py:994-997` | 30min |
| T-5 | Raise `max_examples` to 100 on invariant property tests | `tests/property/*.py` | 15min |

### Detail for selected medium tasks:

#### D-3: UNIQUE constraint on `primary_ip`

**Migration `008_unique_primary_ip.sql`:**
```sql
-- Deduplicate any existing IP collisions before adding the constraint.
DELETE FROM assets WHERE id NOT IN (
    SELECT MIN(id) FROM assets GROUP BY primary_ip
) AND primary_ip IS NOT NULL;
CREATE UNIQUE INDEX idx_assets_primary_ip_unique ON assets(primary_ip)
    WHERE primary_ip IS NOT NULL;
```

Use a partial unique index (SQLite supports `WHERE` on indexes) so NULL IPs don't conflict. This is defense-in-depth — the identity resolver still handles the logic, but the DB prevents TOCTOU races.

**Risk:** Medium — if any existing DB has duplicate IPs, the `DELETE` cleans them. Document this in the migration description. Consider making it `ON CONFLICT IGNORE` instead and logging a warning.

---

#### S-4: Strip all secret env vars from daemon

**File:** `src/langusta/cli.py:994-997`

```python
_SECRET_ENV_VARS = frozenset({
    "LANGUSTA_MASTER_PASSWORD",
    "LANGUSTA_CRED_SECRET",
    "LANGUSTA_CRED_V3_USER",
    "LANGUSTA_CRED_V3_AUTH_PROTO",
    "LANGUSTA_CRED_V3_AUTH_PASS",
    "LANGUSTA_CRED_V3_PRIV_PROTO",
    "LANGUSTA_CRED_V3_PRIV_PASS",
    "LANGUSTA_NETBOX_TOKEN",
    "LANGUSTA_SMTP_USERNAME",
    "LANGUSTA_SMTP_PASSWORD",
})
child_env = {
    k: v for k, v in os.environ.items()
    if k not in _SECRET_ENV_VARS
}
```

---

## Wave 4 — Low fixes

| ID | Task | File(s) | Effort |
|----|------|---------|--------|
| D-7 | Add `\` to `_FTS_UNSAFE` regex | `db/search.py:22` | 5min |
| D-8 | Optimize `_list_identities` to targeted lookups | `db/writer.py:108-127` | 2h |
| L-5 | Remove dead `_mac_exists` from `import_netbox.py` | `db/import_netbox.py:89-92` | 5min |
| L-6 | Cross-validate `corrects_id` asset_id match in `append_entry` | `db/timeline.py:62-83` | 30min |
| L-7 | Filter `None` values in `import_common._apply_update` | `db/import_common.py:185` | 15min |
| S-6 | Bind credential `id`+`kind` as AES-GCM AAD | `crypto/vault.py` + `db/credentials.py` | 3h |
| S-7 | File-lock TOFU known_hosts first-use | `monitor/ssh/known_hosts.py` | 2h |
| S-8 | Sanitize `CheckResult.detail` for credential patterns | `monitor/runner.py:304` | 1h |
| S-9 | Add `cred rekey` command | `crypto/master_password.py` + `cli.py` | 4h |
| A-7 | Correct orchestrator gather comment | `scan/orchestrator.py:106-110` | 5min |
| A-8 | Make `_required_str` return fail instead of raising | `monitor/checks/snmp_oid.py:68` + `ssh_command.py:66` | 1h |
| R-12 | Add `synchronous=NORMAL` to AGENTS.md pragma list | `AGENTS.md:62` | 5min |

---

## Sequencing diagram

```
Wave 1 (Critical)                    Wave 2 (High)
┌─────────────────────┐              ┌──────────────────────────┐
│ X-1 SMTP auth       │              │ X-4 Schema-version guard │
│ X-2 Import invariant│              │ X-5 Fix invariant tests  │
│ X-3 Daemon + scan   │─────────────▶│ D-1 Backup parser        │
└─────────────────────┘              │ D-2 FK check in txn      │
                                     │ S-1 Backup perms (0600)  │
                                     │ S-2 Encrypt webhook URLs │
                                     │ R-1 Remove APScheduler   │
                                     │ A-1 Per-check timeout    │
                                     │ T-1 Timeline DAL lint    │
                                     │ T-2 raising=True         │
                                     │ T-3 CI scan+review smoke │
                                     └────────────┬─────────────┘
                                                   │
                                    Wave 3 (Medium)│
                                    ┌──────────────▼──────────────┐
                                    │ D-3 UNIQUE(primary_ip)      │
                                    │ D-5 check_results retention │
                                    │ L-3,L-4 Provenance cleanup  │
                                    │ A-2..A-6 Async/error fixes  │
                                    │ R-2,R-3,R-6 Arch fixes      │
                                    │ S-3,S-4 Security hardening  │
                                    │ T-5 Hypothesis max_examples │
                                    └──────────────┬──────────────┘
                                                   │
                                    Wave 4 (Low)   │
                                    ┌──────────────▼──────────────┐
                                    │ All remaining polish items  │
                                    └─────────────────────────────┘
```

---

## Commit strategy

Each task = one conventional-commit:

| Task | Commit message |
|------|---------------|
| X-1 | `fix(notifications): read SMTP credentials from env vars at send time` |
| X-2a | `fix(import): route Lansweeper import through core.identity.resolve for hostname-aware matching` |
| X-2b | `fix(import): route NetBox import through apply_imported_observation for provenance enforcement` |
| X-3a | `fix(scan): commit after start_scan to release write lock during network I/O` |
| X-3b | `fix(monitor): add per-cycle error recovery so transient DB errors don't kill the daemon` |
| X-4 | `feat(monitor): add schema-version guard at daemon startup` |
| X-5 | `test: fix vacuous MANUAL-field property test and strengthen writer idempotency` |
| D-1 | `fix(backup): rename pre-migration backups so prune and verify can manage them` |
| D-2 | `fix(migrate): run foreign_key_check inside each migration transaction` |
| S-1 | `fix(backup): enforce 0600 on backup files and fix connection leak in verify()` |
| S-2 | `feat(notifications): encrypt webhook URLs at rest via vault` |
| R-1 | `chore: remove unused APScheduler dependency` |
| A-1 | `feat(monitor): add per-check timeout as defense-in-depth` |

---

## Verification gate (after each wave)

```bash
# Full gate — run after every wave completion
uv run ruff check src tests scripts
uv run python -m scripts.lint_boundaries
uv run pytest -v --tb=short
uv run langusta --version
```

All four must pass on both Linux and macOS before proceeding to the next wave.
