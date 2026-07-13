"""
Phase 7g -- read-only AI observability, cost-governance, and operational
health endpoints. Kept in their own module (mirroring
apps.carbon.metrics_views living apart from apps.carbon's main views.py)
since these are a distinct platform-ops/governance concern from
apps.ai.views' tenant-facing conversational/report-narration endpoints.

Every view here is GET-only by structure (plain APIView.get, no
mixins/actions that could mutate) -- these expose data this codebase
already writes, they never write anything themselves.
"""
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import CanViewAICosts, IsPlatformAdmin
from apps.accounts.tenancy import resolve_tenant_context
from apps.ai.services.cost_governance import org_cost_summary
from apps.ai.services.observability import platform_ai_summary
from apps.ai.services.ops_health import ai_ops_health


def _date_filters(request):
    filters = {}
    if request.query_params.get("date_from"):
        filters["date_from"] = request.query_params["date_from"]
    if request.query_params.get("date_to"):
        filters["date_to"] = request.query_params["date_to"]
    return filters


class AIObservabilityView(APIView):
    """GET /api/ai/ops/observability/ -- platform-wide AI usage/health
    metrics (apps.ai.services.observability.platform_ai_summary).
    Cross-tenant, so Platform Admin only -- same boundary as
    apps.carbon.metrics_views.PlatformMetricsView."""
    permission_classes = [IsPlatformAdmin]

    def get(self, request):
        return Response(platform_ai_summary(_date_filters(request)))


class AIOpsHealthView(APIView):
    """GET /api/ai/ops/health/ -- AI provider status, AI heartbeat, 'ai'
    queue depth, evaluation health, replay provider health
    (apps.ai.services.ops_health.ai_ops_health). Platform Admin only --
    richer/authenticated counterpart to the public /healthz/ai probe."""
    permission_classes = [IsPlatformAdmin]

    def get(self, request):
        return Response(ai_ops_health())


class AICostGovernanceView(APIView):
    """GET /api/ai/costs/ -- token consumption, estimated spend, budget
    utilization, provider distribution, and capability distribution for
    the active organization (apps.ai.services.cost_governance.
    org_cost_summary). Org Admin / Auditor only (CanViewAICosts, an inert
    Phase 7a seam this milestone activates) -- AI spend is governance-
    adjacent observability, the same boundary as the audit/activity feed,
    not the broader CanUseAI gate."""
    permission_classes = [CanViewAICosts]

    def get(self, request):
        ctx = resolve_tenant_context(request)
        if ctx.organization is None:
            raise PermissionDenied("Select an organization (X-Organization-ID) to view AI costs.")
        return Response(org_cost_summary(ctx.organization, _date_filters(request)))
