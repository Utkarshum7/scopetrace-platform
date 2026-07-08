# AI Evaluation Infrastructure (`AI_EVALUATION.md`)

Phase 7a.5 — the evaluation and regression framework every later AI
capability (7b–7g) uses. **No user-facing AI feature is implemented in this
milestone.** Five capability names (anomaly detection, factor
recommendation, validation assistance, ESG assistant, report narration)
have PLANNED prompt templates and schemas here — eval-harness fixtures
only, with no business logic, no `EmissionRecord`/`EmissionCalculation`
reference, no API endpoint, and no Celery task behind any of them. See
[`AI_ARCHITECTURE.md`](AI_ARCHITECTURE.md) for the foundation this builds
on (`apps.ai` — providers, the gateway, prompt registry, policy/cost/egress).

---

## 1. Why this milestone exists

The Phase 6 architecture review found a real frontend/backend contract
drift that shipped undetected (the approval modal called an endpoint the
backend no longer accepted). Phase 7's own design explicitly calls for the
equivalent safeguard for AI: a way to detect when a prompt, a schema, or a
capability's expected output silently drifts from what was tested —
*before* a feature milestone ships it. `apps.ai.evaluation` is that
safeguard, built before any real capability (7b+) exists to need it.

---

## 2. Package layout

```
apps/ai/evaluation/                  (its own nested Django app -- own models/migrations)
  models.py            EvaluationRun, EvaluationResult (platform-level, no organization FK)
  capabilities.py       CAPABILITY_REGISTRY -- capability name -> prompt/schema/fixture identity
  scoring.py             deterministic (Tier 1) scoring functions
  runner.py               EvaluationRunner -- pure, side-effect-free case execution
  service.py               EvaluationService -- persists a run + its results
  judge.py                  LLM-as-Judge framework (Tier 2, disabled by default)
  fixtures/
    loader.py                loads golden-dataset JSON into EvaluationCase objects
    golden/
      anomaly_detection/v1/cases.json    (superseded, kept unreferenced)
      anomaly_detection/v2/cases.json    (Phase 7b -- the real capability)
      factor_recommendation/v1/cases.json    (superseded, kept unreferenced)
      factor_recommendation/v2/cases.json    (Phase 7c -- the real capability)
      validation_assistance/v1/cases.json
      esg_assistant/v1/cases.json
      report_narration/v1/cases.json
      foundation_selftest/v1/cases.json
  tests_*.py             (9 test modules, ~86 tests)

apps/ai/providers/replay.py           ReplayProvider -- deterministic, offline, zero-cost
apps/ai/providers/replay_fixtures/    standalone example fixtures for ReplayProvider's file-lookup mode
apps/ai/prompts/templates/            +5 planned-capability templates, +2 judge templates
apps/ai/schemas.py                    +5 planned-capability schemas, +2 judge schemas
```

---

## 3. Golden datasets

One JSON file per `(capability, version)`:
`apps/ai/evaluation/fixtures/golden/<dataset>/<version>/cases.json`. Each
case:

```json
{
  "case_id": "anomaly_detection_v1_001",
  "description": "human-readable context",
  "prompt_name": "anomaly_detection",
  "response_schema_id": "anomaly_detection",
  "response_schema_version": 1,
  "template_vars": { "...": "..." },
  "expected_response": { "...": "..." },
  "expected_prompt_template_hash": "<sha256, snapshot at authoring time>",
  "expected_rendered_input_hash": "<sha256, snapshot at authoring time>",
  "min_score": 1.0
}
```

**Versioned by directory, not by a field inside the file** — a new
`v2/cases.json` is how a golden dataset changes; `v1`'s fixtures never get
edited in place, so a regression run that still references `v1` never has
its ground truth silently change underneath it (the same reasoning
`AIPromptVersion` itself uses for prompt templates).

`expected_prompt_template_hash`/`expected_rendered_input_hash` are captured
by actually calling `render_prompt()` once, at fixture-authoring time —
they are the **prompt snapshot** and **rendered prompt snapshot** the
milestone's scope asked for. `tests_fixtures.py`'s
`GoldenFixtureHashSelfConsistencyTests` proves every fixture's recorded
hash still matches a fresh render, for every real fixture, every test run.

12 cases are actively loaded across 6 capabilities today (16 ship on disk,
counting superseded-but-kept `v1` files no capability config references
anymore): 3 in `anomaly_detection/v2` (Phase 7b's real capability,
replacing the 2-case `v1` placeholder) and 3 in `factor_recommendation/v2`
(Phase 7c's real capability, replacing its own 2-case `v1` placeholder), 2
each for `esg_assistant`/`validation_assistance`, 1 for `report_narration`,
1 for the existing `foundation.selftest`. `anomaly_detection`'s and
`factor_recommendation`'s v1 → v2 jumps are this versioning discipline's
real exercise, not just documentation: see ADR 0009 for why
`anomaly_detection` v2 dropped its `is_anomalous` field entirely (AI must
never classify, only explain), and ADR 0010 for why
`factor_recommendation` v2 asks the AI to pick a candidate LABEL rather
than reproduce a raw `EmissionFactor` identifier.

---

## 4. Prompt regression detection

`EvaluationRunner.run_case()`, for each case:

1. **Render** the prompt via the exact same `apps.ai.prompts.registry.render_prompt()`
   the real gateway uses (not a second implementation).
2. **Compare** the freshly-computed `template_hash`/`rendered_input_hash`
   against the fixture's recorded snapshot. A mismatch means the prompt
   template *or* its rendering changed since the fixture was authored,
   without the fixture being updated to match — outcome `REGRESSION`. This
   is the automatic prompt-regression detection the milestone's scope
   requires: edit `apps/ai/prompts/templates/anomaly_detection.txt`
   without updating its golden fixture, and the next evaluation run fails
   with a specific, actionable detail message.
3. **Validate** the fixture's own `expected_response` against the *live*
   schema (`apps.ai.schemas.get_schema()`) — if the schema shape changed
   without the fixture being updated, outcome `SCHEMA_INVALID`.
4. **Replay** via `ReplayProvider`, which echoes the case's
   `expected_response` back verbatim (deterministic, zero cost, fully
   offline) — proving the schema-validation + scoring pipeline runs end to
   end, not asserting anything about a real model's output quality (that's
   Tier 2's job, once a real capability and real provider exist).
5. **Score** actual vs. expected (`apps.ai.evaluation.scoring`, default
   `score_exact_match`). Below `case.min_score`: `REGRESSION`.

Any unclassified exception during steps 1–5 is caught and recorded as
`EVALUATION_FAILURE` — one bad case never aborts a batch of otherwise-good
ones (`tests_runner.py`'s
`test_a_batch_with_one_broken_case_still_completes_the_rest`).

### Failure classification

| Outcome | Meaning |
|---|---|
| `OK` | Rendered correctly, schema-valid, score ≥ threshold. |
| `SCHEMA_INVALID` | The fixture's or the replayed response's shape no longer matches the live schema. |
| `REGRESSION` | Prompt/rendering hash drifted from the golden snapshot, *or* the score fell below the required minimum. |
| `PROVIDER_ERROR` | The provider couldn't be constructed or the call itself failed. |
| `EVALUATION_FAILURE` | Any other unclassified harness/scoring error. |

---

## 5. Replay providers

`apps.ai.providers.replay.ReplayProvider` extends the `EchoProvider`
pattern (Phase 7a) with two deterministic, offline, zero-cost lookup modes:

1. `request.extra["canned_response"]` (dict) — returned verbatim. What
   `EvaluationRunner` uses: it already has the golden fixture's
   `expected_response` in memory.
2. `request.extra["case_id"]` (str) — loads
   `apps/ai/providers/replay_fixtures/<case_id>.json` from disk. Supports
   standalone/CLI replay (e.g. a `NO_EGRESS` tenant selecting
   `AI_PROVIDER=replay` for deterministic canned answers) without a caller
   pre-loading fixtures itself.

Selectable via the standard factory (`get_llm_provider(provider_name="replay")`)
exactly like `echo`/`anthropic`/`openai`, and included in
`ZERO_EGRESS_PROVIDERS`.

---

## 6. LLM-as-Judge framework (Tier 2, disabled by default)

`apps.ai.evaluation.judge` — real, tested code (the same "real class,
inert until turned on" precedent as the carbon pipeline's
`AIRecommendationStage`), gated by `settings.AI_JUDGE_ENABLED` (default
`False`). Calling `run_judge_scoring()`/`run_pairwise_comparison()` while
disabled raises `JudgeDisabledError` immediately, before rendering a
prompt or touching a provider.

- `JudgeRubric` — a name, a list of criteria, a scale description.
- `run_judge_scoring(rubric, candidate_response) -> JudgeScoringResult`
  (`score` in [0.0, 1.0], `rationale`).
- `run_pairwise_comparison(rubric, response_a, response_b) -> PairwiseComparisonResult`
  (`winner` in `{A, B, TIE}`, `rationale`).

Even when explicitly enabled, both go through the same
`render_prompt()`/`get_llm_provider()`/`validate_response()` building
blocks as everything else in `apps.ai`, defaulting to `echo` in
`DEBUG`/`_TESTING` — this module's own test suite never makes a real
vendor call. **No production usage yet**: no Tier 1 (blocking) test calls
this module, and no feature milestone wires it into a real workflow.

---

## 7. Two CI tiers

| Tier | What | Job | Blocking? |
|---|---|---|---|
| 1 — Deterministic | Schema validation, provider contract, replay provider, invariant tests (I1–I6), deterministic regressions | `backend-ci.yml`'s existing `test` job (`--exclude-tag=ai_advisory`) | **Yes** |
| 2 — Advisory | LLM-judge framework, qualitative scoring | New `ai-evaluation-advisory` job (`--tag=ai_advisory`, `continue-on-error: true`) | No |

Tier 1 needed **no new CI mechanism** — every Tier 1 check (schema
validation, provider contract, replay provider, the formal I1–I6
invariant suite in `tests_invariants.py`, prompt-regression detection) is
an ordinary Django `TestCase`, already covered by the existing blocking
`test` job. Only Tier 2 (`tests_judge.py`, tagged `@tag("ai_advisory")`)
needed carving out, via Django's `--tag`/`--exclude-tag` mechanism — the
`test` job excludes it, a new advisory job runs only it, mirroring the
`security`/`pip-audit` job's existing advisory precedent (see
[`CI_CD.md`](CI_CD.md)).

Both tiers run entirely offline today (`AI_PROVIDER` defaults to `echo` in
CI's `DEBUG=True` environment) — zero cost, zero network, in either tier.
Tier 2 stays advisory not because it's non-deterministic *today*, but
because a future capability milestone's quality scoring against a real
provider will not be, and this is where that lands.

---

## 8. Invariant merge gate

`apps/ai/evaluation/tests_invariants.py` is the single, discoverable place
naming what proves each of Phase 7's six invariants (I1–I6, see
[`AI_ARCHITECTURE.md`](AI_ARCHITECTURE.md) §1) holds — for checks that
already existed (mostly in `tests_gateway.py`/`tests_import_guard.py`),
this module names and cross-references them; for gaps this milestone
introduced (does the *new* `apps.ai.evaluation` package itself also uphold
I2/I3? is Tier 2 disabled by default per I6?), it adds the missing check.
Every future AI milestone (7b+) should keep this whole file green, not
just its own new tests — that is what makes it a merge gate rather than
just another test file.

---

## 9. Running an evaluation

```python
from apps.ai.evaluation.service import run_tier1_evaluation

run = run_tier1_evaluation(trigger="manual")          # every capability
run = run_tier1_evaluation(trigger="manual", capability="anomaly_detection")  # one capability
```

Both are also exercised directly by CI (`manage.py test`, not a separate
CLI command — Django's test runner already is the harness's entry point).

---

## 10. Adding a real capability (the steps Phase 7b actually followed)

1. Reuse the existing `CAPABILITY_REGISTRY` entry (or add a new one) —
   the prompt template and schema may be kept as-is, version-bumped, or
   replaced; a Phase 7a.5 placeholder contract is a sketch, not a
   commitment. `anomaly_detection` bumped schema v1 → v2 and rewrote its
   template (see AI_ARCHITECTURE.md §12, ADR 0009).
2. Add `fixtures/golden/<dataset>/v2/cases.json` if the schema or prompt
   shape changes (new version directory, never edit `v1` in place) — 3
   real-scenario cases for `anomaly_detection/v2`.
3. Wire the real feature through `apps.ai.services.gateway.invoke_ai()`
   (never bypass it) for actual tenant-facing calls — a new
   capability-specific service module
   (`apps.ai.services.anomaly_detection`) builds prompt context from
   governed data (read-only) and persists the result as an immutable
   `AIAnnotation`, never through the gateway's own generic response
   handling alone.
4. Keep `tests_invariants.py` green — it is the merge gate.
   `InvariantI2AnomalyDetectionConcreteProofTests` is the first
   capability-specific addition to it: a behavioral (not just structural)
   proof that the real capability's service function never mutates the
   governed record it reads.

## 11. Related documents

- [`AI_ARCHITECTURE.md`](AI_ARCHITECTURE.md) — the Phase 7a/7b foundation.
- [`CI_CD.md`](CI_CD.md) — overall CI philosophy (blocking vs. advisory).
- [`docs/adr/0008-ai-evaluation-tiering.md`](adr/0008-ai-evaluation-tiering.md)
- [`docs/adr/0009-anomaly-explanation-async-dispatch-and-immutable-annotations.md`](adr/0009-anomaly-explanation-async-dispatch-and-immutable-annotations.md)
