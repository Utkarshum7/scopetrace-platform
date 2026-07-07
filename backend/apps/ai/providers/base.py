"""
LLMProvider -- the provider-independent AI completion contract.

Modeled directly on apps.core.storage.base.StorageService: application code
(the gateway, and eventually feature milestones) depends only on this
interface, never on a concrete provider. Swapping Anthropic <-> OpenAI <-> a
self-hosted/BYO model is a settings change plus, at most, one new thin
adapter class under apps/ai/providers/ -- never a change to caller code.

Deliberately a lowest-common-denominator interface: complete() takes a fully
rendered prompt string and returns raw text. Providers do NOT parse or
validate structured output themselves -- see apps.ai.services.gateway's
docstring for why schema enforcement lives at the gateway/envelope level,
not per-provider (every Phase 7 capability requires STRUCTURED_OUTPUT; a
provider that can't honor that is only eligible for capabilities that don't
need schema-enforced output, of which Phase 7 has none).
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum


class AICapability(str, Enum):
    """What a provider can do. STRUCTURED_OUTPUT means "the gateway can
    reliably ask this provider to return parseable JSON and get it back" --
    not that the provider has a native structured-output feature (the
    gateway enforces the schema itself; this just declares the provider is
    capable of following a "respond with JSON" instruction acceptably)."""
    STRUCTURED_OUTPUT = "structured_output"
    STREAMING = "streaming"


@dataclass
class LLMRequest:
    """A fully rendered request -- by the time this reaches a provider,
    prompt rendering, schema-instruction injection, and egress
    redaction have already happened (apps.ai.services.gateway's job).
    Providers never see template_vars or raw tenant data structures,
    only the final prompt string."""
    prompt: str
    model: str
    temperature: float = 0.0
    top_p: float | None = None
    max_tokens: int = 1024
    seed: int | None = None
    stop: list[str] | None = None
    # Opaque passthrough for provider-specific tuning a future milestone
    # might need (e.g. a system prompt) without changing this dataclass's
    # shape -- deliberately untyped, unlike every field above.
    extra: dict = field(default_factory=dict)


@dataclass
class LLMResponse:
    """Raw provider output plus call metadata. `text` is unparsed,
    unvalidated -- the gateway is responsible for JSON-parsing and
    schema-validating it before it's usable for anything (Phase 7's I1/I6
    invariants: no un-validated response is ever usable)."""
    text: str
    model_id: str
    model_snapshot: str = ""
    provider_request_id: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int | None = None
    finish_reason: str = ""


class LLMProviderError(Exception):
    """Raised by a provider's complete() on any failure (auth, rate limit,
    network, malformed response envelope from the vendor SDK) -- the
    gateway catches this uniformly and records outcome=ERROR, never letting
    a raw vendor SDK exception type leak past the provider boundary."""


class LLMProvider(ABC):
    """One instance per call (constructed fresh by the factory, no shared
    mutable state, no caching) -- mirrors StorageService's
    get_storage_service() docstring: construction itself does no network
    I/O, so it's cheap and safe to call in a health check."""

    name: str = "unset"

    @abstractmethod
    def capabilities(self) -> frozenset[AICapability]:
        ...

    @abstractmethod
    def complete(self, request: LLMRequest) -> LLMResponse:
        """Raises LLMProviderError on any failure. Never returns partial/
        best-effort output silently -- a caller either gets a full
        LLMResponse or an exception, nothing in between."""
        ...
