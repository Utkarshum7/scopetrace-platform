import logging

from django.conf import settings
from django.db import connection
from django.http import JsonResponse
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

    Phase 5h adds a complementary Beat-driven passive heartbeat on top of this
    (inspect().ping() can hang under some broker-partition conditions); this
    endpoint alone is sufficient for Phase 5a's scope (proving the pipeline
    exists and works at all).
    """
    if not settings.CELERY_BROKER_URL:
        return JsonResponse(
            {"status": "unhealthy", "detail": "CELERY_BROKER_URL is not configured."},
            status=503,
        )

    from config.celery import app as celery_app

    try:
        replies = celery_app.control.inspect(timeout=2.0).ping()
    except Exception as exc:  # noqa: BLE001 - report any broker connectivity failure
        logger.warning("Celery worker health check failed: broker unreachable: %s", exc)
        return JsonResponse(
            {"status": "unhealthy", "detail": f"broker unreachable: {exc}"},
            status=503,
        )

    if not replies:
        return JsonResponse(
            {"status": "unhealthy", "detail": "broker reachable, but no workers responded."},
            status=503,
        )

    return JsonResponse(
        {"status": "ok", "workers": sorted(replies.keys())},
        status=200,
    )
