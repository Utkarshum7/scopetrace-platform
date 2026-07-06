# Job Lifecycle (`JOB_LIFECYCLE.md`)

Phase 5c — `UploadBatch` as the source of truth for the async ingestion job's
lifecycle: state machine, progress tracking, the polling API, observability,
and error handling. Builds directly on Phase 5b (async upload processing via
Celery) and Phase 5a (Celery/Redis foundation).

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
| `PENDING` → `QUEUED` | File saved durably, `process_upload_batch.delay()` returned, and (checked via a DB re-read, not assumed) the task has **not** already run — i.e. real async dispatch, sitting in the broker. `celery_task_id` is recorded here. |
| `{PENDING,QUEUED,PROCESSING}` → `PROCESSING` | The task begins executing. **Not** gated on "incoming status == QUEUED" — see the eager-mode note below. `started_at`, `worker_id`, `retry_count` are set/refreshed here. |
| `PROCESSING` → `COMPLETED` | Pipeline finished, `failed_rows == 0` |
| `PROCESSING` → `PARTIALLY_COMPLETED` | Pipeline finished, `failed_rows > 0` (even 100% failed) — the **job** completed; this is distinct from a pipeline crash |
| `PROCESSING` → `FAILED` | Unhandled exception during parsing/validation/persistence. `error_message` always includes exception type + message + stage context — never a bare "processing failed". |
| `{COMPLETED,PARTIALLY_COMPLETED,FAILED,CANCELLED}` → *(terminal)* | A redelivered task (Celery's `acks_late`) is skipped, never reprocessed — see `UploadBatch.TERMINAL_STATUSES` |
| `{QUEUED,PROCESSING}` → `CANCELLED` | **Not implemented this phase** — see §6 |

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
| `finished_at` | `ingest_batch()` | Set on every terminal transition (`COMPLETED`/`PARTIALLY_COMPLETED`/`FAILED`), and on the pre-queue `FAILED` (storage save failure) |
| `worker_id` | `process_upload_batch`'s `self.request.hostname` (Celery, `bind=True`) | Real Celery worker hostname — verified non-empty even under eager-mode tests (Celery still populates a request context) |
| `retry_count` | `self.request.retries` | Captured for real now — always `0` until Phase 5e adds retry policies, at which point this reports real values with **zero further schema change** |
| `celery_task_id` | The view, right after `.delay()` returns | Not consumed by anything yet — this is what a future cancel endpoint calls `AsyncResult(id).revoke()` on |
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
