"""
Regression tests for a pre-Phase-7 bug found while writing a Phase 7a model
test (apps.ai.tests_models.AIInteractionTests.test_actor_field_is_set_null_on_delete):

EmissionRecordQuerySet.update() (and, it turns out, the identical override
on EmissionRecordVersionQuerySet and AuditTrailQuerySet) raised
ValidationError on EVERY call, including ones that match zero rows. Django's
deletion Collector always issues a `.update(<field>=None)` call against each
model with a SET_NULL foreign key to the row being deleted -- so deleting
ANY User (not just one who had approved/touched a record) hit this guard and
raised, because the guard fired on the call itself rather than on whether
any row was actually affected.

Fixed by SetNullCascadeSafeQuerySet (apps/core/querysets.py): update() now
lets through exactly the shape Django's own cascade produces (setting only
fields declared on_delete=SET_NULL on this model, only to None) while still
blocking every real bulk business-field edit -- see that class's docstring.
"""
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import TestCase

from apps.audit.models import AuditTrail
from apps.audit.services import verify_chain
from apps.core.models import DataSource, Organization
from apps.ingestion.models import EmissionRecord, EmissionRecordVersion, UploadBatch
from apps.ingestion.services.workflow import transition_record

User = get_user_model()
RS = EmissionRecord.RecordStatus


def _make_batch(org, data_source):
    return UploadBatch.objects.create(organization=org, data_source=data_source, file_name="del.csv")


def _make_record(org, batch, row_index=1):
    return EmissionRecord.objects.create(
        organization=org, batch=batch, row_index=row_index,
        raw_data_payload={"a": 1}, status=RS.DRAFT,
        normalized_value=100, normalized_unit="L",
    )


class UserDeletionDoesNotBreakBulkGuardsTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="User Deletion Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL,
        )

    def test_delete_user_with_no_referencing_records_succeeds(self):
        # This is the exact repro from the bug report: a user with ZERO
        # EmissionRecord/EmissionRecordVersion/AuditTrail rows referencing
        # it still hit the queryset-level guard, because Django's collector
        # calls .update(approved_by=None) etc. regardless of match count.
        user = User.objects.create_user("nobody", password="pw")
        user.delete()
        self.assertFalse(User.objects.filter(username="nobody").exists())

    def test_delete_user_who_approved_a_record_nulls_approved_by_only(self):
        approver = User.objects.create_user("approver", password="pw")
        batch = _make_batch(self.org, self.ds)
        record = _make_record(self.org, batch)

        with transaction.atomic():
            locked = EmissionRecord.objects.select_for_update().get(pk=record.pk)
            transition_record(record=locked, target_status=RS.SUBMITTED, actor=approver)
        with transaction.atomic():
            locked = EmissionRecord.objects.select_for_update().get(pk=record.pk)
            transition_record(record=locked, target_status=RS.APPROVED, actor=approver)

        record.refresh_from_db()
        self.assertEqual(record.status, RS.APPROVED)
        self.assertEqual(record.approved_by_id, approver.id)
        version_count_before = EmissionRecordVersion.objects.filter(record=record).count()
        audit_count_before = AuditTrail.objects.filter(organization=self.org).count()
        self.assertGreater(version_count_before, 0)
        self.assertGreater(audit_count_before, 0)

        approver.delete()

        record.refresh_from_db()
        # SET_NULL did what it's supposed to: the dangling reference is
        # gone, nothing else about the locked record changed.
        self.assertIsNone(record.approved_by_id)
        self.assertEqual(record.status, RS.APPROVED)
        self.assertEqual(record.normalized_value, 100)

        # The cascade must not have created a new version or a new audit
        # entry (it's Django's raw SQL UPDATE bypassing save()/clean()
        # entirely -- there is no "changed_by" for it to attribute to) and
        # must not have touched the existing ones either.
        self.assertEqual(
            EmissionRecordVersion.objects.filter(record=record).count(),
            version_count_before,
        )
        self.assertEqual(
            AuditTrail.objects.filter(organization=self.org).count(),
            audit_count_before,
        )

    def test_bulk_business_field_update_is_still_blocked(self):
        # The guard's actual purpose -- blocking bulk mutation of business
        # data bypassing audit-trail/version-history -- must survive the fix.
        batch = _make_batch(self.org, self.ds)
        _make_record(self.org, batch)
        with self.assertRaises(ValidationError):
            EmissionRecord.objects.filter(organization=self.org).update(status=RS.APPROVED)

    def test_bulk_delete_is_still_blocked(self):
        batch = _make_batch(self.org, self.ds)
        _make_record(self.org, batch)
        with self.assertRaises(ValidationError):
            EmissionRecord.objects.filter(organization=self.org).delete()

    def test_approved_by_cannot_be_bulk_set_to_a_non_null_value(self):
        # Only the None-valued, SET_NULL-declared shape is permitted --
        # setting approved_by to an actual user via bulk update is exactly
        # the kind of audit-bypassing business mutation the guard exists to
        # block, and must still be rejected even though "approved_by" is on
        # the allowed-field list.
        other_user = User.objects.create_user("sneaky", password="pw")
        batch = _make_batch(self.org, self.ds)
        _make_record(self.org, batch)
        with self.assertRaises(ValidationError):
            EmissionRecord.objects.filter(organization=self.org).update(approved_by=other_user)

    def test_audit_hash_chain_known_limitation_after_approver_deletion(self):
        # Documents a real, pre-existing design tension surfaced (not
        # created) by this fix: AuditTrail.entry_hash hashes changed_by_id
        # at write time (apps.audit.services._canonical_payload). Deleting
        # a user who ever appears as `changed_by` nulls that FK via the
        # same SET_NULL cascade this fix unblocks, so a later verify_chain()
        # recomputes a DIFFERENT hash than the one stored and reports the
        # chain as broken -- indistinguishable from real tampering by
        # verify_chain() alone. This was previously impossible to hit
        # because User.delete() failed outright for any user who had ever
        # touched an audited record. Not fixed here: doing so would mean
        # changing what the hash chain commits to (e.g. snapshotting an
        # immutable actor identifier at entry-creation time instead of
        # reading the live FK), a design change out of scope for this bug
        # fix. Flagged for Phase 6f/7 security hardening follow-up.
        approver = User.objects.create_user("hash_actor", password="pw")
        batch = _make_batch(self.org, self.ds)
        record = _make_record(self.org, batch)
        with transaction.atomic():
            locked = EmissionRecord.objects.select_for_update().get(pk=record.pk)
            transition_record(record=locked, target_status=RS.SUBMITTED, actor=approver)

        result_before = verify_chain(self.org)
        self.assertTrue(result_before.valid)

        approver.delete()

        result_after = verify_chain(self.org)
        self.assertFalse(
            result_after.valid,
            "If this starts passing, the known hash-chain limitation "
            "documented above has been independently fixed -- update this "
            "test (and the docstring) rather than leaving it stale.",
        )
