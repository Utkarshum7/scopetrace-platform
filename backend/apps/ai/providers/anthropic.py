"""
AnthropicProvider -- the default production LLMProvider (settings.AI_PROVIDER
defaults to 'anthropic' outside DEBUG/_TESTING). Sole file in this codebase
permitted to import the `anthropic` package (apps.ai.tests_import_guard
enforces this).
"""
import time

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

import anthropic

from .base import AICapability, LLMProvider, LLMProviderError, LLMRequest, LLMResponse


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self):
        # Fail fast on missing credentials at construction time, not on
        # first real call -- lets /healthz/ai detect misconfiguration with
        # zero network I/O, matching StorageService's S3-credential-check
        # precedent (fails at settings/construction time, not mid-request).
        if not settings.ANTHROPIC_API_KEY:
            raise ImproperlyConfigured(
                "ANTHROPIC_API_KEY is not configured but AI_PROVIDER=anthropic."
            )
        self._client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    def capabilities(self) -> frozenset[AICapability]:
        return frozenset({AICapability.STRUCTURED_OUTPUT})

    def complete(self, request: LLMRequest) -> LLMResponse:
        kwargs = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "messages": [{"role": "user", "content": request.prompt}],
        }
        if request.top_p is not None:
            kwargs["top_p"] = request.top_p
        if request.stop:
            kwargs["stop_sequences"] = request.stop

        started = time.monotonic()
        try:
            message = self._client.messages.create(**kwargs)
        except anthropic.APIError as exc:
            raise LLMProviderError(f"anthropic provider error: {exc}") from exc
        latency_ms = int((time.monotonic() - started) * 1000)

        text = "".join(
            block.text for block in message.content if getattr(block, "type", None) == "text"
        )

        return LLMResponse(
            text=text,
            model_id=message.model,
            model_snapshot=message.model,
            provider_request_id=message.id,
            input_tokens=message.usage.input_tokens if message.usage else None,
            output_tokens=message.usage.output_tokens if message.usage else None,
            latency_ms=latency_ms,
            finish_reason=message.stop_reason or "",
        )
