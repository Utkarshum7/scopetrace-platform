import uuid
from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
from apps.core.models import Organization, DataSource

class UploadBatch(models.Model):
    """
    Groups entries from a single file upload execution. 
    Serves as the transaction boundary for file ingestion.
    """
    class BatchStatus(models.TextChoices):
        PENDING = "PENDING", "Pending Ingestion"
        PROCESSING = "PROCESSING", "Processing Ingestion"
        COMPLETED = "COMPLETED", "Completed Successfully"
        FAILED = "FAILED", "Failed Ingestion"

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
