"""
Phase 7a -- apps.ai's Celery task(s). Phase 7b adds
generate_anomaly_explanations_task, the first real (non-heartbeat) work on
the 'ai' queue. Phase 7c adds generate_factor_recommendations_task. Phase
7d adds generate_validation_assistance_task. Phase 7f adds
generate_report_narration_task -- unlike the other three, dispatched from
a new API action, not an existing pipeline event (see ADR 0013).
"""
import logging

from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

# Mirrors apps.core.tasks.HEARTBEAT_CACHE_KEY's pattern -- read by a future
# /healthz/ai (this same milestone, next commit) as additive context.
AI_HEARTBEAT_CACHE_KEY = "ai:heartbeat:last_seen"


@shared_task(name='apps.ai.tasks.ai_heartbeat_task', bind=True)
def ai_heartbeat_task(self) -> str:
    """Constructs the configured AI provider adapter (config/credential
    presence only) -- deliberately NEVER makes a real provider call. A full
    end-to-end round trip would be a real, billable cost incurred on a
    fixed schedule with no feature yet using it; that's deferred until a
    real capability (7b+) exists to piggyback its cost on. This still
    proves two useful things cheaply: (a) the 'ai' queue has a live
    consumer, and (b) the provider is at least constructible (an
    ImproperlyConfigured here means a real call would fail too).

    Same TTL/cache-key/"stale means unknown, not a false healthy" contract
    as apps.core.tasks.heartbeat_task -- see that task's docstring.
    """
    status = "disabled"
    detail = ""

    if settings.AI_ENABLED:
        from apps.ai.providers.factory import get_llm_provider

        try:
            get_llm_provider()
            status = "ok"
        except Exception as exc:  # noqa: BLE001 - reported as heartbeat detail, not raised
            status = "provider_unavailable"
            detail = str(exc)

    cache.set(
        AI_HEARTBEAT_CACHE_KEY,
        {
            "timestamp": timezone.now().isoformat(),
            "worker_id": self.request.hostname,
            "status": status,
            "detail": detail,
        },
        timeout=settings.CELERY_HEARTBEAT_TTL_SECONDS,
    )
    logger.info("apps.ai.tasks.ai_heartbeat_task executed on %s: %s", self.request.hostname, status)
    return status


@shared_task(name='apps.ai.tasks.generate_anomaly_explanations_task')
def generate_anomaly_explanations_task(batch_id: str) -> str:
    """Phase 7b -- fire-and-forget, dispatched from apps.ingestion.tasks.
    ingest_task's success path, mirroring apps.core.tasks.
    send_notification_task's exact dispatch pattern (a separate,
    independently-scheduled task, never inline with ingestion). A slow or
    unavailable AI provider can therefore never delay or fail the
    deterministic ingestion pipeline -- this task doesn't even start until
    that pipeline has already committed its own transaction and moved on.

    Idempotent under Celery's at-least-once redelivery: re-derives the
    suspicious-record set fresh from the DB every run and skips any record
    that already has an anomaly_detection AIAnnotation, rather than
    trusting anything passed in the task message. One record's failure
    (caught broadly, logged, counted) never aborts the rest of the batch --
    matches generate_anomaly_explanation()'s own I6 fail-safe design one
    level up.
    """
    from apps.ai.models import AIAnnotation
    from apps.ai.services.anomaly_detection import generate_anomaly_explanation
    from apps.ingestion.models import EmissionRecord

    records = (
        EmissionRecord.objects.filter(batch_id=batch_id, is_suspicious=True)
        .exclude(ai_annotations__capability=AIAnnotation.Capability.ANOMALY_DETECTION)
        .select_related("organization", "batch__data_source")
    )

    generated = 0
    errored = 0
    for record in records:
        try:
            annotation = generate_anomaly_explanation(record)
        except Exception:  # noqa: BLE001 - one bad record must never abort the batch
            logger.exception(
                "generate_anomaly_explanations_task: record %s failed", record.id,
            )
            errored += 1
            continue
        if annotation is not None:
            generated += 1

    logger.info(
        "generate_anomaly_explanations_task: batch %s -- %s generated, %s errored",
        batch_id, generated, errored,
    )
    return f"generated={generated} errored={errored}"


@shared_task(name='apps.ai.tasks.generate_factor_recommendations_task')
def generate_factor_recommendations_task(batch_id: str) -> str:
    """Phase 7c -- fire-and-forget, dispatched from apps.carbon.tasks.
    calculate_task's success path, mirroring
    generate_anomaly_explanations_task's exact dispatch pattern (a
    separate, independently-scheduled task on the 'ai' queue, never inline
    with calculation). A slow or unavailable AI provider can therefore
    never delay or fail the deterministic calculation pipeline -- this
    task doesn't even start until CarbonCalculationService has already
    committed every EmissionCalculation row for the batch.

    Idempotent under Celery's at-least-once redelivery: re-derives the
    UNRESOLVED_NO_FACTOR record set fresh from the DB every run and skips
    any record that already has an AIFactorRecommendation, rather than
    trusting anything passed in the task message. One record's failure
    (caught broadly, logged, counted) never aborts the rest of the batch --
    matches recommend_emission_factor()'s own I6 fail-safe design one
    level up.
    """
    from apps.ai.services.factor_recommendation import recommend_emission_factor
    from apps.carbon.models import EmissionCalculation
    from apps.ingestion.models import EmissionRecord

    records = (
        EmissionRecord.objects.filter(
            batch_id=batch_id,
            calculations__is_current=True,
            calculations__resolution_status=EmissionCalculation.ResolutionStatus.UNRESOLVED_NO_FACTOR,
        )
        .exclude(ai_factor_recommendations__isnull=False)
        .select_related("organization", "batch__data_source")
        .distinct()
    )

    generated = 0
    errored = 0
    for record in records:
        try:
            recommendation = recommend_emission_factor(record)
        except Exception:  # noqa: BLE001 - one bad record must never abort the batch
            logger.exception(
                "generate_factor_recommendations_task: record %s failed", record.id,
            )
            errored += 1
            continue
        if recommendation is not None:
            generated += 1

    logger.info(
        "generate_factor_recommendations_task: batch %s -- %s generated, %s errored",
        batch_id, generated, errored,
    )
    return f"generated={generated} errored={errored}"


@shared_task(name='apps.ai.tasks.generate_validation_assistance_task')
def generate_validation_assistance_task(batch_id: str) -> str:
    """Phase 7d -- fire-and-forget, dispatched from apps.ingestion.tasks.
    ingest_task's success path (a sibling dispatch alongside
    generate_anomaly_explanations_task, not apps.carbon.tasks.
    calculate_task -- FAILED status is a validation-time decision, not a
    calculation-time one). A slow or unavailable AI provider can therefore
    never delay or fail the deterministic ingestion pipeline -- this task
    doesn't even start until that pipeline has already committed its own
    transaction and moved on.

    Idempotent under Celery's at-least-once redelivery: re-derives the
    FAILED-record set fresh from the DB every run and skips any record
    that already has a validation_assistance AIAnnotation, rather than
    trusting anything passed in the task message. One record's failure
    (caught broadly, logged, counted) never aborts the rest of the batch --
    matches generate_validation_assistance()'s own I6 fail-safe design one
    level up.
    """
    from apps.ai.models import AIAnnotation
    from apps.ai.services.validation_assistance import generate_validation_assistance
    from apps.ingestion.models import EmissionRecord

    records = (
        EmissionRecord.objects.filter(batch_id=batch_id, status=EmissionRecord.RecordStatus.FAILED)
        .exclude(ai_annotations__capability=AIAnnotation.Capability.VALIDATION_ASSISTANCE)
        .select_related("organization", "batch__data_source")
    )

    generated = 0
    errored = 0
    for record in records:
        try:
            annotation = generate_validation_assistance(record)
        except Exception:  # noqa: BLE001 - one bad record must never abort the batch
            logger.exception(
                "generate_validation_assistance_task: record %s failed", record.id,
            )
            errored += 1
            continue
        if annotation is not None:
            generated += 1

    logger.info(
        "generate_validation_assistance_task: batch %s -- %s generated, %s errored",
        batch_id, generated, errored,
    )
    return f"generated={generated} errored={errored}"


@shared_task(name='apps.ai.tasks.generate_report_narration_task')
def generate_report_narration_task(
    organization_id: str, date_from: str, date_to: str, scope: str = "", actor_id: str | None = None,
) -> str:
    """Phase 7f -- async, dispatched from a NEW API action
    (POST /api/report-narration/regenerate/), not an existing pipeline
    event: unlike anomaly/factor/validation, there is no ingest_task or
    calculate_task success path to hook into, because compliance reports
    are on-demand query results, never a persisted row a background job
    would naturally attach to (ADR 0002). The API view dispatches this
    task and returns immediately; a slow or unavailable AI provider can
    therefore never delay the report-narration request's own response,
    and (since this task is entirely outside the ingest -> calculate
    pipeline) can never affect report generation correctness either.

    Processes exactly one narration request, not a batch of records --
    unlike the other three AI tasks, there's no per-item loop to protect
    with a broad try/except; a real failure here is left to propagate
    to Celery's own task-failure handling (apps.tasks.signals' existing
    DEAD LETTER path already covers every task platform-wide).

    date_from/date_to arrive as ISO date strings (Celery task args must
    be JSON-serializable) and are parsed back to date objects here.
    """
    from datetime import date as date_cls

    from django.contrib.auth import get_user_model

    from apps.ai.services.report_narration import generate_report_narration
    from apps.core.models import Organization

    try:
        organization = Organization.objects.get(id=organization_id)
    except Organization.DoesNotExist:
        logger.error("generate_report_narration_task: organization %s does not exist", organization_id)
        return "organization_not_found"

    actor = None
    if actor_id:
        actor = get_user_model().objects.filter(id=actor_id).first()

    narration = generate_report_narration(
        organization, date_cls.fromisoformat(date_from), date_cls.fromisoformat(date_to),
        scope or None, actor=actor,
    )

    logger.info(
        "generate_report_narration_task: organization %s period %s..%s (scope=%s) -- %s",
        organization_id, date_from, date_to, scope or "ALL", "generated" if narration else "no_narration",
    )
    return "generated" if narration is not None else "no_narration"
