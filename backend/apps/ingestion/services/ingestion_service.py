import os
import logging
from dataclasses import dataclass
from datetime import date
from django.db import transaction
from django.utils import timezone
from apps.core.models import DataSource
from apps.ingestion.models import UploadBatch, EmissionRecord
from .sap_parser import SAPFuelParser
from .utility_parser import UtilityElectricityParser
from .travel_parser import TravelParser
from .validator import RowValidator
from .normalizer import NormalizationService

logger = logging.getLogger(__name__)


@dataclass
class IngestionResult:
    """
    Structured outcome of the ingestion execution.
    """
    batch: UploadBatch
    total_rows: int
    failed_rows: int
    suspicious_rows: int
    # Structured, row-addressable parse errors: [{"row_index": int, "error": str}]
    errors: list[dict]


class IngestionService:
    """
    Orchestration service running the Strategy Pattern ingestion pipeline.

    Responsibilities:
      - Creates and manages the transactional UploadBatch state.
      - Resolves the appropriate parser adapter based on SourceType.
      - Parses the input file into intermediate ParsedRows.
      - Validates each ParsedRow against semantic business rules.
      - Normalizes valid records into base units using Python Decimal.
      - Bulk inserts records to the database to bypass individual save overhead.
      - Marks the Batch complete or failed cleanly.
    """

    PARSER_REGISTRY = {
        DataSource.SourceType.SAP_FUEL: SAPFuelParser,
        DataSource.SourceType.UTILITY_ELECTRICITY: UtilityElectricityParser,
        DataSource.SourceType.CORP_TRAVEL: TravelParser,
    }

    def ingest(
        self,
        data_source: DataSource,
        file_path: str,
        uploaded_by=None,
        original_filename: str | None = None,
    ) -> IngestionResult:
        """Synchronous entry point: create the batch, then run the pipeline.

        Kept for direct/service-level callers and existing tests. The
        asynchronous path (Phase 5b) creates the batch itself — PENDING,
        before the file is even durably staged — and calls `ingest_batch`
        directly; see apps.ingestion.tasks.process_upload_batch.
        """
        # Prefer the original uploaded filename for lineage/audit. Fall back to
        # the temp file's basename only when the caller does not supply one
        # (e.g. direct service-level usage in tests).
        file_name = original_filename or os.path.basename(file_path)

        # Create batch in PROCESSING status outside transaction
        # to ensure that if ingestion fails completely, the metadata log remains.
        batch = UploadBatch.objects.create(
            organization=data_source.organization,
            data_source=data_source,
            file_name=file_name,
            status=UploadBatch.BatchStatus.PROCESSING,
            uploaded_by=uploaded_by,
        )
        return self.ingest_batch(batch, file_path)

    def ingest_batch(self, batch: UploadBatch, file_path: str) -> IngestionResult:
        """Run the parse -> validate -> normalize -> persist -> calculate
        pipeline against an ALREADY-CREATED batch.

        This is the shared core `ingest()` delegates to. It exists as its own
        method so the asynchronous upload path (Phase 5b) can hand it a batch
        that was created — and durably staged via StorageService — before the
        Celery task ever ran, without duplicating batch-creation logic or
        creating a second orphaned batch.

        Ensures the batch is PROCESSING before doing any work, regardless of
        its incoming status (PENDING under CELERY_TASK_ALWAYS_EAGER, QUEUED
        under real async dispatch, or already PROCESSING — both `ingest()`'s
        synchronous path above and a crash-recovery redelivery land here with
        status already PROCESSING). started_at/worker_id/retry_count may
        already be set on the in-memory `batch` object by the caller (the
        Celery task sets worker_id/retry_count before calling this — see
        apps.ingestion.tasks.process_upload_batch) — this plain .save() (no
        update_fields restriction) persists whatever the caller has already
        populated, not just status/started_at.
        """
        if batch.status != UploadBatch.BatchStatus.PROCESSING:
            batch.status = UploadBatch.BatchStatus.PROCESSING
            batch.started_at = timezone.now()
            batch.save()

        data_source = batch.data_source
        parser_class = self.PARSER_REGISTRY.get(data_source.source_type)
        if not parser_class:
            err_msg = f"No parser registered for source type: {data_source.source_type}"
            batch.status = UploadBatch.BatchStatus.FAILED
            batch.error_message = f"Pipeline configuration error: {err_msg}"
            batch.finished_at = timezone.now()
            batch.save(update_fields=["status", "error_message", "finished_at"])
            raise ValueError(err_msg)

        parser = parser_class()
        validator = RowValidator()
        normalizer = NormalizationService()

        try:
            # We run parsing, validation, normalization, and bulk creation in one transaction.
            with transaction.atomic():
                # 2. Parse the file
                parsed_rows, parse_errors = parser.parse(file_path)

                # 3. Gather quantities for batch median calculation
                batch_quantities = [
                    row.quantity for row in parsed_rows
                    if row.quantity is not None
                ]

                records_to_create = []
                record_row_pairs = []
                failed_validation_count = 0
                suspicious_count = 0

                # 4. Process each parsed row
                for row in parsed_rows:
                    validation_result = validator.validate(row, batch_quantities)

                    is_failed = validation_result.is_failed
                    is_suspicious = validation_result.is_suspicious
                    errors_dict = validation_result.errors.copy()

                    norm_value = None
                    norm_unit = None
                    scope_category = None

                    # Normalize if not failed at validation stage
                    if not is_failed:
                        norm_result = normalizer.normalize(row)
                        if not norm_result.is_success:
                            is_failed = True
                            errors_dict.setdefault("normalization", []).append(
                                norm_result.error or "Normalization failed."
                            )
                        else:
                            norm_value = norm_result.value
                            norm_unit = norm_result.unit
                            scope_category = norm_result.scope_category

                    # Determine status
                    if is_failed:
                        status = EmissionRecord.RecordStatus.FAILED
                        failed_validation_count += 1
                    elif is_suspicious:
                        status = EmissionRecord.RecordStatus.SUSPICIOUS
                        suspicious_count += 1
                    else:
                        status = EmissionRecord.RecordStatus.DRAFT

                    record = EmissionRecord(
                        organization=data_source.organization,
                        batch=batch,
                        row_index=row.row_index,
                        raw_data_payload=row.raw_data,
                        status=status,
                        is_suspicious=is_suspicious,
                        validation_errors=errors_dict,
                        normalized_value=norm_value,
                        normalized_unit=norm_unit,
                        scope_category=scope_category,
                    )
                    records_to_create.append(record)
                    record_row_pairs.append((record, row))

                # 5. Bulk create records
                if records_to_create:
                    EmissionRecord.objects.bulk_create(records_to_create)

                # 5b. Compute carbon (CO2e) for each record in bulk.
                self._calculate_carbon(data_source.organization, record_row_pairs)

                # 6. Update batch status to COMPLETED
                total_rows = len(parsed_rows) + len(parse_errors)
                failed_rows = len(parse_errors) + failed_validation_count

                # Structured, row-addressable errors so a client can render
                # "Row #N: <message>" instead of opaque strings. Persisted on
                # the batch (Phase 5b) — ingestion no longer runs on the
                # request thread, so this can no longer be returned only in a
                # synchronous HTTP response; it must be durable to be
                # discoverable by polling the batch afterwards.
                errors_summary = [
                    {"row_index": err["row_index"], "error": err["error"]}
                    for err in parse_errors
                ]

                batch.total_rows = total_rows
                batch.failed_rows = failed_rows
                batch.parse_errors = errors_summary
                # PARTIALLY_COMPLETED (not COMPLETED) whenever any row
                # failed to parse/validate — even if every row failed. The
                # pipeline itself did not crash; that's the distinction from
                # FAILED below. See the BatchStatus docstring in models.py.
                batch.status = (
                    UploadBatch.BatchStatus.PARTIALLY_COMPLETED
                    if failed_rows > 0
                    else UploadBatch.BatchStatus.COMPLETED
                )
                batch.finished_at = timezone.now()
                batch.save()

                return IngestionResult(
                    batch=batch,
                    total_rows=total_rows,
                    failed_rows=failed_rows,
                    suspicious_rows=suspicious_count,
                    errors=errors_summary,
                )

        except Exception as exc:
            # Ingestion transaction rolled back, mark batch status as FAILED.
            # Exception type + message + stage context — never a bare/generic
            # "processing failed" (Phase 5c requirement #6).
            batch.status = UploadBatch.BatchStatus.FAILED
            batch.error_message = (
                f"Ingestion pipeline failed while parsing/validating/persisting: "
                f"{type(exc).__name__}: {exc}"
            )
            batch.finished_at = timezone.now()
            batch.save(update_fields=["status", "error_message", "finished_at"])
            logger.exception("Ingestion transaction failed for batch %s", batch.id)
            raise exc

    def _calculate_carbon(self, organization, record_row_pairs):
        """
        Compute a CO2e EmissionCalculation for each ingested record (bulk).

        Imported lazily to keep the carbon engine an optional dependency of the
        ingestion pipeline. Unresolved factors do not fail the batch — the
        calculation is stored with an UNRESOLVED status for later review.
        """
        if not record_row_pairs:
            return

        from apps.carbon.models import EmissionCalculation
        from apps.carbon.services.carbon_service import CarbonCalculationService
        from apps.carbon.services.pipeline import ActivityInput

        inputs = []
        for record, row in record_row_pairs:
            activity_date = None
            if row.date:
                try:
                    activity_date = date.fromisoformat(row.date)
                except ValueError:
                    activity_date = None
            inputs.append(ActivityInput(
                record_id=record.id,
                organization_id=organization.id,
                source_type=row.source_type,
                quantity=record.normalized_value if record.normalized_value is not None else 0,
                unit=record.normalized_unit or "",
                scope=record.scope_category or "",
                match_keys=[row.material_or_mode] if row.material_or_mode else [],
                activity_date=activity_date,
                status=record.status,
            ))

        calculations = CarbonCalculationService().build_calculations(inputs, organization)
        EmissionCalculation.objects.bulk_create(calculations)

        # Invalidate this org's cached metrics.
        from apps.carbon.services.metrics_cache import bump_calc_version
        bump_calc_version(organization.id)
