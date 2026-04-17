"""Typer entry point. Subcommands grow per milestone.

M0: init
M1: add, list
"""

from __future__ import annotations

from datetime import UTC, datetime

import typer

from langusta import __version__, paths
from langusta.db import assets as assets_dal
from langusta.db.connection import connect
from langusta.db.migrate import latest_schema_version, migrate
from langusta.platform import get_backend

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

    Idempotent — safe to re-run. Applies pending migrations if the DB exists
    at a prior schema version.
    """
    backend = get_backend()
    home = paths.langusta_home()
    backups = paths.backups_dir()
    db = paths.db_path()

    home.mkdir(parents=True, exist_ok=True)
    backups.mkdir(parents=True, exist_ok=True)

    migrate(db, backups_dir=backups)

    # Lock down permissions *after* the file exists. Directories first, then
    # the DB file, so WAL sidecars created during migrate() inherit a tight
    # parent dir.
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
