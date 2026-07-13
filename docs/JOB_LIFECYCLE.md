# Job Lifecycle (`JOB_LIFECYCLE.md`)

Phase 5c — `UploadBatch` as the source of truth for the async ingestion job's
lifecycle: state machine, progress tracking, the polling API, observability,
and error handling. Builds directly on Phase 5b (async upload processing via
Celery) and Phase 5a (Celery/Redis foundation).

Phase 5d — the single processing task split into a two-stage chain:
`apps.ingestion.tasks.ingest_task` (parse/validate/normalize/persist) then
`apps.carbon.tasks.calculate_task` (compute + persist CO₂e), chained via
Celery's `chain()`. See §0 below for the design, then §1 for how it changed
the state machine.

---

## 0. Chained orchestration (Phase 5d)

**Why a chain, not a group or chord.** `calculate_task` depends on
`ingest_task`'s output existing in the DB — a sequential dependency, not
parallel work, so `chain()` is the right primitive:

```python
chain(
    ingest_task.si(str(batch.id), storage_key, workflow_id),
    calculate_task.si(str(batch.id), workflow_id),
).delay()
```

`.si()` (immutable signatures), not `.s()` — `calculate_task` re-fetches
`EmissionRecord`s from the DB (via `activity_input_from_record()`, which
already existed for the synchronous `recalculate` action and
`backfill_calculations`) rather than receiving `ingest_task`'s return value
through Celery's result backend. A `group`/`chord` (fan-out + a completion
callback) would be the right primitive for a **future** large-file feature —
parallel chunk-ingestion with a chord callback finalizing the batch — but
there's no parallel work to exploit at today's scale (10MB file cap,
sub-second processing observed in every test), so building for it now would
be speculative.

**Two independent status axes.** `UploadBatch.status` reflects **ingestion**
outcome only (unchanged from Phase 5c — `PENDING → QUEUED → PROCESSING →
COMPLETED/PARTIALLY_COMPLETED/FAILED`). A new `UploadBatch.calculation_status`
(`NOT_STARTED → CALCULATING → CALCULATED/CALCULATION_FAILED`) tracks the
calculation stage separately — owned by
`CarbonCalculationService.calculate_for_batch()`, called by both
`calculate_task` and the synchronous `IngestionService.ingest()` convenience
path. Splitting these was necessary, not just tidy: before 5d, ingestion and
calculation shared one transaction, so a calculation crash rolled back
already-good ingested rows. After the split, `calculate_for_batch()` runs in
its **own** transaction — a calculation failure no longer touches durably-
committed ingestion data, which is the entire point of the split.

This falls out of the exception-handling shape that already existed:
`ingest_batch()`'s crash path already `raise`s, and Celery chains stop at the
first raised exception — so `calculate_task` automatically never runs for a
batch whose ingestion genuinely crashed, with no extra guard code needed.
`PARTIALLY_COMPLETED` is a normal return (not an exception), so the chain
correctly proceeds to calculation even when some rows failed validation —
there are still valid records to calculate for.

**`finished_at` ownership moved.** Before 5d, `ingest_batch()`'s success path
set `finished_at`. Now: `ingest_task`'s own `FAILED` path still sets it
(chain-terminating — nothing else will run), but on success, `finished_at` is
set by `calculate_for_batch()`'s completion instead — because ingestion
succeeding no longer means the whole job is done. `duration_seconds`
therefore now reports the full chain's wall-clock time, not just ingestion's.

**Two independent idempotency guards.** `calculate_task` needed its own
guard, mirroring `ingest_task`'s: `EmissionCalculation` has
`UniqueConstraint(fields=["emission_record"], condition=Q(is_current=True))`
— a redelivered `calculate_task` re-running `bulk_create()` after its first
attempt already committed would raise `IntegrityError`, not silently
duplicate. `calculate_task` checks `UploadBatch.CALCULATION_TERMINAL_STATUSES`
before doing any work, exactly as `ingest_task` checks `TERMINAL_STATUSES`.
Verified live against a real Docker Compose stack: manually redelivering
`calculate_task` for an already-`CALCULATED` batch returns `"skipped-
CALCULATED"` and the calculation count stays at 1.

**Workflow correlation ID — not just a Celery task ID.**
`UploadBatch.workflow_id` (set once at batch creation, a `CharField` not a
`UUIDField` so it's format-flexible enough to be adopted directly as an
OpenTelemetry trace ID later without a schema change) is threaded unchanged
through both task signatures. Each task has its own, *different* Celery task
ID (`self.request.id`) — `workflow_id` is what correlates log lines across
both stages regardless of which task emitted them. Verified live: both
`ingest_task` and `calculate_task`'s log lines for the same upload share the
identical `workflow_id`.

**`celery_task_id`, revisited.** `chain(...).delay()` returns the
`AsyncResult` for the **last** task (`calculate_task`), not the first — its
`.parent` is `ingest_task`'s real, already-queued result. Since the only
practical cancellation window is "before the task starts" (see §6),
`celery_task_id` is set to `result.parent.id` at enqueue — `ingest_task`'s
own id — and `calculate_task` overwrites it with its own id once the chain
reaches it, so the field always points at whichever task is currently active
or about to run.

**`pipeline_version`.** A new `UploadBatch.pipeline_version` field
(default `"1.0"`) labels the *shape* of the pipeline that processed a batch —
distinct from `EmissionCalculation.engine_version`, which versions the
calculation algorithm specifically. Not read by any branching logic yet;
pure preparation so a future pipeline restructuring (a third chain link,
chunked processing) can coexist with batches processed under the old shape
without a schema redesign.

**Queue routing.** `CELERY_TASK_ROUTES` (config/settings.py) routes
`ingest_task` to an `ingestion` queue and `calculate_task` to a `calculation`
queue — a seam, not yet a behavior change. The single worker service listens
on all three queue names (`celery,ingestion,calculation` via `-Q`), so one
pool still consumes everything today. This lets a future deployment dedicate
a worker pool to calculation specifically (e.g. once AI enrichment — Phase 7
— makes it meaningfully slower than ingestion) by adding a `-Q calculation`
worker service, with zero code change. Verified live: the worker's own
startup log lists all three queues, and a real upload flows through both
routed queues end-to-end.

---

## 1. State machine

```
(create) ──► PENDING ──► QUEUED ──► PROCESSING ──► COMPLETED
                  │                       │      └─► PARTIALLY_COMPLETED
                  │                       └─► FAILED
                  └─► FAILED (storage save failed before queuing)

QUEUED / PROCESSING ──► CANCELLED   (declared, NOT implemented this phase)
```

| Transition | Trigger |
|---|---|
| `(create)` → `PENDING` | `BaseUploadView.post()` creates the batch, before the file is durably staged |
| `PENDING` → `FAILED` | `StorageService.save()` raised — the upload never reaches the queue at all. `started_at` stays `None` (processing never began); `finished_at` is set. |
| `PENDING` → `QUEUED` | File saved durably, `chain(ingest_task, calculate_task).delay()` returned, and (checked via a DB re-read, not assumed) the chain has **not** already run — i.e. real async dispatch, sitting in the broker. `celery_task_id` is recorded here (`ingest_task`'s id — see §0). |
| `{PENDING,QUEUED,PROCESSING}` → `PROCESSING` | The task begins executing. **Not** gated on "incoming status == QUEUED" — see the eager-mode note below. `started_at`, `worker_id`, `retry_count` are set/refreshed here. |
| `PROCESSING` → `COMPLETED` | Ingestion finished, `failed_rows == 0` (Phase 5d: `status` reflects ingestion only — see §0 for `calculation_status`, the separate calculation-stage axis) |
| `PROCESSING` → `PARTIALLY_COMPLETED` | Ingestion finished, `failed_rows > 0` (even 100% failed) — the **job** completed; this is distinct from a pipeline crash |
| `PROCESSING` → `FAILED` | Unhandled exception during parsing/validation/persistence. `error_message` always includes exception type + message + stage context — never a bare "processing failed". Chain-terminating: `calculate_task` never runs. |
| `{COMPLETED,PARTIALLY_COMPLETED,FAILED,CANCELLED}` → *(terminal)* | A redelivered task (Celery's `acks_late`) is skipped, never reprocessed — see `UploadBatch.TERMINAL_STATUSES` |
| `{QUEUED,PROCESSING}` → `CANCELLED` | **Not implemented this phase** — see §6 |

Separately, `calculation_status` (Phase 5d): `NOT_STARTED → CALCULATING →
CALCULATED`/`CALCULATION_FAILED`, owned by `calculate_task` — see §0.

### Why `PROCESSING` is reachable from three different incoming statuses

Under `CELERY_TASK_ALWAYS_EAGER` (tests, local `DEBUG`), `.delay()` runs the
task **synchronously** — it completes before the view code that would write
`QUEUED` ever executes. So the task's guard is "not yet terminal", not
"specifically `QUEUED`" — otherwise eager-mode tests (and local dev without a
real worker) would never transition correctly. The view only writes `QUEUED`
if, after `.delay()` returns, the batch is *still* `PENDING` — proof that
real async dispatch happened. Writing `QUEUED` unconditionally would clobber
a terminal status eager mode already wrote.

A batch stuck in `PROCESSING` from a crashed worker is also legitimately
picked up and reprocessed here — that's the whole point of `acks_late`
(locked in during Phase 5a): a crashed worker's in-flight task is redelivered
rather than lost.

---

## 2. Progress tracking — coarse-grained by design

Ingestion runs as **one atomic transaction** (parse → validate → normalize →
bulk-insert → calculate CO₂e, all-or-nothing). This is deliberate: it
guarantees a batch is entirely-in or entirely-out, and it's what makes
retries safe. The trade-off: **nothing written mid-transaction is visible to
a separate polling request** until the whole thing commits (standard
read-committed isolation) — so there is no meaningful "42 of 100 rows
processed" signal to report while `PROCESSING`.

True incremental progress would require restructuring ingestion into chunked
commits — a real architectural change with its own cost (a crash mid-chunk-
sequence leaves a batch *partially* persisted, needing its own recovery
semantics) — and is explicitly **out of scope** for this phase. Given actual
file-size caps (10MB) and observed processing times (sub-second to
low-single-digits in every manual/automated test), the practical value of
chunked progress at today's scale is limited.

**What's actually implemented** (all computed at read time in
`BatchProgressFieldsMixin` / `serializers.py` — nothing stored redundantly):

| Field | Behavior |
|---|---|
| `total_records` (`total_rows`) | Populated at completion; `0` before |
| `successful_records` | `total_rows - failed_rows` |
| `processed_records` | `0` while non-terminal, `total_rows` once `COMPLETED`/`PARTIALLY_COMPLETED` — the honest "0 → all" jump, not a fake animation |
| `progress_percentage` | `100` for `COMPLETED`/`PARTIALLY_COMPLETED`, `0` otherwise (including `FAILED` — nothing durably committed, so anything but 0 would misrepresent a crash as a successful finish) |
| `duration_seconds` | `finished_at - started_at`; while still `PROCESSING` (no `finished_at` yet), reports elapsed time so far, not a final duration |
| `estimated_completion_time` | Only while `PROCESSING`. Heuristic: average duration of the last 5 completed batches for the **same `DataSource`**, applied to `started_at`. Returns `null` with no history. Not a predictor. |

---

## 3. API design — polling now, WebSocket/SSE-ready later

- `GET /api/batches/{id}/progress/` — **the polling endpoint.** Lean,
  job-lifecycle-focused JSON (`BatchProgressSerializer`): status, record
  counts, percentage, timestamps, `worker_id`, `retry_count`,
  `estimated_completion_time`, error info. Deliberately self-contained and
  transport-agnostic — a future WebSocket/SSE channel could push this exact
  same payload shape without any frontend contract change. Always a
  single-object fetch (never paginated), which is also why it's safe for
  this endpoint alone to run the `estimated_completion_time` historical
  query — that query is excluded from `UploadBatchSerializer` (used by the
  list endpoint) specifically to avoid an N+1 query risk across a page of
  batches.
- `GET /api/batches/{id}/` — the full batch resource (`UploadBatchSerializer`),
  now carrying the same cheap progress fields (everything except
  `estimated_completion_time`) for a complete one-time fetch.
- Both reuse `UploadBatchViewSet`'s existing tenant scoping
  (`TenantScopedViewSetMixin`) and `IsOrgMember` permission — no separate,
  easy-to-forget authorization path for the progress action.

---

## 4. Frontend: `useBatchProgress()` hook

`frontend/src/hooks/useBatchProgress.js` wraps TanStack Query (the
established pattern since Phase 4's dashboard) around the `/progress/`
endpoint:

- `refetchInterval` is a function of the latest data — polls every 1.5s while
  non-terminal, stops (`false`) once `status` is in the terminal set.
- Components consume `{ data, isTerminal, error, isLoading }` and never see a
  fetch call, an interval, or a URL. Swapping the transport to a WebSocket or
  SSE subscription later means rewriting the **inside** of this hook only —
  no consuming component (`UploadPage.jsx`'s `BatchProgressCard`) would need
  to change.
- Wired into `UploadPage.jsx`: after the `202` upload response, the page
  switches from showing axios's byte-level upload progress to polling this
  hook for real job progress — a live progress bar, record counts, elapsed
  time, and (once terminal) a link to the ledger or the failure reason.

---

## 5. Observability

New `UploadBatch` fields:

| Field | Source | Notes |
|---|---|---|
| `started_at` | `IngestionService.ingest_batch()` | Set/refreshed every real processing attempt, including crash-recovery redeliveries |
| `finished_at` | `CarbonCalculationService.calculate_for_batch()`, or `ingest_batch()`'s own `FAILED` path | Phase 5d: marks the end of the WHOLE CHAIN, not just ingestion — see §0 |
| `worker_id` | Whichever task is currently active (`self.request.hostname`, Celery `bind=True`) | Real Celery worker hostname — verified non-empty even under eager-mode tests; overwritten by `calculate_task` once the chain reaches it |
| `retry_count` | `self.request.retries`, same "last active task" ownership as `worker_id` | Captured for real now — always `0` until Phase 5e adds retry policies, at which point this reports real values with **zero further schema change** |
| `celery_task_id` | The view sets it to `ingest_task`'s id at enqueue; `calculate_task` overwrites it with its own id | Always points at whichever task is currently active or about to run — this is what a future cancel endpoint calls `AsyncResult(id).revoke()` on |
| `calculation_status` | `calculate_for_batch()` | Phase 5d: the calculation stage's own axis, independent of `status` — see §0 |
| `workflow_id` | Set once at batch creation, threaded through both task signatures | Stable across every chain link, unlike each task's own (different) Celery task id — see §0 |
| `pipeline_version` | Model default (`"1.0"`) | Not read by any branching logic yet — see §0 |
| `duration` | *(not stored)* | Trivially `finished_at - started_at`; storing a derived value risks drift if either timestamp ever changes. Exposed as computed `duration_seconds`. |

---

## 6. Cancellation — declared, not implemented

`CANCELLED` is a real `BatchStatus` member and is included in
`UploadBatch.TERMINAL_STATUSES` (so the idempotency guard and progress
calculations already handle it correctly), but **no code path can transition
a batch into it this phase** — no cancel endpoint, no Celery task revocation.
This mirrors the exact pattern the carbon engine already established for its
`AIRecommendationStage`/`OptimizationStage` (Phase 3): a reserved interface
point with zero implementation, so the eventual feature needs no schema or
terminal-status-set change.

**Intended future design**, when actually built:

- `POST /api/batches/{id}/cancel/` (Org-Admin/Analyst, audited via
  `AuditTrail`, same pattern as `approve`/`recalculate`).
- `QUEUED` → `CANCELLED`: `AsyncResult(batch.celery_task_id).revoke()` — clean,
  since the task hasn't started; Celery drops it from the queue without
  running any of it.
- `PROCESSING` → `CANCELLED`: **cooperative cancellation only.**
  `revoke(terminate=True)` mid-transaction risks leaving inconsistent state
  (SIGTERM during a DB write). The task would need to check a cancellation
  flag between pipeline stages instead — likely restricted to `QUEUED`-only
  cancellation initially, with `PROCESSING`-time cancellation deferred
  further until actually designed.

---

## 7. Error handling

Every `FAILED` transition's `error_message` includes exception type +
message + stage context — never a generic "processing failed":

- Storage save failure: `"Failed to persist upload to durable storage: {ExceptionType}: {message}"`
- Missing parser registration: `"Pipeline configuration error: No parser registered for source type: {type}"`
- Any other pipeline exception: `"Ingestion pipeline failed while parsing/validating/persisting: {ExceptionType}: {message}"`

---

## 8. Testing

`apps/ingestion/tests_lifecycle.py`:

- **Lifecycle** — every reachable transition (`COMPLETED`, `PARTIALLY_COMPLETED`,
  `FAILED` via pipeline crash, `FAILED` via storage failure, `QUEUED` under
  real dispatch vs. eager mode), plus redelivery-is-skipped across all four
  `TERMINAL_STATUSES` at once.
- **Progress calculations** — every field's behavior across every status,
  including the historical-average `estimated_completion_time` heuristic.
- **Polling** — the `/progress/` endpoint's payload shape, tenant scoping
  (404 cross-org, matching `TenantScopedViewSetMixin`'s queryset-filtering
  behavior), and authentication requirement.
- **Retry** — validates the *capture mechanism* (`self.request.retries`/
  `hostname`) works today; forcing a genuinely nonzero `retry_count` needs an
  actual retry policy to trigger a real redelivery, which is Phase 5e's
  concern, not this one.
- **Cancellation** — documents the future state (this section) and asserts
  `CANCELLED` is already correctly wired into `TERMINAL_STATUSES` and the
  idempotency guard, even with no way to reach it yet.

All verified against both the automated suite (`CELERY_TASK_ALWAYS_EAGER`)
and a real Docker Compose stack (Postgres + Redis + MinIO + a real Celery
worker) — including observing `QUEUED` for real (unreachable under eager
mode, where the task always finishes before the view can write it) and a
real `worker_id` (`celery@<container-hostname>`) on a genuine cross-container
task dispatch.

**Phase 5d additions** (`apps/ingestion/tests_tasks.py`): `IngestTaskTests`
(ingestion-only assertions — `calculation_status` stays `NOT_STARTED` and no
`EmissionCalculation` rows exist after `ingest_task` alone) and
`CalculateTaskTests` (the calculation stage's own idempotency guard,
`finished_at`/`worker_id` ownership). Both were also live-verified against
the real Docker Compose stack — including a manual `calculate_task`
redelivery against an already-`CALCULATED` batch, confirming
`"skipped-CALCULATED"` and no duplicate calculation.
