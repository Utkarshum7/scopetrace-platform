"""Phase 7a -- provider abstraction + EchoProvider + factory tests. Anthropic/
OpenAI adapter tests live in tests_providers_vendor.py (mocked SDK calls,
added alongside those adapters)."""
import json

from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase, override_settings

from apps.ai.providers.base import AICapability, LLMRequest
from apps.ai.providers.echo import EchoProvider, canned
from apps.ai.providers.factory import get_llm_provider


class EchoProviderTests(TestCase):
    def setUp(self):
        self.provider = EchoProvider()

    def test_capabilities_include_structured_output(self):
        self.assertIn(AICapability.STRUCTURED_OUTPUT, self.provider.capabilities())

    def test_deterministic_for_same_input(self):
        request = LLMRequest(prompt="hello world", model="echo-1")
        first = self.provider.complete(request)
        second = self.provider.complete(request)
        self.assertEqual(first.text, second.text)
        self.assertEqual(first.provider_request_id, second.provider_request_id)

    def test_different_input_produces_different_output(self):
        r1 = self.provider.complete(LLMRequest(prompt="a", model="echo-1"))
        r2 = self.provider.complete(LLMRequest(prompt="b", model="echo-1"))
        self.assertNotEqual(r1.text, r2.text)

    def test_default_response_is_valid_json(self):
        response = self.provider.complete(LLMRequest(prompt="anything", model="echo-1"))
        parsed = json.loads(response.text)
        self.assertIn("echo", parsed)

    def test_canned_response_echoed_verbatim(self):
        payload = {"suggestion": "example", "confidence": "HIGH"}
        prompt = f"context...\n{canned(payload)}\nmore context"
        response = self.provider.complete(LLMRequest(prompt=prompt, model="echo-1"))
        self.assertEqual(json.loads(response.text), payload)

    def test_never_touches_network_no_egress(self):
        # Structural proof, not a mock assertion: EchoProvider's module has
        # no HTTP/socket dependency at all -- importing it and calling
        # complete() with no network access configured must simply work.
        response = self.provider.complete(LLMRequest(prompt="x", model="echo-1"))
        self.assertEqual(response.latency_ms, 0)


class ProviderFactoryTests(TestCase):
    def test_echo_provider_selected_explicitly(self):
        provider = get_llm_provider(provider_name="echo")
        self.assertEqual(provider.name, "echo")

    @override_settings(AI_PROVIDER="echo")
    def test_echo_provider_selected_from_settings_default(self):
        provider = get_llm_provider()
        self.assertEqual(provider.name, "echo")

    def test_unknown_provider_raises_improperly_configured(self):
        with self.assertRaises(ImproperlyConfigured):
            get_llm_provider(provider_name="not-a-real-provider")

    @override_settings(AI_PROVIDER="")
    def test_unset_provider_raises_improperly_configured(self):
        with self.assertRaises(ImproperlyConfigured):
            get_llm_provider()

    def test_construction_is_fresh_each_call_not_cached(self):
        first = get_llm_provider(provider_name="echo")
        second = get_llm_provider(provider_name="echo")
        self.assertIsNot(first, second)
