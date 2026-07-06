# Scheduled Tasks and Celery Beat (`SCHEDULED_TASKS.md`)

Phase 5f — Celery Beat scheduling, periodic maintenance tasks, and the
production-safety considerations around running a scheduler alongside the
existing worker/queue architecture from 5a-5e. Builds directly on 5d's queue
routing and 5e's retry/DLQ machinery — none of that was redesigned, only
extended with a fourth queue and four new periodic tasks.

---

## 0. Pre-implementation review — how scheduled tasks integrate with 5a-5e

**Queue routing.** Periodic tasks get their own `maintenance` queue
(`config/settings.py` `CELERY_TASK_ROUTES`), for the same reason ingestion
and calculation got their own queues in Phase 5d: a burst of scheduled sweep
work must never compete with or delay time-sensitive, user-facing ingestion/
calculation processing. The existing worker service's `-Q` list was extended
to include it (`celery,ingestion,calculation,maintenance`) rather than
standing up a dedicated worker — exactly the "one pool consumes everything
today" pattern already established for the other two queues; a future
deployment can split `maintenance` onto its own worker with zero code
change, the same way `calculation` already can.

**`workflow_id` and the retry/backoff policy (5e) don't apply the same
way.** `workflow_id` correlates one batch's multi-stage processing history;
scheduled maintenance tasks aren't per-tenant-job work tied to a single
`UploadBatch` — they're system-level sweeps with no chain and nothing to
correlate across hops. None of the four tasks below carry a Celery
`autoretry_for` policy either, and this is a deliberate choice, not an
oversight: every one is an idempotent, self-healing sweep over a *set* of
rows, not a one-shot operation with a payload that would be lost if not
retried. If a run fails outright (logged normally — see §3), the next
scheduled invocation, minutes or hours later, simply finds the same
(or more) work waiting and does it then. Retrying a periodic sweep within
seconds gains nothing a periodic sweep doesn't already provide for free.

**Idempotency without a distributed lock.** Every task's actual DB
operation is a single atomic conditional `UPDATE` or `DELETE`
(`.exclude(...).filter(...).update(...)` / `.filter(...).delete()`) — once a
row is touched, it no longer matches the `WHERE` clause. Two overlapping
invocations of the same task (e.g. Beat catching up after being down, or a
slow run still finishing when the next scheduled one fires) can run
concurrently with no risk of double-processing or corruption, and no
`SELECT ... FOR UPDATE` or Redis lock is needed.

**Architectural consideration: Beat must be single-instance.** Unlike the
`worker` service (`docker compose up --scale worker=3` is a real, documented
capability — see `docs/JOB_LIFECYCLE.md` §0), Celery Beat is **not**
horizontally scalable: two Beat processes would each independently fire
every scheduled task on its own timer, double-dispatching everything. This
is why Beat is its own dedicated `beat` service in `docker-compose.yml`
rather than bundled into the worker via `celery worker -B` — bundling it
into a service that's meant to scale would silently break the moment
someone scaled it.

---

## 1. Design decision: static schedule vs. `django-celery-beat`

Two reasonable options existed for where the schedule itself lives:

| | Static, code-defined (chosen) | `django-celery-beat` (DB-backed) |
|---|---|---|
| Storage | `CELERY_BEAT_SCHEDULE` dict in `config/settings.py`, Celery's built-in file-based `PersistentScheduler` | New package + migrations + a `PeriodicTask` admin model |
| Changing a schedule | Requires a code change + deploy | Runtime edit via Django admin, no deploy |
| Auditability | Every change goes through code review and git history | An unaudited runtime surface — any staff user could silently change *when* maintenance/compliance-adjacent tasks run, with no review trail |
| New dependencies | None | `django-celery-beat` + its own migrations |
| Fit for this product | This is an ESG compliance platform where `AuditTrail` exists specifically to make changes reviewable. An unreviewed schedule-editing surface is a real regression here, not just "less flexible". | Dynamic scheduling is valuable when non-engineers need to tune schedules often — not the case for internal maintenance sweeps. |

**Chosen: static, code-defined schedule.** No new dependency, no new DB
surface, and every schedule change is reviewable exactly like everything
else in this codebase.

---

## 2. The four scheduled tasks

| Beat entry | Task | Schedule | Why this cadence |
|---|---|---|---|
| `cleanup-stale-batches` | `apps.ingestion.tasks.cleanup_stale_batches_task` | every 15 minutes | Frequent enough to catch a genuinely stuck batch the same day; the 30-minute staleness threshold (below) already provides the real safety margin, so the *check* interval itself can be short and cheap. |
| `recalculate-missing-calculations` | `apps.carbon.tasks.recalculate_missing_calculations_task` | daily, 03:30 UTC | A safety net, not a hot path — in steady state it should find nothing (every record is calculated synchronously at upload time). Scheduled off-peak and staggered 30 minutes after the DLQ cleanup below so the two don't contend for the same worker slot at the exact same instant. |
| `cleanup-old-failed-task-logs` | `apps.tasks.tasks.cleanup_old_failed_task_logs_task` | daily, 03:00 UTC | Pure housekeeping (audit-log retention) — daily is more than sufficient; off-peak avoids any contention with real traffic. |
| `celery-heartbeat` | `apps.core.tasks.heartbeat_task` | every 1 minute | Needs to be frequent relative to the cache TTL (180s = 3x the interval) so a health check reading it is never more than ~1-2 minutes stale during normal operation, while still tolerating one missed beat without flapping to "stale". |

### 2.1 `cleanup_stale_batches_task` — the backstop for 5e's known double-failure gap

Phase 5e's live Docker Compose verification (`docs/RETRY_DLQ.md` §4.3) found
that if a DB outage outlasts a task's *entire* retry budget, the Dead Letter
Queue's own fallback write can fail for the same reason — leaving a batch
non-terminal with no error message, documented then as "will remain
non-terminal until manually investigated." This task is that investigation,
automated.

Two independent sweeps, matching `UploadBatch`'s two independent status axes
(`docs/JOB_LIFECYCLE.md` §0):

```python
# Ingestion itself never finished
UploadBatch.objects.exclude(status__in=TERMINAL_STATUSES) \
    .filter(updated_at__lt=cutoff) \
    .update(status=FAILED, error_message=..., finished_at=now())

# Ingestion finished, but calculation never got to run/finish
UploadBatch.objects.filter(status__in=(COMPLETED, PARTIALLY_COMPLETED)) \
    .exclude(calculation_status__in=CALCULATION_TERMINAL_STATUSES) \
    .filter(updated_at__lt=cutoff) \
    .update(calculation_status=CALCULATION_FAILED, error_message=..., finished_at=now())
```

**Why 30 minutes (`STALE_BATCH_THRESHOLD_MINUTES`, env-configurable).** Every
real ingestion/calculation in this system completes in low-single-digit
seconds, even across a *full* retry budget (~14s worst case for
`ingest_task`, ~62s for `calculate_task` — see `docs/RETRY_DLQ.md`). 30
minutes leaves an enormous margin against false-positively sweeping a batch
that's still genuinely, legitimately in flight, while still catching a
truly stuck job the same day rather than leaving it stuck indefinitely.

### 2.2 `recalculate_missing_calculations_task` — safety net, not a new engine

Deliberately delegates to the existing, already-tested
`backfill_calculations` management command's default (non-`--force`) mode
via `call_command(...)`, rather than duplicating its query/resolution logic:

```python
call_command("backfill_calculations", stdout=output)
```

`--force` is never passed — this task only fills in what's *missing*, it
never supersedes an existing calculation. That distinction matters for
`APPROVED` records specifically: `backfill_calculations`' default mode
computes a first-time calculation for an approved record with none (no
audit-lock violation — the record itself is never mutated), while `--force`
explicitly skips `APPROVED` records (recomputing an *existing* pinned
calculation would violate the audit lock). This task inherits that exact
distinction for free by reusing the command rather than reimplementing it.

### 2.3 `cleanup_old_failed_task_logs_task` — DLQ retention

`FailedTaskLog` (Phase 5e) has no foreign keys — `batch_id`/`workflow_id`
are plain `CharField`s, not relations — so deleting old rows can never
cascade into or corrupt any other table. These rows are audit/observability
records of *past* failures, not the failure's actual current state (already
fixed up on `UploadBatch` at the time of the original failure); purging old
ones is pure housekeeping. Default retention: 90 days
(`FAILED_TASK_LOG_RETENTION_DAYS`, env-configurable).

### 2.4 `heartbeat_task` — the passive health signal promised since Phase 5a

`apps/core/views.py`'s `healthz_worker` docstring has said since Phase 5a
that a "Beat-driven passive heartbeat" would complement its active
`inspect().ping()` check (which can hang under some broker-partition
conditions, even with its existing 2s timeout). This task fulfills that:
every minute, it writes `{"timestamp": ..., "worker_id": self.request.hostname}`
to cache under `tasks:heartbeat:last_seen`, TTL'd at
`CELERY_HEARTBEAT_TTL_SECONDS` (default 180s = 3x the schedule interval) so
a genuinely dead Beat/worker pair naturally reports "stale" rather than a
false "healthy" forever.

**Deliberately additive, not authoritative** — `healthz_worker`'s existing
pass/fail HTTP status logic (200 vs 503) is completely unchanged; the
heartbeat is reported as an extra `beat_heartbeat` field in the JSON payload
for operator/monitoring-dashboard context, never overriding the active
check's verdict. Extend, not redesign.

```json
{"status": "ok", "workers": ["celery@abc123"], "beat_heartbeat": {"status": "ok", "worker_id": "celery@abc123", "age_seconds": 12.4}}
```

---

## 3. Failure handling — reusing 5e's DLQ, not a parallel system

None of these four tasks carry a `bind=True`/`autoretry_for` policy (§0
explains why), but an unhandled exception in any of them still fires
Celery's `task_failure` signal exactly like `ingest_task`/`calculate_task`
would — `apps/tasks/signals.py`'s handler logs it to `FailedTaskLog`
unconditionally. The batch-status-fixup half of that handler is a no-op for
these tasks (they don't pass a `batch_id` kwarg — there's no single batch a
system-level sweep belongs to), but the audit-log half still gives full
visibility into a failed maintenance run without building a second,
parallel failure-tracking mechanism.

---

## 4. Production-safe configuration — `docker-compose.yml`

```yaml
beat:
  command: ["celery", "-A", "config", "beat", "--loglevel=info",
            "--schedule=/app/beat-data/celerybeat-schedule"]
  volumes:
    - beatdata:/app/beat-data
```

- **Single instance, by design** — see §0. Never `--scale beat=N>1`, never a
  second Beat service.
- **`--schedule` on a named volume** so the "last run time" bookkeeping
  (Celery's `PersistentScheduler`) survives container restarts/rebuilds —
  without it, every restart would immediately re-fire any task whose
  interval had already elapsed since the image was last built. Not a
  correctness requirement (every task here is idempotent and self-healing —
  a spurious extra run costs nothing), just avoids noise.
- **`Dockerfile`** pre-creates `/app/beat-data` (empty) before the
  `chown -R appuser:appuser /app` step, so a fresh named volume mounted
  there inherits correct ownership — otherwise Docker would seed the volume
  owned by root and the non-root `appuser` Beat runs as couldn't write its
  schedule file.
- **`RUN_MIGRATIONS=false`** on `beat`, same as `worker` — `api` remains the
  single migration/seed owner.

---

## 5. Testing

- `apps/ingestion/tests_maintenance.py` — `cleanup_stale_batches_task`:
  marks a stale non-terminal batch on each axis independently, leaves a
  recently-updated or already-terminal batch untouched, and is idempotent
  across two consecutive runs.
- `apps/tasks/tests_maintenance.py` — `cleanup_old_failed_task_logs_task`:
  deletes only rows past the retention window, no-ops when nothing
  qualifies, idempotent.
- `apps/carbon/tests/test_scheduled_recalculation.py` —
  `recalculate_missing_calculations_task`: computes a missing calculation,
  never supersedes an existing one, idempotent. Deliberately thin — the
  underlying calculation/resolution logic is already covered exhaustively by
  `test_backfill.py`.
- `apps/core/tests.py` (`BeatHeartbeatTests`, `CeleryBeatScheduleTests`) —
  `heartbeat_task` writes the expected cache payload; `healthz_worker`
  reports `beat_heartbeat` as `"stale"` when missing and `"ok"` with a fresh
  age after the task runs, on every response path including the earliest
  broker-not-configured return; `CELERY_BEAT_SCHEDULE`/`CELERY_TASK_ROUTES`
  contain all four expected entries (catches configuration drift).
