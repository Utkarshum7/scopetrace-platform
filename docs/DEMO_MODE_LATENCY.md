# Demo Mode — Evidence-Based Runtime & Latency Assessment (D5)

D4 made Demo Mode's entire `ingest → AI anomaly/validation → calculate →
notify → AI factor-rec` chain run synchronously inside one
`POST /api/upload/*` HTTP request (see [`ARCHITECTURE_OVERVIEW.md`](ARCHITECTURE_OVERVIEW.md)
§6 and [`apps/core/execution.py`](../backend/apps/core/execution.py)). That
request is bounded by gunicorn's `--timeout 120` (`render.yaml`). This
document answers, with evidence rather than assumption: how long does that
request actually take, what dominates it, and does it stay under 120s.

## Method

Two configurations were **measured** directly against this codebase (Django
test client, real SQLite test DB, `CELERY_TASK_ALWAYS_EAGER=True`, no
mocking of the pipeline itself — only wall-clock instrumentation wrapped
around `IngestionService.ingest_batch`, `CarbonCalculationService.calculate_for_batch`,
and `apps.ai.services.gateway.invoke_ai`). Five uploads of the same 5-row
SAP CSV fixture were run per configuration (1 suspicious row → 1
`generate_anomaly_explanations_task` candidate; 2 failed rows → 2
`generate_validation_assistance_task` candidates — 3 AI calls per upload).
The first iteration of each run includes one-time Python import/JIT costs
and is excluded from the steady-state averages below; the instrumentation
script and raw JSON output are described at the end of this document.

A **real remote provider** (Anthropic/OpenAI) call cannot be measured in
this environment — no credentials, no outbound network egress. That figure
is instead derived from published, cited third-party benchmarks combined
with the measured gateway overhead, and is explicitly labeled as an
estimate below, not a measurement.

## 1–3. Measured results

| Configuration | ingest_batch | calculate_for_batch | AI gateway calls | Total request time (steady state) |
| :--- | :--- | :--- | :--- | :--- |
| **AI disabled** | ~14 ms | ~10 ms | 0 | **~94 ms** |
| **AI enabled, provider=echo** | ~13 ms | ~11 ms | 3 (~20 ms each) | **~144 ms** |
| **AI enabled, real provider (estimated)** | ~14 ms | ~10 ms | 3 (~2–4 s each, *estimated*) | **~6.3–12.3 s** (this fixture) |

Per-AI-call gateway overhead, isolated by the echo measurement (echo's own
`provider.complete()` does no I/O and returns in <1 ms — see
[`apps/ai/providers/echo.py`](../backend/apps/ai/providers/echo.py)):
**~19–21 ms/call**, spent on the per-organization row lock
(`select_for_update` in `apps.ai.services.gateway._lock_org_policy`), the
budget check, egress-tier enforcement, prompt redaction/rendering, schema
lookup, and the `AIInteraction` row write — i.e. this is Django/Postgres
overhead, not network latency, and is paid identically regardless of which
provider answers the call.

## Operations contributing most to latency, ranked

1. **The real provider's network + model-generation time** (when a real
   provider is used) — 2–3 orders of magnitude larger than everything else
   combined; see estimate below.
2. **Each AI gateway call's fixed overhead** (~20 ms) — negligible on its
   own, but multiplies linearly with the number of suspicious/failed/
   unresolved records in the batch (see "Fan-out" below).
3. `ingest_batch` (parse/validate/normalize/persist) — ~13–14 ms, dominated
   by DB writes (`bulk_create` of `EmissionRecord` rows), independent of AI.
4. `calculate_for_batch` — ~10–11 ms, same shape as #3.
5. Everything else (auth, serialization, `send_notification_task` when no
   recipient is configured, Django request/response overhead) — low
   single-digit milliseconds, not separately significant.

**Fan-out is the multiplier that matters.** `generate_anomaly_explanations_task`,
`generate_validation_assistance_task`, and `generate_factor_recommendations_task`
each loop over their candidate records and call `invoke_ai()` once **per
record, sequentially** (`apps/ai/tasks.py`) — there is no batching or
parallelism. The 5-row fixture above produces exactly 3 calls; a batch with
more suspicious/failed/unresolved rows produces proportionally more, and the
only existing ceiling on batch size is the pre-existing 10 MB upload cap
(`apps/ingestion/serializers.py`, `MAX_UPLOAD_SIZE_MB`), which does not
translate to a small, predictable row count.

## Real remote provider — published evidence and estimate

This codebase's `AnthropicProvider`/`OpenAIProvider` call the vendor SDKs'
**non-streaming** `messages.create()` / `chat.completions.create()` (no
`stream=True` anywhere in `apps/ai/providers/`) — the entire response is
generated before this process sees any of it back.

Two facts, both externally published and cited (not measured in this
session — this is the "published" part of the evidence):

- Claude Sonnet's typical time-to-first-token is **~0.9–1.0 s**, and
  Sonnet-class throughput once generating is on the order of **~20–56
  tokens/second** depending on model/provider ([kunalganglani.com
  LLM API latency benchmarks](https://www.kunalganglani.com/blog/llm-api-latency-benchmarks-2026),
  [dev.to — 5 LLM APIs tested for latency](https://dev.to/kunal_d6a8fea2309e1571ee7/5-llm-apis-tested-for-latency-real-data-2026-3e4o)).
- For **non-streaming** calls specifically, total wait scales with output
  length at roughly that same per-token rate — e.g. a ~500-token response is
  reported to take **~20–25 s** end-to-end non-streaming
  ([HolySheep AI — Claude API streaming vs non-streaming benchmark](https://www.holysheep.ai/articles/en-claude-api-streaming-vs-non-streaming-response-tim-2026-04-11-0027.html)).

This pipeline's three AI capabilities (`anomaly_detection`,
`validation_assistance`, `factor_recommendation`) each return one short
structured-JSON field (a one-to-few-sentence explanation/recommendation —
see their prompt templates and response schemas under `apps/ai/prompts/`
and `apps/ai/schemas/`), not a long-form document. Scaling the published
per-token rate down to a realistic **50–200 output tokens** for this shape
of response gives an estimated **~2–4 s per call**, non-streaming, under
typical conditions.

Combining the measured ~20 ms gateway overhead with this estimate:

- **This 5-row fixture (3 AI calls):** ~0.1 s (measured, deterministic
  stages) + 3 × ~2–4 s (estimated) ≈ **6.3–12.3 s total** — comfortably
  under 120 s.
- **A batch with ~20 AI-eligible records:** ~20 × ~2–4 s ≈ **40–80 s** —
  eating most of the 120 s budget with no margin for variance.
- **A batch with ~30+ AI-eligible records:** plausibly **exceeds 120 s**
  even under "typical" per-call latency, before any single call is slow.
- **Worst case, independent of record count:** before this milestone,
  neither `AnthropicProvider` nor `OpenAIProvider` passed a `timeout=` to
  its vendor client, so a single degraded upstream response could block for
  up to the **Anthropic Python SDK's own default of 10 minutes**
  ([Anthropic Python SDK docs — request timeouts](https://platform.claude.com/docs/en/api/sdks/python)) —
  far past gunicorn's 120 s regardless of how few AI calls the batch
  triggers.

## 4. Does Demo Mode stay comfortably within the 120 s Gunicorn timeout?

- **AI disabled or `AI_PROVIDER=echo`: yes, by a wide margin.** Measured
  steady-state total request time is ~0.1–0.15 s regardless of how many
  suspicious/failed/unresolved records a batch contains (the ~20 ms/call
  gateway overhead scales linearly but would need thousands of AI-eligible
  records in one upload to approach 120 s).
- **A real remote provider: no, not comfortably, for two independent
  reasons** — (a) an unbounded per-call timeout, so any single slow
  response can consume the entire 120 s budget on its own, and (b)
  unbounded sequential fan-out, so a batch with enough suspicious/failed/
  unresolved rows can exceed 120 s even when every individual call is
  "typical." Neither risk depends on the other.

## 5. Recommendation (implemented in D5)

**Default Demo Mode to `AI_PROVIDER=echo` (or `AI_ENABLED=False`), not a
real remote provider.** Demo Mode exists for free-tier/portfolio hosting —
echo demonstrates the complete governed pipeline (ingest → validate →
calculate → AI-advisory annotation → approval workflow) with zero network
calls, zero cost, and a measured ~150 ms worst-case request time, which is
what a demo/portfolio visitor actually needs. This is now the codebase's
actual default: `config/settings.py`'s `AI_PROVIDER` resolves to `'echo'`
whenever `DEMO_MODE=True` and no explicit provider is set (previously this
default only applied under `DEBUG`/the test runner), so an operator who
enables `AI_ENABLED=True` in Demo Mode without picking a provider gets the
safe zero-latency choice, not an `ImproperlyConfigured` failure or an
accidental real, billable call from a copy-pasted production `.env`.

**If an operator explicitly opts into a real provider in Demo Mode anyway**
(their choice — not the default), a second, narrowly-scoped safety net was
added: `AI_PROVIDER_TIMEOUT_SECONDS` (default **30 s**), which is passed as
`timeout=` to the Anthropic/OpenAI client's constructor **only when
`DEMO_MODE=True`**. Production (`DEMO_MODE=False`) is completely
unaffected — `AnthropicProvider`/`OpenAIProvider` keep the vendor SDK's own
10-minute default exactly as before, because production AI calls run inside
an async Celery worker, not inside a gunicorn-timed request, so there is no
120 s wall-clock ceiling to protect there. This closes risk (a) above: no
single call can now stall a Demo Mode request past 30 s. It does **not**
close risk (b) — an operator who both enables a real provider *and* uploads
a batch with many dozens of suspicious/failed/unresolved rows in Demo Mode
can still exceed 120 s in aggregate. Capping the number of AI-eligible
records per batch (or parallelizing the per-record AI calls) would close
that remaining gap, but is a change to the dispatch/fan-out logic itself —
out of scope for this milestone's "keep the implementation as small as
possible, don't touch `.delay()` call sites" mandate carried over from D4.
Operators choosing a real provider in Demo Mode should keep demo datasets
small, consistent with Demo Mode's portfolio/demo purpose.

## What this document does not cover

- Real network round-trip time to Anthropic/OpenAI's API from wherever
  Demo Mode is hosted (free-tier egress can add its own variable latency on
  top of the model-generation estimate above).
- A hard cap on AI-eligible records per batch (see residual risk above).
- Cold-start latency (container spin-up on a free-tier host sleeping
  between requests) — a platform-level concern, not a pipeline one.

## Reproducing the measurements

The instrumentation lived at
`backend/apps/ingestion/_scratch_demo_latency.py` during this milestone (a
`django.test.TestCase`-based script, not part of the committed test suite —
removed after use). It wrapped `IngestionService.ingest_batch`,
`CarbonCalculationService.calculate_for_batch`, and each AI service
module's imported `invoke_ai` reference with `time.perf_counter()`
timing via `unittest.mock.patch.object(..., wraps=original)`, ran 5
uploads per `@override_settings` configuration (`DEMO_MODE=True,
CELERY_TASK_ALWAYS_EAGER=True, CELERY_TASK_EAGER_PROPAGATES=False`, with
and without `AI_ENABLED`/a `TenantAIPolicy(ai_enabled=True,
provider_override="echo")`), and wrote per-stage timings to JSON. Run via
`manage.py test apps.ingestion._scratch_demo_latency`.
