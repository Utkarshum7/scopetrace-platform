# AI Architecture (`AI_ARCHITECTURE.md`)

Phase 7a — the AI Foundation & Governance Seam. `apps.ai` exists so every
later AI feature (7b–7g: anomaly detection, factor/activity recommendation,
validation assist, an ESG assistant, report narrative generation) plugs into
one governed, metered, audited choke point instead of each reinventing
provider calls, cost tracking, and safety controls. **No AI feature is
implemented in this milestone** — the pipeline's `AIRecommendationStage`/
`OptimizationStage` (reserved since Phase 3, see
[`CARBON_ENGINE_DESIGN.md`](CARBON_ENGINE_DESIGN.md)) remain inert. This
document describes the foundation those future milestones build on.

---

## 1. Invariants (non-negotiable, enforced structurally)

| # | Invariant | How it's enforced |
|---|---|---|
| I1 | **Advisory by default** | No un-validated provider response is ever usable — schema validation happens once, in the gateway, before anything touches the response body. |
| I2 | **No direct mutation of governed data** | `apps.ai` has no import of, and no write path to, `apps.ingestion.models.EmissionRecord` or `apps.carbon.models.EmissionCalculation`. Proved by `apps.ai.tests_gateway.InvokeAINoGovernedDataMutationTests` (an AST scan of `gateway.py`'s own imports), not just documented. A future feature that wants an AI suggestion to become a real business-data change must go through the *existing* governed workflow (`apps.ingestion.services.workflow` / the calculation recalculation path) — never through `apps.ai`. |
| I3 | **Tenant isolation extends to prompts and retrieval** | Every `invoke_ai()` call is scoped to one `organization`; `AIInteraction.organization` is a required FK, budget checks and idempotency lookups are always org-scoped queries. |
| I4 | **Provider-agnostic** | Application code depends only on `apps.ai.providers.base.LLMProvider` and `apps.ai.services.gateway.invoke_ai()` — never a vendor SDK directly. Enforced by `apps.ai.tests_import_guard` (an AST scan of the whole `apps/ai/` tree). |
| I5 | **Everything audited + metered** | Every call through `invoke_ai()` — including one refused before reaching a provider — writes exactly one `AIInteraction` row. |
| I6 | **Fail-safe, not fail-open** | A malformed response, missing schema, disabled tenant, exhausted budget, or blocked egress tier all degrade to a clean, recorded refusal — never a crash, never a silently-accepted invalid result. |

---

## 2. Module layout

Mirrors `apps/core/storage/`'s ABC + factory + providers pattern exactly —
the same shape this codebase already uses for a provider-agnostic external
dependency.

```
apps/ai/
  models.py                AIPromptVersion, AIInteraction, TenantAIPolicy
  schemas.py                Response JSON-Schema registry
  admin.py
  tasks.py                  ai_heartbeat_task ('ai' queue)
  providers/
    base.py                 LLMProvider ABC, LLMRequest/LLMResponse, AICapability
    factory.py               get_llm_provider() — lazy per-branch imports
    echo.py                  deterministic, zero-egress dev/test provider
    anthropic.py             sole file permitted to `import anthropic`
    openai.py                sole file permitted to `import openai`
  prompts/
    registry.py              render_prompt() — versions + hashes
    templates/*.txt
  services/
    gateway.py               invoke_ai() — the single enforcement choke point
    policy.py                 per-tenant AI policy resolution
    cost.py                    token→$ estimation, monthly budget check
    egress.py                  provider-allowed check + PII redaction
  tests_*.py                 32 tests_* files' worth of coverage (see §7)
```

---

## 3. The gateway (`invoke_ai()`)

`apps.ai.services.gateway.invoke_ai()` is the **sole** enforcement point.
Every call flows through the same fixed sequence:

```
idempotency short-circuit
  -> policy resolution (global kill switch + per-tenant opt-in)
  -> budget check
  -> egress / provider-allowed check
  -> response schema lookup
  -> redact + render prompt
  -> provider.complete()
  -> response schema validation
  -> write exactly one AIInteraction row
```

A call refused at any step before "provider.complete()" **never reaches a
provider and never costs anything**, but still writes an `AIInteraction`
row (outcome `AI_DISABLED` / `BUDGET_EXCEEDED` / `EGRESS_BLOCKED` / `ERROR`)
for observability. A response that fails schema validation still records
its real cost — the provider was actually called and billed — but its
`parsed` body is discarded and never returned to the caller (`SCHEMA_INVALID`).

No caller constructs a provider, calls `apps.ai.prompts.registry` directly,
or writes `AIInteraction` — this is the only module that composes those
pieces, and `apps.ai.tests_import_guard` structurally prevents a caller
from reaching a vendor SDK around it.

**Idempotency** (`idempotency_key`) exists to stop a redelivered Celery task
(this codebase's `ACKS_LATE` at-least-once contract) from re-billing the
provider — it is not a data cache. A replayed call returns the prior
outcome and `interaction_id` but no parsed body, since `AIInteraction`
deliberately never persists raw response text (see §4). A feature that
needs to recover a prior result's *data* on redelivery looks it up in its
own table (e.g. `AIAnnotation`/`AIFactorRecommendation`, both keyed back
to the `AIInteraction` that produced them), not from the gateway.

---

## 4. Reproducibility metadata

Every `AIInteraction` row captures the complete set needed to reconstruct
what was asked and audit what happened, without persisting raw
tenant-derived content at rest:

| Group | Fields |
|---|---|
| Provider/model | `provider`, `model_id`, `model_snapshot`, `provider_request_id` |
| Prompt | `prompt_version` (FK), `prompt_template_hash`, `rendered_input_hash` |
| Context | `context_provenance` (record/metric ids that formed the prompt) |
| Parameters | `parameters` JSON: model, temperature, top_p, max_tokens, seed, stop, response_schema_id/version |
| Output | `response_hash`, `schema_valid`, `outcome`, `error_detail` (sanitized) |
| Economics | `input_tokens`, `output_tokens`, `cost_usd`, `latency_ms` |
| Governance | `egress_tier_applied`, `redaction_applied`, `idempotency_key`, `gateway_version` |

Only **hashes** of the rendered prompt and response are stored, never the
raw text — the same content-addressed pattern `AIPromptVersion` itself
uses. `AIPromptVersion` is the registry of *what could have been asked*
(one row per distinct template content, the AI analog of
`EmissionRecordVersion`); `AIInteraction` is the record of *what actually
happened* on one call.

---

## 5. Provider abstraction

`LLMProvider` (`apps/ai/providers/base.py`) is a lowest-common-denominator
interface: `complete(request) -> response`, `capabilities()`. Providers
return **raw text only** — JSON parsing and schema validation happen once,
in the gateway, not per-provider. This keeps every adapter uniform and
means schema enforcement doesn't depend on any one vendor's native
structured-output feature.

- **`echo`** — deterministic, zero-egress, zero-cost. Default in
  `DEBUG`/tests. Its non-canned response satisfies no real schema by
  design (it exists to prove determinism/hashing, not to satisfy arbitrary
  shapes); tests asserting a specific outcome embed an exact canned
  response via `apps.ai.providers.echo.canned()`.
- **`anthropic`** — the default production provider (Claude Sonnet 5).
  Fails fast at construction (`ImproperlyConfigured`) on a missing API key
  — zero network I/O, so `/healthz/ai/` can detect misconfiguration cheaply.
- **`openai`** — exists to prove the abstraction is a real seam, not
  single-vendor ceremony (the same reason `StorageService` ships both
  `local` and `s3`).

Adding a new provider (a self-hosted/BYO model) is additive: one new class
under `providers/`, one new branch in `factory.py`. **No self-hosted/BYO
adapter is implemented in 7a** — `TenantAIPolicy.byo_api_key_ref` and the
`NO_EGRESS` tier are the seam; a concrete adapter is deferred until a
tenant actually needs it (per the finalized Phase 7 design's decision #2).

---

## 6. Policy, cost, and egress

**Policy** (`services/policy.py`) resolves in a fixed order: the global
`AI_ENABLED` kill switch, then a per-organization `TenantAIPolicy` row. A
missing row, or one with `ai_enabled=False`, always resolves to disabled —
an org must explicitly opt in, never inherit "the platform default
provider is on" implicitly.

**Cost** (`services/cost.py`) uses a small, hand-maintained
`(provider, model) -> $/1K tokens` table — never fetched from a vendor
pricing API. `check_budget()` sums every interaction with a recorded cost
in the current calendar month, regardless of outcome: a `SCHEMA_INVALID`
response still consumed real, billable tokens and must still count.

**Egress** (`services/egress.py`) has two independent jobs:
1. `enforce_provider_allowed()` — is the resolved provider even reachable
   under this tenant's egress tier? `NO_EGRESS` permits only zero-egress
   providers (today: `echo` only).
2. `redact_template_vars()` — under the platform-default `REDACTED` tier,
   scrubs common PII-shaped patterns (email addresses, long digit
   sequences) from tenant-derived `template_vars` *before* rendering, so
   the recorded hash reflects what was actually sent. `RAW` is an explicit
   opt-in that skips redaction entirely.

---

## 7. Async processing

A new, fifth Celery queue, `ai` — AI work is bursty and vendor-rate-limited
in a way none of the existing queues (`celery`/`ingestion`/`calculation`/
`maintenance`/`notifications`) are, so it gets its own routing seam from
day one (the same split Phase 5d's own routing comment already anticipated
for AI enrichment). One worker pool consumes all six queues today
(`docker-compose.yml`'s `-Q` list) — dedicating a pool to `ai` specifically
is a zero-code-change future option, same story as every queue before it.

`apps.ai.tasks.ai_heartbeat_task` (Celery Beat, every 5 minutes) only
constructs the configured provider adapter — config/credential presence,
**zero network I/O, zero cost**. A full end-to-end provider round trip on a
fixed schedule would be a real, billable cost with no feature yet using
it; that's deliberately deferred until a real capability (7b+) exists to
piggyback its cost on.

---

## 8. Operational health — `/healthz/ai/`

Lives in `apps.core.views` alongside `/healthz` and `/healthz/worker/`
(apps.core owns cross-cutting health/infra concerns). Returns **200 when
`AI_ENABLED=False`** — a deliberately-disabled feature is expected, healthy
state, not a failure that should page anyone. When enabled, checks only
that the configured provider adapter can be *constructed* (no network
call); `ai_heartbeat_task`'s last result is surfaced as additive
`ai_heartbeat` context, never the authoritative pass/fail signal — same
pattern as `/healthz/worker/`'s `beat_heartbeat`.

---

## 9. RBAC

- **`CanUseAI`** — a pure role-gate (`ORG_ADMIN`/`ANALYST`/`AUDITOR`,
  mirrors `ROLES_CAN_APPROVE`'s set). Deliberately does **not** check
  whether AI is enabled for the organization — that's
  `TenantAIPolicy.ai_enabled`, resolved by `resolve_policy()` inside the
  gateway itself, the same separation `CanApprove` already has from
  `apps.ingestion.services.workflow`'s own state checks. A caller with this
  permission but an AI-disabled org still reaches the gateway and gets a
  clean `AI_DISABLED` outcome, not a bare 403.
- **`CanManageAIPolicy`** — Org Admin only; who can edit `TenantAIPolicy`.
- **`CanViewAICosts`** — Org Admin + Auditor, mirrors `CanViewActivity`.

No AI-specific DRF endpoint exists yet in 7a to attach these to — they are
tested directly against the permission classes (`apps.ai.tests_permissions`)
and become real end-to-end API-level RBAC once 7b+ adds the first endpoint.

---

## 10. What's explicitly NOT in Phase 7a (updated as later milestones land)

No report narrative generation — it remains a later milestone (7f) that
calls `invoke_ai()`, never reimplements any part of this foundation. Also
still not implemented (per the finalized Phase 7 design):
- A concrete self-hosted/BYO provider adapter (the seam exists; no adapter).
- ~~The AI evaluation/golden-set harness~~ — done in **Phase 7a.5**, see
  [`AI_EVALUATION.md`](AI_EVALUATION.md).
- ~~Anomaly detection, any `AIAnnotation` model~~ — done in **Phase 7b**:
  `apps.ai.services.anomaly_detection` (capability `anomaly_detection`),
  `AIAnnotation` (immutable, PROTECT-only FKs), a read-only
  `GET /api/records/{id}/ai-annotations/` endpoint, and a frontend "AI
  Insights" panel. See §12 and ADR 0009.
- ~~Factor/activity recommendation, any `AIFactorRecommendation` model~~ —
  done in **Phase 7c**: `apps.ai.services.factor_recommendation`
  (capability `factor_recommendation`), `AIFactorRecommendation`
  (immutable, PROTECT-only FKs, nullable `recommended_factor`), a
  read-only `GET /api/records/{id}/factor-recommendations/` endpoint, and
  a second sub-section in the same "AI Insights" panel. See §13 and ADR
  0010.
- ~~Validation assistance~~ — done in **Phase 7d**:
  `apps.ai.services.validation_assistance` (capability
  `validation_assistance`), reusing `AIAnnotation` with a second
  `Capability` choice rather than a new model, surfaced through the
  SAME `GET /api/records/{id}/ai-annotations/` endpoint and a third
  sub-section in the same "AI Insights" panel. See §14 and ADR 0011.
- ~~ESG Assistant, any `AIConversation`/`AIConversationMessage` model~~ —
  done in **Phase 7e**: `apps.ai.services.esg_assistant` (capability
  `esg_assistant`) plus `apps.ai.services.esg_context_builder` (structured
  retrieval, not a vector store), `AIConversation`/`AIConversationMessage`
  (only messages immutable), apps.ai's own first API views
  (`/api/esg-assistant/conversations/...`), and a new dedicated ESG
  Assistant page. See §16 and ADR 0012.

---

## 12. Phase 7b — Advisory AI Anomaly Detection

The first real Phase 7 capability. `apps.ai.services.anomaly_detection.
generate_anomaly_explanation(record)` builds prompt context from an
ALREADY-suspicious `EmissionRecord` — scope, source type, normalized
quantity, and (the actual evidence) the deterministic engine's own
`validation_errors`, formatted verbatim — calls `invoke_ai()`, and
persists exactly one immutable `AIAnnotation` on success. AI never
classifies (`ANOMALY_DETECTION_V2`'s schema has no `is_anomalous` field,
unlike the Phase 7a.5 placeholder v1); the deterministic engine
(`apps.ingestion.services.validator.RowValidator`) already decided a
record is suspicious before AI is ever invoked.

**Dispatch is fire-and-forget, off the deterministic pipeline entirely** —
`apps.ai.tasks.generate_anomaly_explanations_task` (the `ai` queue) is
dispatched from `ingest_task`'s success path with the same one-line
`.delay()` pattern already used for `send_notification_task`, never
inline in the synchronous calculation pipeline's reserved
`AIRecommendationStage` seam (still inert). See ADR 0009 for the full
reasoning and the alternatives considered.

**Read path**: `GET /api/records/{id}/ai-annotations/`, mirroring the
existing `/versions/` action's exact `self.get_object()` tenant-scoping
precedent — no mutation verb exists on this path. The frontend's
`AIInsightsPanel` (in `RecordsPage.jsx`'s existing detail drawer, no page
redesign) renders whatever this endpoint returns, clearly labeled
"AI Advisory," and renders nothing when empty.

---

## 13. Phase 7c — AI Emission Factor Recommendation

The second real Phase 7 capability. `apps.ai.services.factor_recommendation.
recommend_emission_factor(record)` runs only against records whose current
`EmissionCalculation.resolution_status` is exactly `UNRESOLVED_NO_FACTOR`
— the deterministic engine (`apps.carbon.services.resolution.
ActivityTypeResolver`) already resolved an activity type, but
`FactorIndex.resolve()` found no single factor confidently matching its
region/date/publisher constraints. `UNRESOLVED_NO_ACTIVITY_TYPE` is
explicitly out of scope — a different problem (activity-type mapping), not
factor selection. See ADR 0010, Decision 1.

The service independently queries candidate `EmissionFactor` rows for the
resolved activity type (read-only; it neither imports nor modifies
`FactorIndex`, which only ever returns a single winner or `None`) and
shows them to the AI as labels — `candidate_1`, `candidate_2`, ..., or
`"none"` — never as raw UUIDs. The AI's response picks a label; the
service resolves it back to the real object it already holds in memory,
defensively resolving to no factor if the label is unrecognized. See ADR
0010, Decision 2, for why raw identifiers are never shown to the AI.

`FACTOR_RECOMMENDATION_V2`'s schema — `recommended_candidate_label`,
`confidence`, `explanation`, `reasoning`, `alternative_candidates` —
persists as exactly one immutable `AIFactorRecommendation` on success, a
new dedicated model (not a reuse of `AIAnnotation` — see ADR 0010,
Decision 3) with the same `AuditTrail`-style immutability and all-`PROTECT`
FK discipline ADR 0009 established. `recommended_factor` is nullable: the
AI recommending none of the candidates it was shown is a valid, honest
outcome, not a failure.

**Dispatch is fire-and-forget, off the deterministic pipeline entirely** —
`apps.ai.tasks.generate_factor_recommendations_task` (the `ai` queue) is
dispatched from `calculate_task`'s success path with the same one-line
`.delay()` pattern already used for `send_notification_task`, mirroring
7b's `ingest_task` → `generate_anomaly_explanations_task` dispatch exactly.

**Read path**: `GET /api/records/{id}/factor-recommendations/`, mirroring
`/ai-annotations/`'s exact `self.get_object()` precedent — no mutation
verb exists on this path. `AIFactorRecommendationSerializer` computes a
human-readable `recommended_factor_label` rather than exposing the raw FK.
The frontend's `AIInsightsPanel` gained a second sub-section rendering
whatever this endpoint returns, alongside the existing anomaly-annotation
sub-section, both clearly labeled "AI Advisory."

The milestone's explicit callout — "verify AI never changes the
deterministic factor" — has a formal, merge-gate-visible proof:
`InvariantI2FactorRecommendationConcreteProofTests` in
`apps/ai/evaluation/tests_invariants.py`, which confirms every field on
both the `EmissionCalculation` and the candidate `EmissionFactor` it
recommended is byte-identical before and after a successful call.

---

## 14. Phase 7d — AI Validation Assistant

The third real Phase 7 capability. `apps.ai.services.validation_assistance.
generate_validation_assistance(record)` runs against records whose
`EmissionRecord.status` is exactly `FAILED` — the deterministic validator
(`apps.ingestion.services.validator.RowValidator`) already excluded this
row from calculations. `SUSPICIOUS` is explicitly out of scope here — that
outcome is already Phase 7b's `anomaly_detection` territory. See ADR 0011,
Decision 1.

Unlike 7c, this capability reuses `AIAnnotation` rather than introducing a
third model: `VALIDATION_ASSISTANCE_V2`'s schema (`explanation`,
`affected_fields`, `confidence`, `suggested_correction`) maps onto
`AIAnnotation`'s existing four columns with no type mismatch —
`contributing_factors` holds affected field names, `suggested_
investigation` holds the suggested correction. See ADR 0011, Decision 3,
for why this differs from 7c's decision to build `AIFactorRecommendation`
as a dedicated model.

**Dispatch is fire-and-forget, off the deterministic pipeline entirely** —
`apps.ai.tasks.generate_validation_assistance_task` (the `ai` queue) is
dispatched from `ingest_task`'s success path, as a sibling of `generate_
anomaly_explanations_task` (not `calculate_task` — `FAILED` is a
validation-time decision, matching where it's decided; see ADR 0011,
Decision 2).

**Read path**: no new endpoint. Because this capability reuses
`AIAnnotation`, the existing `GET /api/records/{id}/ai-annotations/`
already returns `VALIDATION_ASSISTANCE` rows alongside
`ANOMALY_DETECTION` ones — the frontend's `AIInsightsPanel` splits them
client-side by `capability` into a third sub-section (Issue / explanation
/ Suggested fix / confidence), clearly labeled "AI Advisory," alongside
the existing anomaly and factor-recommendation sub-sections.

The milestone's explicit callout — "no record mutation, no validation
status changes, deterministic validator remains authoritative" — has a
formal, merge-gate-visible proof:
`InvariantI2ValidationAssistanceConcreteProofTests` in
`apps/ai/evaluation/tests_invariants.py`, which confirms every field on
the record (including `status` and `validation_errors`) is byte-identical
before and after a successful call, and that the record remains exactly
`RecordStatus.FAILED` afterward.

---

## 16. Phase 7e — ESG Assistant (RAG)

The fourth real Phase 7 capability, and the first with a genuinely
different shape: conversational (multi-turn), user-initiated (not
triggered by a pipeline event), with no single governed record to attach
advisory output to. See ADR 0012 for the four structural decisions this
section summarizes.

**Retrieval is deterministic, structured retrieval — not a vector
store.** `apps.ai.services.esg_context_builder.build_context(organization)`
queries `MetricsService.summary()` (the same dashboard aggregation),
the compliance-report's APPROVED-only query pattern (a separate,
audit-grade total), recent `UploadBatch` rows, and active
`EmissionFactorDataset` rows — every figure comes from a query this
codebase already trusts elsewhere for the same data. Tenant isolation is
structural: every query takes `organization` as a required, explicit
parameter.

**`ask_esg_assistant(conversation, question)` is synchronous, not
Celery-queued** — unlike 7b/7c/7d's fire-and-forget background
enrichment, a human is waiting in the UI for THIS answer to THIS
question. The USER's question is persisted unconditionally, independent
of whether the AI call succeeds; the ASSISTANT's answer (with
citations/confidence/unsupported_claim and the exact `retrieved_context`
shown to the AI) is persisted only on a successful `invoke_ai()` call.
This capability was never in the ingest → calculate pipeline, so "never
increase ingest → calculate latency" holds by construction.

**Persistence**: `AIConversation` (organization, user — a plain,
un-guarded container) and `AIConversationMessage` (immutable,
`AuditTrail`-style, all-`PROTECT` FKs). Only messages are immutable, not
the conversation container — see ADR 0012, Decision 3, for why that
split avoids a new deletion hazard on `AIConversation.user`.

**Read path**: apps.ai's own first API views (previously every AI
capability was surfaced through an `@action` on an existing
apps.ingestion viewset). `GET/POST /api/esg-assistant/conversations/`,
`GET .../{id}/messages/`, `POST .../{id}/ask/` — structurally read-only
except the two actions a conversational feature necessarily requires
(no Update/Destroy mixin anywhere). Gated by `CanUseAI`, unused since its
Phase 7a introduction until this milestone. The frontend gained a new,
dedicated ESG Assistant page (not a detail-drawer panel — a chat
interface warrants its own screen), showing the conversation, citations,
retrieved context, and confidence, every assistant response labeled "AI
Advisory."

The milestone's explicit callouts — "no governed data mutation," "no
cross-tenant retrieval," "RBAC enforcement" — have formal,
merge-gate-visible proofs: `InvariantI2EsgAssistantConcreteProofTests`
and `InvariantI3EsgAssistantConcreteProofTests` in
`apps/ai/evaluation/tests_invariants.py`.

---

## 17. Related documents

- [`ROADMAP.md`](ROADMAP.md) — Phase 7 milestone breakdown (7a–7g).
- [`AI_EVALUATION.md`](AI_EVALUATION.md) — Phase 7a.5's evaluation/
  regression framework: golden datasets, replay providers, the two-tier CI
  split, the formal I1–I6 invariant merge gate every future AI milestone
  must keep green.
- [`CARBON_ENGINE_DESIGN.md`](CARBON_ENGINE_DESIGN.md) — the pipeline's
  reserved `AIRecommendationStage`/`OptimizationStage` seams, still inert
  after Phase 7e (see ADR 0009 for why anomaly explanation didn't use them).
- [`docs/adr/0005-ai-provider-abstraction-and-schema-enforcement.md`](adr/0005-ai-provider-abstraction-and-schema-enforcement.md)
- [`docs/adr/0006-ai-advisory-only-no-direct-mutation.md`](adr/0006-ai-advisory-only-no-direct-mutation.md)
- [`docs/adr/0007-ai-tenant-egress-and-cost-policy.md`](adr/0007-ai-tenant-egress-and-cost-policy.md)
- [`docs/adr/0008-ai-evaluation-tiering.md`](adr/0008-ai-evaluation-tiering.md)
- [`docs/adr/0009-anomaly-explanation-async-dispatch-and-immutable-annotations.md`](adr/0009-anomaly-explanation-async-dispatch-and-immutable-annotations.md)
- [`docs/adr/0010-factor-recommendation-candidate-labels-and-dedicated-model.md`](adr/0010-factor-recommendation-candidate-labels-and-dedicated-model.md)
- [`docs/adr/0011-validation-assistance-reuses-aiannotation.md`](adr/0011-validation-assistance-reuses-aiannotation.md)
- [`docs/adr/0012-esg-assistant-synchronous-structured-retrieval.md`](adr/0012-esg-assistant-synchronous-structured-retrieval.md)
- [`docs/adr/0010-factor-recommendation-candidate-labels-and-dedicated-model.md`](adr/0010-factor-recommendation-candidate-labels-and-dedicated-model.md)
- [`docs/adr/0011-validation-assistance-reuses-aiannotation.md`](adr/0011-validation-assistance-reuses-aiannotation.md)
