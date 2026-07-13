from rest_framework import viewsets

from apps.accounts.mixins import TenantScopedViewSetMixin
from apps.accounts.permissions import IsOrgMember
from apps.carbon.cache_mixin import CachedReferenceListMixin
from apps.carbon.filters import (
    CalculationFilter,
    EmissionFactorFilter,
    FactorDatasetFilter,
)
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
class ActivityTypeViewSet(CachedReferenceListMixin, viewsets.ReadOnlyModelViewSet):
    queryset = ActivityType.objects.all()
    serializer_class = ActivityTypeSerializer
    permission_classes = [IsOrgMember]
    pagination_class = None  # bounded reference list


class FactorDatasetViewSet(CachedReferenceListMixin, viewsets.ReadOnlyModelViewSet):
    queryset = EmissionFactorDataset.objects.select_related("region", "imported_by").all()
    serializer_class = EmissionFactorDatasetSerializer
    permission_classes = [IsOrgMember]
    filterset_class = FactorDatasetFilter


class EmissionFactorViewSet(CachedReferenceListMixin, viewsets.ReadOnlyModelViewSet):
    queryset = EmissionFactor.objects.select_related("activity_type", "dataset", "region").all()
    serializer_class = EmissionFactorSerializer
    permission_classes = [IsOrgMember]
    filterset_class = EmissionFactorFilter


# --- Tenant-scoped calculations ---
class EmissionCalculationViewSet(TenantScopedViewSetMixin, viewsets.ReadOnlyModelViewSet):
    queryset = (
        EmissionCalculation.objects
        .select_related("emission_factor", "activity_type")
        # Phase 6d: __-traversal doesn't respect EmissionRecord.objects'
        # default is_deleted=False filter -- a soft-deleted record's
        # calculations must not appear in this active/working list either.
        .exclude(emission_record__is_deleted=True)
    )
    serializer_class = EmissionCalculationSerializer
    permission_classes = [IsOrgMember]
    filterset_class = CalculationFilter
