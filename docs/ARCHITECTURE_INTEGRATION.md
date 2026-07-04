# Architecture Integration Map (`ARCHITECTURE_INTEGRATION.md`)

This document identifies **where each planned feature integrates** into the current
codebase and the **minimal change** required to land it. The goal is that each phase
adds consuming code at known seams rather than refactoring the architecture.

> **Status update (Phase 2):** JWT Authentication, RBAC, and Multi-Tenant Isolation
> (sections 1–3) are now **implemented** — see [`AUTH_RBAC.md`](AUTH_RBAC.md) for the
> authoritative design. Sections 4–6 (Emission Factor Engine, Metrics API, Celery/Redis)
> remain seams for future phases, unchanged by this update.

The current design already provides most of the seams:

- Every domain model carries an `organization` ForeignKey → multi-tenant ready.
- Business logic lives in a `services/` layer, not views → swappable / async-ready.
- Ingestion is a single orchestrator method (`IngestionService.ingest`) → one task boundary.
- Normalization is isolated (`NormalizationService`) → emission factors slot in after it.
- DRF config is centralized in `settings.REST_FRAMEWORK` → auth/permissions/pagination flip in one place.
- Inert config seams (`REDIS_URL`, `CELERY_*`, `CACHES`, `FEATURE_*` flags) are declared in `settings.py` and default to off.

---

## Summary

| Feature | Phase | Status | Primary integration point |
| :--- | :---: | :--- | :--- |
| JWT Authentication | 2 | ✅ Implemented | `apps/accounts/` (views, serializers); `settings.SIMPLE_JWT` |
| RBAC | 2 | ✅ Implemented | `apps/accounts/permissions.py`; `Membership.role` |
| Multi-Tenant Isolation | 2 | ✅ Implemented | `apps/accounts/tenancy.py`; `TenantScopedViewSetMixin` |
| Emission Factor Engine | 3 | Seam only | `NormalizationService.normalize` |
| Metrics API | 4 | Seam only | `apps/ingestion/urls.py` router |
| Celery + Redis (async) | 5 | Seam only | `IngestionService.ingest` / `views.BaseUploadView` |

Sections 4–6 below are unchanged from Phase 0/1 planning. Sections 1–3 are kept
for historical context (what was planned) — see [`AUTH_RBAC.md`](AUTH_RBAC.md) for
what was actually built.

---

## 1. JWT Authentication — Phase 2

**Where it plugs in:** [`backend/config/settings.py`](../backend/config/settings.py) → `REST_FRAMEWORK['DEFAULT_AUTHENTICATION_CLASSES']`, and [`backend/config/urls.py`](../backend/config/urls.py) for token endpoints.

**Current seam:** `REST_FRAMEWORK` now lists the effective defaults explicitly. `BaseUploadView.post` and the `approve` action already read `request.user` (falling back to `None` when unauthenticated) — see [`apps/ingestion/views.py`](../backend/apps/ingestion/views.py) lines ~61 and ~175. Once auth is enforced, `request.user` becomes a real user and `uploaded_by` / `approved_by` populate automatically.

**Minimal change:**
1. `pip install djangorestframework-simplejwt`; add to `requirements.txt`.
2. Add `'rest_framework_simplejwt.authentication.JWTAuthentication'` to `DEFAULT_AUTHENTICATION_CLASSES`.
3. Add `TokenObtainPairView` / `TokenRefreshView` routes under `/api/auth/`.
4. Frontend: attach `Authorization: Bearer` in [`frontend/src/services/api.js`](../frontend/src/services/api.js) request interceptor (a seam already exists there for the response interceptor).

**Data-model impact:** none (uses the built-in `User`). **Deployment impact:** none new.

---

## 2. RBAC (Role-Based Access Control) — Phase 2

**Where it plugs in:** DRF permission classes on the viewsets in [`apps/ingestion/views.py`](../backend/apps/ingestion/views.py); the global default in `settings.REST_FRAMEWORK`.

**Current seam:** Views are thin and already action-oriented (`approve` is a discrete `@action`), so per-action permissions attach cleanly. The `DEFAULT_PERMISSION_CLASSES` seam flips the global default to `IsAuthenticated`.

**Minimal change:**
1. Model roles via Django Groups, or a `Membership(user, organization, role)` model (preferred — it also carries tenant binding, see §3).
2. Add custom permission classes (e.g. `IsAnalyst`, `IsApprover`) and set them on the `approve` action and write endpoints.
3. Gate the `FEATURE_JWT_AUTH` flag (already declared) if a phased rollout is wanted.

**Data-model impact:** new `Membership`/role table (additive migration). **Deployment impact:** none.

---

## 3. Multi-Tenant Isolation — Phase 2

**Where it plugs in:** every `get_queryset` in [`apps/ingestion/views.py`](../backend/apps/ingestion/views.py) (`UploadBatchViewSet`, `EmissionRecordViewSet`, `OrganizationViewSet`, `DataSourceViewSet`) and the upload/approve flows.

**Current seam:** **All** domain models already have an `organization` FK — `Organization`, `DataSource`, `UploadBatch`, `EmissionRecord`, `AuditTrail`. The isolation is documented in [`MODEL.md`](MODEL.md) but **not yet enforced** in queries; that enforcement is the whole change. A `FEATURE_ENFORCE_TENANT_SCOPE` flag is declared for a safe rollout.

**Minimal change:**
1. Add a `Membership(user, organization, role)` model resolving `request.user → organization`.
2. Add a `TenantScopedViewSetMixin` whose `get_queryset` applies `.filter(organization=request.user_org)`; mix it into the four viewsets (replaces the manual `organization` query-param filter currently in `EmissionRecordViewSet.get_queryset`).
3. Set `organization` from the authenticated user on create/upload instead of deriving it from the `DataSource`.

**Data-model impact:** `Membership` table only (the FKs already exist). **Deployment impact:** none. **Risk:** must land with auth (needs a real `request.user`).

---

## 4. Emission Factor Engine → real tCO₂e — Phase 3

**Where it plugs in:** [`apps/ingestion/services/normalizer.py`](../backend/apps/ingestion/services/normalizer.py) (`NormalizationService.normalize`) and the record-build step in [`ingestion_service.py`](../backend/apps/ingestion/services/ingestion_service.py) (~lines 100–135).

**Current seam:** Normalization already converts raw inputs to a **base activity unit + scope** (`L`, `kWh`, `km` with a `scope_category`) and returns a structured `NormalizationResult`. This is exactly the input an emission-factor lookup needs. `DECISIONS.md §3.1` explicitly deferred CO₂e to "downstream" — this is that downstream. `EmissionRecord` already has precise `DecimalField`s to hold the result.

**Minimal change:**
1. New `EmissionFactor` model — versioned by `(scope, activity_unit, region, valid_from)` with a `kg_co2e_per_unit` Decimal.
2. New `EmissionCalculationService` that maps a `NormalizationResult` → `co2e_tonnes` via the active factor.
3. Add `co2e_value` (+ `emission_factor` FK, `factor_version`) fields to `EmissionRecord` (additive migration).
4. Call the calc service right after normalization in `IngestionService.ingest`; store the result.
5. Gate on `FEATURE_EMISSION_FACTORS` (declared) during rollout; the frontend's mislabeled "Calculated CO₂e" column then becomes truthful.

**Data-model impact:** new `EmissionFactor` table + additive `EmissionRecord` columns. **Deployment impact:** a factor seed (extend `bootstrap_data`).

---

## 5. Metrics / Aggregation API — Phase 4

**Where it plugs in:** [`apps/ingestion/urls.py`](../backend/apps/ingestion/urls.py) router; consumed by [`frontend/src/pages/DashboardPage.jsx`](../frontend/src/pages/DashboardPage.jsx).

**Current seam:** The Dashboard currently fetches whole record lists and counts them client-side (`response.length`). A dedicated endpoint replaces that with DB-side aggregation. The `organization` FK enables per-tenant aggregates; the `CACHES`/`REDIS_URL` seam is ready for caching.

**Minimal change:**
1. Add a `MetricsViewSet` / `APIView` at `/api/metrics/` using ORM `Count`/`Sum`/`annotate` (grouped by status, scope, batch).
2. Optionally cache via the `default` cache alias (Redis when `REDIS_URL` is set).
3. Frontend: replace the three sequential list fetches in `DashboardPage` with one metrics call.
4. Enable `REST_FRAMEWORK` pagination **in the same phase** (frontend must switch to reading `results`).

**Data-model impact:** none (read-only aggregation). **Deployment impact:** optional Redis.

---

## 6. Celery + Redis (Async Ingestion) — Phase 5

**Where it plugs in:** [`apps/ingestion/services/ingestion_service.py`](../backend/apps/ingestion/services/ingestion_service.py) (`IngestionService.ingest`) and the caller [`apps/ingestion/views.py`](../backend/apps/ingestion/views.py) (`BaseUploadView.post`).

**Current seam:** Ingestion is already a **single orchestrator method** with a clean signature (`ingest(data_source, file_path, uploaded_by)`) that creates the `UploadBatch` up front and returns a structured `IngestionResult`. The `UploadBatch.status` state machine (`PENDING → PROCESSING → COMPLETED/FAILED`) is designed for async progress. `CELERY_*` config seams and `CELERY_TASK_ALWAYS_EAGER` (defaults to `DEBUG`) are already declared in `settings.py`.

**Minimal change:**
1. `pip install celery redis`; add `backend/config/celery.py` app + `apps/ingestion/tasks.py` wrapping `IngestionService.ingest` in a `@shared_task`.
2. `BaseUploadView.post` persists the upload to durable storage and enqueues the task, returning `202 Accepted` + `batch_id` (the batch already exists in `PENDING`).
3. Frontend polls `/api/batches/{id}/` for status (the endpoint already exists).
4. Add a `worker` service to `docker-compose.yml` and a Render background worker; add a `redis` service. All config already reads from env.

**Data-model impact:** none (status field already exists). **Deployment impact:** new Redis + worker process (compose service + Render worker).

---

## Config seams already in place

Declared in [`backend/config/settings.py`](../backend/config/settings.py):

| Setting | Consumed by | Status |
| :--- | :--- | :--- |
| `REST_FRAMEWORK` (auth/perm) | JWT (2), RBAC (2), Pagination (4) | JWT auth + `IsAuthenticated` default now **active** (Phase 2). Pagination still off. |
| `SIMPLE_JWT` (lifetimes, rotation, blacklist) | JWT (2) | **Active** (Phase 2). |
| `REDIS_URL` | Cache (4), Celery (5) | Inert (`''` unset) |
| `CELERY_BROKER_URL` / `_RESULT_BACKEND` / `_TASK_ALWAYS_EAGER` | Celery (5) | Inert (Redis fallback / eager in debug) |
| `CACHES` (Redis when `REDIS_URL` set) | Metrics API (4) | Inert |
| `FEATURE_EMISSION_FACTORS` | Emission Factor Engine (3) | Inert, still reserved for a phased rollout |
| `FEATURE_JWT_AUTH` / `FEATURE_ENFORCE_TENANT_SCOPE` | — | **Superseded / dead.** Phase 2 implemented auth and tenant isolation unconditionally rather than behind a dark-launch flag. These two settings are unused and are candidates for removal in a future cleanup pass. |

**Nothing above is wired to behavior yet** — these are declarations so later phases add only consuming code.
