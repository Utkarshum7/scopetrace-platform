"""
Phase 5g — confirms ingest_task dispatches send_notification_task on a
non-retryable (final) failure, and apps.tasks.signals's DLQ handler does the
same after a successful retries-exhausted batch-status fixup. See
docs/NOTIFICATIONS.md.

CELERY_TASK_ALWAYS_EAGER is forced True under the test runner, so
send_notification_task.delay(...) executes inline/synchronously here — no
mocking of Celery dispatch needed, just checking django.core.mail.outbox
afterward.
"""
from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase

from apps.core.models import DataSource, Organization
from apps.core.storage import get_storage_service
from apps.ingestion.models import UploadBatch
from apps.ingestion.tasks import ingest_task
from apps.tasks.signals import _handle_permanently_failed_task

User = get_user_model()


def _sap_csv_bytes():
    today = date.today().strftime("%d.%m.%Y")
    return (
        "Werk;Buchungsdatum;Material;Materialkurztext;Menge;Einheit;Nettopreis\n"
        f"DE01;{today};DSL;Diesel;500,00;L;750.00\n"
    ).encode("utf-8")


class IngestTaskNotificationDispatchTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Notify Dispatch Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL
        )
        self.user = User.objects.create_user(
            username="uploader3", email="uploader3@example.com", password="x"
        )

    def test_non_retryable_failure_dispatches_notification(self):
        batch = UploadBatch.objects.create(
            organization=self.org, data_source=self.ds, file_name="sap.csv",
            uploaded_by=self.user, status=UploadBatch.BatchStatus.PENDING,
        )
        key = f"uploads/{self.org.id}/{batch.id}/sap.csv"
        get_storage_service().save(key, _sap_csv_bytes(), content_type="text/csv")

        # ValueError is not in INGEST_RETRYABLE_EXCEPTIONS — this is the
        # non-retryable path: ingest_batch() marks the batch FAILED (final
        # state) and ingest_task's new `except Exception` dispatches the
        # notification before re-raising.
        with patch(
            "apps.ingestion.services.ingestion_service.EmissionRecord.objects.bulk_create",
            side_effect=ValueError("malformed data"),
        ):
            with self.assertRaises(ValueError):
                ingest_task(batch_id=str(batch.id), storage_key=key, workflow_id="wf-notify-fail")

        batch.refresh_from_db()
        self.assertEqual(batch.status, UploadBatch.BatchStatus.FAILED)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("failed", mail.outbox[0].subject.lower())
        self.assertEqual(mail.outbox[0].to, ["uploader3@example.com"])

    def test_retryable_failure_does_not_dispatch_notification(self):
        # A transient failure is NOT a final state — no notification yet,
        # since a retry might still succeed.
        from django.db import OperationalError

        batch = UploadBatch.objects.create(
            organization=self.org, data_source=self.ds, file_name="sap.csv",
            uploaded_by=self.user, status=UploadBatch.BatchStatus.PENDING,
        )
        key = f"uploads/{self.org.id}/{batch.id}/sap.csv"
        get_storage_service().save(key, _sap_csv_bytes(), content_type="text/csv")

        with patch(
            "apps.ingestion.services.ingestion_service.EmissionRecord.objects.bulk_create",
            side_effect=OperationalError("connection reset"),
        ):
            with self.assertRaises(OperationalError):
                ingest_task(batch_id=str(batch.id), storage_key=key, workflow_id="wf-notify-retry")

        self.assertEqual(len(mail.outbox), 0)


class DeadLetterNotificationDispatchTests(TestCase):
    """The DLQ signal handler (apps/tasks/signals.py) is the other place a
    batch reaches its final resting state — after retries are genuinely
    exhausted and its atomic fixup succeeds."""

    def setUp(self):
        self.org = Organization.objects.create(name="DLQ Notify Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL
        )
        self.user = User.objects.create_user(
            username="uploader4", email="uploader4@example.com", password="x"
        )

    class _FakeSender:
        def __init__(self, name, retries):
            self.name = name
            self.request = type("R", (), {"retries": retries})()

    def test_dlq_fixup_dispatches_notification(self):
        batch = UploadBatch.objects.create(
            organization=self.org, data_source=self.ds, file_name="x.csv",
            uploaded_by=self.user, status=UploadBatch.BatchStatus.PROCESSING,
        )

        _handle_permanently_failed_task(
            sender=self._FakeSender("apps.ingestion.tasks.ingest_task", retries=3),
            task_id="task-dlq-notify",
            exception=ConnectionError("persistent DB outage"),
            args=[],
            kwargs={"batch_id": str(batch.id), "workflow_id": "wf-dlq-notify"},
            traceback=None,
            einfo=None,
        )

        batch.refresh_from_db()
        self.assertEqual(batch.status, UploadBatch.BatchStatus.FAILED)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["uploader4@example.com"])

    def test_dlq_no_op_fixup_does_not_dispatch_notification(self):
        # Batch already terminal (a non-retryable exception already marked
        # it) — the fixup is a no-op, and per _handle_permanently_failed_task
        # only dispatches when `updated` is truthy, no duplicate notification.
        batch = UploadBatch.objects.create(
            organization=self.org, data_source=self.ds, file_name="x.csv",
            uploaded_by=self.user, status=UploadBatch.BatchStatus.FAILED,
            error_message="Original reason",
        )

        _handle_permanently_failed_task(
            sender=self._FakeSender("apps.ingestion.tasks.ingest_task", retries=0),
            task_id="task-dlq-noop",
            exception=ValueError("some other exception"),
            args=[],
            kwargs={"batch_id": str(batch.id), "workflow_id": "wf-dlq-noop"},
            traceback=None,
            einfo=None,
        )

        self.assertEqual(len(mail.outbox), 0)
