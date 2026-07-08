"""
Phase 7c -- AIFactorRecommendation model tests. New file, separate from
tests_models.py/tests_annotations.py, so this model's tests stay
self-contained.
"""
from django.core.exceptions import ValidationError
from django.db.models import ProtectedError
from django.test import TestCase

from apps.ai.models import AIFactorRecommendation, AIInteraction, TenantAIPolicy
from apps.carbon.tests.factories import activity_type, dataset, factor
from apps.core.models import DataSource, Organization
from apps.ingestion.models import EmissionRecord, UploadBatch


def _make_batch(org, ds):
    return UploadBatch.objects.create(organization=org, data_source=ds, file_name="factor_rec_test.csv")


def _make_record(org, batch, **extra):
    defaults = dict(
        organization=org, batch=batch, row_index=1, raw_data_payload={"a": 1},
        status=EmissionRecord.RecordStatus.DRAFT, normalized_value=500,
        normalized_unit="L", scope_category="SCOPE_1",
    )
    defaults.update(extra)
    return EmissionRecord.objects.create(**defaults)


def _make_interaction(org):
    return AIInteraction.objects.create(
        organization=org, capability="factor_recommendation", provider="echo", model_id="echo-1",
        outcome=AIInteraction.Outcome.OK, egress_tier_applied=TenantAIPolicy.EgressTier.REDACTED,
    )


class AIFactorRecommendationCreationTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Factor Rec Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL,
        )
        self.batch = _make_batch(self.org, self.ds)
        self.record = _make_record(self.org, self.batch)
        self.interaction = _make_interaction(self.org)
        self.activity_type = activity_type()
        self.dataset = dataset()
        self.factor = factor(self.dataset, self.activity_type)

    def test_create_with_a_recommended_factor(self):
        rec = AIFactorRecommendation.objects.create(
            organization=self.org, record=self.record, interaction=self.interaction,
            recommended_factor=self.factor, confidence=AIFactorRecommendation.Confidence.HIGH,
            explanation="Best regional match.", reasoning="Region and date both align.",
            alternative_candidates=["candidate_2"],
        )
        self.assertIsNotNone(rec.id)
        self.assertEqual(rec.recommended_factor, self.factor)

    def test_create_with_no_recommended_factor(self):
        # A valid, honest outcome -- the AI can recommend none of the
        # candidates it was shown.
        rec = AIFactorRecommendation.objects.create(
            organization=self.org, record=self.record, interaction=self.interaction,
            recommended_factor=None, confidence=AIFactorRecommendation.Confidence.LOW,
            explanation="None of the candidates match this activity's region.",
            reasoning="All candidates are region-specific to regions other than the org's default.",
        )
        self.assertIsNone(rec.recommended_factor)

    def test_reachable_from_record(self):
        rec = AIFactorRecommendation.objects.create(
            organization=self.org, record=self.record, interaction=self.interaction,
            recommended_factor=self.factor, confidence=AIFactorRecommendation.Confidence.MEDIUM,
            explanation="x", reasoning="x",
        )
        self.assertIn(rec, self.record.ai_factor_recommendations.all())

    def test_reachable_from_recommended_factor(self):
        rec = AIFactorRecommendation.objects.create(
            organization=self.org, record=self.record, interaction=self.interaction,
            recommended_factor=self.factor, confidence=AIFactorRecommendation.Confidence.MEDIUM,
            explanation="x", reasoning="x",
        )
        self.assertIn(rec, self.factor.ai_recommendations.all())


class AIFactorRecommendationImmutabilityTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Immutable Factor Rec Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL,
        )
        self.batch = _make_batch(self.org, self.ds)
        self.record = _make_record(self.org, self.batch)
        self.interaction = _make_interaction(self.org)
        self.recommendation = AIFactorRecommendation.objects.create(
            organization=self.org, record=self.record, interaction=self.interaction,
            confidence=AIFactorRecommendation.Confidence.MEDIUM, explanation="original", reasoning="x",
        )

    def test_instance_save_after_mutation_raises(self):
        self.recommendation.explanation = "edited"
        with self.assertRaises(ValidationError):
            self.recommendation.save()

    def test_instance_delete_raises(self):
        with self.assertRaises(ValidationError):
            self.recommendation.delete()
        self.assertTrue(AIFactorRecommendation.objects.filter(pk=self.recommendation.pk).exists())

    def test_bulk_update_raises(self):
        with self.assertRaises(ValidationError):
            AIFactorRecommendation.objects.filter(pk=self.recommendation.pk).update(explanation="bulk edited")

    def test_bulk_delete_raises(self):
        with self.assertRaises(ValidationError):
            AIFactorRecommendation.objects.filter(pk=self.recommendation.pk).delete()


class AIFactorRecommendationProtectedForeignKeyTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Protected FK Factor Rec Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL,
        )
        self.batch = _make_batch(self.org, self.ds)
        self.record = _make_record(self.org, self.batch)
        self.interaction = _make_interaction(self.org)
        self.activity_type = activity_type()
        self.dataset = dataset()
        self.factor = factor(self.dataset, self.activity_type)
        AIFactorRecommendation.objects.create(
            organization=self.org, record=self.record, interaction=self.interaction,
            recommended_factor=self.factor, confidence=AIFactorRecommendation.Confidence.HIGH,
            explanation="x", reasoning="x",
        )

    def test_organization_protected(self):
        with self.assertRaises(ProtectedError):
            self.org.delete()

    def test_interaction_protected(self):
        with self.assertRaises(ProtectedError):
            self.interaction.delete()

    def test_recommended_factor_protected(self):
        with self.assertRaises(ProtectedError):
            self.factor.delete()

    def test_no_field_on_this_model_uses_set_null(self):
        for f in AIFactorRecommendation._meta.fields:
            if f.is_relation:
                self.assertNotEqual(
                    f.remote_field.on_delete.__name__, "SET_NULL",
                    f"{f.name} must not be SET_NULL on an immutable, bulk-update-blocked model",
                )
