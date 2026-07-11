# Version (`VERSION.md`)

## Current version: `1.0.0`

## Versioning scheme

This project has been developed in numbered phases since its inception
(Phase 0 → Phase 10), each with its own architecture review, design
decisions, small focused commits, and milestone report — see
[`docs/ROADMAP.md`](docs/ROADMAP.md) and [`docs/DECISIONS.md`](docs/DECISIONS.md).
`VERSION.md` maps that phase history onto semantic versioning directly,
rather than introducing an unrelated numbering scheme retroactively:

- **`0.N.0`** = Phase `N` complete.
- **`1.0.0`** = Phase 10 (Final Production Sign-Off & Release
  Certification) complete — see
  [`docs/RELEASE_CERTIFICATION.md`](docs/RELEASE_CERTIFICATION.md) for
  the full principal-engineer certification review this tag represents.
  Note the scope correction from this file's own earlier prediction: `0.N`
  originally expected Phase 10 to be a feature/launch-polish pass (landing
  page, screenshots, OpenAPI schema); it turned out to be the engineering
  certification review instead, and that launch-polish work is still open
  under `ROADMAP.md` §2's "Phase 11+ — Launch & beyond" bucket, unblocked
  by this tag rather than gating it.

`1.0.0` reflects **engineering certification and build tag**, not a
claim of live production traffic — `RELEASE_CERTIFICATION.md`'s own
decision is explicit that going live is conditioned on completing the
documented first-deploy verification steps (`DEPLOYMENT_GUIDE.md` §3.4–
3.5, `SMOKE_TEST_CHECKLIST.md`) against the real Render/Vercel
environment, which this development environment has no access to perform.

`frontend/package.json`'s own `"version": "0.1.0"` field predates this
scheme (never bumped since early development) and has no functional
effect anywhere in the codebase — this file, not `package.json`, is the
authoritative version reference going forward.

## What's in `1.0.0`

Phases 0 through 10, in full — see [`RELEASE_NOTES.md`](RELEASE_NOTES.md)
for the phase-by-phase breakdown, [`docs/RELEASE_CHECKLIST.md`](docs/RELEASE_CHECKLIST.md)
for the Phase 9d system-wide readiness audit, and
[`docs/RELEASE_CERTIFICATION.md`](docs/RELEASE_CERTIFICATION.md) for the
Phase 10 independent re-verification, scored assessment, and release
decision (approved, zero release blockers found).

## Release history

| Version | Date | Summary |
|---|---|---|
| `1.0.0` | 2026-07-11 | Phase 10 (Final Production Sign-Off & Release Certification) complete — approved for release, zero blockers found across 5 independent re-verification passes. |
| `0.9.0-rc1` | 2026-07-11 | Phase 9 (9a Deployment & Environment Validation, 9b Observability/Logging, 9c Security/Dependency Audit, 9d Release Validation) complete. First release candidate. |

Future releases should append a row here rather than rewriting this
table's history.
