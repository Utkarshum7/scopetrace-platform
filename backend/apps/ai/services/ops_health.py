"""
Phase 7g -- read-only AI operational health, distinct from
apps.ai.services.observability (business-usage metrics: how much AI is
being used and what it costs) and apps.core.views.healthz_ai (the public,
unauthenticated infra liveness probe every load balancer/orchestrator
polls, which stays minimal and fast on purpose). This module composes a
richer, authenticated-endpoint-facing ops snapshot by REUSING the exact
primitives healthz_ai already established (get_llm_provider construction
check, the AI heartbeat cache key) plus signals healthz_ai deliberately
never needed: queue depth (a capacity/observability concern, not a
pass/fail liveness gate), evaluation health, and replay-provider health
(dev/CI-facing, not something a container orchestrator should page on).

`ai_heartbeat_status()` here is the single source of truth for reading
apps.ai.tasks.AI_HEARTBEAT_CACHE_KEY -- apps.core.views.healthz_ai now
calls this instead of duplicating the cache-read/age-computation logic,
since apps.ai (not apps.core) owns that cache key.
"""
from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.ai.providers.factory import get_llm_provider
from apps.ai.providers.replay import fixture_stats
from apps.ai.services.observability import evaluation_summary


def ai_heartbeat_status() -> dict:
    """Reads apps.ai.tasks.ai_heartbeat_task's last cache write. Returns
    `{"status": "stale"}` if the key is missing/expired (task never ran,
    or its result expired), otherwise `{"status": "ok"/"error", ...}`."""
    from django.core.cache import cache

    from apps.ai.tasks import AI_HEARTBEAT_CACHE_KEY

    payload = cache.get(AI_HEARTBEAT_CACHE_KEY)
    if not payload:
        return {"status": "stale"}

    last_seen = parse_datetime(payload["timestamp"])
    age_seconds = (timezone.now() - last_seen).total_seconds()
    return {
        "status": payload.get("status", "unknown"),
        "worker_id": payload.get("worker_id"),
        "age_seconds": round(age_seconds, 1),
    }


def ai_provider_status() -> dict:
    """Same construction-only check /healthz/ai performs (no network I/O
    for any provider -- see get_llm_provider's own docstring), reported in
    a richer shape for an authenticated ops audience."""
    if not settings.AI_ENABLED:
        return {"status": "disabled", "ai_enabled": False}
    try:
        get_llm_provider()
    except Exception as exc:  # noqa: BLE001 - report any configuration failure
        return {
            "status": "unhealthy", "ai_enabled": True, "provider": settings.AI_PROVIDER,
            "model": settings.AI_DEFAULT_MODEL, "detail": f"provider misconfigured: {exc}",
        }
    return {
        "status": "ok", "ai_enabled": True, "provider": settings.AI_PROVIDER,
        "model": settings.AI_DEFAULT_MODEL,
    }


def ai_queue_depth(queue_name: str = "ai") -> dict:
    """Real backlog depth (not just worker liveness, which
    /healthz/worker already covers) via a direct Redis LLEN on the
    Celery+Redis transport's queue key -- read-only, no task is consumed
    or acknowledged. Returns status="unknown" (not a failure) when no
    broker is configured, matching /healthz/worker's own "not configured"
    handling."""
    if not settings.CELERY_BROKER_URL:
        return {"status": "unknown", "detail": "CELERY_BROKER_URL is not configured."}
    try:
        import redis

        client = redis.from_url(settings.CELERY_BROKER_URL)
        depth = client.llen(queue_name)
        return {"status": "ok", "queue": queue_name, "depth": depth}
    except Exception as exc:  # noqa: BLE001 - report any broker connectivity failure
        return {"status": "unknown", "detail": f"broker unreachable: {exc}"}


def replay_provider_health() -> dict:
    """Can the replay provider be constructed (mirrors ai_provider_status'
    construction-only check) plus the on-disk fixture inventory
    (apps.ai.providers.replay.fixture_stats) it depends on for its
    standalone case_id lookup mode."""
    try:
        get_llm_provider(provider_name="replay")
    except Exception as exc:  # noqa: BLE001 - report any configuration failure
        return {"status": "unhealthy", "detail": str(exc)}
    return {"status": "ok", **fixture_stats()}


def ai_ops_health() -> dict:
    """The full operational snapshot: AI health, provider status, queue
    depth, evaluation health, replay provider health."""
    return {
        "ai_provider": ai_provider_status(),
        "ai_heartbeat": ai_heartbeat_status(),
        "queue_depth": ai_queue_depth(),
        "evaluation": evaluation_summary(),
        "replay_provider": replay_provider_health(),
    }
