# ADR 0009: Anomaly explanation is dispatched off the deterministic pipeline; AIAnnotation is immutable with PROTECT-only FKs

- Status: Accepted
- Date: 2026-07-08
- Phase: 7b (Advisory AI Anomaly Detection)

## Context

Phase 7b is the first Phase 7 milestone to implement a real, user-facing
AI capability. Two structural questions have to be answered before writing
any code: (1) where, in the existing ingest → validate → calculate
pipeline, does the AI explanation call actually happen, given the pipeline
already has a reserved seam for exactly this (`AIRecommendationStage`,
inert since Phase 3) but that seam runs synchronously, per-record, inside
`calculate_task`; and (2) what does the persistence layer for "AI said
this about a record" look like, given the milestone's explicit requirement
that "suggestions must be immutable" and that human actions must "remain
separate."

## Decision 1: fire-and-forget dispatch from `ingest_task`, not inline in `AIRecommendationStage`

**Alternatives considered:**

**A. Dispatch a new, separate Celery task from `ingest_task`'s success
path, on the existing `ai` queue** (chosen). `apps.ai.tasks.
generate_anomaly_explanations_task` is queued with the exact one-line
`.delay()` pattern already used for `send_notification_task` — added
without changing a single line of `IngestionService`/`RowValidator`'s own
deterministic logic. It doesn't start until `ingest_task` has already
committed its transaction and returned.

**B. Implement `AIRecommendationStage.process()` for real, calling
`invoke_ai()` synchronously inside `calculate_task`'s per-record loop.**
This is the seam Phase 3 explicitly reserved for AI — using it would look
like the "obviously correct" choice. Rejected because: (1) `is_suspicious`
is decided at INGESTION time (`apps.ingestion.services.validator.
RowValidator`), not at calculation time — `AIRecommendationStage` runs
*after* factor resolution, on every record reaching that stage, not only
suspicious ones, so using it would mean either explaining every record
(wasteful, off-scope) or threading a suspicious-only filter into a stage
whose contract is "runs for whatever reaches it"; (2) a synchronous,
network-bound AI call inside the calculation pipeline's per-record loop
would add real, unbounded latency to `calculate_task` — the exact
milestone constraint ("do not modify the deterministic ESG pipeline," "AI
must remain advisory only") this option would violate in practice even
without changing a single line of calculation math, because SLOWING the
deterministic pipeline down to accommodate AI is itself a form of coupling
this milestone rules out.

**C. A Celery Beat scheduled sweep** (periodically scan for unexplained
suspicious records), mirroring `recalculate_missing_calculations_task`'s
safety-net pattern. Not rejected outright — a reasonable future addition —
but out of this milestone's explicit scope; the per-batch dispatch from
`ingest_task` is the primary, sufficient trigger for every record actually
flagged suspicious today.

**Decision: A.** `AIRecommendationStage` remains exactly as inert as
Phase 3 left it — this milestone makes zero changes to
`apps/carbon/services/pipeline.py`. The dispatch lives entirely in
`apps/ingestion/tasks.py` (one new import, one new `.delay()` call) and
`apps/ai/tasks.py` (the new task itself). Confirmed via the full
`apps.ingestion` test suite (190 tests, zero regressions) that adding this
inline-under-`CELERY_TASK_ALWAYS_EAGER` dispatch to every test that calls
`ingest_task` doesn't break anything — the new task's own fail-safe design
(AI disabled → clean no-op) makes it cheap even when exercised
unintentionally.

## Decision 2: `AIAnnotation` uses only PROTECT foreign keys, immutability mirrors `AuditTrail`

**Alternatives considered:**

**A. `record`/`organization`/`interaction` all `on_delete=PROTECT`;
immutability via `clean()`/`delete()`/`save()` overrides identical to
`AuditTrail`'s own pattern, plus a matching QuerySet-level bulk-update/
delete guard** (chosen).

**B. `interaction` as `on_delete=SET_NULL`** (matching `AIInteraction.actor`'s
own nullable pattern) — rejected specifically because of a real bug found
earlier this session: `EmissionRecordQuerySet.update()` unconditionally
blocking any bulk `.update()` call broke `User.delete()` entirely, because
Django's deletion Collector always issues a
`<model>.objects.filter(<fk>_id__in=[...]).update(<fk>=None)` call to
satisfy a SET_NULL cascade, and a queryset that blocks *all* updates
blocks that one too. `AIAnnotation`'s own bulk-update guard (needed for
"suggestions must be immutable") would reintroduce exactly this bug class
the moment any code path ever deletes an `AIInteraction` row. Using
`PROTECT` everywhere sidesteps the entire bug class structurally: Django's
collector never issues a SET_NULL-shaped update against a model with no
SET_NULL fields, so the immutability guard can be unconditional and safe.

**C. No queryset-level guard, immutability enforced only at the instance
level** (`clean()`/`delete()`/`save()`, no `_AIAnnotationQuerySet`
override). Rejected: `AuditTrail` already demonstrates why this gap
matters — `EmissionRecord.objects.filter(...).update(...)` bypasses
`save()`/`clean()` entirely (raw SQL `UPDATE`), so instance-level
guards alone don't stop a bulk mutation. The QuerySet-level guard closes
exactly that gap, for the same reason `AuditTrailQuerySet` exists.

**Decision: A.** No `SET_NULL` field exists anywhere on `AIAnnotation` —
verified by a dedicated test
(`AIAnnotationProtectedForeignKeyTests.test_no_field_on_this_model_uses_set_null`),
not just documented. Multiple annotations can accumulate per
`(record, capability)` over time (no uniqueness constraint); each one,
once created, is permanent. Idempotency ("don't regenerate on Celery task
redelivery") is a service/task-layer concern
(`generate_anomaly_explanations_task` excludes already-annotated records),
not a DB constraint — a genuine future "explain this again" action isn't
foreclosed by a uniqueness constraint that would need migrating away.

## Consequences

- A slow, rate-limited, or temporarily-down AI provider can never delay or
  fail an ingestion run — the two pipelines are decoupled at the task
  level, not just conceptually.
- `AIRecommendationStage`/`OptimizationStage` remain available, inert
  seams for a future milestone that genuinely needs a synchronous,
  per-calculation hook (e.g. a factor-resolution assist that must complete
  before the calculation itself can proceed) — Phase 7b's choice not to
  use them for anomaly explanation doesn't retire them.
- `AIAnnotation` can never be the target of a `SET_NULL` cascade from any
  future FK Django might add pointing at `AIInteraction` or
  `EmissionRecord`, because there are none on this model to trigger one —
  this constraint should be kept in mind if a future milestone is tempted
  to relax `interaction`/`record`/`organization` to nullable for
  convenience.
- Explaining the same record twice (e.g. after new context appears) is
  possible without a migration — it just creates a second, independent,
  equally immutable row.
