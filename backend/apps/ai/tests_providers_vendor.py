"""Phase 7a -- Anthropic/OpenAI adapter tests. Mocked SDK client calls only
-- no real network access, no real credentials, no cost. Real end-to-end
provider connectivity is deliberately never exercised by the automated test
suite (see docs/AI_ARCHITECTURE.md's eval-harness section, Phase 7a.5)."""
from unittest.mock import MagicMock, patch

from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase, override_settings

from apps.ai.providers.base import LLMProviderError, LLMRequest


@override_settings(ANTHROPIC_API_KEY="")
class AnthropicProviderConfigTests(TestCase):
    def test_missing_api_key_raises_improperly_configured(self):
        from apps.ai.providers.anthropic import AnthropicProvider

        with self.assertRaises(ImproperlyConfigured):
            AnthropicProvider()


@override_settings(ANTHROPIC_API_KEY="sk-ant-test-key")
class AnthropicProviderCompleteTests(TestCase):
    def _make_message(self, text="hello", finish_reason="end_turn"):
        block = MagicMock()
        block.type = "text"
        block.text = text
        message = MagicMock()
        message.content = [block]
        message.model = "claude-sonnet-5"
        message.id = "msg_123"
        message.usage.input_tokens = 10
        message.usage.output_tokens = 5
        message.stop_reason = finish_reason
        return message

    def test_complete_returns_llm_response(self):
        from apps.ai.providers.anthropic import AnthropicProvider

        provider = AnthropicProvider()
        with patch.object(provider._client.messages, "create", return_value=self._make_message()) as mocked:
            response = provider.complete(LLMRequest(prompt="hi", model="claude-sonnet-5"))

        self.assertEqual(response.text, "hello")
        self.assertEqual(response.model_id, "claude-sonnet-5")
        self.assertEqual(response.provider_request_id, "msg_123")
        self.assertEqual(response.input_tokens, 10)
        self.assertEqual(response.output_tokens, 5)
        mocked.assert_called_once()
        call_kwargs = mocked.call_args.kwargs
        self.assertEqual(call_kwargs["messages"], [{"role": "user", "content": "hi"}])

    def test_provider_error_wrapped_as_llm_provider_error(self):
        import anthropic as anthropic_sdk

        from apps.ai.providers.anthropic import AnthropicProvider

        provider = AnthropicProvider()
        api_error = anthropic_sdk.APIConnectionError(request=MagicMock())
        with patch.object(provider._client.messages, "create", side_effect=api_error):
            with self.assertRaises(LLMProviderError):
                provider.complete(LLMRequest(prompt="hi", model="claude-sonnet-5"))


@override_settings(OPENAI_API_KEY="")
class OpenAIProviderConfigTests(TestCase):
    def test_missing_api_key_raises_improperly_configured(self):
        from apps.ai.providers.openai import OpenAIProvider

        with self.assertRaises(ImproperlyConfigured):
            OpenAIProvider()


@override_settings(OPENAI_API_KEY="sk-openai-test-key")
class OpenAIProviderCompleteTests(TestCase):
    def _make_response(self, text="hello", finish_reason="stop"):
        choice = MagicMock()
        choice.message.content = text
        choice.finish_reason = finish_reason
        response = MagicMock()
        response.choices = [choice]
        response.model = "gpt-4o"
        response.id = "chatcmpl_123"
        response.usage.prompt_tokens = 8
        response.usage.completion_tokens = 4
        return response

    def test_complete_returns_llm_response(self):
        from apps.ai.providers.openai import OpenAIProvider

        provider = OpenAIProvider()
        with patch.object(provider._client.chat.completions, "create", return_value=self._make_response()) as mocked:
            response = provider.complete(LLMRequest(prompt="hi", model="gpt-4o"))

        self.assertEqual(response.text, "hello")
        self.assertEqual(response.model_id, "gpt-4o")
        self.assertEqual(response.provider_request_id, "chatcmpl_123")
        self.assertEqual(response.input_tokens, 8)
        self.assertEqual(response.output_tokens, 4)
        mocked.assert_called_once()

    def test_provider_error_wrapped_as_llm_provider_error(self):
        import openai as openai_sdk

        from apps.ai.providers.openai import OpenAIProvider

        provider = OpenAIProvider()
        api_error = openai_sdk.APIConnectionError(request=MagicMock())
        with patch.object(provider._client.chat.completions, "create", side_effect=api_error):
            with self.assertRaises(LLMProviderError):
                provider.complete(LLMRequest(prompt="hi", model="gpt-4o"))


class ProviderFactoryVendorTests(TestCase):
    @override_settings(ANTHROPIC_API_KEY="sk-ant-test-key")
    def test_factory_selects_anthropic(self):
        from apps.ai.providers.factory import get_llm_provider

        provider = get_llm_provider(provider_name="anthropic")
        self.assertEqual(provider.name, "anthropic")

    @override_settings(OPENAI_API_KEY="sk-openai-test-key")
    def test_factory_selects_openai(self):
        from apps.ai.providers.factory import get_llm_provider

        provider = get_llm_provider(provider_name="openai")
        self.assertEqual(provider.name, "openai")
