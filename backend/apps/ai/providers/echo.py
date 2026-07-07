"""
EchoProvider -- deterministic, zero-egress, zero-cost provider. The AI
analog of StorageService's 'local' backend / CELERY_TASK_ALWAYS_EAGER: the
default in DEBUG/_TESTING (settings.AI_PROVIDER), so the entire gateway path
is exercised in dev and CI without any real credentials, network call, or
vendor SDK import.

Never imports a vendor SDK (nothing to import) -- this file exists purely so
'echo' is a real, selectable branch in the factory, not a special-cased
bypass of it.
"""
import hashlib
import json
import re

from .base import AICapability, LLMProvider, LLMRequest, LLMResponse

_SCHEMA_HINT_RE = re.compile(r"__ECHO_SCHEMA__:(\{.*\})__END_SCHEMA__", re.DOTALL)


class EchoProvider(LLMProvider):
    """Returns a deterministic function of the input, never a real
    completion. Two modes, both requiring no network:

    1. If the prompt embeds a canned response via
       apps.ai.providers.echo.canned(response_dict) folded into the prompt
       text, that exact JSON is echoed back verbatim -- lets a test assert
       an exact gateway outcome (e.g. schema_invalid) without needing a real
       model.
    2. Otherwise, returns a minimal, deterministic JSON object derived only
       from a hash of the input -- proves the gateway's parse/validate/hash
       pipeline runs end-to-end without asserting on real model content.
    """

    name = "echo"

    def capabilities(self) -> frozenset[AICapability]:
        return frozenset({AICapability.STRUCTURED_OUTPUT})

    def complete(self, request: LLMRequest) -> LLMResponse:
        canned_match = _SCHEMA_HINT_RE.search(request.prompt)
        if canned_match:
            text = canned_match.group(1)
        else:
            digest = hashlib.sha256(request.prompt.encode("utf-8")).hexdigest()[:16]
            text = json.dumps({"echo": True, "input_digest": digest})

        return LLMResponse(
            text=text,
            model_id="echo-1",
            model_snapshot="echo-1",
            provider_request_id=f"echo-{hashlib.sha256(request.prompt.encode()).hexdigest()[:12]}",
            input_tokens=len(request.prompt.split()),
            output_tokens=len(text.split()),
            latency_ms=0,
            finish_reason="stop",
        )


def canned(response: dict) -> str:
    """Embed an exact canned JSON response into a prompt for EchoProvider to
    echo back verbatim -- test-only helper, used to assert specific gateway
    outcomes (e.g. a response that fails schema validation on purpose)."""
    return f"__ECHO_SCHEMA__:{json.dumps(response)}__END_SCHEMA__"
