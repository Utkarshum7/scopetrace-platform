"""Phase 7a — model-level tests for the AI foundation's three tables. No
provider/gateway logic here (that's tests_providers.py / tests_gateway.py);
this only covers each model's own invariants."""
from decimal import Decimal

from django.contrib.auth import get_user_model
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
        # Actual cascading User.delete() is exercised at the framework level
        # elsewhere; a pre-existing, unrelated EmissionRecord bulk-update
        # guard currently blocks User.delete() entirely (tracked
        # separately, not a Phase 7a concern). This asserts the FK's own
        # on_delete policy directly instead.
        field = AIInteraction._meta.get_field("actor")
        self.assertEqual(field.remote_field.on_delete.__name__, "SET_NULL")
        self.assertTrue(field.null)

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
