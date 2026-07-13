"""
Phase 7f -- the report_narration capability's own service. Async, on the
`ai` queue (apps.ai.tasks.generate_report_narration_task), dispatched
from a new API-triggered action rather than an existing pipeline event --
compliance reports are on-demand query results (ADR 0002), never a
background job a service call could hook into. See ADR 0013.

Read-only with respect to governed data: this module has no write path to
EmissionRecord/EmissionCalculation/EmissionFactor anywhere in this file --
apps.ai.services.report_context_builder does the only reading, and this
module only ever writes AIReportNarration rows.
"""
from apps.ai.models import AIInteraction, AIReportNarration
from apps.ai.services.gateway import invoke_ai
from apps.ai.services.report_context_builder import build_report_context

REPORT_NARRATION_SCHEMA_VERSION = 2


def generate_report_narration(organization, date_from, date_to, scope=None, *, actor=None) -> AIReportNarration | None:
    """Generates and persists one AIReportNarration for the given report
    period. Returns None (writes nothing) if the gateway call didn't
    succeed (AI disabled, over budget, egress blocked, schema invalid,
    provider error) -- a refused/failed call is still recorded in
    AIInteraction (the gateway's own job), but never produces a partial
    or placeholder narration. I6: fail-safe, not fail-open, matching
    every other Phase 7 capability's service contract.

    Idempotent by construction: idempotency_key is derived from the
    exact report period, so a redelivered Celery task (ACKS_LATE
    at-least-once) never double-bills the provider for the same period --
    it replays the prior gateway outcome instead. Does NOT skip
    generating a new narration just because one already exists for this
    period (unlike the other capabilities' task-level "already
    annotated" exclusion) -- regeneration is an explicit, intentional
    action here (see the API's /regenerate/ action), and history is kept,
    not overwritten.
    """
    context = build_report_context(organization, date_from, date_to, scope)

    result = invoke_ai(
        organization=organization,
        actor=actor,
        capability="report_narration",
        prompt_name="report_narration",
        template_vars={
            "report_context": context,
            "date_from": str(date_from),
            "date_to": str(date_to),
            "scope": scope or "ALL",
        },
        response_schema_id="report_narration",
        response_schema_version=REPORT_NARRATION_SCHEMA_VERSION,
        context_provenance=[str(organization.id), str(date_from), str(date_to), scope or "ALL"],
        idempotency_key=f"report_narration:{organization.id}:{date_from}:{date_to}:{scope or 'ALL'}",
    )

    if result.outcome != AIInteraction.Outcome.OK or result.parsed is None:
        return None

    return AIReportNarration.objects.create(
        organization=organization,
        interaction_id=result.interaction_id,
        date_from=date_from,
        date_to=date_to,
        scope=scope or "",
        executive_summary=result.parsed["executive_summary"],
        key_highlights=result.parsed["key_highlights"],
        trend_explanations=result.parsed["trend_explanations"],
        recommendations=result.parsed["recommendations"],
        confidence=result.parsed["confidence"],
    )
