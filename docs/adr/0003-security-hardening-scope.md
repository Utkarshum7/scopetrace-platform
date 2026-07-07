# ADR 0003: Phase 6f security hardening — what's in scope, what's deferred to infrastructure

- Status: Accepted
- Date: 2026-07-07
- Phase: 6f (Security Hardening)

## Context

By Phase 6f, the platform has a real governance stack to protect: a
cryptographic audit hash-chain (6a), immutable record versioning (6b), a
fixed approval workflow (6c), and compliance reporting over certified data
(6e) — all sitting behind JWT auth, RBAC, and server-side tenant isolation
(Phase 2). `docs/SECURITY.md` §10 and `docs/ROADMAP.md` §1 already catalog
a specific, honest list of known gaps from the Phase 5k Production
Readiness Review: 3 dead `FEATURE_*` flags, 5 known Django CVEs (fix
available), no secret-scanning CI, no IP/network restriction on `/admin/`,
`render.yaml` details still marked `# VERIFY:`.

The question this milestone has to answer isn't "what's insecure" (that
list already exists) — it's "which of these gaps are genuinely
*application*-layer fixes belonging in this Django codebase, versus
*infrastructure*-layer concerns that don't belong in application code at
all." The milestone's own instructions are explicit on this: "do not
implement infrastructure features (WAF, firewall, IP allowlists) inside
Django unless there is a strong technical reason."

## Alternatives considered

**A. Fix everything in `docs/SECURITY.md` §10 (as numbered at the time
this ADR was written — since renumbered by this same milestone's own §9
addition), in Django, in this milestone.** Including IP-restricting
`/admin/` via custom middleware, adding a WAF-style request-filtering
layer, etc.

**B. Draw a hard line: application-layer code changes only; genuinely
infrastructure-layer items get written up as operator-facing
recommendations, not code** (chosen).

## Decision

**Option B.** Concretely:

**Implemented in Django (this milestone):**
- Removed the 3 dead `FEATURE_*` flags (read nowhere in the codebase —
  confirmed by `grep`).
- Bumped `Django` from `6.0.5` to `6.0.6` (patch release, fixes all 5
  currently-flagged CVEs; a patch bump carries no API-surface risk by
  Django's own versioning policy).
- CSV formula-injection ("CSV injection") sanitization on every value
  written into a CSV export (`RecordExportView`, the new-in-6e
  `ComplianceReportCSVView`) — a genuinely exploitable, well-documented
  (OWASP) class of vulnerability that this codebase's own CSV exports were
  exposed to (`UploadBatch.file_name` is user-controlled at upload time
  and was written into `RecordExportView`'s CSV verbatim).
- `max_length` caps on the free-text `reason` fields accepted from
  authenticated-but-untrusted client input on privileged workflow actions
  (`WorkflowActionSerializer`, `RejectionSerializer`) — an unbounded
  `TextField`-backed input accepted directly from a request body is a
  minor storage/CPU-exhaustion vector with no legitimate use case for
  arbitrarily large text.
- An explicit, independently-rotatable JWT signing key
  (`SIMPLE_JWT['SIGNING_KEY']`, sourced from a new optional
  `JWT_SIGNING_KEY` env var, defaulting to `SECRET_KEY` — zero behavior
  change unless explicitly set).
- `SECURE_REFERRER_POLICY` / `SECURE_CROSS_ORIGIN_OPENER_POLICY` made
  **explicit** in `settings.py` (both already default to the exact values
  set here in Django 6.0 — this changes zero runtime behavior; it makes an
  otherwise-implicit security posture reviewable without requiring a
  reader to know Django's own undocumented-in-this-codebase defaults,
  matching this project's established "explicit over implicit" ethos).
- Security-relevant logging: a `CRITICAL` log line the moment
  `apps.audit.services.verify_chain()` detects a broken hash chain (fires
  for every caller — the API endpoint and the management command — since
  it lives in the shared service, not duplicated per call site); a
  `WARNING` on failed login attempts (username + remote address, no
  password); an `INFO` line recording who generated which compliance
  report for which organization/period.
- Advisory secret-scanning in CI (`gitleaks`, full git history, mirroring
  `pip-audit`/`npm audit`'s existing advisory-not-blocking pattern — see
  `docs/CI_CD.md` §1.2 for why blocking-from-day-one is the wrong default
  for a first scan against pre-existing history).

**Explicitly NOT implemented in Django — written up as infrastructure
recommendations instead** (see `docs/INFRASTRUCTURE_SECURITY.md`, new):
- IP allow-listing / VPN-gating `/admin/` — this is a reverse-proxy /
  platform-level concern (Render, or any future host, already terminates
  TLS and sits in front of every request; that is the correct layer to
  block traffic by source, not a Django middleware that still spends a
  full request/response cycle before rejecting it).
- Any WAF-style request inspection/filtering.
- Formal RPO/RTO and a tested disaster-recovery drill (already tracked in
  `docs/INCIDENT_RESPONSE.md` §2 — an operational process, not a code
  change).
- The `render.yaml` `# VERIFY:` items (Redis service-type keyword,
  cross-service `SECRET_KEY` sharing) — unchanged from the Phase 5
  closeout; still requires live Render deploy access to confirm, which
  this milestone doesn't have either.

## Consequences

- The `/admin/` exposure gap remains genuinely open after this milestone —
  by design. It's now documented as an explicit, actionable infrastructure
  task (`docs/INFRASTRUCTURE_SECURITY.md`) rather than half-solved with a
  Django middleware that would give a false sense of completeness while
  the *real* fix (blocking at the edge, before Django even sees the
  request) remains undone.
- The Django 6.0.6 bump could not be installed/tested in this development
  sandbox (no outbound PyPI network access here — confirmed via a direct
  `pip install` attempt, which failed on TLS cert verification, unrelated
  to the package itself). Verification for this specific change relies on
  `backend-ci.yml`'s `Test (Postgres + Redis)` job, which does have real
  network access and will install and run the full suite against the
  bumped version — the same "let CI verify what local tooling can't"
  precedent already used earlier this phase when Docker Desktop was
  transiently unavailable.
- Gitleaks starts advisory (`continue-on-error: true`), matching
  `pip-audit`/`npm audit` — a first scan against full history could
  surface false positives needing a `.gitleaksignore` entry before this
  can safely become a blocking gate; that triage is follow-up work, not
  bundled into this milestone.
