"""
Phase 6a — the audit hash chain: apps.audit.services (append_entry,
verify_chain), the QuerySet-level bulk delete/update block, the
Organization on_delete=PROTECT change, the AuditChainVerifyView API
endpoint, and the verify_audit_chain management command.
"""
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase, TransactionTestCase
from rest_framework import status as drf_status
from rest_framework.test import APIClient

from apps.accounts.models import Membership, Role
from apps.audit.models import GENESIS_HASH, AuditChainState, AuditTrail
from apps.audit.services import append_entry, compute_entry_hash, verify_chain
from apps.core.models import Organization

User = get_user_model()


class AppendEntryTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Audit Chain Org")

    def test_first_entry_chains_from_genesis(self):
        entry = append_entry(organization=self.org, action="TEST_ACTION", reason="first")
        self.assertEqual(entry.sequence, 1)
        self.assertEqual(entry.prev_hash, GENESIS_HASH)
        self.assertEqual(len(entry.entry_hash), 64)

        state = AuditChainState.objects.get(organization=self.org)
        self.assertEqual(state.last_sequence, 1)
        self.assertEqual(state.last_hash, entry.entry_hash)

    def test_second_entry_links_to_first(self):
        first = append_entry(organization=self.org, action="A1")
        second = append_entry(organization=self.org, action="A2")
        self.assertEqual(second.sequence, 2)
        self.assertEqual(second.prev_hash, first.entry_hash)
        self.assertNotEqual(second.entry_hash, first.entry_hash)

    def test_two_organizations_have_independent_chains(self):
        org2 = Organization.objects.create(name="Second Org")
        e1 = append_entry(organization=self.org, action="A")
        e2 = append_entry(organization=org2, action="A")
        # Same action, same (empty) changes/reason, same prev_hash (both
        # genesis) — sequence is identical (1) but organization_id differs,
        # so the hashes must differ despite everything else matching.
        self.assertEqual(e1.sequence, e2.sequence)
        self.assertEqual(e1.prev_hash, e2.prev_hash)
        self.assertNotEqual(e1.entry_hash, e2.entry_hash)

    def test_hash_is_deterministic_for_identical_inputs(self):
        h1 = compute_entry_hash(
            sequence=1, organization_id=self.org.id, record_uuid_backup=None,
            action="X", changed_by_id=None, changes={"a": 1}, reason="r",
            timestamp_iso="2026-01-01T00:00:00+00:00", prev_hash=GENESIS_HASH,
        )
        h2 = compute_entry_hash(
            sequence=1, organization_id=self.org.id, record_uuid_backup=None,
            action="X", changed_by_id=None, changes={"a": 1}, reason="r",
            timestamp_iso="2026-01-01T00:00:00+00:00", prev_hash=GENESIS_HASH,
        )
        self.assertEqual(h1, h2)


class VerifyChainTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Verify Org")

    def test_empty_chain_is_valid(self):
        result = verify_chain(self.org)
        self.assertTrue(result.valid)
        self.assertEqual(result.entries_checked, 0)

    def test_intact_chain_is_valid(self):
        for i in range(5):
            append_entry(organization=self.org, action=f"A{i}")
        result = verify_chain(self.org)
        self.assertTrue(result.valid)
        self.assertEqual(result.entries_checked, 5)

    def test_tampered_entry_content_is_detected(self):
        append_entry(organization=self.org, action="A1")
        target = append_entry(organization=self.org, action="A2")
        append_entry(organization=self.org, action="A3")

        # Simulate a raw DB edit bypassing the ORM entirely — the only way
        # to actually alter a row, since AuditTrail.clean()/delete() block
        # normal ORM mutation and AuditTrailQuerySet blocks bulk operations.
        with connection.cursor() as cur:
            cur.execute(
                "UPDATE audit_audittrail SET reason = %s WHERE id = %s",
                ["tampered", target.id.hex if hasattr(target.id, "hex") else str(target.id)],
            )

        result = verify_chain(self.org)
        self.assertFalse(result.valid)
        self.assertEqual(result.broken_at_sequence, target.sequence)
        self.assertIn("entry_hash mismatch", result.detail)

    def test_broken_chain_logs_critical(self):
        # Phase 6f: a broken chain is a tampering event -- must be
        # observable via logs (CRITICAL), not just returned in the result,
        # so an operator's log-based alerting can actually catch it.
        append_entry(organization=self.org, action="A1")
        target = append_entry(organization=self.org, action="A2")
        with connection.cursor() as cur:
            cur.execute(
                "UPDATE audit_audittrail SET reason = %s WHERE id = %s",
                ["tampered", target.id.hex if hasattr(target.id, "hex") else str(target.id)],
            )
        with self.assertLogs("apps.audit.services", level="CRITICAL") as ctx:
            verify_chain(self.org)
        self.assertIn("BROKEN", ctx.output[0])
        self.assertIn(str(self.org.id), ctx.output[0])

    def test_broken_prev_hash_link_is_detected(self):
        append_entry(organization=self.org, action="A1")
        second = append_entry(organization=self.org, action="A2")

        with connection.cursor() as cur:
            cur.execute(
                "UPDATE audit_audittrail SET prev_hash = %s WHERE id = %s",
                ["f" * 64, second.id.hex if hasattr(second.id, "hex") else str(second.id)],
            )

        result = verify_chain(self.org)
        self.assertFalse(result.valid)
        self.assertEqual(result.broken_at_sequence, second.sequence)
        self.assertIn("prev_hash mismatch", result.detail)


class AppendOnlyEnforcementTests(TestCase):
    """Confirms the append-only guarantee holds at both the instance level
    (pre-existing behavior) and the QuerySet/bulk level (the real gap Phase
    6a closes — QuerySet.delete()/.update() bypass instance methods
    entirely, same class of Django gotcha already hit in Phase 5f/5g)."""

    def setUp(self):
        self.org = Organization.objects.create(name="Append Only Org")
        self.entry = append_entry(organization=self.org, action="A")

    def test_instance_delete_raises(self):
        with self.assertRaises(ValidationError):
            self.entry.delete()

    def test_instance_resave_raises(self):
        self.entry.reason = "edited"
        with self.assertRaises(ValidationError):
            self.entry.save()

    def test_bulk_delete_is_blocked(self):
        with self.assertRaises(ValidationError):
            AuditTrail.objects.filter(organization=self.org).delete()
        # Confirm it genuinely wasn't deleted, not just that the exception
        # happened to be raised after a partial delete.
        self.assertTrue(AuditTrail.objects.filter(pk=self.entry.pk).exists())

    def test_bulk_update_is_blocked(self):
        with self.assertRaises(ValidationError):
            AuditTrail.objects.filter(organization=self.org).update(reason="bulk edited")
        self.entry.refresh_from_db()
        self.assertIsNone(self.entry.reason)

    def test_organization_with_audit_history_cannot_be_deleted(self):
        # Phase 6a: on_delete changed CASCADE -> PROTECT specifically so
        # deleting an org can no longer silently destroy its audit history.
        with self.assertRaises(ProtectedError):
            self.org.delete()
        self.assertTrue(Organization.objects.filter(pk=self.org.pk).exists())

    def test_organization_without_audit_history_can_still_be_deleted(self):
        empty_org = Organization.objects.create(name="No History Org")
        empty_org.delete()
        self.assertFalse(Organization.objects.filter(pk=empty_org.pk).exists())


class ConcurrentAppendTests(TransactionTestCase):
    """A real multi-threaded concurrency test — TransactionTestCase because
    SQLite's/Django's test-transaction wrapping in plain TestCase would
    otherwise hide genuine cross-connection locking behavior.

    SQLite does not provide real row-level locking — select_for_update()
    degrades to SQLite's own file-level write lock, which under genuine
    concurrent writers from separate threads routinely raises "database
    table is locked" (a SQLite limitation, not a bug in append_entry's
    locking logic). This is the same category of thing this project has
    consistently handled by trusting unit tests for logic correctness and
    real Postgres (via Docker Compose, or backend-ci.yml's Postgres service
    container — see docs/CI_CD.md) for genuine concurrency guarantees. Under
    sqlite, this test tolerates (but logs) locking errors from threads that
    lost the race and asserts only that whichever appends DID succeed have
    a correct, gapless, non-duplicated sequence — under postgres (CI, or
    local docker compose), it asserts the strict "all ten succeeded"
    guarantee.
    """

    def test_concurrent_appends_do_not_corrupt_sequence(self):
        import threading

        org = Organization.objects.create(name="Concurrency Org")
        errors = []

        def worker():
            try:
                with transaction.atomic():
                    append_entry(organization=org, action="CONCURRENT")
            except Exception as exc:  # noqa: BLE001 - captured for the assertion below
                errors.append(exc)
            finally:
                # Each thread gets its own thread-local DB connection (that's
                # exactly what makes this a real concurrency test — 10 genuine
                # connections contending on the AuditChainState row lock).
                # Django's test teardown only closes the MAIN thread's
                # connection, so without this each worker's connection leaks
                # and blocks DROP DATABASE at teardown ("database is being
                # accessed by other users") — which under Postgres fails the
                # whole run even though every assertion passed. Close it here.
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        if connection.vendor == "sqlite":
            # SQLite: some threads may have lost a file-lock race outright
            # (not a correctness bug — see class docstring). What matters is
            # that every append which DID succeed produced a valid, gapless,
            # non-duplicated chain — never a corrupted one.
            succeeded = 10 - len(errors)
            self.assertGreater(succeeded, 0, "every concurrent append failed — investigate separately from lock contention")
        else:
            # Postgres (or any backend with real row-level locking): no
            # excuse for a lost append — select_for_update() should
            # serialize all ten without error.
            self.assertEqual(errors, [])
            succeeded = 10

        entries = list(AuditTrail.objects.filter(organization=org).order_by("sequence"))
        self.assertEqual(len(entries), succeeded)
        # No gaps, no duplicates, regardless of how many succeeded.
        self.assertEqual([e.sequence for e in entries], list(range(1, succeeded + 1)))
        self.assertTrue(verify_chain(org).valid)
        result = verify_chain(org)
        self.assertTrue(result.valid)


class AuditChainVerifyViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.org = Organization.objects.create(name="API Verify Org")
        self.admin = self._user("org_admin_u", Role.ORG_ADMIN)
        self.auditor = self._user("auditor_u", Role.AUDITOR)
        self.analyst = self._user("analyst_u", Role.ANALYST)
        self.viewer = self._user("viewer_u", Role.VIEWER)

    def _user(self, name, role):
        u = User.objects.create_user(name, password="pw")
        Membership.objects.create(user=u, organization=self.org, role=role, active=True)
        return u

    def test_requires_authentication(self):
        response = self.client.get("/api/audit/verify/")
        self.assertEqual(response.status_code, drf_status.HTTP_401_UNAUTHORIZED)

    def test_denied_for_analyst(self):
        self.client.force_authenticate(self.analyst)
        self.assertEqual(self.client.get("/api/audit/verify/").status_code, drf_status.HTTP_403_FORBIDDEN)

    def test_denied_for_viewer(self):
        self.client.force_authenticate(self.viewer)
        self.assertEqual(self.client.get("/api/audit/verify/").status_code, drf_status.HTTP_403_FORBIDDEN)

    def test_allowed_for_org_admin_empty_chain(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/audit/verify/")
        self.assertEqual(response.status_code, drf_status.HTTP_200_OK)
        body = response.json()
        self.assertTrue(body["valid"])
        self.assertEqual(body["entries_checked"], 0)

    def test_allowed_for_auditor_valid_chain(self):
        append_entry(organization=self.org, action="A1")
        append_entry(organization=self.org, action="A2")
        self.client.force_authenticate(self.auditor)
        response = self.client.get("/api/audit/verify/")
        self.assertEqual(response.status_code, drf_status.HTTP_200_OK)
        body = response.json()
        self.assertTrue(body["valid"])
        self.assertEqual(body["entries_checked"], 2)

    def test_reports_broken_chain(self):
        entry = append_entry(organization=self.org, action="A1")
        with connection.cursor() as cur:
            cur.execute(
                "UPDATE audit_audittrail SET reason = %s WHERE id = %s",
                ["tampered", entry.id.hex if hasattr(entry.id, "hex") else str(entry.id)],
            )
        self.client.force_authenticate(self.admin)
        body = self.client.get("/api/audit/verify/").json()
        self.assertFalse(body["valid"])
        self.assertEqual(body["broken_at_sequence"], 1)


class VerifyAuditChainCommandTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Command Org")

    def test_valid_chain_reports_success_and_exits_cleanly(self):
        append_entry(organization=self.org, action="A1")
        out = StringIO()
        call_command("verify_audit_chain", stdout=out)
        self.assertIn("VALID", out.getvalue())

    def test_broken_chain_raises_command_error(self):
        entry = append_entry(organization=self.org, action="A1")
        with connection.cursor() as cur:
            cur.execute(
                "UPDATE audit_audittrail SET reason = %s WHERE id = %s",
                ["tampered", entry.id.hex if hasattr(entry.id, "hex") else str(entry.id)],
            )
        out = StringIO()
        with self.assertRaises(CommandError):
            call_command("verify_audit_chain", stdout=out)
        self.assertIn("BROKEN", out.getvalue())

    def test_single_organization_filter(self):
        other = Organization.objects.create(name="Other Org")
        append_entry(organization=other, action="A1")
        out = StringIO()
        call_command("verify_audit_chain", organization=str(self.org.id), stdout=out)
        self.assertIn("Command Org", out.getvalue())
        self.assertNotIn("Other Org", out.getvalue())

    def test_unknown_organization_id_raises(self):
        import uuid
        with self.assertRaises(CommandError):
            call_command("verify_audit_chain", organization=str(uuid.uuid4()))
