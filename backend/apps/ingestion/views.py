import logging
from celery import chain
from rest_framework import status, viewsets
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.exceptions import APIException, NotFound
from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.decorators import action

from apps.core.models import DataSource, Organization
from apps.core.storage import get_storage_service
from apps.ingestion.models import UploadBatch, EmissionRecord, EmissionRecordVersion
from apps.audit.services import append_entry
from apps.ingestion.services.versioning import create_version_for_calculation_change
from apps.ingestion.services.workflow import (
    InvalidTransitionError,
    available_actions,
    transition_record,
)
from apps.ingestion.serializers import (
    UploadBatchSerializer,
    BatchProgressSerializer,
    EmissionRecordSerializer,
    EmissionRecordVersionSerializer,
    WorkflowActionSerializer,
    RejectionSerializer,
    UploadInputSerializer,
    OrganizationSerializer,
    DataSourceSerializer,
)
from apps.ingestion.tasks import ingest_task
from apps.carbon.tasks import calculate_task
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
    Upload intake endpoint (async since Phase 5b; chained ingest/calculate
    since Phase 5d).

    Accepts the file, durably persists it via StorageService, creates the
    UploadBatch immediately in PENDING, and enqueues
    chain(ingest_task, calculate_task) — returning 202 Accepted with the
    batch id right away. Ingestion and calculation run as two separate,
    independently-retryable Celery tasks off the request thread; poll
    GET /api/batches/{id}/ (or the lean /progress/ endpoint) for status,
    calculation_status, total_rows, failed_rows, parse_errors.
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
            batch.error_message = (
                f"Failed to persist upload to durable storage: {type(exc).__name__}: {exc}"
            )
            batch.finished_at = timezone.now()
            batch.save(update_fields=["status", "error_message", "finished_at"])
            return Response(
                {
                    "error": "Upload storage unavailable",
                    "detail": str(exc),
                    # The batch record already exists (created PENDING above)
                    # even though the file never reached durable storage —
                    # a client should always have an id to look up, per the
                    # comment on batch creation above.
                    "batch_id": batch.id,
                    "status": batch.status,
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        try:
            # workflow_id is set once at batch creation (UploadBatch.
            # workflow_id's default) and threaded unchanged through both
            # tasks — a stable identifier for the whole chain's execution,
            # independent of each task's own (different) Celery task id.
            # Not relying solely on Celery task IDs, per the requirement.
            #
            # batch_id/workflow_id are passed as KEYWORD arguments (not
            # positional) specifically so apps.tasks.signals's task_failure
            # handler (Phase 5e) can reliably extract them via kwargs.get(...)
            # for the dead-letter log, regardless of which task in the chain
            # failed — the two tasks have different positional signatures
            # (ingest_task also takes storage_key), so positional extraction
            # would need per-task-name special-casing in a generic signal
            # handler; a shared keyword convention avoids that entirely.
            workflow_id = str(batch.workflow_id)
            result = chain(
                ingest_task.si(batch_id=str(batch.id), storage_key=storage_key, workflow_id=workflow_id),
                calculate_task.si(batch_id=str(batch.id), workflow_id=workflow_id),
            ).delay()
            batch.refresh_from_db()
            if batch.status == UploadBatch.BatchStatus.PENDING:
                # Still PENDING after .delay() returned means real async
                # dispatch happened and the chain genuinely hasn't started
                # yet (sitting in the broker) — record that and remember
                # ingest_task's id (future cancellation hook: the only task
                # actually revoke()-able before it starts). chain(...).delay()
                # returns the AsyncResult for the LAST task (calculate_task);
                # .parent is ingest_task's real, already-queued result.
                # calculate_task overwrites celery_task_id with its own id
                # once the chain reaches it. Under CELERY_TASK_ALWAYS_EAGER
                # (tests, local DEBUG) the whole chain has ALREADY run inside
                # .delay() by this point, so status is already terminal and
                # this branch is skipped — writing QUEUED unconditionally
                # here would otherwise clobber that terminal status right
                # back to a false "still queued".
                batch.status = UploadBatch.BatchStatus.QUEUED
                batch.celery_task_id = result.parent.id if result.parent else result.id
                batch.save(update_fields=["status", "celery_task_id"])
        except Exception:
            # Only reachable when CELERY_TASK_ALWAYS_EAGER is True (tests,
            # local DEBUG) — CELERY_TASK_EAGER_PROPAGATES re-raises whichever
            # task's exception synchronously right here (ingest_task's, or —
            # if ingestion succeeded and calculation crashed —
            # calculate_task's). In real async dispatch .delay() never raises
            # for a downstream task failure. Either way, the failing stage
            # already recorded it on the batch itself before re-raising, so
            # the refreshed batch below already reflects it — nothing
            # further to do here.
            logger.exception(
                "chain(ingest_task, calculate_task) raised synchronously (eager mode) for batch %s",
                batch.id,
            )

        # Refresh so the response is honest about current state: under eager
        # mode (tests/local DEBUG) the task has already fully run by this
        # point; under real async dispatch this will show QUEUED (or PENDING,
        # if the try block above hit the except branch).
        batch.refresh_from_db()

        # `batch_id` (not `id`) is the established key here — existing
        # callers (tests, UploadPage.jsx) already depend on it; the new
        # progress fields are added on top rather than switching to
        # BatchProgressSerializer's shape, which would rename it unnecessarily.
        progress = BatchProgressSerializer(batch).data
        return Response(
            {
                "batch_id": batch.id,
                "file_name": batch.file_name,
                "status": batch.status,
                "total_rows": batch.total_rows,
                "failed_rows": batch.failed_rows,
                "errors": batch.parse_errors,
                "error_message": batch.error_message,
                **{k: v for k, v in progress.items() if k not in ("id", "error_message", "parse_errors")},
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

    @action(detail=True, methods=["GET"])
    def progress(self, request, pk=None):
        """GET /api/batches/{id}/progress/ — the polling endpoint (Phase 5c).

        Lean, job-lifecycle-focused payload (see BatchProgressSerializer) —
        intentionally the same shape a future WebSocket/SSE push would send,
        so migrating the transport later never requires a frontend contract
        change. Reuses this ViewSet's existing tenant scoping/permissions
        (get_object() -> TenantScopedViewSetMixin.get_queryset()) rather than
        introducing a separate, easy-to-forget authorization path.
        """
        batch = self.get_object()
        return Response(BatchProgressSerializer(batch).data)


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
        # Phase 6c: submitting for approval requires the same role that can
        # upload (the preparer decides readiness); approving/rejecting
        # requires an approver role (unchanged from pre-6c); recalculation
        # requires an org-admin role; reads require membership.
        if self.action == "submit":
            return [CanUpload()]
        if self.action in ("approve", "reject"):
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

    def _apply_workflow_transition(self, request, pk, target_status, reason):
        """Phase 6c — shared by submit/approve/reject. Approval logic lives
        here (a thin dispatcher into apps.ingestion.services.workflow), not
        duplicated per action. Preserves the exact lock-fetch ->
        object-permission-check structure the pre-6c approve() action
        established: get_object() is bypassed in favor of a manual
        select_for_update().get(pk=pk) so the row is locked from the very
        start of the check-then-update, then check_object_permissions() is
        called explicitly for tenant isolation (cross-org -> 403, matching
        existing precedent -- see docs/AUTH_RBAC.md)."""
        actor = request.user if request.user.is_authenticated else None
        try:
            with transaction.atomic():
                # Lock the row for the whole check-then-update. Without this,
                # two concurrent transitions could both pass the state check
                # and each write an AuditTrail entry (e.g. double approval).
                try:
                    record = EmissionRecord.objects.select_for_update().get(pk=pk)
                except EmissionRecord.DoesNotExist:
                    return Response(
                        {"detail": "Record not found."},
                        status=status.HTTP_404_NOT_FOUND,
                    )

                self.check_object_permissions(request, record)

                try:
                    transition_record(
                        record=record, target_status=target_status,
                        actor=actor, reason=reason,
                    )
                except InvalidTransitionError as exc:
                    return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

            return Response(EmissionRecordSerializer(record).data, status=status.HTTP_200_OK)

        except APIException:
            # Let DRF exceptions (e.g. PermissionDenied from the object-level
            # check) propagate with their correct status code instead of being
            # flattened into a 400 below.
            raise
        except DjangoValidationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            logger.exception("Workflow transition to %s failed for record %s", target_status, pk)
            return Response(
                {"detail": f"Transition failed: {str(exc)}"}, status=status.HTTP_400_BAD_REQUEST
            )

    @action(detail=True, methods=["POST"], serializer_class=WorkflowActionSerializer)
    def submit(self, request, pk=None):
        """POST /api/records/{id}/submit/ — DRAFT/SUSPICIOUS/VALIDATED -> SUBMITTED."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reason = serializer.validated_data.get("reason", "")
        return self._apply_workflow_transition(
            request, pk, EmissionRecord.RecordStatus.SUBMITTED, reason
        )

    @action(detail=True, methods=["POST"], serializer_class=WorkflowActionSerializer)
    def approve(self, request, pk=None):
        """POST /api/records/{id}/approve/ — SUBMITTED -> APPROVED (audit-locked)."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reason = serializer.validated_data.get("reason", "")
        return self._apply_workflow_transition(
            request, pk, EmissionRecord.RecordStatus.APPROVED, reason
        )

    @action(detail=True, methods=["POST"], serializer_class=RejectionSerializer)
    def reject(self, request, pk=None):
        """POST /api/records/{id}/reject/ — SUBMITTED -> REJECTED (reason required)."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reason = serializer.validated_data["reason"]
        return self._apply_workflow_transition(
            request, pk, EmissionRecord.RecordStatus.REJECTED, reason
        )

    @action(detail=True, methods=["GET"], url_path="workflow")
    def workflow(self, request, pk=None):
        """GET /api/records/{id}/workflow/ — current status + the legally
        available next actions. Read-only, so (unlike submit/approve/
        reject above) this reuses self.get_object() -- tenant scoping +
        IsOrgMember, matching the /versions/ endpoints' precedent -- rather
        than the manual lock-fetch pattern the mutating actions need."""
        record = self.get_object()
        return Response({
            "status": record.status,
            "available_transitions": sorted(available_actions(record.status)),
        })

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
            # Phase 6b: recalculation changes WHICH EmissionCalculation is
            # current for this record without touching any of the record's
            # OWN fields — EmissionRecord.save()'s field-diff version trigger
            # would never fire for this, so it's created explicitly here
            # instead (see create_version_for_calculation_change's own
            # docstring for why this needs its own entry point).
            version = create_version_for_calculation_change(
                record=record, changed_by=changed_by, reason="Manual recalculation",
            )
            # See the approve() action's comment above — same append_entry()
            # requirement, same reasoning.
            changes = {"co2e_kg": str(calc.co2e_kg), "status": calc.resolution_status}
            if version is not None:
                changes["record_version"] = version.version_number
            append_entry(
                organization=record.organization,
                record=record,
                action="RECORD_RECALCULATION",
                changed_by=changed_by,
                changes=changes,
                reason="Manual recalculation",
            )

        bump_calc_version(record.organization_id)
        return Response(EmissionCalculationSerializer(calc).data, status=status.HTTP_200_OK)

    # --- Phase 6b: version history -------------------------------------
    # All three reuse self.get_object() — TenantScopedViewSetMixin's scoped
    # queryset + IsOrgMember (this viewset's existing base permission) apply
    # automatically, exactly like every other detail action here. No new
    # tenant-isolation code: this is the same mechanism already proven for
    # GET /api/records/{id}/ itself. RBAC is deliberately NOT narrower than
    # that endpoint — it already exposes calculation_trace/factor_provenance
    # to any org member, so restricting version history specifically would
    # be an inconsistent asymmetry, not tighter security.

    @action(detail=True, methods=["GET"], url_path="versions")
    def versions(self, request, pk=None):
        """GET /api/records/{id}/versions/ — full history, newest first."""
        record = self.get_object()
        qs = record.versions.all()  # already ordered -version_number (Meta)
        return Response(EmissionRecordVersionSerializer(qs, many=True).data)

    @action(detail=True, methods=["GET"], url_path=r"versions/(?P<version_number>\d+)")
    def version_detail(self, request, pk=None, version_number=None):
        """GET /api/records/{id}/versions/{n}/ — one historical snapshot."""
        record = self.get_object()
        version = self._get_version_or_404(record, version_number)
        return Response(EmissionRecordVersionSerializer(version).data)

    @action(detail=True, methods=["GET"], url_path=r"versions/(?P<version_number>\d+)/compare")
    def version_compare(self, request, pk=None, version_number=None):
        """GET /api/records/{id}/versions/{n}/compare/ — field-by-field diff
        between historical version n and the record's CURRENT live state."""
        record = self.get_object()
        version = self._get_version_or_404(record, version_number)

        current_data = EmissionRecordSerializer(record).data
        version_data = EmissionRecordVersionSerializer(version).data
        # Only compare fields both serializers actually share — id/timestamps
        # are deliberately excluded (never meaningful to "diff").
        comparable_fields = (
            "status", "is_suspicious", "scope_category", "normalized_value",
            "normalized_unit", "approved_by", "approved_at", "co2e_kg",
            "co2e_tonnes",
        )
        diff = {
            field: {"version": version_data.get(field), "current": current_data.get(field)}
            for field in comparable_fields
            if version_data.get(field) != current_data.get(field)
        }
        return Response({
            "version_number": version.version_number,
            "is_current_state": not diff,
            "diff": diff,
        })

    @staticmethod
    def _get_version_or_404(record, version_number):
        try:
            return record.versions.get(version_number=version_number)
        except EmissionRecordVersion.DoesNotExist:
            raise NotFound(f"No version {version_number} for this record.")


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
