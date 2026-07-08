"""
Phase 7b -- AIAnnotation model tests. New file, separate from
tests_models.py, so this model's tests stay self-contained and don't
entangle with that file's own history.
"""
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase

from apps.ai.models import AIAnnotation, AIInteraction, TenantAIPolicy
from apps.core.models import DataSource, Organization
from apps.ingestion.models import EmissionRecord, UploadBatch


def _make_batch(org, ds):
    return UploadBatch.objects.create(organization=org, data_source=ds, file_name="ai_annotation_test.csv")


def _make_record(org, batch, **extra):
    defaults = dict(
        organization=org, batch=batch, row_index=1, raw_data_payload={"a": 1},
        status=EmissionRecord.RecordStatus.SUSPICIOUS, is_suspicious=True,
        normalized_value=500, normalized_unit="L", scope_category="SCOPE_1",
    )
    defaults.update(extra)
    return EmissionRecord.objects.create(**defaults)


def _make_interaction(org):
    return AIInteraction.objects.create(
        organization=org, capability="anomaly_detection", provider="echo", model_id="echo-1",
        outcome=AIInteraction.Outcome.OK, egress_tier_applied=TenantAIPolicy.EgressTier.REDACTED,
    )


class AIAnnotationCreationTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Annotation Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL,
        )
        self.batch = _make_batch(self.org, self.ds)
        self.record = _make_record(self.org, self.batch)
        self.interaction = _make_interaction(self.org)

    def test_create_minimal_annotation(self):
        annotation = AIAnnotation.objects.create(
            organization=self.org, record=self.record, interaction=self.interaction,
            capability=AIAnnotation.Capability.ANOMALY_DETECTION,
            explanation="Quantity is 5x the batch median.",
            contributing_factors=["unusually large purchase"],
            confidence=AIAnnotation.Confidence.HIGH,
            suggested_investigation="Confirm with the site's fuel log.",
        )
        self.assertIsNotNone(annotation.id)
        self.assertEqual(annotation.record, self.record)

    def test_annotation_reachable_from_record(self):
        annotation = AIAnnotation.objects.create(
            organization=self.org, record=self.record, interaction=self.interaction,
            capability=AIAnnotation.Capability.ANOMALY_DETECTION, explanation="x",
            confidence=AIAnnotation.Confidence.LOW, suggested_investigation="x",
        )
        self.assertIn(annotation, self.record.ai_annotations.all())

    def test_create_validation_assistance_annotation(self):
        # Phase 7d -- same model, second capability (see ADR 0011).
        annotation = AIAnnotation.objects.create(
            organization=self.org, record=self.record, interaction=self.interaction,
            capability=AIAnnotation.Capability.VALIDATION_ASSISTANCE,
            explanation="Quantity field is missing; likely a blank export cell.",
            contributing_factors=["quantity"],
            confidence=AIAnnotation.Confidence.MEDIUM,
            suggested_investigation="Re-enter the quantity from the source invoice.",
        )
        self.assertEqual(annotation.capability, AIAnnotation.Capability.VALIDATION_ASSISTANCE)
        self.assertIn(annotation, self.record.ai_annotations.all())

    def test_both_capabilities_coexist_on_the_same_record(self):
        anomaly = AIAnnotation.objects.create(
            organization=self.org, record=self.record, interaction=self.interaction,
            capability=AIAnnotation.Capability.ANOMALY_DETECTION, explanation="x",
            confidence=AIAnnotation.Confidence.LOW, suggested_investigation="x",
        )
        validation = AIAnnotation.objects.create(
            organization=self.org, record=self.record, interaction=self.interaction,
            capability=AIAnnotation.Capability.VALIDATION_ASSISTANCE, explanation="y",
            confidence=AIAnnotation.Confidence.LOW, suggested_investigation="y",
        )
        capabilities = set(self.record.ai_annotations.values_list("capability", flat=True))
        self.assertEqual(capabilities, {"ANOMALY_DETECTION", "VALIDATION_ASSISTANCE"})
        self.assertIn(anomaly, self.record.ai_annotations.all())
        self.assertIn(validation, self.record.ai_annotations.all())

    def test_multiple_annotations_can_accumulate_per_record(self):
        AIAnnotation.objects.create(
            organization=self.org, record=self.record, interaction=self.interaction,
            capability=AIAnnotation.Capability.ANOMALY_DETECTION, explanation="first",
            confidence=AIAnnotation.Confidence.LOW, suggested_investigation="x",
        )
        AIAnnotation.objects.create(
            organization=self.org, record=self.record, interaction=_make_interaction(self.org),
            capability=AIAnnotation.Capability.ANOMALY_DETECTION, explanation="second",
            confidence=AIAnnotation.Confidence.HIGH, suggested_investigation="y",
        )
        self.assertEqual(self.record.ai_annotations.count(), 2)


class AIAnnotationImmutabilityTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Immutable Annotation Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL,
        )
        self.batch = _make_batch(self.org, self.ds)
        self.record = _make_record(self.org, self.batch)
        self.interaction = _make_interaction(self.org)
        self.annotation = AIAnnotation.objects.create(
            organization=self.org, record=self.record, interaction=self.interaction,
            capability=AIAnnotation.Capability.ANOMALY_DETECTION, explanation="original",
            confidence=AIAnnotation.Confidence.MEDIUM, suggested_investigation="x",
        )

    def test_instance_save_after_mutation_raises(self):
        self.annotation.explanation = "edited"
        with self.assertRaises(ValidationError):
            self.annotation.save()

    def test_instance_delete_raises(self):
        with self.assertRaises(ValidationError):
            self.annotation.delete()
        self.assertTrue(AIAnnotation.objects.filter(pk=self.annotation.pk).exists())

    def test_bulk_update_raises(self):
        with self.assertRaises(ValidationError):
            AIAnnotation.objects.filter(pk=self.annotation.pk).update(explanation="bulk edited")

    def test_bulk_delete_raises(self):
        with self.assertRaises(ValidationError):
            AIAnnotation.objects.filter(pk=self.annotation.pk).delete()
        self.assertTrue(AIAnnotation.objects.filter(pk=self.annotation.pk).exists())


class AIAnnotationProtectedForeignKeyTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Protected FK Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL,
        )
        self.batch = _make_batch(self.org, self.ds)
        self.record = _make_record(self.org, self.batch)
        self.interaction = _make_interaction(self.org)
        AIAnnotation.objects.create(
            organization=self.org, record=self.record, interaction=self.interaction,
            capability=AIAnnotation.Capability.ANOMALY_DETECTION, explanation="x",
            confidence=AIAnnotation.Confidence.LOW, suggested_investigation="x",
        )

    def test_organization_protected_once_it_has_annotations(self):
        with self.assertRaises(ProtectedError):
            self.org.delete()

    def test_interaction_protected_once_referenced(self):
        with self.assertRaises(ProtectedError):
            self.interaction.delete()

    def test_no_field_on_this_model_uses_set_null(self):
        # Structural proof this model can never trigger the SET_NULL-
        # cascade-vs-blocked-update landmine class of bug found earlier.
        for f in AIAnnotation._meta.fields:
            if f.is_relation:
                self.assertNotEqual(
                    f.remote_field.on_delete.__name__, "SET_NULL",
                    f"{f.name} must not be SET_NULL on an immutable, bulk-update-blocked model",
                )


class AIAnnotationOneRowPerOrgConstraintNotEnforcedTests(TestCase):
    """Confirms the DELIBERATE absence of a uniqueness constraint -- see
    the model's own docstring for why idempotency is a service-layer
    concern, not a DB constraint, here."""

    def test_two_annotations_same_record_same_capability_both_persist(self):
        org = Organization.objects.create(name="No Constraint Org")
        ds = DataSource.objects.create(organization=org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL)
        batch = _make_batch(org, ds)
        record = _make_record(org, batch)

        AIAnnotation.objects.create(
            organization=org, record=record, interaction=_make_interaction(org),
            capability=AIAnnotation.Capability.ANOMALY_DETECTION, explanation="a",
            confidence=AIAnnotation.Confidence.LOW, suggested_investigation="x",
        )
        try:
            with transaction.atomic():
                AIAnnotation.objects.create(
                    organization=org, record=record, interaction=_make_interaction(org),
                    capability=AIAnnotation.Capability.ANOMALY_DETECTION, explanation="b",
                    confidence=AIAnnotation.Confidence.HIGH, suggested_investigation="y",
                )
        except IntegrityError:
            self.fail("No uniqueness constraint should exist on (record, capability).")
        self.assertEqual(AIAnnotation.objects.filter(record=record).count(), 2)
