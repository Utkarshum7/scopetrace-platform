# Backup, Disaster Recovery, Incident Response & Troubleshooting (`INCIDENT_RESPONSE.md`)

Phase 5k — the "something is wrong, what do I do" document. Every command
below was actually run against a real `docker compose` Postgres container
during this milestone, not copied from memory.

---

## 1. Backup & Recovery Procedures

### 1.1 Database backup

```bash
# Custom-format dump (compressed, supports selective/parallel restore)
docker compose exec -T db pg_dump -U scopetrace -d scopetrace -F c -f /tmp/backup.dump
docker compose cp db:/tmp/backup.dump ./backup-$(date +%Y%m%d-%H%M%S).dump
```

Verified this session: the dump completes, and a `pg_restore --list` against
it correctly enumerates all TOC entries.

### 1.2 Database restore

```bash
# Into a fresh/empty database (adjust target db name as needed)
docker compose cp ./backup-YYYYMMDD-HHMMSS.dump db:/tmp/restore.dump
docker compose exec -T db psql -U scopetrace -d scopetrace -c "CREATE DATABASE scopetrace_restored;"
docker compose exec -T db pg_restore -U scopetrace -d scopetrace_restored /tmp/restore.dump
```

Verified this session end-to-end: dumped the live Compose database, copied
the dump out to the host and back in, restored into a fresh database, and
confirmed row counts matched via a direct query
(`SELECT count(*) FROM ingestion_uploadbatch;`).

**To restore in place** (replacing the current database, not a side-by-side
copy): stop everything that connects to it first (`docker compose stop api
worker beat`), drop and recreate the target database, restore, then
restart. This is destructive — never do this without a fresh backup of the
*current* state taken first, even if that current state is the one you're
trying to fix.

### 1.3 What is NOT currently backed up (know before you need it)

- **Durable upload storage** (MinIO locally / S3-compatible in production) —
  no backup procedure exists in this repo today. Production S3/R2/B2
  typically offer their own versioning/replication features; verify and
  enable them at the provider level — this is provider configuration, not
  application code.
- **Beat's schedule bookkeeping** (`beatdata` volume) — losing it is not a
  correctness risk (every scheduled task is idempotent/self-healing per
  [`SCHEDULED_TASKS.md`](SCHEDULED_TASKS.md)), only a minor "might re-fire
  a task sooner than its normal interval once" inconvenience. Not worth a
  dedicated backup procedure.
- **Redis** — purely broker/cache/ephemeral-state (queued task messages,
  the Django cache, the Beat heartbeat key). Losing it loses in-flight
  queued tasks (anything not yet consumed) but not any durable business
  data (`UploadBatch`/`EmissionRecord`/`EmissionCalculation` all live in
  Postgres). A Redis loss during active processing would leave some
  batches stuck `QUEUED` — the stale-batch sweep (§3.2 of
  [`OPERATIONS_RUNBOOK.md`](OPERATIONS_RUNBOOK.md)) is the backstop, though
  it only fixes the *status*, not the fact that the file was never actually
  processed — see §3 below for the real remediation.

---

## 2. Disaster Recovery Guide

**Scope**: total loss of the database (or the whole environment). Recovery
Point Objective (RPO) and Recovery Time Objective (RTO) are **not
formally defined anywhere in this project today** — stated here honestly
rather than inventing numbers; see [`ROADMAP.md`](ROADMAP.md) for this as a
known gap. In the absence of a defined RPO, treat "as often as you can
tolerate re-processing everything since the last backup" as the working
assumption.

### 2.1 Recovery sequence

1. Provision a fresh Postgres instance (managed service, or a new `db`
   container with an empty volume).
2. Restore the most recent backup (§1.2).
3. Point `DATABASE_URL` at the restored instance.
4. Bring up `api` first, confirm `/healthz` is `200` (proves DB
   connectivity) before starting `worker`/`beat`.
5. Bring up `worker`/`beat`. Confirm `/healthz/worker/` is `200`.
6. Any uploads that were mid-processing at the time of the failure and
   aren't reflected in the restored backup: their `UploadBatch` rows won't
   exist at all (the batch itself is gone, not just stuck) — the affected
   users will need to re-upload. There is no "replay from storage" tooling
   today (the durably-stored file in S3/MinIO still exists, but nothing
   automatically re-associates it with a fresh batch) — a manual
   re-upload is the current recovery path.
7. Verify with a real end-to-end smoke test (upload → confirm
   `COMPLETED`/`CALCULATED`), not just health checks — this project's own
   practice throughout Phase 5 (every milestone's live verification did a
   real upload, not just a health-check poke).

### 2.2 What would need to change for a lower RPO/RTO

Point-in-time recovery (Postgres WAL archiving / a managed provider's PITR
feature) is not configured anywhere in this repo — it would need to be set
up at the database-provider level (Render's paid Postgres plans, or an
external managed Postgres, typically offer this). Flagged as a Production
Readiness Review recommendation, not built speculatively now.

---

## 3. Incident Response Guide

### 3.1 Severity levels (proposed — not currently formalized elsewhere)

| Severity | Definition | Example |
|---|---|---|
| **SEV-1** | Total outage or data-integrity risk | API down entirely; database unreachable; a bug that could silently corrupt `EmissionCalculation`/`AuditTrail` data |
| **SEV-2** | Core feature broken, workaround exists or blast radius contained | Async pipeline stuck (uploads accepted but never processed); one queue stranded |
| **SEV-3** | Degraded but functional | Email notifications not sending (console-backend fallback, or SMTP down); Flower unreachable |
| **SEV-4** | Cosmetic / non-blocking | A misleading log message; a stale doc |

### 3.2 First response (any severity)

1. `curl /healthz` and `curl /healthz/worker/` — establishes which tier is
   actually affected (web vs. async) in under a second.
2. `docker compose ps` (or the equivalent for your environment) — which
   service(s) are unhealthy/restarting.
3. `docker compose logs --tail 100 <service>` for anything unhealthy.
4. Classify severity (§3.1), and follow §4 (Troubleshooting) for the
   specific symptom.

### 3.3 Communication

Not formalized in this repo (no on-call rotation, status page, or paging
integration exists) — a real team adopting this project would define these
separately. What already exists that *supports* incident communication:
`workflow_id` for correlating a specific user-reported issue to exact log
lines (§1 of [`OPERATIONS_RUNBOOK.md`](OPERATIONS_RUNBOOK.md)), and the
`FailedTaskLog` admin view as a ready-made "what has actually failed
recently" dashboard without needing log access.

### 3.4 Postmortem

No template exists in this repo. Recommended minimum: what broke, what was
the actual root cause (not just the symptom), what detected it (or *should*
have — did a health check fail silently, did nothing alert at all), and one
concrete follow-up action. This project's own git history is a reasonable
model for the *level of detail* worth capturing — e.g. the Phase 5e DLQ
double-failure discovery and Phase 5i's Postgres-vs-SQLite test bug were
both root-caused and documented in their respective design docs, not just
patched silently.

---

## 4. Troubleshooting Guide

| Symptom | Likely cause | Check | Fix |
|---|---|---|---|
| API returns 503 at `/healthz` | DB unreachable | `docker compose exec db pg_isready -U scopetrace -d scopetrace` | Restart `db`; check credentials/`DATABASE_URL`; see §9.3 of `OPERATIONS_RUNBOOK.md` |
| API returns 503 at `/healthz/worker/`, `detail: "CELERY_BROKER_URL is not configured"` | `REDIS_URL`/`CELERY_BROKER_URL` unset | `echo $REDIS_URL` in the `api` container | Set `REDIS_URL` |
| `/healthz/worker/` 503, `detail: "broker unreachable"` | Redis down | `docker compose exec redis redis-cli ping` | Restart `redis` |
| `/healthz/worker/` 503, `detail: "no workers responded"` | No worker process running (or it crashed) | `docker compose ps worker`; `docker compose logs worker` | `docker compose up -d worker`; investigate the crash in logs |
| Django boots with `ImproperlyConfigured: STORAGE_BACKEND must be 's3'` | `DEBUG=False` without `STORAGE_BACKEND=s3` + credentials set | Env vars | Set the five `AWS_*` variables — see [`DEPLOYMENT_GUIDE.md`](DEPLOYMENT_GUIDE.md) §3.3/§4.5 |
| Uploads accepted (`202`) but never leave `QUEUED` | Worker not consuming the right queue, or down | §9.1 of `OPERATIONS_RUNBOOK.md` | Same |
| A batch is `FAILED` with no clear reason | Check `error_message` on the batch first, then `FailedTaskLog` | `GET /api/batches/{id}/`; Django Admin → Failed Task (Dead Letter) | See §3 of `OPERATIONS_RUNBOOK.md` |
| Duplicate notification email for one batch | Rare, accepted trade-off — a worker crash between `send_mail()` succeeding and Celery acking the message causes at-least-once redelivery | Check worker logs around that timestamp for a crash/restart | Not a bug to "fix" — see [`NOTIFICATIONS.md`](NOTIFICATIONS.md)'s accepted-limitation note |
| `docker build` includes unexpectedly large/unwanted files | `.dockerignore` missing a new dev artifact | `docker run --rm --entrypoint sh <image> -c "ls /app"` | Add to `.dockerignore`; the multi-stage `Dockerfile`'s explicit COPY allow-list is the structural backstop — see [`DOCKER.md`](DOCKER.md) |
| Metrics API numbers look different between local dev and CI/production | SQLite (local) vs. Postgres (CI/prod) return different string precision for `Sum()`-aggregated `Decimal` fields | Compare as `Decimal`, not raw string | Already fixed in this project's own test suite (Phase 5i) — if you hit this in new code, compare Decimals, don't string-match |
| Flower unreachable | It's `profiles: ["monitoring"]` — never starts by default | `docker compose ps` | `docker compose --profile monitoring up -d flower` |
