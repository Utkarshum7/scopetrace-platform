"""
Phase 5g — confirms calculate_task dispatches send_notification_task on
BOTH its success path (the whole chain's true final resting state) and its
non-retryable failure path. See docs/NOTIFICATIONS.md.
"""
from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase

from apps.carbon.tasks import calculate_task
from apps.core.models import DataSource, Organization
from apps.core.storage import get_storage_service
from apps.ingestion.models import UploadBatch
from apps.ingestion.tasks import ingest_task

User = get_user_model()


def _sap_csv_bytes():
    today = date.today().strftime("%d.%m.%Y")
    return (
        "Werk;Buchungsdatum;Material;Materialkurztext;Menge;Einheit;Nettopreis\n"
        f"DE01;{today};DSL;Diesel;500,00;L;750.00\n"
    ).encode("utf-8")


class CalculateTaskNotificationDispatchTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Calc Notify Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL
        )
        self.user = User.objects.create_user(
            username="uploader5", email="uploader5@example.com", password="x"
        )

    def _ingested_batch(self):
        batch = UploadBatch.objects.create(
            organization=self.org, data_source=self.ds, file_name="sap.csv",
            uploaded_by=self.user, status=UploadBatch.BatchStatus.PENDING,
        )
        key = f"uploads/{self.org.id}/{batch.id}/sap.csv"
        get_storage_service().save(key, _sap_csv_bytes(), content_type="text/csv")
        ingest_task(batch_id=str(batch.id), storage_key=key, workflow_id="wf-calc-notify-setup")
        # ingest_task's own success path never sends a notification — the
        # chain isn't done until calculation finishes too.
        mail.outbox.clear()
        batch.refresh_from_db()
        return batch

    def test_calculation_success_dispatches_notification(self):
        batch = self._ingested_batch()

        result = calculate_task(batch_id=str(batch.id), workflow_id="wf-calc-notify-success")

        self.assertEqual(result, "completed")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("processed", mail.outbox[0].subject.lower())
        self.assertEqual(mail.outbox[0].to, ["uploader5@example.com"])

    def test_calculation_non_retryable_failure_dispatches_notification(self):
        batch = self._ingested_batch()

        with patch(
            "apps.carbon.services.carbon_service.EmissionCalculation.objects.bulk_create",
            side_effect=ValueError("malformed calc"),
        ):
            with self.assertRaises(ValueError):
                calculate_task(batch_id=str(batch.id), workflow_id="wf-calc-notify-fail")

        batch.refresh_from_db()
        self.assertEqual(batch.calculation_status, UploadBatch.CalculationStatus.CALCULATION_FAILED)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("calculation failed", mail.outbox[0].subject.lower())

    def test_calculation_retryable_failure_does_not_dispatch_notification(self):
        from django.db import OperationalError

        batch = self._ingested_batch()

        with patch(
            "apps.carbon.services.carbon_service.EmissionCalculation.objects.bulk_create",
            side_effect=OperationalError("connection reset"),
        ):
            with self.assertRaises(OperationalError):
                calculate_task(batch_id=str(batch.id), workflow_id="wf-calc-notify-retry")

        self.assertEqual(len(mail.outbox), 0)
