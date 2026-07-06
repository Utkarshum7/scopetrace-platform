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
