# Authentication, RBAC & Multi-Tenancy (`AUTH_RBAC.md`)

ScopeTrace uses stateless JWT authentication, role-based authorization, and
server-side tenant isolation. This document describes the model, the request
flow, and the permission matrix.

---

## 1. Identity model

| Entity | Purpose |
| :--- | :--- |
| `User` (Django built-in) | Authentication principal (username, password hash). |
| `Organization` (core) | Tenant boundary. |
| `Membership` (accounts) | Binds a `User` to an `Organization` with a `role` and an `active` flag. Unique per (user, organization). |

- **Platform Admin** is modeled as a Django **superuser** (`is_superuser=True`) — a cross-tenant operator, intentionally *not* a membership role.
- A regular user accesses an organization **only** through an active `Membership`.

Passwords are hashed by Django's configured hashers (PBKDF2 by default); plaintext is never stored.

---

## 2. Authentication (JWT / SimpleJWT)

Access + refresh tokens with rotation and blacklist-on-logout.

| Endpoint | Method | Auth | Body | Returns |
| :--- | :--- | :--- | :--- | :--- |
| `/api/auth/login/` | POST | none | `{username, password}` | `{access, refresh, user}` |
| `/api/auth/refresh/` | POST | none | `{refresh}` | `{access, refresh}` (rotated) |
| `/api/auth/logout/` | POST | Bearer | `{refresh}` | `205` (refresh blacklisted) |
| `/api/me/` | GET | Bearer | — | user profile, memberships, active org + role |

Configuration (`settings.SIMPLE_JWT`): access lifetime **15 min** (env `JWT_ACCESS_MINUTES`), refresh **7 days** (`JWT_REFRESH_DAYS`), `ROTATE_REFRESH_TOKENS=True`, `BLACKLIST_AFTER_ROTATION=True`. **Phase 6f**: the signing key is explicit and independently rotatable via `JWT_SIGNING_KEY` (falls back to `SECRET_KEY`); failed login attempts are logged at `WARNING` (username + remote address only) — see [`SECURITY.md`](SECURITY.md) §1.

### Request flow

```
POST /api/auth/login   {username, password}
      -> 200 {access (15m), refresh (7d), user{...memberships}}

GET  /api/records/     Authorization: Bearer <access>
      -> 200 (scoped to the user's active organization)

# access expired:
GET  /api/records/     -> 401
POST /api/auth/refresh {refresh}
      -> 200 {access (new), refresh (new; old one blacklisted)}
      retry original request with the new access token

POST /api/auth/logout  {refresh}   -> 205  (refresh blacklisted; reuse -> 401)
```

The frontend automates the 401→refresh→retry cycle in the axios response
interceptor and forces a logout when refresh fails.

---

## 3. Roles & permission matrix

Roles are enforced at the API layer via DRF permission classes
(`apps/accounts/permissions.py`). Reads are available to every member; writes
and approvals are role-gated.

| Capability | Platform Admin | Org Admin | ESG Analyst | Auditor | Viewer |
| :--- | :---: | :---: | :---: | :---: | :---: |
| Read records / batches / data sources | ✅ (all orgs) | ✅ | ✅ | ✅ | ✅ |
| Upload files (`/api/upload/*`) | ✅ | ✅ | ✅ | ❌ | ❌ |
| Submit records for approval (`/records/{id}/submit`) | ✅ | ✅ | ✅ | ❌ | ❌ |
| Approve / reject records (`/records/{id}/approve`, `/reject`) | ✅ | ✅ | ✅ | ✅ | ❌ |
| Manage org resources (write) | ✅ | ✅ | ❌ | ❌ | ❌ |
| View compliance reports (`/reports/compliance`) | ✅ | ✅ | ❌ | ✅ | ❌ |
| Soft-delete / restore records, view trash (`/records/{id}` DELETE, `/restore`, `?deleted=true`) | ✅ | ✅ | ❌ | ❌ | ❌ |
| Cross-tenant access | ✅ | ❌ | ❌ | ❌ | ❌ |

Permission classes: `IsOrgMember` (base), `CanUpload`, `CanApprove`,
`CanManageOrgResources`, `CanViewActivity`, `IsOrgAdmin`. Each also
implements `has_object_permission` to verify an object belongs to the
request's active organization. `submit` reuses `CanUpload` (the same roles
that prepare data decide when it's ready for review); `approve`/`reject`
reuse `CanApprove`, unchanged from Phase 2/3 — Phase 6c added the formal
Draft → Submitted → Approved/Rejected state machine (see
[`GOVERNANCE.md`](GOVERNANCE.md) §6c) without changing who is allowed to
approve. Compliance reports (Phase 6e) reuse `CanViewActivity` (Org Admin +
Auditor) — the same roles that can view the audit-trail activity feed and
verify the hash chain — not the broader
`IsOrgMember` the dashboards/metrics endpoints use. Soft-delete/restore
(Phase 6d) use a new `IsOrgAdmin` class (Org Admin only, *every* method) —
deliberately not `CanManageOrgResources`, which allows reads to any member
and only restricts writes; reusing it for the `?deleted=true` list would
have incorrectly let any member view it, since listing is a `GET`.

---

## 4. Tenant isolation

The active organization is resolved **server-side** for every request
(`apps/accounts/tenancy.resolve_tenant_context`):

1. **Platform admins**: unscoped by default (all orgs); may narrow to one org via the `X-Organization-ID` header.
2. **Regular users**: the org must be one of their *active* memberships. An `X-Organization-ID` header, if present, is validated against those memberships (else `403`); otherwise the first active membership is used. No active membership → `403`.

Enforcement is layered (defense in depth):

- **Queryset scoping** — `TenantScopedViewSetMixin.get_queryset()` filters every list/detail queryset to the active org.
- **Object-level checks** — `has_object_permission` (and an explicit `check_object_permissions` in the `approve` action) reject objects outside the active org.
- **Upload guard** — the target `DataSource` must belong to the active org.

**Untrusted inputs:** the previous `?organization=` query parameter has been
removed. Client-supplied organization ids are never used to widen access; the
`X-Organization-ID` header only *narrows* within what a user is already
authorized to see.

Result: a user in Org A cannot list, retrieve, or approve Org B's data —
verified by the `TenantIsolationTests` suite (scoped lists, `404` on cross-org
retrieve, `403` on cross-org approve, ignored query param, rejected header,
inactive-membership denial).

---

## 5. Frontend integration

- `AuthContext` holds session state and exposes `canUpload` / `canApprove` derived from the active role.
- Tokens are stored in `localStorage`; the access token is attached as `Bearer` on every request; refresh is automatic on `401`.
- The app is gated behind authentication (login page for unauthenticated users); navigation is role-aware (Upload hidden for Auditor/Viewer); the profile dropdown shows the user, role, organization, and Sign out.
