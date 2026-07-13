import django_filters

from apps.carbon.models import (
    EmissionCalculation,
    EmissionFactor,
    EmissionFactorDataset,
)


class FactorDatasetFilter(django_filters.FilterSet):
    publisher = django_filters.CharFilter(field_name="publisher", lookup_expr="iexact")
    status = django_filters.CharFilter(field_name="status", lookup_expr="iexact")

    class Meta:
        model = EmissionFactorDataset
        fields = ["publisher", "status"]


class EmissionFactorFilter(django_filters.FilterSet):
    activity_type = django_filters.CharFilter(field_name="activity_type__code", lookup_expr="iexact")
    region = django_filters.CharFilter(field_name="region__code", lookup_expr="iexact")

    class Meta:
        model = EmissionFactor
        fields = ["activity_type", "region"]


class CalculationFilter(django_filters.FilterSet):
    current = django_filters.BooleanFilter(field_name="is_current")
    status = django_filters.CharFilter(field_name="resolution_status", lookup_expr="iexact")

    class Meta:
        model = EmissionCalculation
        fields = ["current", "status"]
