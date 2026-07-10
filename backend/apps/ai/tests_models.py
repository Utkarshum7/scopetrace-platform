"""Phase 7a — model-level tests for the AI foundation's three tables. No
provider/gateway logic here (that's tests_providers.py / tests_gateway.py);
this only covers each model's own invariants."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.ai.models import AIInteraction, AIPromptVersion, TenantAIPolicy
from apps.core.models import Organization

User = get_user_model()


class AIPromptVersionRegisterTests(TestCase):
    def test_first_registration_creates_version_1(self):
        row, created = AIPromptVersion.register(
            name="foundation.selftest", template_text="Hello $name",
            template_hash="a" * 64, response_schema_id="selftest", response_schema_version=1,
        )
        self.assertTrue(created)
        self.assertEqual(row.version, 1)

    def test_same_hash_returns_existing_row_not_a_duplicate(self):
        first, _ = AIPromptVersion.register(
            name="foundation.selftest", template_text="Hello $name",
            template_hash="a" * 64, response_schema_id="selftest", response_schema_version=1,
        )
        second, created = AIPromptVersion.register(
            name="foundation.selftest", template_text="Hello $name",
            template_hash="a" * 64, response_schema_id="selftest", response_schema_version=1,
        )
        self.assertFalse(created)
        self.assertEqual(first.id, second.id)
        self.assertEqual(AIPromptVersion.objects.filter(name="foundation.selftest").count(), 1)

    def test_different_hash_same_name_increments_version(self):
        AIPromptVersion.register(
            name="foundation.selftest", template_text="Hello $name",
            template_hash="a" * 64, response_schema_id="selftest", response_schema_version=1,
        )
        second, created = AIPromptVersion.register(
            name="foundation.selftest", template_text="Hi $name",
            template_hash="b" * 64, response_schema_id="selftest", response_schema_version=1,
        )
        self.assertTrue(created)
        self.assertEqual(second.version, 2)

    def test_different_name_starts_its_own_sequence_at_1(self):
        AIPromptVersion.register(
            name="foundation.selftest", template_text="x",
            template_hash="a" * 64, response_schema_id="s", response_schema_version=1,
        )
        other, created = AIPromptVersion.register(
            name="other.prompt", template_text="y",
            template_hash="c" * 64, response_schema_id="s", response_schema_version=1,
        )
        self.assertTrue(created)
        self.assertEqual(other.version, 1)


class TenantAIPolicyTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="AI Policy Org")

    def test_defaults_to_disabled_and_redacted_egress(self):
        policy = TenantAIPolicy.objects.create(organization=self.org)
        self.assertFalse(policy.ai_enabled)
        self.assertEqual(policy.egress_tier, TenantAIPolicy.EgressTier.REDACTED)

    def test_one_policy_row_per_organization(self):
        TenantAIPolicy.objects.create(organization=self.org)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                TenantAIPolicy.objects.create(organization=self.org)


class AIInteractionTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="AI Interaction Org")
        self.user = User.objects.create_user("ai_actor", password="pw")

    def test_create_minimal_interaction(self):
        interaction = AIInteraction.objects.create(
            organization=self.org,
            actor=self.user,
            capability="foundation.selftest",
            provider="echo",
            model_id="echo-1",
            outcome=AIInteraction.Outcome.OK,
            egress_tier_applied=TenantAIPolicy.EgressTier.REDACTED,
            cost_usd=Decimal("0.000000"),
        )
        self.assertIsNotNone(interaction.id)
        self.assertEqual(interaction.outcome, "OK")

    def test_actor_field_is_set_null_on_delete(self):
        field = AIInteraction._meta.get_field("actor")
        self.assertEqual(field.remote_field.on_delete.__name__, "SET_NULL")
        self.assertTrue(field.null)

    def test_actor_becomes_null_when_user_is_deleted(self):
        # Previously, deleting ANY user raised ValidationError -- a
        # pre-existing, unrelated EmissionRecordQuerySet.update() guard
        # (apps.ingestion.models) blocked Django's own SET_NULL cascade
        # unconditionally, not just when it would have affected a row. See
        # apps.core.querysets.SetNullCascadeSafeQuerySet and
        # apps.ingestion.tests_user_deletion for the fix and full regression
        # coverage; this test just confirms the cascade actually reaches
        # AIInteraction.actor now that it's unblocked.
        interaction = AIInteraction.objects.create(
            organization=self.org, actor=self.user, capability="x", provider="echo",
            model_id="echo-1", outcome=AIInteraction.Outcome.OK,
            egress_tier_applied=TenantAIPolicy.EgressTier.REDACTED,
        )
        self.user.delete()
        interaction.refresh_from_db()
        self.assertIsNone(interaction.actor_id)

    def test_organization_protected_from_deletion_once_it_has_ai_history(self):
        AIInteraction.objects.create(
            organization=self.org, capability="x", provider="echo", model_id="echo-1",
            outcome=AIInteraction.Outcome.OK, egress_tier_applied=TenantAIPolicy.EgressTier.REDACTED,
        )
        from django.db.models import ProtectedError
        with self.assertRaises(ProtectedError):
            self.org.delete()

    def test_system_initiated_call_has_no_actor(self):
        interaction = AIInteraction.objects.create(
            organization=self.org, actor=None, capability="x", provider="echo",
            model_id="echo-1", outcome=AIInteraction.Outcome.AI_DISABLED,
            egress_tier_applied=TenantAIPolicy.EgressTier.REDACTED,
        )
        self.assertIsNone(interaction.actor)


class AIInteractionImmutabilityTests(TestCase):
    """Phase 7.5 (H4-2): AIInteraction is apps.ai's single audit/
    reproducibility record, but was the one AI model with no QuerySet-level
    guard against bulk update()/delete() -- unlike AIAnnotation,
    AIFactorRecommendation, AIConversationMessage, AIReportNarration, all of
    which block it. These tests pin the guard AND confirm it doesn't regress
    the SET_NULL cascade covered by test_actor_becomes_null_when_user_is_
    deleted above."""

    def setUp(self):
        self.org = Organization.objects.create(name="AI Immutability Org")
        self.user = User.objects.create_user("ai_immutable_actor", password="pw")

    def _interaction(self, **extra):
        defaults = dict(
            organization=self.org, actor=self.user, capability="foundation.selftest",
            provider="echo", model_id="echo-1", outcome=AIInteraction.Outcome.OK,
            egress_tier_applied=TenantAIPolicy.EgressTier.REDACTED,
        )
        defaults.update(extra)
        return AIInteraction.objects.create(**defaults)

    def test_bulk_update_of_a_business_field_is_blocked(self):
        interaction = self._interaction()
        with self.assertRaises(ValidationError):
            AIInteraction.objects.filter(pk=interaction.pk).update(outcome=AIInteraction.Outcome.ERROR)
        interaction.refresh_from_db()
        self.assertEqual(interaction.outcome, AIInteraction.Outcome.OK)

    def test_bulk_delete_is_blocked(self):
        self._interaction()
        with self.assertRaises(ValidationError):
            AIInteraction.objects.all().delete()
        self.assertEqual(AIInteraction.objects.count(), 1)

    def test_single_instance_delete_is_not_covered_by_this_guard(self):
        # Documents the guard's real, deliberate scope -- matching every
        # sibling model's own docstring ("blocks bulk delete/update at the
        # QuerySet level -- the gap instance-level delete()/clean()
        # overrides don't cover"): Model.delete() for a single row with no
        # related-object cascade does NOT route through the manager's
        # QuerySet.delete() override anywhere in this codebase, so this
        # guard -- like AIAnnotation's, AuditTrail's, and every other
        # sibling -- only refuses the BULK form (.filter(...).delete() /
        # .all().delete()), never a single instance.delete() call.
        interaction = self._interaction()
        interaction.delete()  # does not raise -- not this guard's scope
        self.assertEqual(AIInteraction.objects.count(), 0)

    def test_the_set_null_cascade_still_succeeds_through_the_guard(self):
        # THE regression this guard must never reintroduce (ADR 0009's bug
        # class): deleting a user must still succeed and null out actor_id,
        # not raise. Mirrors test_actor_becomes_null_when_user_is_deleted,
        # phrased explicitly against the new guard for this milestone.
        interaction = self._interaction()
        self.user.delete()  # must not raise
        interaction.refresh_from_db()
        self.assertIsNone(interaction.actor_id)

    def test_bulk_setting_actor_to_a_real_user_is_still_blocked(self):
        # The carve-out only allows the SET_NULL cascade's exact shape
        # (actor=None) -- bulk-assigning actor to a REAL user must still be
        # refused, matching apps.ingestion.tests_user_deletion's equivalent
        # coverage for EmissionRecord.approved_by.
        interaction = self._interaction(actor=None)
        other_user = User.objects.create_user("other_actor", password="pw")
        with self.assertRaises(ValidationError):
            AIInteraction.objects.filter(pk=interaction.pk).update(actor=other_user)
        interaction.refresh_from_db()
        self.assertIsNone(interaction.actor_id)
