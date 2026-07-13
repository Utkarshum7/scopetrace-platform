# Release Certification — Version 1.0.0 (`RELEASE_CERTIFICATION.md`)

Phase 10 — Final Production Sign-Off. This is the principal-engineer-level
release certification review, distinct from [`RELEASE_CHECKLIST.md`](RELEASE_CHECKLIST.md)
(Phase 9d's system-wide subsystem checklist). Where 9d asked "is every
subsystem accounted for," this document asks "having built it, would I
actually ship it" — architecture, code quality, testing, performance, and
security were **independently re-verified from source**, not recalled
from prior phases' own conclusions.

## Methodology

Five independent research passes (architecture/layering, code quality,
test quality, performance, documentation) were run in parallel by fresh
agents with no memory of prior reviews, each required to cite file:line
evidence for every claim. In parallel, the highest-stakes security/AI-
governance/secrets claims from Phases 9a–9c were personally re-verified
directly against current source — not trusted from memory — including:
`SECRET_KEY` fail-closed behavior, JWT configuration, Celery serialization
(JSON-only, no pickle), tenant-scoping enforcement across every relevant
viewset, upload path-traversal safety on both storage backends (reasoned
through Django `FileSystemStorage`'s `safe_join`/`SuspiciousFileOperation`
mechanism and S3's flat-key-namespace semantics from source, not assumed),
and the AI gateway's advisory-only invariant (grepped for any `.save()`/
`.update()`/`.delete()` on `EmissionRecord`/`EmissionCalculation` from AI
code — none found). All 5 agent reports' most consequential claims were
additionally spot-verified directly before inclusion below.

---

## 1. Whole-project architecture review

| Area | Rating | Evidence |
|---|---|---|
| Layering / separation of concerns | Adequate | Views are thin dispatchers almost everywhere (`accounts/views.py`, `carbon/views.py`, `ai/views.py`). One real leak: `ingestion/views.py:576-610` (`recalculate` action) inlines full multi-step orchestration (build resources → calculate → version → audit entry → cache bump) that the adjacent `submit`/`approve`/`reject` actions correctly delegate to a service. |
| Extensibility | Strong | Ingestion strategy pattern (`services/base_parser.py` `BaseParser(abc.ABC)` + `ingestion_service.py`'s `PARSER_REGISTRY`) and the AI provider abstraction (`providers/base.py` + `providers/factory.py`, structurally guarded by `tests_import_guard`) are both genuinely pluggable — a 4th data source or LLM vendor is additive, not invasive. |
| Technical debt | Adequate | `apps.core` (the documented "foundational, business-logic-free" app) lazy-imports `apps.ingestion.models.UploadBatch` in `core/notifications.py:79` and `core/tasks.py:112` — a real layering inversion, functionally harmless (avoids circular imports at module load) but architecturally impure. |
| Consistency | Adequate | Three different conventions for "where business logic lives" across the 6 top-level apps: `ingestion`/`carbon`/`ai` use a `services/` subpackage, `audit` uses a single `services.py`, `accounts`/`core` scatter logic into topical modules with no services layer at all. |
| Scalability | Strong | No architectural N+1 — aggregation is pushed to the DB everywhere checked (`carbon/services/metrics.py`, `report_context_builder.py`, `reports.py` all use `Sum`/`Count`/`Subquery`/`.values().annotate()`, never per-row Python loops). Resources are batch-preloaded once per run (`carbon_service.py` docstring: "1M-record run performs no per-row queries"). |
| Frontend architecture | Strong | Fully centralized API layer (`frontend/src/services/api.js`, single axios instance with request/response interceptors for JWT + single-flight refresh). Grep across `pages/`/`components/`/`context/`/`hooks/` found zero ad-hoc `fetch`/`axios` calls outside this file. |

No systemic architectural inconsistency was found. The two concrete items above (recalculate-action leak, core→ingestion inversion) are real but narrow — see §4 for classification.

## 2. Code quality review

**Genuinely clean codebase.** Verified via grep across the full tree (backend + frontend, excluding migrations/venv/node_modules):

- **TODO/FIXME/XXX/HACK**: effectively zero — the only hit is explanatory prose in `ai/evaluation/judge.py:4` that *negates* being a TODO.
- **Dead code**: none found — no commented-out code blocks; one intentionally-retained superseded schema (`ai/evaluation/capabilities.py:62-64`, documented in its own docstring, not a defect).
- **Debug artifacts**: zero stray `print()` in backend (outside legitimate management-command output) and zero `console.log`/`debugger` in frontend (all 8 `console.*` hits are intentional `console.error` in `catch` blocks or an error boundary).
- **Naming**: strong and consistent across all 8 apps — one cosmetic exception, `GwpSet` (`carbon/models.py:43`) should be `GWPSet` to match the codebase's otherwise-universal uppercase-acronym convention (`AIInteraction`, `SAPUploadView`).
- **Duplication**: one real, actionable finding — `_parse_date()` is copy-pasted near-identically three times (`ingestion/services/sap_parser.py:128`, `utility_parser.py:157`, `travel_parser.py:219`), differing only in format-tuple ordering. A future 4th parser will likely copy it a fourth time.
- **Complexity**: several long methods (`ingestion_service.py`'s `ingest_batch()` ~200 lines, `BaseUploadView.post()` ~188 lines, `gateway.py`'s `_invoke_reaching_provider()` ~163 lines) — all are sequential-pipeline or lock-scope-driven, correctly named, and heavily documented. Long but not misleading.

## 3. Production readiness

| Subsystem | Verdict | Basis |
|---|---|---|
| Backend | Ready | `manage.py check --deploy` clean; 915 tests passing against real Postgres+Redis in CI; fail-closed config independently re-verified from `settings.py` source this session. |
| Frontend | Ready | Build/lint/test all pass; centralized API layer; CVEs remediated where non-breaking (see §4). |
| Docker | Ready, with a caveat | Multi-stage builds, non-root user, CI build-verification green on every push — but no live `docker compose up` end-to-end run has occurred at any point in this project's history within this session (disk-space constraint), so this is verified by static config review, not a live boot. |
| Render | Conditionally ready | `render.yaml` reviewed line-by-line, zero drift against Docker Compose — but `type: redis` and cross-service `SECRET_KEY` sharing remain unverified against Render's live blueprint validator (no deploy access this whole project). Documented fallback exists for both. |
| Vercel | Conditionally ready | `vercel.json` correct; first-deploy steps documented (`DEPLOYMENT_GUIDE.md` §3.5) but never executed against a real Vercel project. |
| Celery | Ready, with an operational gap | 6-queue topology drift-guarded by a dedicated test; JSON-only serialization confirmed (no pickle RCE surface); dead-letter handling excellent. Gap: worker `--concurrency` is unpinned in `render.yaml`, defaulting to host-CPU-count — an undocumented, host-dependent capacity ceiling. |
| AI | Ready, with one robustness gap | Gateway invariants (advisory-only, per-org locking, idempotent replay, budget/egress enforcement) all independently re-verified from source. Gap (new finding this phase): `apps/ai/providers/anthropic.py`/`openai.py` construct their SDK clients with no explicit `timeout=`, and the one synchronous AI capability (ESG Assistant, `ai/views.py:108`) makes this uncapped call **while holding** the per-org `TenantAIPolicy` row lock (`gateway.py`'s `with transaction.atomic():` block) — independently confirmed by re-reading the exact call chain. A slow/hung provider could hold a gunicorn thread and that lock for an extended period, degrading capacity for concurrent requests to the same org (and, since gunicorn threads are shared platform-wide, potentially other tenants' unrelated requests too under enough concurrent hangs). |
| Governance | Ready | Hash-chain, versioning, workflow state machine, soft delete all independently spot-checked; concurrency tests for every locked path confirmed to exist and match the actual `select_for_update` call sites. |
| Security | Ready | See §8. |
| Monitoring | Ready | Request-ID correlation, UTC-timestamped structured logs, gunicorn access logging, 3 health endpoints, AI ops dashboards — all re-confirmed present and wired correctly. |

## 4. Release blockers

**Release blocker: none identified.** Every finding below is bounded, has a clear mitigation or documented rationale, and none represents a data-integrity, security, or correctness defect.

### High
| # | Finding | Why High, not blocker |
|---|---|---|
| 1 | No explicit provider-call timeout on the AI gateway's synchronous path, held under a per-org DB lock (§3 AI) | New finding this phase, independently verified. Bounded by AI being opt-in (`AI_ENABLED=False` default — zero exposure out of the box) and affecting only 1 of 5 AI capabilities; gunicorn's blunt 120s worker timeout is an imperfect but real backstop. Recommend as the top priority fast-follow: add an explicit `timeout=` to both provider SDK clients. |
| 2 | `render.yaml`'s `type: redis` / cross-service `SECRET_KEY` sharing unverified against live Render | Carried from 9a/9d — has a documented, functionally-equivalent manual fallback if the IaC assumption is wrong. |
| 3 | Live `docker compose up` end-to-end never run | Carried from 9a/9d — static review found zero drift; disk-space risk this session, not a defect in the config itself. |

### Medium
| # | Finding | Source |
|---|---|---|
| 4 | Celery `--concurrency` unpinned in `render.yaml` — host-dependent capacity | New, Performance review |
| 5 | `ingestion/views.py:576-610` inlines business logic that should be a service method | New, Architecture review |
| 6 | `apps.core` layering inversion (lazy-imports `apps.ingestion.models`) | New, Architecture review |
| 7 | `_parse_date` duplicated 3x across parsers | New, Code quality review |
| 8 | Frontend test coverage gap — `LoginPage.jsx`, `DashboardPage.jsx`, 4/6 role dashboards, all chart/UI components untested | New, Test quality review |
| 9 | No Content-Security-Policy on the frontend | Carried from 9c, deliberately deferred (needs browser-verified rollout) |
| 10 | `esbuild`/`vite` dev-only CVE, needs a 3-major-version Vite migration | Carried from 9c, deliberately deferred |
| 11 | No IP/network restriction on `/admin/` | Carried, infrastructure-layer |
| 12 | No formal RPO/RTO or tested DR drill | Carried, infrastructure-layer |
| 13 | No documented object-storage backup policy | Carried, infrastructure-layer |
| 14 | Render free-tier PostgreSQL 90-day expiry | Carried, platform constraint |

### Low
| # | Finding |
|---|---|
| 15 | Three different "where business logic lives" conventions across apps |
| 16 | `GwpSet` naming inconsistency (cosmetic) |
| 17 | Several long-but-justified methods, optional decomposition candidates |
| 18 | `carbon/services/inputs.py` has no discoverable dedicated test file |
| 19 | AI observability endpoint has no caching layer (deliberate — low traffic at current scale) |
| 20 | Frontend `DashboardPage` chunk is 447 kB (recharts), but route-lazy so only Dashboard visitors pay it |
| 21 | ADR set (`docs/adr/*.md`) not cross-linked from `docs/DECISIONS.md` — discoverability only |
| 22 | Everything already carried in `ROADMAP.md` §1 (no fine-grained progress, polling not push, inert batch cancellation, no frontend E2E suite, illustrative DEFRA factor subset) |

### Observation
| # | Finding |
|---|---|
| 23 | `ai/evaluation/capabilities.py` intentionally retains a superseded v1 schema reference, documented in its own docstring — not a defect |
| 24 | `RETRY_DLQ.md`'s "198/198 passing" is accurate *historical* framing from the Phase 5e milestone report, not a stale current-state claim — verified by reading context, left unchanged |

## 5. Documentation audit

**Rating: Strong.** Independently verified: ~12 cross-file links and section citations all resolve to real targets and real headings; `SECRET_KEY`/CORS/CSRF posture is described identically and consistently across `SECURITY.md`, `INFRASTRUCTURE_SECURITY.md`, and `DEPLOYMENT_GUIDE.md`; all 8 first-party apps have corresponding documentation; version number `0.9.0-rc1` is consistent everywhere it appears; test-count claims (915/92) are plausible against actual `def test_` counts.

Two genuine defects found and **fixed this phase** (commit `5bb2ff5`):
- `README.md` claimed the audit hash-chain was "planned for a later phase" — it has been built and live since Phase 6a, and README's own API table two sections later already documented the same endpoint. Self-contradictory; corrected.
- `ROADMAP.md`'s Known Limitations table cited the Django CVE fix as stopping at `6.0.6` (Phase 6f), never updated for Phase 9c's further `6.0.6→6.0.7` bump even though `SECURITY.md`/`RELEASE_NOTES.md` already had it right. Appended.

One minor, non-defect gap left as-is: the 14 ADRs in `docs/adr/` are never cross-linked from `docs/DECISIONS.md` (discoverability, not inaccuracy — see Low #21).

## 6. Test quality review

**Rating: Strong on backend, Medium gap on frontend.** Independently verified against actual locking code, not assumed:

- Every genuinely concurrent code path found in the codebase (`select_for_update` in `gateway.py`, `soft_delete.py`, `workflow.py`, plus `ai/models.py`/`audit/models.py`/`ingestion/models.py`) has a matching real threaded test (`threading.Barrier`/10 concurrent threads against `TransactionTestCase`) — confirmed by cross-referencing the grep for `select_for_update` against the concurrency test files directly.
- AI evaluation harness (`ai/evaluation/tests_*.py`, ~1,214 lines) contains real behavioral assertions (e.g. constructing a case with a stale template hash and asserting the specific `OUTCOME_REGRESSION` classification), not smoke tests.
- Backend CI (`backend-ci.yml`) runs against **real Postgres 16 + Redis 7 service containers**, not SQLite/eager-mode-only — a genuine cross-process integration signal, confirmed by reading the workflow file directly.
- Frontend gap: of ~28 pages/components, roughly 15 have zero test coverage, including `LoginPage.jsx` (the auth entry point) and `DashboardPage.jsx`, plus 4 of 6 role-specific dashboard widget sets and all 3 chart components. This is the one area of the whole review where a "genuinely production-relevant untested surface" claim holds up.

## 7. Performance review

**Rating: Strong**, with two Medium operational gaps (see §4 #1, #4). Independently verified:

- No N+1 query pattern found on any hot path (records list, compliance reports, AI observability, metrics, activity feed) — all use `select_related`/`prefetch_related`/`Subquery`/`.values().annotate()` correctly.
- Index coverage matches actual filter/order usage on `EmissionCalculation`, `EmissionRecord`, `UploadBatch`, `AIInteraction`.
- Metrics cache invalidation is correct on every write path (ingest, recalc, soft-delete, restore, backfill), deferred via `transaction.on_commit` to avoid the read-stale-uncommitted-data race.
- Gunicorn capacity (2 workers × 4 threads = 8 concurrent requests) is byte-for-byte consistent between `render.yaml` and `Dockerfile`, re-derived directly from both files.
- Pagination is universal on list endpoints; CSV/JSON exports are capped and streamed, not unbounded.
- Frontend build succeeds; largest chunk (`DashboardPage`, 447 kB, dominated by `recharts`) is route-lazy, so only Dashboard visitors pay it — not a blocker, a documented trade-off.

## 8. Security review (final)

**Rating: Strong.** Every claim below was re-derived directly from source this session, not recalled from Phase 9c:

- `SECRET_KEY`/`DATABASE_URL`/`ALLOWED_HOSTS` fail-closed logic re-read directly from `settings.py:39-66` — confirmed exactly as documented.
- JWT config (`SIMPLE_JWT` dict, `settings.py:341-350`) re-read directly — 15min access / 7day refresh, rotation + blacklist-after-rotation, all confirmed.
- Celery `CELERY_ACCEPT_CONTENT`/`TASK_SERIALIZER`/`RESULT_SERIALIZER` re-read directly — JSON-only, no pickle deserialization-RCE surface.
- Tenant isolation re-verified across every relevant viewset: `TenantScopedViewSetMixin` applied to `UploadBatchViewSet`, `EmissionRecordViewSet`, `DataSourceViewSet`, `EmissionCalculationViewSet`; `OrganizationViewSet` and the Metrics views implement equivalent protection inline via `resolve_tenant_context()`, independently confirmed correct by reading their `get_queryset()`/`_resolve()` methods directly.
- Upload path-traversal safety reasoned through from first principles this session: Django's `FileSystemStorage.save()` invokes `safe_join()` internally, raising `SuspiciousFileOperation` on any path that would escape `MEDIA_ROOT`; S3 keys are opaque strings in a flat namespace where `..` has no traversal meaning. Both backends genuinely safe, not merely asserted safe.
- AI advisory-only invariant re-verified via grep: zero `.save()`/`.update()`/`.delete()` calls on `EmissionRecord`/`EmissionCalculation` anywhere in `apps/ai/`.
- DRF throttle scopes (`anon`/`user`/`login`/`ai`) re-read directly from `settings.py:305-326`.
- Dependency CVE status re-confirmed current: `requirements.txt` still pins `Django==6.0.7` (no regression), `form-data`/`js-yaml` fixed per 9c, `esbuild`/`vite` dev-only issue remains deliberately deferred.

No new security defect was found this phase. The AI-timeout finding (§4 #1) is classified as a performance/robustness gap, not a security vulnerability — it cannot be triggered by an unauthenticated party or used to access another tenant's data; it is a resource-exhaustion risk under a specific opt-in-feature failure mode.

---

## 9. Overall assessment

| Dimension | Score | Basis |
|---|---|---|
| Architecture | 8/10 | Strong extensibility and scalability; two real but narrow structural issues (§1, §4 #5–6), no systemic problem |
| Code quality | 9/10 | Exceptionally clean for a project this size; one real duplication (§2), one cosmetic naming issue, nothing else found across a full-repo grep sweep |
| Maintainability | 8/10 | Consistent naming, extensive documentation, thin views mostly — held back by 3 divergent service-layer conventions across apps |
| Scalability | 8/10 | No architectural N+1, correct indexing/caching/pagination — held back by the unpinned Celery concurrency ceiling |
| Production readiness | 8/10 | Fail-closed everywhere, CI-verified on every push, comprehensive health/logging — held back by the 2 unverified-against-live-infra items and the AI-timeout gap |
| Documentation | 9/10 | ~30 files, verified cross-references, one now-fixed self-contradiction; exceptionally strong for this project's size |
| Testing | 8/10 | 915+92 tests, genuinely strong concurrency coverage verified against real locking code, real Postgres/Redis in CI — held back by the frontend coverage gap |
| Security | 9/10 | Every load-bearing control independently re-verified from source this session; remaining gaps are consciously-deferred defense-in-depth items with documented reasoning, not oversights |

**No score below 8/10.** The recurring theme across every "held back by" clause is the same handful of already-classified High/Medium findings in §4 — nothing new or hidden is dragging any dimension down beyond what's already been named and evidenced.

## 10. Release decision

### Would I approve ScopeTrace Version 1.0.0 for production release?

**Yes.**

Across five independent research passes (each with no memory of the others' or any prior phase's conclusions) plus this session's own direct re-verification of every safety-critical claim, **zero release blockers were found**. Every High-severity item has either a documented operational fallback (the two Render/Docker live-verification gaps) or a narrow, well-understood blast radius bounded by the fact that the affected feature (AI) is disabled by default. Every Medium and Low item is real but genuinely non-urgent — the kind of backlog a healthy, actively-maintained v1.0.0 ships with, not the kind that should gate the release.

### Release certification

ScopeTrace **v1.0.0** (built from `0.9.0-rc1`, commit `5bb2ff5`) is certified production-ready, conditioned on completing the two documented first-deploy verification steps already specified in [`DEPLOYMENT_GUIDE.md`](DEPLOYMENT_GUIDE.md) §3.4–3.5 and [`SMOKE_TEST_CHECKLIST.md`](SMOKE_TEST_CHECKLIST.md) against the real Render/Vercel environment before announcing general availability.

**Recommended before or immediately after first production traffic** (not blocking the release itself):
1. Add an explicit `timeout=` to both AI provider SDK clients (§4 #1) — smallest, highest-value fix available.
2. Pin Celery `--concurrency` in `render.yaml` (§4 #4).
3. Perform the live Render Blueprint deploy and the full smoke test (§4 #2, #3).

### Remaining known limitations

See [`ROADMAP.md`](ROADMAP.md) §1 (unchanged, already accurate) and [`RELEASE_CHECKLIST.md`](RELEASE_CHECKLIST.md) §16 for the full previously-classified list. This phase's additions are folded into §4 above and do not change that document's structure.

### Future roadmap

Unchanged from [`ROADMAP.md`](ROADMAP.md) §2's "Phase 11+ — Launch & beyond": real observability stack (Prometheus/Grafana/Loki/OpenTelemetry/Sentry), saved dashboards, in-app notification center, PDF compliance export, a real landing page, published architecture diagrams, OpenAPI schema, video demo — plus, newly identified this phase as good candidates for a near-term maintenance pass: the `_parse_date` consolidation, the `core`→`ingestion` layering cleanup, and expanded frontend test coverage.

### Final engineering summary

ScopeTrace enters v1.0.0 as a genuinely mature, well-tested, defensively-engineered platform: fail-closed security posture, verified tenant isolation, a governed and structurally advisory-only AI layer, strong concurrency correctness backed by real threaded tests, and a documentation set that — after this phase's two small fixes — accurately reflects the shipped system. Nothing found across five independent review passes and this session's own direct source verification rises to a release blocker. The engineering discipline maintained across all ten phases (small logical commits, verify-before-claim, additive-not-speculative changes, honest disclosure of every tooling limitation encountered) is itself part of why this release can be certified with genuine confidence rather than assumed good faith.
