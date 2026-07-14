# ScopeTrace — Architecture Decision Records (Consolidated)

This document records the **foundational, stack-level** architecture decisions for ScopeTrace (ADR 0015–0024). They were made at project inception and are recorded here retroactively so the rationale is defensible and revisitable.

## How to Read This Document

ScopeTrace's architectural history is split across two places, and they should be read **together** for the complete picture:

- **[`docs/adr/0001–0014`](adr/)** — the **domain-specific** decisions (approval workflow, soft delete, compliance reports, and the entire AI subsystem: provider abstraction, advisory-only guarantee, egress/cost policy, per-capability designs). These are one file per decision and remain the authoritative record for each topic. They are indexed at the end of this document.
- **This document (ADR 0015–0024)** — the **foundational platform** decisions that predate the domain ADRs and were never formally captured: the frameworks (Django, React), the datastore (PostgreSQL), authentication (JWT), API style (REST), client state (React Query), multi-tenancy, RBAC, Demo Mode + the Demo AI provider, and the deployment topology.

Read the 0015–0024 records first for *how the platform is built*, then the 0001–0014 records for *how the ESG/AI domain logic is built on top of it*.

Each record uses the same shape: **Context/Problem → Options Considered (assessment table + pros/cons) → Decision → Why selected → Trade-offs/Consequences → When to revisit → Related ADRs / source files / env vars.**

---

## ADR 0015: Django + Django REST Framework as the backend

- **Status:** Accepted · **Date:** 2026-07 (retroactive) · **Deciders:** solo engineer

### Context / Problem
ScopeTrace needs a relational data model with strict integrity (emissions must be traceable and auditable), an admin surface for operators, a migration system for an evolving schema, authentication, RBAC, and a REST API — built quickly by one engineer, for a compliance-sensitive domain.

### Options considered
| Option | Complexity | Ecosystem/Batteries | Team familiarity | Fit for auditable domain |
|---|---|---|---|---|
| **Django + DRF** | Med | Very high (ORM, admin, migrations, auth, DRF) | High | Excellent |
| FastAPI + SQLAlchemy | Med–High | Medium (assemble ORM/migrations/admin/auth) | Med | Good, more wiring |
| Node/Express + Prisma | Med | Medium | Med | Good, less mature admin/migrations |
| Rails | Med | High | Low | Good |

**Django+DRF pros:** ORM with real transactions + `select_for_update`; auto-generated admin (`apps/*/admin.py`); robust migrations; built-in auth + `token_blacklist`; DRF ViewSets/serializers/permissions/throttling out of the box.
**Cons:** synchronous WSGI by default (addressed with gthreads/Celery); heavier than a micro-framework for a tiny API.

### Decision
Django 6 + DRF, one modular-monolith project (`backend/config`) with domain apps (`apps/core|accounts|ingestion|carbon|audit|ai`).

### Why selected
The domain is CRUD-plus-workflow over a rich relational model with hard auditability requirements — exactly Django's strength. The admin alone (populated ModelAdmins for every model) replaced building an internal ops UI. DRF's permission/serializer/throttle primitives let RBAC and the AI throttle scope be declarative rather than hand-rolled.

### Trade-offs / consequences
- Easier: schema evolution, admin, RBAC, transactional safety.
- Harder: high-concurrency async I/O (mitigated by gunicorn gthreads + Celery, ADR 0024); the AI synchronous fan-out in Demo Mode is a known ceiling.

### When to revisit
If a single endpoint needs sustained high-concurrency streaming/websockets, or if the service is split into independently-deployed microservices, reconsider an ASGI framework for that slice.

### Related
- **ADRs:** 0016 (React frontend it pairs with), 0019 (REST/DRF API style), 0022 (RBAC via DRF permission classes).
- **Source:** `backend/config/settings.py`, `backend/config/urls.py`, `apps/*/views.py`, `apps/*/admin.py`.
- **Env vars:** `DEBUG`, `SECRET_KEY`, `ALLOWED_HOSTS`.

---

## ADR 0016: React + Vite single-page app (static) for the frontend

- **Status:** Accepted · **Date:** 2026-07 (retroactive)

### Context / Problem
The product is an authenticated internal dashboard (charts, tables, upload, chat). It needs a rich client, must be hostable cheaply/statically, and must talk to a separate API origin.

### Options considered
| Option | Complexity | Hosting | SEO need | Fit |
|---|---|---|---|---|
| **React + Vite SPA (static)** | Low–Med | Any CDN/static host | None (auth-gated) | Excellent |
| Next.js (SSR/RSC) | Med–High | Node runtime | None here | Overkill |
| Django templates (server-rendered) | Low | Same as API | None | Poor for a charty SPA |

**SPA pros:** clean frontend/backend separation; static bundle → free/cheap CDN hosting (Vercel); Vite's fast dev/build.
**Cons:** client-side auth token handling; `VITE_API_URL` baked in at build time.

### Decision
React 18 + Vite 5, built to a static bundle, served by nginx (Docker) or Vercel; API base URL from `VITE_API_URL` (`frontend/src/services/api.js:4`).

### Why selected
Behind a login, SEO/SSR is irrelevant, so SSR's cost buys nothing. A static bundle deploys anywhere and decouples release cadence from the API. Vite keeps the dev loop fast.

### Trade-offs / consequences
- Easier: hosting, separation of concerns, caching.
- Harder: `VITE_API_URL` is build-time only (a backend URL change requires a rebuild); tokens live client-side (see ADR 0018).

### When to revisit
If public marketing pages or SEO become requirements, add a Next.js shell for those routes while keeping the app SPA.

### Related
- **ADRs:** 0015 (Django API it consumes), 0018 (JWT client handling), 0020 (React Query), 0024 (Vercel deployment).
- **Source:** `frontend/src/main.jsx`, `frontend/src/App.jsx`, `frontend/src/services/api.js`, `frontend/Dockerfile`, `frontend/vite.config.js`, `frontend/vercel.json`.
- **Env vars:** `VITE_API_URL` (build-time).

---

## ADR 0017: PostgreSQL as the single datastore

- **Status:** Accepted · **Date:** 2026-07 (retroactive)

### Context / Problem
Emissions data demands transactional integrity, row-level locking (concurrent approvals, AI budget), JSON payloads (raw upload rows, audit changes), and durability across redeploys. An earlier iteration lost data by falling back to an ephemeral SQLite file on container disk.

### Options considered
| Option | Transactions/locks | JSON | Ops on free tier | Fit |
|---|---|---|---|---|
| **PostgreSQL** | Excellent (`select_for_update`) | Native JSONB | Managed addons available | Excellent |
| SQLite | Weak (no row locks, file-based) | OK | N/A | Dev-only |
| MySQL | Good | OK | Available | Good |
| A NoSQL store | Eventual/weak txns | Native | Available | Poor for relational+audit |

### Decision
PostgreSQL for all environments except local unit tests. `settings.py:165-173` **fails closed**: `DATABASE_URL` is mandatory when `DEBUG=False`; SQLite is permitted only under `DEBUG=True`.

### Why selected
The concurrency guarantees (approval race, AI budget/idempotency via `select_for_update`, audit-chain append) are only correct on a real RDBMS with row locks — SQLite silently no-ops them. JSONB stores raw rows and audit diffs without extra tables.

### Trade-offs / consequences
- Easier: correctness under concurrency, durability, audit integrity.
- Harder: needs a managed instance (a dependency and a cost); local dev needs Docker Postgres or the SQLite dev fallback.

### When to revisit
Only if the workload outgrows a single primary (read replicas / partitioning) — not on the horizon for this scope.

### Related
- **ADRs:** 0015 (Django ORM), 0021 (multi-tenant scoping over one schema), 0024 (managed DB addon).
- **Source:** `backend/config/settings.py` (`DATABASES`), `docker-compose.yml` (local Postgres), `render.yaml` / `deployment/northflank/template.json` (managed DB).
- **Env vars:** `DATABASE_URL`.

---

## ADR 0018: Stateless JWT authentication (not server sessions)

- **Status:** Accepted · **Date:** 2026-07 (retroactive)

### Context / Problem
A static SPA on one origin (Vercel) must authenticate to an API on another origin (Northflank). Auth must survive reloads and work cross-origin without sticky sessions.

### Options considered
| Option | Cross-origin | Server state | CSRF surface | Fit |
|---|---|---|---|---|
| **JWT (access+refresh)** | Easy (Bearer header) | None | None for token calls | Excellent |
| Django session cookies | Needs `SameSite`/CORS credentials + CSRF | Server-side | Yes | Awkward cross-origin |
| Third-party IdP (Auth0/etc.) | Easy | External | Low | Overkill for a demo |

### Decision
SimpleJWT: access token 15 min, refresh 7 days, `ROTATE_REFRESH_TOKENS`+`BLACKLIST_AFTER_ROTATION`; `Bearer` scheme; separable `JWT_SIGNING_KEY`. Frontend stores tokens in `localStorage`, attaches Bearer via an axios interceptor, and transparently refreshes once on 401 (`services/api.js:43-88`).

### Why selected
Stateless tokens make cross-origin calls trivial (a header, no cookie/CSRF dance), require no server session store, and rotation+blacklist bound the damage of a leaked refresh token. Failed logins are logged non-enumerating.

### Trade-offs / consequences
- Easier: cross-origin auth, horizontal scaling (no shared session store).
- Harder: **`localStorage` tokens are XSS-reachable** (documented future fix — httpOnly-cookie or in-memory strategy); logout must blacklist the refresh token server-side.

### When to revisit
If a stricter security posture is required, move refresh tokens to httpOnly cookies (accepting the CSRF/CORS complexity) or adopt an IdP.

### Related
- **ADRs:** 0016 (SPA client), 0021 (tenant resolved from the authenticated user), 0022 (role read from Membership).
- **Source:** `apps/accounts/views.py`, `apps/accounts/serializers.py`, `settings.py` (`SIMPLE_JWT`, ~362-371), `frontend/src/services/api.js`, `frontend/src/context/AuthContext.jsx`.
- **Env vars:** `SECRET_KEY`, `JWT_SIGNING_KEY`, `JWT_ACCESS_MINUTES`, `JWT_REFRESH_DAYS`.

---

## ADR 0019: REST (DRF) API style, not GraphQL/RPC

- **Status:** Accepted · **Date:** 2026-07 (retroactive)

### Context / Problem
The client needs CRUD over records/batches/factors plus a set of explicit workflow verbs (submit/approve/reject/recalculate) and a few reports.

### Options considered
| Option | Fit for CRUD+verbs | Caching | Client tooling | Complexity |
|---|---|---|---|---|
| **REST + DRF ViewSets/`@action`** | Excellent | HTTP-native | React Query | Low |
| GraphQL | Good for flexible reads | Harder | Apollo/urql | Higher |
| gRPC/RPC | Good for internal svc-svc | Poor for browser | Extra layer | Higher |

### Decision
REST with DRF `DefaultRouter` + ViewSets; custom state transitions as `@action` sub-routes (e.g. `/records/{id}/approve/`). Full contract in [TECHNICAL_HANDOVER.md §8](TECHNICAL_HANDOVER.md).

### Why selected
The data needs are fixed and CRUD-shaped; GraphQL's flexible-query benefit (and its N+1/authorization complexity) isn't warranted for a single known client. `@action` maps workflow verbs cleanly onto resources; HTTP status codes carry semantics (202 async, 201 created, 200 fail-safe).

### Trade-offs / consequences
- Easier: caching, pagination/throttling, one obvious client pattern (React Query).
- Harder: over/under-fetching is possible (mitigated by lean serializers like `BatchProgressSerializer`).

### When to revisit
If many heterogeneous clients need widely different field sets, a GraphQL read layer over the same services could pay off.

### Related
- **ADRs:** 0015 (DRF), 0020 (React Query consumes these endpoints).
- **Source:** `apps/*/urls.py`, `apps/*/views.py`, `backend/config/urls.py`, `docs/TECHNICAL_HANDOVER.md` §8.
- **Env vars:** `THROTTLE_ANON`, `THROTTLE_USER`, `THROTTLE_LOGIN`, `THROTTLE_AI`.

---

## ADR 0020: React Query for server state (not Redux/manual fetch)

- **Status:** Accepted · **Date:** 2026-07 (retroactive)

### Context / Problem
Most frontend state is server-derived (records, batches, metrics, conversations) needing caching, background refetch, polling (upload progress), and invalidation after mutations.

### Options considered
| Option | Server-cache semantics | Boilerplate | Fit |
|---|---|---|---|
| **@tanstack/react-query** | Built-in (stale/refetch/invalidate) | Low | Excellent |
| Redux (+Thunk/RTK) | Manual | High | Overkill |
| Manual `useEffect`+fetch | None | Med, error-prone | Poor |

### Decision
React Query for all server data; `useState`/Context only for local UI + auth. Mutations invalidate the relevant query keys; `useBatchProgress` polls `/batches/{id}/progress/`.

### Why selected
Server state is a cache, not app state — React Query models exactly that (dedup, retries, background refresh, invalidation) with minimal code, eliminating a class of stale-data bugs. Redux would add a store for data that's really remote.

### Trade-offs / consequences
- Easier: consistency after writes, polling, loading/error states.
- Harder: a second mental model alongside Context; cache-key discipline required.

### When to revisit
If complex cross-component *client* state emerges (multi-step wizards, offline), add a dedicated client-state store alongside React Query.

### Related
- **ADRs:** 0016 (React app), 0019 (REST endpoints it caches).
- **Source:** `frontend/src/services/api.js`, `frontend/src/hooks/useBatchProgress.js`, `frontend/src/pages/*`.
- **Env vars:** none.

---

## ADR 0021: Multi-tenant isolation via shared schema + server-side scoping

- **Status:** Accepted · **Date:** 2026-07 (retroactive)

### Context / Problem
Multiple organizations share one deployment; no tenant may ever see another's data. The client must never be trusted to assert its own tenant.

### Options considered
| Option | Isolation strength | Ops complexity | Cost | Fit for scale here |
|---|---|---|---|---|
| **Shared schema + `organization` FK + server scoping** | Strong (if enforced centrally) | Low | Low | Excellent |
| Schema-per-tenant | Stronger | Medium | Medium | Overkill |
| Database-per-tenant | Strongest | High | High | Overkill |

### Decision
One shared schema; every tenant row carries an `organization` FK. Active org is resolved **server-side** from the user's active `Membership` (+ optional validated `X-Organization-ID`) in `resolve_tenant_context`. `TenantScopedViewSetMixin` filters every queryset; `IsOrgMember.has_object_permission` re-checks `obj.organization_id` (defense in depth). The client's org is never trusted from body/query.

### Why selected
For tens–hundreds of tenants, shared-schema scoping gives strong isolation at near-zero operational cost, provided enforcement is centralized (it is: one mixin + one permission base). Schema/DB-per-tenant would add migration and connection complexity with no benefit at this scale.

### Trade-offs / consequences
- Easier: one migration path, cheap onboarding, simple ops.
- Harder: isolation depends on discipline — a query that forgets to scope is a leak (mitigated by the shared mixin + object-level check + tests).

### When to revisit
If a compliance regime mandates physical data separation, or a whale tenant needs isolation/performance guarantees, promote that tenant to schema- or DB-per-tenant.

### Related
- **ADRs:** 0017 (single Postgres schema), 0022 (roles within the tenant), 0018 (tenant derived from the authenticated user).
- **Source:** `apps/accounts/tenancy.py`, `apps/accounts/mixins.py`, `apps/accounts/permissions.py`, `apps/core/models.py` (Organization), `apps/accounts/models.py` (Membership).
- **Env vars:** none (tenant is the `X-Organization-ID` request header, validated against memberships — never an env var).

---

## ADR 0022: RBAC via composable DRF permission classes + Membership roles

- **Status:** Accepted · **Date:** 2026-07 (retroactive)

### Context / Problem
Four org-scoped roles (ORG_ADMIN/ANALYST/AUDITOR/VIEWER) plus a cross-tenant Platform Admin, with capability differences (upload, approve, manage, use AI, view activity). Access must be closed by default and testable.

### Options considered
| Option | Expressiveness | Testability | Fit |
|---|---|---|---|
| **DRF permission classes over `Membership.role`** | High (composable) | High (unit-testable) | Excellent |
| Django auth groups/permissions | Model-level, per-object awkward | Med | Weak for tenant+role |
| Ad-hoc checks in views | High | Low, duplicated | Poor |

### Decision
`Role` TextChoices + capability frozensets (`ROLES_CAN_UPLOAD/APPROVE/…`). Permission classes all extend `IsOrgMember`: `CanUpload`, `CanApprove`, `CanViewActivity`, `CanManageOrgResources`, `CanUseAI`, `CanViewAICosts`, `IsOrgAdmin`, `IsPlatformAdmin`. `DEFAULT_PERMISSION_CLASSES=[IsAuthenticated]` (closed by default). Platform Admin = Django superuser, deliberately **not** a membership role. Business-state gates (is AI on for this org?) are kept **separate** from RBAC (resolved in the AI gateway, see ADR 0007) so `CanUseAI` stays a pure role check.

### Why selected
Composable classes read declaratively on each view, unit-test in isolation, and keep RBAC out of business logic. Modeling Platform Admin as a superuser (not a role) cleanly separates cross-tenant ops from in-tenant roles.

### Trade-offs / consequences
- Easier: auditing "who can do what," adding a capability, testing.
- Harder: several near-identical classes (deliberate — distinct call-site meanings that can diverge later without a rename).

### When to revisit
If roles need to become dynamic/customer-defined, move the capability sets into data (a Role/Permission table) instead of code.

### Related
- **ADRs:** 0021 (tenant scoping the roles operate within), 0018 (identity), 0007 (business-state AI gating kept separate from RBAC).
- **Source:** `apps/accounts/permissions.py`, `apps/accounts/models.py` (Role, capability frozensets), `apps/*/views.py` (`permission_classes`).
- **Env vars:** none.

---

## ADR 0023: Demo Mode + a deterministic Demo AI provider (not a real LLM in the demo)

- **Status:** Accepted · **Date:** 2026-07 · Related: `apps/ai/providers/demo.py`, `core/execution.py`, `services/policy.py`, ADR 0005

### Context / Problem
A public portfolio demo must run on free hosting with **no background worker/broker**, **no external AI spend**, **no data egress**, and must still exercise the full pipeline and give a working ESG Assistant. Real LLMs cost money, require keys, can leak data, and — run synchronously in a gunicorn request (Demo Mode) — can exceed the request timeout.

### Options considered
| Option | Cost | Egress/keys | Determinism | Answers ESG Qs |
|---|---|---|---|---|
| **`demo` provider (deterministic, schema-valid)** | $0 | None | Yes | Yes (built-in KB) |
| `echo` provider | $0 | None | Yes | **No** (fails schema) |
| Real Anthropic/OpenAI in demo | $ | Key + egress | No | Yes, but timeout/cost/leak risk |
| Disable AI in demo | $0 | None | — | No (assistant dead) |

### Decision
Two orthogonal pieces:
1. **Demo Mode** (`DEMO_MODE=True`): `core/execution.resolve_celery_execution` derives `CELERY_TASK_ALWAYS_EAGER=True`, so the ingest→calculate→AI chain runs synchronously in-process — no worker/broker needed. Production (`DEMO_MODE=False`) is byte-for-byte the async system.
2. **Demo AI provider** (`providers/demo.py`): deterministic, zero-network, **schema-valid** responses; detects the target capability by the unique required-field name each prompt template spells out, and answers common ESG questions (Scope 1/2/3, carbon footprint, SAP fuel, factors) from a built-in knowledge base. `bootstrap_data` seeds a `TenantAIPolicy(ai_enabled=True, provider_override="demo", egress_tier=NO_EGRESS)` for the demo org, and `demo` is in `ZERO_EGRESS_PROVIDERS` — so the demo tenant can **never** call a real vendor even if misconfigured.

`echo` was insufficient because it returns a hash stub that fails every real schema; a real LLM was rejected for cost/egress/timeout. This fits the existing provider abstraction (ADR 0005) — it's just one more adapter behind the gateway.

### Why selected
It's the only option that is free, egress-free, deterministic, **and** produces a genuinely working assistant, while changing zero production behavior (a flag + a provider adapter). `NO_EGRESS` makes the safety property structural, not procedural.

### Trade-offs / consequences
- Easier: a safe, free, self-contained live demo; production untouched.
- Harder: demo answers are canned (not "real AI"); enabling AI for the demo org routes upload-time capabilities through `demo` too (deterministic, fine). Demo Mode's synchronous fan-out with a *real* provider would risk the gunicorn timeout — which is exactly why the demo uses `demo`, not a vendor.

### When to revisit
If the demo should showcase real generative answers, provision a real key + budget and flip the demo org's `provider_override` — but only after bounding AI fan-out per request and moving the provider call out of the per-org lock (both noted as future work).

### Related
- **ADRs:** 0005 (LLM provider abstraction this plugs into), 0007 (egress tiers / `NO_EGRESS`), 0024 (Demo Mode deployment profile).
- **Source:** `apps/ai/providers/demo.py`, `apps/ai/providers/factory.py`, `apps/ai/services/policy.py`, `apps/ai/services/egress.py`, `apps/core/execution.py`, `apps/core/management/commands/bootstrap_data.py`, `backend/config/settings.py`.
- **Env vars:** `DEMO_MODE`, `AI_ENABLED`, `AI_PROVIDER`, `AI_PROVIDER_TIMEOUT_SECONDS`, `BOOTSTRAP_DATA`, `BOOTSTRAP_DEMO_USERS`, `DEMO_USER_PASSWORD`, `CELERY_TASK_ALWAYS_EAGER` (derived).

---

## ADR 0024: Split deployment — Northflank (API) + Vercel (SPA) + Cloudflare R2, Demo Mode vs Render production

- **Status:** Accepted · **Date:** 2026-07 · Related: `deployment/northflank/`, `render.yaml`, `docker-compose.yml`, `entrypoint.sh`

### Context / Problem
Need a live, free/cheap public deployment of both a Dockerized Django API (with Postgres and S3-compatible storage) and a static SPA, plus a documented path to a full production topology (worker+beat+redis) for later.

### Options considered (compute for the API)
| Option | Free tier for Docker+Postgres | Always-on | Public HTTPS | Fit for demo |
|---|---|---|---|---|
| **Northflank (Sandbox)** | Yes (verified) | Yes (no sleep) | Yes | Chosen |
| Render (free web) | Yes but sleeps; blueprint used for prod | Sleeps | Yes | Prod blueprint |
| Railway/Koyeb/Fly.io | Trial-limited / credit-based / sleeps | Varies | Yes | Ruled out (evidence in DEMO_DEPLOYMENT_PLAN) |

### Decision
- **Backend** → Northflank Combined Service from `/backend/Dockerfile` (leading-slash paths; build context `/backend`; port 8000 hardcoded; health `/healthz`), **Demo Mode** profile (single service, no worker/broker).
- **Database** → Northflank PostgreSQL addon (`DATABASE_URL`).
- **Files** → Cloudflare R2 over the S3 API (`AWS_*`, `addressing_style=virtual`) — R2 works with django-storages with no code change, egress-friendly.
- **Frontend** → Vercel static build (`VITE_API_URL` baked in), SPA rewrites in `vercel.json`.
- **Production topology** (kept, not used for the demo) → `render.yaml`: api + worker + beat + redis + db. `docker-compose.yml` runs the whole stack locally (incl. MinIO + Flower).
- **Boot** → `entrypoint.sh`: migrate → collectstatic → (if `BOOTSTRAP_DATA`) `bootstrap_data` + `seed_carbon`.

Production and Demo Mode share one codebase but are **completely separated** by configuration — Render/Celery artifacts are never modified for the demo.

### Why selected
Northflank's Sandbox was the only compared free tier that runs a Dockerized Django app + Postgres, always-on, with a public HTTPS URL and no forced trial expiry (verified against current docs, not memory). Vercel is the natural home for a static Vite bundle. R2 avoids storage egress fees and needs no code change. Keeping Render's blueprint documents the real production shape without paying for it now.

### Trade-offs / consequences
- Easier: a free always-on demo; clean prod/demo separation; cheap static frontend.
- Harder: two platforms + R2 to configure (many env vars, entered manually — the source of several real incidents: missing `DEMO_MODE`, `DEMO_USER_PASSWORD`, `AI_ENABLED`); `VITE_API_URL` is build-time; the demo's synchronous pipeline has a throughput ceiling.

### When to revisit
When real async/scale is needed, deploy the `render.yaml` topology (or replicate worker+beat+redis on Northflank). If the manual env-var entry keeps causing incidents, adopt the IaC `template.json` as the source of truth.

### Related
- **ADRs:** 0016 (Vercel-hosted SPA), 0017 (managed Postgres), 0023 (Demo Mode profile deployed here).
- **Source:** `deployment/northflank/template.json`, `deployment/northflank/CHECKLIST.md`, `render.yaml`, `docker-compose.yml`, `backend/Dockerfile`, `frontend/Dockerfile`, `backend/entrypoint.sh`.
- **Env vars:** `DATABASE_URL`, `STORAGE_BACKEND`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_STORAGE_BUCKET_NAME`, `AWS_S3_ENDPOINT_URL`, `AWS_S3_REGION_NAME`, `AWS_S3_ADDRESSING_STYLE`, `ALLOWED_HOSTS`, `CORS_ALLOWED_ORIGINS`, `CSRF_TRUSTED_ORIGINS`, `RUN_MIGRATIONS`, `DJANGO_SUPERUSER_USERNAME/EMAIL/PASSWORD`, `DEMO_MODE`, `VITE_API_URL`, `REDIS_URL` (production only).

---

## Decision Stability Matrix

A future engineer's quick-reference: which foundational decisions are expected to stay put, and which are likely to change as the product grows.

| Decision | Current Status | Still Valid? | Should Revisit? | Why |
|---|---|---|---|---|
| 0015 Django + DRF | Accepted | ✅ Yes | 🟢 Stable | Core fit for an auditable relational domain; no pressure to change. |
| 0016 React + Vite SPA | Accepted | ✅ Yes | 🟢 Stable | Auth-gated app; SSR/SEO not needed. Revisit only if public/SEO pages appear. |
| 0017 PostgreSQL | Accepted | ✅ Yes | 🟢 Stable | Concurrency + audit correctness depend on it; revisit only at read-replica scale. |
| 0018 JWT auth | Accepted | ✅ Yes | 🟡 Watch | Sound, but `localStorage` token storage is the top hardening candidate (XSS). |
| 0019 REST/DRF | Accepted | ✅ Yes | 🟢 Stable | Fixed CRUD+verbs, single client; GraphQL unjustified. |
| 0020 React Query | Accepted | ✅ Yes | 🟢 Stable | Right tool for server-cache state; revisit only if heavy client state emerges. |
| 0021 Shared-schema multi-tenancy | Accepted | ✅ Yes | 🟡 Watch | Correct at current scale; revisit if a whale tenant or physical-isolation compliance appears. |
| 0022 RBAC permission classes | Accepted | ✅ Yes | 🟡 Watch | Solid for fixed roles; revisit if roles must become customer-configurable (move to data). |
| 0023 Demo Mode + Demo provider | Accepted | ✅ Yes | 🟡 Watch | Perfect for the demo; revisit if the demo must show real generative AI (needs fan-out + lock fixes first). |
| 0024 Northflank + Vercel + R2 | Accepted | ✅ Yes | 🟠 Likely | Great for a free demo; a real production launch means standing up the `render.yaml` async topology and adopting IaC for env vars. |

Legend: 🟢 stable (no change foreseen) · 🟡 watch (sound now, has a known upgrade path) · 🟠 likely (expected to change as the product moves from demo to production).

---

## Index — existing domain ADRs (`docs/adr/`)

These cover decisions below the stack level and remain the authoritative record for each topic:

| ADR | Decision |
|---|---|
| [0001](adr/0001-fixed-approval-workflow-status-field.md) | Approval workflow reuses `EmissionRecord.status`, not a second field |
| [0002](adr/0002-compliance-reports-on-demand-not-persisted.md) | Compliance reports generated on demand, never persisted |
| [0003](adr/0003-security-hardening-scope.md) | Security-hardening scope (app vs infrastructure) |
| [0004](adr/0004-soft-delete-orthogonal-fields.md) | Soft delete as orthogonal fields + manager split |
| [0005](adr/0005-ai-provider-abstraction-and-schema-enforcement.md) | **LLM provider abstraction + schema enforcement at the gateway** |
| [0006](adr/0006-ai-advisory-only-no-direct-mutation.md) | **AI is advisory-only — no path to mutate governed data** |
| [0007](adr/0007-ai-tenant-egress-and-cost-policy.md) | **Per-tenant AI egress tiers, redaction, budget in the gateway** |
| [0008](adr/0008-ai-evaluation-tiering.md) | Two-tier AI evaluation (deterministic blocking + LLM-judge advisory) |
| [0009](adr/0009-anomaly-explanation-async-dispatch-and-immutable-annotations.md) | Anomaly explanation async + immutable annotations |
| [0010](adr/0010-factor-recommendation-candidate-labels-and-dedicated-model.md) | Factor recommendation uses candidate labels, dedicated model |
| [0011](adr/0011-validation-assistance-reuses-aiannotation.md) | Validation assistance reuses AIAnnotation |
| [0012](adr/0012-esg-assistant-synchronous-structured-retrieval.md) | **ESG Assistant synchronous, deterministic retrieval (no vector store)** |
| [0013](adr/0013-report-narration-approved-only-context-and-async-api-dispatch.md) | Report narration from approved data only, async dispatch |
| [0014](adr/0014-ai-observability-cost-governance-and-ops-health-reuse-not-duplicate.md) | AI observability/cost/ops built from existing data, no duplicate accounting |

**Bold** rows are the AI decisions this document's request referenced ("provider abstraction for AI", advisory-only, egress) — they already exist as full ADRs; ADR 0023 above adds the newer Demo-Provider decision that post-dates them.
