"""
Phase 5g — apps.core.notifications (subject/body composition + the
skip/send decision) and apps.core.tasks.send_notification_task (the
fire-and-forget dispatch wrapper). See docs/NOTIFICATIONS.md.

Django's test runner always forces EMAIL_BACKEND to the locmem backend
regardless of settings.py, so django.core.mail.outbox is available directly
without any override_settings here.
"""
from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase

from apps.core.models import DataSource, Organization
from apps.core.notifications import notify_batch_result
from apps.core.tasks import send_notification_task
from apps.ingestion.models import UploadBatch


class NotifyBatchResultTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Notify Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL
        )
        self.user = User.objects.create_user(
            username="uploader", email="uploader@example.com", password="x"
        )

    def _batch(self, **kwargs):
        return UploadBatch.objects.create(
            organization=self.org, data_source=self.ds, file_name="x.csv",
            uploaded_by=self.user, **kwargs
        )

    def test_skips_when_no_uploaded_by(self):
        batch = UploadBatch.objects.create(
            organization=self.org, data_source=self.ds, file_name="x.csv",
            status=UploadBatch.BatchStatus.FAILED, error_message="boom",
        )
        sent = notify_batch_result(batch)
        self.assertFalse(sent)
        self.assertEqual(len(mail.outbox), 0)

    def test_skips_when_uploaded_by_has_no_email(self):
        no_email_user = User.objects.create_user(username="noemail", password="x")
        batch = UploadBatch.objects.create(
            organization=self.org, data_source=self.ds, file_name="x.csv",
            uploaded_by=no_email_user,
            status=UploadBatch.BatchStatus.FAILED, error_message="boom",
        )
        sent = notify_batch_result(batch)
        self.assertFalse(sent)
        self.assertEqual(len(mail.outbox), 0)

    def test_skips_when_not_in_a_final_state(self):
        for status, calc_status in (
            (UploadBatch.BatchStatus.PENDING, UploadBatch.CalculationStatus.NOT_STARTED),
            (UploadBatch.BatchStatus.QUEUED, UploadBatch.CalculationStatus.NOT_STARTED),
            (UploadBatch.BatchStatus.PROCESSING, UploadBatch.CalculationStatus.NOT_STARTED),
            (UploadBatch.BatchStatus.COMPLETED, UploadBatch.CalculationStatus.NOT_STARTED),
            (UploadBatch.BatchStatus.COMPLETED, UploadBatch.CalculationStatus.CALCULATING),
        ):
            batch = self._batch(status=status, calculation_status=calc_status)
            sent = notify_batch_result(batch)
            self.assertFalse(sent, f"unexpectedly sent for {status}/{calc_status}")
        self.assertEqual(len(mail.outbox), 0)

    def test_sends_ingestion_failed_email(self):
        batch = self._batch(status=UploadBatch.BatchStatus.FAILED, error_message="Parser crashed")

        sent = notify_batch_result(batch)

        self.assertTrue(sent)
        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertIn("failed", email.subject.lower())
        self.assertEqual(email.to, ["uploader@example.com"])
        self.assertIn("Parser crashed", email.body)
        self.assertIn(str(batch.id), email.body)

    def test_sends_upload_processed_email_for_completed_calculated(self):
        batch = self._batch(
            status=UploadBatch.BatchStatus.COMPLETED,
            calculation_status=UploadBatch.CalculationStatus.CALCULATED,
            total_rows=10, failed_rows=0,
        )

        sent = notify_batch_result(batch)

        self.assertTrue(sent)
        email = mail.outbox[0]
        self.assertIn("processed", email.subject.lower())
        self.assertIn("10", email.body)

    def test_sends_upload_processed_email_for_partially_completed_calculated(self):
        batch = self._batch(
            status=UploadBatch.BatchStatus.PARTIALLY_COMPLETED,
            calculation_status=UploadBatch.CalculationStatus.CALCULATED,
            total_rows=10, failed_rows=3,
        )

        sent = notify_batch_result(batch)

        self.assertTrue(sent)
        email = mail.outbox[0]
        self.assertIn("processed", email.subject.lower())
        self.assertIn("3", email.body)

    def test_sends_calculation_failed_email(self):
        batch = self._batch(
            status=UploadBatch.BatchStatus.COMPLETED,
            calculation_status=UploadBatch.CalculationStatus.CALCULATION_FAILED,
            error_message="DB blew up", total_rows=5,
        )

        sent = notify_batch_result(batch)

        self.assertTrue(sent)
        email = mail.outbox[0]
        self.assertIn("calculation failed", email.subject.lower())
        self.assertIn("DB blew up", email.body)


class SendNotificationTaskTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Notify Task Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL
        )
        self.user = User.objects.create_user(
            username="uploader2", email="uploader2@example.com", password="x"
        )

    def test_returns_batch_not_found_for_unknown_id(self):
        import uuid
        result = send_notification_task(batch_id=str(uuid.uuid4()))
        self.assertEqual(result, "batch-not-found")
        self.assertEqual(len(mail.outbox), 0)

    def test_sends_and_returns_sent(self):
        batch = UploadBatch.objects.create(
            organization=self.org, data_source=self.ds, file_name="x.csv",
            uploaded_by=self.user,
            status=UploadBatch.BatchStatus.FAILED, error_message="boom",
        )

        result = send_notification_task(batch_id=str(batch.id))

        self.assertEqual(result, "sent")
        self.assertEqual(len(mail.outbox), 1)

    def test_returns_skipped_when_no_recipient(self):
        batch = UploadBatch.objects.create(
            organization=self.org, data_source=self.ds, file_name="x.csv",
            status=UploadBatch.BatchStatus.FAILED, error_message="boom",
        )

        result = send_notification_task(batch_id=str(batch.id))

        self.assertEqual(result, "skipped")
        self.assertEqual(len(mail.outbox), 0)
