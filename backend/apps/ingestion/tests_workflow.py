"""
Phase 6c — the fixed enterprise approval workflow:

    DRAFT/SUSPICIOUS/VALIDATED -> SUBMITTED -> APPROVED
                                       |
                                       +-> REJECTED -> SUBMITTED (resubmit)

Covers: valid transitions, invalid transitions (rejected with a clear
message, enforced in EmissionRecord.clean() itself), RBAC for submit/
approve/reject, tenant isolation, EmissionRecordVersion integration (every
transition creates a new version), audit hash-chain integration (every
transition creates a verifiable AuditTrail entry), the read-only
GET /workflow/ endpoint, and real multi-threaded concurrent approvals.
"""
import threading

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.test import TestCase, TransactionTestCase
from rest_framework import status as drf_status
from rest_framework.test import APIClient

from apps.accounts.models import Membership, Role
from apps.audit.models import AuditTrail
from apps.audit.services import verify_chain
from apps.core.models import DataSource, Organization
from apps.ingestion.models import EmissionRecord, UploadBatch
from apps.ingestion.services.workflow import (
    InvalidTransitionError,
    available_actions,
    transition_record,
)

User = get_user_model()

RS = EmissionRecord.RecordStatus


def _make_batch(org, data_source):
    return UploadBatch.objects.create(organization=org, data_source=data_source, file_name="wf.csv")


def _make_record(org, batch, status=RS.DRAFT, row_index=1):
    return EmissionRecord.objects.create(
        organization=org, batch=batch, row_index=row_index,
        raw_data_payload={"a": 1}, status=status,
        normalized_value=100, normalized_unit="L",
    )


class TransitionGraphTests(TestCase):
    """Direct, model-level tests of the service function -- no HTTP, no
    RBAC -- isolating the state machine itself."""

    def setUp(self):
        self.org = Organization.objects.create(name="Workflow Graph Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL,
        )
        self.batch = _make_batch(self.org, self.ds)

    def test_draft_to_submitted_is_valid(self):
        record = _make_record(self.org, self.batch, status=RS.DRAFT)
        transition_record(record=record, target_status=RS.SUBMITTED, actor=None)
        self.assertEqual(record.status, RS.SUBMITTED)

    def test_suspicious_to_submitted_is_valid(self):
        record = _make_record(self.org, self.batch, status=RS.SUSPICIOUS)
        transition_record(record=record, target_status=RS.SUBMITTED, actor=None)
        self.assertEqual(record.status, RS.SUBMITTED)

    def test_submitted_to_approved_is_valid(self):
        record = _make_record(self.org, self.batch, status=RS.SUBMITTED)
        transition_record(record=record, target_status=RS.APPROVED, actor=None)
        self.assertEqual(record.status, RS.APPROVED)
        self.assertIsNotNone(record.approved_at)

    def test_submitted_to_rejected_is_valid(self):
        record = _make_record(self.org, self.batch, status=RS.SUBMITTED)
        transition_record(record=record, target_status=RS.REJECTED, actor=None, reason="Bad data")
        self.assertEqual(record.status, RS.REJECTED)

    def test_rejected_can_be_resubmitted(self):
        record = _make_record(self.org, self.batch, status=RS.REJECTED)
        transition_record(record=record, target_status=RS.SUBMITTED, actor=None)
        self.assertEqual(record.status, RS.SUBMITTED)

    def test_draft_to_approved_directly_is_invalid(self):
        record = _make_record(self.org, self.batch, status=RS.DRAFT)
        with self.assertRaises(InvalidTransitionError):
            transition_record(record=record, target_status=RS.APPROVED, actor=None)

    def test_approved_is_terminal(self):
        record = _make_record(self.org, self.batch, status=RS.APPROVED)
        with self.assertRaises(InvalidTransitionError):
            transition_record(record=record, target_status=RS.SUBMITTED, actor=None)
        with self.assertRaises(InvalidTransitionError):
            transition_record(record=record, target_status=RS.REJECTED, actor=None)

    def test_failed_never_enters_the_workflow(self):
        record = _make_record(self.org, self.batch, status=RS.FAILED)
        with self.assertRaises(InvalidTransitionError):
            transition_record(record=record, target_status=RS.SUBMITTED, actor=None)

    def test_double_submit_is_invalid(self):
        record = _make_record(self.org, self.batch, status=RS.SUBMITTED)
        with self.assertRaises(InvalidTransitionError):
            transition_record(record=record, target_status=RS.SUBMITTED, actor=None)

    def test_rejected_to_approved_directly_is_invalid(self):
        record = _make_record(self.org, self.batch, status=RS.REJECTED)
        with self.assertRaises(InvalidTransitionError):
            transition_record(record=record, target_status=RS.APPROVED, actor=None)

    def test_available_actions_reflects_the_graph(self):
        self.assertEqual(available_actions(RS.DRAFT), {RS.SUBMITTED})
        self.assertEqual(available_actions(RS.SUBMITTED), {RS.APPROVED, RS.REJECTED})
        self.assertEqual(available_actions(RS.APPROVED), set())
        self.assertEqual(available_actions(RS.FAILED), set())

    def test_model_level_clean_enforces_the_same_graph_independent_of_the_service(self):
        # Defense in depth: bypassing the service entirely (direct ORM use,
        # exactly like Django Admin would) must still be rejected --
        # EmissionRecord.clean() enforces WORKFLOW_TRANSITIONS itself.
        record = _make_record(self.org, self.batch, status=RS.DRAFT)
        record.status = RS.APPROVED
        with self.assertRaises(ValidationError) as ctx:
            record.save()
        self.assertIn("Invalid workflow transition", str(ctx.exception))


class WorkflowAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.org = Organization.objects.create(name="Workflow API Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL,
        )
        self.batch = _make_batch(self.org, self.ds)
        self.analyst = self._user("analyst", Role.ANALYST)
        self.org_admin = self._user("org_admin", Role.ORG_ADMIN)
        self.auditor = self._user("auditor", Role.AUDITOR)
        self.viewer = self._user("viewer", Role.VIEWER)

    def _user(self, name, role):
        u = User.objects.create_user(username=name, password="pw")
        Membership.objects.create(user=u, organization=self.org, role=role, active=True)
        return u

    def test_submit_transitions_draft_to_submitted(self):
        record = _make_record(self.org, self.batch, status=RS.DRAFT)
        self.client.force_authenticate(self.analyst)
        response = self.client.post(f"/api/records/{record.id}/submit/", data={}, format="json")
        self.assertEqual(response.status_code, drf_status.HTTP_200_OK)
        self.assertEqual(response.json()["status"], RS.SUBMITTED)

    def test_submit_denied_for_auditor_and_viewer(self):
        record = _make_record(self.org, self.batch, status=RS.DRAFT)
        for user in (self.auditor, self.viewer):
            self.client.force_authenticate(user)
            response = self.client.post(f"/api/records/{record.id}/submit/", data={}, format="json")
            self.assertEqual(response.status_code, drf_status.HTTP_403_FORBIDDEN, user)

    def test_approve_requires_submitted_first(self):
        record = _make_record(self.org, self.batch, status=RS.DRAFT)
        self.client.force_authenticate(self.org_admin)
        response = self.client.post(
            f"/api/records/{record.id}/approve/", data={}, format="json"
        )
        self.assertEqual(response.status_code, drf_status.HTTP_400_BAD_REQUEST)
        self.assertIn("Cannot transition from DRAFT", response.json()["detail"])

    def test_full_submit_approve_flow(self):
        record = _make_record(self.org, self.batch, status=RS.DRAFT)
        self.client.force_authenticate(self.analyst)
        self.client.post(f"/api/records/{record.id}/submit/", data={}, format="json")
        response = self.client.post(
            f"/api/records/{record.id}/approve/", data={"reason": "Looks good"}, format="json"
        )
        self.assertEqual(response.status_code, drf_status.HTTP_200_OK)
        self.assertEqual(response.json()["status"], RS.APPROVED)

    def test_reject_requires_reason(self):
        record = _make_record(self.org, self.batch, status=RS.SUBMITTED)
        self.client.force_authenticate(self.org_admin)
        response = self.client.post(f"/api/records/{record.id}/reject/", data={}, format="json")
        self.assertEqual(response.status_code, drf_status.HTTP_400_BAD_REQUEST)

    def test_reject_reason_over_max_length_is_rejected(self):
        # Phase 6f: bounds free-text accepted straight into the hash-
        # chained audit ledger.
        record = _make_record(self.org, self.batch, status=RS.SUBMITTED)
        self.client.force_authenticate(self.org_admin)
        response = self.client.post(
            f"/api/records/{record.id}/reject/",
            data={"reason": "x" * 1001}, format="json",
        )
        self.assertEqual(response.status_code, drf_status.HTTP_400_BAD_REQUEST)

    def test_submit_reason_over_max_length_is_rejected(self):
        record = _make_record(self.org, self.batch, status=RS.DRAFT)
        self.client.force_authenticate(self.analyst)
        response = self.client.post(
            f"/api/records/{record.id}/submit/",
            data={"reason": "x" * 1001}, format="json",
        )
        self.assertEqual(response.status_code, drf_status.HTTP_400_BAD_REQUEST)

    def test_reject_reason_at_max_length_is_accepted(self):
        record = _make_record(self.org, self.batch, status=RS.SUBMITTED)
        self.client.force_authenticate(self.org_admin)
        response = self.client.post(
            f"/api/records/{record.id}/reject/",
            data={"reason": "x" * 1000}, format="json",
        )
        self.assertEqual(response.status_code, drf_status.HTTP_200_OK)

    def test_reject_denied_for_viewer(self):
        record = _make_record(self.org, self.batch, status=RS.SUBMITTED)
        self.client.force_authenticate(self.viewer)
        response = self.client.post(
            f"/api/records/{record.id}/reject/", data={"reason": "no"}, format="json"
        )
        self.assertEqual(response.status_code, drf_status.HTTP_403_FORBIDDEN)

    def test_reject_then_resubmit_then_approve(self):
        record = _make_record(self.org, self.batch, status=RS.SUBMITTED)
        self.client.force_authenticate(self.org_admin)

        reject_resp = self.client.post(
            f"/api/records/{record.id}/reject/", data={"reason": "Wrong unit"}, format="json"
        )
        self.assertEqual(reject_resp.status_code, drf_status.HTTP_200_OK)
        self.assertEqual(reject_resp.json()["status"], RS.REJECTED)

        resubmit_resp = self.client.post(f"/api/records/{record.id}/submit/", data={}, format="json")
        self.assertEqual(resubmit_resp.status_code, drf_status.HTTP_200_OK)
        self.assertEqual(resubmit_resp.json()["status"], RS.SUBMITTED)

        approve_resp = self.client.post(f"/api/records/{record.id}/approve/", data={}, format="json")
        self.assertEqual(approve_resp.status_code, drf_status.HTTP_200_OK)
        self.assertEqual(approve_resp.json()["status"], RS.APPROVED)

    def test_workflow_endpoint_reports_status_and_available_actions(self):
        record = _make_record(self.org, self.batch, status=RS.SUBMITTED)
        self.client.force_authenticate(self.analyst)
        response = self.client.get(f"/api/records/{record.id}/workflow/")
        self.assertEqual(response.status_code, drf_status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data["status"], RS.SUBMITTED)
        self.assertEqual(set(data["available_transitions"]), {RS.APPROVED, RS.REJECTED})

    def test_workflow_endpoint_terminal_state_has_no_actions(self):
        record = _make_record(self.org, self.batch, status=RS.APPROVED)
        self.client.force_authenticate(self.analyst)
        response = self.client.get(f"/api/records/{record.id}/workflow/")
        self.assertEqual(response.json()["available_transitions"], [])


class WorkflowVersioningAndAuditIntegrationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.org = Organization.objects.create(name="Workflow Integration Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL,
        )
        self.batch = _make_batch(self.org, self.ds)
        self.user = User.objects.create_user(username="wf_admin", password="pw")
        Membership.objects.create(
            user=self.user, organization=self.org, role=Role.ORG_ADMIN, active=True
        )
        self.client.force_authenticate(self.user)

    def test_each_transition_creates_a_new_version(self):
        record = _make_record(self.org, self.batch, status=RS.DRAFT)
        self.client.post(f"/api/records/{record.id}/submit/", data={}, format="json")
        self.client.post(f"/api/records/{record.id}/approve/", data={}, format="json")

        versions = list(record.versions.order_by("version_number"))
        # version 1 (create/DRAFT), 2 (SUBMITTED), 3 (APPROVED)
        self.assertEqual(len(versions), 3)
        self.assertEqual([v.status for v in versions], [RS.DRAFT, RS.SUBMITTED, RS.APPROVED])

    def test_each_transition_creates_an_audit_entry_and_cross_references_the_version(self):
        record = _make_record(self.org, self.batch, status=RS.DRAFT)
        self.client.post(f"/api/records/{record.id}/submit/", data={}, format="json")
        self.client.post(f"/api/records/{record.id}/approve/", data={}, format="json")

        submit_audit = AuditTrail.objects.get(
            record_uuid_backup=record.id, action="RECORD_SUBMISSION"
        )
        approve_audit = AuditTrail.objects.get(
            record_uuid_backup=record.id, action="RECORD_APPROVAL"
        )
        versions = {v.version_number: v for v in record.versions.all()}
        self.assertEqual(submit_audit.changes["status"], [RS.DRAFT, RS.SUBMITTED])
        self.assertEqual(approve_audit.changes["status"], [RS.SUBMITTED, RS.APPROVED])
        self.assertIn(submit_audit.changes["record_version"], versions)
        self.assertIn(approve_audit.changes["record_version"], versions)

    def test_audit_chain_remains_valid_after_a_full_workflow_sequence(self):
        record = _make_record(self.org, self.batch, status=RS.DRAFT)
        self.client.post(f"/api/records/{record.id}/submit/", data={}, format="json")
        self.client.post(
            f"/api/records/{record.id}/reject/", data={"reason": "fix unit"}, format="json"
        )
        self.client.post(f"/api/records/{record.id}/submit/", data={}, format="json")
        self.client.post(f"/api/records/{record.id}/approve/", data={}, format="json")

        result = verify_chain(self.org)
        self.assertTrue(result.valid)
        self.assertEqual(result.entries_checked, 4)


class WorkflowTenantIsolationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.org_a = Organization.objects.create(name="Org A")
        self.org_b = Organization.objects.create(name="Org B")
        self.ds_a = DataSource.objects.create(
            organization=self.org_a, name="SAP A", source_type=DataSource.SourceType.SAP_FUEL,
        )
        self.batch_a = _make_batch(self.org_a, self.ds_a)
        self.record_a = _make_record(self.org_a, self.batch_a, status=RS.DRAFT)

        self.user_b = User.objects.create_user(username="org_b_admin", password="pw")
        Membership.objects.create(
            user=self.user_b, organization=self.org_b, role=Role.ORG_ADMIN, active=True
        )
        self.client.force_authenticate(self.user_b)

    def test_cannot_submit_other_org_record(self):
        response = self.client.post(f"/api/records/{self.record_a.id}/submit/", data={}, format="json")
        self.assertEqual(response.status_code, drf_status.HTTP_403_FORBIDDEN)

    def test_cannot_approve_other_org_record(self):
        response = self.client.post(f"/api/records/{self.record_a.id}/approve/", data={}, format="json")
        self.assertEqual(response.status_code, drf_status.HTTP_403_FORBIDDEN)

    def test_cannot_reject_other_org_record(self):
        response = self.client.post(
            f"/api/records/{self.record_a.id}/reject/", data={"reason": "x"}, format="json"
        )
        self.assertEqual(response.status_code, drf_status.HTTP_403_FORBIDDEN)

    def test_workflow_view_hidden_across_tenants(self):
        response = self.client.get(f"/api/records/{self.record_a.id}/workflow/")
        self.assertEqual(response.status_code, drf_status.HTTP_404_NOT_FOUND)

    def test_record_status_unchanged_after_cross_org_attempt(self):
        self.client.post(f"/api/records/{self.record_a.id}/submit/", data={}, format="json")
        self.record_a.refresh_from_db()
        self.assertEqual(self.record_a.status, RS.DRAFT)


class ConcurrentApprovalTests(TransactionTestCase):
    """Mirrors apps.audit.tests.ConcurrentAppendTests and
    apps.ingestion.tests_versioning.ConcurrentVersioningTests: real threads,
    real per-thread connections, racing to apply the SAME transition to the
    SAME record. Exactly one should win; every loser must fail with
    InvalidTransitionError (its re-read, now-locked row no longer has the
    status it raced on), never silently succeed twice."""

    def test_concurrent_approvals_only_one_wins(self):
        org = Organization.objects.create(name="Concurrent Approval Org")
        ds = DataSource.objects.create(
            organization=org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL,
        )
        batch = _make_batch(org, ds)
        record = _make_record(org, batch, status=RS.SUBMITTED)

        results = []

        def worker():
            try:
                with transaction.atomic():
                    r = EmissionRecord.objects.select_for_update().get(pk=record.pk)
                    transition_record(record=r, target_status=RS.APPROVED, actor=None)
                results.append("ok")
            except InvalidTransitionError:
                results.append("invalid")
            except Exception as exc:  # noqa: BLE001 - captured for the assertion below
                results.append(f"error:{exc}")
            finally:
                # Same fix already applied in apps.audit.tests /
                # tests_versioning.py: each thread opens its own
                # connection; Django's teardown only closes the main
                # thread's, so leaving this open blocks Postgres's DROP
                # DATABASE at teardown even when every assertion passes.
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        record.refresh_from_db()
        if connection.vendor == "sqlite":
            # SQLite locks at the file level -- occasionally every thread
            # loses the race outright ("database is locked"), which is a
            # SQLite coarseness artifact, not evidence the transition guard
            # is broken. What must hold regardless: never more than one
            # "ok", and the record ends up APPROVED only if exactly one did.
            ok_count = results.count("ok")
            self.assertLessEqual(ok_count, 1)
            self.assertEqual(record.status, RS.APPROVED if ok_count == 1 else RS.SUBMITTED)
        else:
            # Postgres: real row-level locking serializes all ten -- one
            # wins, the other nine cleanly observe an already-APPROVED
            # record and fail with InvalidTransitionError, never a raw
            # DB-level error and never a silent double-approval.
            self.assertEqual(results.count("ok"), 1)
            self.assertEqual(results.count("invalid"), 9)
            self.assertEqual(record.status, RS.APPROVED)

        # However many succeeded, never more than one APPROVED version was
        # created -- the UniqueConstraint on (record, version_number) would
        # have raised if a duplicate had ever been committed.
        approved_versions = record.versions.filter(status=RS.APPROVED)
        self.assertEqual(approved_versions.count(), min(results.count("ok"), 1))
