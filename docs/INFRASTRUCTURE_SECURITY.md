# Infrastructure Security Recommendations (`INFRASTRUCTURE_SECURITY.md`)

Phase 6f. Deliberately **separate** from [`SECURITY.md`](SECURITY.md),
which documents what's actually implemented in this Django application.
Everything here is an operator/platform-level action — outside this
codebase, and outside what Django application code can (or should)
enforce. See [`docs/adr/0003-security-hardening-scope.md`](adr/0003-security-hardening-scope.md)
for why this split exists: Phase 6f's instructions were explicit that
infrastructure features (WAF, firewall, IP allow-lists) do not belong
inside Django "unless there is a strong technical reason," and none of
the items below meet that bar — they're all better, more completely, and
more cheaply solved at the reverse-proxy/platform layer, which every
realistic deployment target (Render, or any PaaS/load balancer) already
provides.

---

## 1. Restrict `/admin/` at the network edge, not in Django

**Recommendation**: block or gate `/admin/` before it ever reaches the
Django process — an IP allow-list, VPN/tunnel requirement, or a
reverse-proxy `Basic-Auth` wall in front of that one path.

**Why not a Django middleware instead**: a middleware-based IP check still
costs a full TLS handshake, a full HTTP request parse, and a worker/thread
slot before rejecting the request — every one of those costs is avoided
entirely if the reverse proxy (or the platform's own edge, e.g. a Render
static IP restriction, Cloudflare Access, or a VPN-only ingress) drops the
connection first. This is also more robust: an edge-level block covers
`/admin/` regardless of which Django app happens to be handling it,
survives a future refactor that moves admin registration around, and
can't be bypassed by a Django-level misconfiguration.

**Current status**: not configured in `render.yaml` today. Recommended
before this becomes an internet-facing concern beyond the current
single-operator scale (see [`SECURITY.md`](SECURITY.md) §7).

## 2. Disaster recovery: formal RPO/RTO + a tested drill

**Recommendation**: define a target Recovery Point Objective / Recovery
Time Objective for the production database, and actually run a restore
drill against them at least once.

**Why this isn't application code**: RPO/RTO are backup/infrastructure
policy — they're about how the managed Postgres provider's backup
schedule and restore procedure are configured, not about anything Django
does at runtime. Already tracked as a known gap in
[`INCIDENT_RESPONSE.md`](INCIDENT_RESPONSE.md) §2; repeated here only so
this document is a complete index of infrastructure-level work, not
because it's new.

## 3. `render.yaml`'s still-unverified specifics

Two lines in `render.yaml` remain marked `# VERIFY:` since the Phase 5
closeout — Render's blueprint keyword for a managed Redis service, and
cross-service `SECRET_KEY` sharing via `fromService`. Confirming these
requires a live Render deploy, which this milestone (like the Phase 5
closeout before it) doesn't have access to. See
[`DEPLOYMENT_GUIDE.md`](DEPLOYMENT_GUIDE.md) §3.3.

## 4. TLS/CDN-layer protections

Not evaluated in this codebase at all — rate limiting exists at the
Django/DRF layer (`docs/SECURITY.md` §3) but a determined volumetric
attack is a platform/CDN concern (e.g. Cloudflare, or whatever sits in
front of the deployed app) — a `UserRateThrottle` inside the WSGI process
is the wrong layer to defend against that class of traffic; it's listed
here for completeness, not because it's a currently-open finding from any
specific review.

---

## Why the split matters

Every item above is a "someone with deploy/platform access needs to
configure this" action item, not a code change a future PR can make. Each
prior "known gaps" list (`SECURITY.md` §9, `ROADMAP.md` §1) mixed these in
with genuine application-code TODOs, which made it easy to defer both
categories together. This document exists so an operator can work through
platform-level hardening independently of whatever's currently in a code
review queue.
