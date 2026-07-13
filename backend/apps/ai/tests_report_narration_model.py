"""
Phase 7f -- AIReportNarration model tests. New file, separate from the
other capability model test files, so this model's tests stay
self-contained.
"""
from datetime import date

from django.core.exceptions import ValidationError
from django.db.models import ProtectedError
from django.test import TestCase

from apps.ai.models import AIInteraction, AIReportNarration, TenantAIPolicy
from apps.core.models import Organization


def _make_interaction(org):
    return AIInteraction.objects.create(
        organization=org, capability="report_narration", provider="echo", model_id="echo-1",
        outcome=AIInteraction.Outcome.OK, egress_tier_applied=TenantAIPolicy.EgressTier.REDACTED,
    )


class AIReportNarrationCreationTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Report Narration Org")
        self.interaction = _make_interaction(self.org)

    def test_create_narration(self):
        narration = AIReportNarration.objects.create(
            organization=self.org, interaction=self.interaction,
            date_from=date(2026, 1, 1), date_to=date(2026, 3, 31), scope="SCOPE_1",
            executive_summary="Emissions fell 8% quarter over quarter.",
            key_highlights=["Scope 1 down 8%", "Coverage at 97%"],
            trend_explanations="The decline is driven by reduced fuel consumption in Q1.",
            recommendations=["Investigate the Q1 fuel efficiency gains for replicability."],
            confidence=AIReportNarration.Confidence.HIGH,
        )
        self.assertIsNotNone(narration.id)
        self.assertEqual(narration.scope, "SCOPE_1")
        self.assertEqual(narration.key_highlights, ["Scope 1 down 8%", "Coverage at 97%"])

    def test_scope_blank_means_all_scopes(self):
        narration = AIReportNarration.objects.create(
            organization=self.org, interaction=self.interaction,
            date_from=date(2026, 1, 1), date_to=date(2026, 3, 31), scope="",
            executive_summary="x", trend_explanations="x", confidence=AIReportNarration.Confidence.MEDIUM,
        )
        self.assertEqual(narration.scope, "")

    def test_reachable_from_organization(self):
        narration = AIReportNarration.objects.create(
            organization=self.org, interaction=self.interaction,
            date_from=date(2026, 1, 1), date_to=date(2026, 3, 31), scope="",
            executive_summary="x", trend_explanations="x", confidence=AIReportNarration.Confidence.LOW,
        )
        self.assertIn(narration, self.org.ai_report_narrations.all())


class AIReportNarrationImmutabilityTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Immutable Report Narration Org")
        self.interaction = _make_interaction(self.org)
        self.narration = AIReportNarration.objects.create(
            organization=self.org, interaction=self.interaction,
            date_from=date(2026, 1, 1), date_to=date(2026, 3, 31), scope="",
            executive_summary="original", trend_explanations="x", confidence=AIReportNarration.Confidence.LOW,
        )

    def test_instance_save_after_mutation_raises(self):
        self.narration.executive_summary = "edited"
        with self.assertRaises(ValidationError):
            self.narration.save()

    def test_instance_delete_raises(self):
        with self.assertRaises(ValidationError):
            self.narration.delete()
        self.assertTrue(AIReportNarration.objects.filter(pk=self.narration.pk).exists())

    def test_bulk_update_raises(self):
        with self.assertRaises(ValidationError):
            AIReportNarration.objects.filter(pk=self.narration.pk).update(executive_summary="bulk edited")

    def test_bulk_delete_raises(self):
        with self.assertRaises(ValidationError):
            AIReportNarration.objects.filter(pk=self.narration.pk).delete()


class AIReportNarrationProtectedForeignKeyTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Protected FK Report Narration Org")
        self.interaction = _make_interaction(self.org)
        AIReportNarration.objects.create(
            organization=self.org, interaction=self.interaction,
            date_from=date(2026, 1, 1), date_to=date(2026, 3, 31), scope="",
            executive_summary="x", trend_explanations="x", confidence=AIReportNarration.Confidence.LOW,
        )

    def test_organization_protected(self):
        with self.assertRaises(ProtectedError):
            self.org.delete()

    def test_interaction_protected(self):
        with self.assertRaises(ProtectedError):
            self.interaction.delete()

    def test_no_field_on_this_model_uses_set_null(self):
        for f in AIReportNarration._meta.fields:
            if f.is_relation:
                self.assertNotEqual(
                    f.remote_field.on_delete.__name__, "SET_NULL",
                    f"{f.name} must not be SET_NULL on an immutable, bulk-update-blocked model",
                )
