"""
Metrics API — aggregated, tenant-scoped, cached read endpoints for dashboards.

Aggregation lives in MetricsService (pure); caching in metrics_cache (per-org
version invalidation). Payloads are JSON-normalized (Decimal -> str, date ->
ISO) so precision is preserved across cache backends and the wire.
"""
from datetime import date
from decimal import Decimal

from django.db.models import Count, Sum
from rest_framework import serializers, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import CanViewActivity, IsOrgMember, IsPlatformAdmin
from apps.accounts.tenancy import resolve_tenant_context
from apps.audit.models import AuditTrail
from apps.carbon.models import EmissionCalculation, EmissionFactorDataset, Scope
from apps.carbon.services import metrics_cache
from apps.carbon.services.metrics import MetricsService
from apps.core.models import Organization
from apps.ingestion.models import EmissionRecord

_service = MetricsService()


def _jsonify(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonify(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonify(v) for v in value]
    return value


class MetricsFilterSerializer(serializers.Serializer):
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)
    scope = serializers.ChoiceField(choices=Scope.choices, required=False)
    data_source = serializers.UUIDField(required=False)

    def to_filters(self):
        d = self.validated_data
        return {
            key: d[key]
            for key in ("date_from", "date_to", "scope", "data_source")
            if d.get(key) is not None
        }


class _BaseMetricsView(APIView):
    permission_classes = [IsOrgMember]

    def _resolve(self, request):
        ctx = resolve_tenant_context(request)
        if ctx.organization is None:
            raise PermissionDenied(
                "Select an organization (X-Organization-ID) or use /api/metrics/platform/."
            )
        fs = MetricsFilterSerializer(data=request.query_params)
        fs.is_valid(raise_exception=True)
        return ctx.organization, fs.to_filters()


class MetricsSummaryView(_BaseMetricsView):
    def get(self, request):
        org, filters = self._resolve(request)
        params = {"kind": "summary", **filters}
        data = metrics_cache.cached(
            org.id, "summary", params, lambda: _service.summary(org, filters)
        )
        return Response(_jsonify(data))


class MetricsTimeseriesView(_BaseMetricsView):
    def get(self, request):
        org, filters = self._resolve(request)
        bucket = request.query_params.get("bucket", "month")
        group_by = request.query_params.get("group_by")
        params = {"kind": "timeseries", "bucket": bucket, "group_by": group_by, **filters}
        data = metrics_cache.cached(
            org.id, "timeseries", params,
            lambda: _service.timeseries(org, filters, bucket=bucket, group_by=group_by),
        )
        return Response(_jsonify(data))


class MetricsBreakdownView(_BaseMetricsView):
    def get(self, request):
        org, filters = self._resolve(request)
        dimension = request.query_params.get("dimension", "scope")
        params = {"kind": "breakdown", "dimension": dimension, **filters}
        data = metrics_cache.cached(
            org.id, "breakdown", params,
            lambda: _service.breakdown(org, filters, dimension=dimension),
        )
        return Response(_jsonify(data))


class ActivityFeedView(APIView):
    """Recent audit-trail activity for the active organization (Org Admin / Auditor)."""
    permission_classes = [CanViewActivity]

    def get(self, request):
        ctx = resolve_tenant_context(request)
        if ctx.organization is None:
            raise PermissionDenied("Select an organization (X-Organization-ID).")
        entries = (
            AuditTrail.objects.filter(organization=ctx.organization)
            .select_related("changed_by")
            .order_by("-timestamp")[:50]
        )
        return Response([
            {
                "action": e.action,
                "changed_by": e.changed_by.username if e.changed_by else None,
                "record_id": str(e.record_id) if e.record_id else None,
                "reason": e.reason,
                "timestamp": e.timestamp.isoformat(),
            }
            for e in entries
        ])


class PlatformMetricsView(APIView):
    """Cross-tenant overview for Platform Admins (superusers) only."""
    permission_classes = [IsPlatformAdmin]

    def get(self, request):
        calc_qs = EmissionCalculation.objects.filter(
            is_current=True, resolution_status=EmissionCalculation.ResolutionStatus.CALCULATED
        )
        per_org = (
            calc_qs.values("organization__id", "organization__name")
            .annotate(co2e=Sum("co2e_tonnes"), calculations=Count("id"))
            .order_by("-co2e")
        )
        organizations = [
            {
                "id": str(r["organization__id"]),
                "name": r["organization__name"],
                "co2e_tonnes": str(r["co2e"] or Decimal("0")),
                "calculations": r["calculations"],
            }
            for r in per_org
        ]
        totals = {
            "organizations": Organization.objects.count(),
            "records": EmissionRecord.objects.count(),
            "current_calculations": EmissionCalculation.objects.filter(is_current=True).count(),
            "total_co2e_tonnes": str(calc_qs.aggregate(t=Sum("co2e_tonnes"))["t"] or Decimal("0")),
            "active_datasets": EmissionFactorDataset.objects.filter(
                status=EmissionFactorDataset.Status.ACTIVE
            ).count(),
        }
        return Response({"totals": totals, "organizations": organizations}, status=status.HTTP_200_OK)
