"""Tests for the Demo Mode provider (apps.ai.providers.demo) and its
end-to-end behavior through the gateway for the ESG Assistant."""
import json

from django.test import TestCase

from apps.ai.providers.demo import DemoProvider
from apps.ai.providers.base import LLMRequest
from apps.ai.schemas import (
    ANOMALY_DETECTION_V2,
    ESG_ASSISTANT_V2,
    VALIDATION_ASSISTANCE_V2,
    validate_response,
)


def _complete(prompt: str) -> dict:
    resp = DemoProvider().complete(LLMRequest(prompt=prompt, model="demo-1"))
    return json.loads(resp.text)


class DemoProviderSchemaTests(TestCase):
    def test_esg_assistant_known_topic_is_schema_valid_and_answers(self):
        prompt = (
            "Question: What is Scope 1?\n"
            'Respond with ONLY a JSON object matching exactly:\n'
            '{"answer": "<string>", "citations": [...], "confidence": ...,'
            ' "unsupported_claim": <bool>}'
        )
        parsed, ok = validate_response(DemoProvider().complete(
            LLMRequest(prompt=prompt, model="demo-1")).text, ESG_ASSISTANT_V2)
        self.assertTrue(ok)
        self.assertIn("scope 1", parsed["answer"].lower())
        self.assertFalse(parsed["unsupported_claim"])
        self.assertEqual(parsed["confidence"], "HIGH")

    def test_esg_assistant_carbon_footprint_answers(self):
        prompt = 'Question: what is a carbon footprint? "unsupported_claim"'
        parsed = _complete(prompt)
        self.assertIn("co2", parsed["answer"].lower())

    def test_esg_assistant_unknown_topic_flags_unsupported_but_stays_valid(self):
        prompt = 'Question: who won the 1998 world cup? "unsupported_claim"'
        text = DemoProvider().complete(LLMRequest(prompt=prompt, model="demo-1")).text
        parsed, ok = validate_response(text, ESG_ASSISTANT_V2)
        self.assertTrue(ok)
        self.assertTrue(parsed["unsupported_claim"])
        self.assertEqual(parsed["confidence"], "LOW")

    def test_anomaly_detection_shape_is_valid(self):
        prompt = 'return {"explanation":..., "contributing_factors":[...], ...}'
        _, ok = validate_response(
            DemoProvider().complete(LLMRequest(prompt=prompt, model="demo-1")).text,
            ANOMALY_DETECTION_V2,
        )
        self.assertTrue(ok)

    def test_validation_assistance_shape_is_valid(self):
        prompt = 'return {"explanation":..., "affected_fields":[...], ...}'
        _, ok = validate_response(
            DemoProvider().complete(LLMRequest(prompt=prompt, model="demo-1")).text,
            VALIDATION_ASSISTANCE_V2,
        )
        self.assertTrue(ok)


class DemoAssistantEndToEndTests(TestCase):
    """Full gateway path: a demo-policy org gets a real, schema-valid answer."""

    def setUp(self):
        from apps.core.models import Organization
        from apps.ai.models import TenantAIPolicy
        from apps.ai.models import AIConversation

        self.org = Organization.objects.create(name="Demo E2E Org")
        TenantAIPolicy.objects.create(
            organization=self.org,
            ai_enabled=True,
            provider_override="demo",
            egress_tier=TenantAIPolicy.EgressTier.NO_EGRESS,
        )
        self.conversation = AIConversation.objects.create(organization=self.org)

    def test_ask_returns_answer_message(self):
        from apps.ai.services.esg_assistant import ask_esg_assistant
        from apps.ai.models import AIConversationMessage

        with self.settings(AI_ENABLED=True, AI_PROVIDER="demo"):
            message = ask_esg_assistant(self.conversation, "What is Scope 2?")

        self.assertIsNotNone(message)
        self.assertEqual(message.role, AIConversationMessage.Role.ASSISTANT)
        self.assertIn("scope 2", message.content.lower())
