"""
Phase 7c -- the factor_recommendation capability's own service. Runs only
against records whose deterministic resolution (apps.carbon.services.
resolution.FactorIndex, via apps.carbon.services.carbon_service) already
resolved an activity type but could not confidently choose a single
emission factor for it (EmissionCalculation.resolution_status ==
UNRESOLVED_NO_FACTOR). UNRESOLVED_NO_ACTIVITY_TYPE is explicitly out of
scope -- that is a different problem (activity-type mapping, not factor
selection) and is left for a future capability; see ADR 0010.

Deliberately does NOT import or modify apps.carbon.services.resolution --
FactorIndex.resolve() only ever returns a single winner or None, never a
candidate set. This module runs its own independent, read-only query
against EmissionFactor/EmissionFactorDataset to gather the candidates the
deterministic engine considered ambiguous.

The AI is never asked to reproduce an EmissionFactor's UUID (LLMs are
unreliable at reproducing identifiers verbatim). Candidates are shown to
the AI as small labels (candidate_1, candidate_2, ...) and the AI answers
with a label (or "none") -- this module resolves that label back to a real
object it already holds in memory, never trusting an AI-produced
identifier directly.

Read-only with respect to governed data: this module has no write path to
EmissionCalculation or EmissionFactor anywhere in this file (no .save(),
no .update(), no field mutation). See ADR 0010 and docs/AI_ARCHITECTURE.md's
I1/I2 invariants.
"""
from apps.ai.models import AIFactorRecommendation, AIInteraction
from apps.ai.services.gateway import invoke_ai
from apps.carbon.models import EmissionCalculation, EmissionFactor, EmissionFactorDataset

FACTOR_RECOMMENDATION_SCHEMA_VERSION = 2
CANDIDATE_LIMIT = 5


def _candidate_factors(activity_type, *, limit=CANDIDATE_LIMIT):
    """Independent, read-only query -- NOT apps.carbon.services.resolution.
    FactorIndex, which only ever returns a single winner. Ordered
    deterministically (dataset priority, then most recently imported, then
    id) so candidate_N labels are stable across repeated calls for the
    same activity type."""
    return list(
        EmissionFactor.objects.filter(
            activity_type=activity_type,
            dataset__status=EmissionFactorDataset.Status.ACTIVE,
        )
        .select_related("dataset", "region")
        .order_by("-dataset__priority", "-dataset__import_timestamp", "id")[:limit]
    )


def _format_candidates(candidates: list) -> str:
    """Human-readable text block the prompt shows the AI -- deterministic
    facts only (publisher, version, region, validity window, factor
    value), never an opinion or a pre-ranking."""
    if not candidates:
        return "(no candidate factors found for this activity type)"
    lines = []
    for i, f in enumerate(candidates, start=1):
        region_code = f.region.code if f.region else (f.dataset.region.code if f.dataset.region else "GLOBAL")
        valid_from = f.valid_from or f.dataset.valid_from
        valid_to = f.valid_to or f.dataset.valid_to
        lines.append(
            f"candidate_{i}: {f.dataset.publisher} {f.dataset.version}, region={region_code}, "
            f"valid {valid_from} to {valid_to or 'present'}, publisher={f.dataset.publisher}, "
            f"factor={f.co2e_per_unit} {f.unit}"
        )
    return "\n".join(lines)


def recommend_emission_factor(record, *, actor=None) -> AIFactorRecommendation | None:
    """Generates and persists one AIFactorRecommendation for `record` via
    the factor_recommendation capability. Returns None (writes nothing) if:
    - there is no current calculation for this record,
    - the calculation's resolution_status isn't UNRESOLVED_NO_FACTOR,
    - the calculation has no resolved activity_type (defensive -- shouldn't
      happen for UNRESOLVED_NO_FACTOR, which implies the activity type WAS
      resolved),
    - or the gateway call didn't succeed (AI disabled, over budget, egress
      blocked, schema invalid, provider error). A refused/failed call is
      still recorded in AIInteraction (the gateway's own job), but never
      produces a partial or placeholder recommendation. I6: fail-safe, not
      fail-open -- matching generate_anomaly_explanation's exact contract.

    Idempotency is deliberately NOT this function's concern -- it stays a
    pure "make one recommendation" primitive, reusable outside the async
    task path. The caller (generate_factor_recommendations_task) is
    responsible for skipping records that already have a recommendation.
    """
    calc = record.calculations.filter(is_current=True).first()
    if calc is None or calc.resolution_status != EmissionCalculation.ResolutionStatus.UNRESOLVED_NO_FACTOR:
        return None
    if calc.activity_type is None:
        return None

    candidates = _candidate_factors(calc.activity_type)

    org_region = ""
    factor_policy = getattr(record.organization, "factor_policy", None)
    if factor_policy is not None and factor_policy.default_region_id:
        org_region = factor_policy.default_region.code

    result = invoke_ai(
        organization=record.organization,
        actor=actor,
        capability="factor_recommendation",
        prompt_name="factor_recommendation",
        template_vars={
            "activity_type_name": calc.activity_type.name,
            "activity_type_code": calc.activity_type.code,
            "scope": calc.scope or "",
            "quantity": str(calc.activity_quantity) if calc.activity_quantity is not None else "",
            "unit": calc.activity_unit or "",
            "reporting_date": str(calc.reporting_date) if calc.reporting_date else "",
            "org_region": org_region,
            "candidates": _format_candidates(candidates),
        },
        response_schema_id="factor_recommendation",
        response_schema_version=FACTOR_RECOMMENDATION_SCHEMA_VERSION,
        context_provenance=[str(record.id)],
        idempotency_key=f"factor_recommendation:{record.id}",
    )

    if result.outcome != AIInteraction.Outcome.OK or result.parsed is None:
        return None

    label_to_factor = {f"candidate_{i}": f for i, f in enumerate(candidates, start=1)}
    recommended_factor = label_to_factor.get(result.parsed["recommended_candidate_label"])

    return AIFactorRecommendation.objects.create(
        organization=record.organization,
        record=record,
        interaction_id=result.interaction_id,
        recommended_factor=recommended_factor,
        confidence=result.parsed["confidence"],
        explanation=result.parsed["explanation"],
        reasoning=result.parsed["reasoning"],
        alternative_candidates=result.parsed["alternative_candidates"],
    )
