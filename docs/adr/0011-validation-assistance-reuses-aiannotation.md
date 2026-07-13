# ADR 0011: Validation assistance reuses AIAnnotation, dispatches from ingest_task, and targets FAILED records only

- Status: Accepted
- Date: 2026-07-08
- Phase: 7d (AI Validation Assistant)

## Context

Phase 7d is the third real Phase 7 capability. Three structural questions
have to be answered before writing any code: (1) which deterministic
outcome should trigger validation assistance, given `EmissionRecord.
RecordStatus` has both `FAILED` (validation-time, unrecoverable) and
`SUSPICIOUS` (also validation-time, but already Phase 7b's territory);
(2) where does the dispatch happen, given 7c just established a
calculation-time dispatch pattern that doesn't apply here; and (3) whether
the persistence layer should be a new model (as 7c decided) or a reuse of
`AIAnnotation` (as 7b established).

## Decision 1: only `RecordStatus.FAILED` is in scope, not `SUSPICIOUS`

**Alternatives considered:**

**A. Target only `FAILED`** (chosen) -- the deterministic validator
(`apps.ingestion.services.validator.RowValidator`) already decided this
row is unrecoverably bad and excluded it from calculations
(`ValidationResult.mark_failed`). This is genuinely "explain a validation
failure and suggest a correction," matching the milestone's own framing.

**B. Also target `SUSPICIOUS`.** Rejected -- `SUSPICIOUS` is a different,
already-handled deterministic outcome: the row IS included in
calculations, just flagged for analyst review (`ValidationResult.
mark_suspicious`), and Phase 7b's `anomaly_detection` capability already
explains exactly that outcome. Treating `SUSPICIOUS` as a validation
"failure" too would duplicate 7b's own scope and produce two AI
explanations of the same deterministic decision from two different
capabilities -- confusing, not additive.

**Decision: A.** `generate_validation_assistance()` has no explicit
status check the way `recommend_emission_factor()` does (there is no
`resolution_status`-style ambiguity here), but `generate_validation_
assistance_task` structurally never queries a `SUSPICIOUS` row into
this capability's path -- see the task's `status=FAILED` filter.

## Decision 2: dispatch from `ingest_task`, not `calculate_task`

**Alternatives considered:**

**A. Dispatch a new task from `ingest_task`'s success path, alongside
`generate_anomaly_explanations_task`** (chosen) -- `status=FAILED` is
decided entirely at ingestion time (`IngestionService`/`RowValidator`),
before `calculate_task` ever runs. There is nothing calculation-specific
this capability needs to wait for.

**B. Mirror 7c's `calculate_task` dispatch instead**, since it's the most
recently established pattern. Rejected -- `factor_recommendation` (7c)
genuinely needed calculation-time information (`EmissionCalculation.
resolution_status`, candidate `EmissionFactor` rows), which don't exist
until `calculate_task` runs. `validation_assistance` needs none of that;
waiting for calculation to finish first would add a pointless dependency
and delay an explanation that's already fully determinable at ingest time.

**Decision: A.** `generate_validation_assistance_task` is dispatched as a
sibling of `generate_anomaly_explanations_task` in `ingest_task`'s success
path -- both are validation-time capabilities; the dispatch POINT should
match the dispatch point of the deterministic decision each explains, not
just copy the most recent precedent.

## Decision 3: reuse `AIAnnotation`, do not create a third model

**Alternatives considered:**

**A. Reuse `AIAnnotation`, add `VALIDATION_ASSISTANCE` as a second
`Capability` choice** (chosen) -- every output this milestone asks for
(explanation of the issue, likely cause, suggested correction, confidence,
affected fields) maps onto `AIAnnotation`'s existing four columns with no
type mismatch: `explanation` (issue + likely cause, in prose, matching how
`explanation`'s "why" framing already generalizes for anomaly_detection),
`contributing_factors` (repurposed as the list of affected field names --
still a list-of-strings, same structural role), `confidence`, and
`suggested_investigation` (repurposed as the suggested correction -- still
"what a human should do next", just capability-specific in what "next"
means).

**B. A new dedicated model, `AIValidationAssistance`**, mirroring 7c's
`AIFactorRecommendation` precedent. Rejected -- unlike `factor_
recommendation`, which needed a structurally NEW field (a nullable FK to
`EmissionFactor`, a relationship `AIAnnotation` has no equivalent for),
`validation_assistance`'s entire output is plain text/list/string,
exactly the shape `AIAnnotation` already has. ADR 0010's own reasoning
against reusing `AIAnnotation` for `factor_recommendation` was specifically
that bolting on a field only some capabilities populate turns the model
into a union type with unrelated `NULL` columns -- that concern doesn't
apply here, since no new column is needed at all. Adding a third,
structurally-identical model would be the premature-abstraction mistake
this project's own conventions explicitly warn against, not a defensible
extension.

**Decision: A.** `AIAnnotation.Capability.VALIDATION_ASSISTANCE` reuses
every existing field, index, admin registration, immutability guard, and
API endpoint (`GET /api/records/{id}/ai-annotations/`) with zero new
migration beyond the `Capability` choice itself. The `Capability`-indexed
query (`.exclude(ai_annotations__capability=...)`) that made this reuse
practical for idempotent task dispatch was already anticipated by
`AIAnnotation`'s own `Meta.indexes = [..., ("record", "capability",
"-created_at")]`, set when the model was first designed in Phase 7b.

## Consequences

- A future capability whose output is a plain text/list/string shape
  (matching `AIAnnotation`'s four columns) should default to reusing
  `AIAnnotation` with a new `Capability` choice; a future capability that
  needs to reference a specific OTHER governed object (like 7c's
  `recommended_factor`) should default to a new dedicated model, per ADR
  0010. This ADR and ADR 0010 together are the reference pair for that
  decision going forward.
- The frontend's `AIInsightsPanel` splits one API response
  (`/ai-annotations/`) into two visually distinct sections by
  `capability` client-side -- a new capability sharing `AIAnnotation`'s
  shape is a frontend-only change (a new filter + section), no backend
  endpoint change required.
- `generate_validation_assistance_task` and
  `generate_anomaly_explanations_task` both run from the same `ingest_
  task` success path on the same `ai` queue -- a batch with both
  suspicious and failed rows triggers both tasks independently; neither
  can block or fail the other, matching ADR 0009's original fire-and-
  forget reasoning.
