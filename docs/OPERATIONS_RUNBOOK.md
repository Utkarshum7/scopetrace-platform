# Operations Runbook (`OPERATIONS_RUNBOOK.md`)

Phase 5k — day-2 operations: running and scaling Celery, reading queue/DLQ
state, using Flower, health checks, common tasks, and step-by-step
playbooks for specific operational scenarios. See
[`ARCHITECTURE_OVERVIEW.md`](ARCHITECTURE_OVERVIEW.md) for the system map
this document assumes.

---

## 1. Celery Worker Operations

### 1.1 Starting / scaling

```bash
docker compose up -d worker                 # single worker
docker compose up --scale worker=3 -d       # 3 replicas, zero code change
docker compose restart worker                # restart (e.g. after a config change)
```

`acks_late=True` + `prefetch_multiplier=1` (set since Phase 5a) mean: a
task is only acknowledged after it *completes*, so a worker that crashes
mid-task gets its in-flight task redelivered rather than losing it, and
each worker replica only prefetches one message at a time — adding
replicas distributes load evenly instead of one worker hoarding a queue's
worth of work. This is why every task in this codebase is designed to be
safe to re-run if redelivered (checked via `TERMINAL_STATUSES`/
`CALCULATION_TERMINAL_STATUSES` guards at the top of `ingest_task`/
`calculate_task`).

### 1.2 Inspecting a running worker

```bash
docker compose exec worker celery -A config inspect active      # currently-executing tasks
docker compose exec worker celery -A config inspect reserved    # prefetched, not yet started
docker compose exec worker celery -A config inspect stats       # pool size, totals
docker compose exec worker celery -A config inspect ping        # liveness (also what /healthz/worker/ uses)
```

Or use Flower's UI for the same information without a shell (§5).

### 1.3 Logs

```bash
docker compose logs -f worker
docker compose logs -f beat
```

Every task logs `workflow_id`/`batch_id` and an attempt label
(`"initial attempt"` vs `"retry attempt N/max"`) — `grep <workflow_id>`
across worker logs reconstructs one upload's entire multi-attempt history
in order, regardless of which task emitted which line. See
[`RETRY_DLQ.md`](RETRY_DLQ.md) §3.

---

## 2. Queue Architecture

Five queues, one worker pool consuming all of them today (a routing seam,
not a capacity split yet) — full diagram and per-task mapping:
[`ARCHITECTURE_OVERVIEW.md`](ARCHITECTURE_OVERVIEW.md) §3.

**Splitting a queue onto its own worker pool** (e.g. once `calculation`
needs dedicated capacity): add a service to `docker-compose.yml` running
`celery -A config worker -Q calculation` and nothing else — no code or
settings change required, since `CELERY_TASK_ROUTES` already routes
`calculate_task` there.

---

## 3. Retry & Dead Letter Queue — operational guide

Design and rationale: [`RETRY_DLQ.md`](RETRY_DLQ.md). This section is the
"what do I actually do about it" companion.

### 3.1 Reading the Dead Letter Queue

Every permanently-failed task (retries exhausted, or a non-retryable
exception) is logged to `FailedTaskLog` — **Django Admin → Task
Observability → Failed Task (Dead Letter)**, read-only, filterable by
`task_name`/`exception_type`, searchable by `batch_id`/`workflow_id`/
`task_id`/`exception_message`.

```python
# python manage.py shell
from apps.tasks.models import FailedTaskLog
FailedTaskLog.objects.filter(task_name="apps.ingestion.tasks.ingest_task").order_by("-created_at")[:20]
FailedTaskLog.objects.filter(workflow_id="<workflow_id from a user report>")
```

Rows older than `FAILED_TASK_LOG_RETENTION_DAYS` (default 90) are purged
daily by `cleanup_old_failed_task_logs_task` — export/archive anything you
need to keep long-term before then.

### 3.2 A batch stuck non-terminal with no DLQ entry

The one documented gap (see [`RETRY_DLQ.md`](RETRY_DLQ.md) §4.3): if the
database outage that exhausted a task's retries is *still ongoing* when the
DLQ handler tries to log it, the DLQ write itself can fail too (falls back
to a `CRITICAL` log line — `grep "DEAD LETTER.*FAILED" worker logs`).
`cleanup_stale_batches_task` (every 15 min, `STALE_BATCH_THRESHOLD_MINUTES`
= 30 min default) is the automatic backstop for exactly this — it will
catch and terminally mark the batch on its next run. If you need it fixed
sooner than that:

```python
from apps.ingestion.tasks import cleanup_stale_batches_task
cleanup_stale_batches_task()   # safe to run manually any time — idempotent
```

### 3.3 Manually reprocessing a batch

There is currently **no** built-in "retry this batch" admin action —
retries are automatic (Celery's `autoretry_for`) up to each task's
`max_retries`; once exhausted, the batch is terminal by design and won't be
picked up again by `ingest_task`/`calculate_task` (the `TERMINAL_STATUSES`
guard exists specifically to prevent redelivery from reprocessing a
finished batch). To force a genuine reprocess after fixing the underlying
cause:

```python
# python manage.py shell — resets ONLY the axis that failed, so the other
# axis's already-good work (e.g. successful ingestion) isn't redone.
from apps.ingestion.models import UploadBatch
batch = UploadBatch.objects.get(pk="<batch-id>")

# If ingestion failed:
batch.status = UploadBatch.BatchStatus.PENDING
batch.error_message = None
batch.save(update_fields=["status", "error_message"])
from apps.ingestion.tasks import ingest_task
ingest_task.delay(batch_id=str(batch.id), storage_key=f"uploads/{batch.organization_id}/{batch.id}/{batch.file_name}", workflow_id=str(batch.workflow_id))

# If only calculation failed (ingestion succeeded):
batch.calculation_status = UploadBatch.CalculationStatus.NOT_STARTED
batch.save(update_fields=["calculation_status"])
from apps.carbon.tasks import calculate_task
calculate_task.delay(batch_id=str(batch.id), workflow_id=str(batch.workflow_id))
```

This is a manual, deliberate action (not a self-service admin button) —
treat it the same way as any other direct-DB-state change: understand why
the batch failed first (§3.1), don't just re-run it hoping the transient
issue resolved itself without checking.

### 3.4 Missing calculations safety net

`recalculate_missing_calculations_task` (daily, 03:30 UTC) catches any
`EmissionRecord` with no current `EmissionCalculation` — normally zero
matches (everything calculates synchronously in the chain). To run it
on-demand:

```bash
docker compose exec api python manage.py backfill_calculations
```

---

## 4. Health Check Endpoints

| Endpoint | Checks | Healthy response | Unhealthy response |
|---|---|---|---|
| `GET /healthz` | DB reachability (`SELECT 1`) | `200 {"status": "ok", "database": "ok"}` | `503 {"status": "unhealthy", "database": "unreachable", "detail": "<exception>"}` |
| `GET /healthz/worker/` | Real `celery inspect ping` control-plane round trip + passive Beat heartbeat freshness | `200 {"status": "ok", "workers": ["celery@<host>"], "beat_heartbeat": {"status": "ok", "worker_id": "...", "age_seconds": N}}` | `503` — three distinct causes, each with an actionable `detail`: broker not configured, broker unreachable, or broker reachable but zero workers responded |
| `GET /healthz/ai/` | `AI_ENABLED` state + provider construction check (config/credentials only, no network call) + AI heartbeat freshness | `200 {"status": "ok", "ai_enabled": true\|false, "provider": "...", "ai_heartbeat": {...}}` — `ai_enabled: false` is a healthy 200, not a failure | `503 {"status": "unhealthy", "detail": "provider misconfigured: ..."}` when enabled but misconfigured |

`beat_heartbeat` is additive context on every response (including the
earliest broker-not-configured failure) — it never changes the endpoint's
own pass/fail status, which is driven solely by the active `inspect().ping()`
check. `{"status": "stale"}` means no worker has run `heartbeat_task` in
over `CELERY_HEARTBEAT_TTL_SECONDS` (180s default) — either Beat is down,
every worker is down, or this deployment simply has no Beat/worker running
at all (e.g. local `manage.py runserver` alone).

```bash
curl -s http://localhost:8000/healthz | python -m json.tool
curl -s http://localhost:8000/healthz/worker/ | python -m json.tool
```

---

## 5. Monitoring & Flower Guide

Design: [`FLOWER.md`](FLOWER.md). Operational usage:

```bash
docker compose --profile monitoring up -d flower
# → http://localhost:5555, basic auth scopetrace/scopetrace123 by default
```

Useful views: **Workers** tab (per-worker active/processed task counts,
pool size), **Tasks** tab (searchable history — filter by task name or
state to find e.g. every `FAILURE` for `calculate_task`), **Broker** tab
(per-queue message counts — a growing `ingestion` queue with no matching
drop in `worker` activity means either the worker is down or genuinely
falling behind). Flower's own REST API (`/api/workers`, `/api/tasks`,
`/api/queues/length`) is scriptable for lightweight external monitoring
without a UI.

**Never** run this in production without changing the default
`FLOWER_USER`/`FLOWER_PASSWORD`, and never expose port 5555 publicly — it's
an operator tool, gated behind the `monitoring` Compose profile specifically
so it's opt-in per environment.

---

## 5a. AI Operations (Phase 7g)

Three authenticated, read-only endpoints (`apps.ai.ops_views`) go beyond
`/healthz/ai/`'s pass/fail signal with real operational detail. All build
entirely from `AIInteraction`/`EvaluationRun`/`EvaluationResult` rows the
platform already writes — see [`AI_ARCHITECTURE.md`](AI_ARCHITECTURE.md)
§19 and ADR 0014.

| Endpoint | RBAC | Reports |
|---|---|---|
| `GET /api/ai/ops/observability/` | Platform Admin | requests/failures, latency (+ daily trend), provider usage, replay usage, token usage, estimated cost, cache hits, evaluation health |
| `GET /api/ai/ops/health/` | Platform Admin | AI provider status, AI heartbeat, `ai` queue depth (real Redis `LLEN`, not just worker liveness), evaluation health, replay provider health (construction + on-disk fixture count) |
| `GET /api/ai/costs/` | Org Admin / Auditor | token consumption, estimated spend, monthly budget utilization %, provider/capability distribution — scoped to the active organization only |

```bash
# Platform-wide (superuser JWT required)
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/ai/ops/observability/ | python -m json.tool
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/ai/ops/health/ | python -m json.tool

# Per-organization (Org Admin/Auditor JWT + X-Organization-ID if the user has multiple memberships)
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/ai/costs/ | python -m json.tool
```

**Reading `/api/ai/ops/health/`'s `queue_depth`:** `status: "ok"` with a
`depth` count is a real backlog size on the `ai` queue (redelivery-safe —
`LLEN` never consumes a message). A persistently growing depth with no
corresponding worker activity is the same "queue draining" investigation
as §9.1, scoped to the `ai` queue specifically:
`docker compose logs worker | grep -A5 '\[queues\]'` to confirm the
worker actually subscribes to `ai` (not just `celery,ingestion,
calculation,maintenance,notifications`).

**Reading evaluation health:** `regressions`/`schema_failures`/
`replay_failures` > 0 in `/api/ai/ops/observability/`'s `evaluation`
section means the last 10 CI evaluation runs had real failures — cross-
reference the failing `EvaluationRun.id` against CI logs for
`apps/ai/evaluation/tests_invariants.py` and the specific prompt/schema
version involved (see [`AI_EVALUATION.md`](AI_EVALUATION.md) §9).

---

## 6. Common Operational Tasks

**Add a new organization + data source (no admin UI action needed elsewhere):**
```bash
docker compose exec api python manage.py shell -c "
from apps.core.models import Organization, DataSource
org = Organization.objects.create(name='New Client Inc')
DataSource.objects.create(organization=org, name='SAP Feed', source_type=DataSource.SourceType.SAP_FUEL)
"
```
Or via Django Admin (`/admin/`) — same effect, no shell needed.

**Rotate `SECRET_KEY` / `DJANGO_SUPERUSER_PASSWORD` / storage credentials:**
update the environment variable in Render's dashboard (or `.env` locally)
and redeploy/restart — nothing in the app caches these beyond process
lifetime.

**Import a new emission factor dataset:**
```bash
docker compose exec api python manage.py import_emission_factors \
  --file factors.csv --publisher DEFRA --dataset-version 2026 \
  --region GB --valid-from 2026-01-01 --activate
```
Idempotent by `(publisher, version, checksum)` — re-running with the same
file is a safe no-op. `--dry-run` validates without persisting.

**Recompute CO₂e after a factor update:**
```bash
docker compose exec api python manage.py backfill_calculations --force
```
`--force` recalculates existing (non-`APPROVED`) records; `APPROVED`
records are frozen to their original factor version by design (audit lock)
— use the `/api/records/{id}/recalculate/` endpoint for an explicit,
audited re-baseline of an individual approved record instead.

**Check for pending model changes before a deploy:**
```bash
docker compose exec api python manage.py makemigrations --check --dry-run
```

**Tail structured logs for one upload across both pipeline stages:**
```bash
docker compose logs --no-color worker | grep "<workflow_id>"
```

---

## 7. Maintenance Checklist

**Weekly (or per your team's cadence):**
- [ ] Skim `FailedTaskLog` (Django Admin) for any recurring `exception_type` — a pattern (not a one-off) usually means a real bug, not just a transient blip.
- [ ] Check `pip-audit`/`npm audit` output on the latest CI run (advisory, not auto-surfaced elsewhere) for new actionable findings — see [`CI_CD.md`](CI_CD.md).
- [ ] Confirm `docker compose ps` shows all expected services healthy in whatever environment you operate.

**Monthly:**
- [ ] Verify a real backup restores cleanly (§ [`INCIDENT_RESPONSE.md`](INCIDENT_RESPONSE.md) §1) — an untested backup is not a backup.
- [ ] Review `STALE_BATCH_THRESHOLD_MINUTES`/`FAILED_TASK_LOG_RETENTION_DAYS` against actual observed volumes — defaults are conservative starting points, not permanent.
- [ ] Re-run `docker build` for both images locally to confirm they still build cleanly against current base-image tags (`python:3.12-slim`, `node:20-alpine`, `nginx:1.27-alpine` are not pinned to a digest — a base image update could in principle change behavior).

**Per release:** see [`DEPLOYMENT_GUIDE.md`](DEPLOYMENT_GUIDE.md) §6.

---

## 8. Scaling Guide

| Concern | How to scale | Notes |
|---|---|---|
| Ingestion/calculation throughput | `docker compose up --scale worker=N` (or add dedicated `-Q ingestion`/`-Q calculation` worker services) | Verified up to 3 replicas with zero code change; `acks_late`+`prefetch=1` already distribute load evenly. |
| API request throughput | Current baseline (Phase 7.5 H3): `--workers 2 --worker-class gthread --threads 4` = 8 concurrent request slots (was 2, `sync` worker class). To scale further: raise `--threads` (cheap, shares one process's memory — the right first lever for more I/O-bound headroom), raise `--workers` (each is a full process — costs proportionally more RAM, needed once request handling becomes CPU-bound), or scale the Render web service instance count | Stateless — no session affinity required (JWT, not server sessions). `CONN_MAX_AGE=600` means each thread holds its own persistent DB connection — watch total connection count (workers × threads, across every web instance + Celery) against the Postgres plan's connection limit as you scale either lever. |
| Database | Vertical scaling on the managed Postgres plan; read replicas are not wired up anywhere in the ORM layer today (would need explicit `using()` routing) | Not needed at current scale — no evidence of DB being a bottleneck. |
| Redis | Vertical scaling; also serves as the Django cache — a Redis outage affects cache AND Celery simultaneously | Single point of shared-fate today — see [`SECURITY.md`](SECURITY.md)/Production Readiness Review for the risk note. |
| Beat | **Cannot be scaled** — single instance only, always | See [`SCHEDULED_TASKS.md`](SCHEDULED_TASKS.md). |
| Storage (S3-compatible) | Scales with the provider (S3/R2/B2) — no app-level limit | |

---

## 9. Operational Runbooks (step-by-step)

### 9.1 "Uploads are stuck in QUEUED"

1. `curl http://localhost:8000/healthz/worker/` — if `503`, the worker
   isn't responding to `inspect().ping()`. Check `docker compose ps worker`
   / `docker compose logs worker` for a crash loop.
2. If `200` but the queue still isn't draining: `docker compose exec worker
   celery -A config inspect active` — is it actually processing anything?
   `celery -A config inspect reserved` — anything prefetched but stuck?
3. Check Redis itself: `docker compose exec redis redis-cli ping` — if this
   fails, the worker can't be blamed; fix Redis first.
4. If the worker is idle and the queue has messages, confirm queue naming
   matches: `docker compose logs worker | grep -A5 '\[queues\]'` — a `-Q`
   flag typo silently strands an entire queue forever (this exact class of
   bug was caught once already, this session, by explicitly checking this
   log line after every queue-routing change).

### 9.2 "A specific batch never progressed"

1. `GET /api/batches/{id}/` — read `status`/`calculation_status`/
   `error_message`/`workflow_id`.
2. `docker compose logs worker | grep <workflow_id>` — the full attempt
   history in order.
3. Check `FailedTaskLog` for that `batch_id` (§3.1) — was it dead-lettered?
4. If non-terminal and old: see §3.2 (the stale-sweep backstop) or force it
   with §3.3.

### 9.3 "Database looks unreachable"

1. `curl http://localhost:8000/healthz` — confirms it from the app's own
   point of view.
2. `docker compose exec db pg_isready -U scopetrace -d scopetrace`.
3. Check connection count / max_connections if this is a "too many
   connections" style failure — `conn_max_age=600` (persistent connections)
   is configured in `config/settings.py`; a burst of new worker replicas
   each holding connections open can exhaust a small Postgres plan's limit
   faster than expected.
4. If genuinely down: see [`INCIDENT_RESPONSE.md`](INCIDENT_RESPONSE.md).

### 9.4a "AI budget alert / an organization is over its monthly AI budget"

1. `GET /api/ai/costs/` (as an Org Admin/Auditor for that org, or check
   `apps.ai.services.cost_governance.org_cost_summary(org)` from a shell)
   — `budget.over_budget: true` confirms it, `budget.spent_usd` /
   `budget.budget_usd` give the exact figures.
2. This is a **soft** signal, not an enforcement mechanism — the gateway's
   own `check_budget()` (see `AI_ARCHITECTURE.md` §6) already refuses new
   calls once the budget is exceeded (`AI_DISABLED`-style outcome, not a
   hard crash); this endpoint is for visibility/reporting, not the
   enforcement point itself.
3. Adjust `TenantAIPolicy.monthly_budget_usd` for the organization (Org
   Admin, `CanManageAIPolicy`) if the budget itself needs raising, or
   investigate `provider_distribution`/`capability_distribution` in the
   same response to see which capability is driving spend.

### 9.4b "AI queue backlog is growing"

1. `GET /api/ai/ops/health/` (Platform Admin) — `queue_depth.depth` on the
   `ai` queue.
2. Follow §9.1's general stuck-queue investigation, scoped to `ai`
   specifically — confirm the worker's `-Q` flag includes it, confirm
   Redis is reachable, check for a crash loop in the specific AI task
   (`generate_anomaly_explanations_task`, `generate_factor_
   recommendations_task`, `generate_validation_assistance_task`,
   `generate_report_narration_task`, or `ai_heartbeat_task`).
3. A vendor outage (Anthropic/OpenAI down or rate-limiting) will also
   manifest as a growing `ai` queue depth with `PROVIDER_ERROR`-outcome
   `AIInteraction` rows piling up — check `/api/ai/ops/observability/`'s
   `requests.by_outcome` for a spike, not just the queue depth alone.

### 9.4 "I need to redeploy after a hotfix"

1. Run the full local verification in [`DEPLOYMENT_GUIDE.md`](DEPLOYMENT_GUIDE.md) §6.
2. Push the branch, confirm all three CI workflows pass.
3. For Docker Compose environments: `docker compose up --build -d` (rebuilds
   changed images only, restarts affected services).
4. For Render: push to the connected branch — `releaseCommand` re-runs
   migrations/seeds automatically (idempotent, safe).
5. Confirm `/healthz` and `/healthz/worker/` both `200` post-deploy.
