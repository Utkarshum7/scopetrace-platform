"""
Foundational Celery tasks for apps.core.

Kept intentionally free of business logic — apps.core owns cross-cutting
infrastructure concerns (health, in Phase 5 also task plumbing), not domain
rules. Domain tasks (ingestion, carbon calculation) live in their own apps'
tasks.py modules and call existing services, never embed logic inline.
"""
import logging

from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

# Cache key + helpers shared with apps.core.views.healthz_worker, which reads
# this to report heartbeat freshness as a passive, complementary signal
# alongside its existing active `inspect().ping()` check (Phase 5f).
HEARTBEAT_CACHE_KEY = "tasks:heartbeat:last_seen"


@shared_task(name='apps.core.tasks.ping')
def ping() -> str:
    """Trivial liveness task — proves the broker/worker pipeline end-to-end.

    Used by tests (called eagerly) and, against a running compose stack, via
    `ping.delay().get()` to confirm a real worker picked it up.
    """
    logger.info("apps.core.tasks.ping executed")
    return "pong"


@shared_task(name='apps.core.tasks.heartbeat_task', bind=True)
def heartbeat_task(self) -> str:
    """Phase 5f — Beat-driven passive heartbeat.

    Implements what apps.core.views.healthz_worker's docstring has promised
    since Phase 5a: `inspect().ping()` is an active, synchronous
    control-plane round trip that can hang under some broker-partition
    conditions (mitigated there with a 2s timeout, but still adds latency to
    every health check). This task instead writes a timestamp + worker
    hostname to cache once a minute — a health check reads that key as a
    cheap, non-blocking signal: "was a worker actually executing scheduled
    work recently", independent of whether a live inspect() round trip
    currently succeeds.

    TTL is CELERY_HEARTBEAT_TTL_SECONDS (default 180s = 3x the 1-minute
    schedule) — if Beat or every worker genuinely stops, the key expires on
    its own rather than reporting a stale success forever; a health check
    sees a missing key as "unknown/stale", not a false "healthy".

    Deliberately additive, not authoritative: healthz_worker's existing
    pass/fail HTTP status logic is unchanged (extend, not redesign) — this
    is reported as extra context in that endpoint's payload.
    """
    cache.set(
        HEARTBEAT_CACHE_KEY,
        {"timestamp": timezone.now().isoformat(), "worker_id": self.request.hostname},
        timeout=settings.CELERY_HEARTBEAT_TTL_SECONDS,
    )
    logger.info("apps.core.tasks.heartbeat_task executed on %s", self.request.hostname)
    return "ok"
