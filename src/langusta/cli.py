"""Typer entry point. Subcommands grow per milestone; M0 ships `init`."""

from __future__ import annotations

import typer

from langusta import __version__, paths
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
