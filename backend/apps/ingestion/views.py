import logging
import os
import tempfile
from rest_framework import status, viewsets
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser
from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.decorators import action

from apps.core.models import DataSource, Organization
from apps.ingestion.models import UploadBatch, EmissionRecord
from apps.audit.models import AuditTrail
from apps.ingestion.serializers import (
    UploadBatchSerializer,
    EmissionRecordSerializer,
    ApprovalSerializer,
    UploadInputSerializer,
    OrganizationSerializer,
    DataSourceSerializer,
)
from apps.ingestion.services.ingestion_service import IngestionService

logger = logging.getLogger(__name__)


class BaseUploadView(APIView):
    """
    Base upload handler. Handles file reception, writing to temp storage,
    running the ingestion pipeline, and cleaning up the temp file.
    """

    parser_classes = [MultiPartParser, FormParser]
    source_type = None  # Must be set by subclass

    def post(self, request, *args, **kwargs):
        serializer = UploadInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data_source = serializer.validated_data["data_source"]
        uploaded_file = serializer.validated_data["file"]

        # Verify datasource matches route type
        if data_source.source_type != self.source_type:
            return Response(
                {
                    "error": "Invalid DataSource type",
                    "detail": f"This endpoint requires a DataSource of type {self.source_type}. Got {data_source.source_type}.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Write uploaded file to a temporary file on disk
        suffix = os.path.splitext(uploaded_file.name)[1]
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            for chunk in uploaded_file.chunks():
                temp_file.write(chunk)
            temp_file_path = temp_file.name

        try:
            # Determine uploaded_by user if authenticated
            uploaded_by = request.user if request.user.is_authenticated else None

            # Execute IngestionService orchestrator. The original filename is
            # passed for accurate lineage (temp_file_path is a random temp name).
            service = IngestionService()
            result = service.ingest(
                data_source,
                temp_file_path,
                uploaded_by=uploaded_by,
                original_filename=uploaded_file.name,
            )

            return Response(
                {
                    "batch_id": result.batch.id,
                    "file_name": result.batch.file_name,
                    "status": result.batch.status,
                    "total_rows": result.total_rows,
                    "failed_rows": result.failed_rows,
                    "suspicious_rows": result.suspicious_rows,
                    "errors": result.errors,
                },
                status=status.HTTP_201_CREATED,
            )
        except Exception as exc:
            # Log the full traceback (previously failures were silent) and return
            # a stable error envelope. Upload failures are treated as bad input.
            logger.exception(
                "Ingestion failed for file '%s' (data_source=%s)",
                uploaded_file.name,
                data_source.id,
            )
            return Response(
                {"error": "Ingestion failed", "detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        finally:
            # Ensure file cleanup
            if os.path.exists(temp_file_path):
                os.remove(temp_file_path)


class SAPUploadView(BaseUploadView):
    source_type = DataSource.SourceType.SAP_FUEL


class UtilityUploadView(BaseUploadView):
    source_type = DataSource.SourceType.UTILITY_ELECTRICITY


class TravelUploadView(BaseUploadView):
    source_type = DataSource.SourceType.CORP_TRAVEL


class UploadBatchViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Exposes batches list and details for analyst review.
    """

    queryset = UploadBatch.objects.all().select_related("data_source", "uploaded_by")
    serializer_class = UploadBatchSerializer


class EmissionRecordViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Exposes emission records list with advanced filters and approval action.
    """

    queryset = EmissionRecord.objects.all().select_related("organization", "batch", "approved_by")
    serializer_class = EmissionRecordSerializer

    def get_queryset(self):
        queryset = super().get_queryset()

        # Filters
        org_id = self.request.query_params.get("organization")
        if org_id:
            queryset = queryset.filter(organization_id=org_id)

        ds_id = self.request.query_params.get("data_source")
        if ds_id:
            queryset = queryset.filter(batch__data_source_id=ds_id)

        batch_id = self.request.query_params.get("batch")
        if batch_id:
            queryset = queryset.filter(batch_id=batch_id)

        suspicious_param = self.request.query_params.get("suspicious")
        if suspicious_param is not None:
            is_suspicious = suspicious_param.lower() in ("true", "1")
            queryset = queryset.filter(is_suspicious=is_suspicious)

        failed_param = self.request.query_params.get("failed")
        if failed_param is not None:
            is_failed = failed_param.lower() in ("true", "1")
            if is_failed:
                queryset = queryset.filter(status=EmissionRecord.RecordStatus.FAILED)
            else:
                queryset = queryset.exclude(status=EmissionRecord.RecordStatus.FAILED)

        status_param = self.request.query_params.get("status")
        if status_param:
            statuses = [s.strip().upper() for s in status_param.split(",")]
            queryset = queryset.filter(status__in=statuses)

        return queryset

    @action(detail=True, methods=["POST"], serializer_class=ApprovalSerializer)
    def approve(self, request, pk=None):
        record = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reason = serializer.validated_data.get("reason", "")

        # State validations
        if record.status == EmissionRecord.RecordStatus.APPROVED:
            return Response(
                {"detail": "This record is already Approved & Audit Locked."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if record.status == EmissionRecord.RecordStatus.FAILED:
            return Response(
                {"detail": "Cannot approve a record that has Failed validation."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            with transaction.atomic():
                old_status = record.status
                approved_by = request.user if request.user.is_authenticated else None
                approved_at = timezone.now()

                # Perform the state update
                record.status = EmissionRecord.RecordStatus.APPROVED
                record.approved_by = approved_by
                record.approved_at = approved_at

                # Save record (triggers full_clean checks for locked database audits)
                record.save()

                # Create append-only AuditTrail entry
                AuditTrail.objects.create(
                    organization=record.organization,
                    record=record,
                    record_uuid_backup=record.id,
                    action="RECORD_APPROVAL",
                    changed_by=approved_by,
                    changes={"status": [old_status, EmissionRecord.RecordStatus.APPROVED]},
                    reason=reason or "Analyst record approval",
                )

            return Response(EmissionRecordSerializer(record).data, status=status.HTTP_200_OK)

        except DjangoValidationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            return Response(
                {"detail": f"Approval failed: {str(exc)}"}, status=status.HTTP_400_BAD_REQUEST
            )


class OrganizationViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Exposes list of organizations for filters.
    """
    queryset = Organization.objects.all()
    serializer_class = OrganizationSerializer


class DataSourceViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Exposes list of data sources for upload and filters.
    """
    queryset = DataSource.objects.all().select_related("organization")
    serializer_class = DataSourceSerializer
