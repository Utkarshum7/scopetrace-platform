"""
Phase 6e — compliance reports: correctness (APPROVED-only, is_current
CALCULATED-only), tenant isolation, RBAC, N+1 avoidance on large datasets,
historical version consistency, and the CSV export path. See
docs/adr/0002-compliance-reports-on-demand-not-persisted.md for the design.
"""
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework import status as drf
from rest_framework.test import APIClient

from apps.accounts.models import Membership, Role
from apps.carbon.models import EmissionCalculation
from apps.core.models import DataSource, Organization
from apps.ingestion.models import EmissionRecord, UploadBatch

User = get_user_model()
RS = EmissionCalculation.ResolutionStatus


class ComplianceReportTestBase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.org = Organization.objects.create(name="Compliance Org")
        self.other_org = Organization.objects.create(name="Other Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL
        )
        self.batch = UploadBatch.objects.create(
            organization=self.org, data_source=self.ds, file_name="b.csv"
        )
        self._i = 0

        self.org_admin = self._user("org_admin", self.org, Role.ORG_ADMIN)
        self.auditor = self._user("auditor", self.org, Role.AUDITOR)
        self.analyst = self._user("analyst", self.org, Role.ANALYST)
        self.viewer = self._user("viewer", self.org, Role.VIEWER)

    def _user(self, name, org, role):
        u = User.objects.create_user(name, password="pw")
        Membership.objects.create(user=u, organization=org, role=role, active=True)
        return u

    def _record(self, org=None, status=EmissionRecord.RecordStatus.APPROVED, approved_by=None):
        self._i += 1
        org = org or self.org
        return EmissionRecord.objects.create(
            organization=org, batch=self.batch if org == self.org else self._other_batch(),
            row_index=self._i, raw_data_payload={"x": 1}, status=status,
            normalized_value=Decimal("100"), normalized_unit="L", scope_category="SCOPE_1",
            approved_by=approved_by, approved_at=timezone.now() if approved_by else None,
        )

    def _other_batch(self):
        if not hasattr(self, "_other_batch_obj"):
            other_ds = DataSource.objects.create(
                organization=self.other_org, name="SAP-B", source_type=DataSource.SourceType.SAP_FUEL
            )
            self._other_batch_obj = UploadBatch.objects.create(
                organization=self.other_org, data_source=other_ds, file_name="ob.csv"
            )
        return self._other_batch_obj

    def _calc(self, record, scope="SCOPE_1", d="2026-01-15", tonnes="10", is_current=True,
              status=RS.CALCULATED):
        rd = date.fromisoformat(d)
        return EmissionCalculation.objects.create(
            organization=record.organization, emission_record=record, is_current=is_current,
            scope=scope, reporting_date=rd, reporting_month=rd.replace(day=1),
            co2e_tonnes=Decimal(tonnes), co2e_kg=Decimal(tonnes) * 1000, resolution_status=status,
            activity_quantity=Decimal("500"), activity_unit="L",
            factor_publisher="DEFRA", factor_version="2024",
        )


class ComplianceReportCorrectnessTests(ComplianceReportTestBase):
    def test_only_approved_records_are_included(self):
        approved = self._record(status=EmissionRecord.RecordStatus.APPROVED, approved_by=self.org_admin)
        self._calc(approved)
        draft = self._record(status=EmissionRecord.RecordStatus.DRAFT)
        self._calc(draft)
        submitted = self._record(status=EmissionRecord.RecordStatus.SUBMITTED)
        self._calc(submitted)

        self.client.force_authenticate(self.org_admin)
        data = self.client.get(
            "/api/reports/compliance/?date_from=2026-01-01&date_to=2026-01-31"
        ).json()
        self.assertEqual(data["line_item_count"], 1)
        self.assertEqual(data["line_items"][0]["record_id"], str(approved.id))

    def test_superseded_calculation_excluded(self):
        record = self._record(approved_by=self.org_admin)
        old = self._calc(record, tonnes="5", is_current=False)
        current = self._calc(record, tonnes="8", is_current=True)
        self.assertNotEqual(old.id, current.id)

        self.client.force_authenticate(self.org_admin)
        data = self.client.get(
            "/api/reports/compliance/?date_from=2026-01-01&date_to=2026-01-31"
        ).json()
        self.assertEqual(data["line_item_count"], 1)
        self.assertEqual(data["line_items"][0]["calculation_id"], str(current.id))
        self.assertEqual(Decimal(data["line_items"][0]["co2e_tonnes"]), Decimal("8"))

    def test_unresolved_calculation_excluded(self):
        record = self._record(approved_by=self.org_admin)
        self._calc(record, status=RS.UNRESOLVED_NO_FACTOR)

        self.client.force_authenticate(self.org_admin)
        data = self.client.get(
            "/api/reports/compliance/?date_from=2026-01-01&date_to=2026-01-31"
        ).json()
        self.assertEqual(data["line_item_count"], 0)

    def test_date_range_filters_correctly(self):
        record = self._record(approved_by=self.org_admin)
        self._calc(record, d="2026-01-15")
        record2 = self._record(approved_by=self.org_admin)
        self._calc(record2, d="2026-03-15")

        self.client.force_authenticate(self.org_admin)
        data = self.client.get(
            "/api/reports/compliance/?date_from=2026-01-01&date_to=2026-01-31"
        ).json()
        self.assertEqual(data["line_item_count"], 1)

    def test_scope_filter(self):
        record = self._record(approved_by=self.org_admin)
        self._calc(record, scope="SCOPE_1")
        record2 = self._record(approved_by=self.org_admin)
        self._calc(record2, scope="SCOPE_2")

        self.client.force_authenticate(self.org_admin)
        data = self.client.get(
            "/api/reports/compliance/?date_from=2026-01-01&date_to=2026-01-31&scope=SCOPE_2"
        ).json()
        self.assertEqual(data["line_item_count"], 1)
        self.assertEqual(data["line_items"][0]["scope"], "SCOPE_2")

    def test_summary_totals_and_by_scope(self):
        r1 = self._record(approved_by=self.org_admin)
        self._calc(r1, scope="SCOPE_1", tonnes="10")
        r2 = self._record(approved_by=self.org_admin)
        self._calc(r2, scope="SCOPE_2", tonnes="4")

        self.client.force_authenticate(self.org_admin)
        data = self.client.get(
            "/api/reports/compliance/?date_from=2026-01-01&date_to=2026-01-31"
        ).json()
        self.assertEqual(Decimal(data["summary"]["total_co2e_tonnes"]), Decimal("14"))
        self.assertEqual(data["summary"]["record_count"], 2)
        self.assertEqual(Decimal(data["summary"]["by_scope"]["SCOPE_1"]), Decimal("10"))

    def test_audit_chain_snapshot_present_and_valid(self):
        self.client.force_authenticate(self.org_admin)
        data = self.client.get(
            "/api/reports/compliance/?date_from=2026-01-01&date_to=2026-01-31"
        ).json()
        self.assertIn("audit_chain", data)
        self.assertTrue(data["audit_chain"]["valid"])

    def test_metadata_fields_present(self):
        self.client.force_authenticate(self.org_admin)
        data = self.client.get(
            "/api/reports/compliance/?date_from=2026-01-01&date_to=2026-01-31"
        ).json()
        self.assertEqual(data["organization"]["id"], str(self.org.id))
        self.assertEqual(data["period"], {"date_from": "2026-01-01", "date_to": "2026-01-31"})
        self.assertEqual(data["generated_by"], "org_admin")
        self.assertIn("generated_at", data)

    def test_date_range_is_required(self):
        self.client.force_authenticate(self.org_admin)
        response = self.client.get("/api/reports/compliance/")
        self.assertEqual(response.status_code, drf.HTTP_400_BAD_REQUEST)

    def test_generation_is_logged(self):
        # Phase 6f: INFO-level observability (who generated what, for
        # which org/period) -- deliberately NOT an AuditTrail entry, see
        # docs/adr/0002-compliance-reports-on-demand-not-persisted.md.
        self.client.force_authenticate(self.org_admin)
        with self.assertLogs("apps.carbon.report_views", level="INFO") as ctx:
            self.client.get(
                "/api/reports/compliance/?date_from=2026-01-01&date_to=2026-01-31"
            )
        self.assertIn("org_admin", ctx.output[0])
        self.assertIn(str(self.org.id), ctx.output[0])

    def test_date_from_after_date_to_is_rejected(self):
        self.client.force_authenticate(self.org_admin)
        response = self.client.get(
            "/api/reports/compliance/?date_from=2026-02-01&date_to=2026-01-01"
        )
        self.assertEqual(response.status_code, drf.HTTP_400_BAD_REQUEST)


class ComplianceReportVersionConsistencyTests(ComplianceReportTestBase):
    def test_record_version_reflects_the_approved_snapshot(self):
        record = self._record(status=EmissionRecord.RecordStatus.DRAFT)
        # DRAFT -> SUBMITTED -> APPROVED, each a real save() producing a
        # new EmissionRecordVersion (Phase 6b), matching how the workflow
        # (Phase 6c) actually drives this in production.
        record.status = EmissionRecord.RecordStatus.SUBMITTED
        record.save()
        record.status = EmissionRecord.RecordStatus.APPROVED
        record.approved_by = self.org_admin
        record.save()
        self._calc(record)

        expected_version = record.versions.order_by("-version_number").first().version_number
        self.assertEqual(expected_version, 3)  # create, submit, approve

        self.client.force_authenticate(self.org_admin)
        data = self.client.get(
            "/api/reports/compliance/?date_from=2026-01-01&date_to=2026-01-31"
        ).json()
        self.assertEqual(data["line_items"][0]["record_version"], expected_version)

    def test_calculation_id_traces_back_to_an_immutable_row(self):
        record = self._record(approved_by=self.org_admin)
        calc = self._calc(record)

        self.client.force_authenticate(self.org_admin)
        data = self.client.get(
            "/api/reports/compliance/?date_from=2026-01-01&date_to=2026-01-31"
        ).json()
        line_item = data["line_items"][0]
        self.assertEqual(line_item["calculation_id"], str(calc.id))
        # The referenced row is still readable, unchanged, directly.
        fetched = EmissionCalculation.objects.get(pk=line_item["calculation_id"])
        self.assertEqual(fetched.co2e_tonnes, calc.co2e_tonnes)


class ComplianceReportTenantIsolationTests(ComplianceReportTestBase):
    def test_other_org_records_excluded(self):
        mine = self._record(approved_by=self.org_admin)
        self._calc(mine)
        theirs = self._record(org=self.other_org)  # already APPROVED at creation (default)
        self._calc(theirs)

        self.client.force_authenticate(self.org_admin)
        data = self.client.get(
            "/api/reports/compliance/?date_from=2026-01-01&date_to=2026-01-31"
        ).json()
        self.assertEqual(data["line_item_count"], 1)
        self.assertEqual(data["line_items"][0]["record_id"], str(mine.id))

    def test_platform_admin_without_org_header_is_rejected(self):
        superuser = User.objects.create_superuser("root", "root@x.com", "pw")
        self.client.force_authenticate(superuser)
        response = self.client.get(
            "/api/reports/compliance/?date_from=2026-01-01&date_to=2026-01-31"
        )
        self.assertEqual(response.status_code, drf.HTTP_403_FORBIDDEN)


class ComplianceReportRBACTests(ComplianceReportTestBase):
    def test_org_admin_allowed(self):
        self.client.force_authenticate(self.org_admin)
        r = self.client.get("/api/reports/compliance/?date_from=2026-01-01&date_to=2026-01-31")
        self.assertEqual(r.status_code, drf.HTTP_200_OK)

    def test_auditor_allowed(self):
        self.client.force_authenticate(self.auditor)
        r = self.client.get("/api/reports/compliance/?date_from=2026-01-01&date_to=2026-01-31")
        self.assertEqual(r.status_code, drf.HTTP_200_OK)

    def test_analyst_denied(self):
        self.client.force_authenticate(self.analyst)
        r = self.client.get("/api/reports/compliance/?date_from=2026-01-01&date_to=2026-01-31")
        self.assertEqual(r.status_code, drf.HTTP_403_FORBIDDEN)

    def test_viewer_denied(self):
        self.client.force_authenticate(self.viewer)
        r = self.client.get("/api/reports/compliance/?date_from=2026-01-01&date_to=2026-01-31")
        self.assertEqual(r.status_code, drf.HTTP_403_FORBIDDEN)

    def test_unauthenticated_denied(self):
        r = self.client.get("/api/reports/compliance/?date_from=2026-01-01&date_to=2026-01-31")
        self.assertEqual(r.status_code, drf.HTTP_401_UNAUTHORIZED)

    def test_csv_endpoint_same_rbac(self):
        self.client.force_authenticate(self.analyst)
        r = self.client.get("/api/reports/compliance/csv/?date_from=2026-01-01&date_to=2026-01-31")
        self.assertEqual(r.status_code, drf.HTTP_403_FORBIDDEN)


class ComplianceReportCSVTests(ComplianceReportTestBase):
    def _body(self, response):
        return b"".join(response.streaming_content).decode("utf-8")

    def test_csv_streams_line_items(self):
        record = self._record(approved_by=self.org_admin)
        self._calc(record)

        self.client.force_authenticate(self.org_admin)
        response = self.client.get(
            "/api/reports/compliance/csv/?date_from=2026-01-01&date_to=2026-01-31"
        )
        self.assertEqual(response.status_code, drf.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn("attachment; filename=", response["Content-Disposition"])
        body = self._body(response)
        lines = [ln for ln in body.splitlines() if ln]
        self.assertTrue(lines[0].startswith("record_id,"))
        self.assertEqual(len(lines), 2)  # header + 1 line item
        self.assertIn(str(record.id), body)

    def test_csv_empty_range_returns_header_only(self):
        self.client.force_authenticate(self.org_admin)
        response = self.client.get(
            "/api/reports/compliance/csv/?date_from=2026-01-01&date_to=2026-01-31"
        )
        body = self._body(response)
        lines = [ln for ln in body.splitlines() if ln]
        self.assertEqual(len(lines), 1)  # header only


class ComplianceReportLargeDatasetTests(ComplianceReportTestBase):
    def test_no_n_plus_1_regardless_of_row_count(self):
        # 300 approved records across 3 scopes, 3 reporting dates -- enough
        # to make an N+1 (one query per row) obviously blow up query count.
        records = []
        for i in range(300):
            r = self._record(approved_by=self.org_admin)
            records.append(r)
        for i, r in enumerate(records):
            self._calc(
                r, scope=["SCOPE_1", "SCOPE_2", "SCOPE_3"][i % 3],
                d=["2026-01-05", "2026-01-15", "2026-01-25"][i % 3],
                tonnes=str(i + 1),
            )

        self.client.force_authenticate(self.org_admin)
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(
                "/api/reports/compliance/?date_from=2026-01-01&date_to=2026-01-31"
            )
        self.assertEqual(response.status_code, drf.HTTP_200_OK)
        self.assertEqual(response.json()["line_item_count"], 300)
        # Bounded regardless of row count: permission/tenant resolution +
        # the count() query + the line-items query + the summary
        # aggregation queries + audit-chain lookups -- NOT ~300.
        self.assertLess(len(ctx.captured_queries), 20)

    def test_csv_handles_large_dataset_streamed(self):
        for i in range(300):
            r = self._record(approved_by=self.org_admin)
            self._calc(r, d="2026-01-15", tonnes="1")

        self.client.force_authenticate(self.org_admin)
        response = self.client.get(
            "/api/reports/compliance/csv/?date_from=2026-01-01&date_to=2026-01-31"
        )
        body = b"".join(response.streaming_content).decode("utf-8")
        lines = [ln for ln in body.splitlines() if ln]
        self.assertEqual(len(lines), 301)  # header + 300 rows
