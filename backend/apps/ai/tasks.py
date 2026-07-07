"""
Phase 7a -- apps.ai's Celery task(s). Kept as minimal as apps.core.tasks
until a real feature (7b+) needs its own async work: today, only the
scheduled ai_heartbeat_task exists, routed to the new 'ai' queue itself
(not 'maintenance') so it doubles as proof that queue actually has a live
consumer, not just that some worker generally is alive.
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
