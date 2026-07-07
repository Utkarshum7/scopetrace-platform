"""
Phase 6d — soft deletion and record retention. Orthogonal to the approval
workflow (6c): is_deleted/deleted_at live alongside `status`, never
transitioning through it. See
docs/adr/0004-soft-delete-orthogonal-fields.md for the full design.

Covers: delete/restore mechanics, the APPROVED-record carve-out, hard-
delete being permanently blocked (instance + bulk, plus the on_delete
PROTECT changes on organization/batch/emission_record), tenant isolation,
RBAC, EmissionRecordVersion integration, audit hash-chain integration,
compliance reports preserving deleted records' history while dashboards/
active views exclude them, and real multi-threaded concurrent delete.
"""
import threading
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.models import ProtectedError
from django.test import TestCase, TransactionTestCase
from rest_framework import status as drf_status
from rest_framework.test import APIClient

from apps.accounts.models import Membership, Role
from apps.audit.models import AuditTrail
from apps.audit.services import verify_chain
from apps.carbon.models import EmissionCalculation
from apps.carbon.services.metrics_cache import get_calc_version
from apps.core.models import DataSource, Organization
from apps.ingestion.models import EmissionRecord, EmissionRecordVersion, UploadBatch
from apps.ingestion.services.soft_delete import (
    AlreadyDeletedError,
    NotDeletedError,
    restore_record,
    soft_delete_record,
)

User = get_user_model()
RS = EmissionRecord.RecordStatus


def _make_batch(org, data_source):
    return UploadBatch.objects.create(organization=org, data_source=data_source, file_name="sd.csv")


def _make_record(org, batch, status=RS.DRAFT, row_index=1, **extra):
    return EmissionRecord.objects.create(
        organization=org, batch=batch, row_index=row_index,
        raw_data_payload={"a": 1}, status=status,
        normalized_value=100, normalized_unit="L", **extra,
    )


class SoftDeleteMechanicsTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Soft Delete Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL,
        )
        self.batch = _make_batch(self.org, self.ds)

    def test_soft_delete_sets_flags_and_hides_from_default_manager(self):
        record = _make_record(self.org, self.batch)
        soft_delete_record(record=record, actor=None, reason="duplicate entry")
        self.assertTrue(record.is_deleted)
        self.assertIsNotNone(record.deleted_at)

        self.assertFalse(EmissionRecord.objects.filter(pk=record.pk).exists())
        self.assertTrue(EmissionRecord.all_objects.filter(pk=record.pk).exists())

    def test_restore_clears_flags_and_status_is_untouched(self):
        record = _make_record(self.org, self.batch, status=RS.SUBMITTED)
        soft_delete_record(record=record, actor=None, reason="oops")
        restore_record(record=record, actor=None)

        self.assertFalse(record.is_deleted)
        self.assertIsNone(record.deleted_at)
        self.assertEqual(record.status, RS.SUBMITTED)  # untouched by delete/restore
        self.assertTrue(EmissionRecord.objects.filter(pk=record.pk).exists())

    def test_double_delete_raises(self):
        record = _make_record(self.org, self.batch)
        soft_delete_record(record=record, actor=None, reason="x")
        with self.assertRaises(AlreadyDeletedError):
            soft_delete_record(record=record, actor=None, reason="x again")

    def test_restore_not_deleted_raises(self):
        record = _make_record(self.org, self.batch)
        with self.assertRaises(NotDeletedError):
            restore_record(record=record, actor=None)

    def test_soft_delete_allowed_on_approved_record(self):
        # Phase 6d's whole point: hide a locked record without destroying
        # its certified business state.
        record = _make_record(self.org, self.batch, status=RS.APPROVED)
        soft_delete_record(record=record, actor=None, reason="fraudulent activity data")
        self.assertTrue(record.is_deleted)
        self.assertEqual(record.status, RS.APPROVED)  # unchanged

    def test_restore_allowed_on_approved_record(self):
        record = _make_record(self.org, self.batch, status=RS.APPROVED)
        soft_delete_record(record=record, actor=None, reason="x")
        restore_record(record=record, actor=None)
        self.assertFalse(record.is_deleted)
        self.assertEqual(record.status, RS.APPROVED)

    def test_business_field_change_still_blocked_on_approved_record(self):
        # The carve-out is narrow: soft-delete/restore bypass the audit
        # lock, but sneaking in a real business-data edit in the SAME
        # save() call must still be rejected.
        record = _make_record(self.org, self.batch, status=RS.APPROVED)
        record.normalized_value = Decimal("9999")
        record.is_deleted = True
        with self.assertRaises(ValidationError) as ctx:
            record.save()
        self.assertIn("Approved & Audit Locked", str(ctx.exception))


class HardDeleteBlockedTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Hard Delete Blocked Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL,
        )
        self.batch = _make_batch(self.org, self.ds)

    def test_instance_delete_raises(self):
        record = _make_record(self.org, self.batch)
        with self.assertRaises(ValidationError):
            record.delete()
        self.assertTrue(EmissionRecord.all_objects.filter(pk=record.pk).exists())

    def test_bulk_delete_raises(self):
        _make_record(self.org, self.batch)
        with self.assertRaises(ValidationError):
            EmissionRecord.objects.filter(organization=self.org).delete()
        self.assertEqual(EmissionRecord.all_objects.filter(organization=self.org).count(), 1)

    def test_bulk_update_raises(self):
        record = _make_record(self.org, self.batch)
        with self.assertRaises(ValidationError):
            EmissionRecord.objects.filter(pk=record.pk).update(is_deleted=True)
        record.refresh_from_db()
        self.assertFalse(record.is_deleted)

    def test_organization_with_records_cannot_be_deleted(self):
        _make_record(self.org, self.batch)
        with self.assertRaises(ProtectedError):
            self.org.delete()

    def test_batch_with_records_cannot_be_deleted(self):
        _make_record(self.org, self.batch)
        with self.assertRaises(ProtectedError):
            self.batch.delete()

    def test_empty_batch_can_still_be_deleted(self):
        empty_batch = _make_batch(self.org, self.ds)
        empty_batch.delete()
        self.assertFalse(UploadBatch.objects.filter(pk=empty_batch.pk).exists())

    def test_calculation_protected_from_record_deletion_bypass(self):
        # Belt-and-suspenders, tested independently of EmissionRecord.
        # delete()'s own override (which raises first in the sanctioned
        # path) by calling the base Model.delete() directly -- proves the
        # FK-level PROTECT on EmissionCalculation.emission_record holds on
        # its own, not just because of the model-level block.
        from django.db import models as django_models

        record = _make_record(self.org, self.batch, status=RS.APPROVED)
        EmissionCalculation.objects.create(
            organization=self.org, emission_record=record, is_current=True, scope="SCOPE_1",
            co2e_tonnes=Decimal("1"), co2e_kg=Decimal("1000"),
            resolution_status=EmissionCalculation.ResolutionStatus.CALCULATED,
        )
        with self.assertRaises(ProtectedError):
            django_models.Model.delete(record)


class VersioningAndAuditIntegrationTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="SD Versioning Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL,
        )
        self.batch = _make_batch(self.org, self.ds)
        self.user = User.objects.create_user("sd_admin", password="pw")

    def test_delete_and_restore_each_create_a_version(self):
        record = _make_record(self.org, self.batch)
        self.assertEqual(record.versions.count(), 1)  # initial create

        soft_delete_record(record=record, actor=self.user, reason="dup")
        self.assertEqual(record.versions.count(), 2)
        latest = record.versions.order_by("-version_number").first()
        self.assertTrue(latest.is_deleted)
        self.assertIsNotNone(latest.deleted_at)
        self.assertEqual(latest.created_by, self.user)

        restore_record(record=record, actor=self.user)
        self.assertEqual(record.versions.count(), 3)
        latest = record.versions.order_by("-version_number").first()
        self.assertFalse(latest.is_deleted)
        self.assertIsNone(latest.deleted_at)

    def test_delete_creates_audit_entry(self):
        record = _make_record(self.org, self.batch)
        soft_delete_record(record=record, actor=self.user, reason="duplicate row")
        entry = AuditTrail.objects.get(record_uuid_backup=record.id, action="RECORD_SOFT_DELETE")
        self.assertEqual(entry.changed_by, self.user)
        self.assertEqual(entry.reason, "duplicate row")
        self.assertEqual(entry.changes["is_deleted"], [False, True])
        self.assertIn("record_version", entry.changes)

    def test_restore_creates_audit_entry(self):
        record = _make_record(self.org, self.batch)
        soft_delete_record(record=record, actor=self.user, reason="x")
        restore_record(record=record, actor=self.user, reason="was a mistake")
        entry = AuditTrail.objects.get(record_uuid_backup=record.id, action="RECORD_RESTORE")
        self.assertEqual(entry.changes["is_deleted"], [True, False])

    def test_audit_chain_remains_valid_after_delete_and_restore(self):
        record = _make_record(self.org, self.batch)
        soft_delete_record(record=record, actor=self.user, reason="x")
        restore_record(record=record, actor=self.user)

        result = verify_chain(self.org)
        self.assertTrue(result.valid)
        self.assertEqual(result.entries_checked, 2)


class SoftDeleteAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.org = Organization.objects.create(name="SD API Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL,
        )
        self.batch = _make_batch(self.org, self.ds)
        self.org_admin = self._user("sd_org_admin", Role.ORG_ADMIN)
        self.analyst = self._user("sd_analyst", Role.ANALYST)
        self.auditor = self._user("sd_auditor", Role.AUDITOR)
        self.viewer = self._user("sd_viewer", Role.VIEWER)

    def _user(self, name, role):
        u = User.objects.create_user(name, password="pw")
        Membership.objects.create(user=u, organization=self.org, role=role, active=True)
        return u

    def test_delete_requires_reason(self):
        record = _make_record(self.org, self.batch)
        self.client.force_authenticate(self.org_admin)
        response = self.client.delete(f"/api/records/{record.id}/", data={}, format="json")
        self.assertEqual(response.status_code, drf_status.HTTP_400_BAD_REQUEST)

    def test_delete_success_hides_from_list(self):
        record = _make_record(self.org, self.batch)
        self.client.force_authenticate(self.org_admin)
        response = self.client.delete(
            f"/api/records/{record.id}/", data={"reason": "duplicate upload"}, format="json"
        )
        self.assertEqual(response.status_code, drf_status.HTTP_200_OK)
        self.assertTrue(response.json()["is_deleted"])

        listed = self.client.get("/api/records/").json()["results"]
        self.assertNotIn(str(record.id), [r["id"] for r in listed])

    def test_deleted_record_404s_on_detail_and_versions(self):
        record = _make_record(self.org, self.batch)
        self.client.force_authenticate(self.org_admin)
        self.client.delete(f"/api/records/{record.id}/", data={"reason": "x"}, format="json")

        self.assertEqual(
            self.client.get(f"/api/records/{record.id}/").status_code, drf_status.HTTP_404_NOT_FOUND
        )
        self.assertEqual(
            self.client.get(f"/api/records/{record.id}/versions/").status_code,
            drf_status.HTTP_404_NOT_FOUND,
        )

    def test_deleted_list_shows_deleted_records_only(self):
        active = _make_record(self.org, self.batch, row_index=1)
        deleted = _make_record(self.org, self.batch, row_index=2)
        self.client.force_authenticate(self.org_admin)
        self.client.delete(f"/api/records/{deleted.id}/", data={"reason": "x"}, format="json")

        response = self.client.get("/api/records/?deleted=true")
        ids = [r["id"] for r in response.json()["results"]]
        self.assertIn(str(deleted.id), ids)
        self.assertNotIn(str(active.id), ids)

    def test_restore_makes_record_visible_again(self):
        record = _make_record(self.org, self.batch)
        self.client.force_authenticate(self.org_admin)
        self.client.delete(f"/api/records/{record.id}/", data={"reason": "x"}, format="json")

        response = self.client.post(f"/api/records/{record.id}/restore/", data={}, format="json")
        self.assertEqual(response.status_code, drf_status.HTTP_200_OK)
        self.assertFalse(response.json()["is_deleted"])

        listed = self.client.get("/api/records/").json()["results"]
        self.assertIn(str(record.id), [r["id"] for r in listed])

    def test_double_delete_via_api_returns_400(self):
        # Unlike submit/approve/reject (which look up via the filtered
        # `objects` manager and so 404 on a record they can't see),
        # destroy/restore share one lookup that deliberately uses
        # all_objects (restore's target must be found even though it's
        # deleted) -- so a second delete attempt DOES find the record and
        # gets a clear, specific "already deleted" 400, not a bare 404.
        record = _make_record(self.org, self.batch)
        self.client.force_authenticate(self.org_admin)
        self.client.delete(f"/api/records/{record.id}/", data={"reason": "x"}, format="json")
        response = self.client.delete(f"/api/records/{record.id}/", data={"reason": "x"}, format="json")
        self.assertEqual(response.status_code, drf_status.HTTP_400_BAD_REQUEST)
        self.assertIn("already been deleted", response.json()["detail"])

    def test_restore_not_deleted_via_api_returns_400(self):
        record = _make_record(self.org, self.batch)
        self.client.force_authenticate(self.org_admin)
        response = self.client.post(f"/api/records/{record.id}/restore/", data={}, format="json")
        self.assertEqual(response.status_code, drf_status.HTTP_400_BAD_REQUEST)


class SoftDeleteRBACTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.org = Organization.objects.create(name="SD RBAC Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL,
        )
        self.batch = _make_batch(self.org, self.ds)
        self.org_admin = self._user("rbac_org_admin", Role.ORG_ADMIN)
        self.analyst = self._user("rbac_analyst", Role.ANALYST)
        self.auditor = self._user("rbac_auditor", Role.AUDITOR)
        self.viewer = self._user("rbac_viewer", Role.VIEWER)

    def _user(self, name, role):
        u = User.objects.create_user(name, password="pw")
        Membership.objects.create(user=u, organization=self.org, role=role, active=True)
        return u

    def test_org_admin_can_delete(self):
        record = _make_record(self.org, self.batch)
        self.client.force_authenticate(self.org_admin)
        response = self.client.delete(f"/api/records/{record.id}/", data={"reason": "x"}, format="json")
        self.assertEqual(response.status_code, drf_status.HTTP_200_OK)

    def test_analyst_cannot_delete(self):
        record = _make_record(self.org, self.batch)
        self.client.force_authenticate(self.analyst)
        response = self.client.delete(f"/api/records/{record.id}/", data={"reason": "x"}, format="json")
        self.assertEqual(response.status_code, drf_status.HTTP_403_FORBIDDEN)

    def test_auditor_cannot_delete(self):
        # Deliberately narrower than approve/reject's RBAC: deletion is
        # more administrative than review-oriented.
        record = _make_record(self.org, self.batch)
        self.client.force_authenticate(self.auditor)
        response = self.client.delete(f"/api/records/{record.id}/", data={"reason": "x"}, format="json")
        self.assertEqual(response.status_code, drf_status.HTTP_403_FORBIDDEN)

    def test_viewer_cannot_delete(self):
        record = _make_record(self.org, self.batch)
        self.client.force_authenticate(self.viewer)
        response = self.client.delete(f"/api/records/{record.id}/", data={"reason": "x"}, format="json")
        self.assertEqual(response.status_code, drf_status.HTTP_403_FORBIDDEN)

    def test_analyst_cannot_restore(self):
        record = _make_record(self.org, self.batch)
        self.client.force_authenticate(self.org_admin)
        self.client.delete(f"/api/records/{record.id}/", data={"reason": "x"}, format="json")
        self.client.force_authenticate(self.analyst)
        response = self.client.post(f"/api/records/{record.id}/restore/", data={}, format="json")
        self.assertEqual(response.status_code, drf_status.HTTP_403_FORBIDDEN)

    def test_analyst_cannot_view_deleted_list(self):
        self.client.force_authenticate(self.analyst)
        response = self.client.get("/api/records/?deleted=true")
        self.assertEqual(response.status_code, drf_status.HTTP_403_FORBIDDEN)

    def test_org_admin_can_view_deleted_list(self):
        self.client.force_authenticate(self.org_admin)
        response = self.client.get("/api/records/?deleted=true")
        self.assertEqual(response.status_code, drf_status.HTTP_200_OK)


class TenantIsolationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.org_a = Organization.objects.create(name="SD Org A")
        self.org_b = Organization.objects.create(name="SD Org B")
        self.ds_a = DataSource.objects.create(
            organization=self.org_a, name="SAP A", source_type=DataSource.SourceType.SAP_FUEL,
        )
        self.batch_a = _make_batch(self.org_a, self.ds_a)
        self.record_a = _make_record(self.org_a, self.batch_a)

        self.user_b = User.objects.create_user("sd_org_b_admin", password="pw")
        Membership.objects.create(user=self.user_b, organization=self.org_b, role=Role.ORG_ADMIN, active=True)
        self.client.force_authenticate(self.user_b)

    def test_cannot_delete_other_org_record(self):
        response = self.client.delete(
            f"/api/records/{self.record_a.id}/", data={"reason": "x"}, format="json"
        )
        self.assertEqual(response.status_code, drf_status.HTTP_403_FORBIDDEN)
        self.record_a.refresh_from_db()
        self.assertFalse(self.record_a.is_deleted)

    def test_cannot_restore_other_org_record(self):
        soft_delete_record(record=self.record_a, actor=None, reason="x")
        response = self.client.post(f"/api/records/{self.record_a.id}/restore/", data={}, format="json")
        self.assertEqual(response.status_code, drf_status.HTTP_403_FORBIDDEN)

    def test_deleted_list_is_tenant_scoped(self):
        soft_delete_record(record=self.record_a, actor=None, reason="x")
        response = self.client.get("/api/records/?deleted=true")
        ids = [r["id"] for r in response.json()["results"]]
        self.assertNotIn(str(self.record_a.id), ids)


class ComplianceReportsAfterDeletionTests(TestCase):
    """The core "preserve historical compliance reports" requirement:
    a soft-deleted record's certified (APPROVED) history must remain in
    compliance reports, while it disappears from dashboards and the
    active calculations list."""

    def setUp(self):
        self.client = APIClient()
        self.org = Organization.objects.create(name="SD Compliance Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL,
        )
        self.batch = _make_batch(self.org, self.ds)
        self.org_admin = User.objects.create_user("sd_compliance_admin", password="pw")
        Membership.objects.create(user=self.org_admin, organization=self.org, role=Role.ORG_ADMIN, active=True)
        self.client.force_authenticate(self.org_admin)

        self.record = _make_record(self.org, self.batch, status=RS.APPROVED)
        self.calc = EmissionCalculation.objects.create(
            organization=self.org, emission_record=self.record, is_current=True, scope="SCOPE_1",
            reporting_date=date(2026, 1, 15), reporting_month=date(2026, 1, 1),
            co2e_tonnes=Decimal("5"), co2e_kg=Decimal("5000"),
            resolution_status=EmissionCalculation.ResolutionStatus.CALCULATED,
        )

    def test_compliance_report_still_includes_deleted_record(self):
        soft_delete_record(record=self.record, actor=self.org_admin, reason="later found duplicate")

        response = self.client.get(
            "/api/reports/compliance/?date_from=2026-01-01&date_to=2026-01-31"
        )
        data = response.json()
        self.assertEqual(data["line_item_count"], 1)
        line_item = data["line_items"][0]
        self.assertEqual(line_item["record_id"], str(self.record.id))
        self.assertTrue(line_item["is_deleted"])
        self.assertIsNotNone(line_item["deleted_at"])

    def test_compliance_report_summary_totals_unaffected_by_deletion(self):
        soft_delete_record(record=self.record, actor=self.org_admin, reason="x")
        response = self.client.get(
            "/api/reports/compliance/?date_from=2026-01-01&date_to=2026-01-31"
        )
        self.assertEqual(Decimal(response.json()["summary"]["total_co2e_tonnes"]), Decimal("5"))

    def test_compliance_report_csv_still_includes_deleted_record(self):
        soft_delete_record(record=self.record, actor=self.org_admin, reason="x")
        response = self.client.get(
            "/api/reports/compliance/csv/?date_from=2026-01-01&date_to=2026-01-31"
        )
        body = b"".join(response.streaming_content).decode("utf-8")
        self.assertIn(str(self.record.id), body)
        self.assertIn("True", body)  # is_deleted column

    def test_dashboard_metrics_exclude_deleted_record(self):
        soft_delete_record(record=self.record, actor=self.org_admin, reason="x")
        response = self.client.get("/api/metrics/summary/")
        self.assertEqual(Decimal(response.json()["total_co2e_tonnes"]), Decimal("0"))

    def test_calculations_list_excludes_deleted_record(self):
        soft_delete_record(record=self.record, actor=self.org_admin, reason="x")
        response = self.client.get("/api/calculations/")
        ids = [c["id"] for c in response.json()["results"]]
        self.assertNotIn(str(self.calc.id), ids)

    def test_record_export_excludes_deleted_record(self):
        soft_delete_record(record=self.record, actor=self.org_admin, reason="x")
        response = self.client.get("/api/records/export/")
        body = b"".join(response.streaming_content).decode("utf-8")
        self.assertNotIn(str(self.record.id), body)


class MetricsCacheInvalidationTests(TestCase):
    """Phase 6h / H1: soft-delete and restore must bump the org's calc
    cache version, else /api/metrics/summary/ can keep serving a stale
    cached payload from before the delete/restore -- the compliance-report
    test above doesn't catch this because it never primes the cache with a
    pre-delete request first."""

    def setUp(self):
        self.client = APIClient()
        self.org = Organization.objects.create(name="SD Cache Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL,
        )
        self.batch = _make_batch(self.org, self.ds)
        self.org_admin = User.objects.create_user("sd_cache_admin", password="pw")
        Membership.objects.create(user=self.org_admin, organization=self.org, role=Role.ORG_ADMIN, active=True)
        self.client.force_authenticate(self.org_admin)

        self.record = _make_record(self.org, self.batch, status=RS.APPROVED)
        EmissionCalculation.objects.create(
            organization=self.org, emission_record=self.record, is_current=True, scope="SCOPE_1",
            reporting_date=date(2026, 1, 15), reporting_month=date(2026, 1, 1),
            co2e_tonnes=Decimal("5"), co2e_kg=Decimal("5000"),
            resolution_status=EmissionCalculation.ResolutionStatus.CALCULATED,
        )

    def test_soft_delete_bumps_calc_version(self):
        before = get_calc_version(self.org.id)
        soft_delete_record(record=self.record, actor=self.org_admin, reason="x")
        self.assertEqual(get_calc_version(self.org.id), before + 1)

    def test_restore_bumps_calc_version(self):
        soft_delete_record(record=self.record, actor=self.org_admin, reason="x")
        before = get_calc_version(self.org.id)
        restore_record(record=self.record, actor=self.org_admin)
        self.assertEqual(get_calc_version(self.org.id), before + 1)

    def test_dashboard_reflects_delete_without_stale_cache(self):
        first = self.client.get("/api/metrics/summary/")
        self.assertEqual(Decimal(first.json()["total_co2e_tonnes"]), Decimal("5"))

        soft_delete_record(record=self.record, actor=self.org_admin, reason="x")

        second = self.client.get("/api/metrics/summary/")
        self.assertEqual(Decimal(second.json()["total_co2e_tonnes"]), Decimal("0"))

    def test_dashboard_reflects_restore_without_stale_cache(self):
        soft_delete_record(record=self.record, actor=self.org_admin, reason="x")
        first = self.client.get("/api/metrics/summary/")
        self.assertEqual(Decimal(first.json()["total_co2e_tonnes"]), Decimal("0"))

        restore_record(record=self.record, actor=self.org_admin)

        second = self.client.get("/api/metrics/summary/")
        self.assertEqual(Decimal(second.json()["total_co2e_tonnes"]), Decimal("5"))


class ConcurrentSoftDeleteTests(TransactionTestCase):
    """Mirrors apps.ingestion.tests_workflow.ConcurrentApprovalTests: real
    threads, real per-thread connections, racing to soft-delete the SAME
    record. Exactly one should win."""

    def test_concurrent_deletes_only_one_wins(self):
        org = Organization.objects.create(name="SD Concurrent Org")
        ds = DataSource.objects.create(
            organization=org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL,
        )
        batch = _make_batch(org, ds)
        record = _make_record(org, batch)

        results = []

        def worker():
            try:
                with transaction.atomic():
                    r = EmissionRecord.all_objects.select_for_update().get(pk=record.pk)
                    soft_delete_record(record=r, actor=None, reason="race")
                results.append("ok")
            except AlreadyDeletedError:
                results.append("already_deleted")
            except Exception as exc:  # noqa: BLE001 - captured for the assertion below
                results.append(f"error:{exc}")
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        record.refresh_from_db()
        if connection.vendor == "sqlite":
            ok_count = results.count("ok")
            self.assertLessEqual(ok_count, 1)
            self.assertEqual(record.is_deleted, ok_count == 1)
        else:
            self.assertEqual(results.count("ok"), 1)
            self.assertEqual(results.count("already_deleted"), 9)
            self.assertTrue(record.is_deleted)

        approved_versions = EmissionRecordVersion.objects.filter(record=record, is_deleted=True)
        self.assertEqual(approved_versions.count(), min(results.count("ok"), 1))
