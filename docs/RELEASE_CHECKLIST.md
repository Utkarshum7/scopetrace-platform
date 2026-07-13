# Release Checklist (`RELEASE_CHECKLIST.md`)

Phase 9d — the consolidated, system-wide release-candidate audit. Every
item below cites where it's verified in more depth rather than re-deriving
it here; this document's job is to be the one place a release manager
reads top to bottom before a real production launch. See
[`RELEASE_NOTES.md`](RELEASE_NOTES.md) for what shipped and
[`SMOKE_TEST_CHECKLIST.md`](SMOKE_TEST_CHECKLIST.md) for the manual
post-deploy verification pass.

Distinct from [`DEPLOYMENT_GUIDE.md`](DEPLOYMENT_GUIDE.md) §6's
per-commit/per-PR checklist (a lightweight developer-workflow gate run on
every release-bound change) — this is the one-time, comprehensive audit
for an actual production launch decision.

Status legend: ✅ verified this session or a prior phase · ⚠️ verified with
a caveat (see note) · ⛔ not verified (no deploy access) · 🔜 deferred,
tracked as future work.

---

## 1. Backend

| Item | Status | Detail |
|---|---|---|
| Django 6.0.7, no known CVEs at pinned versions | ✅ | [`SECURITY.md`](SECURITY.md) §5, Phase 9c |
| `manage.py check --deploy` clean | ✅ | 0 issues, Phase 9a/9b/9c |
| Fail-closed config (`SECRET_KEY`, `DATABASE_URL`, `ALLOWED_HOSTS`, `STORAGE_BACKEND`) | ✅ | `config/settings.py`, [`SECURITY.md`](SECURITY.md) §2 |
| `DEBUG=False` in every deployed environment (Render, Docker prod profile) | ✅ | `render.yaml`, `docker-compose.yml`'s `api` service sets `DEBUG=False` |
| Migrations apply cleanly, `makemigrations --check` clean | ✅ | Verified every milestone this phase |
| Full backend test suite passing | ✅ | 915 tests, `Ran 915 tests ... OK`, Phase 9a/9b/9c |
| Structured logging with request-ID correlation | ✅ | `apps/core/middleware.py`, `apps/core/logging_utils.py`, Phase 9b |
| DRF browsable API disabled outside `DEBUG` | ✅ | Phase 9a |
| Exception handling doesn't leak internals to clients | ✅ | `apps/core/exception_handlers.py`, [`SECURITY.md`](SECURITY.md) §9 |

## 2. Frontend

| Item | Status | Detail |
|---|---|---|
| `npm run build` succeeds, production bundle generated | ✅ | Every milestone this phase |
| `npm test` passing | ✅ | 92 tests |
| `npm run lint` — 0 errors (4 pre-existing warnings, unrelated to any recent change) | ✅ | |
| Known frontend CVEs remediated where non-breaking | ✅ | `form-data`/`js-yaml` fixed, `esbuild`/`vite` dev-only issue deliberately deferred — [`SECURITY.md`](SECURITY.md) §5 |
| `VITE_API_URL` is the only client-exposed env var (no secret leakage via build-time baking) | ✅ | Phase 9c |
| No frontend automated test suite beyond Vitest unit/component tests (no E2E) | 🔜 | [`ROADMAP.md`](ROADMAP.md) §1 |

## 3. Docker

| Item | Status | Detail |
|---|---|---|
| Multi-stage builds (backend `deps`→`runtime`, frontend `build`→`serve`), non-root user, explicit `COPY` allowlists | ✅ | `backend/Dockerfile`, `frontend/Dockerfile` |
| `docker-build.yml` CI (build-only, no push) green | ✅ | Every push this phase |
| `docker-compose.yml` internally consistent with `render.yaml` (queue topology, env vars, health checks) | ✅ | Cross-checked, Phase 9a |
| Live `docker compose up` end-to-end run | ⛔ | **Not performed this phase** — sandbox disk space (~5.5GB free) matched the exact condition that crashed Docker Desktop twice earlier in this project; static config review was judged sufficient rather than risking a third crash. Recommended before the first real production cutover. |
| nginx security headers on the frontend container | ✅ | Phase 9c |

## 4. Render (backend hosting)

| Item | Status | Detail |
|---|---|---|
| `render.yaml` — web/worker/beat services, health check path, migration gating | ✅ | Reviewed line-by-line, Phase 9a |
| `render.yaml`'s `type: redis` service-type naming | ⛔ | Self-disclosed in `render.yaml`'s own header comment as unverified against Render's live blueprint validator (Render has renamed this before). Documented fallback: provision Redis manually and paste the connection string as a `sync: false` secret. |
| Cross-service `SECRET_KEY` sharing via `fromService` | ⛔ | Same category — unverified against live Render. Documented fallback: generate once, paste into all three services' dashboards identically. |
| Secrets (`DJANGO_SUPERUSER_PASSWORD`, storage/email credentials) never committed, `sync: false` in dashboard | ✅ | `render.yaml` review, Phase 9a/9c |
| Free-tier PostgreSQL 90-day expiry | 🔜 | Documented risk, not a code issue — upgrade to paid plan or external Postgres before a persistent launch |
| gunicorn access logging correlated with request ID | ✅ | Phase 9b |

## 5. Vercel (frontend hosting)

| Item | Status | Detail |
|---|---|---|
| `vercel.json` — build/output/SPA-rewrite config | ✅ | Reviewed, Phase 9a |
| Root Directory = `frontend`, `VITE_API_URL` set as a build-time env var | ⛔ | Documented step-by-step in [`DEPLOYMENT_GUIDE.md`](DEPLOYMENT_GUIDE.md) §3.5 (added Phase 9a) — cannot be verified without an actual Vercel project |
| Real assigned Vercel domain reconciled against `render.yaml`'s `CORS_ALLOWED_ORIGINS`/`CSRF_TRUSTED_ORIGINS` (currently the placeholder `scopetrace.vercel.app`) | ⛔ | Same — first-deploy manual step, documented |

## 6. Celery

| Item | Status | Detail |
|---|---|---|
| 6 named queues (`celery`/`ingestion`/`calculation`/`maintenance`/`notifications`/`ai`), routed via `CELERY_TASK_ROUTES` | ✅ | `config/settings.py`; drift-guarded by `apps/tasks/tests_queue_coverage.py` against `render.yaml`/`docker-compose.yml` on every test run |
| `acks_late` + `prefetch=1` for safe at-least-once redelivery | ✅ | `config/celery.py` |
| JSON-only task serialization (no pickle deserialization-RCE risk) | ✅ | Verified Phase 9c |
| Dead-letter handling (`FailedTaskLog`, retries-exhausted signal) | ✅ | `apps/tasks/signals.py` — reviewed extensively, Phase 9b, already excellent |
| Static, code-defined `CELERY_BEAT_SCHEDULE` (git-auditable, not DB-backed) | ✅ | Deliberate design choice, [`SCHEDULED_TASKS.md`](SCHEDULED_TASKS.md) |
| Worker/Beat health surfaced at `/healthz/worker/` | ✅ | Real `celery inspect ping()` round trip |

## 7. Redis

| Item | Status | Detail |
|---|---|---|
| Used as both Celery broker/result-backend and Django cache backend | ✅ | |
| `ipAllowList: []` on Render — no external network access | ✅ | `render.yaml`, Phase 9c |
| In-memory cache + `CELERY_TASK_ALWAYS_EAGER` fallback when `REDIS_URL` unset (local dev without a broker) | ✅ | `config/settings.py` |

## 8. MinIO / S3 Storage

| Item | Status | Detail |
|---|---|---|
| Provider-agnostic `StorageService` abstraction (AWS S3 / R2 / B2 / MinIO) | ✅ | `apps/core/storage/` |
| `STORAGE_BACKEND=s3` required and fail-closed when `DEBUG=False` | ✅ | Boot-time `ImproperlyConfigured` |
| Presigned download URLs expire (`AWS_S3_URL_EXPIRE_SECONDS`, default 3600s) | ✅ | [`SECURITY.md`](SECURITY.md) §8 |
| Upload-path storage-key construction reviewed for path traversal | ✅ | Investigated Phase 9c — not exploitable on either backend (Django `FileSystemStorage`'s `SuspiciousFileOperation` guard locally; S3's flat key namespace in production) |
| Object storage backup/retention policy | 🔜 | Self-disclosed gap in [`INCIDENT_RESPONSE.md`](INCIDENT_RESPONSE.md), pre-existing, not addressed this phase |

## 9. PostgreSQL

| Item | Status | Detail |
|---|---|---|
| Required in production (SQLite dev-only, `DEBUG=True` only) | ✅ | Fail-closed in `config/settings.py` |
| `/healthz` executes a real `SELECT 1`, not a passive check | ✅ | `apps/core/views.py` |
| Indexes on hot query paths (`EmissionRecord.status`, `UploadBatch`) | ✅ | Added Phase 7.5 H4-1 |
| Migration history applies cleanly on a fresh database | ✅ | Verified every milestone via `manage.py migrate` in test runs |

## 10. AI (apps.ai)

| Item | Status | Detail |
|---|---|---|
| Global kill switch (`AI_ENABLED=False` default) — zero cost/egress until explicitly opted in | ✅ | [`AI_ARCHITECTURE.md`](AI_ARCHITECTURE.md) |
| Single gateway enforcement point (`invoke_ai()`) for every governed AI call | ✅ | `apps/ai/services/gateway.py` — structurally enforced by `apps.ai.tests_import_guard` |
| Per-tenant policy/budget/egress-tier controls | ✅ | `apps/ai/services/policy.py`, `cost.py`, `egress.py` |
| Idempotency + budget-race concurrency safety (per-org lock) | ✅ | Phase 7.5 H2, three findings fixed and verified |
| Schema-enforced provider responses (`jsonschema`) | ✅ | `apps/ai/schemas.py` |
| AI gateway now logs governance-relevant refusals/failures (was previously silent) | ✅ | Phase 9b |
| 5 real capabilities: anomaly explanation, factor recommendation, validation assistance, ESG assistant, report narration — all advisory-only, no direct governed-data mutation | ✅ | `AIRecommendationStage` remains inert by design; [`AI_ARCHITECTURE.md`](AI_ARCHITECTURE.md) |
| Evaluation harness (golden datasets, I1–I6 invariant suite, LLM-as-Judge, disabled by default) | ✅ | [`AI_EVALUATION.md`](AI_EVALUATION.md) |
| `/healthz/ai/` — `AI_ENABLED=False` reports healthy (correct: disabled is the expected default), provider constructibility checked when enabled | ✅ | |

## 11. Governance

| Item | Status | Detail |
|---|---|---|
| Per-org SHA-256 audit hash-chain, tamper-evident | ✅ | [`GOVERNANCE.md`](GOVERNANCE.md) §6a, `verify_audit_chain` command/API/admin action |
| Immutable `EmissionRecordVersion` snapshots on every meaningful edit | ✅ | [`GOVERNANCE.md`](GOVERNANCE.md) §6b |
| Fixed Draft → Submitted → Approved/Rejected workflow, enforced at model layer | ✅ | [`GOVERNANCE.md`](GOVERNANCE.md) §6c |
| Reversible soft delete, `PROTECT` on org/batch FKs (no silent cascade destruction) | ✅ | [`GOVERNANCE.md`](GOVERNANCE.md) §6d |
| CSV/JSON compliance reports over `APPROVED`-only data | ✅ | [`GOVERNANCE.md`](GOVERNANCE.md) §6e (PDF still deferred) |
| Bulk `.update()`/`.delete()` blocked at QuerySet layer (can't bypass `clean()`/audit trail) | ✅ | [`GOVERNANCE.md`](GOVERNANCE.md) §6d |

## 12. Authentication & Authorization

| Item | Status | Detail |
|---|---|---|
| JWT access (15 min) / refresh (7 days), rotation + blacklist-after-rotation | ✅ | Reviewed Phase 9c, [`SECURITY.md`](SECURITY.md) §1 |
| Failed-login logging (username + IP, never password) | ✅ | Phase 6f |
| 4 org-scoped roles + cross-tenant Platform Admin, enforced server-side | ✅ | [`AUTH_RBAC.md`](AUTH_RBAC.md) |
| `TenantScopedViewSetMixin` — every queryset filtered server-side, no client-trusted org param | ✅ | Spot-checked Phase 9c |
| DRF throttling (anon/user/login/AI scopes) | ✅ | Reviewed Phase 9c |
| No IP/network restriction on `/admin/` | 🔜 | Infrastructure-layer, documented in [`INFRASTRUCTURE_SECURITY.md`](INFRASTRUCTURE_SECURITY.md) §1 |

## 13. Upload Pipeline

| Item | Status | Detail |
|---|---|---|
| File-type allowlist, server-side content-type verification (not client-trusted) | ✅ | Phase 7.5 H4-4 |
| Async ingestion with retry/backoff on transient DB errors only | ✅ | `apps/ingestion/tasks.py`, [`RETRY_DLQ.md`](RETRY_DLQ.md) |
| Idempotent under Celery at-least-once redelivery | ✅ | Terminal-status guard at top of `ingest_task` |
| `workflow_id` correlation across the whole ingest→calculate chain | ✅ | Every log line, both tasks |
| CSV formula-injection sanitization on exports | ✅ | `apps/core/csv_security.py`, Phase 6f |

## 14. Carbon Engine

| Item | Status | Detail |
|---|---|---|
| Versioned, provenance-tracked emission-factor datasets, effective-dated resolution | ✅ | [`CARBON_ENGINE_DESIGN.md`](CARBON_ENGINE_DESIGN.md) |
| Decimal-precise, factor-pinned, immutable calculations with an explainability trace | ✅ | |
| Seed factors are an illustrative DEFRA 2024 subset, not the full official dataset | 🔜 | Documented since Phase 3, [`ROADMAP.md`](ROADMAP.md) §1 |
| `EmissionFactorDataset` immutability enforced | ✅ | Phase 7.5 H1 Finding 2 |
| Daily safety-net task for records missing a calculation | ✅ | `recalculate_missing_calculations_task` |

## 15. Compliance Reporting

| Item | Status | Detail |
|---|---|---|
| CSV/JSON reports, on-demand (no new persisted table) | ✅ | ADR 0002 |
| Every report embeds a `verify_chain()` snapshot | ✅ | [`GOVERNANCE.md`](GOVERNANCE.md) §6e |
| RBAC-gated (Org Admin/Auditor via `CanViewActivity`) | ✅ | |
| AI report narration (advisory, built only from approved data) | ✅ | [`AI_ARCHITECTURE.md`](AI_ARCHITECTURE.md) §18 |
| PDF export | 🔜 | Deferred since Phase 6e |

---

## 16. Known Risk Register (Final Production Audit)

Every item below is a **previously verified finding**, not a newly
invented one — cross-referenced to where it was first established.
Classified by severity:

### Release blocker
*(none identified)* — no finding in this codebase's own audit history
rises to "must fix before any production traffic."

### High
| Item | Why High, not blocker | Source |
|---|---|---|
| `render.yaml`'s `type: redis` and cross-service `SECRET_KEY` sharing are unverified against Render's live platform | Both have a documented, functionally-equivalent manual fallback if the IaC assumption is wrong — this is a "verify on first deploy," not an unmitigated gap | [`DEPLOYMENT_GUIDE.md`](DEPLOYMENT_GUIDE.md) §3.3, [`SECURITY.md`](SECURITY.md) §10 |
| Live `docker compose up` end-to-end was never run this phase | Static config review found zero drift, but nothing replaces an actual boot; recommended before first production cutover | This session, §3 above |

### Medium
| Item | Detail | Source |
|---|---|---|
| No IP/network restriction on `/admin/` | Protected by Django's own session-auth + superuser requirement; infrastructure-layer fix recommended (VPN/IP allowlist at the platform level), deliberately not solved in Django middleware | [`INFRASTRUCTURE_SECURITY.md`](INFRASTRUCTURE_SECURITY.md) §1 |
| No Content-Security-Policy on the frontend | Needs every script/style/connect-src enumerated and verified in a real browser — a wrong CSP fails closed; deliberately deferred rather than guessed at | [`SECURITY.md`](SECURITY.md) §10, Phase 9c |
| `esbuild`/`vite` dev-server CVE (moderate, dev-only exposure) | Fix requires a 3-major-version Vite bump with its own migration effort; production build is unaffected (static output, no dev server) | [`SECURITY.md`](SECURITY.md) §5, Phase 9c |
| No formal RPO/RTO or tested disaster-recovery drill | Infrastructure-layer, not application code | [`INFRASTRUCTURE_SECURITY.md`](INFRASTRUCTURE_SECURITY.md) §2 |
| Object storage has no documented backup/retention policy | Self-disclosed gap, pre-existing | [`INCIDENT_RESPONSE.md`](INCIDENT_RESPONSE.md) |
| Render free-tier PostgreSQL expires ~90 days after creation | Platform constraint, not a code issue — upgrade before a persistent launch | `render.yaml` |

### Low
| Item | Detail | Source |
|---|---|---|
| No fine-grained upload progress (0%→100% jump) | Deliberate design (atomic transaction = safe retries) | [`ROADMAP.md`](ROADMAP.md) §1 |
| No WebSocket/SSE push for progress — polling only | Deliberate scope decision | [`TRADEOFFS.md`](TRADEOFFS.md) §1 |
| Batch cancellation declared but inert | Reserved interface, same pattern as `AIRecommendationStage` | [`JOB_LIFECYCLE.md`](JOB_LIFECYCLE.md) §6 |
| No frontend E2E test suite (Vitest unit/component tests only) | | [`CI_CD.md`](CI_CD.md) |
| Seed emission factors are an illustrative subset, not the full official DEFRA dataset | Documented since Phase 3 | [`CARBON_ENGINE_DESIGN.md`](CARBON_ENGINE_DESIGN.md) |
| AI observability/cost endpoints have no caching layer | Deliberate — AI call volume is orders of magnitude smaller than carbon data at current scale | ADR 0014 |

### Future enhancement
| Item | Detail |
|---|---|
| PDF compliance report export | Deferred since Phase 6e (ADR 0002) |
| Prometheus/Grafana/Loki/OpenTelemetry/Sentry-grade observability | Today's surface (health endpoints, structured/correlated logs, AI ops dashboards) is the pre-real-APM layer — see [`RELEASE_NOTES.md`](RELEASE_NOTES.md) |
| A real landing page, published architecture diagrams, OpenAPI/Swagger schema, video demo | Phase 10 scope |
| Read-replica / DB routing | Not needed at current scale |

---

## 17. Sign-off

This checklist reflects the system state as of the Phase 9c tip
(`cb51117`) plus this milestone's own documentation additions. All 4
GitHub Actions workflows (Backend CI, Frontend CI, Docker Build
Verification, Secret Scan) are green on every commit in this phase. No
release blocker was identified. The two **High** items above are both
"verify on first real deploy" items with documented fallbacks, not
defects in shipped code — see [`RELEASE_NOTES.md`](RELEASE_NOTES.md) for
the release-readiness recommendation.
