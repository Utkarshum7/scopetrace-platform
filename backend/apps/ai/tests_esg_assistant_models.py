"""
Phase 7e -- AIConversation/AIConversationMessage model tests. New file,
separate from tests_models.py/tests_annotations.py/tests_factor_
recommendation_model.py, so these models' tests stay self-contained.
"""
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models import ProtectedError
from django.test import TestCase

from apps.ai.models import AIConversation, AIConversationMessage, AIInteraction, TenantAIPolicy
from apps.core.models import Organization

User = get_user_model()


def _make_interaction(org):
    return AIInteraction.objects.create(
        organization=org, capability="esg_assistant", provider="echo", model_id="echo-1",
        outcome=AIInteraction.Outcome.OK, egress_tier_applied=TenantAIPolicy.EgressTier.REDACTED,
    )


class AIConversationCreationTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Conversation Org")
        self.user = User.objects.create_user(username="analyst1", password="pw")

    def test_create_conversation(self):
        conversation = AIConversation.objects.create(organization=self.org, user=self.user)
        self.assertIsNotNone(conversation.id)
        self.assertEqual(conversation.organization, self.org)
        self.assertEqual(conversation.user, self.user)

    def test_user_is_nullable(self):
        conversation = AIConversation.objects.create(organization=self.org, user=None)
        self.assertIsNone(conversation.user)

    def test_user_field_is_declared_set_null(self):
        # NOT a behavioral self.user.delete() test: EmissionRecord's OWN
        # QuerySet.update() override (apps.ingestion.models) unconditionally
        # blocks any bulk update -- including the exact zero-row
        # `.update(approved_by=None)` call Django's deletion Collector
        # issues for EVERY User.delete(), whether or not that user has any
        # related EmissionRecord at all. That pre-existing, unrelated bug
        # (already the subject of a separate, in-progress fix bringing in
        # a carve-out queryset) means self.user.delete() cannot be
        # exercised end-to-end from this test file today without failing
        # for a reason that has nothing to do with AIConversation. This
        # checks the field's own on_delete declaration directly instead --
        # still a real, meaningful proof of intent, just not a full
        # cross-app behavioral one.
        field = AIConversation._meta.get_field("user")
        self.assertEqual(field.remote_field.on_delete.__name__, "SET_NULL")
        self.assertTrue(field.null)

    def test_reachable_from_organization(self):
        conversation = AIConversation.objects.create(organization=self.org, user=self.user)
        self.assertIn(conversation, self.org.ai_conversations.all())


class AIConversationMessageCreationTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Conversation Message Org")
        self.user = User.objects.create_user(username="analyst2", password="pw")
        self.conversation = AIConversation.objects.create(organization=self.org, user=self.user)
        self.interaction = _make_interaction(self.org)

    def test_create_user_message_with_no_interaction(self):
        message = AIConversationMessage.objects.create(
            organization=self.org, conversation=self.conversation,
            role=AIConversationMessage.Role.USER, content="What were our Scope 1 emissions last quarter?",
        )
        self.assertIsNone(message.interaction)
        self.assertEqual(message.role, "USER")

    def test_create_assistant_message_with_interaction(self):
        message = AIConversationMessage.objects.create(
            organization=self.org, conversation=self.conversation, interaction=self.interaction,
            role=AIConversationMessage.Role.ASSISTANT,
            content="Scope 1 emissions last quarter were 12.4 tonnes CO2e.",
            citations=["batch:abc123"], confidence=AIConversationMessage.Confidence.HIGH,
            retrieved_context="total_co2e_tonnes: 12.4, scope: SCOPE_1",
        )
        self.assertEqual(message.interaction, self.interaction)
        self.assertEqual(message.citations, ["batch:abc123"])
        self.assertFalse(message.unsupported_claim)

    def test_messages_reachable_from_conversation_ordered_by_created_at(self):
        first = AIConversationMessage.objects.create(
            organization=self.org, conversation=self.conversation,
            role=AIConversationMessage.Role.USER, content="first",
        )
        second = AIConversationMessage.objects.create(
            organization=self.org, conversation=self.conversation, interaction=self.interaction,
            role=AIConversationMessage.Role.ASSISTANT, content="second",
        )
        messages = list(self.conversation.messages.all())
        self.assertEqual(messages, [first, second])


class AIConversationMessageImmutabilityTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Immutable Conversation Message Org")
        self.conversation = AIConversation.objects.create(organization=self.org)
        self.message = AIConversationMessage.objects.create(
            organization=self.org, conversation=self.conversation,
            role=AIConversationMessage.Role.USER, content="original question",
        )

    def test_instance_save_after_mutation_raises(self):
        self.message.content = "edited question"
        with self.assertRaises(ValidationError):
            self.message.save()

    def test_instance_delete_raises(self):
        with self.assertRaises(ValidationError):
            self.message.delete()
        self.assertTrue(AIConversationMessage.objects.filter(pk=self.message.pk).exists())

    def test_bulk_update_raises(self):
        with self.assertRaises(ValidationError):
            AIConversationMessage.objects.filter(pk=self.message.pk).update(content="bulk edited")

    def test_bulk_delete_raises(self):
        with self.assertRaises(ValidationError):
            AIConversationMessage.objects.filter(pk=self.message.pk).delete()


class AIConversationMessageProtectedForeignKeyTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Protected FK Conversation Message Org")
        self.conversation = AIConversation.objects.create(organization=self.org)
        self.interaction = _make_interaction(self.org)
        AIConversationMessage.objects.create(
            organization=self.org, conversation=self.conversation, interaction=self.interaction,
            role=AIConversationMessage.Role.ASSISTANT, content="x",
        )

    def test_organization_protected(self):
        with self.assertRaises(ProtectedError):
            self.org.delete()

    def test_conversation_protected(self):
        with self.assertRaises(ProtectedError):
            self.conversation.delete()

    def test_interaction_protected(self):
        with self.assertRaises(ProtectedError):
            self.interaction.delete()

    def test_no_field_on_this_model_uses_set_null(self):
        for f in AIConversationMessage._meta.fields:
            if f.is_relation:
                self.assertNotEqual(
                    f.remote_field.on_delete.__name__, "SET_NULL",
                    f"{f.name} must not be SET_NULL on an immutable, bulk-update-blocked model",
                )
