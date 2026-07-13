# ADR 0005: LLM provider abstraction with schema enforcement at the gateway, not per-provider

- Status: Accepted
- Date: 2026-07-08
- Phase: 7a (AI Foundation & Governance Seam)

## Context

Phase 7 needs every AI capability (7b–7g) to return structured, machine-
usable output — an anomaly explanation, a factor suggestion, an assistant
answer with citations — never free-form prose a caller has to parse
hopefully. Two design questions follow directly: (1) should application
code depend on a specific vendor's SDK/features, or on a provider-agnostic
interface; (2) should structured-output enforcement live inside each
provider adapter (using each vendor's own native JSON-mode/tool-calling
feature) or in one shared place.

## Alternatives considered

**A. Provider-agnostic `LLMProvider` ABC; schema validation centralized in
the gateway** (chosen). `apps/ai/providers/base.py` defines a lowest-common-
denominator interface — `complete(request) -> response`, `capabilities()`.
Providers return raw text only. `apps.ai.services.gateway.invoke_ai()`
JSON-parses and validates every response against a versioned JSON Schema
(`apps/ai/schemas.py`) exactly once, in one place, regardless of which
provider produced it.

**B. Depend on a specific vendor's SDK directly, using its native
structured-output/tool-calling feature for schema enforcement.** Simpler
per-call (the vendor enforces its own guarantee), but couples every future
capability to one vendor's API shape and makes swapping providers (or
supporting a tenant's own key/model — the BYO seam) a rewrite of every
call site instead of a config change.

**C. Provider-agnostic interface, but each adapter enforces its own schema
validation internally** (e.g. `AnthropicProvider.complete()` returns an
already-validated dict). Keeps the ABC provider-agnostic but duplicates
validation logic per adapter and makes "no un-validated response is ever
usable" (invariant I1/I6) a property of N adapters instead of one function
— harder to guarantee, harder to audit.

## Decision

**Option A**, mirroring `apps.core.storage`'s established ABC + factory +
providers pattern exactly (`StorageService`/`get_storage_service()`).

1. **Provider-agnostic by construction.** `AnthropicProvider` (default,
   Claude Sonnet 5) and `OpenAIProvider` (built specifically to prove the
   abstraction is a real seam, not single-vendor ceremony with extra
   steps) both satisfy the same interface. Swapping the default provider,
   or letting one tenant run on a different model/key than another
   (`TenantAIPolicy.provider_override`/`model_override`), is a
   configuration change, never a call-site rewrite.
2. **One enforcement point for I1/I6** ("no un-validated response is ever
   usable"). `invoke_ai()` is the only code that calls `jsonschema.validate()`
   against a response body — a single, directly-testable guarantee
   (`apps.ai.tests_gateway.InvokeAISchemaValidationTests`) instead of a
   property that would need proving separately for every current and
   future adapter.
3. **`AICapability.STRUCTURED_OUTPUT`** is declared on every adapter
   (including `echo`) as a required capability for every Phase 7
   capability — not because the gateway calls a vendor's native
   structured-output feature, but as a forward-looking gate: a future
   adapter (e.g. a self-hosted/BYO model) that genuinely cannot follow a
   "respond with JSON matching this schema" instruction acceptably is only
   eligible for capabilities that don't require it, of which Phase 7 has
   none today.
4. **A vendor SDK is never imported outside its own adapter file**
   (`apps.ai.tests_import_guard`, an AST-scanning test — see that module's
   own docstring for why a test rather than a new ruff rule). This is what
   makes "the gateway is the sole enforcement point" more than a
   convention: there is no code path by which a caller could reach a
   vendor SDK and bypass schema validation, cost metering, or the audit
   write.

## Consequences

- Every Phase 7 capability's response is a validated object, not raw
  prose — even the assistant (7e) and report narrative (7f) capabilities,
  whose "free text" always lives inside a typed field of a validated
  envelope (e.g. `{answer: str, citations: [...], ...}`), never as the
  entire response.
- A response that fails validation is recorded (`outcome=SCHEMA_INVALID`,
  its real cost still counted — the provider was actually called and
  billed) but its parsed body is discarded, never returned to a caller.
  This is a deliberate, visible failure mode, not silent data loss: the
  `AIInteraction` row is the audit trail for "the model was asked and
  didn't answer usably."
- A provider that cannot reliably produce schema-conformant JSON will show
  up as a high `SCHEMA_INVALID` rate in `AIInteraction` — a real,
  measurable signal for Phase 7a.5's eval harness to catch, not a silent
  quality regression.
- The lowest-common-denominator interface means Phase 7 never depends on
  provider-specific structured-output/function-calling features directly;
  a future capability that genuinely needs one (e.g. multi-step tool use)
  would need this interface extended, not worked around — an explicit,
  reviewable change, not an accidental one.
