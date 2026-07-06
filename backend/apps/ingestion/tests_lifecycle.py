"""
Phase 5c — enterprise job lifecycle: state machine transitions, progress
calculations, the polling endpoint, retry observability, and cancellation
(documented future state only — not implemented this phase).
"""
from datetime import date, timedelta
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.test import TestCase
from django.utils import timezone
from rest_framework import status as drf_status
from rest_framework.test import APIClient

from apps.accounts.models import Membership, Role
from apps.core.models import DataSource, Organization
from apps.core.storage import get_storage_service
from apps.ingestion.models import UploadBatch
from apps.ingestion.serializers import BatchProgressSerializer
from apps.ingestion.tasks import process_upload_batch

User = get_user_model()


def _sap_csv_bytes(valid=True):
    today = date.today().strftime("%d.%m.%Y")
    quantity = "500,00" if valid else "-500,00"  # negative -> fails validation
    return (
        "Werk;Buchungsdatum;Material;Materialkurztext;Menge;Einheit;Nettopreis\n"
        f"DE01;{today};DSL;Diesel;{quantity};L;750.00\n"
    ).encode("utf-8")


def _upload_file(content=None):
    content = content or _sap_csv_bytes()
    return InMemoryUploadedFile(
        file=BytesIO(content), field_name="file", name="sap.csv",
        content_type="text/csv", size=len(content), charset="utf-8",
    )


class BatchLifecycleTests(TestCase):
    """State machine transitions (Phase 5c requirement #1)."""

    def setUp(self):
        self.org = Organization.objects.create(name="Lifecycle Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL
        )

    def _staged_batch(self, valid=True):
        batch = UploadBatch.objects.create(
            organization=self.org, data_source=self.ds, file_name="sap.csv",
            status=UploadBatch.BatchStatus.PENDING,
        )
        key = f"uploads/{self.org.id}/{batch.id}/sap.csv"
        get_storage_service().save(key, _sap_csv_bytes(valid=valid), content_type="text/csv")
        return batch, key

    def test_all_rows_valid_transitions_to_completed(self):
        batch, key = self._staged_batch(valid=True)
        process_upload_batch(str(batch.id), key)
        batch.refresh_from_db()
        self.assertEqual(batch.status, UploadBatch.BatchStatus.COMPLETED)
        self.assertIsNotNone(batch.started_at)
        self.assertIsNotNone(batch.finished_at)
        self.assertGreaterEqual(batch.finished_at, batch.started_at)

    def test_some_rows_failed_transitions_to_partially_completed(self):
        batch, key = self._staged_batch(valid=False)
        process_upload_batch(str(batch.id), key)
        batch.refresh_from_db()
        self.assertEqual(batch.status, UploadBatch.BatchStatus.PARTIALLY_COMPLETED)
        self.assertGreater(batch.failed_rows, 0)

    def test_pipeline_crash_transitions_to_failed_with_specific_error_message(self):
        # An unregistered parser type crashes the pipeline itself — distinct
        # from a per-row validation failure (PARTIALLY_COMPLETED above).
        batch = UploadBatch.objects.create(
            organization=self.org, data_source=self.ds, file_name="x.csv",
            status=UploadBatch.BatchStatus.PENDING,
        )
        key = f"uploads/{self.org.id}/{batch.id}/x.csv"
        get_storage_service().save(key, b"irrelevant", content_type="text/csv")
        self.ds.source_type = "NOT_A_REAL_SOURCE_TYPE"
        self.ds.save()

        # Called directly (not via .delay()), so nothing catches the
        # exception on the way out — same as it would propagate to a real
        # Celery worker's own failure tracking. IngestionService.ingest_batch
        # records the FAILED state on the batch BEFORE re-raising.
        with self.assertRaises(ValueError):
            process_upload_batch(str(batch.id), key)
        batch.refresh_from_db()

        self.assertEqual(batch.status, UploadBatch.BatchStatus.FAILED)
        # Requirement #6: meaningful failure reasons, never a generic
        # "processing failed" — this specific failure is caught by
        # ingest_batch's parser-registry pre-check (its own distinct message,
        # not the generic exception handler's type(exc).__name__ format).
        self.assertIn("Pipeline configuration error", batch.error_message)
        self.assertIn("No parser registered", batch.error_message)
        self.assertIn("NOT_A_REAL_SOURCE_TYPE", batch.error_message)
        self.assertIsNotNone(batch.finished_at)

    def test_redelivery_of_every_terminal_status_is_skipped_not_reprocessed(self):
        # Covers all four TERMINAL_STATUSES at once, including CANCELLED —
        # proving the idempotency guard already treats it as terminal even
        # though nothing can transition into it yet (see
        # CancellationFutureStateTests below).
        for terminal_status in UploadBatch.TERMINAL_STATUSES:
            batch = UploadBatch.objects.create(
                organization=self.org, data_source=self.ds, file_name="x.csv",
                status=terminal_status,
            )
            result = process_upload_batch(str(batch.id), "uploads/does-not-matter.csv")
            self.assertEqual(result, f"skipped-{terminal_status}")
            batch.refresh_from_db()
            self.assertEqual(batch.status, terminal_status, "redelivery must not alter a terminal batch")


class BatchQueuedTransitionTests(TestCase):
    """View-level PENDING -> QUEUED/FAILED transitions."""

    def setUp(self):
        self.client = APIClient()
        self.org = Organization.objects.create(name="Queue Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL
        )
        self.user = User.objects.create_user("queueuser", password="pw")
        Membership.objects.create(user=self.user, organization=self.org, role=Role.ANALYST, active=True)
        self.client.force_authenticate(self.user)

    def test_upload_response_reflects_actual_post_delay_status(self):
        # Under CELERY_TASK_ALWAYS_EAGER (the test runner), the task has
        # already fully run by the time .delay() returns, so the view's
        # "still PENDING -> write QUEUED" branch is correctly skipped and the
        # response shows the real terminal status, not a stale QUEUED that
        # would misrepresent what actually happened. Real async QUEUED
        # behavior (batch stays QUEUED with a celery_task_id until a worker
        # picks it up) was verified directly against a live Docker Compose
        # worker+Redis+MinIO stack — not something a synchronous unit test
        # can safely exercise.
        response = self.client.post(
            "/api/upload/sap/",
            data={"file": _upload_file(), "data_source": str(self.ds.id)},
            format="multipart",
        )
        self.assertEqual(response.status_code, drf_status.HTTP_202_ACCEPTED)
        data = response.json()
        self.assertEqual(data["status"], UploadBatch.BatchStatus.COMPLETED)
        self.assertIn("progress_percentage", data)
        self.assertIn("successful_records", data)

    def test_storage_failure_transitions_batch_to_failed_with_batch_id_and_specific_message(self):
        with patch("apps.ingestion.views.get_storage_service") as mock_get_storage:
            mock_get_storage.return_value.save.side_effect = ConnectionError("MinIO unreachable")
            response = self.client.post(
                "/api/upload/sap/",
                data={"file": _upload_file(), "data_source": str(self.ds.id)},
                format="multipart",
            )
        self.assertEqual(response.status_code, drf_status.HTTP_503_SERVICE_UNAVAILABLE)
        data = response.json()
        # The batch record must still be discoverable even though the file
        # never reached durable storage.
        self.assertIn("batch_id", data)
        self.assertEqual(data["status"], UploadBatch.BatchStatus.FAILED)

        batch = UploadBatch.objects.get(id=data["batch_id"])
        self.assertEqual(batch.status, UploadBatch.BatchStatus.FAILED)
        self.assertIn("ConnectionError", batch.error_message)
        self.assertIn("MinIO unreachable", batch.error_message)
        self.assertIsNotNone(batch.finished_at)
        self.assertIsNone(batch.started_at)  # processing never began


class ProgressCalculationTests(TestCase):
    """Progress-field computation (Phase 5c requirement #2), independent of
    Celery/HTTP — pure serializer behavior over hand-constructed batches."""

    def setUp(self):
        self.org = Organization.objects.create(name="Progress Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL
        )

    def _batch(self, **kwargs):
        defaults = dict(organization=self.org, data_source=self.ds, file_name="x.csv")
        defaults.update(kwargs)
        return UploadBatch.objects.create(**defaults)

    def test_pending_batch_reports_zero_progress_and_no_duration(self):
        batch = self._batch(status=UploadBatch.BatchStatus.PENDING)
        data = BatchProgressSerializer(batch).data
        self.assertEqual(data["progress_percentage"], 0)
        self.assertEqual(data["processed_records"], 0)
        self.assertIsNone(data["duration_seconds"])
        self.assertIsNone(data["estimated_completion_time"])

    def test_completed_batch_reports_full_progress_and_correct_counts(self):
        now = timezone.now()
        batch = self._batch(
            status=UploadBatch.BatchStatus.COMPLETED,
            total_rows=10, failed_rows=3,
            started_at=now - timedelta(seconds=5), finished_at=now,
        )
        data = BatchProgressSerializer(batch).data
        self.assertEqual(data["progress_percentage"], 100)
        self.assertEqual(data["processed_records"], 10)
        self.assertEqual(data["successful_records"], 7)
        self.assertEqual(data["duration_seconds"], 5.0)

    def test_partially_completed_batch_reports_full_progress(self):
        batch = self._batch(
            status=UploadBatch.BatchStatus.PARTIALLY_COMPLETED, total_rows=5, failed_rows=5
        )
        data = BatchProgressSerializer(batch).data
        self.assertEqual(data["progress_percentage"], 100)
        self.assertEqual(data["successful_records"], 0)

    def test_failed_batch_reports_zero_progress_not_100(self):
        # Nothing was durably committed (the transaction rolled back) — 100%
        # would misrepresent a crash as a successful finish.
        batch = self._batch(status=UploadBatch.BatchStatus.FAILED, started_at=timezone.now())
        data = BatchProgressSerializer(batch).data
        self.assertEqual(data["progress_percentage"], 0)

    def test_processing_batch_reports_elapsed_time_not_a_final_duration(self):
        batch = self._batch(
            status=UploadBatch.BatchStatus.PROCESSING,
            started_at=timezone.now() - timedelta(seconds=2),
        )
        data = BatchProgressSerializer(batch).data
        self.assertIsNone(batch.finished_at)
        self.assertGreaterEqual(data["duration_seconds"], 2.0)

    def test_estimated_completion_time_uses_historical_average_for_same_data_source(self):
        now = timezone.now()
        UploadBatch.objects.create(
            organization=self.org, data_source=self.ds, file_name="h1.csv",
            status=UploadBatch.BatchStatus.COMPLETED,
            started_at=now - timedelta(minutes=10),
            finished_at=now - timedelta(minutes=10) + timedelta(seconds=10),
        )
        UploadBatch.objects.create(
            organization=self.org, data_source=self.ds, file_name="h2.csv",
            status=UploadBatch.BatchStatus.COMPLETED,
            started_at=now - timedelta(minutes=5),
            finished_at=now - timedelta(minutes=5) + timedelta(seconds=20),
        )
        current = self._batch(status=UploadBatch.BatchStatus.PROCESSING, started_at=now)

        data = BatchProgressSerializer(current).data

        # Average of 10s and 20s historical durations = 15s.
        expected = now + timedelta(seconds=15)
        self.assertIsNotNone(data["estimated_completion_time"])
        self.assertAlmostEqual(
            data["estimated_completion_time"].timestamp(), expected.timestamp(), delta=1
        )

    def test_estimated_completion_time_none_without_history(self):
        batch = self._batch(status=UploadBatch.BatchStatus.PROCESSING, started_at=timezone.now())
        data = BatchProgressSerializer(batch).data
        self.assertIsNone(data["estimated_completion_time"])

    def test_estimated_completion_time_only_applies_while_processing(self):
        batch = self._batch(
            status=UploadBatch.BatchStatus.COMPLETED,
            started_at=timezone.now(), finished_at=timezone.now(),
        )
        data = BatchProgressSerializer(batch).data
        self.assertIsNone(data["estimated_completion_time"])


class BatchProgressEndpointTests(TestCase):
    """Polling endpoint (Phase 5c requirement #3): GET /api/batches/{id}/progress/."""

    def setUp(self):
        self.client = APIClient()
        self.orgA = Organization.objects.create(name="Poll Org A")
        self.orgB = Organization.objects.create(name="Poll Org B")
        self.dsA = DataSource.objects.create(
            organization=self.orgA, name="SAP", source_type=DataSource.SourceType.SAP_FUEL
        )
        self.batchA = UploadBatch.objects.create(
            organization=self.orgA, data_source=self.dsA, file_name="a.csv",
            status=UploadBatch.BatchStatus.PROCESSING, total_rows=5, failed_rows=1,
            started_at=timezone.now(),
        )
        self.userA = User.objects.create_user("pollA", password="pw")
        Membership.objects.create(user=self.userA, organization=self.orgA, role=Role.VIEWER, active=True)
        self.userB = User.objects.create_user("pollB", password="pw")
        Membership.objects.create(user=self.userB, organization=self.orgB, role=Role.VIEWER, active=True)

    def test_progress_endpoint_returns_lean_self_contained_payload(self):
        # This exact shape is what Phase 5c requirement #3 asks to remain
        # stable across a future WebSocket/SSE migration.
        self.client.force_authenticate(self.userA)
        response = self.client.get(f"/api/batches/{self.batchA.id}/progress/")
        self.assertEqual(response.status_code, drf_status.HTTP_200_OK)
        data = response.json()
        expected_keys = {
            "id", "status", "total_rows", "failed_rows", "successful_records",
            "processed_records", "progress_percentage", "estimated_completion_time",
            "started_at", "finished_at", "duration_seconds", "worker_id",
            "retry_count", "error_message", "parse_errors",
        }
        self.assertEqual(set(data.keys()), expected_keys)
        self.assertEqual(data["total_rows"], 5)
        self.assertEqual(data["failed_rows"], 1)
        self.assertEqual(data["successful_records"], 4)

    def test_progress_endpoint_is_tenant_scoped(self):
        # Cross-tenant: the batch is filtered out of orgB's queryset entirely
        # (TenantScopedViewSetMixin), so get_object() 404s rather than 403s.
        self.client.force_authenticate(self.userB)
        response = self.client.get(f"/api/batches/{self.batchA.id}/progress/")
        self.assertEqual(response.status_code, drf_status.HTTP_404_NOT_FOUND)

    def test_progress_endpoint_requires_authentication(self):
        response = self.client.get(f"/api/batches/{self.batchA.id}/progress/")
        self.assertEqual(response.status_code, drf_status.HTTP_401_UNAUTHORIZED)


class RetryObservabilityTests(TestCase):
    """Phase 5c requirement #7 'retry tests'. Actual retry/backoff policies
    are Phase 5e's job, not yet implemented — this validates only that the
    CAPTURE mechanism (Celery's self.request.retries/hostname) is wired
    correctly now, so 5e's future retry policies report real values with no
    further code change. Forcing a genuinely nonzero retry_count requires an
    actual retry policy to trigger a real redelivery, which doesn't exist
    yet — that assertion belongs in Phase 5e, not here."""

    def setUp(self):
        self.org = Organization.objects.create(name="Retry Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL
        )

    def _staged_batch(self):
        batch = UploadBatch.objects.create(
            organization=self.org, data_source=self.ds, file_name="x.csv",
            status=UploadBatch.BatchStatus.PENDING,
        )
        key = f"uploads/{self.org.id}/{batch.id}/x.csv"
        get_storage_service().save(key, _sap_csv_bytes(), content_type="text/csv")
        return batch, key

    def test_retry_count_defaults_to_zero_on_first_attempt(self):
        batch, key = self._staged_batch()
        process_upload_batch.delay(str(batch.id), key)
        batch.refresh_from_db()
        self.assertEqual(batch.retry_count, 0)

    def test_worker_id_is_captured(self):
        batch, key = self._staged_batch()
        process_upload_batch.delay(str(batch.id), key)
        batch.refresh_from_db()
        # Real value even under eager mode (verified: Celery's request
        # context still populates .hostname with the local machine's
        # hostname) — not a placeholder/None.
        self.assertTrue(batch.worker_id)


class CancellationFutureStateTests(TestCase):
    """Phase 5c requirement #7: 'cancellation tests (if not implemented yet,
    document the future state)'. Cancellation itself is NOT implemented this
    phase — no cancel endpoint, no Celery task revocation — the same
    "reserved interface, no implementation yet" pattern the carbon engine
    already established for its AIRecommendationStage (Phase 3).

    Intended future behavior (see docs/JOB_LIFECYCLE.md):
      - POST /api/batches/{id}/cancel/ (Org-Admin/Analyst, audited).
      - QUEUED -> CANCELLED: AsyncResult(batch.celery_task_id).revoke() —
        clean, since the task hasn't started; Celery drops it from the
        queue without running any of it.
      - PROCESSING -> CANCELLED: cooperative cancellation only — the task
        would need to check a cancellation flag between pipeline stages,
        since revoke(terminate=True) mid-transaction risks leaving
        inconsistent state. Likely restricted to QUEUED-only, or deferred
        further, when actually designed.
    """

    def setUp(self):
        self.org = Organization.objects.create(name="Cancel Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL
        )

    def test_cancelled_is_a_valid_terminal_status_already(self):
        # Proves the terminal-status set and progress calculations already
        # handle CANCELLED correctly, even though nothing can transition into
        # it yet — implementing the cancel endpoint later needs no schema or
        # terminal-status-set change.
        self.assertIn(UploadBatch.BatchStatus.CANCELLED, UploadBatch.TERMINAL_STATUSES)
        batch = UploadBatch.objects.create(
            organization=self.org, data_source=self.ds, file_name="x.csv",
            status=UploadBatch.BatchStatus.CANCELLED,
        )
        data = BatchProgressSerializer(batch).data
        self.assertEqual(data["progress_percentage"], 0)

    def test_redelivered_task_against_a_cancelled_batch_is_skipped(self):
        batch = UploadBatch.objects.create(
            organization=self.org, data_source=self.ds, file_name="x.csv",
            status=UploadBatch.BatchStatus.CANCELLED,
        )
        result = process_upload_batch(str(batch.id), "uploads/does-not-matter.csv")
        self.assertEqual(result, "skipped-CANCELLED")
