"""
Phase 7d -- the validation_assistance capability's own service. Builds the
prompt's context from an ALREADY-failed EmissionRecord (the deterministic
engine's own decision -- apps.ingestion.services.validator.RowValidator --
never re-derived or second-guessed here), calls invoke_ai(), and persists
the result as one immutable AIAnnotation (capability=VALIDATION_ASSISTANCE
-- see ADR 0011 for why this reuses AIAnnotation rather than a new model).

Read-only with respect to governed data: this module has no write path to
EmissionRecord (no .save(), no .update(), no status/validation_errors
mutation anywhere in this file) -- it only ever reads a record's existing
fields to build prompt context. See ADR 0011 and docs/AI_ARCHITECTURE.md's
I1/I2 invariants.
"""
from apps.ai.models import AIAnnotation, AIInteraction
from apps.ai.services.gateway import invoke_ai

VALIDATION_ASSISTANCE_SCHEMA_VERSION = 2


def _format_validation_errors(validation_errors: dict) -> str:
    """Flattens EmissionRecord.validation_errors (a {field: [messages]}
    dict) into the plain-text evidence the validation_assistance prompt
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


def _format_raw_payload(raw_data_payload: dict) -> str:
    """The as-ingested source row, verbatim -- gives the AI the concrete
    field values behind the validation errors above, without this module
    re-parsing or re-normalizing anything itself."""
    if not raw_data_payload:
        return "(no raw payload recorded)"
    return str(raw_data_payload)


def generate_validation_assistance(record, *, actor=None) -> AIAnnotation | None:
    """Generates and persists one AIAnnotation for `record` via the
    validation_assistance capability. Returns None (writes no annotation)
    if the gateway call didn't succeed (AI disabled, over budget, egress
    blocked, schema invalid, provider error) -- a refused/failed call is
    still recorded in AIInteraction (the gateway's own job), but never
    produces a partial or placeholder AIAnnotation. I6: fail-safe, not
    fail-open -- matching generate_anomaly_explanation's exact contract.

    Idempotency is deliberately NOT this function's concern -- it stays a
    pure "make one explanation" primitive, reusable outside the async task
    path. The caller (generate_validation_assistance_task) is responsible
    for skipping records that already have one.
    """
    source_type = ""
    if record.batch_id and record.batch.data_source_id:
        source_type = record.batch.data_source.source_type

    result = invoke_ai(
        organization=record.organization,
        actor=actor,
        capability="validation_assistance",
        prompt_name="validation_assistance",
        template_vars={
            "scope_category": record.scope_category or "",
            "source_type": source_type,
            "quantity": str(record.normalized_value) if record.normalized_value is not None else "",
            "unit": record.normalized_unit or "",
            "validation_errors": _format_validation_errors(record.validation_errors),
            "raw_payload": _format_raw_payload(record.raw_data_payload),
        },
        response_schema_id="validation_assistance",
        response_schema_version=VALIDATION_ASSISTANCE_SCHEMA_VERSION,
        context_provenance=[str(record.id)],
        idempotency_key=f"validation_assistance:{record.id}",
    )

    if result.outcome != AIInteraction.Outcome.OK or result.parsed is None:
        return None

    return AIAnnotation.objects.create(
        organization=record.organization,
        record=record,
        interaction_id=result.interaction_id,
        capability=AIAnnotation.Capability.VALIDATION_ASSISTANCE,
        explanation=result.parsed["explanation"],
        contributing_factors=result.parsed["affected_fields"],
        confidence=result.parsed["confidence"],
        suggested_investigation=result.parsed["suggested_correction"],
    )
