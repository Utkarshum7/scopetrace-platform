# ADR 0014: AI observability, cost governance, and ops health are built entirely from data this codebase already writes -- no duplicate accounting

- Status: Accepted
- Date: 2026-07-09
- Phase: 7g (AI Observability, Cost Governance & Operational Hardening) -- final Phase 7 milestone

## Context

Phase 7g is explicitly scoped as production readiness, not a new AI
capability: five real capabilities already exist (7b anomaly detection,
7c factor recommendation, 7d validation assistance, 7e ESG assistant, 7f
report narration), each already writing `AIInteraction` rows through the
single-enforcement-point gateway (`apps.ai.services.gateway.invoke_ai`),
and evaluation already writes `EvaluationRun`/`EvaluationResult` rows
(Phase 7a.5). Three structural questions had to be answered before
writing any code: (1) does observability/cost data get a new persistence
layer, or does it read what already exists; (2) what RBAC boundary
protects cross-tenant platform data versus per-organization cost data;
(3) how is "evaluation health" reported when some milestone-requested
signals (replay failures, invariant failures) have no dedicated
persistence of their own.

## Decision 1: pure read-only aggregation over `AIInteraction`/`EvaluationRun`/`EvaluationResult` -- no new accounting model

**Alternatives considered:**

**A. A new `AIMetricsSnapshot`-style model, periodically written by a
Celery Beat task, that pre-aggregates counts/sums for fast dashboard
reads.** Rejected: every number Phase 7g needs to report (request counts,
latency, token usage, cost, provider mix, evaluation pass/fail) is
already a column or countable field on `AIInteraction` or
`EvaluationRun`/`EvaluationResult`. A second, periodically-stale
accounting table would be a second implementation of "how much did this
cost" that could silently drift from the gateway's own numbers -- the
exact class of bug `apps.ai.services.cost_governance` explicitly avoids
by reusing `apps.ai.services.cost.check_budget()` verbatim rather than
re-summing `cost_usd` a second way for the budget figure specifically.

**B. Plain synchronous aggregation functions
(`apps.ai.services.observability.platform_ai_summary()`,
`apps.ai.services.cost_governance.org_cost_summary()`) called directly
from a DRF view on every request** (chosen). No caching layer, unlike
`apps.carbon.services.metrics_cache` -- AI operational data volume is
orders of magnitude smaller than carbon calculation data (per-request
`AIInteraction` rows, not per-record `EmissionCalculation` rows across a
whole tenant's upload history), so the cache-invalidation machinery
Phase 4b needed doesn't earn its complexity here yet. Revisit if/when AI
call volume genuinely makes these endpoints slow.

**Decision: B.** A single additive counter (`apps.ai.services.
cache_metrics`) was the one piece of genuinely new bookkeeping this
milestone needed, and only because the idempotency short-circuit in
`invoke_ai()` (a prior-outcome replay that writes no new `AIInteraction`
row) is structurally invisible to any `AIInteraction`-based query -- it
mirrors `apps.ai.tasks.AI_HEARTBEAT_CACHE_KEY`'s existing lightweight
cache-counter pattern exactly, not a new model or migration.

## Decision 2: platform-wide observability is `IsPlatformAdmin`-only; per-org cost governance activates the pre-existing `CanViewAICosts` seam

**Alternatives considered:**

**A. One combined endpoint returning both cross-tenant and per-org data,
gated by the broader `CanUseAI`.** Rejected: cross-tenant AI usage
(`platform_ai_summary`, `ai_ops_health`) is a platform-engineering
concern exactly like `apps.carbon.metrics_views.PlatformMetricsView`,
which is `IsPlatformAdmin`-only -- an Org Admin has no legitimate reason
to see another tenant's AI request volume. Per-org cost data, meanwhile,
needs a narrower-than-`CanUseAI` gate: `CanUseAI` includes Analyst, but
AI spend is governance-adjacent observability (the same category as the
audit/activity feed), not a feature an Analyst needs to operate AI, only
one an Org Admin or Auditor needs to govern it.

**B. Three separate endpoints with two different permission classes**
(chosen): `GET /api/ai/ops/observability/` and `GET /api/ai/ops/health/`
(`IsPlatformAdmin`), `GET /api/ai/costs/` (`CanViewAICosts`).
`CanViewAICosts` has existed since Phase 7a as a deliberately inert seam
("Organization Admins and Auditors may view AI cost/observability data
-- mirrors `CanViewActivity`'s role set exactly") with no endpoint to
protect until now -- this milestone is that seam's first real activation,
proven end-to-end (not just unit-tested against a bare request object)
for the first time.

**Decision: B.**

## Decision 3: "evaluation health" reports real persisted trend data; "invariant failures" stay a documented CI pointer, not fabricated metrics

The milestone asks observability to track "regressions, schema failures,
replay failures, invariant failures." Three of these map directly onto
`EvaluationResult.Outcome` values already written by every evaluation
run (`REGRESSION`, `SCHEMA_INVALID`, and `PROVIDER_ERROR` -- the
evaluation harness's default provider is `ReplayProvider`, per
`apps.ai.evaluation.runner`, so a `PROVIDER_ERROR` during an evaluation
run *is* a replay failure). "Invariant failures" (the I1-I6 suite,
`apps.ai.evaluation.tests_invariants`, plus every Phase 7b-7f capability's
own `InvariantI2/I3*ConcreteProofTests`) has no equivalent: it is a
regular Django `TestCase` suite enforced as a CI merge gate, with no
runtime persistence layer at all.

**Alternatives considered:**

**A. Add a new `EvaluationResult.Outcome.INVARIANT_FAILURE` value that
nothing currently writes, so the dashboard has a field to bind to.**
Rejected: dead schema that misleadingly implies invariant failures are
runtime-observed when they are CI-observed. A field nothing ever
populates is worse than no field.

**B. `apps.ai.services.observability.evaluation_summary()` reports
`regressions`/`schema_failures`/`replay_failures` as real counts (recent-
run outcome breakdown, capped at the last 10 runs) plus a real per-run
`recent_runs` pass/fail trend (oldest-first, one entry per actually-
persisted `EvaluationRun` -- not interpolated/bucketed synthetic points),
and reports `invariant_suite` as a static documented pointer to the CI
suite, explicitly stating no historical trend is persisted for it**
(chosen).

**Decision: B.** Honest about what is and isn't runtime-observable, and
does not invent a metric where none is measured.

## Decision 4: 'ai' queue depth reads Redis directly (`LLEN`), distinct from `/healthz/worker`'s Celery control-plane `inspect().ping()`

`/healthz/worker` (Phase 5a) already proves worker liveness via a real
`celery inspect ping()` round trip. That call answers "is at least one
worker alive," not "how much work is backed up" -- `inspect()` has no
built-in backlog-depth primitive. `apps.ai.services.ops_health.
ai_queue_depth()` instead opens a direct Redis client
(`redis.from_url(settings.CELERY_BROKER_URL)`) and issues a read-only
`LLEN` against the `ai` queue key (the Celery+Redis transport's list-per-
queue convention) -- no task is consumed or acknowledged. Reported as
`status: "unknown"` (not a failure) when no broker is configured,
matching `/healthz/worker`'s own "not configured" handling rather than
inventing a different convention.

## Consequences

- `apps.ai.services.ops_health.ai_heartbeat_status()` becomes the single
  source of truth for reading `AI_HEARTBEAT_CACHE_KEY`;
  `apps.core.views.healthz_ai` now delegates to it instead of duplicating
  the cache-read/age-computation logic inline -- a small ownership fix
  (apps.ai owns its own cache key) alongside the new functionality, with
  `/healthz/ai`'s public response shape regression-tested unchanged.
- The dashboard's new Platform Admin AI widgets and Org Admin/Auditor AI
  Budget widget are pure presentation over these three endpoints -- no
  client-side aggregation, the same discipline the Phase 4g dashboard
  rewrite established for carbon metrics.
- Every operational number this milestone exposes was already being
  written by Phase 7a-7f; Phase 7g added zero new fields to any existing
  governed model and zero new migrations to any tenant-data table.
