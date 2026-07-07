from datetime import timedelta

from django.db.models import DurationField, ExpressionWrapper, F
from django.utils import timezone
from rest_framework import serializers

from apps.core.models import Organization, DataSource
from apps.ingestion.models import UploadBatch, EmissionRecord, EmissionRecordVersion


class OrganizationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = ["id", "name"]


class DataSourceSerializer(serializers.ModelSerializer):
    class Meta:
        model = DataSource
        fields = ["id", "name", "source_type"]


class BatchProgressFieldsMixin(serializers.Serializer):
    """Shared, cheap-to-compute job-lifecycle fields (Phase 5c). No stored
    duplication of anything derivable from total_rows/failed_rows/status/
    started_at/finished_at — computed at read time so there's nothing to
    drift out of sync.

    Deliberately does NOT include `estimated_completion_time` — that field
    runs a historical-average query per object (see BatchProgressSerializer
    below) and would be an N+1 query risk if mixed into a list-serving
    serializer like UploadBatchSerializer. It's reserved for the dedicated,
    always-single-object /progress/ endpoint.
    """

    successful_records = serializers.SerializerMethodField()
    processed_records = serializers.SerializerMethodField()
    progress_percentage = serializers.SerializerMethodField()
    duration_seconds = serializers.SerializerMethodField()

    def get_successful_records(self, obj):
        return obj.total_rows - obj.failed_rows

    def get_processed_records(self, obj):
        # Coarse-grained by design (see docs/JOB_LIFECYCLE.md): ingestion runs
        # as one atomic transaction, so there is no meaningful "N of M rows
        # processed so far" signal to report mid-PROCESSING — only a 0 -> all
        # jump at the point the transaction commits.
        if obj.status in (UploadBatch.BatchStatus.COMPLETED, UploadBatch.BatchStatus.PARTIALLY_COMPLETED):
            return obj.total_rows
        return 0

    def get_progress_percentage(self, obj):
        if obj.status in (UploadBatch.BatchStatus.COMPLETED, UploadBatch.BatchStatus.PARTIALLY_COMPLETED):
            return 100
        # FAILED reports 0, not 100 — the transaction rolled back, nothing
        # was durably committed, so anything but 0 would misrepresent it as
        # having finished successfully.
        return 0

    def get_duration_seconds(self, obj):
        if not obj.started_at:
            return None
        # When still PROCESSING (finished_at not set yet), this reports
        # elapsed time so far, not a final duration — useful for a live-
        # updating "time elapsed" display while polling.
        end = obj.finished_at or timezone.now()
        return (end - obj.started_at).total_seconds()


class UploadBatchSerializer(BatchProgressFieldsMixin, serializers.ModelSerializer):
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
            "successful_records",
            "processed_records",
            "progress_percentage",
            "started_at",
            "finished_at",
            "duration_seconds",
            "worker_id",
            "retry_count",
            # Phase 5d — chained ingest/calculate. Note: this is the BATCH's
            # own calculation-stage status (NOT_STARTED/CALCULATING/
            # CALCULATED/CALCULATION_FAILED); EmissionRecordSerializer has an
            # unrelated field of the same name for a RECORD's CO2e
            # resolution status (CALCULATED/UNRESOLVED_.../EXCLUDED_FAILED)
            # — same name, different level, different endpoint.
            "calculation_status",
            "workflow_id",
            "pipeline_version",
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
            "started_at",
            "finished_at",
            "worker_id",
            "retry_count",
            "calculation_status",
            "workflow_id",
            "pipeline_version",
            "uploaded_by",
            "error_message",
            "created_at",
            "updated_at",
        ]


class BatchProgressSerializer(BatchProgressFieldsMixin, serializers.ModelSerializer):
    """Lean, job-lifecycle-focused payload for the polling endpoint
    (GET /api/batches/{id}/progress/) — deliberately self-contained JSON, no
    HTTP-polling-specific shape, so a future WebSocket/SSE channel could push
    this exact same payload without the frontend contract changing (Phase 5c
    requirement #3). Always single-object — never used for a list — so it's
    safe to include the historical-average estimated_completion_time query
    here (see BatchProgressFieldsMixin's docstring for why that field is
    excluded from UploadBatchSerializer).
    """

    estimated_completion_time = serializers.SerializerMethodField()

    class Meta:
        model = UploadBatch
        fields = [
            "id",
            "status",
            "total_rows",
            "failed_rows",
            "successful_records",
            "processed_records",
            "progress_percentage",
            "estimated_completion_time",
            "started_at",
            "finished_at",
            "duration_seconds",
            "worker_id",
            "retry_count",
            "calculation_status",
            "workflow_id",
            "pipeline_version",
            "error_message",
            "parse_errors",
        ]
        read_only_fields = fields

    def get_estimated_completion_time(self, obj):
        # Only meaningful for a job that is actually running right now.
        if obj.status != UploadBatch.BatchStatus.PROCESSING or not obj.started_at:
            return None

        # Practical heuristic (Phase 5c requirement #3's "if practical"
        # qualifier) — NOT a predictor: average duration of the last 5
        # completed batches for the same DataSource, applied to this batch's
        # started_at. Returns None with no historical data to estimate from.
        historical = (
            UploadBatch.objects.filter(
                data_source_id=obj.data_source_id,
                status__in=[
                    UploadBatch.BatchStatus.COMPLETED,
                    UploadBatch.BatchStatus.PARTIALLY_COMPLETED,
                ],
                started_at__isnull=False,
                finished_at__isnull=False,
            )
            .exclude(pk=obj.pk)
            .annotate(
                duration=ExpressionWrapper(
                    F("finished_at") - F("started_at"), output_field=DurationField()
                )
            )
            .order_by("-finished_at")[:5]
        )
        durations = [h.duration for h in historical]
        if not durations:
            return None
        average_duration = sum(durations, timedelta()) / len(durations)
        return obj.started_at + average_duration


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
            # Phase 6d — soft-delete state. Read-only: mutated only via
            # DELETE /api/records/{id}/ and .../restore/, never directly.
            "is_deleted",
            "deleted_at",
            # carbon engine (read-only, from current calculation)
            "co2e_kg",
            "co2e_tonnes",
            "calculation_status",
            "factor_provenance",
            "calculation_trace",
        ]
        read_only_fields = ["is_deleted", "deleted_at"]

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


# Phase 6f: bounds how much free text a client can push into a single
# workflow transition's reason -- AuditTrail.reason/EmissionRecordVersion.
# reason are TextFields (unbounded at the DB layer, deliberately, for
# flexibility), but nothing legitimate needs an arbitrarily large
# justification string, and an unbounded client-supplied string accepted
# straight into the hash-chained audit ledger is a needless storage/CPU
# (hashing scales with payload size) abuse vector.
REASON_MAX_LENGTH = 1000


class WorkflowActionSerializer(serializers.Serializer):
    """Phase 6c. Shared by submit/approve -- an optional free-text reason
    for the transition, threaded into both AuditTrail and
    EmissionRecordVersion."""
    reason = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=REASON_MAX_LENGTH,
        help_text="Reason/justification for this workflow transition",
    )


class RejectionSerializer(serializers.Serializer):
    """Phase 6c. Rejection requires a reason -- unlike submit/approve, a
    rejection with no stated justification is poor audit hygiene and
    leaves the submitter with nothing actionable to correct."""
    reason = serializers.CharField(
        required=True,
        allow_blank=False,
        max_length=REASON_MAX_LENGTH,
        help_text="Reason the record is being rejected (required)",
    )


class DeletionSerializer(serializers.Serializer):
    """Phase 6d. Deletion requires a reason -- same justification as
    RejectionSerializer's (structurally identical, but kept separate for
    clarity: rejecting and deleting are different actions with different
    consequences, even though both need "why" recorded)."""
    reason = serializers.CharField(
        required=True,
        allow_blank=False,
        max_length=REASON_MAX_LENGTH,
        help_text="Reason the record is being deleted (required)",
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


class EmissionRecordVersionSerializer(serializers.ModelSerializer):
    """Phase 6b. Mirrors EmissionRecordSerializer's field naming for the
    business-state fields (same names as the live record) so a client can
    diff the two payloads directly without a field-name mapping layer."""
    co2e_kg = serializers.SerializerMethodField()
    co2e_tonnes = serializers.SerializerMethodField()
    resolution_status = serializers.SerializerMethodField()

    class Meta:
        model = EmissionRecordVersion
        fields = [
            "id", "version_number", "status", "is_suspicious", "scope_category",
            "normalized_value", "normalized_unit", "approved_by", "approved_at",
            "validation_errors", "raw_data_payload", "co2e_kg", "co2e_tonnes",
            "resolution_status", "created_at", "created_by", "reason",
        ]

    def get_co2e_kg(self, obj):
        return str(obj.calculation.co2e_kg) if obj.calculation else None

    def get_co2e_tonnes(self, obj):
        return str(obj.calculation.co2e_tonnes) if obj.calculation else None

    def get_resolution_status(self, obj):
        return obj.calculation.resolution_status if obj.calculation else None
