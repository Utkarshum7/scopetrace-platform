from rest_framework import viewsets

from apps.accounts.mixins import TenantScopedViewSetMixin
from apps.accounts.permissions import IsOrgMember
from apps.carbon.models import (
    ActivityType,
    EmissionCalculation,
    EmissionFactor,
    EmissionFactorDataset,
)
from apps.carbon.serializers import (
    ActivityTypeSerializer,
    EmissionCalculationSerializer,
    EmissionFactorDatasetSerializer,
    EmissionFactorSerializer,
)


# --- Global reference data (shared; any authenticated member may read) ---
class ActivityTypeViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = ActivityType.objects.all()
    serializer_class = ActivityTypeSerializer
    permission_classes = [IsOrgMember]


class FactorDatasetViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = EmissionFactorDataset.objects.select_related("region", "imported_by").all()
    serializer_class = EmissionFactorDatasetSerializer
    permission_classes = [IsOrgMember]

    def get_queryset(self):
        qs = super().get_queryset()
        publisher = self.request.query_params.get("publisher")
        if publisher:
            qs = qs.filter(publisher=publisher.upper())
        status_param = self.request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param.upper())
        return qs


class EmissionFactorViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = EmissionFactor.objects.select_related("activity_type", "dataset", "region").all()
    serializer_class = EmissionFactorSerializer
    permission_classes = [IsOrgMember]

    def get_queryset(self):
        qs = super().get_queryset()
        activity_type = self.request.query_params.get("activity_type")
        if activity_type:
            qs = qs.filter(activity_type__code=activity_type.upper())
        region = self.request.query_params.get("region")
        if region:
            qs = qs.filter(region__code=region.upper())
        return qs


# --- Tenant-scoped calculations ---
class EmissionCalculationViewSet(TenantScopedViewSetMixin, viewsets.ReadOnlyModelViewSet):
    queryset = (
        EmissionCalculation.objects
        .select_related("emission_factor", "activity_type")
        .all()
    )
    serializer_class = EmissionCalculationSerializer
    permission_classes = [IsOrgMember]

    def get_queryset(self):
        qs = super().get_queryset()  # tenant-scoped by the mixin
        current = self.request.query_params.get("current")
        if current is not None:
            qs = qs.filter(is_current=current.lower() in ("true", "1"))
        status_param = self.request.query_params.get("status")
        if status_param:
            qs = qs.filter(resolution_status=status_param.upper())
        return qs
