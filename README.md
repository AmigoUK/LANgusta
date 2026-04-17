# LANgusta

> Local-first, self-hosted asset registry + network scanner + lightweight monitoring — for small IT teams and MSP technicians managing networks of up to 250 devices.

**Status:** pre-alpha — specification and planning phase. No shippable code yet.

## The idea in one paragraph

LANgusta combines three tools into a single TUI application: a CMMS-style asset registry (describe, document, and track every host and piece of equipment), a built-in network scanner that auto-populates that registry, and a lightweight recurring monitoring engine that turns any registered asset into a watched host. The thesis is that **discovery, documentation, and monitoring should live on the same surface** — not in three separate tools that drift apart. The distinctive feature is **institutional memory**: every asset carries its full history of changes, incidents, upgrades, and resolutions in one view.

## Target user

A solo or small-team IT administrator (in-house) or MSP technician (external) — Linux-comfortable, SSH-native, managing a single-site or small multi-site environment under ~250 devices.

**Not the target:** enterprise netops teams, Windows-only shops, or users who have never opened a terminal.

## Core invariants (non-negotiable)

1. **Immutable timeline.** You can add, never edit or delete, history entries. Corrections are new entries that reference the original.
2. **Scanner proposes, human disposes.** The scanner never silently overwrites a human-set field. Ambiguous matches go to a review queue.
3. **One database, one file.** Everything lives in `~/.langusta/db.sqlite`. Backups are file copies. Migration is copying the file.
4. **No telemetry, no phone-home.** Zero outbound connections the user did not explicitly configure.

## Tech stack (summary)

| Layer | Choice |
|---|---|
| Language | Python 3.12+ |
| Package manager | [uv](https://github.com/astral-sh/uv) |
| TUI | [Textual](https://github.com/Textualize/textual) |
| CLI | [Typer](https://typer.tiangolo.com/) |
| Database | SQLite 3.38+ (WAL mode) |
| Scheduler | APScheduler |
| Credential crypto | AES-256-GCM + Argon2id |
| License | AGPL-3.0 |

Full technical specification in [`docs/specs/02-tech-stack-and-architecture.md`](docs/specs/02-tech-stack-and-architecture.md).

## Documentation

- [`docs/specs/01-functionality-and-moscow.md`](docs/specs/01-functionality-and-moscow.md) — functional spec and MoSCoW scope for v1.
- [`docs/specs/02-tech-stack-and-architecture.md`](docs/specs/02-tech-stack-and-architecture.md) — technical specification.
- [`docs/adr/`](docs/adr/) — Architecture Decision Records for the five open technical questions.
- [`docs/development-plan.md`](docs/development-plan.md) — milestone-sequenced implementation plan for v1.

## License

AGPL-3.0. See [LICENSE](LICENSE). A commercial add-on tier (multi-user, RBAC, web UI) is a possible future; the core will remain AGPL.
