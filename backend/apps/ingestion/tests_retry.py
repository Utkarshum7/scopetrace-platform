"""
Phase 5e — retry policies, exponential backoff, and the transient_exceptions
mechanism that keeps them from being silently defeated.

Focus: proving the bug found during the pre-implementation review is
actually fixed (a transient exception must NOT mark the batch terminal,
since that would make a subsequent retry's idempotency guard skip it), that
the fix doesn't change the synchronous ingest() path's behavior at all, and
that the retry policies are configured as documented.
"""
from datetime import date
from unittest.mock import patch

from django.db import InterfaceError, OperationalError
from django.test import TestCase

from apps.carbon.services.carbon_service import CarbonCalculationService
from apps.carbon.tasks import CALCULATE_RETRYABLE_EXCEPTIONS, calculate_task
from apps.core.models import DataSource, Organization
from apps.core.storage import get_storage_service
from apps.ingestion.models import EmissionRecord, UploadBatch
from apps.ingestion.services.ingestion_service import IngestionService
from apps.ingestion.tasks import INGEST_RETRYABLE_EXCEPTIONS, ingest_task


def _sap_csv_bytes():
    today = date.today().strftime("%d.%m.%Y")
    return (
        "Werk;Buchungsdatum;Material;Materialkurztext;Menge;Einheit;Nettopreis\n"
        f"DE01;{today};DSL;Diesel;500,00;L;750.00\n"
    ).encode("utf-8")


class RetryPolicyConfigTests(TestCase):
    """Documents and verifies the actual configured policy — if these
    numbers ever drift from what docs/RETRY_DLQ.md claims, this test catches
    it."""

    def test_ingest_task_retry_policy(self):
        t = ingest_task
        self.assertEqual(t.max_retries, 3)
        self.assertEqual(t.retry_backoff, 2)
        self.assertEqual(t.retry_backoff_max, 60)
        self.assertTrue(t.retry_jitter)
        self.assertEqual(set(t.autoretry_for), set(INGEST_RETRYABLE_EXCEPTIONS))

    def test_calculate_task_retry_policy(self):
        t = calculate_task
        self.assertEqual(t.max_retries, 5)
        self.assertEqual(t.retry_backoff, 2)
        self.assertEqual(t.retry_backoff_max, 120)
        self.assertTrue(t.retry_jitter)
        self.assertEqual(set(t.autoretry_for), set(CALCULATE_RETRYABLE_EXCEPTIONS))

    def test_policies_are_independent_objects_not_shared(self):
        # Designed independently per the requirement — not literally the
        # same tuple instance, even though the exception types coincide today.
        self.assertIsNot(INGEST_RETRYABLE_EXCEPTIONS, CALCULATE_RETRYABLE_EXCEPTIONS)


class IngestBatchTransientExceptionTests(TestCase):
    """The core bug fix: a transient exception must not mark the batch
    terminal, or a subsequent retry's idempotency guard would skip it."""

    def setUp(self):
        self.org = Organization.objects.create(name="Retry Ingest Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL
        )

    def _staged_batch(self):
        batch = UploadBatch.objects.create(
            organization=self.org, data_source=self.ds, file_name="sap.csv",
            status=UploadBatch.BatchStatus.PENDING,
        )
        key = f"uploads/{self.org.id}/{batch.id}/sap.csv"
        get_storage_service().save(key, _sap_csv_bytes(), content_type="text/csv")
        return batch, key

    def test_transient_exception_leaves_batch_non_terminal(self):
        batch, key = self._staged_batch()
        temp_path = None
        with patch(
            "apps.ingestion.services.ingestion_service.EmissionRecord.objects.bulk_create",
            side_effect=OperationalError("connection reset"),
        ):
            with self.assertRaises(OperationalError):
                # Direct call to ingest_batch (bypassing the storage-staging
                # step, which isn't the thing under test) with a temp file.
                import shutil
                import tempfile
                with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as tmp:
                    temp_path = tmp.name
                    with get_storage_service().open(key) as src:
                        shutil.copyfileobj(src, tmp)
                IngestionService().ingest_batch(
                    batch, temp_path, transient_exceptions=(OperationalError, InterfaceError)
                )

        batch.refresh_from_db()
        # NOT FAILED — this is the entire point of the fix.
        self.assertNotIn(batch.status, UploadBatch.TERMINAL_STATUSES)
        self.assertEqual(batch.status, UploadBatch.BatchStatus.PROCESSING)
        self.assertIsNone(batch.error_message)

    def test_same_exception_without_transient_exceptions_marks_failed(self):
        # Proves the synchronous ingest() path (which never passes
        # transient_exceptions) is completely unaffected by this fix — same
        # exception, default parameter, exact pre-5e behavior.
        batch, key = self._staged_batch()
        import shutil
        import tempfile
        with patch(
            "apps.ingestion.services.ingestion_service.EmissionRecord.objects.bulk_create",
            side_effect=OperationalError("connection reset"),
        ):
            with self.assertRaises(OperationalError):
                with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as tmp:
                    temp_path = tmp.name
                    with get_storage_service().open(key) as src:
                        shutil.copyfileobj(src, tmp)
                IngestionService().ingest_batch(batch, temp_path)  # no transient_exceptions

        batch.refresh_from_db()
        self.assertEqual(batch.status, UploadBatch.BatchStatus.FAILED)
        self.assertIn("OperationalError", batch.error_message)

    def test_retry_after_transient_failure_succeeds_and_is_not_skipped(self):
        # End-to-end: ingest_task hits a transient failure on attempt 1
        # (simulated), then a manual second call (simulating the retry
        # Celery would schedule) succeeds — and is NOT skipped by the
        # idempotency guard, proving the fix actually unblocks retries
        # rather than just avoiding the FAILED marking in isolation.
        batch, key = self._staged_batch()

        with patch(
            "apps.ingestion.services.ingestion_service.EmissionRecord.objects.bulk_create",
            side_effect=OperationalError("connection reset"),
        ):
            with self.assertRaises(OperationalError):
                ingest_task(batch_id=str(batch.id), storage_key=key, workflow_id="wf-retry-e2e")

        batch.refresh_from_db()
        self.assertEqual(batch.status, UploadBatch.BatchStatus.PROCESSING)

        # The "retry" — same task, no mock this time.
        result = ingest_task(batch_id=str(batch.id), storage_key=key, workflow_id="wf-retry-e2e")

        self.assertEqual(result, "completed")
        batch.refresh_from_db()
        self.assertEqual(batch.status, UploadBatch.BatchStatus.COMPLETED)
        self.assertEqual(EmissionRecord.objects.filter(batch=batch).count(), 1)


class CalculateForBatchTransientExceptionTests(TestCase):
    """Mirror of IngestBatchTransientExceptionTests for the calculation stage."""

    def setUp(self):
        self.org = Organization.objects.create(name="Retry Calc Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL
        )

    def _ingested_batch(self):
        batch = UploadBatch.objects.create(
            organization=self.org, data_source=self.ds, file_name="sap.csv",
            status=UploadBatch.BatchStatus.PENDING,
        )
        key = f"uploads/{self.org.id}/{batch.id}/sap.csv"
        get_storage_service().save(key, _sap_csv_bytes(), content_type="text/csv")
        ingest_task(batch_id=str(batch.id), storage_key=key, workflow_id="wf-calc-setup")
        batch.refresh_from_db()
        return batch

    def test_transient_exception_leaves_calculation_status_non_terminal(self):
        batch = self._ingested_batch()
        with patch(
            "apps.carbon.services.carbon_service.EmissionCalculation.objects.bulk_create",
            side_effect=OperationalError("connection reset"),
        ):
            with self.assertRaises(OperationalError):
                CarbonCalculationService().calculate_for_batch(
                    batch, transient_exceptions=(OperationalError, InterfaceError)
                )

        batch.refresh_from_db()
        self.assertNotIn(batch.calculation_status, UploadBatch.CALCULATION_TERMINAL_STATUSES)
        self.assertEqual(batch.calculation_status, UploadBatch.CalculationStatus.CALCULATING)

    def test_same_exception_without_transient_exceptions_marks_calculation_failed(self):
        batch = self._ingested_batch()
        with patch(
            "apps.carbon.services.carbon_service.EmissionCalculation.objects.bulk_create",
            side_effect=OperationalError("connection reset"),
        ):
            with self.assertRaises(OperationalError):
                CarbonCalculationService().calculate_for_batch(batch)  # no transient_exceptions

        batch.refresh_from_db()
        self.assertEqual(batch.calculation_status, UploadBatch.CalculationStatus.CALCULATION_FAILED)
        self.assertIn("OperationalError", batch.error_message)

    def test_retry_after_transient_failure_succeeds_and_is_not_skipped(self):
        batch = self._ingested_batch()

        with patch(
            "apps.carbon.services.carbon_service.EmissionCalculation.objects.bulk_create",
            side_effect=OperationalError("connection reset"),
        ):
            with self.assertRaises(OperationalError):
                calculate_task(batch_id=str(batch.id), workflow_id="wf-retry-calc-e2e")

        batch.refresh_from_db()
        self.assertEqual(batch.calculation_status, UploadBatch.CalculationStatus.CALCULATING)

        result = calculate_task(batch_id=str(batch.id), workflow_id="wf-retry-calc-e2e")

        self.assertEqual(result, "completed")
        batch.refresh_from_db()
        self.assertEqual(batch.calculation_status, UploadBatch.CalculationStatus.CALCULATED)


class RetryLoggingDistinguishesAttemptsTests(TestCase):
    """Requirement: structured logging clearly distinguishes initial
    execution from retry attempts."""

    def setUp(self):
        self.org = Organization.objects.create(name="Log Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL
        )
        self.batch = UploadBatch.objects.create(
            organization=self.org, data_source=self.ds, file_name="sap.csv",
            status=UploadBatch.BatchStatus.PENDING,
        )
        self.key = f"uploads/{self.org.id}/{self.batch.id}/sap.csv"
        get_storage_service().save(self.key, _sap_csv_bytes(), content_type="text/csv")

    def test_initial_attempt_is_logged_as_such(self):
        with self.assertLogs("apps.ingestion.tasks", level="INFO") as cm:
            ingest_task(batch_id=str(self.batch.id), storage_key=self.key, workflow_id="wf-log-1")
        self.assertTrue(any("initial attempt" in line for line in cm.output))
        self.assertFalse(any("retry attempt" in line for line in cm.output))
