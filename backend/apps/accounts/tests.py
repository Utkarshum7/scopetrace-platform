from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.test import TestCase
from rest_framework import status as drf
from rest_framework.test import APIClient

from apps.accounts.models import Membership, Role
from apps.core.models import DataSource, Organization
from apps.ingestion.models import EmissionRecord, UploadBatch

User = get_user_model()


def make_record(org, ds=None, status=EmissionRecord.RecordStatus.DRAFT, row_index=1):
    ds = ds or DataSource.objects.create(
        organization=org, name=f"DS-{org.name}", source_type=DataSource.SourceType.SAP_FUEL
    )
    batch = UploadBatch.objects.create(organization=org, data_source=ds, file_name="f.csv")
    return EmissionRecord.objects.create(
        organization=org,
        batch=batch,
        row_index=row_index,
        raw_data_payload={"seed": "test"},
        status=status,
    )


# ---------------------------------------------------------------------------
# Authentication flow
# ---------------------------------------------------------------------------
class AuthFlowTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.org = Organization.objects.create(name="Auth Org")
        self.user = User.objects.create_user(username="alice", password="pw12345678")
        Membership.objects.create(
            user=self.user, organization=self.org, role=Role.ANALYST, active=True
        )

    def _login(self):
        return self.client.post(
            "/api/auth/login/",
            {"username": "alice", "password": "pw12345678"},
            format="json",
        ).json()

    def test_login_returns_tokens_and_profile(self):
        r = self.client.post(
            "/api/auth/login/",
            {"username": "alice", "password": "pw12345678"},
            format="json",
        )
        self.assertEqual(r.status_code, drf.HTTP_200_OK)
        d = r.json()
        self.assertIn("access", d)
        self.assertIn("refresh", d)
        self.assertEqual(d["user"]["username"], "alice")
        self.assertEqual(d["user"]["memberships"][0]["role"], "ANALYST")

    def test_login_wrong_password_401(self):
        r = self.client.post(
            "/api/auth/login/", {"username": "alice", "password": "nope"}, format="json"
        )
        self.assertEqual(r.status_code, drf.HTTP_401_UNAUTHORIZED)

    def test_me_requires_authentication(self):
        self.assertEqual(self.client.get("/api/me/").status_code, drf.HTTP_401_UNAUTHORIZED)

    def test_me_returns_active_org_and_role(self):
        self.client.force_authenticate(self.user)
        d = self.client.get("/api/me/").json()
        self.assertEqual(d["username"], "alice")
        self.assertEqual(d["active_role"], "ANALYST")
        self.assertEqual(d["active_organization"]["name"], "Auth Org")
        self.assertFalse(d["is_platform_admin"])

    def test_refresh_issues_new_access(self):
        tokens = self._login()
        r = self.client.post("/api/auth/refresh/", {"refresh": tokens["refresh"]}, format="json")
        self.assertEqual(r.status_code, drf.HTTP_200_OK)
        self.assertIn("access", r.json())

    def test_logout_blacklists_refresh(self):
        tokens = self._login()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        r = self.client.post("/api/auth/logout/", {"refresh": tokens["refresh"]}, format="json")
        self.assertEqual(r.status_code, drf.HTTP_205_RESET_CONTENT)
        # The blacklisted refresh can no longer be used.
        self.client.credentials()
        reuse = self.client.post(
            "/api/auth/refresh/", {"refresh": tokens["refresh"]}, format="json"
        )
        self.assertEqual(reuse.status_code, drf.HTTP_401_UNAUTHORIZED)

    def test_logout_without_refresh_400(self):
        self.client.force_authenticate(self.user)
        r = self.client.post("/api/auth/logout/", {}, format="json")
        self.assertEqual(r.status_code, drf.HTTP_400_BAD_REQUEST)


# ---------------------------------------------------------------------------
# Unauthorized access
# ---------------------------------------------------------------------------
class UnauthorizedAccessTests(TestCase):
    def test_all_business_endpoints_require_auth(self):
        client = APIClient()
        for url in [
            "/api/datasources/",
            "/api/records/",
            "/api/batches/",
            "/api/organizations/",
            "/api/me/",
        ]:
            self.assertEqual(client.get(url).status_code, drf.HTTP_401_UNAUTHORIZED, url)


# ---------------------------------------------------------------------------
# Role restrictions (RBAC)
# ---------------------------------------------------------------------------
class RoleRestrictionTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.org = Organization.objects.create(name="Role Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL
        )
        self.users = {}
        for role in [Role.ORG_ADMIN, Role.ANALYST, Role.AUDITOR, Role.VIEWER]:
            u = User.objects.create_user(username=f"u_{role}", password="pw")
            Membership.objects.create(user=u, organization=self.org, role=role, active=True)
            self.users[role] = u

    def _sap_file(self):
        content = (
            "Werk;Buchungsdatum;Material;Materialkurztext;Menge;Einheit;Nettopreis\n"
            "DE01;01.01.2026;DSL;Diesel;500,00;L;750.00\n"
        ).encode("utf-8")
        return InMemoryUploadedFile(
            BytesIO(content), "file", "sap.csv", "text/csv", len(content), "utf-8"
        )

    def _upload_status(self, role):
        self.client.force_authenticate(self.users[role])
        resp = self.client.post(
            "/api/upload/sap/",
            {"file": self._sap_file(), "data_source": str(self.ds.id)},
            format="multipart",
        )
        return resp.status_code

    def test_analyst_can_upload(self):
        # Phase 5b: upload is now asynchronous — 202 Accepted, not 201.
        self.assertEqual(self._upload_status(Role.ANALYST), drf.HTTP_202_ACCEPTED)

    def test_org_admin_can_upload(self):
        self.assertEqual(self._upload_status(Role.ORG_ADMIN), drf.HTTP_202_ACCEPTED)

    def test_auditor_cannot_upload(self):
        self.assertEqual(self._upload_status(Role.AUDITOR), drf.HTTP_403_FORBIDDEN)

    def test_viewer_cannot_upload(self):
        self.assertEqual(self._upload_status(Role.VIEWER), drf.HTTP_403_FORBIDDEN)

    def _approve_status(self, role):
        # Phase 6c: approve() now requires SUBMITTED first -- create the
        # record already SUBMITTED so this test isolates approve()'s own
        # RBAC, independent of submit()'s (separate, narrower) RBAC.
        record = make_record(self.org, self.ds, status=EmissionRecord.RecordStatus.SUBMITTED)
        self.client.force_authenticate(self.users[role])
        return self.client.post(
            f"/api/records/{record.id}/approve/", {"reason": "ok"}, format="json"
        ).status_code

    def test_analyst_can_approve(self):
        self.assertEqual(self._approve_status(Role.ANALYST), drf.HTTP_200_OK)

    def test_auditor_can_approve(self):
        self.assertEqual(self._approve_status(Role.AUDITOR), drf.HTTP_200_OK)

    def test_viewer_cannot_approve(self):
        self.assertEqual(self._approve_status(Role.VIEWER), drf.HTTP_403_FORBIDDEN)

    def test_all_roles_can_read_records(self):
        make_record(self.org, self.ds)
        for role in self.users:
            self.client.force_authenticate(self.users[role])
            self.assertEqual(self.client.get("/api/records/").status_code, drf.HTTP_200_OK, role)


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------
class TenantIsolationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.orgA = Organization.objects.create(name="Org A")
        self.orgB = Organization.objects.create(name="Org B")

        self.userA = User.objects.create_user(username="userA", password="pw")
        Membership.objects.create(
            user=self.userA, organization=self.orgA, role=Role.ANALYST, active=True
        )

        self.dsA = DataSource.objects.create(
            organization=self.orgA, name="A-SAP", source_type=DataSource.SourceType.SAP_FUEL
        )
        self.dsB = DataSource.objects.create(
            organization=self.orgB, name="B-SAP", source_type=DataSource.SourceType.SAP_FUEL
        )
        self.recordA = make_record(self.orgA, self.dsA, row_index=1)
        self.recordB = make_record(self.orgB, self.dsB, row_index=1)

    def test_records_are_scoped_to_own_org(self):
        self.client.force_authenticate(self.userA)
        ids = [r["id"] for r in self.client.get("/api/records/").json()["results"]]
        self.assertIn(str(self.recordA.id), ids)
        self.assertNotIn(str(self.recordB.id), ids)

    def test_datasources_are_scoped(self):
        self.client.force_authenticate(self.userA)
        names = [d["name"] for d in self.client.get("/api/datasources/").json()]
        self.assertIn("A-SAP", names)
        self.assertNotIn("B-SAP", names)

    def test_organizations_scoped_to_memberships(self):
        self.client.force_authenticate(self.userA)
        names = [o["name"] for o in self.client.get("/api/organizations/").json()]
        self.assertEqual(names, ["Org A"])

    def test_cannot_retrieve_other_org_record(self):
        self.client.force_authenticate(self.userA)
        r = self.client.get(f"/api/records/{self.recordB.id}/")
        self.assertEqual(r.status_code, drf.HTTP_404_NOT_FOUND)

    def test_cannot_approve_other_org_record(self):
        self.client.force_authenticate(self.userA)
        r = self.client.post(f"/api/records/{self.recordB.id}/approve/", {}, format="json")
        self.assertEqual(r.status_code, drf.HTTP_403_FORBIDDEN)

    def test_organization_query_param_is_not_trusted(self):
        # Passing another org's id as a query param must NOT widen visibility.
        self.client.force_authenticate(self.userA)
        ids = [
            r["id"]
            for r in self.client.get(f"/api/records/?organization={self.orgB.id}").json()["results"]
        ]
        self.assertNotIn(str(self.recordB.id), ids)

    def test_header_for_non_member_org_denied(self):
        self.client.force_authenticate(self.userA)
        r = self.client.get("/api/records/", HTTP_X_ORGANIZATION_ID=str(self.orgB.id))
        self.assertEqual(r.status_code, drf.HTTP_403_FORBIDDEN)

    def test_inactive_membership_denied(self):
        Membership.objects.filter(user=self.userA).update(active=False)
        self.client.force_authenticate(self.userA)
        self.assertEqual(self.client.get("/api/records/").status_code, drf.HTTP_403_FORBIDDEN)


# ---------------------------------------------------------------------------
# Platform admin (superuser) cross-tenant access
# ---------------------------------------------------------------------------
class PlatformAdminTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.orgA = Organization.objects.create(name="Org A")
        self.orgB = Organization.objects.create(name="Org B")
        self.recordA = make_record(self.orgA, row_index=1)
        self.recordB = make_record(self.orgB, row_index=1)
        self.admin = User.objects.create_superuser("root", "root@x.com", "pw")

    def test_superuser_sees_all_orgs(self):
        self.client.force_authenticate(self.admin)
        ids = [r["id"] for r in self.client.get("/api/records/").json()["results"]]
        self.assertIn(str(self.recordA.id), ids)
        self.assertIn(str(self.recordB.id), ids)

    def test_superuser_can_scope_via_header(self):
        self.client.force_authenticate(self.admin)
        ids = [
            r["id"]
            for r in self.client.get(
                "/api/records/", HTTP_X_ORGANIZATION_ID=str(self.orgA.id)
            ).json()["results"]
        ]
        self.assertIn(str(self.recordA.id), ids)
        self.assertNotIn(str(self.recordB.id), ids)
