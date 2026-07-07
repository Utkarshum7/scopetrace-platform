# Known Limitations & Future Roadmap (`ROADMAP.md`)

Phase 5k. Replaces the placeholder "Future Improvements" list that used to
live in `README.md` (speculative items — Kafka streaming, a private
blockchain ledger — that were never actually part of this project's real,
executed phase plan). This document reflects the plan actually being
followed.

---

## 1. Known Limitations

Honestly disclosed, cross-referenced to where each is discussed in more
depth:

| Limitation | Why | Detail |
|---|---|---|
| No fine-grained upload progress (0% → 100% jump, not "42 of 100 rows") | Ingestion runs as one atomic transaction by design (all-or-nothing is what makes retries safe) | [`JOB_LIFECYCLE.md`](JOB_LIFECYCLE.md) §2 |
| No WebSocket/SSE push for progress — polling only | Deliberate scope decision, not an oversight | [`TRADEOFFS.md`](TRADEOFFS.md) §1 |
| Batch cancellation is declared but inert (no cancel endpoint, no task revocation) | Reserved interface, same pattern as the carbon engine's AI stages | [`JOB_LIFECYCLE.md`](JOB_LIFECYCLE.md) §6 |
| No frontend automated test suite | Never built — `npm run build`/`lint` are the only frontend CI gates today | [`CI_CD.md`](CI_CD.md) |
| No secret-scanning CI step | Not yet added | [`SECURITY.md`](SECURITY.md) §9 |
| `render.yaml`'s `type: redis` and cross-service `SECRET_KEY` sharing are unverified against Render's live platform (no deploy access) | Fixed in the Phase 5 closeout milestone; two specific lines flagged `# VERIFY:` rather than asserted with false confidence | [`DEPLOYMENT_GUIDE.md`](DEPLOYMENT_GUIDE.md) §3.3 |
| No formal RPO/RTO, no tested DR drill | Not yet formalized | [`INCIDENT_RESPONSE.md`](INCIDENT_RESPONSE.md) §2 |
| 5 known Django CVEs unpatched (fix available) | Advisory CI finding, not yet remediated | [`SECURITY.md`](SECURITY.md) §5 |
| Three `FEATURE_*` flags declared in settings but read nowhere | Dead code from Phase 2/3's actual (unconditional) implementation | [`DEPLOYMENT_GUIDE.md`](DEPLOYMENT_GUIDE.md) §4.9 |
| ~~No cryptographic audit hash-chain~~ | **Implemented in Phase 6a** — per-org SHA-256 chain, tamper-evident, with verification command/API/admin action | [`GOVERNANCE.md`](GOVERNANCE.md) §6a |
| ~~No historical record versioning~~ | **Implemented in Phase 6b** — immutable `EmissionRecordVersion` snapshots on every meaningful edit, list/retrieve/compare APIs | [`GOVERNANCE.md`](GOVERNANCE.md) §6b |
| Seed emission factors are an illustrative DEFRA 2024 subset, not the full official dataset | Documented since Phase 3 | [`CARBON_ENGINE_DESIGN.md`](CARBON_ENGINE_DESIGN.md) |
| No read-replica / DB routing support | Not needed at current scale | [`OPERATIONS_RUNBOOK.md`](OPERATIONS_RUNBOOK.md) §8 |
| README screenshots are stock placeholder images | Explicitly marked as TODO in the file itself, awaiting a real deployment to screenshot | `README.md` |

---

## 2. Future Roadmap

Phases 0–5 (rebrand/infra → correctness fixes → auth/RBAC → carbon engine →
metrics/analytics → production engineering: async processing, retries/DLQ,
scheduling, notifications, monitoring, CI/CD, Docker, this documentation
pass) are complete. What's next, as currently planned:

- **Phase 6 — Enterprise Governance**: full audit timeline UI, a real
  cryptographic immutable audit hash-chain (see §1, done in 6a), immutable
  version history on records (done in 6b), soft delete, a formal approval
  workflow beyond single-step approve/lock, compliance report generation.
- **Phase 7 — AI**: AI anomaly detection, AI recommendations, an AI ESG
  assistant, AI-assisted report generation, AI-assisted validation. The
  carbon calculation pipeline's `AIRecommendationStage` has been an inert,
  reserved seam for this since Phase 3 — see
  [`CARBON_ENGINE_DESIGN.md`](CARBON_ENGINE_DESIGN.md).
- **Phase 8 — UX**: accessibility audit, responsive design pass, theming,
  saved/custom dashboards, an in-app notification center (distinct from
  Phase 5g's email notifications — a UI-visible feed, not a new delivery
  channel; see [`NOTIFICATIONS.md`](NOTIFICATIONS.md) for why that's a
  clean extension point, not a redesign).
- **Phase 9 — Observability**: Prometheus/Grafana/Loki/OpenTelemetry/Sentry,
  structured logging beyond today's `logging`-module-based approach. Flower
  (Phase 5h) and the two health endpoints are today's observability
  surface; this phase is the "real" production-grade layer on top.
- **Phase 10 — Production Release**: a real landing page, a demo
  environment, published architecture diagrams (this document's Mermaid
  diagrams are a starting point), API documentation (OpenAPI/Swagger —
  DRF's schema generation isn't wired up yet), real screenshots (replacing
  §1's placeholder), a video demo, GitHub release notes.

Each phase, as with every one so far, will get its own architecture review,
explicit design decisions where trade-offs exist, small focused commits,
and a milestone-specific implementation report before merge — this
project's established, unchanged workflow throughout Phases 0–5.
