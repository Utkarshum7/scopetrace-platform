# Production Smoke-Test Checklist (`SMOKE_TEST_CHECKLIST.md`)

Phase 9d. A **manual, documented** post-deploy verification pass — not
automation. Run this against a real deployed environment (or a full
`docker compose up --build` stack standing in for one) after every
production deploy, or whenever [`RELEASE_CHECKLIST.md`](RELEASE_CHECKLIST.md)
calls for "verified live." Each step lists what to do and what "pass"
looks like; a failure at any step means the deploy is not verified, per
this project's own established practice (a passing health check is
necessary, not sufficient — [`DEPLOYMENT_GUIDE.md`](DEPLOYMENT_GUIDE.md)
§3.4).

Use the seeded demo users (`orgadmin`/`analyst`/`auditor`/`viewer`,
password `demo12345` — see [`DEPLOYMENT_GUIDE.md`](DEPLOYMENT_GUIDE.md)
§2.1) unless testing against a real tenant.

---

## 1. Health endpoints (do this first)

| Check | Expected |
|---|---|
| `GET /healthz` | `200`, `{"status": "ok", "database": "ok"}` |
| `GET /healthz/worker/` | `200`, `{"status": "ok", "workers": [...]}` — a non-empty worker list proves a real Celery worker is consuming from the broker, not just that the web service deployed |
| `GET /healthz/ai/` | `200` regardless of `AI_ENABLED` — `{"ai_enabled": false, ...}` is a *healthy* result when AI is off (the default), not a failure |

If any of these fail, stop — nothing downstream will work correctly and
there's no point continuing the checklist.

## 2. Login

1. Open the frontend URL. Log in as `orgadmin` / `demo12345`.
2. **Pass**: redirected to the dashboard, JWT stored, user's org/role
   visible in the UI (e.g. nav or profile area).
3. Log out, then attempt an authenticated API call (e.g. reload the
   dashboard) — **pass**: redirected back to login, not a broken/blank
   page.
4. Attempt a login with a wrong password. **Pass**: a generic, non-
   enumerating error ("No active account found...") — never "user not
   found" vs. "wrong password" as distinct messages.

## 3. Upload

1. Log in as `analyst`. Navigate to Upload.
2. Upload a small, valid file for one of the three adapters (SAP Fuel
   CSV / Utility Electricity CSV / Corporate Travel JSON).
3. **Pass**: batch appears with status progressing (`QUEUED` →
   `PROCESSING` → `COMPLETED`/`PARTIALLY_COMPLETED`/`FAILED`), matching
   the row count of the uploaded file.
4. Upload a file with at least one deliberately bad row (e.g. a negative
   quantity). **Pass**: that row is `FAILED`, the rest of the batch still
   processes — one bad row never aborts the whole upload.
5. Upload a file with a disallowed extension (e.g. `.exe` renamed to
   `.csv`). **Pass**: rejected server-side, not just by a frontend file
   picker filter (Phase 7.5 H4-4).

## 4. Calculation

1. After an upload completes, check the Records page for the new batch's
   records.
2. **Pass**: each successfully-ingested record has a computed CO₂e value,
   the emission factor used, and an explainability trace available (via
   the record detail drawer).
3. **Pass**: a record left `UNRESOLVED_NO_FACTOR` (no matching emission
   factor) is visibly distinguishable in the UI, not silently dropped.

## 5. Approval workflow

1. Log in as `analyst`. Open a `Draft` record, `Submit` it.
2. **Pass**: status becomes `Submitted`; an `AuditTrail` entry and an
   `EmissionRecordVersion` snapshot were created (spot-check via the
   record's audit history in the UI, or `GET /api/audit/verify/`).
3. Log in as `orgadmin` (or another role with approval rights). `Approve`
   the submitted record with a reason.
4. **Pass**: status becomes `Approved`; attempting to edit a business
   field on it afterward is blocked (the UI should not even offer an
   edit path for an approved record's core fields).
5. Repeat with `Reject` on a different record — **pass**: status becomes
   `Rejected`, reason is recorded and visible.
6. Soft-delete a record, then view it via "Show deleted records" — **pass**:
   hidden from the default view, visible and clearly marked in the
   deleted view, and still present in compliance reports (§8) if it was
   `Approved`.

## 6. AI capabilities

*Skip this section entirely if `AI_ENABLED=False` in this environment —
that's the correct, healthy default, not a gap.*

1. Upload a batch containing a record that will be flagged suspicious
   (e.g. an anomalously high value). **Pass**: an "AI Insights" panel
   entry appears on that record within a reasonable time (async,
   dispatched from `ingest_task`'s success path) with an explanation,
   confidence, and an "AI Advisory" label — never presented as a
   deterministic fact.
2. Trigger a record left `UNRESOLVED_NO_FACTOR` — **pass**: an AI factor
   recommendation appears (candidate label, confidence), clearly advisory.
3. Open the ESG Assistant page, ask a question about the org's emissions
   data. **Pass**: a labeled "AI Advisory" answer with citations/retrieved
   context, or a clear "assistant unavailable" message — never a silent
   failure.
4. Generate report narration for a compliance report period. **Pass**:
   narration appears, labeled advisory, built only from `Approved` data.
5. As Platform Admin, check `GET /api/ai/ops/observability/` and
   `GET /api/ai/ops/health/`. **Pass**: real request/latency/cost/
   evaluation figures, not zeros/errors (unless genuinely no AI traffic
   has occurred yet).

## 7. Dashboards

1. Log in as each of the four roles in turn. **Pass**: dashboard content
   is role-appropriate (e.g. Viewer sees no Upload action; Platform Admin
   sees cross-tenant widgets Org Admin doesn't).
2. **Pass**: KPI cards, trend charts, and breakdown charts all render with
   real data (not stuck on loading skeletons or showing an error state).
3. Switch between organizations (if logged in as a multi-org user or
   Platform Admin with `X-Organization-ID`). **Pass**: data changes to
   match the selected org — no cross-tenant data leakage.

## 8. Reports

1. As `orgadmin` or `auditor`, generate a compliance report (CSV and
   JSON) for a period containing approved records.
2. **Pass**: report contains only `Approved` records, embeds a
   `verify_chain()` snapshot, and — if AI is enabled — includes narration.
3. **Pass**: CSV export does not contain a raw formula-injection payload
   even if a record's `file_name`/free-text fields contain one (e.g. a
   value starting with `=` should appear prefixed/neutralized, not as a
   live formula when opened in Excel/Sheets — Phase 6f).

## 9. Governance

1. `GET /api/audit/verify/` (or the equivalent admin action). **Pass**:
   `valid: true` for an org with no tampering.
2. Open a record's version history. **Pass**: every meaningful edit has a
   corresponding immutable snapshot, in order.
3. Attempt an illegal workflow transition (e.g. `Approved` → `Submitted`
   directly, bypassing the state machine) via the API. **Pass**: rejected
   with a clear error, not a silent no-op or a server error.

## 10. Observability

1. Tail the API service's logs (Render logs, or `docker compose logs -f
   api`). **Pass**: gunicorn access log lines appear for every request
   (`rid=<id>` present), and application log lines show the same
   `[request_id]` for a given request's worth of activity (Phase 9b).
2. Tail the worker's logs during an upload (§3 above). **Pass**: log
   lines show `workflow_id`/`batch_id` correlating `ingest_task` and
   `calculate_task` for that one upload (Phase 9b,
   [`OPERATIONS_RUNBOOK.md`](OPERATIONS_RUNBOOK.md) §1.3).
3. Trigger a deliberate failure (e.g. stop the worker process, then
   upload) — **pass**: `/healthz/worker/` immediately reflects `503`, and
   the batch does not silently hang forever (verify against
   `STALE_BATCH_THRESHOLD_MINUTES` if waiting that long is practical, or
   just confirm the health check catches it).

---

## Sign-off

Record the date, environment (Render URL / local Docker Compose), tester,
and pass/fail per section. A deploy is not considered production-verified
until every section above has a documented pass — see
[`RELEASE_CHECKLIST.md`](RELEASE_CHECKLIST.md) §17 for how this fits into
the overall release decision.
