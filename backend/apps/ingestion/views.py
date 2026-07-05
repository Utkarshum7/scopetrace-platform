import logging
from rest_framework import status, viewsets
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.exceptions import APIException
from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.decorators import action

from apps.core.models import DataSource, Organization
from apps.core.storage import get_storage_service
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
from apps.ingestion.tasks import process_upload_batch
from django.db.models import Prefetch

from apps.accounts.mixins import TenantScopedViewSetMixin
from apps.accounts.permissions import CanApprove, CanManageOrgResources, CanUpload, IsOrgMember
from apps.accounts.tenancy import resolve_tenant_context
from apps.carbon.models import EmissionCalculation
from apps.carbon.serializers import EmissionCalculationSerializer
from apps.carbon.services.carbon_service import CarbonCalculationService
from apps.carbon.services.inputs import activity_input_from_record
from apps.carbon.services.metrics_cache import bump_calc_version

logger = logging.getLogger(__name__)


class BaseUploadView(APIView):
    """
    Upload intake endpoint (Phase 5b: asynchronous).

    Accepts the file, durably persists it via StorageService, creates the
    UploadBatch immediately in PENDING, and enqueues process_upload_batch —
    returning 202 Accepted with the batch id right away. The parse/validate/
    normalize/calculate pipeline runs off the request thread; poll
    GET /api/batches/{id}/ for status, total_rows, failed_rows, parse_errors.
    """

    parser_classes = [MultiPartParser, FormParser]
    permission_classes = [CanUpload]
    source_type = None  # Must be set by subclass

    def post(self, request, *args, **kwargs):
        serializer = UploadInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data_source = serializer.validated_data["data_source"]
        uploaded_file = serializer.validated_data["file"]

        # Tenant isolation: the DataSource must belong to the caller's active
        # organization (platform admins may act across orgs).
        ctx = resolve_tenant_context(request)
        if not ctx.is_platform_admin and str(data_source.organization_id) != str(ctx.organization_id):
            return Response(
                {"detail": "The selected DataSource does not belong to your organization."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Verify datasource matches route type
        if data_source.source_type != self.source_type:
            return Response(
                {
                    "error": "Invalid DataSource type",
                    "detail": f"This endpoint requires a DataSource of type {self.source_type}. Got {data_source.source_type}.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        uploaded_by = request.user if request.user.is_authenticated else None

        # The batch is the durable, immediately-visible record of "an upload
        # was received" — created PENDING before the file is even durably
        # staged, so a client always has a batch_id to poll even if storage
        # or the broker is briefly unavailable.
        batch = UploadBatch.objects.create(
            organization=data_source.organization,
            data_source=data_source,
            file_name=uploaded_file.name,
            status=UploadBatch.BatchStatus.PENDING,
            uploaded_by=uploaded_by,
        )

        storage_key = f"uploads/{data_source.organization_id}/{batch.id}/{uploaded_file.name}"
        try:
            storage = get_storage_service()
            storage.save(storage_key, uploaded_file, content_type=uploaded_file.content_type)
        except Exception as exc:
            logger.exception("Failed to persist durable upload for batch %s", batch.id)
            batch.status = UploadBatch.BatchStatus.FAILED
            batch.error_message = f"Failed to persist upload: {exc}"
            batch.save(update_fields=["status", "error_message"])
            return Response(
                {"error": "Upload storage unavailable", "detail": str(exc)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        try:
            process_upload_batch.delay(str(batch.id), storage_key)
        except Exception:
            # Only reachable when CELERY_TASK_ALWAYS_EAGER is True (tests,
            # local DEBUG) — CELERY_TASK_EAGER_PROPAGATES re-raises the
            # task's own exception synchronously right here. In real async
            # dispatch .delay() never raises for a downstream task failure.
            # Either way, IngestionService.ingest_batch already recorded the
            # failure on the batch itself before re-raising, so the refreshed
            # batch below already reflects it — nothing further to do here.
            logger.exception(
                "process_upload_batch raised synchronously (eager mode) for batch %s",
                batch.id,
            )

        # Refresh so the response is honest about current state: under eager
        # mode (tests/local DEBUG) the task has already fully run by this
        # point; under real async dispatch this will still show PENDING.
        batch.refresh_from_db()

        return Response(
            {
                "batch_id": batch.id,
                "file_name": batch.file_name,
                "status": batch.status,
                "total_rows": batch.total_rows,
                "failed_rows": batch.failed_rows,
                "errors": batch.parse_errors,
                "error_message": batch.error_message,
            },
            status=status.HTTP_202_ACCEPTED,
        )


class SAPUploadView(BaseUploadView):
    source_type = DataSource.SourceType.SAP_FUEL


class UtilityUploadView(BaseUploadView):
    source_type = DataSource.SourceType.UTILITY_ELECTRICITY


class TravelUploadView(BaseUploadView):
    source_type = DataSource.SourceType.CORP_TRAVEL


class UploadBatchViewSet(TenantScopedViewSetMixin, viewsets.ReadOnlyModelViewSet):
    """
    Exposes batches list and details for analyst review (scoped to the active org).
    """

    queryset = UploadBatch.objects.all().select_related("data_source", "uploaded_by")
    serializer_class = UploadBatchSerializer
    permission_classes = [IsOrgMember]


class EmissionRecordViewSet(TenantScopedViewSetMixin, viewsets.ReadOnlyModelViewSet):
    """
    Exposes emission records list with advanced filters and approval action.
    All results are scoped to the caller's active organization.
    """

    queryset = (
        EmissionRecord.objects.all()
        .select_related("organization", "batch", "approved_by")
        .prefetch_related(Prefetch(
            "calculations",
            queryset=EmissionCalculation.objects.filter(is_current=True),
            to_attr="current_calcs",
        ))
    )
    serializer_class = EmissionRecordSerializer
    permission_classes = [IsOrgMember]

    def get_permissions(self):
        # Approving a record requires an approver role; recalculation requires an
        # org-admin role; reads require membership.
        if self.action == "approve":
            return [CanApprove()]
        if self.action == "recalculate":
            return [CanManageOrgResources()]
        return [IsOrgMember()]

    def get_queryset(self):
        # Base queryset is already tenant-scoped by TenantScopedViewSetMixin.
        # The previously-trusted `organization` query param has been REMOVED —
        # cross-tenant scoping is enforced server-side, not by the client.
        queryset = super().get_queryset()

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
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reason = serializer.validated_data.get("reason", "")
        approved_by = request.user if request.user.is_authenticated else None

        try:
            with transaction.atomic():
                # Lock the row for the whole check-then-update. Without this,
                # two concurrent approvals could both pass the state checks and
                # each write an AuditTrail entry (double approval).
                try:
                    record = EmissionRecord.objects.select_for_update().get(pk=pk)
                except EmissionRecord.DoesNotExist:
                    return Response(
                        {"detail": "Record not found."},
                        status=status.HTTP_404_NOT_FOUND,
                    )

                # Object-level tenant + role enforcement. get_object() is bypassed
                # here (manual select_for_update), so run the checks explicitly:
                # a caller may only approve records in their own organization.
                self.check_object_permissions(request, record)

                # State validations (now guarded by the row lock)
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

                old_status = record.status
                record.status = EmissionRecord.RecordStatus.APPROVED
                record.approved_by = approved_by
                record.approved_at = timezone.now()
                # save() triggers full_clean() which enforces the audit lock.
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

        except APIException:
            # Let DRF exceptions (e.g. PermissionDenied from the object-level
            # check) propagate with their correct status code instead of being
            # flattened into a 400 below.
            raise
        except DjangoValidationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            logger.exception("Approval failed for record %s", pk)
            return Response(
                {"detail": f"Approval failed: {str(exc)}"}, status=status.HTTP_400_BAD_REQUEST
            )

    @action(detail=True, methods=["POST"])
    def recalculate(self, request, pk=None):
        """Recompute CO2e for a record with the currently-active factors.
        Org-Admin only; APPROVED records are frozen to their pinned factor."""
        try:
            record = (
                EmissionRecord.objects
                .select_related("batch__data_source", "organization")
                .get(pk=pk)
            )
        except EmissionRecord.DoesNotExist:
            return Response({"detail": "Record not found."}, status=status.HTTP_404_NOT_FOUND)

        # Object-level tenant enforcement (get_object is bypassed here).
        self.check_object_permissions(request, record)

        if record.status == EmissionRecord.RecordStatus.APPROVED:
            return Response(
                {"detail": "Approved records are audit-locked to their factor version and cannot be recalculated."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        service = CarbonCalculationService()
        resources = service.build_resources(record.organization)
        context = service.calculate_one(activity_input_from_record(record), resources)
        calc = service.to_calculation(context, record.organization)

        changed_by = request.user if request.user.is_authenticated else None
        with transaction.atomic():
            EmissionCalculation.objects.filter(
                emission_record=record, is_current=True
            ).update(is_current=False)
            calc.save()
            AuditTrail.objects.create(
                organization=record.organization,
                record=record,
                record_uuid_backup=record.id,
                action="RECORD_RECALCULATION",
                changed_by=changed_by,
                changes={"co2e_kg": str(calc.co2e_kg), "status": calc.resolution_status},
                reason="Manual recalculation",
            )

        bump_calc_version(record.organization_id)
        return Response(EmissionCalculationSerializer(calc).data, status=status.HTTP_200_OK)


class OrganizationViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Exposes the organizations the caller belongs to (platform admins see all).
    """
    queryset = Organization.objects.all()
    serializer_class = OrganizationSerializer
    permission_classes = [IsOrgMember]
    pagination_class = None  # bounded selector list

    def get_queryset(self):
        ctx = resolve_tenant_context(self.request)
        if ctx.is_platform_admin:
            if ctx.organization is not None:
                return Organization.objects.filter(id=ctx.organization_id)
            return Organization.objects.all()
        # Regular users: only organizations where they hold an active membership.
        return Organization.objects.filter(
            memberships__user=self.request.user,
            memberships__active=True,
        ).distinct()


class DataSourceViewSet(TenantScopedViewSetMixin, viewsets.ReadOnlyModelViewSet):
    """
    Exposes data sources for upload and filters (scoped to the active org).
    """
    queryset = DataSource.objects.all().select_related("organization")
    serializer_class = DataSourceSerializer
    permission_classes = [IsOrgMember]
    pagination_class = None  # bounded selector list
