"""Typer entry point. Subcommands grow per milestone.

M0: init
M1: add, list
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime

import typer

from langusta import __version__, backup, paths
from langusta.crypto import master_password as mp
from langusta.crypto.vault import Vault
from langusta.db import assets as assets_dal
from langusta.db import credentials as cred_dal
from langusta.db import export as export_mod
from langusta.db import import_lansweeper as import_lansweeper_mod
from langusta.db import import_netbox as import_netbox_mod
from langusta.db import monitoring as mon_dal
from langusta.db import notifications as notif_dal
from langusta.db import proposed_changes as pc_dal
from langusta.db.connection import connect
from langusta.db.migrate import latest_schema_version, migrate
from langusta.platform import get_backend


def _get_master_password() -> str:
    env = os.environ.get("LANGUSTA_MASTER_PASSWORD")
    if env:
        return env
    return typer.prompt("Master password", hide_input=True)


def _unlock_vault() -> Vault:
    password = _get_master_password()
    with connect(paths.db_path()) as conn:
        try:
            return mp.unlock(conn, password=password)
        except mp.WrongMasterPassword as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        except RuntimeError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=1) from exc

app = typer.Typer(
    name="langusta",
    help="Local-first asset registry + network scanner + lightweight monitoring.",
    add_completion=False,
)


def _print_version(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        help="Show the LANgusta version and exit.",
        is_eager=True,
        callback=_print_version,
    ),
) -> None:
    """Root callback: surfaces --version and routes to subcommands."""
    if ctx.invoked_subcommand is None and not version:
        typer.echo(ctx.get_help())
        raise typer.Exit()


@app.command()
def init() -> None:
    """Create ~/.langusta/ and the SQLite database with the latest schema.

    On first run, prompts for the master password (or reads
    LANGUSTA_MASTER_PASSWORD). Idempotent — safe to re-run.
    """
    backend = get_backend()
    home = paths.langusta_home()
    backups = paths.backups_dir()
    db = paths.db_path()

    home.mkdir(parents=True, exist_ok=True)
    backups.mkdir(parents=True, exist_ok=True)

    migrate(db, backups_dir=backups)

    # Set up the master password on first init.
    with connect(db) as conn:
        if not mp.is_set(conn):
            password = _get_master_password()
            try:
                mp.setup(conn, password=password, now=datetime.now(UTC))
            except ValueError as exc:
                typer.echo(f"error: {exc}", err=True)
                raise typer.Exit(code=1) from exc

    # Lock down permissions *after* the file exists.
    backend.enforce_private(home)
    backend.enforce_private(backups)
    backend.enforce_private(db)

    typer.echo(f"LANgusta initialised at {db} (schema v{latest_schema_version()})")


@app.command()
def add(
    hostname: str | None = typer.Option(None, "--hostname", "-n", help="Human-readable hostname."),
    ip: str | None = typer.Option(None, "--ip", help="Primary IPv4 address."),
    mac: str | None = typer.Option(None, "--mac", help="Primary MAC address (any case, stored lowercase)."),
    description: str | None = typer.Option(None, "--description", "-d", help="Short description."),
    location: str | None = typer.Option(None, "--location", help="Physical location."),
    owner: str | None = typer.Option(None, "--owner", help="Responsible party."),
    management_url: str | None = typer.Option(None, "--url", help="Management URL or SSH target."),
    criticality: str | None = typer.Option(None, "--criticality", help="Criticality level."),
) -> None:
    """Add an asset manually. At least one of --hostname/--ip/--mac is required.

    Every field you pass is recorded with `manual` provenance — future scans
    will propose changes rather than silently overwrite.
    """
    if not any((hostname, ip, mac)):
        typer.echo(
            "error: supply at least one of --hostname, --ip, --mac",
            err=True,
        )
        raise typer.Exit(code=2)

    now = datetime.now(UTC)
    with connect(paths.db_path()) as conn:
        try:
            asset_id = assets_dal.insert_manual(
                conn,
                hostname=hostname,
                primary_ip=ip,
                mac=mac,
                description=description,
                location=location,
                owner=owner,
                management_url=management_url,
                criticality=criticality,
                now=now,
            )
        except assets_dal.DuplicateMacError as exc:
            typer.echo(f"error: {exc}".lower(), err=True)
            raise typer.Exit(code=1) from exc

    typer.echo(f"added asset id={asset_id}")


@app.command("ui")
def ui() -> None:
    """Launch the Textual UI."""
    from langusta.tui.app import LangustaApp

    LangustaApp().run()


@app.command("list")
def list_assets() -> None:
    """Print all assets as a table."""
    with connect(paths.db_path()) as conn:
        rows = assets_dal.list_all(conn)

    if not rows:
        typer.echo("No assets yet. Use `langusta add` to create one.")
        return

    headers = ("ID", "Hostname", "IP", "MAC", "Source", "Last seen")
    widths = [len(h) for h in headers]
    table = [
        (
            str(r.id),
            r.hostname or "-",
            r.primary_ip or "-",
            ",".join(r.macs) if r.macs else "-",
            r.source,
            r.last_seen.strftime("%Y-%m-%d %H:%M"),
        )
        for r in rows
    ]
    for row in table:
        widths = [max(w, len(cell)) for w, cell in zip(widths, row, strict=True)]

    def _fmt(cells: tuple[str, ...]) -> str:
        return "  ".join(cell.ljust(w) for cell, w in zip(cells, widths, strict=True))

    typer.echo(_fmt(headers))
    typer.echo(_fmt(tuple("-" * w for w in widths)))
    for row in table:
        typer.echo(_fmt(row))


@app.command()
def scan(
    target: str = typer.Argument(..., help="Subnet (CIDR) or single IPv4 address."),
    snmp: str | None = typer.Option(
        None, "--snmp",
        help="Credential label (snmp_v2c) to enrich hosts with sysDescr.",
    ),
) -> None:
    """Sweep a subnet and populate the inventory with live hosts.

    The wedge: `langusta scan 192.168.1.0/24`. Uses ICMP to detect live
    hosts, consults the local ARP table to pair them with MACs, and feeds
    each observation through the scanner-proposes-human-disposes write path.

    `--snmp <label>` uses the named SNMP v2c credential to enrich hosts
    with sysDescr; hosts that don't respond are silently skipped.
    """
    from icmplib.exceptions import SocketPermissionError

    from langusta.scan.orchestrator import run_scan
    from langusta.scan.snmp.pysnmp_backend import PysnmpBackend

    backend = get_backend()
    snmp_client = None
    snmp_community: str | None = None
    if snmp is not None:
        vault = _unlock_vault()
        with connect(paths.db_path()) as conn:
            info = cred_dal.get_by_label(conn, snmp)
            if info is None:
                typer.echo(f"error: no credential with label {snmp!r}", err=True)
                raise typer.Exit(code=1)
            if info.kind != "snmp_v2c":
                typer.echo(
                    f"error: credential {snmp!r} is {info.kind}, "
                    "only snmp_v2c is supported in v1",
                    err=True,
                )
                raise typer.Exit(code=2)
            secret = cred_dal.get_secret(conn, credential_id=info.id, vault=vault)
        snmp_community = secret.decode("utf-8")
        snmp_client = PysnmpBackend()

    with connect(paths.db_path()) as conn:
        try:
            report = asyncio.run(
                run_scan(
                    conn, target, platform_backend=backend,
                    snmp_client=snmp_client,
                    snmp_community=snmp_community,
                    backups_dir=paths.backups_dir(),
                )
            )
        except ValueError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=2) from exc
        except SocketPermissionError as exc:
            typer.echo(
                "error: this system doesn't permit unprivileged ICMP. "
                "Either run as root, or enable unprivileged ping:\n"
                "  Linux: sudo sysctl -w net.ipv4.ping_group_range='0 2147483647'\n"
                "  macOS: unprivileged ICMP is on by default — check net.inet.raw.maxdgram\n"
                f"(underlying error: {exc})",
                err=True,
            )
            raise typer.Exit(code=1) from exc

    typer.echo(
        f"Found {report.hosts_alive} devices in {report.duration_seconds:.1f}s "
        f"({report.inserted} inserted, {report.updated} updated, "
        f"{report.deferred} ambiguous, {report.proposed_changes} proposed changes)"
    )


# ---------------------------------------------------------------------------
# review — resolve proposed_changes from the CLI
# ---------------------------------------------------------------------------


review_app = typer.Typer(
    help="Review scan-proposed changes against manually-set fields.",
    invoke_without_command=True,
)


@review_app.callback(invoke_without_command=True)
def review_root(ctx: typer.Context) -> None:
    """List all open proposed changes (when no subcommand is given)."""
    if ctx.invoked_subcommand is not None:
        return
    with connect(paths.db_path()) as conn:
        rows = pc_dal.list_open(conn)
    if not rows:
        typer.echo("No pending proposals.")
        return
    typer.echo(f"{len(rows)} pending proposal(s):")
    for r in rows:
        typer.echo(
            f"  #{r.id} asset={r.asset_id} {r.field}: "
            f"{r.current_value!r} -> {r.proposed_value!r}"
        )


@review_app.command("accept")
def review_accept(pc_id: int = typer.Argument(..., help="Proposed change id.")) -> None:
    """Apply the proposed value to the asset; flips provenance to scanned."""
    now = datetime.now(UTC)
    with connect(paths.db_path()) as conn:
        try:
            pc_dal.accept(conn, pc_id, now=now)
        except pc_dal.AlreadyResolvedError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
    typer.echo(f"accepted #{pc_id}")


@review_app.command("reject")
def review_reject(pc_id: int = typer.Argument(..., help="Proposed change id.")) -> None:
    """Discard the proposed value; asset stays as the human set it."""
    now = datetime.now(UTC)
    with connect(paths.db_path()) as conn:
        try:
            pc_dal.reject(conn, pc_id, now=now)
        except pc_dal.AlreadyResolvedError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
    typer.echo(f"rejected #{pc_id}")


app.add_typer(review_app, name="review")


# ---------------------------------------------------------------------------
# cred — encrypted credential management
# ---------------------------------------------------------------------------


cred_app = typer.Typer(
    help="Manage encrypted credentials (SNMP communities, SSH keys, API tokens).",
    invoke_without_command=True,
)


@cred_app.callback(invoke_without_command=True)
def cred_root(ctx: typer.Context) -> None:
    """Show help when no subcommand is given."""
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


@cred_app.command("add")
def cred_add(
    label: str = typer.Option(..., "--label", help="Short name to reference this credential."),
    kind: str = typer.Option(..., "--kind", help="snmp_v2c | snmp_v3 | ssh_key | ssh_password | api_token"),
) -> None:
    """Add a new credential. Reads the secret from LANGUSTA_CRED_SECRET or prompts."""
    if kind not in cred_dal.VALID_KINDS:
        typer.echo(f"error: unknown kind {kind!r}; valid: {sorted(cred_dal.VALID_KINDS)}", err=True)
        raise typer.Exit(code=2)

    vault = _unlock_vault()
    secret_env = os.environ.get("LANGUSTA_CRED_SECRET")
    secret = secret_env if secret_env is not None else typer.prompt("Secret", hide_input=True)
    now = datetime.now(UTC)
    with connect(paths.db_path()) as conn:
        try:
            cred_id = cred_dal.create(
                conn, label=label, kind=kind,
                secret=secret.encode("utf-8"), vault=vault, now=now,
            )
        except cred_dal.DuplicateLabel as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
    typer.echo(f"added credential id={cred_id} label={label} kind={kind}")


@cred_app.command("list")
def cred_list() -> None:
    """List credential metadata. NEVER reveals secrets."""
    with connect(paths.db_path()) as conn:
        rows = cred_dal.list_info(conn)
    if not rows:
        typer.echo("No credentials stored.")
        return
    headers = ("ID", "Label", "Kind", "Created")
    widths = [len(h) for h in headers]
    table = [
        (str(r.id), r.label, r.kind, r.created_at.strftime("%Y-%m-%d %H:%M"))
        for r in rows
    ]
    for row in table:
        widths = [max(w, len(cell)) for w, cell in zip(widths, row, strict=True)]

    def _fmt(cells: tuple[str, ...]) -> str:
        return "  ".join(cell.ljust(w) for cell, w in zip(cells, widths, strict=True))

    typer.echo(_fmt(headers))
    typer.echo(_fmt(tuple("-" * w for w in widths)))
    for row in table:
        typer.echo(_fmt(row))


@cred_app.command("rm")
def cred_rm(cred_id: int = typer.Argument(..., help="Credential id to remove.")) -> None:
    """Delete a credential by id."""
    with connect(paths.db_path()) as conn:
        cred_dal.delete(conn, credential_id=cred_id)
    typer.echo(f"removed credential id={cred_id}")


app.add_typer(cred_app, name="cred")


# ---------------------------------------------------------------------------
# backup — snapshot + retention
# ---------------------------------------------------------------------------


backup_app = typer.Typer(help="Manage LANgusta SQLite snapshots.")


@backup_app.command("now")
def backup_now() -> None:
    """Write an immediate snapshot (ignores the 1h dedup window)."""
    now = datetime.now(UTC)
    result = backup.write(
        paths.db_path(), paths.backups_dir(), now=now, dedupe_window_hours=0,
    )
    if result is None:
        typer.echo("no backup written (source DB missing)")
        raise typer.Exit(code=1)
    typer.echo(f"wrote {result}")


@backup_app.command("list")
def backup_list() -> None:
    """List all snapshots newest-first."""
    snapshots = backup.list_backups(paths.backups_dir())
    if not snapshots:
        typer.echo("No backups.")
        return
    for b in snapshots:
        typer.echo(f"{b.stamp.isoformat()}  {b.path}")


@backup_app.command("verify")
def backup_verify(
    path: str = typer.Argument(..., help="Backup file path to check."),
) -> None:
    """PRAGMA integrity_check against a backup."""
    from pathlib import Path
    ok = backup.verify(Path(path))
    typer.echo("ok" if ok else "CORRUPT")
    if not ok:
        raise typer.Exit(code=1)


@backup_app.command("prune")
def backup_prune(
    keep: int = typer.Option(30, "--keep", help="How many snapshots to keep."),
) -> None:
    """Delete all but the most recent --keep snapshots."""
    removed = backup.prune(paths.backups_dir(), keep=keep)
    typer.echo(f"removed {removed} snapshot(s)")


app.add_typer(backup_app, name="backup")


# ---------------------------------------------------------------------------
# export / import
# ---------------------------------------------------------------------------


@app.command("export")
def export_cmd(
    output: str | None = typer.Option(
        None, "--output", "-o", help="Write to file instead of stdout.",
    ),
) -> None:
    """Export the user-owned asset data as JSON (credentials excluded)."""
    import json as _json
    with connect(paths.db_path()) as conn:
        dump = export_mod.export_to_dict(conn)
    payload = _json.dumps(dump, indent=2, sort_keys=True)
    if output:
        from pathlib import Path as _Path
        _Path(output).write_text(payload)
    else:
        typer.echo(payload)


@app.command("import")
def import_cmd(
    path: str = typer.Argument(..., help="JSON dump produced by `langusta export`."),
) -> None:
    """Import a previously-exported JSON dump into this (empty) DB."""
    import json as _json
    from pathlib import Path as _Path
    data = _json.loads(_Path(path).read_text())
    with connect(paths.db_path()) as conn:
        try:
            export_mod.import_from_dict(conn, data)
        except export_mod.ImportRefused as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
    typer.echo(f"imported {path}")


@app.command("import-lansweeper")
def import_lansweeper_cmd(
    csv_path: str = typer.Argument(..., help="Lansweeper CSV export."),
) -> None:
    """Import assets from a Lansweeper CSV export (source='imported').

    Duplicate MACs or IPs (colliding with rows already in the DB) are
    skipped — run `langusta list` first to see what's already present.
    """
    from pathlib import Path as _Path
    now = datetime.now(UTC)
    path = _Path(csv_path)
    if not path.exists():
        typer.echo(f"error: file not found: {path}", err=True)
        raise typer.Exit(code=1)
    with connect(paths.db_path()) as conn:
        report = import_lansweeper_mod.import_lansweeper_csv(
            conn, csv_path=path, now=now,
        )
    typer.echo(f"imported {report.imported}, skipped {report.skipped}")


@app.command("import-netbox")
def import_netbox_cmd(
    url: str = typer.Option(..., "--url", help="NetBox base URL (e.g. https://netbox.example.com)."),
) -> None:
    """Import devices from a NetBox instance via /api/dcim/devices/.

    Requires LANGUSTA_NETBOX_TOKEN in the environment (never passed on the
    command line — tokens leak into shell history and process listings).
    """
    token = os.environ.get("LANGUSTA_NETBOX_TOKEN")
    if not token:
        typer.echo(
            "error: LANGUSTA_NETBOX_TOKEN not set. Export a NetBox API token:\n"
            "  export LANGUSTA_NETBOX_TOKEN=<token>",
            err=True,
        )
        raise typer.Exit(code=2)

    now = datetime.now(UTC)
    with connect(paths.db_path()) as conn:
        try:
            report = asyncio.run(
                import_netbox_mod.import_netbox(
                    conn, base_url=url, token=token, now=now,
                )
            )
        except import_netbox_mod.NetBoxAuthError as exc:
            typer.echo(f"error: authentication failed: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        except import_netbox_mod.NetBoxNetworkError as exc:
            typer.echo(f"error: network error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
    typer.echo(f"imported {report.imported}, skipped {report.skipped}")


# ---------------------------------------------------------------------------
# monitor — subscriptions, single-cycle run, status
# ---------------------------------------------------------------------------


monitor_app = typer.Typer(help="Configure and run monitoring checks.")


@monitor_app.command("enable")
def monitor_enable(
    asset: int = typer.Option(..., "--asset", help="Asset id to monitor."),
    kind: str = typer.Option(..., "--kind", help="icmp | tcp | http"),
    interval: int = typer.Option(60, "--interval", help="Seconds between checks."),
    port: int | None = typer.Option(None, "--port", help="Port for tcp/http."),
    path: str | None = typer.Option(None, "--path", help="URL path for http."),
    target: str | None = typer.Option(
        None, "--target", help="Override asset primary_ip if set.",
    ),
) -> None:
    """Enable a monitoring check against an asset."""
    if kind not in mon_dal.VALID_KINDS:
        typer.echo(
            f"error: unknown kind {kind!r}; valid: {sorted(mon_dal.VALID_KINDS)}",
            err=True,
        )
        raise typer.Exit(code=2)
    now = datetime.now(UTC)
    with connect(paths.db_path()) as conn:
        try:
            cid = mon_dal.enable_check(
                conn, asset_id=asset, kind=kind,
                interval_seconds=interval, target=target,
                port=port, path=path, now=now,
            )
        except ValueError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=2) from exc
    typer.echo(f"enabled check id={cid}")


@monitor_app.command("disable")
def monitor_disable(
    check_id: int = typer.Argument(..., help="Monitoring check id to disable."),
) -> None:
    with connect(paths.db_path()) as conn:
        mon_dal.disable_check(conn, check_id)
    typer.echo(f"disabled check id={check_id}")


@monitor_app.command("list")
def monitor_list() -> None:
    """List all configured checks."""
    with connect(paths.db_path()) as conn:
        checks = mon_dal.list_checks(conn)
    if not checks:
        typer.echo("No checks configured.")
        return
    typer.echo(f"{len(checks)} check(s):")
    for c in checks:
        bits = [f"id={c.id}", f"asset={c.asset_id}", c.kind, f"every {c.interval_seconds}s"]
        if c.port is not None:
            bits.append(f"port={c.port}")
        if c.path is not None:
            bits.append(f"path={c.path}")
        bits.append("enabled" if c.enabled else "disabled")
        if c.last_status is not None:
            bits.append(f"last={c.last_status}")
        typer.echo("  " + "  ".join(bits))


@monitor_app.command("run")
def monitor_run() -> None:
    """Execute one cycle of due checks and exit."""
    from langusta.monitor.runner import run_once

    now = datetime.now(UTC)
    logfile = paths.langusta_home() / "notifications.log"
    with connect(paths.db_path()) as conn:
        summary = asyncio.run(
            run_once(conn, now=now, notifications_logfile=logfile),
        )
    typer.echo(
        f"executed {summary.executed} check(s) "
        f"({summary.ok_count} ok, {summary.fail_count} fail, "
        f"{summary.transitions} state transition(s))"
    )


@monitor_app.command("install-service")
def monitor_install_service(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print the unit/plist instead of writing it.",
    ),
    force: bool = typer.Option(
        False, "--force", help="Overwrite an existing file.",
    ),
) -> None:
    """Render a systemd user unit (Linux) or launchd plist (macOS) that runs
    `langusta monitor daemon --foreground`.

    The file is placed under the user's XDG / LaunchAgents directory and a
    `systemctl --user` (or `launchctl`) invocation is printed as the next
    step — LANgusta never runs them for you per ADR-0004.
    """
    import shutil
    import sys as _sys

    from langusta.platform.base import NotImplementedCapability

    backend = get_backend()
    # Resolve the binary path so the generated unit references an absolute
    # command line rather than relying on PATH.
    exec_path = shutil.which("langusta") or _sys.argv[0]
    try:
        recipe = backend.daemon_install_recipe(exec_path=exec_path)
    except NotImplementedCapability as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if dry_run:
        typer.echo(recipe.content)
        typer.echo(f"# Would write to: {recipe.install_path}")
        typer.echo(f"# Post-install: {recipe.start_hint}")
        return

    if recipe.install_path.exists() and not force:
        typer.echo(
            f"error: {recipe.install_path} already exists — use --force to overwrite",
            err=True,
        )
        raise typer.Exit(code=1)

    recipe.install_path.parent.mkdir(parents=True, exist_ok=True)
    recipe.install_path.write_text(recipe.content)
    typer.echo(f"wrote {recipe.install_path}")
    typer.echo(f"next: {recipe.start_hint}")


@monitor_app.command("daemon")
def monitor_daemon(
    foreground: bool = typer.Option(
        False, "--foreground",
        help="Run the loop in the foreground (for systemd / launchd).",
    ),
    interval: int = typer.Option(
        60, "--interval", help="Seconds between monitor cycles.",
    ),
) -> None:
    """Run the monitor loop. Without `--foreground`, this is a no-op — use
    the service-manager (systemd / launchd) via `monitor install-service`
    instead of backgrounding LANgusta itself (ADR-0002)."""
    import time as _time

    from langusta.monitor.runner import run_once

    if not foreground:
        typer.echo(
            "daemon without --foreground is intentionally a no-op; use "
            "`langusta monitor install-service` and let systemd/launchd "
            "supervise. Pass --foreground to run in this process.",
            err=True,
        )
        raise typer.Exit(code=2)

    typer.echo(f"langusta monitor daemon — cycle every {interval}s (Ctrl+C to stop)")
    logfile = paths.langusta_home() / "notifications.log"
    while True:
        now = datetime.now(UTC)
        with connect(paths.db_path()) as conn:
            summary = asyncio.run(
                run_once(conn, now=now, notifications_logfile=logfile),
            )
        typer.echo(
            f"[{now.isoformat(timespec='seconds')}] "
            f"executed {summary.executed} "
            f"({summary.ok_count} ok, {summary.fail_count} fail, "
            f"{summary.transitions} transitions)"
        )
        _time.sleep(interval)


@monitor_app.command("status")
def monitor_status() -> None:
    """Show daemon heartbeat freshness."""
    now = datetime.now(UTC)
    with connect(paths.db_path()) as conn:
        hb = mon_dal.get_heartbeat(conn)
    if hb is None:
        typer.echo("no heartbeat recorded — monitor has never run")
        return
    age = (now - hb).total_seconds()
    stale = mon_dal.is_heartbeat_stale(hb, now=now, tolerance_seconds=120)
    marker = "STALE" if stale else "fresh"
    typer.echo(f"heartbeat {hb.isoformat()}  ({int(age)}s ago, {marker})")


app.add_typer(monitor_app, name="monitor")


# ---------------------------------------------------------------------------
# notify — notification sinks (log always-on; webhook + SMTP opt-in)
# ---------------------------------------------------------------------------


notify_app = typer.Typer(help="Configure notification sinks for monitor events.")


@notify_app.command("add-webhook")
def notify_add_webhook(
    label: str = typer.Option(..., "--label", help="Unique name for this sink."),
    url: str = typer.Option(..., "--url", help="HTTPS endpoint accepting POST."),
) -> None:
    """Register a webhook sink. Monitor events POST JSON to this URL."""
    now = datetime.now(UTC)
    with connect(paths.db_path()) as conn:
        try:
            sid = notif_dal.create(
                conn, label=label, kind="webhook",
                config={"url": url}, now=now,
            )
        except notif_dal.DuplicateLabel as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
    typer.echo(f"added webhook sink id={sid} label={label}")


@notify_app.command("add-smtp")
def notify_add_smtp(
    label: str = typer.Option(..., "--label", help="Unique name for this sink."),
    host: str = typer.Option(..., "--host"),
    port: int = typer.Option(..., "--port"),
    sender: str = typer.Option(..., "--from", help="From: address."),
    recipient: str = typer.Option(..., "--to", help="To: address."),
    starttls: bool = typer.Option(False, "--starttls"),
) -> None:
    """Register an SMTP sink. Credentials (if any) go in env vars
    LANGUSTA_SMTP_USERNAME / LANGUSTA_SMTP_PASSWORD at send time."""
    now = datetime.now(UTC)
    config = {
        "host": host, "port": port,
        "from": sender, "to": recipient,
        "starttls": starttls,
    }
    with connect(paths.db_path()) as conn:
        try:
            sid = notif_dal.create(
                conn, label=label, kind="smtp", config=config, now=now,
            )
        except notif_dal.DuplicateLabel as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
    typer.echo(f"added smtp sink id={sid} label={label}")


@notify_app.command("list")
def notify_list() -> None:
    """List configured notification sinks."""
    with connect(paths.db_path()) as conn:
        rows = notif_dal.list_all(conn)
    if not rows:
        typer.echo("No notification sinks configured.")
        typer.echo(
            "The built-in log file at ~/.langusta/notifications.log is "
            "always on regardless of this list."
        )
        return
    for s in rows:
        status = "enabled" if s.enabled else "DISABLED"
        typer.echo(f"id={s.id}  {s.label}  {s.kind}  {status}")


@notify_app.command("rm")
def notify_rm(
    sink_id: int = typer.Argument(..., help="Sink id to remove."),
) -> None:
    with connect(paths.db_path()) as conn:
        notif_dal.delete(conn, sink_id)
    typer.echo(f"removed sink id={sink_id}")


@notify_app.command("disable")
def notify_disable(
    sink_id: int = typer.Argument(..., help="Sink id to disable."),
) -> None:
    with connect(paths.db_path()) as conn:
        notif_dal.disable(conn, sink_id)
    typer.echo(f"disabled sink id={sink_id}")


@notify_app.command("test")
def notify_test(
    sink_id: int = typer.Argument(..., help="Sink id to fire a test event at."),
) -> None:
    """Fire a synthetic failure+recovery event at one sink."""
    from langusta.monitor.notifications import (
        _SENDERS,
        MonitorEvent,
    )

    with connect(paths.db_path()) as conn:
        rows = [s for s in notif_dal.list_all(conn) if s.id == sink_id]
    if not rows:
        typer.echo(f"error: no sink with id={sink_id}", err=True)
        raise typer.Exit(code=1)
    sink = rows[0]
    sender = _SENDERS.get(sink.kind)
    if sender is None:
        typer.echo(f"error: no sender for kind={sink.kind}", err=True)
        raise typer.Exit(code=1)
    event = MonitorEvent(
        asset_id=0, asset_hostname="langusta-test", asset_ip="127.0.0.1",
        kind="failure", check_kind="test", detail="synthetic test event",
        occurred_at=datetime.now(UTC),
    )
    ok = asyncio.run(sender(sink.config, event))
    typer.echo(f"sink {sink.label!r}: {'ok' if ok else 'FAILED'}")
    if not ok:
        raise typer.Exit(code=1)


app.add_typer(notify_app, name="notify")
