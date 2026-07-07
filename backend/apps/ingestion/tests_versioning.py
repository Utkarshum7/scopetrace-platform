"""
Phase 6b — immutable EmissionRecordVersion history: automatic version
creation on save()/bulk_create()/recalculation, immutability enforcement
(instance + QuerySet level), duplicate-prevention for unchanged saves,
concurrent-update safety, tenant isolation, the historical-record backfill
migration, and the three new /versions/... API endpoints.
"""
import threading

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.test import TestCase, TransactionTestCase
from rest_framework import status as drf_status
from rest_framework.test import APIClient

from apps.accounts.models import Membership, Role
from apps.core.models import DataSource, Organization
from apps.ingestion.models import EmissionRecord, EmissionRecordVersion, UploadBatch
from apps.ingestion.services.versioning import (
    create_initial_versions_bulk,
    create_version_for_calculation_change,
    create_version_if_changed,
)

User = get_user_model()


def _make_batch(org, data_source, user=None):
    return UploadBatch.objects.create(
        organization=org, data_source=data_source, file_name="versions_test.csv",
        uploaded_by=user, total_rows=1,
    )


class VersionCreationTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Version Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP Fuel", source_type=DataSource.SourceType.SAP_FUEL,
        )
        self.batch = _make_batch(self.org, self.ds)

    def test_create_produces_version_1(self):
        record = EmissionRecord.objects.create(
            organization=self.org, batch=self.batch, row_index=1,
            raw_data_payload={"a": 1}, status=EmissionRecord.RecordStatus.DRAFT,
            normalized_value=100, normalized_unit="L",
        )
        versions = list(record.versions.all())
        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0].version_number, 1)
        self.assertEqual(versions[0].status, EmissionRecord.RecordStatus.DRAFT)
        self.assertEqual(versions[0].record_uuid_backup, record.id)
        self.assertEqual(versions[0].organization_id, self.org.id)

    def test_business_field_change_creates_new_version(self):
        record = EmissionRecord.objects.create(
            organization=self.org, batch=self.batch, row_index=1,
            raw_data_payload={"a": 1}, normalized_value=100, normalized_unit="L",
        )
        record.normalized_value = 200
        record.save()
        versions = list(record.versions.order_by("version_number"))
        self.assertEqual(len(versions), 2)
        self.assertEqual(versions[0].normalized_value, 100)
        self.assertEqual(versions[1].normalized_value, 200)

    def test_unchanged_resave_does_not_create_duplicate_version(self):
        record = EmissionRecord.objects.create(
            organization=self.org, batch=self.batch, row_index=1,
            raw_data_payload={"a": 1}, normalized_value=100, normalized_unit="L",
        )
        self.assertEqual(record.versions.count(), 1)
        # Re-save with no field changes at all.
        record.save()
        self.assertEqual(record.versions.count(), 1)

    def test_version_numbers_are_monotonic_and_gapless(self):
        record = EmissionRecord.objects.create(
            organization=self.org, batch=self.batch, row_index=1,
            raw_data_payload={"a": 1}, normalized_value=100, normalized_unit="L",
        )
        for v in (200, 300, 400):
            record.normalized_value = v
            record.save()
        numbers = list(
            record.versions.order_by("version_number").values_list("version_number", flat=True)
        )
        self.assertEqual(numbers, [1, 2, 3, 4])

    def test_bulk_create_produces_initial_versions(self):
        records = [
            EmissionRecord(
                organization=self.org, batch=self.batch, row_index=i,
                raw_data_payload={"a": i}, normalized_value=i * 10, normalized_unit="L",
            )
            for i in range(1, 4)
        ]
        EmissionRecord.objects.bulk_create(records)
        create_initial_versions_bulk(records)
        for record in records:
            versions = list(EmissionRecordVersion.objects.filter(record=record))
            self.assertEqual(len(versions), 1)
            self.assertEqual(versions[0].version_number, 1)

    def test_recalculation_creates_version_even_without_record_field_change(self):
        # Recalculation only changes WHICH EmissionCalculation is current —
        # it does not touch any EmissionRecord field, so the diff-based
        # create_version_if_changed would never fire. This is why
        # create_version_for_calculation_change exists as its own entry
        # point (see apps/ingestion/services/versioning.py).
        record = EmissionRecord.objects.create(
            organization=self.org, batch=self.batch, row_index=1,
            raw_data_payload={"a": 1}, normalized_value=100, normalized_unit="L",
        )
        self.assertEqual(record.versions.count(), 1)
        version = create_version_for_calculation_change(
            record=record, changed_by=None, reason="Manual recalculation",
        )
        self.assertIsNotNone(version)
        self.assertEqual(version.version_number, 2)
        self.assertEqual(record.versions.count(), 2)

    def test_no_change_returns_none(self):
        record = EmissionRecord.objects.create(
            organization=self.org, batch=self.batch, row_index=1,
            raw_data_payload={"a": 1}, normalized_value=100, normalized_unit="L",
        )
        result = create_version_if_changed(old_record=record, new_record=record)
        self.assertIsNone(result)


class VersionImmutabilityTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Immutable Version Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP Fuel", source_type=DataSource.SourceType.SAP_FUEL,
        )
        self.batch = _make_batch(self.org, self.ds)
        self.record = EmissionRecord.objects.create(
            organization=self.org, batch=self.batch, row_index=1,
            raw_data_payload={"a": 1}, normalized_value=100, normalized_unit="L",
        )
        self.version = self.record.versions.get(version_number=1)

    def test_instance_resave_raises(self):
        self.version.reason = "edited after the fact"
        with self.assertRaises(ValidationError):
            self.version.save()

    def test_instance_delete_raises(self):
        with self.assertRaises(ValidationError):
            self.version.delete()

    def test_bulk_delete_is_blocked(self):
        with self.assertRaises(ValidationError):
            EmissionRecordVersion.objects.filter(record=self.record).delete()
        self.assertTrue(EmissionRecordVersion.objects.filter(pk=self.version.pk).exists())

    def test_bulk_update_is_blocked(self):
        with self.assertRaises(ValidationError):
            EmissionRecordVersion.objects.filter(record=self.record).update(reason="bulk edited")
        self.version.refresh_from_db()
        self.assertIsNone(self.version.reason)


class VersionTenantIsolationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.org_a = Organization.objects.create(name="Org A")
        self.org_b = Organization.objects.create(name="Org B")
        self.ds_a = DataSource.objects.create(
            organization=self.org_a, name="SAP Fuel A", source_type=DataSource.SourceType.SAP_FUEL,
        )
        self.batch_a = _make_batch(self.org_a, self.ds_a)
        self.record_a = EmissionRecord.objects.create(
            organization=self.org_a, batch=self.batch_a, row_index=1,
            raw_data_payload={"a": 1}, normalized_value=100, normalized_unit="L",
        )

        self.user_b = User.objects.create_user(username="org_b_analyst", password="pw")
        Membership.objects.create(
            user=self.user_b, organization=self.org_b, role=Role.ANALYST, active=True
        )
        self.client.force_authenticate(user=self.user_b)

    def test_versions_list_hidden_across_tenants(self):
        response = self.client.get(f"/api/records/{self.record_a.id}/versions/")
        self.assertEqual(response.status_code, drf_status.HTTP_404_NOT_FOUND)

    def test_version_detail_hidden_across_tenants(self):
        response = self.client.get(f"/api/records/{self.record_a.id}/versions/1/")
        self.assertEqual(response.status_code, drf_status.HTTP_404_NOT_FOUND)

    def test_version_compare_hidden_across_tenants(self):
        response = self.client.get(f"/api/records/{self.record_a.id}/versions/1/compare/")
        self.assertEqual(response.status_code, drf_status.HTTP_404_NOT_FOUND)

    def test_direct_model_query_still_isolable_by_organization(self):
        # Belt-and-suspenders: organization is denormalized onto the version
        # row precisely so a tenant-scoped query never needs a join through
        # a possibly-null `record` FK (record is SET_NULL, not PROTECT).
        self.assertEqual(
            EmissionRecordVersion.objects.filter(organization=self.org_b).count(), 0
        )
        self.assertEqual(
            EmissionRecordVersion.objects.filter(organization=self.org_a).count(), 1
        )


class VersionAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.org = Organization.objects.create(name="Version API Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP Fuel", source_type=DataSource.SourceType.SAP_FUEL,
        )
        self.batch = _make_batch(self.org, self.ds)
        self.user = User.objects.create_user(username="version_api_analyst", password="pw")
        Membership.objects.create(
            user=self.user, organization=self.org, role=Role.ANALYST, active=True
        )
        self.client.force_authenticate(user=self.user)
        self.record = EmissionRecord.objects.create(
            organization=self.org, batch=self.batch, row_index=1,
            raw_data_payload={"a": 1}, normalized_value=100, normalized_unit="L",
        )
        self.record.normalized_value = 200
        self.record.save()

    def test_list_versions_newest_first(self):
        response = self.client.get(f"/api/records/{self.record.id}/versions/")
        self.assertEqual(response.status_code, drf_status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]["version_number"], 2)
        self.assertEqual(data[1]["version_number"], 1)

    def test_retrieve_specific_version(self):
        response = self.client.get(f"/api/records/{self.record.id}/versions/1/")
        self.assertEqual(response.status_code, drf_status.HTTP_200_OK)
        self.assertEqual(response.json()["normalized_value"], "100.000000")

    def test_retrieve_missing_version_404s(self):
        response = self.client.get(f"/api/records/{self.record.id}/versions/99/")
        self.assertEqual(response.status_code, drf_status.HTTP_404_NOT_FOUND)

    def test_compare_historical_version_against_current(self):
        response = self.client.get(f"/api/records/{self.record.id}/versions/1/compare/")
        self.assertEqual(response.status_code, drf_status.HTTP_200_OK)
        data = response.json()
        self.assertFalse(data["is_current_state"])
        self.assertIn("normalized_value", data["diff"])
        self.assertEqual(data["diff"]["normalized_value"]["version"], "100.000000")
        self.assertEqual(data["diff"]["normalized_value"]["current"], "200.000000")

    def test_compare_current_version_shows_no_diff(self):
        response = self.client.get(f"/api/records/{self.record.id}/versions/2/compare/")
        self.assertEqual(response.status_code, drf_status.HTTP_200_OK)
        data = response.json()
        self.assertTrue(data["is_current_state"])
        self.assertEqual(data["diff"], {})

    def test_unauthenticated_request_is_rejected(self):
        self.client.force_authenticate(user=None)
        response = self.client.get(f"/api/records/{self.record.id}/versions/")
        self.assertEqual(response.status_code, drf_status.HTTP_401_UNAUTHORIZED)


class ApprovalIntegrationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.org = Organization.objects.create(name="Approval Version Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP Fuel", source_type=DataSource.SourceType.SAP_FUEL,
        )
        self.batch = _make_batch(self.org, self.ds)
        self.user = User.objects.create_user(username="approver", password="pw")
        Membership.objects.create(
            user=self.user, organization=self.org, role=Role.ORG_ADMIN, active=True
        )
        self.client.force_authenticate(user=self.user)
        # Phase 6c: approve() now requires SUBMITTED first.
        self.record = EmissionRecord.objects.create(
            organization=self.org, batch=self.batch, row_index=1,
            raw_data_payload={"a": 1}, status=EmissionRecord.RecordStatus.SUBMITTED,
            normalized_value=100, normalized_unit="L",
        )

    def test_approval_creates_new_version_and_cross_references_audit_trail(self):
        from apps.audit.models import AuditTrail

        response = self.client.post(
            f"/api/records/{self.record.id}/approve/",
            data={"reason": "Looks correct"}, format="json",
        )
        self.assertEqual(response.status_code, drf_status.HTTP_200_OK)

        versions = list(self.record.versions.order_by("version_number"))
        self.assertEqual(len(versions), 2)
        self.assertEqual(versions[1].status, EmissionRecord.RecordStatus.APPROVED)
        self.assertEqual(versions[1].created_by, self.user)

        audit = AuditTrail.objects.get(record_uuid_backup=self.record.id, action="RECORD_APPROVAL")
        self.assertEqual(audit.changes["record_version"], versions[1].version_number)

    def test_approved_record_version_carries_approval_fields(self):
        self.client.post(
            f"/api/records/{self.record.id}/approve/",
            data={"reason": "Looks correct"}, format="json",
        )
        latest = self.record.versions.order_by("-version_number").first()
        self.assertEqual(latest.approved_by, self.user)
        self.assertIsNotNone(latest.approved_at)


class ConcurrentVersioningTests(TransactionTestCase):
    """Mirrors apps.audit.tests.ConcurrentAppendTests: real threads, real
    per-thread connections. Under SQLite, contending writers can lose a
    file-lock race (not a correctness bug); what matters is that whichever
    saves DID succeed produced a correct, gapless, non-duplicated version
    sequence — never a corrupted one. Under Postgres, select_for_update()
    on the record's own row should serialize every save; see
    docs/GOVERNANCE.md for how this is verified against real Postgres."""

    def test_concurrent_saves_do_not_corrupt_version_sequence(self):
        org = Organization.objects.create(name="Concurrent Version Org")
        ds = DataSource.objects.create(
            organization=org, name="SAP Fuel", source_type=DataSource.SourceType.SAP_FUEL,
        )
        batch = _make_batch(org, ds)
        record = EmissionRecord.objects.create(
            organization=org, batch=batch, row_index=1,
            raw_data_payload={"a": 1}, normalized_value=100, normalized_unit="L",
        )

        errors = []

        def worker(new_value):
            try:
                with transaction.atomic():
                    r = EmissionRecord.objects.get(pk=record.pk)
                    r.normalized_value = new_value
                    r.save()
            except Exception as exc:  # noqa: BLE001 - captured for the assertion below
                errors.append(exc)
            finally:
                # Same fix already applied in apps.audit.tests: each thread
                # opens its own connection; Django's teardown only closes
                # the main thread's, so leaving this open blocks Postgres's
                # DROP DATABASE at teardown even when every assertion passes.
                connection.close()

        threads = [
            threading.Thread(target=worker, args=(100 + (i + 1) * 10,)) for i in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        if connection.vendor == "sqlite":
            # SQLite locks at the file level, not the row level — ten real
            # threads holding a transaction open through full_clean() +
            # versioning logic frequently make EVERY thread lose the race
            # ("database is locked"), which is a SQLite coarseness artifact,
            # not evidence that EmissionRecord.save()'s select_for_update()
            # locking is broken. Observed empirically: reruns of this exact
            # test locally saw 0/10, 10/10, and figures in between across
            # different runs. Nothing to assert about *how many* succeed
            # here — only that whichever did produced a valid sequence.
            succeeded = 10 - len(errors)
        else:
            self.assertEqual(errors, [])
            succeeded = 10

        versions = list(
            EmissionRecordVersion.objects.filter(record=record).order_by("version_number")
        )
        # version 1 (initial create) + one per successful concurrent save.
        self.assertEqual(len(versions), succeeded + 1)
        numbers = [v.version_number for v in versions]
        self.assertEqual(numbers, list(range(1, succeeded + 2)))
        # No duplicate version_number was ever committed for this record —
        # the DB-level UniqueConstraint would have raised if one had.
        self.assertEqual(len(set(numbers)), len(numbers))


class BackfillMigrationTests(TestCase):
    """Exercises the same logic as
    apps/ingestion/migrations/0007_backfill_initial_record_versions.py
    directly against the real apps registry — a migration test proper would
    need MigratorTestCase-style rollback/replay machinery this project
    doesn't otherwise use; this instead proves the backfill's actual
    behavior (one version-1 snapshot per pre-existing record, no version
    for records that already have one) using the same apps.get_model-style
    historical-model access pattern the migration itself uses."""

    def test_backfill_creates_exactly_one_version_per_existing_record(self):
        import importlib

        from django.apps import apps as real_apps

        org = Organization.objects.create(name="Backfill Org")
        ds = DataSource.objects.create(
            organization=org, name="SAP Fuel", source_type=DataSource.SourceType.SAP_FUEL,
        )
        batch = _make_batch(org, ds)

        pre_existing = EmissionRecord.objects.create(
            organization=org, batch=batch, row_index=1,
            raw_data_payload={"a": 1}, normalized_value=100, normalized_unit="L",
        )

        # Simulate a record that predates EmissionRecordVersion's existence:
        # raw-SQL delete of the version its own save() already created —
        # the only way to remove it at all, since normal ORM delete() and
        # bulk QuerySet.delete() are both blocked by design (see
        # VersionImmutabilityTests above).
        with connection.cursor() as cur:
            cur.execute(
                "DELETE FROM ingestion_emissionrecordversion WHERE record_id = %s",
                [pre_existing.id.hex if hasattr(pre_existing.id, "hex") else str(pre_existing.id)],
            )
        self.assertEqual(EmissionRecordVersion.objects.filter(record=pre_existing).count(), 0)

        # Directly invoke the migration's own function against the live
        # apps registry — get_model()'s interface is identical for a
        # migration's historical-state apps registry and the real one, so
        # this proves the backfill's actual output shape without needing a
        # full migrate-back-and-forward cycle.
        backfill_module = importlib.import_module(
            "apps.ingestion.migrations.0007_backfill_initial_record_versions"
        )
        backfill_module.backfill_versions(real_apps, schema_editor=None)

        versions = list(EmissionRecordVersion.objects.filter(record=pre_existing))
        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0].version_number, 1)
        self.assertEqual(versions[0].normalized_value, 100)
        self.assertIn("Backfilled by migration 0007", versions[0].reason)
