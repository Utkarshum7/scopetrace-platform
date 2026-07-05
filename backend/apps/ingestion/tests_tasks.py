"""
Phase 5b — apps.ingestion.tasks.process_upload_batch.

Focus of these tests: the idempotency guard (the reason acks_late/prefetch=1
were locked in during Phase 5a) and the storage -> local-tempfile staging
step, since the request/response-level upload contract is already covered in
tests.py (CELERY_TASK_ALWAYS_EAGER makes those tests exercise this same task
inline).
"""
from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status as drf_status
from rest_framework.test import APIClient

from apps.accounts.models import Membership, Role
from apps.core.models import DataSource, Organization
from apps.core.storage import get_storage_service
from apps.ingestion.models import EmissionRecord, UploadBatch
from apps.ingestion.tasks import process_upload_batch

User = get_user_model()


def _sap_csv_bytes():
    today = date.today().strftime("%d.%m.%Y")
    return (
        "Werk;Buchungsdatum;Material;Materialkurztext;Menge;Einheit;Nettopreis\n"
        f"DE01;{today};DSL;Diesel;500,00;L;750.00\n"
    ).encode("utf-8")


class ProcessUploadBatchTaskTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Task Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL
        )

    def _staged_batch(self, status=UploadBatch.BatchStatus.PENDING):
        batch = UploadBatch.objects.create(
            organization=self.org, data_source=self.ds, file_name="sap.csv", status=status
        )
        key = f"uploads/{self.org.id}/{batch.id}/sap.csv"
        get_storage_service().save(key, _sap_csv_bytes(), content_type="text/csv")
        return batch, key

    def test_batch_not_found_does_not_raise(self):
        result = process_upload_batch("00000000-0000-0000-0000-000000000000", "uploads/nope.csv")
        self.assertEqual(result, "batch-not-found")

    def test_already_completed_batch_is_skipped_not_reprocessed(self):
        batch, key = self._staged_batch(status=UploadBatch.BatchStatus.PENDING)
        # Run once for real, then simulate redelivery of the same task.
        process_upload_batch(str(batch.id), key)
        self.assertEqual(EmissionRecord.objects.filter(batch=batch).count(), 1)

        result = process_upload_batch(str(batch.id), key)

        self.assertEqual(result, "skipped-COMPLETED")
        # The critical assertion: redelivery must NOT re-run bulk_create — if
        # it did, this would raise IntegrityError (unique_together on
        # (batch, row_index)) rather than silently duplicate, but either way
        # the count below proves it never got that far.
        self.assertEqual(EmissionRecord.objects.filter(batch=batch).count(), 1)

    def test_already_failed_batch_is_skipped(self):
        batch, key = self._staged_batch(status=UploadBatch.BatchStatus.FAILED)
        result = process_upload_batch(str(batch.id), key)
        self.assertEqual(result, "skipped-FAILED")

    def test_pending_batch_is_fully_processed(self):
        batch, key = self._staged_batch(status=UploadBatch.BatchStatus.PENDING)
        result = process_upload_batch(str(batch.id), key)

        self.assertEqual(result, "completed")
        batch.refresh_from_db()
        self.assertEqual(batch.status, UploadBatch.BatchStatus.COMPLETED)
        self.assertEqual(batch.total_rows, 1)
        self.assertEqual(batch.failed_rows, 0)
        self.assertEqual(EmissionRecord.objects.filter(batch=batch).count(), 1)


class UploadBatchDetailExposesParseErrorsTests(TestCase):
    """The batch-detail endpoint is the durable, pollable source of parse
    errors now that ingestion doesn't run on the request thread — confirms
    the Phase 5b UploadBatch.parse_errors field round-trips through the API,
    not just through the immediate 202 response body."""

    def setUp(self):
        self.client = APIClient()
        self.org = Organization.objects.create(name="Detail Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="Travel", source_type=DataSource.SourceType.CORP_TRAVEL
        )
        self.user = User.objects.create_user("detailuser", password="pw")
        Membership.objects.create(user=self.user, organization=self.org, role=Role.ANALYST, active=True)
        self.client.force_authenticate(self.user)

    def test_batch_detail_exposes_persisted_parse_errors(self):
        import json
        from io import BytesIO

        from django.core.files.uploadedfile import InMemoryUploadedFile

        travel_data = [
            {
                "trip_id": "T001", "travel_mode": "RAIL",
                "origin": "LON", "destination": "PAR",
                "distance_km": 490.0, "travel_date": date.today().isoformat(),
                "employee_id": "EMP001",
            },
            "this-is-not-an-object",
        ]
        payload = json.dumps(travel_data).encode("utf-8")
        file_obj = InMemoryUploadedFile(
            file=BytesIO(payload), field_name="file",
            name="travel_bad.json", content_type="application/json",
            size=len(payload), charset="utf-8",
        )
        upload_resp = self.client.post(
            "/api/upload/travel/",
            data={"file": file_obj, "data_source": str(self.ds.id)},
            format="multipart",
        )
        self.assertEqual(upload_resp.status_code, drf_status.HTTP_202_ACCEPTED)
        batch_id = upload_resp.json()["batch_id"]

        detail_resp = self.client.get(f"/api/batches/{batch_id}/")
        self.assertEqual(detail_resp.status_code, drf_status.HTTP_200_OK)
        errors = detail_resp.json()["parse_errors"]
        self.assertTrue(len(errors) >= 1)
        self.assertIn("row_index", errors[0])
        self.assertIn("error", errors[0])
