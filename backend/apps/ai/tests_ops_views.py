"""
Phase 7g -- apps.ai.ops_views API tests. First real end-to-end API-level
coverage for CanViewAICosts (previously a unit-tested-only inert seam --
see apps.ai.tests_permissions).
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status as drf_status
from rest_framework.test import APIClient

from apps.accounts.models import Membership, Role
from apps.ai.models import AIInteraction, TenantAIPolicy
from apps.core.models import Organization

User = get_user_model()


def _make_interaction(org, **extra):
    defaults = dict(
        organization=org, capability="anomaly_detection", provider="echo", model_id="echo-1",
        outcome=AIInteraction.Outcome.OK, egress_tier_applied=TenantAIPolicy.EgressTier.REDACTED,
    )
    defaults.update(extra)
    return AIInteraction.objects.create(**defaults)


class OpsAPITestBase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.org = Organization.objects.create(name="Ops API Org")
        self.org_admin = self._user("ops_api_admin", Role.ORG_ADMIN)
        self.auditor = self._user("ops_api_auditor", Role.AUDITOR)
        self.analyst = self._user("ops_api_analyst", Role.ANALYST)
        self.viewer = self._user("ops_api_viewer", Role.VIEWER)
        self.platform_admin = User.objects.create_superuser("ops_api_platform_admin", password="pw")

    def _user(self, name, role):
        u = User.objects.create_user(name, password="pw")
        Membership.objects.create(user=u, organization=self.org, role=role, active=True)
        return u


class AIObservabilityViewTests(OpsAPITestBase):
    def test_platform_admin_can_view(self):
        _make_interaction(self.org)
        self.client.force_authenticate(self.platform_admin)
        response = self.client.get("/api/ai/ops/observability/")
        self.assertEqual(response.status_code, drf_status.HTTP_200_OK)
        self.assertEqual(response.json()["requests"]["total"], 1)

    def test_org_admin_is_denied(self):
        self.client.force_authenticate(self.org_admin)
        response = self.client.get("/api/ai/ops/observability/")
        self.assertEqual(response.status_code, drf_status.HTTP_403_FORBIDDEN)

    def test_unauthenticated_is_denied(self):
        response = self.client.get("/api/ai/ops/observability/")
        self.assertIn(response.status_code, (drf_status.HTTP_401_UNAUTHORIZED, drf_status.HTTP_403_FORBIDDEN))

    def test_date_filters_are_forwarded(self):
        import datetime

        old = _make_interaction(self.org)
        AIInteraction.objects.filter(pk=old.pk).update(
            created_at=datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
        )
        _make_interaction(self.org)

        self.client.force_authenticate(self.platform_admin)
        response = self.client.get("/api/ai/ops/observability/", {"date_from": "2025-01-01T00:00:00Z"})
        self.assertEqual(response.status_code, drf_status.HTTP_200_OK)
        self.assertEqual(response.json()["requests"]["total"], 1)


class AIOpsHealthViewTests(OpsAPITestBase):
    def test_platform_admin_can_view(self):
        self.client.force_authenticate(self.platform_admin)
        response = self.client.get("/api/ai/ops/health/")
        self.assertEqual(response.status_code, drf_status.HTTP_200_OK)
        body = response.json()
        for key in ("ai_provider", "ai_heartbeat", "queue_depth", "evaluation", "replay_provider"):
            self.assertIn(key, body)

    def test_org_admin_is_denied(self):
        self.client.force_authenticate(self.org_admin)
        response = self.client.get("/api/ai/ops/health/")
        self.assertEqual(response.status_code, drf_status.HTTP_403_FORBIDDEN)

    def test_never_makes_a_real_provider_call(self):
        self.client.force_authenticate(self.platform_admin)
        self.client.get("/api/ai/ops/health/")
        self.assertEqual(AIInteraction.objects.count(), 0)


class AICostGovernanceViewTests(OpsAPITestBase):
    def test_org_admin_can_view(self):
        TenantAIPolicy.objects.create(organization=self.org, ai_enabled=True, monthly_budget_usd="50.00")
        _make_interaction(self.org, cost_usd="1.000000")
        self.client.force_authenticate(self.org_admin)
        response = self.client.get("/api/ai/costs/")
        self.assertEqual(response.status_code, drf_status.HTTP_200_OK)
        self.assertEqual(response.json()["estimated_spend_usd"], "1.000000")

    def test_auditor_can_view(self):
        self.client.force_authenticate(self.auditor)
        response = self.client.get("/api/ai/costs/")
        self.assertEqual(response.status_code, drf_status.HTTP_200_OK)

    def test_analyst_is_denied(self):
        self.client.force_authenticate(self.analyst)
        response = self.client.get("/api/ai/costs/")
        self.assertEqual(response.status_code, drf_status.HTTP_403_FORBIDDEN)

    def test_viewer_is_denied(self):
        self.client.force_authenticate(self.viewer)
        response = self.client.get("/api/ai/costs/")
        self.assertEqual(response.status_code, drf_status.HTTP_403_FORBIDDEN)

    def test_platform_admin_without_membership_must_select_org(self):
        self.client.force_authenticate(self.platform_admin)
        response = self.client.get("/api/ai/costs/")
        self.assertEqual(response.status_code, drf_status.HTTP_403_FORBIDDEN)

    def test_scoped_to_active_organization_only(self):
        other_org = Organization.objects.create(name="Ops API Other Org")
        _make_interaction(other_org, cost_usd="999.000000")
        self.client.force_authenticate(self.org_admin)
        response = self.client.get("/api/ai/costs/")
        self.assertEqual(response.json()["estimated_spend_usd"], "0.000000")
