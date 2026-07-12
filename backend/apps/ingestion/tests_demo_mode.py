"""
D4 (Demo Mode) — integration proof that, under Demo Mode's execution settings,
a single upload request runs the FULL ingest -> calculate pipeline
synchronously in-process, with no Celery worker or Beat running (the test
environment has neither). The mode-derivation matrix and demo-aware health
endpoint are covered in apps/core/tests_demo_mode.py.
"""
from datetime import date, timedelta
from io import BytesIO

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.test import TestCase, override_settings
from rest_framework import status as drf_status
from rest_framework.test import APIClient

from apps.accounts.models import Membership, Role
from apps.carbon.models import EmissionCalculation
from apps.core.models import DataSource, Organization
from apps.ingestion.models import EmissionRecord, UploadBatch
# Reuse the exact SAP-CSV fixture the existing API upload suite uses, so this
# test exercises the same real parse/validate/normalize path.
from apps.ingestion.tests import _make_sap_csv_bytes

User = get_user_model()


@override_settings(
    DEMO_MODE=True,
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=False,
)
class DemoModePipelineTests(TestCase):
    """With Demo Mode's execution settings, an upload drives ingest -> calculate
    to completion inside the request thread. No worker/Beat process exists in
    the test environment, so a green result here proves Demo Mode needs none."""

    def setUp(self):
        self.client = APIClient()
        self.org = Organization.objects.create(name="Demo Org")
        self.user = User.objects.create_user(username="demo_analyst", password="pw")
        Membership.objects.create(
            user=self.user, organization=self.org, role=Role.ANALYST, active=True
        )
        self.client.force_authenticate(user=self.user)
        self.sap_ds = DataSource.objects.create(
            organization=self.org,
            name="SAP Fuel Feed",
            source_type=DataSource.SourceType.SAP_FUEL,
        )
        today = date.today().strftime("%d.%m.%Y")
        yesterday = (date.today() - timedelta(days=1)).strftime("%d.%m.%Y")
        self._sap_csv = _make_sap_csv_bytes(today, yesterday)

    def _upload(self):
        file_obj = InMemoryUploadedFile(
            file=BytesIO(self._sap_csv), field_name="file", name="demo.csv",
            content_type="text/csv", size=len(self._sap_csv), charset="utf-8",
        )
        return self.client.post(
            "/api/upload/sap/",
            data={"file": file_obj, "data_source": str(self.sap_ds.id)},
            format="multipart",
        )

    def test_upload_runs_ingest_and_calculate_synchronously_without_a_worker(self):
        # Demo Mode's execution setting: tasks run inline, no broker/worker.
        self.assertTrue(settings.CELERY_TASK_ALWAYS_EAGER)

        resp = self._upload()
        self.assertEqual(resp.status_code, drf_status.HTTP_202_ACCEPTED)
        data = resp.json()

        # Ingest ran synchronously in the request: the batch is already TERMINAL
        # (COMPLETED), not left QUEUED for a worker that does not exist.
        self.assertEqual(data["status"], UploadBatch.BatchStatus.COMPLETED)
        batch = UploadBatch.objects.get(id=data["batch_id"])
        self.assertIn(batch.status, UploadBatch.TERMINAL_STATUSES)

        # Ingest produced the emission records...
        records = EmissionRecord.objects.filter(batch=batch)
        self.assertEqual(records.count(), 2)

        # ...and the chained calculate_task ran synchronously right after,
        # producing a current CO2e calculation for each record and moving the
        # batch's calculation axis to a terminal state.
        self.assertIn(batch.calculation_status, UploadBatch.CALCULATION_TERMINAL_STATUSES)
        self.assertEqual(
            EmissionCalculation.objects.filter(
                emission_record__batch=batch, is_current=True
            ).count(),
            records.count(),
        )
