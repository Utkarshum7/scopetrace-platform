# ADR 0013: Report narration is built ONLY from approved data, dispatched async from a new API action, and RBAC-gated to match the report it narrates

- Status: Accepted
- Date: 2026-07-08
- Phase: 7f (AI Report Narration)

## Context

Phase 7f is the fifth and final planned real Phase 7 capability. Three
structural questions have to be answered before writing any code: (1)
which existing data-aggregation service should the narration's context
come from, given the milestone's explicit "approved, deterministic
platform data only" requirement; (2) how does an async, queue-based
capability get dispatched when there's no existing pipeline event to hook
into (compliance reports are on-demand, per ADR 0002); and (3) what RBAC
gate should protect the new endpoints.

## Decision 1: context comes from `compliance_summary()` + new APPROVED-only queries, never `MetricsService`

**Alternatives considered:**

**A. Reuse `apps.carbon.services.reports.compliance_summary()` directly
for the headline figures, and add two NEW queries (activity breakdown,
monthly trend) using the identical APPROVED-only filter shape
(`is_current=True`, `resolution_status=CALCULATED`,
`emission_record__status=APPROVED`)** (chosen).

**B. Reuse `apps.carbon.services.metrics.MetricsService`, the same way
`apps.ai.services.esg_context_builder` does for the general assistant.**
Rejected: `MetricsService` intentionally includes non-approved
(DRAFT/SUSPICIOUS/SUBMITTED/REJECTED) records for its dashboard use case
— appropriate for "what's the current state of everything," wrong for a
compliance report narrative, which must reflect ONLY certified data. The
milestone is explicit here in a way 7e's own scope wasn't: "narration
must be derived only from approved, deterministic platform data." Using
`MetricsService` would silently violate that constraint the first time an
org had any non-approved records in the period.

**Decision: A.** `compliance_summary()` is reused, not reimplemented, so
narration and the compliance report it narrates are structurally
guaranteed to agree — there is no second implementation of "what counts
as approved" to drift out of sync. `apps.ai.services.report_context_builder`
formalizes this reuse and adds the two new APPROVED-only queries
(activity breakdown, monthly trend) the compliance report endpoints
don't currently expose.

## Decision 2: async dispatch via a new API action, not a pipeline hook

**Alternatives considered:**

**A. `POST /api/report-narration/regenerate/` validates and dispatches
`generate_report_narration_task` on the `ai` queue, returning 202
immediately; `GET /api/report-narration/` reads back whatever's been
persisted** (chosen).

**B. Hook into an existing pipeline event, mirroring 7b/7c/7d's
`ingest_task`/`calculate_task` success-path dispatch pattern.** Rejected:
there is no such event to hook into. Compliance reports are on-demand
query results (ADR 0002) — there is no "report generation pipeline,"
persisted `Report` row, or Celery task whose success path narration could
piggyback on. Milestone 7f's own "run narration asynchronously on the AI
queue" requirement is satisfied by making the ONE user-facing action that
exists (regenerating a narrative) itself asynchronous, not by inventing a
pipeline event that doesn't otherwise exist.

**C. Synchronous, mirroring 7e's `ask_esg_assistant()`.** Rejected: unlike
a live chat question, nothing about generating a report narrative demands
an in-request-cycle answer — the milestone explicitly asks for queue-based
async execution here (a real, stated difference from 7e's own scope), and
a report narrative is naturally consumed later (on a dashboard widget),
not in the same interaction that requested it.

**Decision: A.** `generate_report_narration_task` processes exactly one
narration request (no per-item batch loop, unlike the other three AI
tasks) — a real failure is left to propagate to Celery's own
task-failure handling rather than being swallowed, since there's no
batch of sibling work to protect from one bad item.

## Decision 3: RBAC is `CanViewActivity`, matching the compliance report itself

**Alternatives considered:**

**A. `CanViewActivity` (Org Admin/Auditor only)** (chosen) — identical to
`apps.carbon.report_views._BaseComplianceReportView`'s own gate.

**B. `CanUseAI` (Org Admin/Analyst/Auditor), matching every other Phase
7 capability's general AI-feature gate.** Rejected: narration is advisory
content ABOUT a specific, already-gated audit artifact (the compliance
report). Using the broader AI gate would let an Analyst read AI
commentary on report figures they have no access to see the underlying
report for at all — a real information-boundary violation, not just an
inconsistency. Report narration inherits the access boundary of the
resource it's commentary on, the same way a comment on a document should
never be visible to someone who can't see the document itself.

**Decision: A.** `ReportNarrationListView`/`ReportNarrationRegenerateView`
both use `CanViewActivity`, proven at the real API (not just in
isolation) via `InvariantI3ReportNarrationConcreteProofTests`'s
Analyst-denied test.

## Consequences

- `ReportsWidget` (the dashboard's existing CSV-export widget) needed to
  be added to Org Admin's and Auditor's widget lists — it was previously
  Viewer-only, but Viewer cannot see narration (`CanViewActivity` excludes
  Viewer). The CSV export itself is unaffected for Viewer; the new AI
  section simply never renders for a role without narration access
  (a 403 is treated as "nothing to show," not an error).
- A future capability whose output should inherit an existing resource's
  RBAC boundary (rather than the general `CanUseAI` gate) has a clear
  precedent here, distinct from every other Phase 7 capability's choice.
- Regeneration is deliberately NOT idempotent-by-skip the way the other
  three capabilities' tasks are ("already exists, skip") — every
  regeneration creates a new, independent `AIReportNarration` row, and
  history is kept, not overwritten, matching the milestone's "maintain
  immutable history" requirement literally.
