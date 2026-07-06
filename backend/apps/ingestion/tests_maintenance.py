"""
Phase 5f — apps.ingestion.tasks.cleanup_stale_batches_task, the periodic
backstop for batches left non-terminal (see docs/RETRY_DLQ.md §4.3 and
docs/SCHEDULED_TASKS.md for the full design).
"""
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.core.models import DataSource, Organization
from apps.ingestion.models import UploadBatch
from apps.ingestion.tasks import cleanup_stale_batches_task


class CleanupStaleBatchesTaskTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Stale Sweep Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL
        )

    def _backdate(self, batch, minutes_ago):
        # auto_now fields are only touched by Model.save(), never by
        # QuerySet.update() — so this reliably backdates updated_at to
        # simulate "no activity for N minutes" without waiting in real time.
        past = timezone.now() - timezone.timedelta(minutes=minutes_ago)
        UploadBatch.objects.filter(pk=batch.id).update(updated_at=past)

    @override_settings(STALE_BATCH_THRESHOLD_MINUTES=30)
    def test_marks_stale_non_terminal_ingestion_batch_failed(self):
        batch = UploadBatch.objects.create(
            organization=self.org, data_source=self.ds, file_name="x.csv",
            status=UploadBatch.BatchStatus.PROCESSING,
        )
        self._backdate(batch, minutes_ago=45)

        result = cleanup_stale_batches_task()

        batch.refresh_from_db()
        self.assertEqual(batch.status, UploadBatch.BatchStatus.FAILED)
        self.assertIn("stale-batch sweep", batch.error_message)
        self.assertIsNotNone(batch.finished_at)
        self.assertEqual(result, "ingestion=1 calculation=0")

    @override_settings(STALE_BATCH_THRESHOLD_MINUTES=30)
    def test_marks_stale_non_terminal_calculation_batch_failed(self):
        batch = UploadBatch.objects.create(
            organization=self.org, data_source=self.ds, file_name="x.csv",
            status=UploadBatch.BatchStatus.COMPLETED,
            calculation_status=UploadBatch.CalculationStatus.CALCULATING,
        )
        self._backdate(batch, minutes_ago=45)

        result = cleanup_stale_batches_task()

        batch.refresh_from_db()
        # Ingestion axis untouched — this batch's ingestion genuinely
        # succeeded; only the calculation axis is stuck.
        self.assertEqual(batch.status, UploadBatch.BatchStatus.COMPLETED)
        self.assertEqual(batch.calculation_status, UploadBatch.CalculationStatus.CALCULATION_FAILED)
        self.assertIn("stale-batch sweep", batch.error_message)
        self.assertEqual(result, "ingestion=0 calculation=1")

    @override_settings(STALE_BATCH_THRESHOLD_MINUTES=30)
    def test_does_not_touch_recently_updated_non_terminal_batch(self):
        # A batch genuinely still in flight (updated moments ago) must never
        # be swept — this is the false-positive case the threshold guards
        # against.
        batch = UploadBatch.objects.create(
            organization=self.org, data_source=self.ds, file_name="x.csv",
            status=UploadBatch.BatchStatus.PROCESSING,
        )

        result = cleanup_stale_batches_task()

        batch.refresh_from_db()
        self.assertEqual(batch.status, UploadBatch.BatchStatus.PROCESSING)
        self.assertIsNone(batch.error_message)
        self.assertEqual(result, "ingestion=0 calculation=0")

    @override_settings(STALE_BATCH_THRESHOLD_MINUTES=30)
    def test_does_not_touch_already_terminal_batch(self):
        batch = UploadBatch.objects.create(
            organization=self.org, data_source=self.ds, file_name="x.csv",
            status=UploadBatch.BatchStatus.FAILED,
            error_message="Original specific failure reason",
        )
        self._backdate(batch, minutes_ago=999)

        cleanup_stale_batches_task()

        batch.refresh_from_db()
        self.assertEqual(batch.error_message, "Original specific failure reason")

    @override_settings(STALE_BATCH_THRESHOLD_MINUTES=30)
    def test_is_idempotent_when_run_twice(self):
        # Simulates Beat catching up after being down — two back-to-back
        # invocations must not error or double-apply.
        batch = UploadBatch.objects.create(
            organization=self.org, data_source=self.ds, file_name="x.csv",
            status=UploadBatch.BatchStatus.PROCESSING,
        )
        self._backdate(batch, minutes_ago=45)

        first = cleanup_stale_batches_task()
        second = cleanup_stale_batches_task()

        self.assertEqual(first, "ingestion=1 calculation=0")
        self.assertEqual(second, "ingestion=0 calculation=0")
        batch.refresh_from_db()
        self.assertEqual(batch.status, UploadBatch.BatchStatus.FAILED)
