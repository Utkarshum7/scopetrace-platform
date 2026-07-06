"""
Phase 5f — apps.carbon.tasks.recalculate_missing_calculations_task, the
daily safety net for EmissionRecords that fell through the normal
ingest -> calculate chain without ever getting a calculation.

Deliberately a thin test: the actual calculation/resolution logic is already
covered exhaustively by test_backfill.py (BackfillTests) — this only proves
the task correctly delegates to backfill_calculations' non-force mode and
doesn't duplicate that coverage.
"""
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase

from apps.carbon.models import EmissionCalculation
from apps.carbon.tasks import recalculate_missing_calculations_task
from apps.core.models import DataSource, Organization
from apps.ingestion.models import EmissionRecord, UploadBatch


class RecalculateMissingCalculationsTaskTests(TestCase):
    def setUp(self):
        call_command("seed_carbon")
        self.org = Organization.objects.create(name="Scheduled Recalc Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL
        )
        self.batch = UploadBatch.objects.create(
            organization=self.org, data_source=self.ds, file_name="legacy.csv"
        )

    def _record(self, status=EmissionRecord.RecordStatus.DRAFT, row_index=1):
        return EmissionRecord.objects.create(
            organization=self.org, batch=self.batch, row_index=row_index,
            raw_data_payload={"Material": "DSL"},
            status=status, normalized_value=Decimal("1000"), normalized_unit="L",
            scope_category="SCOPE_1",
        )

    def test_computes_calculation_for_record_missing_one(self):
        record = self._record()
        self.assertFalse(EmissionCalculation.objects.filter(emission_record=record).exists())

        result = recalculate_missing_calculations_task()

        self.assertTrue(EmissionCalculation.objects.filter(emission_record=record, is_current=True).exists())
        self.assertIn("Backfilled", result)

    def test_never_supersedes_an_existing_calculation(self):
        # --force is never passed by this task — an existing calculation
        # must survive untouched, exactly like backfill_calculations'
        # default (non-force) mode.
        record = self._record()
        call_command("backfill_calculations")
        original = EmissionCalculation.objects.get(emission_record=record, is_current=True)

        recalculate_missing_calculations_task()

        current = EmissionCalculation.objects.get(emission_record=record, is_current=True)
        self.assertEqual(current.id, original.id)

    def test_is_idempotent_when_run_twice(self):
        self._record()

        recalculate_missing_calculations_task()
        recalculate_missing_calculations_task()

        self.assertEqual(EmissionCalculation.objects.filter(is_current=True).count(), 1)
