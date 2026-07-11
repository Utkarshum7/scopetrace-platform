# Release Notes (`RELEASE_NOTES.md`)

## `0.9.0-rc1` — 2026-07-11

First release candidate. See [`VERSION.md`](../VERSION.md) for the
versioning scheme and [`RELEASE_CHECKLIST.md`](RELEASE_CHECKLIST.md) for
the full system-wide readiness audit backing this release.

---

### Completed phases

**Phase 0–4 — Foundation.** Rebrand/infra, correctness fixes, JWT auth +
RBAC + multi-tenant isolation, the Carbon Intelligence Engine (versioned,
provenance-tracked emission factors; Decimal-precise, factor-pinned,
immutable calculations), and analytics/metrics (cached Metrics API,
role-aware dashboards).

**Phase 5 — Production Engineering.** Async processing (Celery + Redis,
6-queue topology), retry/backoff + dead-letter handling, scheduled
maintenance tasks, email notifications, Flower monitoring, full CI/CD
(4 GitHub Actions workflows), Docker + Docker Compose, and the first
complete documentation set.

**Phase 6 — Enterprise Governance.** A per-organization cryptographic
SHA-256 audit hash-chain (tamper-*evident*), immutable
`EmissionRecordVersion` history on every meaningful edit, a fixed
Draft→Submitted→Approved/Rejected workflow enforced at the model layer,
CSV/JSON compliance reports over approved-only data, reversible soft
delete with cascade-delete protection, and a security-hardening pass
(CVE fixes, explicit `SECURE_*` settings, secret-scanning CI).

**Phase 7 — AI (7a–7g).** A provider-agnostic, schema-enforced,
per-tenant-governed AI gateway (the sole enforcement point for every AI
call — budget, egress tier, idempotency, full audit trail) plus a formal
evaluation harness (golden datasets, I1–I6 invariant suite, LLM-as-Judge).
Five real, advisory-only capabilities built on top: anomaly explanation,
emission-factor recommendation, validation assistance, a conversational
ESG assistant, and AI report narration — none of them ever mutate
governed business data; the deterministic engines still make every real
decision. Closed with AI observability/cost-governance dashboards and
ops-health endpoints.

**Phase 8 — UX (8a–8e).** A full accessibility and design-system pass:
shared UI primitives (`Card`, `PageHeader`, `Modal`, `Skeleton`,
`EmptyState`/`ErrorState`, `ConfidenceBadge`, `AIAdvisoryBadge`, etc.),
design tokens, WCAG-conscious focus/landmark/heading conventions, and a
consistent loading/empty/error state pattern applied across every page.

**Phase 9 — Production Engineering & Release Readiness (9a–9d, this
release).**
- **9a — Deployment & Environment Validation**: full audit of Docker,
  Docker Compose, `render.yaml`, environment variables, health endpoints,
  and startup ordering; confirmed zero drift between Compose and Render;
  disabled the DRF browsable API outside `DEBUG`; filled documentation
  gaps (AI env vars in `.env.example`, a Vercel deployment section).
- **9b — Observability, Logging & Runtime Diagnostics**: added
  per-request correlation IDs (`X-Request-ID`, threaded through every log
  line), explicit UTC log timestamps, governance-outcome logging in the
  AI gateway (previously silent), health-check logging consistency, and
  gunicorn access logging correlated with the new request ID.
- **9c — Production Security, Dependency Audit & Release Hardening**:
  fixed 3 Django CVEs (6.0.6→6.0.7) and 2 frontend CVEs (`form-data`,
  `js-yaml`); closed a fail-closed gap in demo-user bootstrap seeding;
  added missing frontend security headers; reviewed authentication,
  authorization, AI governance, the upload pipeline, and background
  workers with no further gaps found.
- **9d — Final Production Release Validation & Launch Checklist** (this
  document + [`RELEASE_CHECKLIST.md`](RELEASE_CHECKLIST.md) +
  [`SMOKE_TEST_CHECKLIST.md`](SMOKE_TEST_CHECKLIST.md) +
  [`VERSION.md`](../VERSION.md)): the consolidated, system-wide
  release-candidate audit.

---

### Architecture summary

Django 6.0.7 + DRF 3.17 backend, React 18 + Vite 5 frontend, PostgreSQL
16, Redis (cache + Celery broker/result-backend), S3-compatible object
storage (provider-agnostic — AWS S3/R2/B2/MinIO), Celery for async
ingestion/calculation/AI/maintenance work across 6 routed queues. 8
first-party Django apps (`core`, `accounts`, `ingestion`, `audit`,
`carbon`, `tasks`, `ai`, `ai.evaluation`). Full diagrams:
[`ARCHITECTURE_OVERVIEW.md`](ARCHITECTURE_OVERVIEW.md).

### Deployment summary

**Backend**: Render (web + 2 worker services + managed Redis + managed
PostgreSQL), or fully portable via Docker Compose. **Frontend**: Vercel
(static Vite build), or the same Docker Compose stack (nginx-served).
Full step-by-step instructions: [`DEPLOYMENT_GUIDE.md`](DEPLOYMENT_GUIDE.md).

### Supported environments

| Environment | Status |
|---|---|
| Local dev (SQLite, eager Celery, no Docker) | ✅ Fully supported — fast-iteration path |
| Local dev (Docker Compose — Postgres/Redis/MinIO/full async stack) | ✅ Fully supported — recommended, exercises the real architecture |
| Render (backend) + Vercel (frontend) | ⚠️ IaC-defined and reviewed; **first real deploy not yet performed** — see `RELEASE_CHECKLIST.md`'s High-severity items |
| Any other S3-compatible storage provider / any PostgreSQL 16 host | ✅ Supported by design (`StorageService` abstraction, standard `DATABASE_URL`) |

### Known limitations

See [`ROADMAP.md`](ROADMAP.md) §1 for the full, continuously-maintained
list. Headline items: no fine-grained upload progress (atomic-transaction
design), polling instead of WebSocket/SSE push, no frontend E2E test
suite, seed emission factors are an illustrative DEFRA subset (not the
full official dataset), no PDF compliance export.

### Deferred work

See [`RELEASE_CHECKLIST.md`](RELEASE_CHECKLIST.md) §16 for the full
classified risk register. Headline items: a Content-Security-Policy for
the frontend (needs browser-verified rollout), the `esbuild`/`vite`
dev-server CVE (needs a 3-major-version Vite migration), IP/network
restriction on `/admin/` (infrastructure-layer), Prometheus/Grafana/
Loki/OpenTelemetry/Sentry-grade observability (Phase 10+ scope — today's
surface is health endpoints + correlated structured logs + AI ops
dashboards).

### Breaking changes

None in this release relative to Phase 8's tip — Phase 9 was explicitly
scoped as additive operational/security hardening throughout (9a/9b/9c/9d
each individually verified to preserve every backend API, business rule,
and AI governance guarantee).

### Upgrade notes

No data migration beyond the standard `manage.py migrate` is required.
Operators upgrading an existing deployment should note: gunicorn now logs
HTTP access lines to stdout by default (§ Phase 9b — log volume increases
accordingly, correlated via `rid=` for easy filtering); `bootstrap_data
--demo-users` now requires `DEMO_USER_PASSWORD` to be set explicitly when
`DEBUG=False` (previously silently used a public default — see
[`SECURITY.md`](SECURITY.md) §4).
