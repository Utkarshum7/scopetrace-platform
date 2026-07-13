from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status as drf
from rest_framework.test import APIClient

from apps.accounts.models import Membership, Role
from apps.carbon.models import EmissionCalculation
from apps.core.models import DataSource, Organization
from apps.ingestion.models import EmissionRecord, UploadBatch

User = get_user_model()


class RecordExportTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.orgA = Organization.objects.create(name="Org A")
        self.orgB = Organization.objects.create(name="Org B")
        self.dsA = DataSource.objects.create(
            organization=self.orgA, name="SAP", source_type=DataSource.SourceType.SAP_FUEL
        )
        self.batchA = UploadBatch.objects.create(
            organization=self.orgA, data_source=self.dsA, file_name="a.csv"
        )
        self.batchB = UploadBatch.objects.create(
            organization=self.orgB,
            data_source=DataSource.objects.create(
                organization=self.orgB, name="B", source_type=DataSource.SourceType.SAP_FUEL
            ),
            file_name="b.csv",
        )
        self.recA = self._record(self.orgA, self.batchA, 1)
        EmissionCalculation.objects.create(
            organization=self.orgA, emission_record=self.recA, is_current=True, scope="SCOPE_1",
            co2e_tonnes=Decimal("2.5"), co2e_kg=Decimal("2500"),
            resolution_status=EmissionCalculation.ResolutionStatus.CALCULATED,
        )
        self._record(self.orgB, self.batchB, 1)  # other org

        self.user = User.objects.create_user("u", password="pw")
        Membership.objects.create(user=self.user, organization=self.orgA, role=Role.VIEWER, active=True)

    def _record(self, org, batch, idx):
        return EmissionRecord.objects.create(
            organization=org, batch=batch, row_index=idx, raw_data_payload={"x": 1},
            status=EmissionRecord.RecordStatus.DRAFT, normalized_value=Decimal("1000"),
            normalized_unit="L", scope_category="SCOPE_1",
        )

    def _body(self, response):
        return b"".join(response.streaming_content).decode("utf-8")

    def test_export_requires_auth(self):
        self.assertEqual(self.client.get("/api/records/export/").status_code, drf.HTTP_401_UNAUTHORIZED)

    def test_export_streams_csv_scoped_with_co2e(self):
        self.client.force_authenticate(self.user)
        response = self.client.get("/api/records/export/")
        self.assertEqual(response.status_code, drf.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn("attachment; filename=", response["Content-Disposition"])
        body = self._body(response)
        lines = [ln for ln in body.splitlines() if ln]
        self.assertTrue(lines[0].startswith("record_id,"))
        self.assertEqual(len(lines), 2)  # header + 1 record (Org A only)
        self.assertIn(str(self.recA.id), body)
        self.assertIn("2500", body)   # co2e_kg present
        self.assertIn("CALCULATED", body)

    def test_export_respects_status_filter(self):
        self.client.force_authenticate(self.user)
        body = self._body(self.client.get("/api/records/export/?status=APPROVED"))
        lines = [ln for ln in body.splitlines() if ln]
        self.assertEqual(len(lines), 1)  # header only; no APPROVED records
