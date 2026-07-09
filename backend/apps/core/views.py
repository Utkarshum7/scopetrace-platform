import logging

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET

logger = logging.getLogger(__name__)


@require_GET
def healthz(request):
    """
    Database-aware health probe.

    Unlike the DRF router root (which never touches the database), this endpoint
    executes a trivial query so the health check actually reflects whether the
    service can serve data. Returns 200 when the DB is reachable, 503 otherwise —
    this lets Render/Kubernetes stop routing traffic to a broken instance instead
    of reporting "healthy" during a total data-layer outage.
    """
    database_ok = True
    detail = None
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception as exc:  # noqa: BLE001 - report any connectivity failure
        database_ok = False
        detail = str(exc)

    payload = {
        "status": "ok" if database_ok else "unhealthy",
        "database": "ok" if database_ok else "unreachable",
    }
    if detail:
        payload["detail"] = detail

    return JsonResponse(payload, status=200 if database_ok else 503)


@require_GET
def healthz_worker(request):
    """
    Celery worker health probe (Phase 5).

    Unlike /healthz (which only proves the web process can reach the DB), this
    proves at least one Celery worker is alive and actually consuming from the
    broker — a real control-plane round trip (`celery inspect ping`), not a
    passive "is Redis reachable" check. Distinguishes three states with a
    distinct, actionable `detail` message:
      - broker not configured at all (no CELERY_BROKER_URL) -> 503
      - broker configured but unreachable (Redis down/misconfigured) -> 503
      - broker reachable but zero workers respond (no worker process running,
        or `CELERY_TASK_ALWAYS_EAGER` — nothing is actually consuming) -> 503
      - one or more workers respond -> 200, worker hostnames included as a
        lightweight liveness metric.

    Phase 5f adds a complementary Beat-driven passive heartbeat on top of this
    (inspect().ping() can hang under some broker-partition conditions):
    apps.core.tasks.heartbeat_task writes a timestamp to cache once a minute,
    and its freshness is reported below as an additional `beat_heartbeat`
    field — additive context only, never changing this endpoint's pass/fail
    HTTP status, which remains driven by the active inspect().ping() check
    alone (extend, not redesign). This endpoint alone was already sufficient
    for Phase 5a's scope (proving the pipeline exists and works at all).
    """
    if not settings.CELERY_BROKER_URL:
        return JsonResponse(
            {
                "status": "unhealthy",
                "detail": "CELERY_BROKER_URL is not configured.",
                **_beat_heartbeat(),
            },
            status=503,
        )

    from config.celery import app as celery_app

    try:
        replies = celery_app.control.inspect(timeout=2.0).ping()
    except Exception as exc:  # noqa: BLE001 - report any broker connectivity failure
        logger.warning("Celery worker health check failed: broker unreachable: %s", exc)
        return JsonResponse(
            {"status": "unhealthy", "detail": f"broker unreachable: {exc}", **_beat_heartbeat()},
            status=503,
        )

    if not replies:
        return JsonResponse(
            {
                "status": "unhealthy",
                "detail": "broker reachable, but no workers responded.",
                **_beat_heartbeat(),
            },
            status=503,
        )

    return JsonResponse(
        {"status": "ok", "workers": sorted(replies.keys()), **_beat_heartbeat()},
        status=200,
    )


@require_GET
def healthz_ai(request):
    """
    AI foundation health probe (Phase 7a).

    Unlike /healthz and /healthz/worker (infrastructure this platform always
    needs), AI is opt-in -- AI_ENABLED=False (the default) is expected,
    healthy state, not a failure: returns 200 with ai_enabled=False rather
    than 503, exactly like a deliberately-disabled feature shouldn't page
    anyone.

    When AI_ENABLED=True, the check is cheap and synchronous, same "no
    network I/O, no cost" discipline healthz_worker's own inspect() call
    has: can the configured provider adapter even be constructed
    (config/credentials present)? Constructing an LLMProvider does no
    network call for any adapter -- see each provider's __init__ docstring.
    A real end-to-end provider round trip is never made here --
    apps.ai.tasks.ai_heartbeat_task's docstring explains why that's a
    deliberate, deferred trade-off. That task's last result is reported
    below as additive `ai_heartbeat` context (same "additive, never changes
    pass/fail status" pattern as healthz_worker's beat_heartbeat), not the
    authoritative pass/fail signal.
    """
    if not settings.AI_ENABLED:
        return JsonResponse(
            {"status": "ok", "ai_enabled": False, "detail": "AI is disabled (AI_ENABLED=False)."},
            status=200,
        )

    from apps.ai.providers.factory import get_llm_provider

    try:
        get_llm_provider()
    except Exception as exc:  # noqa: BLE001 - report any configuration failure
        return JsonResponse(
            {
                "status": "unhealthy",
                "ai_enabled": True,
                "provider": settings.AI_PROVIDER,
                "model": settings.AI_DEFAULT_MODEL,
                "detail": f"provider misconfigured: {exc}",
                **_ai_heartbeat(),
            },
            status=503,
        )

    return JsonResponse(
        {
            "status": "ok",
            "ai_enabled": True,
            "provider": settings.AI_PROVIDER,
            "model": settings.AI_DEFAULT_MODEL,
            **_ai_heartbeat(),
        },
        status=200,
    )


def _ai_heartbeat() -> dict:
    """Mirrors _beat_heartbeat()'s exact shape/semantics -- see that
    function. Phase 7g moved the actual cache-read/age-computation logic
    to apps.ai.services.ops_health.ai_heartbeat_status(), since apps.ai
    (not apps.core) owns AI_HEARTBEAT_CACHE_KEY; this stays a thin
    wrapper so /healthz/ai's response shape is unchanged."""
    from apps.ai.services.ops_health import ai_heartbeat_status

    return {"ai_heartbeat": ai_heartbeat_status()}


def _beat_heartbeat() -> dict:
    """Reads apps.core.tasks.heartbeat_task's last cache write.

    Returns a `beat_heartbeat` dict — `{"status": "stale"}` if the key is
    missing/expired (Beat down, or every worker down, or simply not deployed
    yet — e.g. local `manage.py runserver` without a worker/beat process),
    otherwise `{"status": "ok", "worker_id": ..., "age_seconds": ...}`.
    """
    from apps.core.tasks import HEARTBEAT_CACHE_KEY

    payload = cache.get(HEARTBEAT_CACHE_KEY)
    if not payload:
        return {"beat_heartbeat": {"status": "stale"}}

    last_seen = parse_datetime(payload["timestamp"])
    age_seconds = (timezone.now() - last_seen).total_seconds()
    return {
        "beat_heartbeat": {
            "status": "ok",
            "worker_id": payload.get("worker_id"),
            "age_seconds": round(age_seconds, 1),
        }
    }
