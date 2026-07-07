"""
Phase 7a -- CanUseAI / CanManageAIPolicy / CanViewAICosts RBAC tests.

No AI-specific DRF endpoint exists yet in 7a (only /healthz/ai, which is
unauthenticated infra, not gated by these classes) -- these are unit tests
against the permission classes directly via a bare request object, matching
how apps.accounts.tenancy.resolve_tenant_context only needs `.user` and
`.META`, not a real view. A feature milestone (7b+) that adds the first
real AI endpoint wires these in and gets real end-to-end API-level RBAC
tests for free at that point, exactly like every existing CanUpload/
CanApprove test in apps.ingestion.
"""
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from apps.accounts.models import Membership, Role
from apps.accounts.permissions import CanManageAIPolicy, CanUseAI, CanViewAICosts
from apps.core.models import Organization

User = get_user_model()


class PermissionTestBase(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.org = Organization.objects.create(name="RBAC AI Org")

    def _request_as(self, user):
        request = self.factory.get("/")
        request.user = user
        return request

    def _user(self, name, role):
        user = User.objects.create_user(name, password="pw")
        Membership.objects.create(user=user, organization=self.org, role=role, active=True)
        return user


class CanUseAITests(PermissionTestBase):
    def test_org_admin_can_use_ai(self):
        user = self._user("ai_org_admin", Role.ORG_ADMIN)
        self.assertTrue(CanUseAI().has_permission(self._request_as(user), None))

    def test_analyst_can_use_ai(self):
        user = self._user("ai_analyst", Role.ANALYST)
        self.assertTrue(CanUseAI().has_permission(self._request_as(user), None))

    def test_auditor_can_use_ai(self):
        user = self._user("ai_auditor", Role.AUDITOR)
        self.assertTrue(CanUseAI().has_permission(self._request_as(user), None))

    def test_viewer_cannot_use_ai(self):
        user = self._user("ai_viewer", Role.VIEWER)
        self.assertFalse(CanUseAI().has_permission(self._request_as(user), None))

    def test_platform_admin_can_use_ai(self):
        admin = User.objects.create_superuser("ai_platform_admin", password="pw")
        self.assertTrue(CanUseAI().has_permission(self._request_as(admin), None))

    def test_unauthenticated_user_cannot_use_ai(self):
        from django.contrib.auth.models import AnonymousUser

        request = self.factory.get("/")
        request.user = AnonymousUser()
        self.assertFalse(CanUseAI().has_permission(request, None))


class CanManageAIPolicyTests(PermissionTestBase):
    def test_org_admin_can_manage_ai_policy(self):
        user = self._user("policy_org_admin", Role.ORG_ADMIN)
        self.assertTrue(CanManageAIPolicy().has_permission(self._request_as(user), None))

    def test_analyst_cannot_manage_ai_policy(self):
        user = self._user("policy_analyst", Role.ANALYST)
        self.assertFalse(CanManageAIPolicy().has_permission(self._request_as(user), None))

    def test_auditor_cannot_manage_ai_policy(self):
        user = self._user("policy_auditor", Role.AUDITOR)
        self.assertFalse(CanManageAIPolicy().has_permission(self._request_as(user), None))

    def test_viewer_cannot_manage_ai_policy(self):
        user = self._user("policy_viewer", Role.VIEWER)
        self.assertFalse(CanManageAIPolicy().has_permission(self._request_as(user), None))


class CanViewAICostsTests(PermissionTestBase):
    def test_org_admin_can_view_ai_costs(self):
        user = self._user("costs_org_admin", Role.ORG_ADMIN)
        self.assertTrue(CanViewAICosts().has_permission(self._request_as(user), None))

    def test_auditor_can_view_ai_costs(self):
        user = self._user("costs_auditor", Role.AUDITOR)
        self.assertTrue(CanViewAICosts().has_permission(self._request_as(user), None))

    def test_analyst_cannot_view_ai_costs(self):
        user = self._user("costs_analyst", Role.ANALYST)
        self.assertFalse(CanViewAICosts().has_permission(self._request_as(user), None))

    def test_viewer_cannot_view_ai_costs(self):
        user = self._user("costs_viewer", Role.VIEWER)
        self.assertFalse(CanViewAICosts().has_permission(self._request_as(user), None))
