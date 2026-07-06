import uuid
from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
from apps.core.models import Organization, DataSource

class UploadBatch(models.Model):
    """
    Groups entries from a single file upload execution.
    Serves as the transaction boundary for file ingestion, and (Phase 5c) is
    the source of truth for the async job's lifecycle.

    State machine — every transition documented (see docs/JOB_LIFECYCLE.md
    for the full design, including the coarse-grained-progress and
    inert-CANCELLED trade-offs):

        (create)              -> PENDING
        PENDING                -> FAILED       StorageService.save() raised;
                                                the upload never reaches the
                                                queue at all.
        PENDING                -> QUEUED       File durably staged, the
                                                Celery task was enqueued, and
                                                (checked via a DB re-read,
                                                not assumed) it has not
                                                already run — i.e. real async
                                                dispatch, not eager mode.
        {PENDING,QUEUED,
         PROCESSING}           -> PROCESSING   The task begins executing.
                                                Deliberately NOT gated on
                                                "incoming status == QUEUED":
                                                under CELERY_TASK_ALWAYS_EAGER
                                                (tests/local DEBUG) the task
                                                runs synchronously inside
                                                .delay(), before the view can
                                                ever write QUEUED, so it must
                                                still see PENDING. A batch
                                                stuck in PROCESSING from a
                                                crashed worker is also
                                                legitimately reprocessed here
                                                (acks_late redelivery).
        PROCESSING              -> COMPLETED   Pipeline finished with
                                                failed_rows == 0.
        PROCESSING              -> PARTIALLY_COMPLETED
                                                Pipeline finished with
                                                failed_rows > 0 (even 100%
                                                failed) — the JOB completed;
                                                this is distinct from a
                                                pipeline crash.
        PROCESSING              -> FAILED      Unhandled exception during
                                                parsing/validation/
                                                persistence.
        {COMPLETED,PARTIALLY_COMPLETED,
         FAILED,CANCELLED}     -> (terminal)   A redelivered task (Celery's
                                                acks_late) is skipped, never
                                                reprocessed — see
                                                TERMINAL_STATUSES below.
        {QUEUED,PROCESSING}    -> CANCELLED    NOT IMPLEMENTED this phase —
                                                declared in the enum and in
                                                TERMINAL_STATUSES (so future
                                                cancellation logic needs no
                                                migration), same "reserved,
                                                inert" pattern as the carbon
                                                engine's AIRecommendationStage
                                                (Phase 3). No cancel endpoint
                                                or task-revocation exists yet.
    """
    class BatchStatus(models.TextChoices):
        PENDING = "PENDING", "Pending Ingestion"
        QUEUED = "QUEUED", "Queued for Processing"
        PROCESSING = "PROCESSING", "Processing Ingestion"
        COMPLETED = "COMPLETED", "Completed Successfully"
        PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED", "Partially Completed (Some Rows Failed)"
        FAILED = "FAILED", "Failed Ingestion"
        CANCELLED = "CANCELLED", "Cancelled"

    # Single source of truth for "this job will never be touched again" —
    # used by the task's idempotency guard, progress-percentage/duration
    # calculations, and tests. CANCELLED is included even though nothing can
    # transition into it yet (see the docstring above).
    TERMINAL_STATUSES = frozenset({
        BatchStatus.COMPLETED,
        BatchStatus.PARTIALLY_COMPLETED,
        BatchStatus.FAILED,
        BatchStatus.CANCELLED,
    })

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="upload_batches",
        help_text="Tenant that owns this batch"
    )
    data_source = models.ForeignKey(
        DataSource,
        on_delete=models.PROTECT,
        related_name="upload_batches",
        help_text="Configured data source template used to parse the file"
    )
    file_name = models.CharField(max_length=255, help_text="Original file name uploaded")
    status = models.CharField(
        max_length=50,
        choices=BatchStatus.choices,
        default=BatchStatus.PENDING
    )
    total_rows = models.IntegerField(default=0, help_text="Total rows in source file")
    failed_rows = models.IntegerField(default=0, help_text="Rows failed during validation/parsing")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Analyst who uploaded this file"
    )
    error_message = models.TextField(
        null=True,
        blank=True,
        help_text="Reason for system parsing error if batch fails"
    )
    parse_errors = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "Structured, row-addressable parser errors: [{row_index, error}]. "
            "Phase 5b: previously returned only in the synchronous upload "
            "response and never persisted — since ingestion now runs off the "
            "request thread, this is the durable record a client polls for."
        ),
    )

    # --- Phase 5c: job observability -----------------------------------
    started_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When processing actually began (set by the task/service, not at batch creation).",
    )
    finished_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the job reached a terminal state (COMPLETED/PARTIALLY_COMPLETED/FAILED).",
    )
    worker_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="Celery worker hostname that processed this batch (self.request.hostname).",
    )
    retry_count = models.IntegerField(
        default=0,
        help_text=(
            "Celery's own self.request.retries for the processing attempt. Captured for real "
            "now (not a placeholder) — always 0 until Phase 5e adds retry policies, at which "
            "point this starts reporting real values with no further schema change."
        ),
    )
    celery_task_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text=(
            "The Celery AsyncResult id captured at enqueue time. Not consumed by anything yet — "
            "this is what a future cancel endpoint would call AsyncResult(id).revoke() on, "
            "captured now so that feature needs no further migration."
        ),
    )
    # duration is deliberately NOT a stored field — it's finished_at minus
    # started_at, trivially computable, and storing a derived value risks
    # drift if either timestamp ever changes. Exposed as a computed
    # `duration_seconds` field in the API (see serializers.py).

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Upload Batch"
        verbose_name_plural = "Upload Batches"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.file_name} ({self.status}) - {self.created_at.strftime('%Y-%m-%d %H:%M')}"


class EmissionRecord(models.Model):
    """
    The normalized row transaction. Contains the source data, validation errors,
    standardized values, and approval status details.
    """
    class RecordStatus(models.TextChoices):
        DRAFT = "DRAFT", "Draft Ingested"
        FAILED = "FAILED", "Failed Validation (Excluded from Calculations)"
        SUSPICIOUS = "SUSPICIOUS", "Suspicious (Needs Review)"
        VALIDATED = "VALIDATED", "Validated (Ready for Approval)"
        APPROVED = "APPROVED", "Approved & Audit Locked"

    class ScopeCategory(models.TextChoices):
        SCOPE_1 = "SCOPE_1", "Scope 1 (Direct)"
        SCOPE_2 = "SCOPE_2", "Scope 2 (Indirect Electricity)"
        SCOPE_3 = "SCOPE_3", "Scope 3 (Other Indirect / Travel)"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="emission_records",
        help_text="Tenant that owns this record"
    )
    batch = models.ForeignKey(
        UploadBatch,
        on_delete=models.CASCADE,
        related_name="records",
        help_text="Source ingestion batch file context"
    )
    row_index = models.IntegerField(help_text="1-indexed row index of raw source file")

    # Lineage / Origin
    raw_data_payload = models.JSONField(
        help_text="Exact raw unparsed input row dictionary"
    )

    # Validation & Auditing Status
    status = models.CharField(
        max_length=50,
        choices=RecordStatus.choices,
        default=RecordStatus.DRAFT
    )
    is_suspicious = models.BooleanField(
        default=False,
        help_text="True if row values fall outside normal baseline ranges"
    )
    validation_errors = models.JSONField(
        default=dict,
        blank=True,
        help_text="Dictionary of validation errors or warnings found during ingestion"
    )

    # Normalized fields
    normalized_value = models.DecimalField(
        max_digits=20,
        decimal_places=6,
        null=True,
        blank=True,
        help_text="Converted value in the target base unit"
    )
    normalized_unit = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        help_text="Standardized unit (e.g. kWh, Liters, Miles)"
    )
    scope_category = models.CharField(
        max_length=20,
        choices=ScopeCategory.choices,
        null=True,
        blank=True,
        help_text="ESG greenhouse gas emissions scope definition"
    )

    # Approval and Immutability Controls
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_records"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Emission Record"
        verbose_name_plural = "Emission Records"
        unique_together = (("batch", "row_index"),)
        ordering = ["batch", "row_index"]

    def clean(self):
        super().clean()
        if self.pk:
            try:
                # Fetch the original database record prior to modifications
                original = EmissionRecord.objects.get(pk=self.pk)
                if original.status == self.RecordStatus.APPROVED:
                    raise ValidationError(
                        "This record has been Approved & Audit Locked. "
                        "No modifications are permitted on locked transaction logs."
                    )
            except EmissionRecord.DoesNotExist:
                # Record is being created for the first time, skip check
                pass

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Record {self.row_index} in batch {self.batch.file_name} ({self.status})"
