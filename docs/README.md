# LANgusta documentation index

## Specifications

| File | Purpose |
|---|---|
| [`specs/01-functionality-and-moscow.md`](specs/01-functionality-and-moscow.md) | Functional spec: what LANgusta does, the four use modes, the four pillars, core flows, MoSCoW scope for v1, risks. |
| [`specs/02-tech-stack-and-architecture.md`](specs/02-tech-stack-and-architecture.md) | Technical spec: language, runtime, data layer, TUI framework, scanner libs, monitoring, crypto, packaging. |

## Architecture Decision Records

MADR-style lightweight ADRs — one file per load-bearing decision. Numbered, immutable once accepted (supersede rather than rewrite).

| # | Decision | File |
|---|---|---|
| 0000 | Template | [`adr/0000-adr-template.md`](adr/0000-adr-template.md) |
| 0001 | Data layer: raw SQL vs SQLModel vs SQLAlchemy | [`adr/0001-data-layer-orm-choice.md`](adr/0001-data-layer-orm-choice.md) |
| 0002 | Process architecture: single vs multi-process | [`adr/0002-process-architecture.md`](adr/0002-process-architecture.md) |
| 0003 | SNMP library: pysnmp-lextudio vs net-snmp shell-out | [`adr/0003-snmp-library.md`](adr/0003-snmp-library.md) |
| 0004 | Platform support: native Windows vs WSL-only vs Linux+macOS | [`adr/0004-platform-support.md`](adr/0004-platform-support.md) |
| 0005 | Schema migration discipline pre-1.0 | [`adr/0005-schema-migration-discipline.md`](adr/0005-schema-migration-discipline.md) |

## Implementation plan

- [`development-plan.md`](development-plan.md) — milestone-sequenced plan for the v1 Must-Have scope.
