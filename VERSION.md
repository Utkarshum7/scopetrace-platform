# Version (`VERSION.md`)

## Current version: `0.9.0-rc1`

## Versioning scheme

This project has been developed in numbered phases since its inception
(Phase 0 → Phase 9), each with its own architecture review, design
decisions, small focused commits, and milestone report — see
[`docs/ROADMAP.md`](docs/ROADMAP.md) and [`docs/DECISIONS.md`](docs/DECISIONS.md).
`VERSION.md` maps that phase history onto semantic versioning directly,
rather than introducing an unrelated numbering scheme retroactively:

- **`0.N.0`** = Phase `N` complete.
- **`-rc1`** = release candidate — Phase 9 (Production Engineering &
  Release Readiness) is functionally complete and this is the first
  release candidate built from it, but it has not yet had a real
  production deployment or the Phase 10 launch-polish pass (real landing
  page, published architecture diagrams, OpenAPI schema, screenshots —
  see [`ROADMAP.md`](docs/ROADMAP.md) §2).
- **`1.0.0`** is reserved for the first real production launch, after
  Phase 10.

`frontend/package.json`'s own `"version": "0.1.0"` field predates this
scheme (never bumped since early development) and has no functional
effect anywhere in the codebase — this file, not `package.json`, is the
authoritative version reference going forward.

## What's in `0.9.0-rc1`

Phases 0 through 9 (9a–9d), in full — see
[`RELEASE_NOTES.md`](RELEASE_NOTES.md) for the complete breakdown and
[`docs/RELEASE_CHECKLIST.md`](docs/RELEASE_CHECKLIST.md) for the
system-wide readiness audit backing this release candidate.

## Release history

| Version | Date | Summary |
|---|---|---|
| `0.9.0-rc1` | 2026-07-11 | Phase 9 (9a Deployment & Environment Validation, 9b Observability/Logging, 9c Security/Dependency Audit, 9d Release Validation) complete. First release candidate. |

Future releases should append a row here rather than rewriting this
table's history.
