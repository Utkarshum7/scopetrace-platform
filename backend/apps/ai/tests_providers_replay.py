"""Phase 7a.5 -- ReplayProvider tests. Co-located with the other provider
test files (tests_providers.py covers Echo) rather than under
apps/ai/evaluation/, since ReplayProvider is itself a provider, selectable
via the same factory as echo/anthropic/openai."""
from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase, override_settings

from apps.ai.providers.base import AICapability, LLMProviderError, LLMRequest
from apps.ai.providers.factory import get_llm_provider
from apps.ai.providers.replay import ReplayProvider
from apps.ai.services.egress import ZERO_EGRESS_PROVIDERS, enforce_provider_allowed


class ReplayProviderCannedResponseModeTests(TestCase):
    def setUp(self):
        self.provider = ReplayProvider()

    def test_capabilities_include_structured_output(self):
        self.assertIn(AICapability.STRUCTURED_OUTPUT, self.provider.capabilities())

    def test_returns_canned_response_verbatim(self):
        payload = {"is_anomalous": True, "explanation": "spike", "confidence": "HIGH"}
        response = self.provider.complete(
            LLMRequest(prompt="anything", model="replay-1", extra={"canned_response": payload})
        )
        import json

        self.assertEqual(json.loads(response.text), payload)

    def test_ignores_prompt_content_entirely(self):
        payload = {"echo": "fixed"}
        r1 = self.provider.complete(
            LLMRequest(prompt="prompt A", model="replay-1", extra={"canned_response": payload})
        )
        r2 = self.provider.complete(
            LLMRequest(prompt="totally different prompt B", model="replay-1", extra={"canned_response": payload})
        )
        self.assertEqual(r1.text, r2.text)

    def test_zero_cost_tokens_and_latency(self):
        response = self.provider.complete(
            LLMRequest(prompt="x", model="replay-1", extra={"canned_response": {"a": 1}})
        )
        self.assertEqual(response.input_tokens, 0)
        self.assertEqual(response.output_tokens, 0)
        self.assertEqual(response.latency_ms, 0)


class ReplayProviderCaseIdModeTests(TestCase):
    def setUp(self):
        self.provider = ReplayProvider()

    def test_loads_fixture_by_case_id(self):
        response = self.provider.complete(
            LLMRequest(prompt="x", model="replay-1", extra={"case_id": "example-case-001"})
        )
        import json

        self.assertEqual(json.loads(response.text), {"acknowledged": True, "echo": "example-case-001"})

    def test_missing_case_id_file_raises_llm_provider_error(self):
        with self.assertRaises(LLMProviderError):
            self.provider.complete(
                LLMRequest(prompt="x", model="replay-1", extra={"case_id": "no-such-case"})
            )


class ReplayProviderMisuseTests(TestCase):
    def test_no_canned_response_or_case_id_raises(self):
        provider = ReplayProvider()
        with self.assertRaises(LLMProviderError):
            provider.complete(LLMRequest(prompt="x", model="replay-1"))


class ReplayProviderFactoryTests(TestCase):
    def test_factory_selects_replay(self):
        provider = get_llm_provider(provider_name="replay")
        self.assertEqual(provider.name, "replay")

    @override_settings(AI_PROVIDER="replay")
    def test_factory_selects_replay_from_settings_default(self):
        provider = get_llm_provider()
        self.assertEqual(provider.name, "replay")

    def test_construction_never_raises_improperly_configured(self):
        # Unlike anthropic/openai, replay needs no credentials at all.
        try:
            get_llm_provider(provider_name="replay")
        except ImproperlyConfigured:
            self.fail("ReplayProvider should never require configuration to construct.")


class ReplayProviderEgressTests(TestCase):
    def test_replay_is_a_zero_egress_provider(self):
        self.assertIn("replay", ZERO_EGRESS_PROVIDERS)

    def test_no_egress_tier_permits_replay(self):
        enforce_provider_allowed("replay", "NO_EGRESS")  # must not raise
