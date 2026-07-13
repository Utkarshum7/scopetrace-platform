# Retry Policies, Backoff, and the Dead Letter Queue (`RETRY_DLQ.md`)

Phase 5e — independent retry policies with exponential backoff/jitter for
`ingest_task` and `calculate_task`, the `transient_exceptions` mechanism that
keeps a retry-eligible failure from being silently defeated by the existing
idempotency guards, and a DB-backed Dead Letter Queue (DLQ) for tasks whose
retries are genuinely exhausted. Builds directly on Phase 5d's chained
orchestration, `workflow_id`, and terminal-status idempotency guards (see
[`JOB_LIFECYCLE.md`](JOB_LIFECYCLE.md)) — none of that was redesigned, only
extended.

---

## 0. Pre-implementation review — was 5a-5d sufficient to support retries?

Before writing any retry policy, the existing async architecture was
reviewed against three questions:

**1. Is the task chain retry-safe?** Yes, structurally — `chain()` with
`.si()` immutable signatures means each task only depends on durably-committed
DB state (re-fetched via `activity_input_from_record()`), not on a signature's
bound arguments or the previous task's return value. Retrying either task in
isolation (Celery's per-task retry, not a chain-level retry) requires no
special chain-awareness: `ingest_task` retrying doesn't re-run
`calculate_task`, and `calculate_task` retrying doesn't re-run `ingest_task`.

**2. Is `workflow_id` retry-safe?** Yes — it's read from the DB
(`UploadBatch.workflow_id`) and passed as a plain argument, not derived from
Celery's own task id (which changes across retries — `self.request.id` is
stable *per delivery*, not per logical task). `workflow_id` was designed in
5d specifically to survive exactly this kind of multi-attempt correlation
need.

**3. Are the idempotency guards retry-safe?** **No — this was the one real
gap, and the central finding of this review.** `ingest_task`/`calculate_task`
already guarded against *redelivery* (`acks_late` + `TERMINAL_STATUSES`
check), but `IngestionService.ingest_batch()` and
`CarbonCalculationService.calculate_for_batch()` **unconditionally marked the
batch terminal (`FAILED`/`CALCULATION_FAILED`) on any exception** — including
one Celery was about to retry. Sequence, before the fix:

1. Attempt 1 raises `OperationalError` → service layer catches it, marks the
   batch `FAILED`, re-raises.
2. `autoretry_for` schedules attempt 2.
3. Attempt 2 begins, hits the `TERMINAL_STATUSES` guard at the top of the
   task (the batch is `FAILED` from step 1) → logs "already FAILED, skipping"
   and returns — **the retry never actually retries the work.**

This would have silently defeated every retry policy on the very first
attempt. Fixed via the `transient_exceptions` parameter (§2) — this is the
prerequisite the rest of this milestone depends on, not an optional
refinement.

**Conclusion:** the chain, `workflow_id`, and queue routing needed zero
changes. The idempotency guards needed exactly one targeted fix before retry
policies could be layered on safely.

---

## 1. Design: independent policies, not shared config

`ingest_task` and `calculate_task` each define their **own**
`autoretry_for` tuple, `max_retries`, `retry_backoff`, and
`retry_backoff_max` — deliberately not extracted into a shared constant,
per the requirement that the two be designed independently:

```python
# apps/ingestion/tasks.py
INGEST_RETRYABLE_EXCEPTIONS = (OperationalError, InterfaceError)

@shared_task(
    bind=True,
    autoretry_for=INGEST_RETRYABLE_EXCEPTIONS,
    retry_backoff=2,        # base delay: 2s, 4s, 8s
    retry_backoff_max=60,   # cap, never reached at max_retries=3
    retry_jitter=True,
    max_retries=3,
)
def ingest_task(self, batch_id, storage_key, workflow_id): ...
```

```python
# apps/carbon/tasks.py
CALCULATE_RETRYABLE_EXCEPTIONS = (OperationalError, InterfaceError)

@shared_task(
    bind=True,
    autoretry_for=CALCULATE_RETRYABLE_EXCEPTIONS,
    retry_backoff=2,
    retry_backoff_max=120,
    retry_jitter=True,
    max_retries=5,
)
def calculate_task(self, batch_id, workflow_id): ...
```

`tests_retry.py::RetryPolicyConfigTests.test_policies_are_independent_objects_not_shared`
asserts the two exception tuples are not the same object (`assertIsNot`) —
they coincide in value today only because both stages' only realistic
transient failure mode is the database, not because one was copy-pasted from
the other.

### Why these specific numbers

| | `ingest_task` | `calculate_task` | Why the difference |
|---|---|---|---|
| `max_retries` | 3 | 5 | Ingestion is sub-second, cheap to retry more times isn't valuable — 3 attempts (~14s worst case) is enough to ride out a brief connection blip without holding the queue. Calculation runs **after** ingestion already committed durably — abandoning it after a DB blip throws away completed upstream work, so it gets more attempts before giving up. |
| `retry_backoff` | 2 | 2 | Same base — both stages' only transient failure mode is the same class of DB connectivity error; there's no reason for the *shape* of the curve to differ, only its length. |
| `retry_backoff_max` | 60s | 120s | Scales with `max_retries` — calculation's 5th retry would otherwise reach a 32s base delay; 120s gives room for the curve to actually grow across 5 attempts instead of flattening early. |
| `retry_jitter` | True | True | Multiple batches failing at the same moment (e.g. a full DB restart affecting every in-flight task) must not all retry in lockstep and re-hammer the DB the instant it's healthy again — jitter randomizes each task's actual delay within `[0, backoff]`. |
| `autoretry_for` | `(OperationalError, InterfaceError)` | same values, independent tuple | Scoped to Django's own transient DB exceptions only — **not** storage-layer errors. `boto3`/MinIO's S3 client already retries transient network errors internally (its own retry config), so retrying at the Celery level too would compound backoff on top of backoff for the same underlying blip. A genuine non-transient storage failure (missing bucket, bad credentials) should fail fast, not spend a retry budget on something that will never resolve itself. |

---

## 2. `transient_exceptions` — keeping retries idempotent

`IngestionService.ingest_batch()` and
`CarbonCalculationService.calculate_for_batch()` both gained a
`transient_exceptions: tuple = ()` parameter. The default `()` is a pure
no-op — the synchronous `ingest()` path (never passes this parameter) and
every pre-5e caller keep byte-identical behavior; this was verified by
running the full pre-5e test suite (134/134 unchanged) against the change.

```python
except transient_exceptions:
    # A retry-eligible exception — the transaction already rolled back on
    # its own, but do NOT mark the batch terminal here. The caller
    # (ingest_task, via autoretry_for) is about to retry; marking it
    # terminal now would make the NEXT attempt's idempotency guard see an
    # already-terminal batch and skip it, permanently defeating the retry.
    logger.warning("... expecting a retry, not marking FAILED ...", exc_info=True)
    raise
except Exception as exc:
    # Not retryable (or transient_exceptions=() for the sync path) — mark
    # terminal now, exactly as before 5e.
    batch.status = UploadBatch.BatchStatus.FAILED
    ...
    raise exc
```

`ingest_task`/`calculate_task` pass their own `*_RETRYABLE_EXCEPTIONS` tuple
through as `transient_exceptions` — so the service layer's terminal/non-terminal
decision is driven by the exact same exception classes the task decorator
will actually retry, not a second hardcoded list that could drift out of
sync.

**Why this can't corrupt `UploadBatch`/`EmissionCalculation` state:**
ingestion and calculation each still run inside their own single
`transaction.atomic()` block (unchanged from 5d) — a transient exception
still rolls back every partial DB write from that attempt. The only change
is whether the batch's *status field* gets marked terminal before a retry;
no calculation or emission record data is ever left half-written.

---

## 3. Structured logging: initial attempt vs. retry attempt

Both tasks compute a human-readable attempt label from `self.request.retries`
(Celery increments this on every redelivery, including autoretry-triggered
ones) at the very top of the task body, before any work happens:

```python
attempt_label = (
    "initial attempt" if self.request.retries == 0
    else f"retry attempt {self.request.retries}/{self.max_retries}"
)
```

Every log line for that invocation — the terminal-status guard, the
"starting" line, the completion/failure line — includes this label alongside
`workflow_id` and `batch_id`, so a single `grep <workflow_id>` across worker
logs reconstructs the entire multi-attempt history of one upload in order,
distinguishing which line came from which attempt.

Verified live against Docker Compose (§5): a genuine DB outage produced

```
ingest_task: workflow 899ac723... batch baf6ef32... starting (retry attempt 3/3)
ingest_task: workflow 899ac723... batch baf6ef32... completed (retry attempt 3/3, attempt 4) — 1 rows, 0 failed
```

— i.e. `workflow_id` identical across every attempt, and the label correctly
distinguishing the 4th real delivery (3rd retry) from the initial one.

---

## 4. Dead Letter Queue — `apps.tasks`

A new Django app, `apps.tasks` ("Task Observability" in admin), owns the DLQ.
**Chosen design: a DB-logged model + a Django signal handler, not a
dedicated Celery DLQ queue.** This was a deliberate trade-off, not the only
valid option:

| | DB-logged (chosen) | Dedicated Celery DLQ queue |
|---|---|---|
| Queryability | Trivial — Django admin, ORM filters, joins against `UploadBatch` | Needs a separate consumer/inspector; messages aren't naturally queryable |
| Consistency with existing patterns | Matches `AuditTrail`'s existing append-only-log shape (Phase 3/4) | Would be the first message-queue-native audit trail in the codebase |
| Operational surface | Zero new infrastructure — reuses Postgres, already the durability backbone everywhere else | New queue to provision, monitor, and alert on independently of Postgres |
| Replay | Not automatic — an operator reads `FailedTaskLog`, decides, and manually re-triggers if appropriate | Could support automatic reprocessing from the DLQ queue itself |

Automatic reprocessing wasn't a requirement here, and this system's failure
volume (currently: any batch, at most twice a chain) doesn't yet justify a
second queue-based subsystem.

**Manual replay (Phase 7.5 H4-13):** `python manage.py replay_failed_task`
gives an operator a real recovery path instead of a hand-written shell
command. Read-only by default (lists what would be replayed):

```
python manage.py replay_failed_task --task-name apps.ingestion.tasks.ingest_task
python manage.py replay_failed_task --id <FailedTaskLog id> --replay
python manage.py replay_failed_task --id <FailedTaskLog id> --replay --delete-on-replay
```

Re-dispatches the original task by name/args/kwargs via `app.send_task()` --
the exact same queue routing and target-task idempotency guard
(`ingest_task`/`calculate_task` already tolerate redelivery by design) as a
normal at-least-once redelivery. Deliberately opt-in and never automatic
(the failed task's OWN retries were already exhausted by the time it landed
here — blindly auto-replaying could loop forever on a genuinely broken
input). The log row survives a replay by default (it's an observability
record, not task state); `--delete-on-replay` clears it once an operator has
confirmed the replay succeeded.

### 4.1 `FailedTaskLog`

```python
class FailedTaskLog(models.Model):
    id = UUIDField(primary_key=True, default=uuid.uuid4)
    task_name = CharField(db_index=True)      # e.g. "apps.ingestion.tasks.ingest_task"
    task_id = CharField()                     # Celery's own id for the FINAL failed attempt
    batch_id = CharField(null=True, db_index=True)
    workflow_id = CharField(null=True, db_index=True)
    args = JSONField(default=list)
    kwargs = JSONField(default=dict)
    exception_type = CharField()
    exception_message = TextField()
    traceback = TextField()
    retries_attempted = IntegerField(default=0)
    created_at = DateTimeField(auto_now_add=True)
```

Read-only in Django admin (`has_add_permission`/`has_change_permission`
return `False`) — these are audit records of what already happened, not
something to hand-edit.

### 4.2 The signal handler

Celery's `task_failure` signal fires exactly once per task, **only** on a
task's truly final failure. `self.retry()` (whether invoked automatically by
`autoretry_for` or manually) raises Celery's own `Retry` exception
internally, which Celery's task machinery treats as control flow, not a
failure — `task_failure` does not fire for an attempt that still has retries
remaining. This is what makes it safe to reuse this one signal for both
dead-letter logging and the batch-status fixup below: both need to happen
**exactly once**, and only when nothing is left to retry.

```python
def _handle_permanently_failed_task(sender, task_id, exception, args, kwargs, traceback, einfo, **extra):
    FailedTaskLog.objects.create(...)  # always — audit trail regardless of task
    logger.error("DEAD LETTER: ...")

    batch_id = kwargs.get("batch_id")
    if not batch_id:
        return

    if task_name == "apps.ingestion.tasks.ingest_task":
        UploadBatch.objects.filter(pk=batch_id) \
            .exclude(status__in=UploadBatch.TERMINAL_STATUSES) \
            .update(status=FAILED, error_message=..., finished_at=now())
    elif task_name == "apps.carbon.tasks.calculate_task":
        UploadBatch.objects.filter(pk=batch_id) \
            .exclude(calculation_status__in=UploadBatch.CALCULATION_TERMINAL_STATUSES) \
            .update(calculation_status=CALCULATION_FAILED, error_message=..., finished_at=now())
```

Design points:

- **`kwargs`, never `args`.** `ingest_task` and `calculate_task` have
  different positional signatures (the former also takes `storage_key`), so a
  generic, task-name-agnostic handler needs one shared convention. `views.py`
  was changed to call `.si(batch_id=..., workflow_id=..., ...)` with keyword
  arguments specifically to support this — any future task wanting
  DLQ + batch-status integration just needs to follow the same convention.
- **Atomic, race-free, idempotent update.** `.exclude(...__in=TERMINAL_STATUSES).update(...)`
  is a single conditional `UPDATE` statement — no read-then-write race, and a
  guaranteed no-op if the batch is already terminal (e.g. a *non-retryable*
  exception already marked it `FAILED` via the service layer's own
  `except Exception` branch before `task_failure` even fires). It never
  overwrites a more specific existing `error_message` with a less specific
  one, since it only touches rows that are still non-terminal.
- **Wired via `TasksConfig.ready()`** — Django's guaranteed-once startup
  hook — so the signal connection is established exactly once per process
  and survives worker restarts without depending on import order elsewhere
  in the app registry.

### 4.3 A second finding: what happens when the DLQ write itself can't reach the DB

Live Docker Compose testing (§5.2) surfaced a genuine edge case: when the
database itself is the resource whose outage caused a task's retries to
exhaust, and that outage is *still ongoing* when `task_failure` fires, the
signal handler's own `FailedTaskLog.objects.create(...)` call fails for the
exact same reason. Uncaught, this would propagate out of the signal receiver
and — worse — mean the batch-status fixup (which comes after, in the same
function) never runs either, leaving the batch silently stuck non-terminal
with no error message and no audit trail: a "double failure" where the one
dependency the failure-*logging* mechanism needs is the very thing that
broke.

This only bites when the outage outlasts the *entire* retry budget (~14s for
`ingest_task`, up to ~62s for `calculate_task`) — realistic for a prolonged
failover, not a hypothetical.

**Fix:** both the `FailedTaskLog` write and the batch-status fixup are each
wrapped in their own `try/except`, independent of each other — one failing
doesn't prevent the other from being attempted. On failure, each logs a
`CRITICAL`-level fallback message with full context (`task_name`, `task_id`,
`batch_id`, `workflow_id`, the original exception) so the failure is still
visible via worker stdout / any log aggregator, even though neither the DB
row nor the batch fixup could be persisted. This is a deliberately minimal
fix — see the trade-off table below for why a bigger one (a decoupled,
independently-retrying DLQ-write sub-task) was not built now.

| | Minimal fallback logging (chosen) | Decoupled retrying sub-task |
|---|---|---|
| Scope | A `try/except` + `logger.critical(...)` around each write | A new Celery task with its own `autoretry_for` DB-exception policy, dedicated to persisting `FailedTaskLog`/batch fixups |
| Coverage | Full observability via logs even during the double-failure window; no DB record until an operator investigates | Would eventually self-heal the DB record and batch fixup once the DB recovers, without operator involvement |
| Risk added | None — purely defensive, no new moving parts | A third retry-policied task to design, test, and reason about before this milestone could close |
| Why chosen | The failure is still fully visible (worker logs / log aggregator) the moment it happens — nothing is silently lost, only the structured DB record is delayed until manual follow-up. Matches the actual severity: this is a rare double-failure edge case, not the common path. |  |

`apps/tasks/tests.py::DeadLetterSignalHandlerUnitTests.test_does_not_raise_when_failed_task_log_write_itself_fails`
and `test_does_not_raise_when_batch_fixup_itself_fails` cover both halves
independently (mocking each DB write to fail in isolation), confirming
neither failure raises out of the handler and that the two are not coupled
to each other.

### 4.4 A separate discovery during testing: eager mode hides this signal entirely

While writing tests for the signal wiring, dispatching a task via `.delay()`
under `CELERY_TASK_ALWAYS_EAGER=True` + `CELERY_TASK_EAGER_PROPAGATES=True`
(the setting used for the whole test suite and local `DEBUG` dev, per
`config/settings.py`) turned out to **never fire `task_failure` at all**,
even for a task with zero retries left.

Root cause, confirmed by reading Celery's own source
(`celery.app.trace.build_tracer`'s `on_error` helper): `if propagate: raise`
bypasses `handle_error_state()` → `handle_failure()` entirely — and
`signals.task_failure.send(...)` lives inside `handle_failure()`. This is
genuine Celery eager-mode behavior, not a bug in this implementation:
`task_failure` fires correctly during real (non-eager) dispatch, confirmed
in §5 below.

This is exactly why this milestone's requirements call for verification
against a **real** Docker Compose environment rather than unit tests alone —
this specific class of behavior (a signal that real dispatch fires but eager
mode structurally cannot) is invisible to `manage.py test`. The unit test
suite (`apps/tasks/tests.py::DeadLetterSignalHandlerUnitTests`) instead calls
the handler function directly with hand-constructed arguments — fast and
precise for the "handler logic is correct" question — plus one wiring test
(`DeadLetterSignalWiringTests`) that sends `task_failure` directly via
`celery.signals.task_failure.send(...)`, bypassing Celery's eager tracer, to
confirm only that `TasksConfig.ready()` actually connected our handler to the
real signal.

---

## 5. Live verification against Docker Compose

All of the following were run against the real stack
(`docker compose up --build`) — Postgres, Redis, MinIO, and a real Celery
worker process (`celery@<container-hostname>`), `DEBUG=False`, i.e. **not**
eager mode.

### 5.1 Transient failure → automatic recovery (retry succeeds)

A batch was dispatched via `ingest_task.delay(...)`, then the `db` container
was stopped for ~6 seconds and restarted. Worker log (unedited):

```
Task ingest_task[...] retry: Retry in 1s: OperationalError('server closed the connection unexpectedly...')
Task ingest_task[...] retry: Retry in 1s: OperationalError('could not translate host name "db" to address...')
Task ingest_task[...] retry: Retry in 4s: OperationalError('could not translate host name "db" to address...')
ingest_task: workflow 899ac723-87c2-4574-85c4-a7af4d112e3a batch baf6ef32-... starting (retry attempt 3/3)
ingest_task: workflow 899ac723-87c2-4574-85c4-a7af4d112e3a batch baf6ef32-... completed (retry attempt 3/3, attempt 4) — 1 rows, 0 failed
Task ingest_task[...] succeeded in 0.36594668100042327s: 'completed'
```

Confirms: real (jittered) exponential backoff — actual delays 1s/1s/4s, all
within the `[0, base]` range Celery's jitter draws from — `workflow_id`
identical across every attempt, and recovery after the DB came back, using
the 3rd and final retry. Final DB state:

```
status=COMPLETED, retry_count=3, worker_id=celery@9c722761c1fc,
error_message=None, 0 FailedTaskLog rows for this batch
```

— i.e. a task that *used* its entire retry budget and still succeeded
produces zero DLQ noise and a completely clean final state.

### 5.2 Retries genuinely exhausted → Dead Letter Queue fires

A fresh batch was dispatched with `db` already stopped *before* dispatch
(eliminating any race with the first test). Worker log (unedited):

```
Task ingest_task[eae53cce-...] received
ingest_task: workflow 537e6121-... batch 84eebb56-... starting (initial attempt)
[... OperationalError, retry scheduled ...]
Task ingest_task[eae53cce-...] retry: Retry in ...
[... repeated for all 3 retries, db still down throughout ...]
[2026-07-06 ...] ERROR apps.tasks.signals: DEAD LETTER: task apps.ingestion.tasks.ingest_task
    (id=eae53cce-..., workflow=537e6121-..., batch=84eebb56-...) permanently failed
    after 3 retries: OperationalError: ...
[2026-07-06 ...] INFO apps.tasks.signals: DEAD LETTER: batch 84eebb56-... marked FAILED
    (was left non-terminal by a retryable exception whose retries were exhausted)
```

Post-recovery DB check confirmed a `FailedTaskLog` row exists for this
`task_id`/`batch_id`/`workflow_id` with `retries_attempted=3`, and
`UploadBatch.status == FAILED` with
`error_message` containing `"Ingestion failed permanently after 3 retries"`.
This is the concrete, real-dispatch proof that `task_failure` fires (unlike
the eager-mode limitation in §4.4), the DLQ record is created, and the batch
is correctly fixed up from its retry-preserved non-terminal state to a
terminal one — all without any unit test able to exercise this path.

---

## 6. Testing summary

- `apps/ingestion/tests_retry.py` — retry policy configuration values match
  this document; the `transient_exceptions` fix leaves a batch non-terminal
  on a transient failure and terminal on a non-transient one (proving the
  synchronous path is unaffected); an end-to-end "fails once, retried
  manually, succeeds, not skipped by the idempotency guard" test for both
  `ingest_task` and `calculate_task`; structured-logging attempt-label
  assertions.
- `apps/tasks/tests.py` — `FailedTaskLog` creation and field mapping;
  `ingest_task`/`calculate_task` exhausted-retry batch-status fixup for both
  status axes independently; idempotent no-op when the batch is already
  terminal; missing-`batch_id` doesn't raise; signal wiring (`TasksConfig.
  ready()` actually connects the handler to Celery's real signal).
- Full backend suite: 198/198 passing, zero regressions from any 5e change.
- Docker Compose: real transient-failure recovery (§5.1) and real
  retries-exhausted → DLQ (§5.2), both exercising code paths eager-mode
  tests structurally cannot reach (§4.4).
