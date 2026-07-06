"""
Phase 5e — dead-letter queue: apps.tasks.models.FailedTaskLog and the
task_failure signal handler (apps/tasks/signals.py).
"""
import uuid

from django.test import TestCase

from apps.core.models import DataSource, Organization
from apps.ingestion.models import UploadBatch
from apps.tasks.models import FailedTaskLog
from apps.tasks.signals import _handle_permanently_failed_task


class DeadLetterSignalHandlerUnitTests(TestCase):
    """Calls the signal handler function directly with manually-constructed
    arguments simulating what Celery's task_failure signal actually passes —
    fast and precise for the "retries genuinely exhausted" scenario, which
    would otherwise require sitting through real backoff delays (2s/4s/8s+)
    in eager mode to reproduce end-to-end."""

    def setUp(self):
        self.org = Organization.objects.create(name="DLQ Unit Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL
        )

    class _FakeSender:
        """Minimal stand-in for the Task instance Celery passes as `sender`
        — only needs `.name` and `.request.retries`, which is all the
        handler reads from it."""
        def __init__(self, name, retries):
            self.name = name
            self.request = type("R", (), {"retries": retries})()

    def test_creates_failed_task_log_with_correct_fields(self):
        fake_batch_id = str(uuid.uuid4())
        _handle_permanently_failed_task(
            sender=self._FakeSender("apps.ingestion.tasks.ingest_task", retries=3),
            task_id="task-abc-123",
            exception=ConnectionError("MinIO unreachable"),
            args=[],
            kwargs={"batch_id": fake_batch_id, "workflow_id": "wf-xyz", "storage_key": "uploads/x.csv"},
            traceback=None,
            einfo="Traceback (most recent call last): ...",
        )

        log = FailedTaskLog.objects.get(task_id="task-abc-123")
        self.assertEqual(log.task_name, "apps.ingestion.tasks.ingest_task")
        self.assertEqual(log.batch_id, fake_batch_id)
        self.assertEqual(log.workflow_id, "wf-xyz")
        self.assertEqual(log.exception_type, "ConnectionError")
        self.assertEqual(log.exception_message, "MinIO unreachable")
        self.assertEqual(log.retries_attempted, 3)
        self.assertEqual(log.kwargs["storage_key"], "uploads/x.csv")

    def test_ingest_task_exhausted_retries_marks_batch_failed(self):
        # Simulates the batch being left non-terminal by the
        # transient_exceptions mechanism (PROCESSING, never marked FAILED
        # across 3 real attempts), then retries genuinely exhaust.
        batch = UploadBatch.objects.create(
            organization=self.org, data_source=self.ds, file_name="x.csv",
            status=UploadBatch.BatchStatus.PROCESSING,
        )

        _handle_permanently_failed_task(
            sender=self._FakeSender("apps.ingestion.tasks.ingest_task", retries=3),
            task_id="task-exhausted",
            exception=ConnectionError("persistent DB outage"),
            args=[],
            kwargs={"batch_id": str(batch.id), "workflow_id": "wf-exhausted"},
            traceback=None,
            einfo=None,
        )

        batch.refresh_from_db()
        self.assertEqual(batch.status, UploadBatch.BatchStatus.FAILED)
        self.assertIn("failed permanently after 3 retries", batch.error_message)
        self.assertIn("ConnectionError", batch.error_message)
        self.assertIsNotNone(batch.finished_at)

    def test_calculate_task_exhausted_retries_marks_calculation_failed(self):
        batch = UploadBatch.objects.create(
            organization=self.org, data_source=self.ds, file_name="x.csv",
            status=UploadBatch.BatchStatus.COMPLETED,
            calculation_status=UploadBatch.CalculationStatus.CALCULATING,
        )

        _handle_permanently_failed_task(
            sender=self._FakeSender("apps.carbon.tasks.calculate_task", retries=5),
            task_id="task-calc-exhausted",
            exception=ConnectionError("persistent DB outage"),
            args=[],
            kwargs={"batch_id": str(batch.id), "workflow_id": "wf-calc-exhausted"},
            traceback=None,
            einfo=None,
        )

        batch.refresh_from_db()
        self.assertEqual(batch.calculation_status, UploadBatch.CalculationStatus.CALCULATION_FAILED)
        # status (ingestion axis) must be untouched — this signal only ever
        # fixes up the axis the failing task actually owns.
        self.assertEqual(batch.status, UploadBatch.BatchStatus.COMPLETED)

    def test_is_idempotent_when_batch_already_terminal(self):
        # A non-retryable exception may have ALREADY marked the batch FAILED
        # (via ingest_batch's own except-Exception branch) before
        # task_failure even fires — the signal handler's update must be a
        # safe no-op, not double-apply or overwrite with different values.
        batch = UploadBatch.objects.create(
            organization=self.org, data_source=self.ds, file_name="x.csv",
            status=UploadBatch.BatchStatus.FAILED,
            error_message="Original specific failure reason",
        )

        _handle_permanently_failed_task(
            sender=self._FakeSender("apps.ingestion.tasks.ingest_task", retries=0),
            task_id="task-already-failed",
            exception=ValueError("some other exception"),
            args=[],
            kwargs={"batch_id": str(batch.id), "workflow_id": "wf-already-failed"},
            traceback=None,
            einfo=None,
        )

        batch.refresh_from_db()
        self.assertEqual(batch.status, UploadBatch.BatchStatus.FAILED)
        # Untouched — the exclude(status__in=TERMINAL_STATUSES) made this a no-op.
        self.assertEqual(batch.error_message, "Original specific failure reason")

    def test_missing_batch_id_does_not_raise(self):
        # Some hypothetical future task might not pass batch_id at all —
        # must not crash the signal handler.
        _handle_permanently_failed_task(
            sender=self._FakeSender("some.other.task", retries=0),
            task_id="task-no-batch",
            exception=RuntimeError("unrelated failure"),
            args=[],
            kwargs={},
            traceback=None,
            einfo=None,
        )
        self.assertTrue(FailedTaskLog.objects.filter(task_id="task-no-batch").exists())


class DeadLetterSignalWiringTests(TestCase):
    """Confirms apps.tasks.apps.TasksConfig.ready() actually connected our
    handler to Celery's real task_failure signal (not just that the handler
    function works correctly in isolation, which
    DeadLetterSignalHandlerUnitTests already covers).

    Deliberately sends the signal directly (celery.signals.task_failure.send)
    rather than triggering it via a real task failure through .delay(),
    because CELERY_TASK_EAGER_PROPAGATES=True (needed so exceptions surface
    immediately in tests/local dev) makes Celery's eager tracer skip its own
    signal-dispatch machinery entirely for propagated exceptions — confirmed
    by reading celery.app.trace.build_tracer's on_error() helper:
    `if propagate: raise`, bypassing handle_error_state() -> handle_failure(),
    which is where task_failure.send(...) actually lives. This is a genuine
    Celery eager-mode behavior, not a gap in our code: task_failure fires
    correctly during real (non-eager) dispatch — verified separately against
    a live Docker Compose worker (see docs/RETRY_DLQ.md), which is exactly
    why this milestone's requirements call for Docker verification and not
    only unit tests.
    """

    def test_task_failure_signal_is_connected_to_dead_letter_handler(self):
        from celery.signals import task_failure

        org = Organization.objects.create(name="Wiring Org")
        ds = DataSource.objects.create(
            organization=org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL
        )
        batch = UploadBatch.objects.create(
            organization=org, data_source=ds, file_name="x.csv",
            status=UploadBatch.BatchStatus.PENDING,
        )

        class _FakeSender:
            name = "apps.ingestion.tasks.ingest_task"
            request = type("R", (), {"retries": 0})()

        task_failure.send(
            sender=_FakeSender(),
            task_id="wiring-test-task-id",
            exception=RuntimeError("simulated"),
            args=[],
            kwargs={"batch_id": str(batch.id), "workflow_id": "wf-wiring"},
            traceback=None,
            einfo=None,
        )

        self.assertTrue(FailedTaskLog.objects.filter(task_id="wiring-test-task-id").exists())
        batch.refresh_from_db()
        self.assertEqual(batch.status, UploadBatch.BatchStatus.FAILED)
