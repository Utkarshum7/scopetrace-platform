# ADR 0010: Factor recommendation targets UNRESOLVED_NO_FACTOR only, candidates are labeled not identified by UUID, and AIFactorRecommendation is a dedicated model

- Status: Accepted
- Date: 2026-07-08
- Phase: 7c (AI Emission Factor Recommendation)

## Context

Phase 7c is the second real Phase 7 capability. Three structural questions
have to be answered before writing any code: (1) which of the
deterministic engine's own "I couldn't resolve this" outcomes should
trigger a recommendation, given `EmissionCalculation.ResolutionStatus` has
two distinct unresolved states; (2) how does the AI refer to a specific
`EmissionFactor` candidate without the reliability risk of asking it to
reproduce a UUID; and (3) whether the persistence layer should reuse
`AIAnnotation` (Phase 7b's model) or be a new, dedicated model.

## Decision 1: only `UNRESOLVED_NO_FACTOR` is in scope, not `UNRESOLVED_NO_ACTIVITY_TYPE`

**Alternatives considered:**

**A. Target only `UNRESOLVED_NO_FACTOR`** (chosen) — the activity type has
already been resolved (`apps.carbon.services.resolution.
ActivityTypeResolver` succeeded), but `FactorIndex.resolve()` found no
factor matching the resolved activity type's region/date/publisher
constraints. This is genuinely a factor-selection problem: real candidate
`EmissionFactor` rows exist, they were just ambiguous or empty after
filtering.

**B. Also target `UNRESOLVED_NO_ACTIVITY_TYPE`** — rejected. This status
means the deterministic engine never even matched an activity type in the
first place (`ActivityTypeResolver.resolve()` returned `None`), so there
is no activity type to query candidate factors FOR. Recommending a factor
here would require the AI to first guess an activity type from a raw
description — a different problem (activity-type mapping) with a
different, currently-out-of-scope contract. Conflating the two would mean
one capability doing two jobs with one schema, muddying both the prompt
and the evaluation harness's golden-case design.

**Decision: A.** `recommend_emission_factor()` returns `None` immediately
if the current calculation's `resolution_status` isn't exactly
`UNRESOLVED_NO_FACTOR`, or if (defensively) `activity_type` is somehow
still `None` despite that status. A future milestone that wants
activity-type-mapping assistance gets its own capability, prompt, and
schema — not a scope creep of this one.

## Decision 2: candidates are shown to the AI as labels, never as raw UUIDs

**Alternatives considered:**

**A. Show the AI a small, service-provided set of candidates as plain
labels (`candidate_1`, `candidate_2`, ..., or `"none"`); the AI's response
picks a label; the service resolves that label back to the real object it
already holds in memory** (chosen).

**B. Show the AI each candidate's real UUID and ask it to echo back the
UUID it recommends.** Rejected: LLMs are unreliable at reproducing long
identifiers verbatim — a single transposed or hallucinated character in a
UUID either resolves to the WRONG factor silently (the dangerous failure
mode: an AI recommendation the analyst trusts, pointing at an unrelated
factor) or to no factor at all (a false "AI recommended none" outcome).
Option A makes the wrong-object failure mode structurally impossible: the
service builds the label→object mapping itself from the exact candidate
list it queried, so a label the AI DID emit correctly can only ever
resolve to a real candidate it was actually shown, and a label it got
wrong (or invented, e.g. `candidate_99`) defensively resolves to no
factor rather than raising or guessing.

**Decision: A.** `FACTOR_RECOMMENDATION_V2`'s `recommended_candidate_label`
field is a free-form string by JSON Schema (not an enum, since the
candidate count varies per record), but the SERVICE — not the schema — is
what enforces "must be a label we actually offered." See
`apps.ai.services.factor_recommendation._candidate_factors()` /
`_format_candidates()`.

## Decision 3: `AIFactorRecommendation` is a new, dedicated model — not a reuse of `AIAnnotation`

**Alternatives considered:**

**A. A new model, `AIFactorRecommendation`** (chosen) — same
`AuditTrail`-style immutability pattern as `AIAnnotation` (`clean()`/
`delete()`/`save()` overrides plus a matching QuerySet-level bulk-update/
delete guard), same all-`PROTECT` FK discipline (no `SET_NULL` anywhere,
proven by the same kind of field-introspection test ADR 0009 established
for `AIAnnotation`), but a genuinely different output shape: a nullable
`recommended_factor` FK (`AIAnnotation` has no equivalent — nothing in 7b
identifies a specific OTHER governed object) and an
`alternative_candidates` list of labels, versus `AIAnnotation`'s
`contributing_factors` list of free-text strings.

**B. Reuse `AIAnnotation`, adding a `recommended_factor` FK and treating
`factor_recommendation` as a third `Capability` choice alongside
`anomaly_detection`.** Rejected: `AIAnnotation`'s fixed field set
(`explanation`, `contributing_factors`, `confidence`,
`suggested_investigation`) has no field that means "the specific object
this recommendation points at" — bolting on a `recommended_factor` FK that
only two of three (eventually more) capabilities ever populate turns the
model into a union type where most rows have unrelated `NULL` columns from
a different capability's outputs. `AIAnnotation`'s own serializer and
admin would need conditional logic per capability, which is exactly the
kind of implicit coupling `AIPromptVersion`'s content-hash versioning
already avoids elsewhere in this design. A new model keeps each
capability's persistence shape exactly as wide as its own contract needs,
mirroring how `AIInteraction` stays capability-agnostic (audit metadata
only) while each CAPABILITY gets its own advisory-output model.

**Decision: A.** `AIFactorRecommendation` lives in `apps/ai/models.py`
alongside `AIAnnotation`, registered read-only in the admin the same way,
with its own dedicated serializer (`AIFactorRecommendationSerializer`,
computing a human-readable `recommended_factor_label` rather than exposing
the raw FK) and its own read-only endpoint
(`GET /api/records/{id}/factor-recommendations/`).

## Consequences

- A future capability that also needs to reference a specific governed
  object (e.g. a validation-assistance capability suggesting a specific
  `ActivityMapping` row) has a clear precedent: a new dedicated model, not
  a growing union on `AIAnnotation`.
- `AIFactorRecommendation.recommended_factor` being nullable is a
  deliberate, honest outcome — not a data-quality problem to detect and
  filter. A `None` recommendation with a well-reasoned `explanation` is as
  valid a persisted row as one that names a factor.
- Like `AIAnnotation`, `AIFactorRecommendation` can never be the target of
  a `SET_NULL` cascade from any future FK Django might add, because there
  are none on this model to trigger one.
- `recommend_emission_factor()`'s independent candidate query
  (`_candidate_factors()`) deliberately does not import or modify
  `apps.carbon.services.resolution.FactorIndex` — that module's contract
  (`resolve()` returns a single winner or `None`) is unchanged, and this
  capability's candidate-gathering logic is free to evolve (e.g. tuning
  the candidate limit or ordering) without touching deterministic
  resolution code at all.
