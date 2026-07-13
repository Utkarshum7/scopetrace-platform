# ADR 0008: Two-tier AI evaluation -- deterministic checks blocking from day one, LLM-judge/qualitative checks advisory

- Status: Accepted
- Date: 2026-07-08
- Phase: 7a.5 (AI Evaluation Infrastructure)

## Context

The finalized Phase 7 architecture (approved before this milestone began)
called for re-evaluating "advisory vs. blocking AI evaluation in CI" and
concluded a blanket advisory posture under-gates the checks that are
actually safety-critical. Phase 7a.5 has to translate that into a real CI
configuration: which evaluation checks fail the build, and which merely
report.

## Alternatives considered

**A. Two tiers -- deterministic checks blocking, LLM-judge/qualitative
checks advisory** (chosen). Schema validation, provider contract tests,
`ReplayProvider` behavior, the formal I1–I6 invariant suite, and
prompt-regression detection (golden-fixture hash comparison) all run as
ordinary Django tests in the existing blocking `test` job. Only
`apps.ai.evaluation.judge`'s tests (tagged `@tag("ai_advisory")`) are
excluded from that job and run instead in a new `ai-evaluation-advisory`
job with `continue-on-error: true`.

**B. Everything advisory, matching `security`/`pip-audit`'s existing
precedent uniformly.** Simpler (one job, one policy), but this is exactly
the "blanket advisory under-gates safety-critical checks" problem the
finalized Phase 7 design already flagged: a schema-validation regression
or a broken invariant (e.g. a future capability accidentally gaining a
write path to `EmissionRecord`) is a correctness bug, not a "needs its own
testing burden before we can act on it" finding like a `pip-audit` CVE.

**C. Everything blocking, including the LLM-judge framework's tests.**
The judge framework's tests in THIS milestone are fully deterministic
(they run through `EchoProvider`, never a real vendor call), so nothing
stops them from being blocking today. But the framework exists
specifically so a *future* capability milestone can score real-provider
output against a rubric — and that scoring is inherently non-deterministic
(the same prompt, the same rubric, a different day's model sampling, can
produce a different judge score). Making the *mechanism* blocking now
would mean either relaxing that gate later (a real design change happening
quietly) or blocking merges on non-deterministic output once a real
capability lands. Better to set the correct long-term policy now, even
though it costs nothing to enforce yet.

## Decision

**Option A.**

1. **No new mechanism for Tier 1.** Schema validation, provider contract,
   replay provider behavior, and the invariant suite were already going to
   run as part of `manage.py test` (the existing blocking job) the moment
   they were written as ordinary `TestCase` classes -- there was nothing
   to "wire in." The only actual CI change needed was excluding Tier 2.
2. **Django's `--tag`/`--exclude-tag`, not a separate test command or
   directory convention.** `tests_judge.py`'s classes are tagged
   `@tag("ai_advisory")`; the existing `test` job's command gained
   `--exclude-tag=ai_advisory`; the new `ai-evaluation-advisory` job runs
   `--tag=ai_advisory` with `continue-on-error: true`. Minimal, surgical
   diff to an existing, working CI file rather than restructuring it.
3. **The advisory job runs at the same Postgres+Redis fidelity as the
   blocking job**, not a lighter-weight sqlite shortcut -- a future
   capability milestone will extend this same job with real
   golden-dataset-driven quality scoring, which should run at production-
   equivalent fidelity from day one, not need a fidelity upgrade later.
4. **The tier is a property of the *check's nature* (non-deterministic
   once a real provider is involved), not of whether it happens to be
   deterministic today.** Every judge test in this milestone runs through
   `EchoProvider` and is fully reproducible right now -- it's still
   classified Tier 2, because the framework's entire purpose is to be
   pointed at a real provider eventually.

## Consequences

- A future capability milestone (7b+) that adds real golden-dataset-driven
  LLM-judge scoring against a real provider lands directly in the existing
  `ai-evaluation-advisory` job -- no new CI job, no new policy decision,
  just more test cases under the same tag.
- Per the finalized Phase 7 design's own language: promoting the quality
  tier from advisory to blocking, once a real baseline exists to measure
  drift against, is a follow-up decision for whichever milestone first has
  real-provider judge data to set that baseline from -- not this one.
- If a genuinely deterministic Tier 1 check is ever added that doesn't fit
  neatly into "ordinary Django test in the existing job" (e.g. a
  standalone CLI tool), it should still prefer integrating into the
  existing blocking `test` job's command over inventing a third CI
  mechanism, unless a real reason emerges to do otherwise.
