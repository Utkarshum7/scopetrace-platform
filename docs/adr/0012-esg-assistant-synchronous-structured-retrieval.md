# ADR 0012: ESG Assistant is synchronous, retrieval is deterministic (not a vector store), and AIConversation is a plain container

- Status: Accepted
- Date: 2026-07-08
- Phase: 7e (ESG Assistant / RAG)

## Context

Phase 7e is the fourth real Phase 7 capability, and the first with a
genuinely different shape from 7b/7c/7d: it's conversational (multi-turn),
user-initiated (not triggered by a pipeline event), and has no single
governed record to attach advisory output to. Four structural questions
have to be answered before writing any code: (1) does "RAG" require new
retrieval infrastructure; (2) does asking a question run synchronously or
go through the `ai` queue like every prior capability; (3) how is a
multi-turn conversation persisted, given the milestone's "messages must be
immutable" requirement; and (4) whether the ESG_ASSISTANT_V1 placeholder
schema needed to change.

## Decision 1: "RAG" means deterministic, structured retrieval — not a vector store

**Alternatives considered:**

**A. Structured retrieval against existing, already tenant/RBAC/soft-
delete/approval-aware services** (chosen) —
`apps.ai.services.esg_context_builder` queries `MetricsService.summary()`
(the same dashboard aggregation), the compliance-report's APPROVED-only
query pattern, `UploadBatch`, and `EmissionFactorDataset`, and formats the
results into a labeled text block substituted into the prompt's
`$context` placeholder.

**B. A real embedding/vector-search pipeline** (chunk documents, embed,
store in a vector index, similarity-search at query time). Rejected: this
platform has no vector store today, and introducing one is genuinely new
infrastructure, not a reuse of anything — this milestone's own
integration requirement ("reuse the existing AI Gateway... no duplicate
infrastructure") argues directly against it. ScopeTrace's underlying data
is structured (rows in Postgres), not a corpus of unstructured documents
a vector index is designed for; a SQL query already retrieves the exact,
correct rows a similarity search would only approximate.

**Decision: A.** Every figure in the built context comes from a query
this codebase already trusts elsewhere for the SAME data (the dashboard,
the compliance report) — never a new, parallel query with its own
tenant-scoping bugs to introduce. Tenant isolation is structural, not a
filter this module has to get right on its own: every retrieval function
takes `organization` as a required, explicit parameter.

## Decision 2: `ask_esg_assistant()` is synchronous, not Celery-queued

**Alternatives considered:**

**A. Synchronous, in the request/response cycle** (chosen) — the same way
`invoke_ai()` already behaves when called directly (no queue exists
between a Python caller and the gateway; queuing is something a CALLER
chooses to do). `apps.ai.views.AIConversationViewSet.ask` calls
`ask_esg_assistant()` inline and returns the answer (or a null-answer
response) in the same HTTP response.

**B. Fire-and-forget on the `ai` queue, mirroring 7b/7c/7d exactly** —
dispatch a task, have the frontend poll for the answer. Rejected: those
three capabilities are background ENRICHMENT of an already-committed
pipeline event (a record finished ingesting or calculating whether or not
AI ever explains it) — there is no user waiting on the other end of that
dispatch. Asking a question is fundamentally different: a human is
waiting, in the UI, for THIS answer, to THIS question, right now. Queuing
it would mean either blocking the UI on a poll loop (worse UX than just
awaiting the HTTP response) or returning immediately with nothing useful
to show. Nothing in the existing ingest → calculate pipeline is at risk
either way — this capability was never in that pipeline to begin with, so
the milestone's "never increase ingest → calculate latency" requirement
is satisfied by construction, not by choosing to queue.

**Decision: A.** The USER's question is persisted unconditionally,
independent of whether the AI call succeeds — a chat transcript that
silently drops questions on a failed answer would be confusing, not safe
(I6's fail-safe principle applies to the ANSWER, not the record of what
was asked).

## Decision 3: only `AIConversationMessage` is immutable; `AIConversation` is a plain container

**Alternatives considered:**

**A. `AIConversationMessage` gets the full `AuditTrail`-style guard
(clean/delete/save overrides + QuerySet-level bulk-update/delete block,
all-PROTECT FKs); `AIConversation` itself is a normal, un-guarded model**
(chosen).

**B. Guard both models identically.** Rejected: `AIConversation` is a
grouping row, not advisory output — there is nothing on it that needs
protecting from mutation the way an AI-generated explanation or
recommendation does. Guarding it anyway would force `AIConversation.user`
into `on_delete=PROTECT` (the ADR 0009 lesson: a guarded model's FKs must
never be `SET_NULL`, or a user deletion's cascade update gets blocked by
the model's own guard) — permanently blocking deletion of any user who
ever started a conversation, for no corresponding safety benefit. Leaving
`AIConversation` un-guarded lets `user` stay `on_delete=SET_NULL`, exactly
matching `AIInteraction.actor`'s own established nullable pattern.

**Decision: A.** The immutability guarantee the milestone actually asks
for ("messages must be immutable") is met precisely, without over-
extending it to a container model where it would create a new deletion
hazard.

## Decision 4: `ESG_ASSISTANT_V2`'s schema fields are unchanged from v1

Unlike `anomaly_detection`, `factor_recommendation`, and
`validation_assistance` — whose v1 placeholder schemas each had a real
flaw the real capability's v2 schema had to fix (a classification field
that let AI decide instead of explain, a raw-identifier field an LLM
can't reliably reproduce, a single-field-pair shape that didn't match a
whole record's `validation_errors`) — `ESG_ASSISTANT_V1`'s fields
(`answer`, `citations`, `confidence`, `unsupported_claim`) were already
the right shape for a RAG-style assistant: a citation list and a
machine-checkable "did I actually support this" flag are exactly ADR
0005's "machine-checkable, not just readable" principle applied to a
free-text answer. `ESG_ASSISTANT_V2` is therefore field-for-field
identical to v1. The version bump still happens — purely so every Phase 7
capability's "placeholder generation vs real generation" semantics stay
consistent (a v2 fixture directory, a v2 `CapabilityConfig` entry) — not
because the JSON contract itself needed fixing this time.

## Consequences

- A future capability that also needs structured, multi-source retrieval
  (e.g. report narration pulling from several report sections at once)
  has a clear precedent: a dedicated context-builder module reusing
  existing services, not a new query layer or a vector store.
- `AIConversation` can be safely deleted or have its `user` nulled out by
  ordinary Django cascade behavior; only its child `AIConversationMessage`
  rows are permanent once created.
- `ask_esg_assistant()` being synchronous means its latency is real,
  user-visible latency (an LLM call inside an HTTP request) — acceptable
  for a live chat feature, but a future milestone that wants a heavier or
  slower retrieval step should reconsider whether that step specifically
  (not the whole capability) belongs on the `ai` queue instead.
- `CanUseAI` — defined in Phase 7a, unused until now — has its first real
  endpoint to protect, proving the RBAC gate genuinely works end to end,
  not just in isolation.
