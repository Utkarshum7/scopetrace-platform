"""
LLMProvider selection (Phase 7a) -- mirrors apps.core.storage.factory
exactly. Adding a new provider (a self-hosted/BYO adapter, a second vendor)
is additive: drop a new class under providers/, add one branch here.

Each branch imports its concrete provider module lazily, inside the branch --
so importing this module (or apps.ai generally) never imports the anthropic
or openai SDKs unless that specific provider is actually selected. This is
also what apps.ai.tests_import_guard relies on: only providers/anthropic.py
and providers/openai.py are allowed to import those packages at all.
"""
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .base import LLMProvider


def get_llm_provider(*, provider_name: str | None = None) -> LLMProvider:
    """Construct the configured LLMProvider. Always builds fresh (no
    caching) so overridden settings in tests take effect without extra
    plumbing -- construction itself does no network I/O for any provider
    (only reads config/credentials), so this is cheap and safe to call from
    /healthz/ai on every request.

    Raises ImproperlyConfigured if the name is unset or unrecognized --
    callers (the gateway, the health check) decide what to do with that,
    this function never silently falls back to a different provider.
    """
    name = provider_name or settings.AI_PROVIDER

    if not name:
        raise ImproperlyConfigured(
            "AI_PROVIDER is not configured. Set AI_PROVIDER (or pass provider_name explicitly)."
        )

    if name == "echo":
        from .echo import EchoProvider

        return EchoProvider()

    if name == "replay":
        from .replay import ReplayProvider

        return ReplayProvider()

    if name == "anthropic":
        from .anthropic import AnthropicProvider

        return AnthropicProvider()

    if name == "openai":
        from .openai import OpenAIProvider

        return OpenAIProvider()

    raise ImproperlyConfigured(f"Unknown AI_PROVIDER: {name!r}")
