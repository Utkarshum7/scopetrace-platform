from django.db import connection
from django.http import JsonResponse
from django.views.decorators.http import require_GET


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
