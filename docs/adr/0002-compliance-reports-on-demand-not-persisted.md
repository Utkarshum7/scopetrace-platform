# ADR 0002: Compliance reports are generated on-demand, never persisted

- Status: Accepted
- Date: 2026-07-07
- Phase: 6e (Compliance Reports)

## Context

Phase 6e needs compliance reports (CSV/JSON, PDF deferred) that are:
reproducible from historical data, tenant-isolated, RBAC-gated, free of
N+1 queries, deterministic, and extensible toward GHG Protocol / CSRD /
ESG-specific formats later.

By this point the platform already has three immutability primitives to
build on: `AuditTrail` (6a, hash-chained governance ledger),
`EmissionRecordVersion` (6b, immutable per-record snapshots — and since
`APPROVED` is workflow-terminal and audit-locked, a record's *latest*
version is always its approved state), and the fixed approval workflow
(6c, `EmissionRecord.status == APPROVED` is the only certified state).

The question: should a compliance report be a fresh, on-demand query over
this existing data, or should generating one create a new persisted
snapshot (a `ComplianceReport` row/table capturing exactly what was
included, retrievable later even if the underlying data changes)?

## Alternatives considered

**A. On-demand generation** (chosen). Every request re-runs a deterministic
query — `EmissionCalculation` rows that are `is_current=True`,
`CALCULATED`, whose `emission_record.status == APPROVED`, within the
requested date range — and returns the result directly (JSON or streamed
CSV). Reproducibility comes from the query being over already-immutable
data, plus embedding provenance (`record_version`, `calculation_id`,
`generated_at`, an `audit_chain` verification snapshot) in the output
itself, so any line item can be traced back to its exact underlying rows
without a new storage layer.

**B. Persisted `ComplianceReport` snapshot**. Generating a report creates a
durable row (or a row plus a snapshot of line items / referenced ids),
retrievable later via its own id regardless of what happens to the
underlying data afterward. Would need its own retention policy, its own
RBAC on "who can list past reports," and its own migration/storage growth.

## Decision

**Option A.**

1. **The underlying data is already immutable where it matters.** `APPROVED`
   records are locked (`EmissionRecord.clean()`); `EmissionCalculation` is
   append-only via its `is_current` pattern (a superseded calculation row
   is never deleted, just flagged); `EmissionRecordVersion` never changes
   after creation. A fresh query over `APPROVED` data for a fixed date
   range returns the same rows every time unless *more* data gets approved
   into that range later — which is the *correct*, expected behavior for a
   compliance report (it should reflect the latest certified state), not a
   reproducibility bug.
2. **A persisted snapshot duplicates guarantees 6a/6b already provide** —
   the retention-policy question a snapshot table would raise is already
   flagged as a separate, later Phase 6 concern (per the Phase 6 approval),
   not something to fold into this milestone.
3. **Matches this milestone's explicit scope**: "CSV and JSON exports," not
   a report-management system with its own history/CRUD.
4. **Keeps generation a pure, side-effect-free read** — no `AuditTrail`
   entry is written for "a report was viewed," consistent with every other
   read-only governance/metrics endpoint already in this codebase
   (`ActivityFeedView`, `MetricsSummaryView`, `AuditChainVerifyView` are
   all audit-free reads). This was considered and rejected: writing an
   audit entry per report view would add write/lock contention to what
   should be a cheap read path, and works against "keep report generation
   deterministic" (a GET that mutates state is a much harder thing to
   reason about and test).
5. **No caching**, unlike the dashboard `MetricsService` endpoints — a
   compliance artifact should reflect current certified state on every
   call, not a stale cache with its own invalidation-timing question.

## Consequences

- Two reports generated for "the same" historical period on different
  dates *can* differ if records were approved (including backdated) into
  that period in the meantime. This is intentional; each report embeds
  `generated_at` and an `audit_chain` verification result so a reader can
  tell exactly when it was pulled and that the ledger was intact at that
  moment.
- There is no server-side history of "who generated which report when" —
  only what's implicit in normal HTTP/application-log access records. If
  that's needed later, it's a small additive change (an `AuditTrail` entry
  per generation), not a redesign.
- JSON responses are capped (5,000 line items) with `truncated`/
  `line_item_count` fields; CSV is the uncapped, streamed path for larger
  exports — mirrors the existing `RecordExportView`/`EXPORT_ROW_CAP`
  precedent rather than inventing a new pagination scheme.
- RBAC is `CanViewActivity` (Org Admin + Auditor), matching the existing
  governance-facing endpoints (`/api/audit/verify/`,
  `/api/metrics/activity/`) rather than the broader `IsOrgMember` dashboard
  endpoints use — a compliance report is an audit artifact, not a working
  dashboard.
