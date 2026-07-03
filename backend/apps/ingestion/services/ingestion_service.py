import os
import logging
from dataclasses import dataclass
from django.db import transaction
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
        # Prefer the original uploaded filename for lineage/audit. Fall back to
        # the temp file's basename only when the caller does not supply one
        # (e.g. direct service-level usage in tests).
        file_name = original_filename or os.path.basename(file_path)

        # 1. Create batch in PROCESSING status outside transaction
        # to ensure that if ingestion fails completely, the metadata log remains.
        batch = UploadBatch.objects.create(
            organization=data_source.organization,
            data_source=data_source,
            file_name=file_name,
            status=UploadBatch.BatchStatus.PROCESSING,
            uploaded_by=uploaded_by,
        )

        parser_class = self.PARSER_REGISTRY.get(data_source.source_type)
        if not parser_class:
            err_msg = f"No parser registered for source type: {data_source.source_type}"
            batch.status = UploadBatch.BatchStatus.FAILED
            batch.error_message = err_msg
            batch.save(update_fields=["status", "error_message"])
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

                # 5. Bulk create records
                if records_to_create:
                    EmissionRecord.objects.bulk_create(records_to_create)

                # 6. Update batch status to COMPLETED
                total_rows = len(parsed_rows) + len(parse_errors)
                failed_rows = len(parse_errors) + failed_validation_count

                batch.total_rows = total_rows
                batch.failed_rows = failed_rows
                batch.status = UploadBatch.BatchStatus.COMPLETED
                batch.save()

                # Return structured, row-addressable errors so the client can
                # render "Row #N: <message>" instead of opaque strings.
                errors_summary = [
                    {"row_index": err["row_index"], "error": err["error"]}
                    for err in parse_errors
                ]

                return IngestionResult(
                    batch=batch,
                    total_rows=total_rows,
                    failed_rows=failed_rows,
                    suspicious_rows=suspicious_count,
                    errors=errors_summary,
                )

        except Exception as exc:
            # Ingestion transaction rolled back, mark batch status as FAILED
            batch.status = UploadBatch.BatchStatus.FAILED
            batch.error_message = str(exc)
            batch.save(update_fields=["status", "error_message"])
            logger.exception("Ingestion transaction failed for batch %s", batch.id)
            raise exc
