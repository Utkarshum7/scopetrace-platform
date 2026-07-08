"""
Phase 7a -- apps.ai's Celery task(s). Phase 7b adds
generate_anomaly_explanations_task, the first real (non-heartbeat) work on
the 'ai' queue.
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
