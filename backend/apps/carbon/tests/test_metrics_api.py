from datetime import date
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
RS = EmissionCalculation.ResolutionStatus


class MetricsApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.orgA = Organization.objects.create(name="Org A")
        self.orgB = Organization.objects.create(name="Org B")
        self.ds = DataSource.objects.create(
            organization=self.orgA, name="SAP", source_type=DataSource.SourceType.SAP_FUEL
        )
        self.batch = UploadBatch.objects.create(
            organization=self.orgA, data_source=self.ds, file_name="b.csv"
        )
        self._i = 0
        self._calc(self.orgA, "SCOPE_1", "2024-01-15", "10")
        self._calc(self.orgA, "SCOPE_2", "2024-02-15", "4")
        self._calc(self.orgB, "SCOPE_1", "2024-01-15", "99")

        self.viewer = self._user("viewer_a", self.orgA, Role.VIEWER)
        self.admin = self._user("admin_a", self.orgA, Role.ORG_ADMIN)
        self.analyst = self._user("analyst_a", self.orgA, Role.ANALYST)
        self.super = User.objects.create_superuser("root", "root@x.com", "pw")

    def _user(self, name, org, role):
        u = User.objects.create_user(name, password="pw")
        Membership.objects.create(user=u, organization=org, role=role, active=True)
        return u

    def _calc(self, org, scope, date_str, tonnes, status=RS.CALCULATED):
        self._i += 1
        rec = EmissionRecord.objects.create(
            organization=org, batch=self.batch, row_index=self._i,
            raw_data_payload={"x": 1}, status=EmissionRecord.RecordStatus.DRAFT,
            normalized_value=Decimal("1"), normalized_unit="L", scope_category=scope,
        )
        d = date.fromisoformat(date_str)
        return EmissionCalculation.objects.create(
            organization=org, emission_record=rec, is_current=True, scope=scope,
            reporting_date=d, reporting_month=d.replace(day=1),
            co2e_tonnes=Decimal(tonnes), co2e_kg=Decimal(tonnes) * 1000, resolution_status=status,
        )

    # --- auth + scoping ---
    def test_summary_requires_auth(self):
        self.assertEqual(self.client.get("/api/metrics/summary/").status_code, drf.HTTP_401_UNAUTHORIZED)

    def test_summary_is_tenant_scoped(self):
        # Compared as Decimal, not raw string: EmissionCalculation.co2e_tonnes
        # is DecimalField(decimal_places=9), and Sum()'s returned scale is a
        # DB-engine detail (Postgres preserves the full declared precision —
        # e.g. "14.000000000" — SQLite's aggregation does not), not part of
        # this API's actual contract. The frontend already only ever
        # Number()/parseFloat()s this field (never compares the raw string),
        # confirming the string's exact digit count was never a real
        # contract to begin with.
        self.client.force_authenticate(self.viewer)
        data = self.client.get("/api/metrics/summary/").json()
        self.assertEqual(Decimal(data["total_co2e_tonnes"]), Decimal("14"))  # 10 + 4 (Org B's 99 excluded)
        self.assertEqual(Decimal(data["by_scope"]["SCOPE_1"]), Decimal("10"))

    def test_timeseries(self):
        self.client.force_authenticate(self.viewer)
        rows = self.client.get("/api/metrics/timeseries/?bucket=month").json()
        by_month = {r["period"][:7]: r["co2e_tonnes"] for r in rows}
        self.assertEqual(Decimal(by_month["2024-01"]), Decimal("10"))
        self.assertEqual(Decimal(by_month["2024-02"]), Decimal("4"))

    def test_breakdown(self):
        self.client.force_authenticate(self.viewer)
        rows = self.client.get("/api/metrics/breakdown/?dimension=scope").json()
        self.assertEqual(rows[0]["key"], "SCOPE_1")

    # --- activity feed RBAC ---
    def test_activity_allowed_for_admin(self):
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get("/api/metrics/activity/").status_code, drf.HTTP_200_OK)

    def test_activity_denied_for_analyst(self):
        self.client.force_authenticate(self.analyst)
        self.assertEqual(self.client.get("/api/metrics/activity/").status_code, drf.HTTP_403_FORBIDDEN)

    # --- platform RBAC + cross-tenant ---
    def test_platform_denied_for_non_superuser(self):
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get("/api/metrics/platform/").status_code, drf.HTTP_403_FORBIDDEN)

    def test_platform_cross_tenant_for_superuser(self):
        self.client.force_authenticate(self.super)
        data = self.client.get("/api/metrics/platform/").json()
        self.assertEqual(data["totals"]["organizations"], 2)
        # See test_summary_is_tenant_scoped's comment — Decimal-compared, not
        # a raw string match, since the exact trailing-zero scale is a
        # DB-engine (Postgres vs SQLite) detail, not this API's contract.
        self.assertEqual(Decimal(data["totals"]["total_co2e_tonnes"]), Decimal("113"))  # 10 + 4 + 99 across all orgs
        names = {o["name"] for o in data["organizations"]}
        self.assertEqual(names, {"Org A", "Org B"})
