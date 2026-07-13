from rest_framework import serializers

from apps.carbon.models import (
    ActivityType,
    EmissionCalculation,
    EmissionFactor,
    EmissionFactorDataset,
)


class ActivityTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ActivityType
        fields = ["id", "code", "name", "default_scope", "base_unit", "description"]


class EmissionFactorDatasetSerializer(serializers.ModelSerializer):
    region_code = serializers.CharField(source="region.code", read_only=True, default=None)
    imported_by = serializers.CharField(source="imported_by.username", read_only=True, default=None)

    class Meta:
        model = EmissionFactorDataset
        fields = [
            "id", "publisher", "name", "version", "region_code", "status",
            "valid_from", "valid_to", "priority",
            # provenance
            "publication_date", "import_timestamp", "checksum",
            "source_filename", "source_url", "imported_by", "import_notes",
        ]


class EmissionFactorSerializer(serializers.ModelSerializer):
    activity_type_code = serializers.CharField(source="activity_type.code", read_only=True)
    publisher = serializers.CharField(source="dataset.publisher", read_only=True)
    version = serializers.CharField(source="dataset.version", read_only=True)
    region_code = serializers.CharField(source="region.code", read_only=True, default=None)

    class Meta:
        model = EmissionFactor
        fields = [
            "id", "activity_type_code", "unit", "co2e_per_unit",
            "publisher", "version", "region_code",
            "valid_from", "valid_to", "methodology", "source_ref",
        ]


class EmissionCalculationSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmissionCalculation
        fields = [
            "id", "emission_record", "is_current", "resolution_status",
            "co2e_kg", "co2e_tonnes",
            "factor_publisher", "factor_version", "factor_value", "factor_unit",
            "activity_quantity", "activity_unit",
            "calculation_trace", "engine_version", "calculated_at",
        ]
