# Metrics, Analytics & Dashboards (`METRICS_ANALYTICS.md`)

Phase 4 adds tenant-scoped analytics over the carbon fact table, a cached
Metrics API, pagination/filtering/export, hardening, and a role-aware, pluggable
dashboard. This document is the authoritative reference.

---

## 1. Fact-table shaping (milestone 4a)

`EmissionCalculation` is the analytics fact table. Three denormalized dimensions
were added so dashboards run **indexed `SUM/GROUP BY` with no joins**:

| Field | Purpose |
| :--- | :--- |
| `scope` | GHG scope (denormalized from the record) — "emissions by scope" |
| `reporting_date` | the activity/emission date — drives time-series (was previously only inside `raw_data_payload`) |
| `reporting_month` | first-of-month truncation — cheap monthly bucketing |

Indexes: `(organization, is_current, scope)`, `(organization, is_current, reporting_date)`, `(organization, is_current, reporting_month)`.

Populated at calc time (ingestion + backfill) and, for existing rows, by data
migration `carbon/0003` (in-place `bulk_update`; never touches the locked record).

---

## 2. Aggregation + caching (milestone 4b)

`MetricsService` (pure, tenant-scoped) computes:
- **summary** — total tCO₂e, previous-period total (for trend deltas), by-scope, status counts, **coverage** (`CALCULATED / (CALCULATED + UNRESOLVED)`), `pending_approval` (records), `batch_count`.
- **timeseries** — `Trunc(reporting_date)` by month/quarter/year, optional `group_by=scope`.
- **breakdown** — grouped by scope / activity_type / data_source.

Only `is_current=True` rows count; CO₂e totals include only `CALCULATED` rows.

**Caching** (`metrics_cache`): each cache key embeds the org's `calc_version`.
Any calc write — **ingestion, recalculate, backfill** — calls `bump_calc_version(org_id)`,
which makes all of that org's cached metrics unreachable at once. No key tracking,
no explicit deletes. Redis when `REDIS_URL` is set; local-memory otherwise. The
service is abstracted so a pre-aggregated rollup table can replace the query
internals later **without changing the API**.

---

## 3. Metrics API (milestone 4c)

| Method | Endpoint | Access | Notes |
| :--- | :--- | :--- | :--- |
| GET | `/api/metrics/summary/` | member | KPIs + coverage + trend basis; cached, tenant-scoped |
| GET | `/api/metrics/timeseries/` | member | `?bucket=month\|quarter\|year&group_by=scope` |
| GET | `/api/metrics/breakdown/` | member | `?dimension=scope\|activity_type\|data_source` |
| GET | `/api/metrics/activity/` | **Org Admin / Auditor** | tenant audit-trail feed |
| GET | `/api/metrics/platform/` | **Platform Admin** | cross-tenant rollup + active orgs |

Filters (`MetricsFilterSerializer`): `date_from`, `date_to`, `scope`, `data_source`.
**Authorization is server-side** — `activity` requires `CanViewActivity`, `platform`
requires `IsPlatformAdmin`. Payloads are JSON-normalized (Decimal→string, date→ISO).

---

## 4. Pagination, filtering, export, hardening (4d–4f)

- **Pagination (4d):** `StandardResultsPagination` (page_size 50, `?page_size` capped at 200). Unbounded lists paginate (`records`, `calculations`, `batches`, `factor-datasets`, `emission-factors`) → `{count, next, previous, results}`. **Bounded selector lists opt out** (`organizations`, `datasources`, `activity-types`) so dropdowns stay whole. `django-filter` FilterSets standardize filtering.
- **Export (4e):** `GET /api/records/export/` streams CSV (`StreamingHttpResponse` + `queryset.iterator()`), tenant-scoped, reuses ledger filters, row-capped at 100k; CO₂e columns via a current-calculation `Subquery` (no N+1). Frontend triggers an authenticated blob download.
- **Hardening (4f):** DRF throttling — anon `100/hour`, user `2000/hour` (env-tunable), scoped `login` `10/min` on the token endpoint. Reference lists cached after permission checks, invalidated by a global version bumped on factor import. Rates disabled under the test runner.

---

## 5. Role-aware, pluggable dashboard (milestone 4g)

**Layering (each boundary independently swappable):**

```
DashboardPage (layout only)
  → registry.js (role → widget descriptors)     ← add a widget here + a file
  → Widget (declares fetch + success render)
      → useWidgetData (TanStack Query → loading/error/empty/success)
      → WidgetFrame (renders the 4 states) + WidgetErrorBoundary (crash isolation)
      → components/charts/* (the ONLY Recharts importer)
  → /api/metrics/* (server-side aggregation + RBAC + cache)
```

- **Pluggable:** `DashboardPage` never imports a widget — it maps the registry. New widget = a component file + one descriptor; the page is untouched.
- **Four-state contract:** every widget uses `useWidgetData` + `WidgetFrame` (loading→skeleton, error→retry, empty→CTA, success→content); one widget failing never breaks the dashboard.
- **Chart abstraction:** `TrendChart` / `DonutChart` / `BarChart` wrap Recharts behind a library-agnostic prop contract; **`components/charts/` is the only place `recharts` is imported**, enforced by an ESLint `no-restricted-imports` rule. Swapping chart libs = rewrite ~4 files, all call sites unchanged.
- **Data layer:** TanStack Query (caching, request dedup — widgets sharing `/metrics/summary` fire one request, retry).
- **Role composition:** common baseline + exact per-role set. **Conditional rendering is UX only** — every role-scoped endpoint enforces RBAC server-side.

| Role | Widgets (beyond common KPIs/trend/scope) |
| :--- | :--- |
| Viewer | Reports (export) |
| Analyst | Upload shortcut · Recent ingestion · Validation summary |
| Auditor | Pending approvals · Audit queue · Locked records |
| Org Admin | Org totals · Coverage · User activity · Active factor dataset |
| Platform Admin | Cross-tenant overview · System health · Active organizations · Dataset inventory |

---

## 6. Deployment notes

- Redis is optional but recommended in production (`REDIS_URL`) so the metrics
  and reference caches (and, later, Celery) share a durable backend.
- No new migrations beyond `carbon/0002` (schema) + `carbon/0003` (data backfill).
- Throttle rates and cache TTLs are env-tunable (`THROTTLE_*`).
