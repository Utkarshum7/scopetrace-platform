# ADR 0001: Fixed approval workflow reuses `EmissionRecord.status`, not a second field

- Status: Accepted
- Date: 2026-07-07
- Phase: 6c (Enterprise Approval Workflow)

## Context

Phase 6c requires a formal, fixed (non-configurable) approval workflow:

```
Draft -> Submitted -> Approved
              |
              +-> Rejected (if applicable)
```

Before 6c, `EmissionRecord.status` already carried five values: `DRAFT`,
`FAILED`, `SUSPICIOUS`, `VALIDATED` (never actually assigned anywhere —
dead), and `APPROVED`. `FAILED`/`SUSPICIOUS` are produced by the ingestion
validator as data-quality signals, not workflow stages; `is_suspicious` (a
separate boolean) already carries the same "needs review" signal redundantly
alongside `status=SUSPICIOUS`. `EmissionRecord.clean()` already locks a
record permanently once `status == APPROVED`. `apps/carbon/services/
metrics.py`'s `_PENDING_STATUSES` and the existing `approve()` action both
key off this single field.

The question: should the two new workflow states (`SUBMITTED`, `REJECTED`)
be added to this same field, or should workflow stage live in a new,
separate field?

## Alternatives considered

**A. Extend `RecordStatus` in place** (chosen). Add `SUBMITTED`/`REJECTED`
as two more `TextChoices` values on the existing `status` field. `FAILED`
stays terminal and outside the workflow graph; `SUSPICIOUS`/`VALIDATED`
feed into `SUBMITTED` exactly like `DRAFT` does.

**B. Add a separate `workflow_status` field**, decoupled from `status`.
`status` would keep its current ingestion-time meaning (`DRAFT`/`FAILED`/
`SUSPICIOUS`/`VALIDATED`); `workflow_status` would carry exactly the four
requested states (`DRAFT`/`SUBMITTED`/`APPROVED`/`REJECTED`) as its own
independent lifecycle.

## Decision

**Option A.** Extend `RecordStatus` in place; enforce the legal-transition
graph (`EmissionRecord.WORKFLOW_TRANSITIONS`) inside `EmissionRecord.clean()`
itself, not only in the service layer.

Reasoning:

1. **No redundant state to keep in sync.** Option B would require `status`
   and `workflow_status` to always agree on "is this APPROVED" (the
   audit-lock check, `metrics.py`'s pending count, `EmissionRecordVersion`'s
   own `status` column, the existing `approve()`/`recalculate()` guards) —
   two fields both claiming to answer "is this approved" is exactly the
   kind of duplicated, driftable state this project has consistently
   avoided (cf. `EmissionCalculation` being the *sole* source of truth for
   CO2e, never denormalized onto `EmissionRecord`).
2. **This is explicitly a *fixed*, non-configurable workflow** (Phase 6
   Decision 3). A second field starts to look like the first piece of a
   more general workflow-engine abstraction — exactly what was rejected.
3. **`SUSPICIOUS`/`FAILED` are already orthogonal to "has a human approved
   this"** in practice: `is_suspicious` (boolean) already carries the
   quality-flag signal independently of `status`. Folding `SUBMITTED`/
   `REJECTED` into the same field the existing quality states already
   share is consistent with how the codebase already treats these as one
   axis, not two.
4. **The transition graph is enforced in `clean()`, not only in
   `apps.ingestion.services.workflow`**, for the same reason Phase 6b
   hooked `EmissionRecord.save()` itself rather than relying on view call
   sites alone: `EmissionRecordAdmin` has no `readonly_fields` restricting
   `status`, so a service-only check would miss Admin edits, direct ORM
   use, and any future call site. Both layers read the same
   `EmissionRecord.WORKFLOW_TRANSITIONS` mapping, so they cannot disagree.

## Consequences

- **Breaking change (explicit, not silent):** `POST /api/records/{id}/
  approve/` previously worked directly from `DRAFT`/`SUSPICIOUS`/
  `VALIDATED`. It now requires `SUBMITTED` first — a direct approve is a
  rejected (400) transition. This is exactly what the requested state
  diagram demands. All previously-passing tests that assumed direct-from-
  DRAFT approval were updated to submit first; two error-message assertions
  were reworded to match the new generic "Invalid workflow transition:
  cannot move from X to Y" wording (replacing the old bespoke "Approved &
  Audit Locked" / "Failed validation" strings for this specific case).
- **`apps/carbon/services/metrics.py`'s `_PENDING_STATUSES`** gained
  `SUBMITTED`/`REJECTED` — without this, records mid-workflow would have
  silently vanished from the "pending approval" dashboard count.
- **Migration impact:** adding two `TextChoices` values only changes
  Django's field metadata (`choices=`), not the DB schema — no data
  migration, no `AlterField` SQL (`max_length` is unchanged). Verified via
  `makemigrations --check --dry-run` both before and after.
- **No new fields for rejection tracking** (no `rejected_by`/`rejected_at`).
  `AuditTrail` (6a, hash-chained) and `EmissionRecordVersion` (6b,
  `created_by`/`created_at`/`reason`) already durably capture who/when/why
  for every transition including rejection — adding mutable fields for the
  same information would be redundant and a second place for it to drift.
