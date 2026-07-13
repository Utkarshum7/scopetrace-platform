"""
Foundational Celery tasks for apps.core.

Kept intentionally free of business logic — apps.core owns cross-cutting
infrastructure concerns (health, in Phase 5 also task plumbing), not domain
rules. Domain tasks (ingestion, carbon calculation) live in their own apps'
tasks.py modules and call existing services, never embed logic inline.
"""
import logging
import smtplib

from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

# Cache key + helpers shared with apps.core.views.healthz_worker, which reads
# this to report heartbeat freshness as a passive, complementary signal
# alongside its existing active `inspect().ping()` check (Phase 5f).
HEARTBEAT_CACHE_KEY = "tasks:heartbeat:last_seen"

# Retry policy for send_notification_task (Phase 5g) — designed
# independently of ingest_task's/calculate_task's (5e), same rationale: a
# transient SMTP connectivity issue is worth retrying, a deterministic one
# (bad template data, malformed recipient) is not. smtplib.SMTPException
# covers protocol-level failures (e.g. temporary 4xx SMTP responses);
# OSError covers connection-level failures (refused/reset/timeout — Python's
# socket errors are OSError subclasses). max_retries=3, backoff 2s/4s/8s
# (capped 60s) — mirrors ingest_task's policy: sending one email is cheap
# and fast, three attempts spanning ~14s comfortably rides out a brief SMTP
# blip without holding a worker slot for long.
NOTIFICATION_RETRYABLE_EXCEPTIONS = (smtplib.SMTPException, OSError)


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


@shared_task(
    name='apps.core.tasks.send_notification_task',
    bind=True,
    autoretry_for=NOTIFICATION_RETRYABLE_EXCEPTIONS,
    retry_backoff=2,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=3,
)
def send_notification_task(self, batch_id: str) -> str:
    """Phase 5g — fire-and-forget email notification for a batch that just
    reached its final resting state.

    Dispatched via .delay() from the three places a batch can actually reach
    that state (see docs/NOTIFICATIONS.md): ingest_task's non-retryable
    failure path, calculate_task's success and non-retryable failure paths,
    and apps.tasks.signals's dead-letter handler (retries exhausted) — never
    called synchronously from any of them, so a slow or down SMTP server can
    never hold up a worker slot that could otherwise process real ingestion/
    calculation work, and can never affect UploadBatch state (this task only
    ever reads the batch, never writes to it).

    Deliberately has no idempotency guard against double-sending — unlike
    ingest_task/calculate_task, there's no persistent state this task could
    corrupt by re-running, only a possible duplicate email in the rare case
    of a worker crashing after send_mail() truly succeeded but before Celery
    could ack (acks_late's normal at-least-once redelivery). An occasional
    duplicate email on that rare timing coincidence is an acceptable
    trade-off against the complexity of a dedup mechanism for a low-severity
    side effect — see docs/NOTIFICATIONS.md.
    """
    from apps.core.notifications import notify_batch_result
    from apps.ingestion.models import UploadBatch

    attempt_label = "initial attempt" if self.request.retries == 0 else (
        f"retry attempt {self.request.retries}/{self.max_retries}"
    )

    try:
        batch = UploadBatch.objects.select_related("uploaded_by").get(pk=batch_id)
    except UploadBatch.DoesNotExist:
        logger.error("send_notification_task: batch %s does not exist", batch_id)
        return "batch-not-found"

    sent = notify_batch_result(batch)
    logger.info(
        "send_notification_task: batch %s notification %s (%s)",
        batch_id, "sent" if sent else "skipped", attempt_label,
    )
    return "sent" if sent else "skipped"
