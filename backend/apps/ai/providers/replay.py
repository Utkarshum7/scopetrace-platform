"""
ReplayProvider -- deterministic, zero-cost, offline replay. The AI analog
of EchoProvider, purpose-built for Phase 7a.5's evaluation harness:
regression detection needs a provider that returns an EXACT, pre-recorded
response for a specific golden-dataset case, not EchoProvider's hash-of-
input stub (which satisfies no real schema by design -- see
EchoProvider's own docstring).

Never imports a vendor SDK -- like EchoProvider, this file exists so
'replay' is a real, selectable factory branch, not a special-cased bypass.

Two independent lookup modes, both requiring no network:

1. `request.extra["canned_response"]` (dict) -- returned verbatim as JSON
   text. This is what apps.ai.evaluation.runner.EvaluationRunner uses: it
   already has the golden fixture's expected_response in memory, so it
   passes it straight through rather than round-tripping via a file.
2. `request.extra["case_id"]` (str), with no canned_response -- loads
   apps/ai/providers/replay_fixtures/<case_id>.json from disk. Supports
   standalone/CLI replay (e.g. `get_llm_provider(provider_name="replay")`
   via TenantAIPolicy, for a NO_EGRESS tenant that wants deterministic
   canned answers) without a caller needing to pre-load fixtures itself.

Raises LLMProviderError if neither is supplied, or a case_id file doesn't
exist -- a caller misusing this provider fails loudly (I6: fail-safe, not
fail-open with a silently-empty response).
"""
import hashlib
import json
from pathlib import Path

from .base import AICapability, LLMProvider, LLMProviderError, LLMRequest, LLMResponse

_FIXTURES_DIR = Path(__file__).resolve().parent / "replay_fixtures"


class ReplayProvider(LLMProvider):
    name = "replay"

    def capabilities(self) -> frozenset[AICapability]:
        return frozenset({AICapability.STRUCTURED_OUTPUT})

    def complete(self, request: LLMRequest) -> LLMResponse:
        extra = request.extra or {}

        if "canned_response" in extra:
            text = json.dumps(extra["canned_response"])
        elif "case_id" in extra:
            text = self._load_fixture_text(extra["case_id"])
        else:
            raise LLMProviderError(
                "ReplayProvider requires request.extra['canned_response'] or ['case_id']."
            )

        return LLMResponse(
            text=text,
            model_id="replay-1",
            model_snapshot="replay-1",
            provider_request_id=f"replay-{hashlib.sha256(request.prompt.encode()).hexdigest()[:12]}",
            input_tokens=0,
            output_tokens=0,
            latency_ms=0,
            finish_reason="stop",
        )

    def _load_fixture_text(self, case_id: str) -> str:
        path = _FIXTURES_DIR / f"{case_id}.json"
        if not path.exists():
            raise LLMProviderError(f"No replay fixture found for case_id={case_id!r} at {path}")
        return path.read_text(encoding="utf-8")
