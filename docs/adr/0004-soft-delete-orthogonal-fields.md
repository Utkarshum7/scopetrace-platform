# ADR 0004: Soft delete as orthogonal fields, filtered by a manager split

- Status: Accepted
- Date: 2026-07-07
- Phase: 6d (Soft Delete and Record Retention)

## Context

By Phase 6d, `EmissionRecord` already carries a fixed approval-workflow
`status` (6c), an immutable version history (`EmissionRecordVersion`, 6b),
and a hash-chained `AuditTrail` (6a); compliance reports (6e) query
certified (`APPROVED`) data on demand, never persisting a snapshot.

Reading the current model relationships before designing anything
surfaced a real, pre-existing gap: **hard deletion of governed data is
possible today.** `EmissionRecord.organization` and `EmissionRecord.batch`
are both `on_delete=CASCADE`; `EmissionCalculation.emission_record` is
too. Neither `EmissionRecordAdmin` nor `UploadBatchAdmin` overrides
`has_delete_permission`. Deleting an `UploadBatch` (or an `Organization`)
via Admin today silently cascades through and destroys every
`EmissionRecord` and `EmissionCalculation` underneath it, while
`AuditTrail`/`EmissionRecordVersion` survive (already `SET_NULL`),
leaving an audit trail that references data that no longer exists. 6d
needs to close this, not just add soft-delete on top of a still-hard-
deletable foundation.

Two questions had to be answered before implementation: (1) how does
"deleted-ness" relate to the existing `status` field, and (2) how do
queries stop showing deleted records without breaking the "preserve
historical compliance reports" requirement.

## Alternatives considered

### Q1: Where does "deleted" live?

**A. Add `DELETED` to `RecordStatus`**, reusing 6c's
`WORKFLOW_TRANSITIONS` machinery.

**B. Orthogonal `is_deleted`/`deleted_at` fields, `status` untouched**
(chosen).

### Q2: How do active views stop showing deleted records?

**A. Explicit `.filter(is_deleted=False)` added at each call site that
needs it** (views, export, metrics), `EmissionRecord.objects` left
exactly as-is.

**B. A manager split**: `EmissionRecord.objects` becomes the default,
*filtered* manager; `EmissionRecord.all_objects` stays unfiltered
(chosen).

## Decision

**Q1: Option B — orthogonal fields.** Deletion doesn't transition through
the approval workflow; it freezes whatever status a record was at and
hides it, and restoring un-hides it at that *exact* same status — no
ambiguity about "restore to which status." Option A would need every
status-based query (`_PENDING_STATUSES`, the transition graph, the
compliance-report filter) to explicitly exclude `DELETED` everywhere, and
"deleted" is conceptually a different axis from "workflow stage" (you can
delete a `DRAFT` record or an `APPROVED` one — deletion doesn't erase or
advance the workflow, it just hides the record from active use). No
dedicated `deleted_by`/`deletion_reason` fields, mirroring 6c's own
precedent for rejection: that provenance is already fully captured by the
`AuditTrail` entry and the `EmissionRecordVersion` snapshot the deletion
creates — a dedicated field would just be a second, driftable place for
the same fact to live.

**Q2: Option B — a manager split.** `EmissionRecord.objects` (the
default, first-declared manager) filters `is_deleted=False`;
`EmissionRecord.all_objects` stays unfiltered. This means
`EmissionRecordViewSet`, `RecordExportView`, `recalculate()`, and
`submit`/`approve`/`reject`'s lookups all get correct, deleted-excluding
behavior with **zero code changes** — they already query
`EmissionRecord.objects`. It also means any *future* code that queries
`EmissionRecord.objects.filter(...)` is safe by default, without having
to remember a filter — matching this project's own established idiom of
using a custom `QuerySet`/`Manager` for governance-relevant behavior
(`AuditTrailQuerySet`, `EmissionRecordVersionQuerySet` already do this
for immutability).

The cost of Option B, and why it's manageable: internal model code that
needs to see the *true* prior state regardless of deletion status must
deliberately use `all_objects`, not `objects`. There are exactly two such
call sites, both fixed as part of this change:
`EmissionRecord.clean()`'s "fetch the original row" lookup, and
`EmissionRecord.save()`'s pre-save snapshot fetch (used for both
version-diffing and the `select_for_update()` lock). Missing either would
be a real, subtle bug — restoring a soft-deleted record would see
`old=None` (the filtered manager can't find its own not-yet-restored
row), silently breaking the lock and confusing the version diff — so both
are covered by a dedicated concurrency test (soft-delete → restore under
real threads) as well as an explicit unit test asserting `clean()` uses
the unfiltered manager.

**`Meta.base_manager_name = "all_objects"`** pins Django's own internal
machinery (related-object traversal, e.g. a future `batch.records.all()`)
to the *unfiltered* manager — Django uses `_base_manager` (the
`base_manager_name`, or the first-declared manager if unset) for related
lookups specifically so a filtered default manager can't silently drop
rows reachable via a relation. Verified via `grep` that no code in this
repo uses that reverse accessor today, but this is exactly the kind of
thing that's invisible until it isn't, and costs nothing to set correctly
now.

**Compliance reports (6e) are deliberately left unfiltered.**
`apps/carbon/services/reports.py` queries from `EmissionCalculation`,
joining to `EmissionRecord` via `emission_record__status=APPROVED` — a
Django `__`-traversal filter operates as a raw SQL `JOIN`, and does
**not** apply the related model's manager filtering (manager
customization only affects queries that start *from* that model). This
means a soft-deleted record's calculations remain in compliance reports
automatically, with no special-casing required — which is exactly the
"preserve historical compliance reports" behavior wanted. `is_deleted`/
`deleted_at` are added to each line item's output for transparency (a
reader can see a line item's source record was later deleted, and when).
`AuditTrail`'s `ActivityFeedView` and `verify_chain()` are similarly
unaffected — neither queries through `EmissionRecord` at all.

**Dashboards and the active calculations list are filtered.**
`MetricsService._base()` and `EmissionCalculationViewSet`'s queryset both
add an explicit `.exclude(emission_record__is_deleted=True)` — since
`__`-traversal doesn't respect a manager, this exclusion has to be
explicit at these two call sites regardless of the manager split above. A
deleted record's emissions must not inflate the org's live dashboard
totals; that's a correctness bug, not merely a display concern.

**Closing the bypass vectors, not just adding the mechanism on top:**
- `EmissionRecord.delete()` now raises unconditionally (matches
  `AuditTrail.delete()`/`EmissionRecordVersion.delete()`'s established
  pattern) — hard deletion is never permitted through the ORM, full stop.
- A new `EmissionRecordQuerySet` blocks bulk `.delete()` **and** bulk
  `.update()`. Blocking bulk update closes a gap that actually predates
  this milestone: `.update()` bypasses `clean()`/`save()` entirely, so it
  could set `is_deleted=True` — or even `status`, dating back to 6c —
  with no audit trail entry and no version snapshot at all. Confirmed via
  `grep` that no code in this repository relies on bulk update or bulk
  delete on `EmissionRecord` today, so closing this is safe.
- `EmissionRecord.organization`, `EmissionRecord.batch`, and
  `EmissionCalculation.emission_record` all move from `CASCADE` to
  `PROTECT` — matching `AuditTrail.organization`'s and
  `EmissionRecordVersion.organization`'s existing `PROTECT`. A batch (or
  an organization) with any records can no longer be hard-deleted at all;
  an empty one still can, matching the "an org/batch with no governed
  history can still be deleted" precedent 6a already established.
- `EmissionRecordAdmin` gets `has_delete_permission = False` (the delete
  button would otherwise reach `.delete()`'s raised exception with a
  confusing 500-style page; PROTECT on the FK-level changes, by contrast,
  Django Admin already surfaces gracefully as a clear "cannot delete
  because of protected objects" message, so `UploadBatchAdmin` doesn't
  need the same treatment).

**RBAC**: a new `IsOrgAdmin` permission class (Org Admin only, *every*
method) for soft-delete, restore, and the opt-in `?deleted=true` list
view. Not the existing `CanManageOrgResources`: that class deliberately
allows reads to any org member and only restricts writes (`if
request.method in SAFE_METHODS: return True`) — reusing it for the
`?deleted=true` list would have incorrectly let any member view it, since
listing is a `GET`. Caught by this milestone's own RBAC test, not assumed.
Deletion is an administrative action, not a routine analyst/approver one, matching
the existing "manage org resources (write)" precedent.

## Retention

The mechanism is implemented fully now (`deleted_at` is exactly what a
future purge sweep would need to select on). An automated *purge*
(hard-delete-after-N-days) is **not** implemented in this milestone —
nothing in the requirements explicitly calls for it, and "do not
physically delete governed records unless explicitly required" argues
for treating eventual purging as a deliberate, separately-approved future
step rather than bundling it into the mechanism's introduction. A
retention *policy* is documented (see `docs/GOVERNANCE.md` §6d) so the
decision not to auto-purge is explicit, not a silent omission.

## Consequences

- `GET /api/records/{deleted-id}/`, `.../versions/`, `.../workflow/`,
  `.../submit/`, `.../approve/`, `.../reject/`, `.../recalculate/` all
  `404` for a soft-deleted record (the default manager can't find it) —
  a deliberately simple, consistent outcome. `DELETE /api/records/{id}/`
  and `.../restore/` are the exception: both share one lookup that
  deliberately uses `all_objects` (restore's target is, by definition,
  invisible through the default manager), so a *second* delete attempt on
  an already-deleted record gets a specific `400` ("already been
  deleted"), not a bare `404`. The only ways to see a deleted record's
  current state are the opt-in `?deleted=true` list (which returns full
  serialized rows, so no separate detail view is needed) and restoring
  it. Considered and rejected: overriding
  `get_object()` to fall back to `all_objects` for read-only actions
  specifically — extra surface area for a capability nothing in the
  requirements asked for.
- Migration is purely additive: two new columns on `EmissionRecord`
  (`is_deleted` indexed boolean default `False`, `deleted_at` nullable),
  two mirrored columns on `EmissionRecordVersion`, and three `on_delete`
  metadata changes (no data backfill needed for any of them — existing
  rows get `is_deleted=False` from the column default, and an
  `on_delete` change doesn't touch existing row data at all).
- `is_deleted` is added to `EmissionRecordVersion`'s diffed-field list
  (`_COMPARED_FIELDS`), so delete/restore automatically produce a new
  version snapshot through the *existing* hook — no new versioning code,
  exactly like 6c's workflow transitions got versioning "for free."
