# Governance, Audit & Compliance (`GOVERNANCE.md`)

Phase 6. The enterprise governance layer built on top of the ScopeTrace
platform: cryptographic audit integrity, record version history, formal
approval workflow, compliance reporting, and data retention. This document
grows one section per milestone (6a → 6g).

---

## 6a — Cryptographic Audit Hash-Chain

### Objective

Make `AuditTrail` tamper-**evident**, not merely append-only *by convention*
— and close two real gaps that convention alone left open.

### The two gaps 6a closes (found by reading the pre-6a code, not assumed)

1. **Bulk operations bypassed append-only-ness entirely.** The pre-6a
   `AuditTrail` overrode `delete()` (instance) and `clean()` (block re-save),
   but `AuditTrail.objects.filter(...).delete()` / `.update(...)` call
   *neither* — `QuerySet.delete()`/`.update()` operate at the SQL level,
   bypassing model instance methods completely. This is the exact same
   Django behavior this project already hit twice in Phase 5 (`QuerySet.
   update()` bypassing signals in the 5f/5g maintenance sweeps). So
   "append-only" was only ever true for single-instance operations.
   **Fix**: a custom `AuditTrailQuerySet` whose `delete()`/`update()` raise
   `ValidationError` — bulk mutation is now blocked at the QuerySet level too.

2. **Deleting an Organization silently destroyed its audit history.**
   `AuditTrail.organization` was `on_delete=CASCADE` — exactly backwards for
   a model whose entire purpose is durable record-keeping. **Fix**: changed
   to `on_delete=PROTECT`. A real behavior change: an organization with any
   audit history can no longer be deleted at all (an org with *no* history
   still can — verified by test).

### Design

Each `AuditTrail` entry gains three fields:

- `sequence` — the organization's monotonic 1-indexed position in *its own*
  chain (the chain is **per-organization**, matching existing tenant
  isolation — each tenant's chain is independently verifiable, and there are
  no cross-tenant ordering questions).
- `prev_hash` — the previous entry's `entry_hash` (or `GENESIS_HASH`, 64
  zeros, for the first entry).
- `entry_hash` — `SHA-256` over a canonical JSON serialization of
  `{sequence, organization_id, record_uuid_backup, action, changed_by_id,
  changes, reason, timestamp, prev_hash}`. Because `prev_hash` is included,
  altering *any* historical entry changes its hash, which breaks every
  subsequent link — the defining property of a hash chain.

A separate `AuditChainState` model holds one row per organization: the
current chain tip (`last_sequence`, `last_hash`). Appends lock this single
row via `select_for_update()` inside the same transaction as the record-level
change, so sequence/hash assignment is atomic under concurrent writers
**without** a race-prone "SELECT the last AuditTrail row for this org" query.
`AuditChainState` is deliberately *mutable* bookkeeping — it is not itself a
governance record; `AuditTrail` is the append-only ledger, this is just the
"what's next" counter.

**All entries must be created via `apps.audit.services.append_entry(...)`**,
never `AuditTrail.objects.create(...)` directly — only `append_entry()`
assigns a correct sequence/prev_hash/entry_hash under the lock. The two
existing call sites (record approval, record recalculation in
`apps/ingestion/views.py`) were switched to it; both already wrapped their
state change in `transaction.atomic()`, which `append_entry()`'s
`select_for_update()` participates in.

### A subtle correctness bug caught during implementation

`AuditTrail.timestamp` was `auto_now_add=True`. That would have
**silently overwritten** the timestamp assigned at save-time with a *fresh*
`timezone.now()` on every INSERT — so the timestamp `append_entry()` hashed
would never match the one actually persisted, and **every single entry would
fail verification the moment it was written**. Fixed by making `timestamp` a
plain `DateTimeField` that `append_entry()` sets explicitly, guaranteeing the
hashed value and the stored value are identical. (Verification re-derives
from the stored row's own `timestamp` column, so both sides always hash the
same value.)

### Trade-off (explicit, not silently assumed)

This makes tampering **detectable on verification** — it does **not** make
tampering **impossible**. Someone with raw database access can rewrite a
historical row *and* recompute a consistent-looking chain from that point
forward, and verification would then pass. True non-repudiation requires an
**external anchor** — periodically publishing the chain head hash somewhere
outside this system's own control (a append-only external log, a
notarization service, etc.). This is **deliberately out of scope**: it's
real operational cost for a benefit that's speculative at this project's
scale, and echoes the already-established pattern of not building unused
ceremony (cf. the rejected Kubernetes manifests in Phase 5). Documented here
as a known limitation so the guarantee is not overstated.

### Verification (the how-to)

Three independent surfaces, all calling the same `verify_chain()` service:

- **Management command**: `python manage.py verify_audit_chain`
  (all orgs, non-zero exit if any chain is broken) or
  `--organization <id>` (one org). For operators with shell access.
- **API endpoint**: `GET /api/audit/verify/` — verifies the active
  organization's chain, returns `{valid, entries_checked,
  broken_at_sequence, detail}`. Same RBAC as the existing audit/activity
  feed (Org Admin / Auditor, via `CanViewActivity`) — no shell access
  required. Added per the Phase 6 approval's Decision 1.
- **Django admin action**: "Verify hash chain for the selected entries'
  organization(s)" on the `AuditTrail` admin list.

### Migration impact

Three sequential migrations (the riskiest part of 6a, hence its own careful
sequence rather than one migration):

1. `0002_add_hash_chain_fields_nullable` — adds the three fields **nullable**,
   creates `AuditChainState`, adds the `(organization, sequence)` unique
   constraint, flips `organization` to `PROTECT`, drops `timestamp`'s
   `auto_now_add`.
2. `0003_backfill_audit_chain` — a `RunPython` data migration that walks
   every organization's existing `AuditTrail` rows in `(timestamp, id)`
   order, computes a real chain over them, and seeds each org's
   `AuditChainState`. Deliberately duplicates the hash logic inline (rather
   than importing `apps.audit.services`) per Django's migration best
   practice: a migration must stay replayable regardless of how app code
   evolves later.
3. `0004_enforce_hash_chain_not_null` — now that every row is backfilled,
   enforces `NOT NULL` to match the model's true final state.

**Verified live** against the real dev database (not just the test runner's
throwaway DB): inserted 5 legacy `AuditTrail` rows across 2 organizations
via raw SQL (simulating pre-6a history), ran all three migrations, and
confirmed both orgs' backfilled chains verify valid, a real `append_entry()`
extends the chain correctly, and a raw-SQL tamper is detected at the exact
sequence. Concurrency (10 simultaneous appends → gapless 1..10 sequence, no
corruption) is asserted under real Postgres in CI (`backend-ci.yml`'s
Postgres service container); under SQLite the same test tolerates
file-lock contention (a SQLite limitation, not an `append_entry` bug) while
still asserting every *successful* append produced a valid chain.

---

## 6b — Immutable Record Versioning

### Objective

Give every `EmissionRecord` a complete, immutable history of its own business
state — not just *that* something changed (6a's job) but *what it actually
looked like* at each point in time, so a historical snapshot can be listed,
retrieved, and diffed against the record's current state.

### Design decision: a dedicated model, not an AuditTrail extension

Per the Phase 6 approval (Decision 2), this is **Option A**: a new, dedicated
`EmissionRecordVersion` model — not a widening of `AuditTrail` to carry full
record snapshots. The two models keep separate responsibilities:

- `AuditTrail` (6a) — the governance ledger. Who did what, when, why,
  hash-chained for tamper-evidence. Freeform `changes` JSON, not a full
  business-state snapshot.
- `EmissionRecordVersion` (6b) — the record-reconstruction mechanism. Typed
  columns mirroring `EmissionRecord`'s own shape (not one opaque JSON blob),
  specifically so they're indexable and trivially diffable against the live
  record.

The two are cross-linked, not merged: the two existing `AuditTrail`-writing
call sites (approval, recalculation) now also write the `version_number`
this created into `AuditTrail.changes["record_version"]` — using
`AuditTrail`'s already-freeform field, not a schema change to the
already-shipped 6a migrations.

### Design: how a version gets created

`EmissionRecord.save()` gained an override that:

1. Locks the record's own row (`select_for_update()`) and reads its
   pre-save state, if this is an update (not a fresh create).
2. Runs `full_clean()` (unchanged pre-existing behavior — this is what
   already enforced the "no modifications after APPROVED" lock) and persists.
3. Diffs old vs. new across a fixed set of business fields (`status`,
   `is_suspicious`, `scope_category`, `normalized_value`, `normalized_unit`,
   `approved_by_id`, `approved_at`, `validation_errors`,
   `raw_data_payload`) and creates a new `EmissionRecordVersion` **only if
   at least one differs** — satisfying "prevent duplicate version creation
   for unchanged records" without a separate dirty-tracking mechanism.

Locking granularity is deliberately **narrower** than 6a's: `AuditTrail`
needed a separate per-organization `AuditChainState` counter row because many
different records in one org can be appended to concurrently. A version's
sequence is scoped to a **single record** — locking that record's own row
(already being saved) is sufficient, and nothing else needs to write to that
same record concurrently for this to be safe.

### Two gaps closed during implementation (found by reading the code, not assumed)

1. **Django Admin bypasses view-level logic entirely.**
   `EmissionRecordAdmin` has no `readonly_fields` restricting business
   fields — an admin edit would silently skip version creation if the hook
   lived only in the two known view call sites (`approve`, `recalculate`).
   **Fix**: the hook lives in `EmissionRecord.save()` itself, so it fires
   for every mutation path — views, admin, shell, future code — with no
   call-site enumeration to keep in sync.

2. **Recalculation changes calculation linkage, not record fields.**
   Recalculating a record's CO2e changes *which* `EmissionCalculation` is
   `is_current=True` for it — it never touches an `EmissionRecord` field
   itself, so the diff-based trigger in `save()` would never fire for this
   case, silently missing a change that's clearly "meaningful" for the
   requirement (approval state visibility, calculation references). **Fix**:
   a second, explicit entry point,
   `create_version_for_calculation_change()`, called directly from the
   `recalculate` view action — sharing the same `_build_version()` snapshot
   logic as the diff-based path, just without the diff gate.

### Bulk operations: a separate, explicit code path

`bulk_create()` bypasses `Model.save()` entirely by Django design (it
issues one multi-row `INSERT`, never instantiating `save()` per object) —
this is exactly the fast path `apps/ingestion/services/ingestion_service.py`
uses for the hot ingestion loop, so the version hook could not simply "also
run" there. `create_initial_versions_bulk()` mirrors it: one
`EmissionRecordVersion.objects.bulk_create()` call producing `version_number=1`
for every newly-ingested record, called immediately after the records'
own `bulk_create()`, avoiding an N+1 regression in the ingestion path.

### Immutability enforcement

Same two-layer pattern 6a established for `AuditTrail`:

- **Instance level**: `clean()` blocks re-save if the row already has a
  primary key in the database; `delete()` unconditionally raises.
- **QuerySet level**: a custom `EmissionRecordVersionQuerySet` whose
  `delete()`/`update()` raise `ValidationError` — closing the same
  bulk-operation gap 6a found for `AuditTrail` (`QuerySet.delete()`/
  `.update()` operate at the SQL level and bypass instance methods
  entirely).

`record` is `on_delete=SET_NULL` (not `PROTECT`) — deliberately different
from `AuditTrail.organization`'s `PROTECT`. A version's own tenant scoping
already comes from its denormalized `organization` FK (`PROTECT`, matching
6a's reasoning exactly), so a version's *history* is never lost if the
specific record it snapshots is ever removed; `record_uuid_backup` preserves
which record it was, mirroring `AuditTrail.record_uuid_backup`.

### APIs

Three new read-only `@action`s on the existing `EmissionRecordViewSet`,
reusing its established tenant scoping (`TenantScopedViewSetMixin` +
`IsOrgMember`) via `self.get_object()` — no new authorization path to keep
in sync with the record endpoint's own:

- `GET /api/records/{id}/versions/` — full history, newest first.
- `GET /api/records/{id}/versions/{n}/` — one historical snapshot.
- `GET /api/records/{id}/versions/{n}/compare/` — field-by-field diff
  between historical version `n` and the record's current live state.

RBAC is deliberately **not narrower** than `GET /api/records/{id}/` itself —
that endpoint already exposes `calculation_trace`/`factor_provenance` to any
org member, so restricting version history specifically would be an
inconsistent asymmetry, not tighter security.

### Migration impact, storage impact, indexing

- **Migration**: purely additive. `0006_emission_record_versioning` is a
  single `CreateModel` — no existing table gains or loses a column, so
  (unlike 6a) there was no need for a 3-phase nullable → backfill →
  enforce-NOT-NULL sequence. `0007_backfill_initial_record_versions` is a
  `RunPython` data migration creating a `version_number=1` snapshot for
  every `EmissionRecord` that predates this feature — otherwise a
  pre-existing record nobody edits again would have no history at all until
  its next change. Duplicates the snapshot logic inline (not importing
  `apps.ingestion.services.versioning`) for the same reason 6a's backfill
  does: a migration must stay replayable indefinitely regardless of how the
  real app code evolves later.
- **Storage impact**: one new row per meaningful record edit, growing
  roughly linearly with edit frequency (in practice: ingest → maybe a few
  validation-driven touch-ups → one approval → occasional recalculation —
  typically single-digit versions per record, not unbounded churn). No
  retention/pruning policy is introduced here; that's explicitly deferred to
  the data-retention policy work called out in the Phase 6 approval.
- **Indexing**: `UniqueConstraint(record, version_number)` (enforces
  gaplessness/no-duplication at the DB level, not just in application code)
  and `Index(organization, created_at)` (the shape of the tenant-scoped,
  time-ordered query the API surfaces above actually run).
- **Performance trade-off**: every `EmissionRecord.save()` now does one
  extra locked `SELECT` (the pre-save snapshot) plus, when something
  changed, one `INSERT`. This lands inside the same transaction the save
  already required for `full_clean()`'s lock-and-check pattern, so it adds
  one query's worth of latency to already-mutating requests — not to reads,
  and not a new transaction.

### Verification

25 new tests in `apps/ingestion/tests_versioning.py`, covering version
creation (on save, on `bulk_create`, on recalculation), duplicate-prevention
(unchanged re-save), immutability (instance + `QuerySet` level), tenant
isolation (cross-org 404 on all three new endpoints, plus a direct
model-level check), the API surface (list/retrieve/compare, including the
"no diff when comparing the current version" case), approval integration
(version + `AuditTrail.changes["record_version"]` cross-reference), the
backfill migration's actual output, and real multi-threaded concurrency.

**Verified against real PostgreSQL**, not SQLite alone (via
`docker compose up -d db` + `DATABASE_URL` pointed at it): all 286 backend
tests pass, `makemigrations --check --dry-run` reports no drift, and the
concurrency test's strict "all ten succeed, gapless 1..11 sequence" branch
was confirmed clean across 5 repeated runs. Under SQLite the same test
tolerates every thread losing the race (SQLite's file-level, not row-level,
locking under ten real threads holding a transaction open through
`full_clean()` + versioning logic) — asserting only that whichever saves
*did* succeed produced a correct, non-corrupted sequence, mirroring 6a's
`ConcurrentAppendTests` reasoning exactly.

---

## 6c — Enterprise Approval Workflow

### Objective

A formal, fixed (non-configurable — Phase 6 approval Decision 3) approval
state machine over `EmissionRecord`:

```
DRAFT / SUSPICIOUS / VALIDATED ──> SUBMITTED ──> APPROVED  (terminal)
                                        │
                                        └──> REJECTED ──> SUBMITTED (resubmit)
```

`FAILED` never enters this graph — an ingestion-time data-quality terminal
state, corrected by re-uploading, not by a workflow transition (matches the
pre-6c `approve()` action's existing behavior). `APPROVED` is terminal:
`EmissionRecord.clean()`'s pre-existing audit-lock (unchanged since before
6a) already blocks any further modification once approved.

### Design decision: reuse `RecordStatus`, don't add a second field

Full reasoning in [`docs/adr/0001-fixed-approval-workflow-status-field.md`
](adr/0001-fixed-approval-workflow-status-field.md). Summary: `SUBMITTED`
and `REJECTED` were added as two more values on the existing `status`
field rather than introducing a separate `workflow_status` — a second
field would duplicate "is this approved" across two columns that must
always agree (the audit lock, `metrics.py`'s pending count,
`EmissionRecordVersion.status`, `recalculate()`'s freeze check all key off
one field today), which is exactly the kind of driftable duplicated state
this project has consistently avoided.

### Where the transition graph is enforced

**In `EmissionRecord.clean()` itself** (`EmissionRecord.WORKFLOW_
TRANSITIONS`, a plain `{status: {legal target statuses}}` dict), not only
in the service layer — the same reasoning Phase 6b used for hooking
`save()` rather than relying on view call sites alone:
`EmissionRecordAdmin` has no `readonly_fields` restricting `status`, so a
service-only check would miss Admin edits, direct ORM use, and any future
call site. `apps/ingestion/services/workflow.py`'s `transition_record()`
also checks `available_actions()` up front (reading the *same* mapping) so
a caller gets a clear, action-oriented 400 before ever touching the row —
both layers can never disagree because they read one shared source of
truth.

### Services, not views

`apps/ingestion/services/workflow.py` owns: the target-status →
(audit-action-name, default-reason) mapping, setting `approved_by`/
`approved_at` when transitioning to `APPROVED`, and the
lock → mutate → save → audit sequencing. `EmissionRecordViewSet`'s
`submit`/`approve`/`reject` actions are now thin: they share one private
`_apply_workflow_transition()` helper that fetches-and-locks the record
(`select_for_update()`, mirroring the pre-6c `approve()` action exactly),
runs `check_object_permissions()` for tenant isolation, and delegates the
actual transition to the service.

### Integration with 6a and 6b

- **`AuditTrail` (6a):** every transition calls `append_entry()` inside the
  same `transaction.atomic()` as the save, with a dedicated action name
  (`RECORD_SUBMISSION` / `RECORD_APPROVAL` / `RECORD_REJECTION`) and the
  resulting version number cross-referenced into `changes["record_version"]`
  — identical pattern to the pre-6c `approve()` action, just applied to
  three actions instead of one.
- **`EmissionRecordVersion` (6b):** no new versioning code was needed.
  `status` was already one of the diffed fields in
  `create_version_if_changed()`, so every transition (`status` changing)
  automatically produces a new immutable snapshot the moment
  `record.save()` runs inside `transition_record()`.
- **RBAC:** unchanged permission classes. `submit` uses `CanUpload` (the
  same roles that prepare data decide when it's ready for review);
  `approve`/`reject` reuse `CanApprove`, exactly the roles that could
  already approve pre-6c.
- **Tenant isolation:** the three mutating actions preserve the exact
  pre-6c pattern (manual lock-fetch by `pk`, then `check_object_
  permissions()` → `403` on cross-org, not `404` — matching
  `docs/AUTH_RBAC.md`'s existing, tested precedent). The new read-only
  `GET /api/records/{id}/workflow/` instead reuses `self.get_object()`
  (tenant-scoped queryset → `404` on cross-org), matching the `/versions/`
  endpoints' precedent from 6b — the two different status codes for
  cross-org access (403 vs. 404) are a pre-existing, deliberate asymmetry
  between mutating and read-only actions, not something 6c introduced.
- **Carbon pipeline:** `recalculate()` is unchanged — still gated solely on
  `status == APPROVED` (the freeze), independent of the new intermediate
  `SUBMITTED`/`REJECTED` states. One real integration gap found and fixed:
  `apps/carbon/services/metrics.py`'s `_PENDING_STATUSES` (powering the
  "pending approval" dashboard count) didn't include `SUBMITTED`/
  `REJECTED` — without the fix, a record would have silently vanished from
  that metric the moment it entered the workflow.

### APIs added

- `POST /api/records/{id}/submit/` — optional `reason`.
- `POST /api/records/{id}/approve/` — optional `reason` (existing endpoint,
  now requires `SUBMITTED` first).
- `POST /api/records/{id}/reject/` — **required** `reason` (a rejection with
  no stated justification is poor audit hygiene and leaves the submitter
  with nothing actionable to correct).
- `GET /api/records/{id}/workflow/` — `{status, available_transitions}`,
  read-only.

### A breaking change, explained (not silent)

Pre-6c, `approve()` worked directly from `DRAFT`/`SUSPICIOUS`/`VALIDATED`.
Requiring `SUBMITTED` first is exactly what the requested state diagram
demands, so every pre-existing test that assumed direct-from-DRAFT approval
was updated to submit first, and two error-message assertions were
reworded from the old bespoke "Approved & Audit Locked" / "Failed
validation" strings to the new generic "Invalid workflow transition: cannot
move from X to Y" message every invalid transition now produces uniformly.

### Migration impact

Adding two `TextChoices` values only changes Django's field metadata — no
DB schema change, no data migration. `0008_workflow_status_choices` is a
single `AlterField` with unchanged `max_length`, confirmed to execute with
no SQL side effects on both SQLite and Postgres.

### Verification

30 new tests in `apps/ingestion/tests_workflow.py`: the transition graph in
isolation (every legal edge, every illegal edge, the model-level `clean()`
guard firing independent of the service), the full API surface (RBAC per
action, the full submit→approve and submit→reject→resubmit→approve
sequences, the `workflow` endpoint), tenant isolation (403 on the three
mutating actions, 404 on the read-only one), versioning + audit-chain
integration (each transition producing exactly one new version and one
audit entry, `verify_chain()` staying valid across a 4-transition
sequence), and real multi-threaded concurrent approvals (exactly one
winner, nine `InvalidTransitionError`s, never a double approval).

**Verified against real PostgreSQL**: all 316 backend tests pass,
`makemigrations --check --dry-run` reports no drift, and the concurrency
test's strict "exactly one wins, nine cleanly rejected" branch was
confirmed clean under Postgres's real row-level locking. Under SQLite the
same test tolerates every thread losing the race outright (file-level
locking), asserting only that the record's final state is consistent with
however many threads actually won — 0 or 1, never more.

---

## 6e — Compliance Reports

### Objective

CSV/JSON compliance reports (PDF deferred, per the Phase 6 approval's
Decision 5) built entirely from the existing immutable data model — no new
tables, no new migration.

### Design decision: on-demand generation, not a persisted report model

Full reasoning in [`docs/adr/0002-compliance-reports-on-demand-not-persisted.md`
](adr/0002-compliance-reports-on-demand-not-persisted.md). Summary: because
`APPROVED` records are already locked (6c), `EmissionCalculation` is
already immutable/versioned via `is_current` (Phase 3/4), and
`EmissionRecordVersion` never changes after creation (6b), a report is
just a deterministic query over data that's *already* immutable where it
matters — a persisted snapshot table would duplicate guarantees these
three systems already provide, and raise its own retention-policy question
(explicitly deferred, per the Phase 6 approval, to later governance work).

### What makes a report "compliance," not a dashboard

`apps/carbon/services/reports.py` filters to `EmissionCalculation` rows
that are `is_current=True`, `resolution_status=CALCULATED`, **and whose
`emission_record.status == APPROVED`** — the third condition is the whole
point: `MetricsService` (Phase 4) intentionally aggregates over every
status (including pending) for a live working dashboard; a compliance
report reflects only certified, audit-locked data. This is a genuinely
new, separately-scoped query, not a mode of `MetricsService`.

### Integration with 6a/6b/6c

- **Audit hash-chain (6a)**: every report embeds an `audit_chain` block —
  `verify_chain(organization)`'s result (`valid`, `entries_checked`,
  `broken_at_sequence`) — proof the governance ledger was intact at
  generation time. This is a pure read; generating a report does **not**
  write an `AuditTrail` entry (see the ADR's "considered and rejected"
  section — consistent with every other read-only governance endpoint in
  this codebase).
- **EmissionRecordVersion (6b)**: every line item embeds `record_version`.
  Since `APPROVED` is workflow-terminal (`clean()` blocks any further
  save), a record's *latest* version is always its approved snapshot — so
  this is a single `Subquery` annotation (`ORDER BY -version_number LIMIT
  1`), not a loop or an extra round-trip per row.
- **Approval workflow (6c)**: the `emission_record__status=APPROVED` filter
  above *is* the integration point — a record only becomes reportable the
  moment it's approved, and (because `APPROVED` is terminal) never stops
  being reportable afterward.
- **Metrics API**: reuses the same aggregation shape (`Sum`/`Count` over
  `EmissionCalculation`, grouped by `scope`) but as an independent,
  approval-filtered query in `compliance_summary()` — deliberately not a
  parameter added to `MetricsService.summary()`, which exists to answer a
  different question ("what does everything in the pipeline look like
  right now") than a compliance report does ("what has been certified").
- **Multi-tenant / RBAC**: same `resolve_tenant_context()` pattern as
  `_BaseMetricsView` (org required; platform admins without an
  `X-Organization-ID` are rejected, not defaulted). RBAC is `CanViewActivity`
  (Org Admin + Auditor) — matching `/api/audit/verify/` and
  `/api/metrics/activity/`, not the broader `IsOrgMember` the dashboards
  use — a compliance report is an audit artifact.

### Reproducibility

A report is reproducible in the sense that matters for compliance: every
line item carries `calculation_id` and `record_version`, both pointing at
rows that are permanently retained and never mutated — re-fetching
`EmissionCalculation.objects.get(pk=calculation_id)` or
`/api/records/{id}/versions/{n}/` always returns exactly what the report
showed. Re-running the *same* report query later can legitimately return
*more* rows if additional records were approved into that period since —
this is correct behavior for a compliance artifact (it reflects the latest
certified state), not a reproducibility bug, and is exactly why
`generated_at` and `audit_chain` are embedded in every report: a reader
can always tell precisely when it was pulled and that the ledger was
intact at that moment.

### Performance

- Query starts from `EmissionCalculation` (already the fact table with
  CO2e/factor provenance), not `EmissionRecord` — `select_related()`
  covers the record + approver in one join, `record_version` is one
  `Subquery` annotation. No per-row queries regardless of result size —
  verified with a 300-record dataset under `CaptureQueriesContext`
  (bounded, well under 20 queries total, not ~300).
- **CSV** is streamed via `.iterator(chunk_size=2000)` +
  `StreamingHttpResponse`, the same pattern `RecordExportView` (Phase 5)
  already established — near-constant memory regardless of dataset size,
  the uncapped path for large exports.
- **JSON** is capped at 5,000 line items (`truncated`/`line_item_count`
  fields tell the caller when to use the CSV endpoint instead) — DRF's
  `Response` isn't naturally streamable, and a JSON "report" payload is
  reasonably summary-sized by nature; CSV is the intended path for bulk
  data.
- **No caching** — unlike `MetricsService`'s dashboard endpoints
  (`metrics_cache`, per-org version invalidation), a compliance report
  always reflects current certified state on every call. Caching an audit
  artifact would add a staleness/invalidation-timing question this
  milestone deliberately avoids.

### Extensibility (GHG Protocol / CSRD / ESG)

`apps/carbon/services/reports.py` is deliberately renderer-agnostic: one
queryset-building function (`build_line_items_queryset`), one aggregation
function (`compliance_summary`), and one typed, explicit line-item shape
(`serialize_line_item`/`csv_row` — not one opaque JSON blob, mirroring
`EmissionRecordVersion`'s own typed-columns reasoning from 6b). Scope
1/2/3 (`by_scope`) is already the GHG Protocol's own taxonomy. A future
CSRD-specific or GHG-Protocol-formatted renderer is an additive function
consuming the same queryset/summary, not a rewrite of the aggregation
logic.

### APIs added

- `GET /api/reports/compliance/` — JSON: metadata, `audit_chain`,
  `summary` (totals + by-scope), up to 5,000 `line_items`,
  `line_item_count`/`truncated`. `date_from`/`date_to` are **required**
  (unlike the optional-range `MetricsFilterSerializer`) — a compliance
  report always covers a defined reporting period.
- `GET /api/reports/compliance/csv/` — streaming CSV of the same line
  items, uncapped.

### Migration impact

None. No model changes — the whole feature is a read-only query layer over
tables that already exist.

### Verification

24 new tests in `apps/carbon/tests/test_reports.py`: correctness (only
`APPROVED` records included, superseded/non-current calculations excluded,
`UNRESOLVED` calculations excluded, date-range and scope filtering,
summary totals), historical version consistency (`record_version` matches
the record's actual approved snapshot after a real
`DRAFT→SUBMITTED→APPROVED` sequence; `calculation_id` round-trips to an
unchanged row), tenant isolation (cross-org exclusion, platform-admin-
without-org-header rejection), RBAC (Org Admin/Auditor allowed, Analyst/
Viewer/unauthenticated denied, on both the JSON and CSV endpoints), and
large-dataset behavior (300 records: bounded query count via
`CaptureQueriesContext`, correct CSV row count).

**Verified against real PostgreSQL**: full backend test suite passes,
`makemigrations --check --dry-run` reports no drift (confirming no schema
change was introduced).
