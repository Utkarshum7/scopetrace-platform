"""
Phase 7b -- the anomaly_detection capability's own service. Builds the
prompt's context from an ALREADY-suspicious EmissionRecord (the
deterministic engine's own decision -- apps.ingestion.services.validator.
RowValidator -- never re-derived or second-guessed here), calls
invoke_ai(), and persists the result as one immutable AIAnnotation.

Read-only with respect to governed data: this module has no write path to
EmissionRecord (no .save(), no .update(), no status/is_suspicious/
validation_errors mutation anywhere in this file) -- it only ever reads a
record's existing fields to build prompt context. See ADR 0009 and
docs/AI_ARCHITECTURE.md's I1/I2 invariants.
"""
from apps.ai.models import AIAnnotation, AIInteraction
from apps.ai.services.gateway import invoke_ai

ANOMALY_DETECTION_SCHEMA_VERSION = 2


def _format_validation_flags(validation_errors: dict) -> str:
    """Flattens EmissionRecord.validation_errors (a {field: [messages]}
    dict) into the plain-text evidence the anomaly_detection prompt
    explains -- the deterministic engine's own reasoning, verbatim, never
    paraphrased or re-derived here."""
    if not validation_errors:
        return "(no specific validation messages recorded)"
    lines = [
        f"{field_name}: {message}"
        for field_name, messages in validation_errors.items()
        for message in messages
    ]
    return "\n".join(lines) if lines else "(no specific validation messages recorded)"


def generate_anomaly_explanation(record, *, actor=None) -> AIAnnotation | None:
    """Generates and persists one AIAnnotation for `record` via the
    anomaly_detection capability. Returns None (writes no annotation) if
    the gateway call didn't succeed (AI disabled, over budget, egress
    blocked, schema invalid, provider error) -- a refused/failed call is
    still recorded in AIInteraction (the gateway's own job), but never
    produces a partial or placeholder AIAnnotation. I6: fail-safe, not
    fail-open.

    Idempotency is deliberately NOT this function's concern -- it stays a
    pure "make one explanation" primitive, reusable outside the async task
    path (e.g. a future on-demand regenerate action). The caller
    (generate_anomaly_explanations_task) is responsible for skipping
    records that already have an annotation.
    """
    source_type = ""
    if record.batch_id and record.batch.data_source_id:
        source_type = record.batch.data_source.source_type

    result = invoke_ai(
        organization=record.organization,
        actor=actor,
        capability="anomaly_detection",
        prompt_name="anomaly_detection",
        template_vars={
            "scope_category": record.scope_category or "",
            "source_type": source_type,
            "quantity": str(record.normalized_value) if record.normalized_value is not None else "",
            "unit": record.normalized_unit or "",
            "validation_flags": _format_validation_flags(record.validation_errors),
        },
        response_schema_id="anomaly_detection",
        response_schema_version=ANOMALY_DETECTION_SCHEMA_VERSION,
        context_provenance=[str(record.id)],
        idempotency_key=f"anomaly_detection:{record.id}",
    )

    if result.outcome != AIInteraction.Outcome.OK or result.parsed is None:
        return None

    return AIAnnotation.objects.create(
        organization=record.organization,
        record=record,
        interaction_id=result.interaction_id,
        capability=AIAnnotation.Capability.ANOMALY_DETECTION,
        explanation=result.parsed["explanation"],
        contributing_factors=result.parsed["contributing_factors"],
        confidence=result.parsed["confidence"],
        suggested_investigation=result.parsed["suggested_investigation"],
    )
