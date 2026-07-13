"""
Phase 7.5 (H1, Finding 2) -- EmissionFactorDataset "immutable once ACTIVE"
enforcement at the persistence layer.

Before this milestone the invariant lived only in clean(), which Django
invokes for admin/ModelForm writes but NOT for programmatic .save()/
.objects.create() or bulk .update(). These tests pin the enforcement on
every write path: normal save, bulk update, and the model-validation
(clean/full_clean) path the admin uses, plus proof that the legitimate
status-only transitions the importer relies on are still allowed.
"""
from datetime import date

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.carbon.models import EmissionFactorDataset, Publisher
from apps.carbon.tests import factories as f

DRAFT = EmissionFactorDataset.Status.DRAFT
ACTIVE = EmissionFactorDataset.Status.ACTIVE
SUPERSEDED = EmissionFactorDataset.Status.SUPERSEDED


class ActiveImmutabilitySaveTests(TestCase):
    def _fresh(self, ds):
        # Reload so _state.adding is False (an existing row being updated),
        # exactly as a real edit-after-load write would look.
        return EmissionFactorDataset.objects.get(pk=ds.pk)

    def test_creating_an_active_dataset_is_allowed(self):
        # The create path (_state.adding) must never trip the guard.
        ds = f.dataset(status=ACTIVE, version="c1")
        self.assertEqual(ds.status, ACTIVE)

    def test_status_transition_on_active_via_save_is_allowed(self):
        ds = self._fresh(f.dataset(status=ACTIVE, version="c2"))
        ds.status = SUPERSEDED
        ds.save()
        self.assertEqual(self._fresh(ds).status, SUPERSEDED)

    def test_changing_a_protected_field_on_active_via_save_raises(self):
        ds = self._fresh(f.dataset(status=ACTIVE, version="c3", priority=100))
        ds.priority = 999
        with self.assertRaises(ValidationError):
            ds.save()
        self.assertEqual(self._fresh(ds).priority, 100)  # unchanged

    def test_changing_version_on_active_via_save_raises(self):
        ds = self._fresh(f.dataset(status=ACTIVE, version="c4"))
        ds.version = "tampered"
        with self.assertRaises(ValidationError):
            ds.save()

    def test_changing_a_protected_field_on_draft_via_save_is_allowed(self):
        ds = self._fresh(f.dataset(status=DRAFT, version="c5", priority=100))
        ds.priority = 5
        ds.save()
        self.assertEqual(self._fresh(ds).priority, 5)

    def test_non_protected_field_on_active_via_save_is_allowed(self):
        # metadata/import_notes are deliberately NOT frozen -- documents the
        # exact boundary of the invariant (same field set clean() always used).
        ds = self._fresh(f.dataset(status=ACTIVE, version="c6"))
        ds.metadata = {"note": "annotated after activation"}
        ds.save()
        self.assertEqual(self._fresh(ds).metadata, {"note": "annotated after activation"})


class ActiveImmutabilityBulkUpdateTests(TestCase):
    def test_bulk_update_of_protected_field_on_active_raises(self):
        ds = f.dataset(status=ACTIVE, version="b1", priority=100)
        with self.assertRaises(ValidationError):
            EmissionFactorDataset.objects.filter(pk=ds.pk).update(priority=1)
        self.assertEqual(EmissionFactorDataset.objects.get(pk=ds.pk).priority, 100)

    def test_bulk_status_only_transition_on_active_is_allowed(self):
        # This is exactly the importer's supersede: .filter(status=ACTIVE)
        # .update(status=SUPERSEDED). Must remain allowed.
        f.dataset(status=ACTIVE, version="b2")
        EmissionFactorDataset.objects.filter(status=ACTIVE).update(status=SUPERSEDED)
        self.assertFalse(EmissionFactorDataset.objects.filter(status=ACTIVE).exists())

    def test_bulk_update_of_protected_field_on_draft_only_is_allowed(self):
        ds = f.dataset(status=DRAFT, version="b3", valid_from=date(2024, 1, 1))
        EmissionFactorDataset.objects.filter(pk=ds.pk).update(valid_from=date(2023, 1, 1))
        self.assertEqual(EmissionFactorDataset.objects.get(pk=ds.pk).valid_from, date(2023, 1, 1))

    def test_bulk_update_is_blocked_if_any_matched_row_is_active(self):
        # Fail-closed: a queryset spanning DRAFT + ACTIVE rows is refused
        # wholesale (Django .update() is all-or-nothing).
        f.dataset(status=DRAFT, version="b4a")
        f.dataset(status=ACTIVE, version="b4b")
        with self.assertRaises(ValidationError):
            EmissionFactorDataset.objects.filter(
                publisher=Publisher.DEFRA
            ).update(source_url="https://example.test/x")

    def test_empty_or_nonmatching_update_does_not_raise(self):
        # No ACTIVE rows in scope -> guard is a no-op even for protected fields.
        f.dataset(status=DRAFT, version="b5")
        EmissionFactorDataset.objects.filter(status=ACTIVE).update(priority=42)  # matches nothing


class ActiveImmutabilityCleanTests(TestCase):
    """The admin/ModelForm path routes through full_clean() -> clean(); it
    must still enforce the invariant (behavior preserved, not regressed)."""

    def test_full_clean_on_active_with_changed_protected_field_raises(self):
        ds = EmissionFactorDataset.objects.get(pk=f.dataset(status=ACTIVE, version="k1").pk)
        ds.source_filename = "swapped.csv"
        with self.assertRaises(ValidationError):
            ds.full_clean()

    def test_full_clean_on_active_status_only_change_is_allowed(self):
        ds = EmissionFactorDataset.objects.get(pk=f.dataset(status=ACTIVE, version="k2").pk)
        ds.status = SUPERSEDED
        ds.full_clean()  # must not raise
