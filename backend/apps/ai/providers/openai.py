"""
OpenAIProvider -- exists primarily to prove the LLMProvider abstraction is a
real seam, not a single-vendor interface with extra ceremony (the same
reason StorageService ships both 'local' and 's3' rather than only ever
having one real backend). Sole file in this codebase permitted to import the
`openai` package (apps.ai.tests_import_guard enforces this).
"""
import time

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

import openai

from .base import AICapability, LLMProvider, LLMProviderError, LLMRequest, LLMResponse


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self):
        if not settings.OPENAI_API_KEY:
            raise ImproperlyConfigured(
                "OPENAI_API_KEY is not configured but AI_PROVIDER=openai."
            )
        client_kwargs = {"api_key": settings.OPENAI_API_KEY}
        # D5: bounded request timeout, Demo Mode only — see settings.
        # AI_PROVIDER_TIMEOUT_SECONDS's docstring. None in production, so this
        # falls through to the SDK's own default unchanged.
        if settings.AI_PROVIDER_TIMEOUT_SECONDS is not None:
            client_kwargs["timeout"] = settings.AI_PROVIDER_TIMEOUT_SECONDS
        self._client = openai.OpenAI(**client_kwargs)

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
            kwargs["stop"] = request.stop

        started = time.monotonic()
        try:
            response = self._client.chat.completions.create(**kwargs)
        except openai.APIError as exc:
            raise LLMProviderError(f"openai provider error: {exc}") from exc
        latency_ms = int((time.monotonic() - started) * 1000)

        choice = response.choices[0]

        return LLMResponse(
            text=choice.message.content or "",
            model_id=response.model,
            model_snapshot=response.model,
            provider_request_id=response.id,
            input_tokens=response.usage.prompt_tokens if response.usage else None,
            output_tokens=response.usage.completion_tokens if response.usage else None,
            latency_ms=latency_ms,
            finish_reason=choice.finish_reason or "",
        )
