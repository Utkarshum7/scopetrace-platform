from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase

from apps.carbon.models import EmissionCalculation
from apps.core.models import DataSource, Organization
from apps.ingestion.models import EmissionRecord, UploadBatch

RS = EmissionCalculation.ResolutionStatus


class BackfillTests(TestCase):
    def setUp(self):
        call_command("seed_carbon")
        self.org = Organization.objects.create(name="Backfill Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL
        )
        self.batch = UploadBatch.objects.create(
            organization=self.org, data_source=self.ds, file_name="legacy.csv"
        )

    def _record(self, status=EmissionRecord.RecordStatus.DRAFT, qty="1000", row_index=1):
        return EmissionRecord.objects.create(
            organization=self.org, batch=self.batch, row_index=row_index,
            raw_data_payload={"Material": "DSL"},
            status=status, normalized_value=Decimal(qty), normalized_unit="L",
            scope_category="SCOPE_1",
        )

    def test_backfill_creates_calculations(self):
        record = self._record()
        call_command("backfill_calculations")
        calc = EmissionCalculation.objects.get(emission_record=record, is_current=True)
        self.assertEqual(calc.resolution_status, RS.CALCULATED)
        self.assertEqual(calc.co2e_kg, Decimal("2682.050000"))  # 1000 x 2.68205

    def test_backfill_is_idempotent(self):
        self._record()
        call_command("backfill_calculations")
        call_command("backfill_calculations")  # no new current calc
        self.assertEqual(EmissionCalculation.objects.filter(is_current=True).count(), 1)

    def test_force_recalculation_supersedes(self):
        record = self._record()
        call_command("backfill_calculations")
        original = EmissionCalculation.objects.get(emission_record=record, is_current=True)
        call_command("backfill_calculations", force=True)
        # Exactly one current calc; the original is now superseded.
        current = EmissionCalculation.objects.filter(emission_record=record, is_current=True)
        self.assertEqual(current.count(), 1)
        self.assertNotEqual(current.first().id, original.id)
        original.refresh_from_db()
        self.assertFalse(original.is_current)

    def test_approved_record_backfilled_without_mutation(self):
        # First-time CO2e for an already-approved record — must not touch the record.
        record = self._record(status=EmissionRecord.RecordStatus.APPROVED)
        call_command("backfill_calculations")  # should not raise the audit-lock error
        self.assertTrue(EmissionCalculation.objects.filter(emission_record=record).exists())
        record.refresh_from_db()
        self.assertEqual(record.status, EmissionRecord.RecordStatus.APPROVED)

    def test_force_freezes_approved_records(self):
        record = self._record(status=EmissionRecord.RecordStatus.APPROVED)
        call_command("backfill_calculations")
        original = EmissionCalculation.objects.get(emission_record=record, is_current=True)
        call_command("backfill_calculations", force=True)  # APPROVED frozen -> skipped
        current = EmissionCalculation.objects.get(emission_record=record, is_current=True)
        self.assertEqual(current.id, original.id)  # unchanged
