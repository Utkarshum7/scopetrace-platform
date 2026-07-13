# Email Notifications (`NOTIFICATIONS.md`)

Phase 5g — channel-agnostic email notifications for a batch's final
outcome. Builds directly on 5d's terminal-state model, 5e's retry/DLQ
machinery, and 5f's stale-batch sweep — none of that was redesigned, only
extended with a fourth (and now fifth) way for those existing terminal-state
transitions to trigger a side effect.

---

## 0. Pre-implementation review

**No `AUTH_USER_MODEL` override** — `UploadBatch.uploaded_by` points at
Django's default `User`, which has `.email`. Can be `None` (system/anonymous
uploads); notification must skip gracefully in that case, not error.

**A batch reaches a terminal state via three separate code paths, not one:**

1. The normal task completion path (`ingest_task`/`calculate_task`, via
   `IngestionService`/`CarbonCalculationService`, using `.save()`).
2. The 5e DLQ signal handler (retries exhausted), using
   `.exclude(...).update(...)`.
3. The 5f stale-batch sweep, also using `.update(...)`.

This rules out a single Django `post_save` signal as "the one hook":
**`QuerySet.update()` never fires model signals** — a well-known Django
limitation. A signal-based design would silently miss paths 2 and 3, which
are exactly the failure cases most worth notifying a user about. Notification
dispatch is therefore an explicit call at each of the sites that already
know they just transitioned a batch to a final state — not a new generic
hook.

**One email per batch, not one per stage.** To avoid double-emailing a
single upload, a notification fires once per axis reaching its true final
resting state:

- Ingestion `FAILED` (chain stops, calculation never runs) → send
  immediately; this **is** the final state.
- Ingestion `COMPLETED`/`PARTIALLY_COMPLETED` → wait for calculation's own
  terminal state (`CALCULATED`/`CALCULATION_FAILED`) before sending the one
  consolidated "upload finished" email.

---

## 1. Design decision: thin wrapper over Django's `EMAIL_BACKEND`, not a custom ABC

Two reasonable options existed:

| | Thin wrapper over `EMAIL_BACKEND` (chosen) | Custom `NotificationService` ABC + providers |
|---|---|---|
| Foundation | Django's own mail-backend system — console/SMTP/any third-party ESP backend (SendGrid/SES/Mailgun) share one interface, swappable via the `EMAIL_BACKEND` setting | A new base class + `ConsoleProvider`/`SMTPProvider` + factory, mirroring `StorageService`'s shape |
| Why `StorageService` needed its own ABC | Django's built-in `Storage` genuinely lacked operations the project needed (`generate_download_url`, checksum metadata, a working `exists()`) | — |
| Does email have an equivalent gap? | No — Django's mail backend abstraction already *is* the "no ESP lock-in" mechanism the roadmap asked for | Would be ceremony for shape-parity with `StorageService`, not because Django's abstraction is insufficient |
| What's actually built | `apps/core/notifications.py`: domain methods (`notify_batch_result`) that call `django.core.mail.send_mail()` | — |

**Chosen: thin wrapper.** `apps/core/notifications.py` has exactly two
functions — `notify_batch_result(batch)` (the public entry point: decides
whether to send, and to whom) and `_compose_message(batch)` (subject/body
for each of the three final states, or `(None, None)` if the batch isn't in
one). Swapping the ESP later is a `EMAIL_BACKEND` + credential change only —
`apps/core/notifications.py` never references a provider by name, exactly
like `StorageService`'s callers never reference a concrete cloud provider.

---

## 2. Design decision: dedicated fire-and-forget Celery task, not inline sending

`apps.core.tasks.send_notification_task` is dispatched via `.delay()` from
every terminal-state call site — never called synchronously inline. This
decouples notification delivery from `ingest_task`/`calculate_task`'s own
transaction and retry policy entirely:

- A slow or down SMTP server can never hold up a worker slot that could
  otherwise process real ingestion/calculation work.
- A mail delivery failure can never affect `UploadBatch` state — this task
  only ever *reads* the batch (to compose the message), never writes to it.
- It gets its **own** small retry policy
  (`NOTIFICATION_RETRYABLE_EXCEPTIONS = (smtplib.SMTPException, OSError)`,
  `max_retries=3`, backoff 2s/4s/8s capped 60s — deliberately mirrors
  `ingest_task`'s policy shape, since "send one email" is comparably cheap
  and fast to retry) — designed independently of `ingest_task`'s/
  `calculate_task`'s, per the same "not shared config" principle from 5e.
- Routed to its **own** `notifications` queue (not reused from 5f's
  `maintenance` queue) — a distinct concern: user-facing email delivery
  triggered by live traffic, vs. periodic system sweeps. Keeping them
  separate means a burst of scheduled maintenance work can never delay
  outbound notification emails, and vice versa.

**Accepted limitation: no de-duplication guard.** Unlike `ingest_task`/
`calculate_task`, `send_notification_task` has no idempotency guard against
being re-run — there's no persistent state it could corrupt by re-running,
only a possible duplicate email in the rare case of a worker crashing after
`send_mail()` truly succeeded but before Celery could ack the message
(`acks_late`'s normal at-least-once redelivery). An occasional duplicate
email on that rare timing coincidence is an acceptable trade-off against the
complexity of building a dedup mechanism (e.g. a `notification_sent_at`
field + its own guard) for a side effect this low-severity.

---

## 3. The five dispatch call sites

| Site | Trigger | File |
|---|---|---|
| `ingest_task`'s non-retryable failure | Ingestion crashed for a non-transient reason — chain-terminating, final state | `apps/ingestion/tasks.py` |
| `calculate_task`'s success path | The whole chain finished successfully | `apps/carbon/tasks.py` |
| `calculate_task`'s non-retryable failure | Ingestion succeeded but calculation crashed for a non-transient reason — also final | `apps/carbon/tasks.py` |
| DLQ handler, ingestion axis fixup | `ingest_task`'s retries exhausted; fixup successfully marked the batch `FAILED` | `apps/tasks/signals.py` |
| DLQ handler, calculation axis fixup | `calculate_task`'s retries exhausted; fixup successfully marked `CALCULATION_FAILED` | `apps/tasks/signals.py` |
| Stale-batch sweep, either axis | 5f's periodic sweep found a batch stuck long enough to mark terminal | `apps/ingestion/tasks.py` (`cleanup_stale_batches_task`) |

Each site dispatches only when it just performed a *real* transition — never
when a batch was already terminal (a no-op DLQ fixup, or a retryable failure
that's about to retry) — so no duplicate or premature emails.

**A wrinkle in the stale-batch sweep specifically:** its actual mutation is
still a single atomic conditional `UPDATE`
(`.exclude(...).filter(id__in=...).update(...)`, matching 5f's original
design), but a bulk `UPDATE` only returns a row *count*, not which rows it
touched — so the affected ids are read via a plain `.values_list("id",
flat=True)` query immediately *before* each `UPDATE`, purely to know who to
notify afterward. The `UPDATE` itself still re-applies the same
`.exclude(status__in=TERMINAL_STATUSES)` condition rather than trusting that
id list, so the atomicity/race-safety guarantee from 5f is unchanged — the
id list is only ever a notification hint, never a substitute for the
conditional `UPDATE`'s own correctness.

**Both DLQ-handler and stale-sweep dispatch calls are wrapped defensively**
(`_notify_batch_result_best_effort` in `signals.py`; a plain loop in the
sweep) so that a broker being unreachable at dispatch time — plausible,
since it may be the very same outage that caused the underlying failure —
can never make the signal handler or sweep task itself raise. A missed
notification is a much smaller loss than the DLQ log entry or batch-status
fixup failing.

---

## 4. Message content

Three states, three messages (`apps/core/notifications.py::_compose_message`):

| State | Subject | Body includes |
|---|---|---|
| `status=FAILED` | `Upload failed: {file_name}` | `error_message`, batch id |
| `status∈{COMPLETED,PARTIALLY_COMPLETED}`, `calculation_status=CALCULATED` | `Upload processed: {file_name}` | status display, `total_rows`, `failed_rows`, batch id |
| `status∈{COMPLETED,PARTIALLY_COMPLETED}`, `calculation_status=CALCULATION_FAILED` | `Upload calculation failed: {file_name}` | `error_message`, `total_rows`, batch id |

Anything else (still in flight on either axis) returns `(None, None)` —
`notify_batch_result` treats that as "nothing to send yet" and skips
silently (logged at `info`, not an error).

---

## 5. Configuration — `config/settings.py`

```python
EMAIL_HOST = config('EMAIL_HOST', default='')
if EMAIL_HOST:
    EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
    EMAIL_PORT = config('EMAIL_PORT', default=587, cast=int)
    EMAIL_HOST_USER = config('EMAIL_HOST_USER', default='')
    EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD', default='')
    EMAIL_USE_TLS = config('EMAIL_USE_TLS', default=True, cast=bool)
else:
    EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
EMAIL_TIMEOUT = config('EMAIL_TIMEOUT', default=10, cast=int)
DEFAULT_FROM_EMAIL = config('DEFAULT_FROM_EMAIL', default='noreply@scopetrace.local')
```

**Deliberately does NOT fail closed like `STORAGE_BACKEND` does.** Storage
is required for uploads to function at all — a missing/wrong
`STORAGE_BACKEND` in production is an active incident. Sending an email is a
side effect of a batch finishing; `apps.core.notifications.
notify_batch_result` never runs on the request path, only from the
decoupled `send_notification_task`. So the safe default even when
`DEBUG=False` is the **console backend** (notifications are logged, not
delivered) until `EMAIL_HOST` is explicitly set — a missing notification
config degrades to "silently not emailing anyone," never to a broken
pipeline.

`EMAIL_TIMEOUT=10s` matches the same defensive-timeout pattern already used
elsewhere in this codebase (`healthz_worker`'s `inspect(timeout=2.0)`,
axios's 60s upload timeout) — a dead/firewalled SMTP host fails fast rather
than hanging a worker slot indefinitely.

`CELERY_TASK_ROUTES` gets one more entry
(`apps.core.tasks.send_notification_task` → `notifications` queue), and
`docker-compose.yml`'s worker `-Q` list is extended to
`celery,ingestion,calculation,maintenance,notifications` — one pool still
consumes everything today, same "seam now, policy later" pattern as every
other queue addition this phase.

---

## 6. Testing

- `apps/core/tests_notifications.py` — `notify_batch_result`/
  `_compose_message`: skip when no recipient / recipient has no email / not
  in a final state (every non-final status×calculation_status combination);
  correct subject/body for all three final states, using `django.core.mail
  .outbox` (Django's test runner always forces the locmem backend
  regardless of `settings.py`, so no `override_settings` needed).
  `send_notification_task`: batch-not-found, sends and returns `"sent"`,
  returns `"skipped"` for no recipient.
- `apps/ingestion/tests_notification_dispatch.py` — `ingest_task` dispatches
  on a non-retryable failure, does NOT dispatch on a retryable one (might
  still succeed on retry); the DLQ handler dispatches after a real fixup,
  does NOT dispatch on a no-op fixup (already-terminal batch).
- `apps/carbon/tests/test_notification_dispatch.py` — `calculate_task`
  dispatches on both its success path and its non-retryable failure path;
  does NOT dispatch on a retryable failure.
- `apps/ingestion/tests_maintenance.py` — sweeping a stale batch dispatches
  a notification to its uploader; a no-op sweep (nothing stale) dispatches
  none.
