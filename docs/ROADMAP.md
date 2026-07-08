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
| ~~No secret-scanning CI step~~ | **Added in Phase 6f** — `gitleaks` over full git history, advisory (see `CI_CD.md` §1.2) | [`SECURITY.md`](SECURITY.md) §4 |
| `render.yaml`'s `type: redis` and cross-service `SECRET_KEY` sharing are unverified against Render's live platform (no deploy access) | Infrastructure-layer, not application code — see Phase 6f's split | [`INFRASTRUCTURE_SECURITY.md`](INFRASTRUCTURE_SECURITY.md) §3 |
| No formal RPO/RTO, no tested DR drill | Infrastructure-layer, not application code | [`INFRASTRUCTURE_SECURITY.md`](INFRASTRUCTURE_SECURITY.md) §2 |
| ~~5 known Django CVEs unpatched~~ | **Fixed in Phase 6f** — bumped `Django` to `6.0.6` (patch release) | [`SECURITY.md`](SECURITY.md) §5 |
| ~~Three `FEATURE_*` flags declared in settings but read nowhere~~ | **Removed in Phase 6f** | [`GOVERNANCE.md`](GOVERNANCE.md) §6f |
| No IP/network restriction on `/admin/` | Infrastructure-layer, not application code — a Django middleware was considered and rejected for this specifically | [`INFRASTRUCTURE_SECURITY.md`](INFRASTRUCTURE_SECURITY.md) §1 |
| ~~No cryptographic audit hash-chain~~ | **Implemented in Phase 6a** — per-org SHA-256 chain, tamper-evident, with verification command/API/admin action | [`GOVERNANCE.md`](GOVERNANCE.md) §6a |
| ~~No historical record versioning~~ | **Implemented in Phase 6b** — immutable `EmissionRecordVersion` snapshots on every meaningful edit, list/retrieve/compare APIs | [`GOVERNANCE.md`](GOVERNANCE.md) §6b |
| ~~No formal approval workflow beyond single-step approve/lock~~ | **Implemented in Phase 6c** — fixed Draft → Submitted → Approved/Rejected state machine, enforced at the model layer | [`GOVERNANCE.md`](GOVERNANCE.md) §6c |
| ~~No compliance reporting~~ | **Implemented in Phase 6e** — CSV/JSON compliance reports over APPROVED-only data, no new tables (PDF still deferred) | [`GOVERNANCE.md`](GOVERNANCE.md) §6e |
| ~~No soft delete / hard deletion of governed data was possible~~ | **Implemented in Phase 6d** — reversible soft delete, `PROTECT` closes the org/batch cascade-delete bypass; no automated purge (retention policy documented, purge deliberately deferred) | [`GOVERNANCE.md`](GOVERNANCE.md) §6d |
| Seed emission factors are an illustrative DEFRA 2024 subset, not the full official dataset | Documented since Phase 3 | [`CARBON_ENGINE_DESIGN.md`](CARBON_ENGINE_DESIGN.md) |
| No read-replica / DB routing support | Not needed at current scale | [`OPERATIONS_RUNBOOK.md`](OPERATIONS_RUNBOOK.md) §8 |
| README screenshots are stock placeholder images | Explicitly marked as TODO in the file itself, awaiting a real deployment to screenshot | `README.md` |

---

## 2. Future Roadmap

Phases 0–6 (rebrand/infra → correctness fixes → auth/RBAC → carbon engine →
metrics/analytics → production engineering: async processing, retries/DLQ,
scheduling, notifications, monitoring, CI/CD, Docker, documentation →
enterprise governance) are complete. What's next, as currently planned:

- **Phase 6 — Enterprise Governance** *(complete)*: full audit timeline UI,
  a real cryptographic immutable audit hash-chain (see §1, done in 6a),
  immutable version history on records (done in 6b), a formal Draft →
  Submitted → Approved/Rejected approval workflow (done in 6c), CSV/JSON
  compliance reports (done in 6e; PDF still deferred), security hardening
  (done in 6f), reversible soft delete (done in 6d), governance docs
  closeout (6g), a Phase 6h hotfix milestone (frontend workflow contract
  fix, metrics cache invalidation, a lightweight Vitest test foundation).
- **Phase 7 — AI**: AI anomaly detection, AI recommendations, an AI ESG
  assistant, AI-assisted report generation, AI-assisted validation. The
  carbon calculation pipeline's `AIRecommendationStage` has been an inert,
  reserved seam for this since Phase 3 — see
  [`CARBON_ENGINE_DESIGN.md`](CARBON_ENGINE_DESIGN.md). **7a (AI Foundation
  & Governance Seam) is done**: provider-agnostic LLM gateway, schema-
  enforced responses, per-tenant policy/budget/egress, full call-
  reproducibility audit trail — advisory-only, no feature implemented yet.
  See [`AI_ARCHITECTURE.md`](AI_ARCHITECTURE.md). **7a.5 (AI Evaluation
  Infrastructure) is done**: golden datasets + automatic prompt-regression
  detection for all five planned capabilities, deterministic replay
  providers, a formal I1–I6 invariant suite intended as a merge gate for
  every future AI milestone, an LLM-as-Judge framework (built, tested,
  disabled by default), and a two-tier CI split (deterministic checks
  blocking, LLM-judge/qualitative checks advisory) — still no AI feature
  implemented, only the harness every future capability must pass. See
  [`AI_EVALUATION.md`](AI_EVALUATION.md). **7b (Advisory AI Anomaly
  Detection) is done**: the first real Phase 7 capability. The
  deterministic engine still decides `is_suspicious` (unchanged); AI only
  explains why, via a new `anomaly_detection` capability (schema v2 --
  explanation/contributing factors/confidence/suggested investigation,
  never a classification), dispatched fire-and-forget from `ingest_task`'s
  success path (never inline in the calculation pipeline --
  `AIRecommendationStage` remains inert), persisted as immutable
  `AIAnnotation` rows, surfaced read-only via
  `GET /api/records/{id}/ai-annotations/` and a new "AI Insights" panel in
  the existing records detail drawer. See AI_ARCHITECTURE.md §12 and ADR
  0009. **7c (AI Emission Factor Recommendation) is done**: the second real
  Phase 7 capability. The deterministic engine
  (`apps.carbon.services.resolution.FactorIndex`) still decides which
  factor a calculation actually uses (unchanged); AI only recommends a
  candidate for records left `UNRESOLVED_NO_FACTOR`, via a new
  `factor_recommendation` capability (schema v2 -- the AI picks a
  service-provided candidate LABEL, never a raw factor identifier),
  dispatched fire-and-forget from `calculate_task`'s success path (never
  inline in the calculation pipeline), persisted as immutable
  `AIFactorRecommendation` rows (a new dedicated model, nullable
  `recommended_factor`), surfaced read-only via
  `GET /api/records/{id}/factor-recommendations/` and a second sub-section
  in the same "AI Insights" panel. See AI_ARCHITECTURE.md §13 and ADR
  0010. **7d (AI Validation Assistant) is done**: the third real Phase 7
  capability. The deterministic validator
  (`apps.ingestion.services.validator.RowValidator`) still decides which
  rows are `FAILED` (unchanged); AI only explains why and suggests a
  correction, via a new `validation_assistance` capability (schema v2 --
  explanation/affected fields/confidence/suggested correction), dispatched
  fire-and-forget from `ingest_task`'s success path alongside the
  anomaly-explanation dispatch (never `calculate_task` -- `FAILED` is a
  validation-time decision). Reuses `AIAnnotation` with a second
  `Capability` choice rather than a new model (unlike 7c's dedicated
  `AIFactorRecommendation`, every output here already fit the existing
  columns), so no new endpoint either -- the existing
  `GET /api/records/{id}/ai-annotations/` already returns both
  capabilities, split client-side into a third "AI Insights" panel
  sub-section. See AI_ARCHITECTURE.md §14 and ADR 0011. **7e (ESG
  Assistant / RAG) is done**: the fourth real Phase 7 capability, and the
  first with a genuinely different shape -- conversational, user-
  initiated, no single governed record to attach output to. Retrieval is
  deterministic structured retrieval against already tenant/RBAC/soft-
  delete/approval-aware services (`MetricsService`, the compliance-report
  query pattern), not a vector store. `ask_esg_assistant()` runs
  synchronously (not queued -- a human is waiting for the answer in the
  same request), persisting the question unconditionally and the answer
  only on success, as immutable `AIConversationMessage` rows grouped
  under a plain `AIConversation` container. apps.ai gained its own first
  API views (`/api/esg-assistant/conversations/...`, gated by `CanUseAI`)
  and a new dedicated ESG Assistant page. See AI_ARCHITECTURE.md §16 and
  ADR 0012. **7f (AI Report Narration) is done**: the fifth and final
  planned real Phase 7 capability. Compliance reports themselves stay
  on-demand query results (unchanged, per ADR 0002); AI only narrates
  them, via a new `report_narration` capability (schema v2 -- executive
  summary/key highlights/trend explanations/recommendations, each its
  own labeled section) built ONLY from approved data
  (`compliance_summary()` reused directly, never the broader
  `MetricsService`). Dispatched async from a NEW API action
  (`POST /api/report-narration/regenerate/`, since compliance reports
  have no pipeline event to hook a dispatch into), persisted as
  immutable `AIReportNarration` history, surfaced read-only via
  `GET /api/report-narration/` and a new AI Narrative sub-section in the
  existing Reports dashboard widget. RBAC matches the compliance report
  itself (`CanViewActivity`, not the broader `CanUseAI` every other
  capability uses) since narration is commentary on that same gated
  artifact. See AI_ARCHITECTURE.md §18 and ADR 0013. 7g (observability/
  cost governance) remains -- the last planned Phase 7 sub-milestone.
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
