# Security Considerations (`SECURITY.md`)

Phase 5k. Consolidates the security posture already implemented across
Phases 0–5j and lists what's known-missing, rather than re-deriving the
auth/RBAC design (see [`AUTH_RBAC.md`](AUTH_RBAC.md) for that).

---

## 1. Authentication & authorization

- **JWT** (`djangorestframework-simplejwt`): access tokens 15 min default
  (`JWT_ACCESS_MINUTES`), refresh 7 days (`JWT_REFRESH_DAYS`), rotation +
  blacklist-on-use/logout. Every endpoint except `/healthz`, `/healthz/worker/`,
  and `/api/auth/login|refresh/` requires a valid Bearer token. **Phase
  6f**: the signing key (`SIMPLE_JWT['SIGNING_KEY']`) is explicit and
  independently rotatable via `JWT_SIGNING_KEY` (falls back to
  `SECRET_KEY` — zero behavior change unless set), so rotating it no
  longer forces also rotating (or being coupled to) the key that signs
  sessions/CSRF tokens. Failed login attempts are logged at `WARNING`
  (username + remote address, never the password).
- **RBAC**: four organization-scoped roles (Org Admin, Analyst, Auditor,
  Viewer) plus a cross-tenant Platform Admin (Django superuser). Enforced
  server-side via DRF permission classes on every viewset/action — the
  frontend's role-aware UI (hiding Upload from Auditor/Viewer, etc.) is
  presentation only, never the actual access control.
- **Multi-tenant isolation**: every domain model carries an `organization`
  FK; `TenantScopedViewSetMixin` filters every queryset server-side.
  Cross-tenant access is tested explicitly (untrusted query params,
  non-member headers, inactive memberships — see `AUTH_RBAC.md`).

## 2. Transport & session security

Fails closed whenever `DEBUG=False`: `SECURE_SSL_REDIRECT`,
`SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE` all default `True`; `SECRET_KEY`
and `DATABASE_URL` are required (boot-time `ImproperlyConfigured` if
missing); `ALLOWED_HOSTS` rejects a wildcard. `CORS_ALLOW_ALL_ORIGINS`
defaults `False` — origins must be explicitly allow-listed.

**Phase 6f**: `SECURE_REFERRER_POLICY` (`same-origin`) and
`SECURE_CROSS_ORIGIN_OPENER_POLICY` (`same-origin`) are now explicit in
`settings.py` rather than left to Django's own (identical) defaults — a
reviewer shouldn't have to know Django's undocumented-in-this-codebase
defaults to confirm the posture. Verified live before making them
explicit: zero runtime behavior change.

**Phase 9c**: `frontend/nginx.conf` (the Docker Compose frontend service)
had no security headers at all. Added `X-Content-Type-Options: nosniff`,
`X-Frame-Options: DENY`, `Referrer-Policy: same-origin` (matching the
backend's own policy above), and `X-XSS-Protection: 1; mode=block` — safe,
standard, zero-ambiguity headers. **Deliberately not added: a
Content-Security-Policy.** A CSP needs every legitimate script/style/
connect-src enumerated and verified in a real browser before shipping —
getting it wrong fails closed (blank page, broken API calls), not open.
Reviewed and left as an open recommendation (§10) rather than guessed at
inside this pass.

## 3. Rate limiting

DRF throttling on every request: `THROTTLE_ANON` (100/hour default),
`THROTTLE_USER` (2000/hour), `THROTTLE_LOGIN` (10/min, scoped specifically
to the login endpoint — the highest-value target for credential stuffing).
Disabled under the test runner only.

## 4. Secrets management

`.gitignore` excludes `**/.env`/`**/*.env` (with an explicit `.env.example`
exception) — verified no real secret has ever been intended to be
committed. Production secrets (`SECRET_KEY`, `DJANGO_SUPERUSER_PASSWORD`,
storage/email credentials) are meant to live in the deployment platform's
own secret store (Render's `sync: false` env vars, or equivalent) — never
in `render.yaml` itself, which IS committed.

**Phase 6f**: `.github/workflows/secret-scan.yml` now runs `gitleaks`
over the full git history on every push — advisory
(`continue-on-error: true`), mirroring `pip-audit`/`npm audit`'s
established pattern (see `CI_CD.md` §1.2). Confirmed clean on a local
100-commit history scan before this workflow was added.

**Phase 9c**: `bootstrap_data`'s admin-creation path already refused to
create a weak-password superuser when `DEBUG=False` and
`DJANGO_SUPERUSER_PASSWORD` was unset — its sibling `--demo-users` path
(creates 4 users, up to `ORG_ADMIN`, and defaults to the publicly-
documented password `demo12345` in README.md/this repo's own source) had
no such guard. `render.yaml` never sets `BOOTSTRAP_DEMO_USERS`, so this
was not an active production exposure, but the code path itself was
structurally unsafe — now mirrors the admin path's fail-closed behavior
exactly (skip + `WARNING` log when `DEBUG=False` and no
`DEMO_USER_PASSWORD` is set).

## 5. Dependency vulnerability posture

CI runs `pip-audit`/`npm audit` on every push, **advisory** (not blocking —
see [`CI_CD.md`](CI_CD.md) §1.2 for why).

- **Backend**: **Phase 6f** bumped `Django` `6.0.5` → `6.0.6`, fixing 5
  CVEs (`PYSEC-2026-197` through `-201`). **Phase 9c** bumped `6.0.6` →
  `6.0.7`, fixing 3 more (`PYSEC-2026-2090/2091/2092` — `CVE-2026-48588`
  cached `Set-Cookie` exposure via `UpdateCacheMiddleware`/`cache_page()`,
  `CVE-2026-53877` `GDALRaster` heap over-read, `CVE-2026-53878`
  `DomainNameValidator` header injection). Verified none of the three
  vulnerable code paths are used anywhere in this codebase (grepped for
  `cache_page`/`UpdateCacheMiddleware`/`GDALRaster`/`contrib.gis`/
  `DomainNameValidator` — zero matches) — this is a defense-in-depth
  upgrade, not an active-exploit fix. Same sandbox network limitation as
  Phase 6f: could not be installed/tested locally, relies on
  `backend-ci.yml`'s real-network job for verification.
- **Frontend**: **Phase 9c** fixed `form-data` (high, `GHSA-hmw2-7cc7-3qxx`,
  CRLF injection via unescaped multipart field names) and `js-yaml`
  (moderate, `GHSA-h67p-54hq-rp68`, quadratic-complexity DoS) via `npm audit
  fix` — both transitive-only (axios/jsdom and eslint respectively),
  resolved without any `package.json` direct-dependency change. **Still
  open**: `esbuild`/`vite` (moderate, `GHSA-67mh-4wv8-2f99` — any website
  can send requests to the Vite *dev server* and read the response). Dev-
  server-only; the production build is always a static `vite build` output
  served by nginx/Vercel, which never runs a dev server. The only fix
  (`npm audit fix --force`) installs `vite@8.1.4`, a 3-major-version jump
  npm itself flags as breaking — deliberately not forced through inside an
  additive hardening pass; needs its own dedicated migration/testing
  effort.

Both are tracked here, not silently ignored — re-check `pip-audit`/`npm
audit` output on every CI run (§7 of `OPERATIONS_RUNBOOK.md`'s weekly
checklist).

## 6. Audit trail integrity

`AuditTrail` is append-only at the model layer (`EmissionRecord.clean()`
blocks all saves once `status=APPROVED`, forcing corrections through the
separate, versioned `EmissionCalculation` table rather than mutating a
locked record). **Phase 6a** added a per-organization cryptographic
SHA-256 hash-chain over `AuditTrail`, making tampering tamper-*evident*
(detectable on verification), plus QuerySet-level blocking of bulk
delete/update and `on_delete=PROTECT` on the organization FK — see
[`GOVERNANCE.md`](GOVERNANCE.md) §6a for the design, the explicit
"detectable, not impossible without an external anchor" trade-off, and the
three verification surfaces (`verify_audit_chain` command,
`GET /api/audit/verify/`, admin action).

**Phase 6b** added a dedicated, immutable `EmissionRecordVersion` model —
a full historical snapshot of a record's business state on every
meaningful edit, enforced immutable with the same two-layer pattern as
`AuditTrail` (instance-level `clean()`/`delete()` blocks, plus
`QuerySet`-level `delete()`/`update()` blocks closing the same bulk-bypass
gap 6a found). The hook lives in `EmissionRecord.save()` itself (not just
known view call sites), so it also covers Django Admin edits, which have
no `readonly_fields` restricting business fields. See
[`GOVERNANCE.md`](GOVERNANCE.md) §6b for the full design and the two gaps
closed.

**Phase 6c** added a fixed Draft → Submitted → Approved/Rejected approval
state machine over `EmissionRecord.status`. The legal-transition graph
(`EmissionRecord.WORKFLOW_TRANSITIONS`) is enforced in `clean()` itself,
not only in `apps.ingestion.services.workflow` — the same reasoning as
6b's Admin-bypass fix: a service-only check would miss Admin edits, direct
ORM use, and any future call site. Every transition creates both an
`AuditTrail` entry (hash-chained, 6a) and an `EmissionRecordVersion`
snapshot (6b) atomically. See
[`GOVERNANCE.md`](GOVERNANCE.md) §6c and
[`docs/adr/0001-fixed-approval-workflow-status-field.md`](adr/0001-fixed-approval-workflow-status-field.md)
for the full design and the breaking change this introduced (approval now
requires submission first).

**Phase 6e** added CSV/JSON compliance reports (`/api/reports/compliance/`,
`.../csv/`), generated on demand — no new persisted table, no new attack
surface beyond a read-only, tenant-scoped, RBAC-gated (`CanViewActivity`:
Org Admin/Auditor) query over already-immutable data (`APPROVED` records
only). Every report embeds a `verify_chain()` snapshot so a reader can
confirm the audit ledger was intact at generation time. See
[`GOVERNANCE.md`](GOVERNANCE.md) §6e and
[`docs/adr/0002-compliance-reports-on-demand-not-persisted.md`](adr/0002-compliance-reports-on-demand-not-persisted.md).

**Phase 6f**: `verify_chain()` now logs `CRITICAL` the instant it detects a
broken chain — previously neither `GET /api/audit/verify/` nor the
`verify_audit_chain` command logged anything when a tamper was found, only
returned it in the response/stdout. Lives in the shared service, so both
callers get it automatically.

**Phase 6d** added reversible soft deletion for `EmissionRecord` and, in
the process, closed a real pre-existing gap: `EmissionRecord.organization`/
`.batch` and `EmissionCalculation.emission_record` were all
`on_delete=CASCADE` with no delete restrictions in Django Admin — deleting
an `UploadBatch` or an `Organization` would have silently destroyed every
governed record and calculation underneath it. All three now `PROTECT`,
`EmissionRecord.delete()` raises unconditionally (matching `AuditTrail`/
`EmissionRecordVersion`'s established immutability pattern), and a new
`EmissionRecordQuerySet` blocks bulk delete/update (the latter closing a
gap dating back to 6c: bulk `.update()` bypasses `clean()`/`save()`
entirely, so it could change governed fields with no audit trail entry and
no version snapshot). Soft-deleted records are excluded from dashboards,
the active calculations list, and record exports, but **remain in
compliance reports** — preserving historical, certified data is the point.
See [`GOVERNANCE.md`](GOVERNANCE.md) §6d and
[`docs/adr/0004-soft-delete-orthogonal-fields.md`](adr/0004-soft-delete-orthogonal-fields.md).

## 7. Admin panel exposure

Django Admin (`/admin/`) is reachable at the same host as the API with no
additional network-level restriction (IP allow-list, VPN requirement, etc.)
configured anywhere in this repo. It's protected by Django's own
session-auth + superuser requirement, but a determined attacker gets a
login form to attack. **This is an infrastructure-layer fix, not an
application one** — see
[`docs/INFRASTRUCTURE_SECURITY.md`](INFRASTRUCTURE_SECURITY.md) §1 for the
recommendation and, specifically, why a Django middleware is the *wrong*
layer to solve this at (Phase 6f considered and rejected implementing this
in Django).

## 8. Storage & data exposure

Presigned S3 download URLs expire after `AWS_S3_URL_EXPIRE_SECONDS`
(3600s default) — not indefinite links. `StorageService` is the only
sanctioned access path to uploaded files; no code bypasses it to read
storage directly (checked via `grep` — `boto3`/`django-storages` imports
are confined to `apps/core/storage/providers/`).

## 9. Privileged-operation input validation

**Phase 6f**:

- **CSV/"formula injection"** (OWASP-documented: a CSV cell starting with
  `=`, `+`, `-`, `@`, tab, or CR can be interpreted as a formula by Excel/
  Sheets): `apps/core/csv_security.sanitize_csv_cell()` is applied to
  every string cell in both CSV exports (`RecordExportView`,
  `ComplianceReportCSVView`). This was a real exposure, not theoretical —
  `UploadBatch.file_name` is user-controlled at upload time and was
  written into `RecordExportView`'s CSV verbatim before this fix.
- **Unbounded free-text input**: the `reason` field accepted by the
  approval-workflow actions (`submit`/`approve`/`reject`) is now capped at
  1000 characters at the serializer layer — this text flows straight into
  the hash-chained `AuditTrail` ledger and an immutable
  `EmissionRecordVersion` snapshot; nothing legitimate needs an
  arbitrarily large justification string.
- **Reviewed and confirmed already solid** (documented so it doesn't get
  "fixed" again without re-verifying): an invalid UUID in a privileged
  action's URL (e.g. `POST /api/records/not-a-uuid/submit/`) already
  returns a clean `400` — DRF's default exception handler converts the
  Django `ValidationError` `UUIDField.get_prep_value()` raises into a
  structured response, verified empirically, not assumed.

See [`GOVERNANCE.md`](GOVERNANCE.md) §6f for the full list of what changed
this milestone.

## 10. Known gaps / recommendations (summary)

**Fixed in Phase 6f**: the 3 dead `FEATURE_*` flags, Django's 5 known
CVEs (bumped to `6.0.6`), no secret-scanning CI step (added, advisory).

**Fixed in Phase 9c**: Django's 3 more known CVEs (bumped to `6.0.7`),
`form-data`/`js-yaml` frontend CVEs, `bootstrap_data --demo-users`'
missing fail-closed guard, missing `nginx.conf` security headers.

**Still open — application-level, deliberately deferred pending dedicated
follow-up (not guessed at inside an additive hardening pass)**:

1. **No Content-Security-Policy** on the frontend (§2). Needs every
   legitimate script/style/connect-src enumerated and verified in a real
   browser — a wrong CSP fails closed (breaks the app), so this needs its
   own browser-verified rollout, not a blind addition.
2. **`esbuild`/`vite` dev-server CVE** (§5, moderate, dev-only exposure).
   Fix requires a 3-major-version Vite bump (`5.4` → `8.1`) with its own
   migration/testing effort.

**Still open — infrastructure-layer, not application code** (see
[`docs/INFRASTRUCTURE_SECURITY.md`](INFRASTRUCTURE_SECURITY.md) for the
full write-up of each):

1. No IP/network restriction on `/admin/`.
2. No formal RPO/RTO or tested disaster-recovery drill — see
   [`INCIDENT_RESPONSE.md`](INCIDENT_RESPONSE.md) §2.
3. **`render.yaml`'s Redis service type and cross-service `SECRET_KEY`
   sharing are unverified against Render's live platform** — see
   [`DEPLOYMENT_GUIDE.md`](DEPLOYMENT_GUIDE.md) §3.3. Not a vulnerability
   in shipped code; verify before the first real deploy of the corrected
   blueprint.
