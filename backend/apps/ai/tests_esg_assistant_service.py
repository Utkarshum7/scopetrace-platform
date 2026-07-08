"""
Phase 7e -- apps.ai.services.esg_assistant tests.

Like factor_recommendation's tests, this mocks invoke_ai directly for
exact-response cases: the context this prompt consumes is built live from
real DB queries (apps.ai.services.esg_context_builder), not a single
bounded field a canned() marker could hide inside. Refusal-path tests
still exercise the real EchoProvider end to end, since those outcomes
don't depend on any specific response content.
"""
from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.ai.models import AIConversation, AIConversationMessage, AIInteraction, TenantAIPolicy
from apps.ai.services.esg_assistant import ask_esg_assistant
from apps.ai.services.gateway import AIGatewayResult
from apps.core.models import Organization


def _make_interaction(org):
    return AIInteraction.objects.create(
        organization=org, capability="esg_assistant", provider="echo", model_id="echo-1",
        outcome=AIInteraction.Outcome.OK, egress_tier_applied=TenantAIPolicy.EgressTier.REDACTED,
    )


@override_settings(AI_ENABLED=True, AI_PROVIDER="echo", AI_DEFAULT_MODEL="echo-1")
class AskEsgAssistantHappyPathTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="ESG Assistant Service Org")
        TenantAIPolicy.objects.create(organization=self.org, ai_enabled=True, provider_override="echo")
        self.conversation = AIConversation.objects.create(organization=self.org)

    def _invoke_ai_returning(self, parsed):
        interaction = _make_interaction(self.org)
        return AIGatewayResult(outcome=AIInteraction.Outcome.OK, interaction_id=str(interaction.id), parsed=parsed)

    def test_persists_the_user_question_regardless_of_outcome(self):
        with patch(
            "apps.ai.services.esg_assistant.invoke_ai",
            return_value=self._invoke_ai_returning({
                "answer": "x", "citations": [], "confidence": "LOW", "unsupported_claim": False,
            }),
        ):
            ask_esg_assistant(self.conversation, "What is our total CO2e?")
        user_messages = self.conversation.messages.filter(role=AIConversationMessage.Role.USER)
        self.assertEqual(user_messages.count(), 1)
        self.assertEqual(user_messages.first().content, "What is our total CO2e?")

    def test_creates_an_assistant_message_on_success(self):
        parsed = {
            "answer": "Total CO2e was 842.15 tonnes.",
            "citations": ["org_summary"],
            "confidence": "HIGH",
            "unsupported_claim": False,
        }
        with patch("apps.ai.services.esg_assistant.invoke_ai", return_value=self._invoke_ai_returning(parsed)):
            message = ask_esg_assistant(self.conversation, "What is our total CO2e?")
        self.assertIsNotNone(message)
        self.assertEqual(message.role, "ASSISTANT")
        self.assertEqual(message.content, parsed["answer"])
        self.assertEqual(message.citations, ["org_summary"])
        self.assertEqual(message.confidence, "HIGH")
        self.assertFalse(message.unsupported_claim)

    def test_persists_the_retrieved_context_on_the_assistant_message(self):
        parsed = {"answer": "x", "citations": [], "confidence": "LOW", "unsupported_claim": False}
        with patch("apps.ai.services.esg_assistant.invoke_ai", return_value=self._invoke_ai_returning(parsed)):
            message = ask_esg_assistant(self.conversation, "question")
        self.assertIn("org_summary:", message.retrieved_context)

    def test_unsupported_claim_flag_is_persisted(self):
        parsed = {
            "answer": "I don't have that figure in the retrieved context.",
            "citations": ["org_summary"], "confidence": "LOW", "unsupported_claim": True,
        }
        with patch("apps.ai.services.esg_assistant.invoke_ai", return_value=self._invoke_ai_returning(parsed)):
            message = ask_esg_assistant(self.conversation, "What is our Scope 3 total?")
        self.assertTrue(message.unsupported_claim)

    def test_links_back_to_the_ai_interaction(self):
        parsed = {"answer": "x", "citations": [], "confidence": "LOW", "unsupported_claim": False}
        with patch("apps.ai.services.esg_assistant.invoke_ai", return_value=self._invoke_ai_returning(parsed)):
            message = ask_esg_assistant(self.conversation, "question")
        self.assertIsNotNone(message.interaction)
        self.assertEqual(message.interaction.capability, "esg_assistant")

    def test_never_writes_to_governed_models(self):
        from apps.carbon.models import EmissionCalculation, EmissionFactor
        from apps.ingestion.models import EmissionRecord

        before_records = EmissionRecord.objects.count()
        before_calcs = EmissionCalculation.objects.count()
        before_factors = EmissionFactor.objects.count()

        parsed = {"answer": "x", "citations": [], "confidence": "LOW", "unsupported_claim": False}
        with patch("apps.ai.services.esg_assistant.invoke_ai", return_value=self._invoke_ai_returning(parsed)):
            ask_esg_assistant(self.conversation, "question")

        self.assertEqual(EmissionRecord.objects.count(), before_records)
        self.assertEqual(EmissionCalculation.objects.count(), before_calcs)
        self.assertEqual(EmissionFactor.objects.count(), before_factors)


@override_settings(AI_ENABLED=True, AI_PROVIDER="echo", AI_DEFAULT_MODEL="echo-1")
class AskEsgAssistantRefusalTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="ESG Assistant Refusal Org")
        self.conversation = AIConversation.objects.create(organization=self.org)

    def test_ai_disabled_returns_none_but_still_records_the_question(self):
        # No TenantAIPolicy row -- AI disabled for this org.
        message = ask_esg_assistant(self.conversation, "What is our total CO2e?")
        self.assertIsNone(message)
        self.assertEqual(
            self.conversation.messages.filter(role=AIConversationMessage.Role.USER).count(), 1,
        )
        self.assertEqual(
            self.conversation.messages.filter(role=AIConversationMessage.Role.ASSISTANT).count(), 0,
        )

    def test_schema_invalid_response_returns_none_and_creates_no_assistant_message(self):
        TenantAIPolicy.objects.create(organization=self.org, ai_enabled=True, provider_override="echo")
        # EchoProvider's default (non-canned) response never matches the
        # esg_assistant schema.
        message = ask_esg_assistant(self.conversation, "What is our total CO2e?")
        self.assertIsNone(message)
        self.assertTrue(
            AIInteraction.objects.filter(capability="esg_assistant", outcome="SCHEMA_INVALID").exists()
        )
