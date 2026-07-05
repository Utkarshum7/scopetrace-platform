from rest_framework import serializers
from apps.core.models import Organization, DataSource
from apps.ingestion.models import UploadBatch, EmissionRecord


class OrganizationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = ["id", "name"]


class DataSourceSerializer(serializers.ModelSerializer):
    class Meta:
        model = DataSource
        fields = ["id", "name", "source_type"]


class UploadBatchSerializer(serializers.ModelSerializer):
    data_source_details = DataSourceSerializer(source="data_source", read_only=True)

    class Meta:
        model = UploadBatch
        fields = [
            "id",
            "organization",
            "data_source",
            "data_source_details",
            "file_name",
            "status",
            "total_rows",
            "failed_rows",
            "parse_errors",
            "uploaded_by",
            "error_message",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "organization",
            "status",
            "total_rows",
            "failed_rows",
            "parse_errors",
            "uploaded_by",
            "error_message",
            "created_at",
            "updated_at",
        ]


class EmissionRecordSerializer(serializers.ModelSerializer):
    # CO2e is sourced from the record's CURRENT EmissionCalculation (carbon
    # engine). Reads a prefetched `current_calcs` attribute — no per-row query.
    co2e_kg = serializers.SerializerMethodField()
    co2e_tonnes = serializers.SerializerMethodField()
    calculation_status = serializers.SerializerMethodField()
    factor_provenance = serializers.SerializerMethodField()
    calculation_trace = serializers.SerializerMethodField()

    class Meta:
        model = EmissionRecord
        fields = [
            "id",
            "organization",
            "batch",
            "row_index",
            "raw_data_payload",
            "status",
            "is_suspicious",
            "validation_errors",
            "normalized_value",
            "normalized_unit",
            "scope_category",
            "approved_by",
            "approved_at",
            "created_at",
            "updated_at",
            # carbon engine (read-only, from current calculation)
            "co2e_kg",
            "co2e_tonnes",
            "calculation_status",
            "factor_provenance",
            "calculation_trace",
        ]

    @staticmethod
    def _calc(obj):
        calcs = getattr(obj, "current_calcs", None)
        return calcs[0] if calcs else None

    def get_co2e_kg(self, obj):
        c = self._calc(obj)
        return str(c.co2e_kg) if c and c.co2e_kg is not None else None

    def get_co2e_tonnes(self, obj):
        c = self._calc(obj)
        return str(c.co2e_tonnes) if c and c.co2e_tonnes is not None else None

    def get_calculation_status(self, obj):
        c = self._calc(obj)
        return c.resolution_status if c else None

    def get_calculation_trace(self, obj):
        c = self._calc(obj)
        return c.calculation_trace if c else None

    def get_factor_provenance(self, obj):
        c = self._calc(obj)
        if not c or not c.factor_publisher:
            return None
        return {
            "publisher": c.factor_publisher,
            "version": c.factor_version,
            "factor_value": str(c.factor_value) if c.factor_value is not None else None,
            "factor_unit": c.factor_unit,
        }


class ApprovalSerializer(serializers.Serializer):
    reason = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Reason/justification for approving this record",
    )


# Maximum accepted upload size. ESG source exports (SAP/utility/travel) are
# small structured files; anything larger is almost certainly a mistake.
MAX_UPLOAD_SIZE_MB = 10


class UploadInputSerializer(serializers.Serializer):
    file = serializers.FileField(required=True, help_text="The source data file to upload")
    data_source = serializers.PrimaryKeyRelatedField(
        queryset=DataSource.objects.all(),
        required=True,
        help_text="The DataSource object associated with this upload",
    )

    def validate_file(self, value):
        if value.size == 0:
            raise serializers.ValidationError("The uploaded file is empty.")
        max_bytes = MAX_UPLOAD_SIZE_MB * 1024 * 1024
        if value.size > max_bytes:
            raise serializers.ValidationError(
                f"File exceeds the maximum allowed size of {MAX_UPLOAD_SIZE_MB} MB."
            )
        return value
