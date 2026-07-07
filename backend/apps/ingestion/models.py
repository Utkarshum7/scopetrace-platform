import uuid
from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
from apps.core.models import Organization, DataSource


def generate_workflow_id():
    # A named, module-level function (not a lambda) — Django's migration
    # serializer can't serialize a lambda as a field default.
    return str(uuid.uuid4())


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

    Phase 5d: `status` above now reflects INGESTION outcome ONLY. Carbon
    calculation is a separate chained task (apps.carbon.tasks.calculate_task)
    with its OWN status axis — see `calculation_status` / `CalculationStatus`
    below and docs/JOB_LIFECYCLE.md. `finished_at` now marks the end of the
    WHOLE chain: set by this batch's ingestion FAILED path (chain-terminating
    — nothing else will run), or by the calculation stage's own completion
    when ingestion succeeded (COMPLETED/PARTIALLY_COMPLETED) and the chain
    continued.
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

    class CalculationStatus(models.TextChoices):
        """Phase 5d: the carbon-calculation chain link's own status, tracked
        independently of `status` (ingestion outcome). A calculate-stage
        crash must be visible without misrepresenting an ingestion that
        already succeeded — that's the whole reason this is a separate axis
        rather than folded into `status`. Owned by
        apps.carbon.services.carbon_service.CarbonCalculationService.
        calculate_for_batch(), called by both the async calculate_task and
        the synchronous IngestionService.ingest() convenience path."""
        NOT_STARTED = "NOT_STARTED", "Not Started"
        CALCULATING = "CALCULATING", "Calculating"
        CALCULATED = "CALCULATED", "Calculated"
        CALCULATION_FAILED = "CALCULATION_FAILED", "Calculation Failed"

    # calculate_task's own idempotency guard checks this — mirrors
    # TERMINAL_STATUSES above but for the calculation axis.
    CALCULATION_TERMINAL_STATUSES = frozenset({
        CalculationStatus.CALCULATED,
        CalculationStatus.CALCULATION_FAILED,
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
        help_text=(
            "When the WHOLE chain finished (Phase 5d): set on ingestion's own FAILED path "
            "(chain-terminating), or by the calculation stage's completion when ingestion "
            "succeeded and the chain continued."
        ),
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
            "The id of whichever Celery task is currently active or about to run for this "
            "batch — the view sets it to ingest_task's id at enqueue, calculate_task "
            "overwrites it with its own id once the chain reaches it. Not consumed by "
            "anything yet — this is what a future cancel endpoint would call "
            "AsyncResult(id).revoke() on, captured now so that feature needs no further "
            "migration."
        ),
    )
    # duration is deliberately NOT a stored field — it's finished_at minus
    # started_at, trivially computable, and storing a derived value risks
    # drift if either timestamp ever changes. Exposed as a computed
    # `duration_seconds` field in the API (see serializers.py).

    # --- Phase 5d: chained ingest -> calculate ---------------------------
    calculation_status = models.CharField(
        max_length=32,
        choices=CalculationStatus.choices,
        default=CalculationStatus.NOT_STARTED,
        help_text=(
            "The carbon-calculation chain link's own status — see CalculationStatus above. "
            "Independent of `status`, which reflects ingestion outcome only."
        ),
    )
    workflow_id = models.CharField(
        max_length=64,
        default=generate_workflow_id,
        editable=False,
        db_index=True,
        help_text=(
            "Stable identifier for this batch's entire processing workflow, threaded "
            "unchanged through every chained task (ingest_task, calculate_task, and any "
            "future chain links) — independent of each task's own Celery task id, which "
            "changes at every hop. Set once at batch creation (CharField, not UUIDField, "
            "so it's format-flexible enough to be adopted directly as an OpenTelemetry "
            "trace id later without a schema change)."
        ),
    )
    pipeline_version = models.CharField(
        max_length=32,
        default="1.0",
        blank=True,
        help_text=(
            "Version label for the shape of the ingest+calculate pipeline that processed "
            "this batch — distinct from EmissionCalculation.engine_version, which versions "
            "the carbon-calculation algorithm specifically. Not read by any branching logic "
            "yet; pure preparation so a future pipeline restructuring (e.g. a third chain "
            "link, chunked processing) can coexist with batches processed under the old "
            "shape without a schema redesign."
        ),
    )

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
        SUBMITTED = "SUBMITTED", "Submitted for Approval"
        APPROVED = "APPROVED", "Approved & Audit Locked"
        REJECTED = "REJECTED", "Rejected (Needs Correction)"

    # Phase 6c — the fixed, non-configurable approval workflow's legal state
    # transitions: {current_status: {legal target statuses}}. FAILED has no
    # entry (implicitly empty set) -- it's an ingestion-time data-quality
    # terminal state, corrected by re-uploading, not by a workflow
    # transition. APPROVED also has no entry -- terminal, already enforced
    # by clean()'s pre-existing audit-lock check below. VALIDATED is folded
    # in alongside DRAFT/SUSPICIOUS even though nothing sets it today, so it
    # already fits the workflow correctly if it's ever wired up later.
    #
    # Enforced HERE (in clean(), not only in
    # apps.ingestion.services.workflow) for the same reason Phase 6b hooked
    # save() itself rather than relying on view call sites alone:
    # EmissionRecordAdmin has no readonly_fields restricting `status`, so a
    # service-only check would miss Admin edits, direct ORM use, and any
    # future call site. The service still owns the action->audit-name
    # mapping and the lock/mutate/save/audit sequencing.
    WORKFLOW_TRANSITIONS = {
        RecordStatus.DRAFT: {RecordStatus.SUBMITTED},
        RecordStatus.SUSPICIOUS: {RecordStatus.SUBMITTED},
        RecordStatus.VALIDATED: {RecordStatus.SUBMITTED},
        RecordStatus.SUBMITTED: {RecordStatus.APPROVED, RecordStatus.REJECTED},
        RecordStatus.REJECTED: {RecordStatus.SUBMITTED},
    }

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
                # Phase 6c — enforce the fixed approval workflow's legal
                # transitions for ANY status change, regardless of call
                # site (service, Admin, shell). Non-status edits (the
                # common case -- normalized_value corrections, etc.) are
                # unaffected: this only fires when status is CHANGING.
                if original.status != self.status:
                    valid_targets = self.WORKFLOW_TRANSITIONS.get(original.status, set())
                    if self.status not in valid_targets:
                        raise ValidationError(
                            f"Invalid workflow transition: cannot move from "
                            f"{original.status} to {self.status}. Valid "
                            f"transitions from {original.status}: "
                            f"{sorted(valid_targets) if valid_targets else 'none (terminal state)'}."
                        )
            except EmissionRecord.DoesNotExist:
                # Record is being created for the first time, skip check
                pass

    def save(self, *args, **kwargs):
        # Phase 6b: snapshot the pre-save state (if this is an update, not a
        # fresh create) under a row lock, BEFORE validating/persisting the
        # new state — apps.ingestion.services.versioning compares the two
        # afterward and creates a new EmissionRecordVersion only if a
        # business field actually changed. Locking this record's own row is
        # sufficient here (unlike Phase 6a's AuditTrail, which needed a
        # separate per-organization counter row because many different
        # records in one org can be approved concurrently) — a version's
        # sequence is scoped to a single record, and nothing else needs to
        # write to that same record concurrently for this save() call to
        # be safe.
        from django.db import transaction

        from apps.ingestion.services.versioning import create_version_if_changed

        with transaction.atomic():
            old = None
            if self.pk:
                old = (
                    EmissionRecord.objects.select_for_update()
                    .filter(pk=self.pk)
                    .first()
                )

            self.full_clean()
            super().save(*args, **kwargs)

            # Stashed on the instance (None if nothing business-meaningful
            # changed) so callers that want to cross-reference the resulting
            # version — e.g. apps.ingestion.views' approve/recalculate
            # actions, into AuditTrail's already-freeform `changes` JSON —
            # can read it right after calling save(), without an extra query.
            self._created_version = create_version_if_changed(
                old_record=old,
                new_record=self,
                changed_by=getattr(self, "_version_changed_by", None),
                reason=getattr(self, "_version_reason", None),
            )

    def __str__(self):
        return f"Record {self.row_index} in batch {self.batch.file_name} ({self.status})"


class EmissionRecordVersionQuerySet(models.QuerySet):
    """Blocks bulk delete/update — the same QuerySet-level gap Phase 6a
    found and fixed for AuditTrail (instance-level delete()/clean()
    overrides don't cover QuerySet.delete()/.update(), which operate at the
    SQL level and bypass model methods entirely)."""

    def delete(self):
        raise ValidationError("Record versions are immutable and cannot be bulk-deleted.")

    def update(self, **kwargs):
        raise ValidationError("Record versions are immutable and cannot be bulk-updated.")


class EmissionRecordVersion(models.Model):
    """
    Phase 6b — an immutable snapshot of an EmissionRecord's full business
    state at one point in its history. Created automatically by
    EmissionRecord.save() (via apps.ingestion.services.versioning) whenever
    a business field actually changes — never edited or deleted afterward.

    Deliberately a SEPARATE model from apps.audit.models.AuditTrail, not a
    replacement for it: AuditTrail is the governance ledger (who did what,
    when, why — hash-chained for tamper-evidence, Phase 6a); this is the
    "what did the record actually look like" reconstruction mechanism. The
    two existing AuditTrail-writing call sites (approve, recalculate)
    reference the version_number this created in their own `changes` JSON —
    a cross-link using AuditTrail's already-freeform field, not a schema
    change to the already-shipped 6a migrations.

    Typed columns mirror EmissionRecord's own shape (not one opaque JSON
    blob) specifically so they're indexable and trivially diffable against
    the live record for the /compare/ endpoint.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    record = models.ForeignKey(
        EmissionRecord,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="versions",
        help_text="The record this is a historical snapshot of.",
    )
    record_uuid_backup = models.UUIDField(
        help_text="Immutable copy of the record ID — survives even if the record itself is ever removed, mirroring AuditTrail's own record_uuid_backup pattern.",
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name="emission_record_versions",
        help_text="Denormalized from record — tenant-scoped queries without a join.",
    )
    version_number = models.PositiveIntegerField(
        help_text="This record's monotonic 1-indexed version position.",
    )

    # --- snapshotted business state (mirrors EmissionRecord's own fields) --
    status = models.CharField(max_length=50)
    is_suspicious = models.BooleanField()
    scope_category = models.CharField(max_length=20, null=True, blank=True)
    normalized_value = models.DecimalField(max_digits=20, decimal_places=6, null=True, blank=True)
    normalized_unit = models.CharField(max_length=50, null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    validation_errors = models.JSONField(default=dict, blank=True)
    raw_data_payload = models.JSONField(
        help_text="Copy of the record's raw source payload at this point (unchanged in practice today, preserved for completeness/future-proofing).",
    )

    # --- calculation reference (not a copy — EmissionCalculation is already
    # immutable/versioned itself; duplicating its fields here would violate
    # the "don't duplicate" principle already established this project) ----
    calculation = models.ForeignKey(
        "carbon.EmissionCalculation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="record_version_references",
        help_text="The EmissionCalculation that was current when this version was snapshotted, if any.",
    )

    # --- provenance of the change itself ------------------------------
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+",
        help_text="Best-effort — set only when the caller threads the acting user through via record._version_changed_by.",
    )
    reason = models.TextField(null=True, blank=True)

    objects = EmissionRecordVersionQuerySet.as_manager()

    class Meta:
        verbose_name = "Emission Record Version"
        verbose_name_plural = "Emission Record Versions"
        ordering = ["-version_number"]
        constraints = [
            models.UniqueConstraint(fields=["record", "version_number"], name="unique_record_version"),
        ]
        indexes = [
            models.Index(fields=["organization", "created_at"]),
        ]

    def clean(self):
        super().clean()
        if self.pk and EmissionRecordVersion.objects.filter(pk=self.pk).exists():
            raise ValidationError("Record versions are immutable and cannot be altered.")

    def delete(self, *args, **kwargs):
        raise ValidationError("Record versions are immutable and cannot be deleted.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Version {self.version_number} of record {self.record_uuid_backup}"
