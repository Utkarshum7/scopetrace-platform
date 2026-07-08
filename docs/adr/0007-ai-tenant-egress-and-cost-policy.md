# ADR 0007: Per-tenant AI egress tiers, redaction, and budget enforcement in the gateway

- Status: Accepted
- Date: 2026-07-08
- Phase: 7a (AI Foundation & Governance Seam)

## Context

ScopeTrace's customers are exactly the kind of buyer who will ask "does
our emissions data leave our infrastructure boundary" before ever asking
what an AI feature does. Real emissions data at the row level (a supplier
name, a facility, a note field) may contain sensitive operational detail.
Separately, calling a third-party LLM API costs real money per call, with
no natural ceiling unless the platform enforces one. Phase 7a has to
decide, before any feature ships: what data-egress guarantee does a tenant
get by default, and what stops a bug (or an unexpectedly chatty prompt) in
a future feature milestone from running up an unbounded bill.

## Alternatives considered

**A. Three tenant-selectable egress tiers (`REDACTED` default, `RAW`
opt-in, `NO_EGRESS`), redaction and provider-eligibility enforced in the
gateway; per-tenant monthly budget checked before every call** (chosen).
`TenantAIPolicy.egress_tier` and `.monthly_budget_usd` are resolved once,
in `apps.ai.services.policy.resolve_policy()`, and enforced by
`apps.ai.services.egress`/`cost` inside `invoke_ai()` before any provider
is even constructed for a refused call.

**B. No tiering — all tenants get the same (redacted) treatment, no
per-tenant override.** Simpler, but forecloses a real, sellable enterprise
requirement (a customer whose contract requires zero third-party data
egress) without a redesign later, and gives every tenant the same budget
ceiling regardless of their actual usage needs.

**C. Redaction/egress enforcement left to each feature milestone to
implement per-capability.** Would mean 7b–7f each reinvent (or, worse,
each slightly differently implement) the same PII-scrubbing and
provider-eligibility logic — exactly the kind of drift Phase 6's
architecture review found once already (the frontend/backend approval
workflow contract drift) and this milestone's own import-guard/gateway
design elsewhere exists to prevent.

## Decision

**Option A.**

1. **`REDACTED` is the platform default**, not `RAW` — the fail-safe
   choice matching `STORAGE_BACKEND`'s "safe by default, explicit opt-in
   for more" philosophy applied per-tenant rather than globally.
   `apps.ai.services.egress.redact_template_vars()` scrubs common
   PII-shaped patterns (email addresses, long digit sequences) from
   tenant-derived `template_vars` *before* they are rendered into a
   prompt, so the hash recorded on `AIInteraction` reflects what was
   actually sent, not a pre-redaction value.
2. **`NO_EGRESS` restricts to zero-egress providers only** (today: `echo`
   — a real self-hosted/BYO adapter is a documented, deferred seam, not a
   concrete provider in 7a; see `docs/AI_ARCHITECTURE.md` §5).
   `enforce_provider_allowed()` raises before rendering or calling
   anything if the tenant's resolved provider isn't in that set.
3. **`RAW` is an explicit, tenant-chosen opt-in** that skips redaction
   entirely — never the default, and never inferred from any other
   setting.
4. **Budget is checked before every call, not sampled or checked
   asynchronously.** `check_budget()` sums `AIInteraction.cost_usd` for
   the current calendar month and refuses the call
   (`outcome=BUDGET_EXCEEDED`) before a provider is even constructed if
   the org is at or over its resolved `monthly_budget_usd`. A
   `SCHEMA_INVALID` response still counts toward spend (the provider was
   actually called and billed for it); a refused call never does.
5. **`TenantAIPolicy.byo_api_key_ref` stores a reference (an env var name
   or secrets-manager path), never a raw key value** — consistent with
   this codebase's existing secrets handling, and keeping a tenant's own
   credential out of the database entirely.

## Consequences

- A tenant can be sold on "your data never leaves your infrastructure"
  today by setting `NO_EGRESS`, even though no concrete self-hosted
  provider ships in Phase 7a — the enforcement mechanism is real and
  tested (`apps.ai.tests_gateway.InvokeAIEgressTests`) even though the
  provider roster it can select from is currently just `echo`.
- Redaction is intentionally simple (regex-based, not a full PII-detection
  model) — a documented, acceptable trade-off for Phase 7a specifically
  because no real feature sends real tenant content through this path yet
  (see `apps.ai.services.egress`'s own module docstring). A feature
  milestone that needs richer redaction extends this module, not a
  parallel one.
- Budget enforcement is per-organization and resets on calendar-month
  boundaries with no rollover — a simple, predictable model chosen over a
  rolling-window or credit-carryover scheme, matching this milestone's
  "minimal, not comprehensive" mandate. Revisiting the model (proration,
  rollover, alerts before the ceiling) is a Phase 7g (cost governance)
  concern, not a 7a one.
- Every refusal (`AI_DISABLED`/`BUDGET_EXCEEDED`/`EGRESS_BLOCKED`) is still
  a recorded `AIInteraction` row — an org that keeps hitting its budget
  ceiling or egress restriction has a complete, queryable history of that,
  not just a support ticket.
