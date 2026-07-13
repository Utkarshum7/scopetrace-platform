"""
apps.ingestion.tasks.ingest_task (Phase 5b/5d) and apps.carbon.tasks.
calculate_task (Phase 5d) — the two links of the ingest -> calculate chain.

Focus of these tests: each task's OWN idempotency guard (the reason
acks_late/prefetch=1 were locked in during Phase 5a), now that ingestion and
calculation are separate tasks with separate status axes
(UploadBatch.status vs UploadBatch.calculation_status). The request/
response-level upload contract is covered in tests.py (CELERY_TASK_
ALWAYS_EAGER makes those tests exercise the whole chain inline).
"""
from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status as drf_status
from rest_framework.test import APIClient

from apps.accounts.models import Membership, Role
from apps.carbon.models import EmissionCalculation
from apps.carbon.tasks import calculate_task
from apps.core.models import DataSource, Organization
from apps.core.storage import get_storage_service
from apps.ingestion.models import EmissionRecord, UploadBatch
from apps.ingestion.tasks import ingest_task

User = get_user_model()


def _sap_csv_bytes():
    today = date.today().strftime("%d.%m.%Y")
    return (
        "Werk;Buchungsdatum;Material;Materialkurztext;Menge;Einheit;Nettopreis\n"
        f"DE01;{today};DSL;Diesel;500,00;L;750.00\n"
    ).encode("utf-8")


class IngestTaskTests(TestCase):
    """ingest_task now does ONLY ingestion — no calculation. Its idempotency
    guard checks UploadBatch.status (TERMINAL_STATUSES), unrelated to
    calculation_status."""

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
        result = ingest_task("00000000-0000-0000-0000-000000000000", "uploads/nope.csv", "wf-1")
        self.assertEqual(result, "batch-not-found")

    def test_already_completed_batch_is_skipped_not_reprocessed(self):
        batch, key = self._staged_batch(status=UploadBatch.BatchStatus.PENDING)
        # Run once for real, then simulate redelivery of the same task.
        ingest_task(str(batch.id), key, "wf-2")
        self.assertEqual(EmissionRecord.objects.filter(batch=batch).count(), 1)

        result = ingest_task(str(batch.id), key, "wf-2")

        self.assertEqual(result, "skipped-COMPLETED")
        # The critical assertion: redelivery must NOT re-run bulk_create — if
        # it did, this would raise IntegrityError (unique_together on
        # (batch, row_index)) rather than silently duplicate, but either way
        # the count below proves it never got that far.
        self.assertEqual(EmissionRecord.objects.filter(batch=batch).count(), 1)

    def test_already_failed_batch_is_skipped(self):
        batch, key = self._staged_batch(status=UploadBatch.BatchStatus.FAILED)
        result = ingest_task(str(batch.id), key, "wf-3")
        self.assertEqual(result, "skipped-FAILED")

    def test_pending_batch_is_ingested_but_not_yet_calculated(self):
        # Phase 5d: ingest_task alone does NOT trigger calculation anymore —
        # that's calculate_task's job, a separate chain link.
        batch, key = self._staged_batch(status=UploadBatch.BatchStatus.PENDING)
        result = ingest_task(str(batch.id), key, "wf-4")

        self.assertEqual(result, "completed")
        batch.refresh_from_db()
        self.assertEqual(batch.status, UploadBatch.BatchStatus.COMPLETED)
        self.assertEqual(batch.total_rows, 1)
        self.assertEqual(batch.failed_rows, 0)
        self.assertEqual(EmissionRecord.objects.filter(batch=batch).count(), 1)
        # Ingestion succeeding does not imply calculation ran.
        self.assertEqual(batch.calculation_status, UploadBatch.CalculationStatus.NOT_STARTED)
        self.assertEqual(EmissionCalculation.objects.filter(emission_record__batch=batch).count(), 0)
        # finished_at is NOT set by ingest_task alone (Phase 5d) — the whole
        # chain isn't done until calculate_task also completes.
        self.assertIsNone(batch.finished_at)


class CalculateTaskTests(TestCase):
    """calculate_task's own idempotency guard checks
    UploadBatch.calculation_status (CALCULATION_TERMINAL_STATUSES),
    independent of ingest_task's guard."""

    def setUp(self):
        self.org = Organization.objects.create(name="Calc Task Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL
        )

    def _ingested_batch(self):
        batch, key = self._staged_batch()
        ingest_task(str(batch.id), key, "wf-calc")
        batch.refresh_from_db()
        return batch

    def _staged_batch(self):
        batch = UploadBatch.objects.create(
            organization=self.org, data_source=self.ds, file_name="sap.csv",
            status=UploadBatch.BatchStatus.PENDING,
        )
        key = f"uploads/{self.org.id}/{batch.id}/sap.csv"
        get_storage_service().save(key, _sap_csv_bytes(), content_type="text/csv")
        return batch, key

    def test_batch_not_found_does_not_raise(self):
        result = calculate_task("00000000-0000-0000-0000-000000000000", "wf-nf")
        self.assertEqual(result, "batch-not-found")

    def test_already_calculated_batch_is_skipped_not_reprocessed(self):
        batch = self._ingested_batch()
        calculate_task(str(batch.id), "wf-dup")
        self.assertEqual(EmissionCalculation.objects.filter(emission_record__batch=batch).count(), 1)

        result = calculate_task(str(batch.id), "wf-dup")

        self.assertEqual(result, "skipped-CALCULATED")
        # Critical assertion: redelivery must NOT re-run bulk_create — that
        # would hit EmissionCalculation's unique_current_calc_per_record
        # constraint rather than silently duplicate, but either way the
        # count below proves it never got that far.
        self.assertEqual(EmissionCalculation.objects.filter(emission_record__batch=batch).count(), 1)

    def test_already_calculation_failed_batch_is_skipped(self):
        batch = self._ingested_batch()
        batch.calculation_status = UploadBatch.CalculationStatus.CALCULATION_FAILED
        batch.save(update_fields=["calculation_status"])

        result = calculate_task(str(batch.id), "wf-failed")
        self.assertEqual(result, "skipped-CALCULATION_FAILED")

    def test_ingested_batch_is_fully_calculated(self):
        batch = self._ingested_batch()
        # .delay() (not a direct call) — needed for self.request.hostname to
        # be populated at all; a direct function call bypasses Celery's
        # request-context setup entirely (worker_id would be None).
        calculate_task.delay(str(batch.id), "wf-full")

        batch.refresh_from_db()
        self.assertEqual(batch.calculation_status, UploadBatch.CalculationStatus.CALCULATED)
        self.assertEqual(EmissionCalculation.objects.filter(emission_record__batch=batch).count(), 1)
        # Phase 5d: calculate_task owns finished_at — marks the whole chain done.
        self.assertIsNotNone(batch.finished_at)
        self.assertTrue(batch.worker_id)


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
