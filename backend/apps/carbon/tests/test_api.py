import os
import tempfile

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from rest_framework import status as drf
from rest_framework.test import APIClient

from apps.accounts.models import Membership, Role
from apps.core.models import DataSource, Organization
from apps.ingestion.models import EmissionRecord
from apps.ingestion.services.ingestion_service import IngestionService

User = get_user_model()

SAP_CSV = (
    "Werk;Buchungsdatum;Material;Materialkurztext;Menge;Einheit;Nettopreis\n"
    "DE01;01.06.2024;DSL;Diesel;1000,00;L;1500.00\n"
)


def _ingest(ds):
    fd, path = tempfile.mkstemp(suffix=".csv")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(SAP_CSV)
    try:
        result = IngestionService().ingest(ds, path, original_filename="a.csv")
    finally:
        os.remove(path)
    return EmissionRecord.objects.filter(batch=result.batch).first()


class CarbonApiTests(TestCase):
    def setUp(self):
        call_command("seed_carbon")
        self.client = APIClient()
        self.orgA = Organization.objects.create(name="Org A")
        self.orgB = Organization.objects.create(name="Org B")

        self.admin = User.objects.create_user("admin_a", password="pw")
        Membership.objects.create(user=self.admin, organization=self.orgA, role=Role.ORG_ADMIN, active=True)
        self.viewer = User.objects.create_user("viewer_a", password="pw")
        Membership.objects.create(user=self.viewer, organization=self.orgA, role=Role.VIEWER, active=True)
        self.userB = User.objects.create_user("user_b", password="pw")
        Membership.objects.create(user=self.userB, organization=self.orgB, role=Role.ANALYST, active=True)

        self.dsA = DataSource.objects.create(
            organization=self.orgA, name="SAP A", source_type=DataSource.SourceType.SAP_FUEL
        )
        self.record = _ingest(self.dsA)

    # --- reference endpoints ---
    def test_reference_requires_auth(self):
        self.assertEqual(self.client.get("/api/activity-types/").status_code, drf.HTTP_401_UNAUTHORIZED)
        self.assertEqual(self.client.get("/api/calculations/").status_code, drf.HTTP_401_UNAUTHORIZED)

    def test_member_can_read_reference(self):
        self.client.force_authenticate(self.viewer)
        # activity-types is a bounded selector list (not paginated)
        self.assertEqual(len(self.client.get("/api/activity-types/").json()), 8)
        self.assertGreaterEqual(len(self.client.get("/api/emission-factors/").json()["results"]), 8)
        datasets = self.client.get("/api/factor-datasets/").json()["results"]
        self.assertTrue(any(d["publisher"] == "DEFRA" and d["checksum"] for d in datasets))

    # --- tenant scoping of calculations ---
    def test_calculations_scoped_to_org(self):
        self.client.force_authenticate(self.admin)
        calcs = self.client.get("/api/calculations/").json()["results"]
        self.assertTrue(any(c["emission_record"] == str(self.record.id) for c in calcs))

        self.client.force_authenticate(self.userB)
        calcs_b = self.client.get("/api/calculations/").json()["results"]
        self.assertFalse(any(c["emission_record"] == str(self.record.id) for c in calcs_b))

    # --- record serializer CO2e ---
    def test_record_includes_co2e(self):
        self.client.force_authenticate(self.admin)
        rows = self.client.get("/api/records/").json()["results"]
        row = next(r for r in rows if r["id"] == str(self.record.id))
        self.assertEqual(row["co2e_kg"], "2682.050000")
        self.assertEqual(row["calculation_status"], "CALCULATED")
        self.assertEqual(row["factor_provenance"]["publisher"], "DEFRA")
        self.assertTrue(row["calculation_trace"]["steps"])

    # --- recalculate RBAC + freeze ---
    def test_recalculate_requires_org_admin(self):
        self.client.force_authenticate(self.viewer)
        r = self.client.post(f"/api/records/{self.record.id}/recalculate/", {}, format="json")
        self.assertEqual(r.status_code, drf.HTTP_403_FORBIDDEN)

    def test_org_admin_can_recalculate(self):
        self.client.force_authenticate(self.admin)
        r = self.client.post(f"/api/records/{self.record.id}/recalculate/", {}, format="json")
        self.assertEqual(r.status_code, drf.HTTP_200_OK)
        self.assertEqual(r.json()["co2e_kg"], "2682.050000")

    def test_recalculate_frozen_for_approved(self):
        # Phase 6c: APPROVED is only reachable via SUBMITTED now.
        self.record.status = EmissionRecord.RecordStatus.SUBMITTED
        self.record.save()
        self.record.status = EmissionRecord.RecordStatus.APPROVED
        self.record.save()
        self.client.force_authenticate(self.admin)
        r = self.client.post(f"/api/records/{self.record.id}/recalculate/", {}, format="json")
        self.assertEqual(r.status_code, drf.HTTP_400_BAD_REQUEST)

    def test_cannot_recalculate_other_org_record(self):
        self.client.force_authenticate(self.userB)
        r = self.client.post(f"/api/records/{self.record.id}/recalculate/", {}, format="json")
        self.assertEqual(r.status_code, drf.HTTP_403_FORBIDDEN)
