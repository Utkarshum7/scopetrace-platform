"""
Backfill EmissionCalculations for existing EmissionRecords.

- Default: computes a calculation for every record that has no current
  calculation (idempotent; first-time CO2e for legacy records, including
  already-APPROVED ones — the record is never mutated, only the separate
  calculation table is written, so the audit lock is respected).
- --force: recalculates records that already have a calculation, superseding the
  previous one. APPROVED records are FROZEN and skipped (their calculation is
  pinned to the factor version used at approval).

Processes per-organization with preloaded resources and chunked bulk writes so a
1M-record backfill performs no per-row resolution query.
"""
from datetime import datetime

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.carbon.models import EmissionCalculation
from apps.carbon.services.carbon_service import CarbonCalculationService
from apps.carbon.services.pipeline import ActivityInput
from apps.core.models import Organization
from apps.ingestion.models import EmissionRecord

_DATE_KEYS = {
    "buchungsdatum", "posting_date", "travel_date", "billing period start",
    "billing_period_start", "period_start", "start date", "date",
}
_DATE_FORMATS = ["%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y"]
_MATCH_KEYS = ("material", "material_code", "travel_mode", "mode")


def _extract_date(payload):
    for key, value in (payload or {}).items():
        if str(key).strip().lower() in _DATE_KEYS and value:
            for fmt in _DATE_FORMATS:
                try:
                    return datetime.strptime(str(value).strip(), fmt).date()
                except ValueError:
                    continue
    return None


def _match_keys(payload):
    keys = []
    for key, value in (payload or {}).items():
        if str(key).strip().lower() in _MATCH_KEYS and value:
            keys.append(str(value))
    return keys


def _chunks(iterable, size):
    chunk = []
    for item in iterable:
        chunk.append(item)
        if len(chunk) >= size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


class Command(BaseCommand):
    help = "Backfill (or --force recalculate) EmissionCalculations for existing records."

    def add_arguments(self, parser):
        parser.add_argument("--organization", default="", help="Limit to one organization id")
        parser.add_argument("--force", action="store_true", help="Recalculate existing (skips APPROVED)")
        parser.add_argument("--batch-size", type=int, default=1000)

    def handle(self, *args, **o):
        service = CarbonCalculationService()
        batch_size = o["batch_size"]

        base = EmissionRecord.objects.select_related("batch__data_source")
        if o["organization"]:
            base = base.filter(organization_id=o["organization"])
        if o["force"]:
            # Freeze approved records — never recompute a locked calculation.
            base = base.exclude(status=EmissionRecord.RecordStatus.APPROVED)
        else:
            base = base.exclude(calculations__is_current=True)

        org_ids = list(base.values_list("organization_id", flat=True).distinct())
        total = 0
        for org_id in org_ids:
            organization = Organization.objects.get(pk=org_id)
            resources = service.build_resources(organization)
            records = base.filter(organization_id=org_id).iterator(chunk_size=batch_size)
            for chunk in _chunks(records, batch_size):
                inputs = [self._to_input(r) for r in chunk]
                calcs = [
                    service.to_calculation(service.calculate_one(i, resources), organization)
                    for i in inputs
                ]
                with transaction.atomic():
                    if o["force"]:
                        EmissionCalculation.objects.filter(
                            emission_record__in=[r.id for r in chunk], is_current=True
                        ).update(is_current=False)
                    EmissionCalculation.objects.bulk_create(calcs)
                total += len(calcs)

        self.stdout.write(self.style.SUCCESS(f"Backfilled {total} calculation(s)."))

    @staticmethod
    def _to_input(record):
        payload = record.raw_data_payload or {}
        source_type = record.batch.data_source.source_type
        return ActivityInput(
            record_id=record.id,
            organization_id=record.organization_id,
            source_type=source_type,
            quantity=record.normalized_value if record.normalized_value is not None else 0,
            unit=record.normalized_unit or "",
            scope=record.scope_category or "",
            match_keys=_match_keys(payload),
            activity_date=_extract_date(payload),
            status=record.status,
        )
