"""
Phase 7f -- apps.ai's report-narration API tests. RBAC mirrors
apps.carbon.tests.test_reports.ComplianceReportRBACTests exactly (Org
Admin/Auditor allowed, Analyst/Viewer denied) since narration is
advisory content about that same gated compliance report.
"""
from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status as drf_status
from rest_framework.test import APIClient

from apps.accounts.models import Membership, Role
from apps.ai.models import AIInteraction, AIReportNarration, TenantAIPolicy
from apps.core.models import Organization

User = get_user_model()


def _make_interaction(org):
    return AIInteraction.objects.create(
        organization=org, capability="report_narration", provider="echo", model_id="echo-1",
        outcome=AIInteraction.Outcome.OK, egress_tier_applied=TenantAIPolicy.EgressTier.REDACTED,
    )


class ReportNarrationAPITestBase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.org = Organization.objects.create(name="Report Narration API Org")
        self.org_admin = self._user("report_api_admin", Role.ORG_ADMIN)
        self.auditor = self._user("report_api_auditor", Role.AUDITOR)
        self.analyst = self._user("report_api_analyst", Role.ANALYST)
        self.viewer = self._user("report_api_viewer", Role.VIEWER)

    def _user(self, name, role):
        u = User.objects.create_user(name, password="pw")
        Membership.objects.create(user=u, organization=self.org, role=role, active=True)
        return u


class ReportNarrationListAPITests(ReportNarrationAPITestBase):
    def test_empty_list_when_no_narrations_exist(self):
        self.client.force_authenticate(self.org_admin)
        response = self.client.get("/api/report-narration/")
        self.assertEqual(response.status_code, drf_status.HTTP_200_OK)
        self.assertEqual(response.json(), [])

    def test_returns_narrations_newest_first(self):
        import time

        first = AIReportNarration.objects.create(
            organization=self.org, interaction=_make_interaction(self.org),
            date_from=date(2026, 1, 1), date_to=date(2026, 1, 31), scope="",
            executive_summary="first", trend_explanations="x", confidence=AIReportNarration.Confidence.LOW,
        )
        time.sleep(0.01)
        second = AIReportNarration.objects.create(
            organization=self.org, interaction=_make_interaction(self.org),
            date_from=date(2026, 2, 1), date_to=date(2026, 2, 28), scope="",
            executive_summary="second", trend_explanations="x", confidence=AIReportNarration.Confidence.HIGH,
        )
        self.client.force_authenticate(self.org_admin)
        response = self.client.get("/api/report-narration/")
        body = response.json()
        self.assertEqual(len(body), 2)
        self.assertEqual(body[0]["id"], str(second.id))
        self.assertEqual(body[1]["id"], str(first.id))

    def test_response_shape_includes_all_advisory_fields(self):
        AIReportNarration.objects.create(
            organization=self.org, interaction=_make_interaction(self.org),
            date_from=date(2026, 1, 1), date_to=date(2026, 3, 31), scope="SCOPE_1",
            executive_summary="Emissions declined.",
            key_highlights=["highlight one"],
            trend_explanations="Steady monthly decline.",
            recommendations=["Investigate the decline."],
            confidence=AIReportNarration.Confidence.HIGH,
        )
        self.client.force_authenticate(self.org_admin)
        response = self.client.get("/api/report-narration/")
        body = response.json()[0]
        self.assertEqual(body["executive_summary"], "Emissions declined.")
        self.assertEqual(body["key_highlights"], ["highlight one"])
        self.assertEqual(body["recommendations"], ["Investigate the decline."])
        self.assertEqual(body["scope"], "SCOPE_1")
        self.assertEqual(body["confidence"], "HIGH")
        self.assertIn("created_at", body)

    def test_filters_by_date_range_and_scope(self):
        AIReportNarration.objects.create(
            organization=self.org, interaction=_make_interaction(self.org),
            date_from=date(2026, 1, 1), date_to=date(2026, 1, 31), scope="SCOPE_1",
            executive_summary="x", trend_explanations="x", confidence=AIReportNarration.Confidence.LOW,
        )
        AIReportNarration.objects.create(
            organization=self.org, interaction=_make_interaction(self.org),
            date_from=date(2026, 2, 1), date_to=date(2026, 2, 28), scope="SCOPE_2",
            executive_summary="y", trend_explanations="y", confidence=AIReportNarration.Confidence.LOW,
        )
        self.client.force_authenticate(self.org_admin)
        response = self.client.get(
            "/api/report-narration/?date_from=2026-01-01&date_to=2026-01-31&scope=SCOPE_1"
        )
        body = response.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["executive_summary"], "x")

    def test_cross_tenant_narration_is_not_visible(self):
        other_org = Organization.objects.create(name="Other Report Narration Org")
        AIReportNarration.objects.create(
            organization=other_org, interaction=_make_interaction(other_org),
            date_from=date(2026, 1, 1), date_to=date(2026, 1, 31), scope="",
            executive_summary="secret", trend_explanations="x", confidence=AIReportNarration.Confidence.LOW,
        )
        self.client.force_authenticate(self.org_admin)
        response = self.client.get("/api/report-narration/")
        self.assertEqual(response.json(), [])

    def test_no_mutation_verb_is_accepted(self):
        self.client.force_authenticate(self.org_admin)
        for method in (self.client.post, self.client.put, self.client.patch, self.client.delete):
            with self.subTest(method=method):
                response = method("/api/report-narration/", data={}, format="json")
                self.assertEqual(response.status_code, drf_status.HTTP_405_METHOD_NOT_ALLOWED)


class ReportNarrationRBACTests(ReportNarrationAPITestBase):
    def test_org_admin_allowed(self):
        self.client.force_authenticate(self.org_admin)
        response = self.client.get("/api/report-narration/")
        self.assertEqual(response.status_code, drf_status.HTTP_200_OK)

    def test_auditor_allowed(self):
        self.client.force_authenticate(self.auditor)
        response = self.client.get("/api/report-narration/")
        self.assertEqual(response.status_code, drf_status.HTTP_200_OK)

    def test_analyst_denied(self):
        self.client.force_authenticate(self.analyst)
        response = self.client.get("/api/report-narration/")
        self.assertEqual(response.status_code, drf_status.HTTP_403_FORBIDDEN)

    def test_viewer_denied(self):
        self.client.force_authenticate(self.viewer)
        response = self.client.get("/api/report-narration/")
        self.assertEqual(response.status_code, drf_status.HTTP_403_FORBIDDEN)

    def test_unauthenticated_denied(self):
        response = self.client.get("/api/report-narration/")
        self.assertEqual(response.status_code, drf_status.HTTP_401_UNAUTHORIZED)

    def test_regenerate_same_rbac(self):
        self.client.force_authenticate(self.analyst)
        response = self.client.post(
            "/api/report-narration/regenerate/",
            {"date_from": "2026-01-01", "date_to": "2026-01-31"}, format="json",
        )
        self.assertEqual(response.status_code, drf_status.HTTP_403_FORBIDDEN)


class ReportNarrationRegenerateAPITests(ReportNarrationAPITestBase):
    def test_regenerate_dispatches_the_task_and_returns_202(self):
        self.client.force_authenticate(self.org_admin)
        with patch("apps.ai.tasks.generate_report_narration_task.delay") as mocked:
            response = self.client.post(
                "/api/report-narration/regenerate/",
                {"date_from": "2026-01-01", "date_to": "2026-03-31", "scope": "SCOPE_1"}, format="json",
            )
        self.assertEqual(response.status_code, drf_status.HTTP_202_ACCEPTED)
        mocked.assert_called_once_with(
            organization_id=str(self.org.id), date_from="2026-01-01", date_to="2026-03-31",
            scope="SCOPE_1", actor_id=str(self.org_admin.id),
        )

    def test_regenerate_never_creates_a_narration_synchronously(self):
        self.client.force_authenticate(self.org_admin)
        with patch("apps.ai.tasks.generate_report_narration_task.delay"):
            self.client.post(
                "/api/report-narration/regenerate/",
                {"date_from": "2026-01-01", "date_to": "2026-01-31"}, format="json",
            )
        self.assertEqual(AIReportNarration.objects.count(), 0)

    def test_date_from_after_date_to_rejected(self):
        self.client.force_authenticate(self.org_admin)
        response = self.client.post(
            "/api/report-narration/regenerate/",
            {"date_from": "2026-02-01", "date_to": "2026-01-01"}, format="json",
        )
        self.assertEqual(response.status_code, drf_status.HTTP_400_BAD_REQUEST)

    def test_regenerate_never_touches_governed_models(self):
        from apps.carbon.models import EmissionCalculation, EmissionFactor
        from apps.ingestion.models import EmissionRecord

        before_records = EmissionRecord.objects.count()
        before_calcs = EmissionCalculation.objects.count()
        before_factors = EmissionFactor.objects.count()

        self.client.force_authenticate(self.org_admin)
        with patch("apps.ai.tasks.generate_report_narration_task.delay"):
            self.client.post(
                "/api/report-narration/regenerate/",
                {"date_from": "2026-01-01", "date_to": "2026-01-31"}, format="json",
            )

        self.assertEqual(EmissionRecord.objects.count(), before_records)
        self.assertEqual(EmissionCalculation.objects.count(), before_calcs)
        self.assertEqual(EmissionFactor.objects.count(), before_factors)
