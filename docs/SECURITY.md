# Security Considerations (`SECURITY.md`)

Phase 5k. Consolidates the security posture already implemented across
Phases 0–5j and lists what's known-missing, rather than re-deriving the
auth/RBAC design (see [`AUTH_RBAC.md`](AUTH_RBAC.md) for that).

---

## 1. Authentication & authorization

- **JWT** (`djangorestframework-simplejwt`): access tokens 15 min default
  (`JWT_ACCESS_MINUTES`), refresh 7 days (`JWT_REFRESH_DAYS`), rotation +
  blacklist-on-use/logout. Every endpoint except `/healthz`, `/healthz/worker/`,
  and `/api/auth/login|refresh/` requires a valid Bearer token.
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

**Gap**: no secret-scanning CI step exists (e.g. gitleaks/truffleHog)
verifying no credential has ever been accidentally committed historically.
Recommended addition — see [`ROADMAP.md`](ROADMAP.md).

## 5. Dependency vulnerability posture

CI runs `pip-audit`/`npm audit` on every push, **advisory** (not blocking —
see [`CI_CD.md`](CI_CD.md) §1.2 for why). Current known findings, as of
Phase 5i's first real scan:

- **Backend**: 5 known CVEs in `Django==6.0.5` (fix available: `6.0.6` or
  `5.2.15`). Not yet remediated — a version bump needs its own test pass
  before merging, deliberately not forced through as a side effect of
  adding CI. **Recommended near-term action.**
- **Frontend**: 4 findings (2 moderate, 2 high) — all in dev/transitive
  dependencies (`esbuild`/`vite`'s dev-server-only issue, `form-data`,
  `js-yaml`), none in code actually shipped to end users in the production
  build.

Both are tracked here, not silently ignored — re-check `pip-audit`/`npm
audit` output on every CI run (§7 of `OPERATIONS_RUNBOOK.md`'s weekly
checklist).

## 6. Audit trail integrity

`AuditTrail` is append-only at the model layer (`EmissionRecord.clean()`
blocks all saves once `status=APPROVED`, forcing corrections through the
separate, versioned `EmissionCalculation` table rather than mutating a
locked record). No cryptographic hash-chain exists yet (an earlier project
iteration's README claimed one that didn't actually exist — corrected in
Phase 1; a real hash-chain is tracked as future work, see
[`ROADMAP.md`](ROADMAP.md) / Phase 6 "Enterprise Governance").

## 7. Admin panel exposure

Django Admin (`/admin/`) is reachable at the same host as the API with no
additional network-level restriction (IP allow-list, VPN requirement, etc.)
configured anywhere in this repo. It's protected by Django's own
session-auth + superuser requirement, but a determined attacker gets a
login form to attack. **Recommended**: restrict `/admin/` at the
reverse-proxy/platform level in production (Render doesn't expose this by
default in `render.yaml` today) if this becomes an internet-facing concern
beyond the current single-operator scale.

## 8. Storage & data exposure

Presigned S3 download URLs expire after `AWS_S3_URL_EXPIRE_SECONDS`
(3600s default) — not indefinite links. `StorageService` is the only
sanctioned access path to uploaded files; no code bypasses it to read
storage directly (checked via `grep` — `boto3`/`django-storages` imports
are confined to `apps/core/storage/providers/`).

## 9. Known gaps / recommendations (summary)

1. **`render.yaml`'s Redis service type and cross-service `SECRET_KEY`
   sharing are unverified against Render's live platform** — see
   [`DEPLOYMENT_GUIDE.md`](DEPLOYMENT_GUIDE.md) §3.3. Not a vulnerability in
   shipped code; verify before the first real deploy of the corrected
   blueprint.
2. Django 6.0.5's 5 known CVEs — schedule a version bump.
3. No secret-scanning CI step.
4. No IP/network restriction on `/admin/`.
5. No formal RPO/RTO or tested disaster-recovery drill — see
   [`INCIDENT_RESPONSE.md`](INCIDENT_RESPONSE.md) §2.

None of these were fixed as part of this documentation milestone — see the
Production Readiness Review delivered alongside this milestone for
prioritization.
