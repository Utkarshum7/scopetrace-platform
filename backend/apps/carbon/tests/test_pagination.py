from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import Membership, Role
from apps.carbon.models import EmissionCalculation
from apps.core.models import DataSource, Organization
from apps.ingestion.models import EmissionRecord, UploadBatch

User = get_user_model()
RS = EmissionCalculation.ResolutionStatus


class PaginationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.org = Organization.objects.create(name="Page Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL
        )
        self.batch = UploadBatch.objects.create(
            organization=self.org, data_source=self.ds, file_name="b.csv"
        )
        self.user = User.objects.create_user("u", password="pw")
        Membership.objects.create(user=self.user, organization=self.org, role=Role.ANALYST, active=True)
        self.client.force_authenticate(self.user)
        for i in range(3):
            rec = EmissionRecord.objects.create(
                organization=self.org, batch=self.batch, row_index=i + 1,
                raw_data_payload={"x": 1}, status=EmissionRecord.RecordStatus.DRAFT,
                normalized_value=Decimal("1"), normalized_unit="L", scope_category="SCOPE_1",
            )
            EmissionCalculation.objects.create(
                organization=self.org, emission_record=rec, is_current=True, scope="SCOPE_1",
                reporting_date=date(2024, 1, 1), reporting_month=date(2024, 1, 1),
                co2e_tonnes=Decimal("1"), co2e_kg=Decimal("1000"), resolution_status=RS.CALCULATED,
            )

    def test_records_are_paginated(self):
        data = self.client.get("/api/records/").json()
        self.assertIn("count", data)
        self.assertIn("results", data)
        self.assertEqual(data["count"], 3)

    def test_page_size_param(self):
        data = self.client.get("/api/records/?page_size=2").json()
        self.assertEqual(len(data["results"]), 2)
        self.assertEqual(data["count"], 3)
        self.assertIsNotNone(data["next"])

    def test_selector_endpoint_is_not_paginated(self):
        # datasources is a bounded selector -> bare array (opt-out)
        data = self.client.get("/api/datasources/").json()
        self.assertIsInstance(data, list)

    def test_calculations_filterset(self):
        data = self.client.get("/api/calculations/?status=CALCULATED").json()
        self.assertEqual(data["count"], 3)
        none = self.client.get("/api/calculations/?status=UNRESOLVED_NO_FACTOR").json()
        self.assertEqual(none["count"], 0)
