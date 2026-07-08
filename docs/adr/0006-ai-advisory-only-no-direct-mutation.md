# ADR 0006: AI is advisory-only -- no code path exists for apps.ai to mutate governed data

- Status: Accepted
- Date: 2026-07-08
- Phase: 7a (AI Foundation & Governance Seam)

## Context

ScopeTrace is an audited ESG governance product: Phase 6 built a
hash-chained audit trail, immutable record versioning, and a fixed
Draft→Submitted→Approved workflow specifically so every change to governed
emissions data is attributable, reviewable, and tamper-evident. Phase 7
introduces AI-generated content (anomaly explanations, factor
recommendations, validation suggestions, assistant answers, report
narratives) into this system. The central design question: how is "AI
output must never become a certified business fact without a human
decision" actually guaranteed — as a coding convention every future
feature milestone has to remember, or as something structurally impossible
to violate by accident?

## Alternatives considered

**A. Structural enforcement by absence — `apps.ai` has no import of, and no
write path to, `EmissionRecord`/`EmissionCalculation`** (chosen). Every
capability that wants an AI-derived value to become real business data
must go through the *existing* governed workflow
(`apps.ingestion.services.workflow.transition_record()` for status changes,
the calculation recalculation path for factor/mapping changes) — never
through `apps.ai` directly. A future `AISuggestion` model (7c+) is a
proposal a human acts on through those existing paths, never a write
target of its own.

**B. A runtime guard/decorator that checks "is this write AI-initiated" and
blocks it.** Requires every governed model's save path to know about
`apps.ai` and carry AI-awareness logic, the inverse of the "advisory,
bolted onto governed data non-invasively" goal — and a decorator can be
forgotten on a new write path in a way an absent import cannot.

**C. A permission/RBAC check that blocks AI-initiated requests from
mutating records.** Doesn't address service-layer or Celery-task code
paths that don't go through DRF permission classes at all, and conflates a
role-based access question with a data-integrity one.

## Decision

**Option A.**

1. **No import, therefore no capability.** `apps/ai/services/gateway.py`
   (the sole enforcement choke point for every AI call, ADR 0005) has zero
   import of `apps.ingestion.models` or `apps.carbon.models`. This isn't a
   convention documented in a docstring — it's structurally true of the
   module's source, and `apps.ai.tests_gateway.InvokeAINoGovernedDataMutationTests`
   proves it by AST-scanning the gateway's own imports, not by asserting
   on behavior that would only catch a mutation attempt after the fact.
2. **Advisory output lands in new, apps.ai-owned tables only.** `AIInteraction`
   is a call-audit record; a future `AIAnnotation`/`AISuggestion` (7b/7c)
   will be an apps.ai model with, at most, a loose reference to a record's
   id — never a foreign key that Django could cascade a write through, and
   never a table any governed model reads at save time.
3. **Human decisions on AI advice go through existing, unchanged
   governance.** Accepting a factor recommendation (7c) triggers the same
   Org-Admin mapping-and-recalculate flow a manual correction would;
   accepting an anomaly explanation doesn't change `is_suspicious` (an
   ingestion-time governed field) at all. The AI layer proposes; the
   *existing* Phase 6 machinery — `AuditTrail`, `EmissionRecordVersion`,
   `bump_calc_version` — is what actually records the human's action, all
   already covered by that machinery's own test suite. `apps.ai` doesn't
   need its own copy of audit/versioning logic because it never triggers a
   governed write.
4. **Cache invalidation follows the same "advisory doesn't touch business
   state" logic.** An `AIAnnotation` is not a calculation change, so it
   must not (and, given the point above, structurally cannot) call
   `bump_calc_version` — a deliberate non-integration, not an oversight.

## Consequences

- A future feature milestone that *wants* AI to be more than advisory
  (e.g. one-click "accept and apply") still cannot skip the governed
  workflow — accepting a suggestion is defined as *performing the existing
  human action*, with the AI layer having only ever proposed inputs to it.
  This is deliberate: the bar for ever letting AI write governed data
  directly is "redesign this ADR with its own review," not "one more
  feature milestone slips it in."
- Every AI-adjacent governance question (who suggested this, when, from
  which model/prompt version, and who accepted or dismissed it) is
  answerable from `AIInteraction` plus the *existing* `AuditTrail` entry
  the human's own action already produces — no new audit primitive needed
  for Phase 7's advisory features.
- This ADR only covers *direct* mutation via `apps.ai`'s own code. It does
  not, and cannot, prevent a human from making a bad decision after
  reading AI-generated advice — that risk is addressed by the review UI
  and RBAC (7b+), not by this architectural boundary.
