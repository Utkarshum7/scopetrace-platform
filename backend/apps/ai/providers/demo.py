"""
DemoProvider -- deterministic, zero-egress, zero-cost provider that returns
REAL, schema-VALID answers for demo deployments that have no external AI
credentials (Demo Mode: DEMO_MODE=True, no ANTHROPIC_API_KEY/OPENAI_API_KEY).

Why this exists alongside EchoProvider:
  EchoProvider is a plumbing stub -- for a normal prompt it returns
  {"echo": true, "input_digest": "..."}, which never satisfies any real
  capability's response schema, so every gateway call using it ends in
  SCHEMA_INVALID and the caller (e.g. ask_esg_assistant) gets nothing usable.
  That is fine for exercising the gateway in tests, but it means the ESG
  Assistant can never actually answer a question in a demo deployment.

DemoProvider instead inspects the rendered prompt to determine which
capability's response contract is expected -- keying on that schema's unique
required-field names, which every prompt template spells out verbatim in its
"Respond with ONLY a JSON object matching exactly: {...}" line -- and returns
a minimal, deterministic, schema-VALID instance of it. For the esg_assistant
capability it additionally keyword-matches a small built-in ESG knowledge base
so common questions ("What is Scope 1?", "What is a carbon footprint?",
"What is SAP fuel data?") get a genuine, useful answer with no network call.

Selected via AI_PROVIDER=demo or a TenantAIPolicy.provider_override of "demo"
(apps.core.management.commands.bootstrap_data seeds exactly that for the demo
organization). Makes zero network calls, so it is a zero-egress provider
(registered in apps.ai.services.egress.ZERO_EGRESS_PROVIDERS).
"""
import json
import logging

from .base import AICapability, LLMProvider, LLMRequest, LLMResponse

logger = logging.getLogger(__name__)

# Built-in ESG glossary for the esg_assistant capability. Each entry is
# (keywords, answer). The FIRST entry whose any keyword is a substring of the
# lowercased question wins; deterministic and order-sensitive on purpose.
_ESG_KNOWLEDGE: list[tuple[tuple[str, ...], str]] = [
    (
        ("scope 1", "scope1", "direct emission"),
        "Scope 1 covers direct greenhouse-gas emissions from sources an "
        "organization owns or controls -- for example fuel burned in company "
        "vehicles, on-site boilers, or furnaces. In ScopeTrace, uploads through "
        "the SAP Fuel feed typically map to Scope 1.",
    ),
    (
        ("scope 2", "scope2", "purchased electric", "purchased energy"),
        "Scope 2 covers indirect emissions from purchased energy -- the "
        "electricity, steam, heating, or cooling an organization buys and "
        "consumes. In ScopeTrace, the Utility Electricity feed maps to Scope 2.",
    ),
    (
        ("scope 3", "scope3", "value chain", "indirect emission"),
        "Scope 3 covers all other indirect emissions across the value chain -- "
        "business travel, purchased goods and services, waste, and more. In "
        "ScopeTrace, the Corporate Travel feed is a common Scope 3 source.",
    ),
    (
        ("carbon footprint", "co2e", "co2 equivalent", "greenhouse gas total"),
        "A carbon footprint is the total greenhouse-gas emissions attributable "
        "to an organization, expressed in tonnes of CO2-equivalent (tCO2e). "
        "ScopeTrace computes it by multiplying each activity's quantity by the "
        "matching emission factor and summing across Scopes 1, 2, and 3.",
    ),
    (
        ("sap fuel", "sap feed", "fuel data", "fuel feed"),
        "SAP fuel data is a procurement/fuel export from an SAP system. "
        "ScopeTrace's SAP parser ingests it (German or English headers, "
        "semicolon or comma delimited), normalizes quantity and unit, and "
        "resolves each row against an emission factor to produce Scope 1 "
        "emissions.",
    ),
    (
        ("emission factor", "factor dataset", "defra"),
        "An emission factor converts an activity quantity (e.g. litres of "
        "diesel, kWh of electricity) into greenhouse-gas emissions. ScopeTrace "
        "resolves factors deterministically from a versioned factor dataset "
        "(e.g. DEFRA) by activity type, unit, and validity period.",
    ),
    (
        ("what is scopetrace", "about scopetrace", "platform", "demo mode"),
        "ScopeTrace is an ESG data platform: it ingests activity data (fuel, "
        "electricity, travel), calculates carbon emissions against versioned "
        "factors, runs a review-and-approval workflow with a tamper-evident "
        "audit trail, and offers advisory AI features. This assistant is "
        "advisory only and never changes your data.",
    ),
    (
        ("carbon", "emission", "greenhouse", "ghg"),
        "Carbon accounting measures the greenhouse gases associated with an "
        "organization's activities, grouped into Scope 1 (direct), Scope 2 "
        "(purchased energy), and Scope 3 (value chain), and reported in tonnes "
        "of CO2-equivalent. ScopeTrace automates this from your uploaded data.",
    ),
]

_DEMO_CITATION = "ScopeTrace ESG glossary (built-in Demo Mode knowledge base)"


def _extract_question(prompt: str) -> str:
    """Pull the user's question out of the rendered esg_assistant prompt
    (the template line is `Question: $question`). Falls back to the whole
    prompt if the marker isn't present."""
    marker = "Question:"
    idx = prompt.rfind(marker)
    if idx == -1:
        return prompt
    tail = prompt[idx + len(marker):]
    # Stop at the JSON-shape instruction line if present.
    cut = tail.find("Respond with ONLY")
    if cut != -1:
        tail = tail[:cut]
    return tail.strip()


def _esg_assistant_answer(prompt: str) -> dict:
    question = _extract_question(prompt).lower()
    for keywords, answer in _ESG_KNOWLEDGE:
        if any(kw in question for kw in keywords):
            return {
                "answer": answer,
                "citations": [_DEMO_CITATION],
                "confidence": "HIGH",
                "unsupported_claim": False,
            }
    # No known topic matched -- answer honestly rather than fabricating, and
    # flag it as unsupported (drives the UI's "not fully supported" badge).
    return {
        "answer": (
            "I'm running in Demo Mode with a built-in knowledge base, so I can "
            "answer general ESG questions about scopes, carbon footprints, "
            "emission factors, and how ScopeTrace ingests and calculates data. "
            "I don't have a confident answer to this specific question from the "
            "retrieved context."
        ),
        "citations": [_DEMO_CITATION],
        "confidence": "LOW",
        "unsupported_claim": True,
    }


# Each capability schema is identified by a required field name that is unique
# to it across the whole schema registry (apps.ai.schemas) and is written
# verbatim into that capability's prompt template's JSON-shape line -- a stable
# signal tied to the response CONTRACT, not to prose. Order matters only in
# that the first match wins; the keys are mutually exclusive in practice.
def _build_response_dict(prompt: str) -> dict:
    if "unsupported_claim" in prompt:  # esg_assistant
        return _esg_assistant_answer(prompt)
    if "contributing_factors" in prompt:  # anomaly_detection v2
        return {
            "explanation": (
                "Demo Mode: this record was flagged by the deterministic "
                "validator as unusual relative to its peers."
            ),
            "contributing_factors": ["value outside the typical range for this source"],
            "confidence": "MEDIUM",
            "suggested_investigation": "Confirm the source figure and unit against the original document.",
        }
    if "recommended_candidate_label" in prompt:  # factor_recommendation v2
        return {
            "recommended_candidate_label": "none",
            "confidence": "LOW",
            "explanation": "Demo Mode does not select an emission factor automatically.",
            "reasoning": "Factor selection stays with the deterministic resolver; this is advisory only.",
            "alternative_candidates": [],
        }
    if "affected_fields" in prompt:  # validation_assistance v2
        return {
            "explanation": "Demo Mode: this row failed deterministic validation.",
            "affected_fields": [],
            "confidence": "LOW",
            "suggested_correction": "Review the row against the source document and re-upload if needed.",
        }
    if "executive_summary" in prompt:  # report_narration v2
        return {
            "executive_summary": "Demo Mode summary of the approved emissions in this period.",
            "key_highlights": ["Generated by the built-in Demo Mode provider (no external AI call)."],
            "trend_explanations": "Trends are derived only from figures already computed by ScopeTrace.",
            "recommendations": ["Configure a real AI provider for narrative depth beyond Demo Mode."],
            "confidence": "MEDIUM",
        }
    if "acknowledged" in prompt:  # foundation.selftest v1
        return {"acknowledged": True, "echo": "demo"}
    # Unknown contract -- safest valid default is an esg_assistant answer,
    # which is the only capability reachable from the demo UI. If this ever
    # routes to a different schema it degrades to SCHEMA_INVALID (no annotation
    # created), identical to today's AI-disabled behavior -- never a crash.
    return _esg_assistant_answer(prompt)


class DemoProvider(LLMProvider):
    """Deterministic, zero-network provider returning schema-valid demo
    answers. See module docstring."""

    name = "demo"

    def capabilities(self) -> frozenset[AICapability]:
        return frozenset({AICapability.STRUCTURED_OUTPUT})

    def complete(self, request: LLMRequest) -> LLMResponse:
        response_dict = _build_response_dict(request.prompt)
        logger.info(
            "DemoProvider.complete: reached; response keys=%s",
            sorted(response_dict.keys()),
        )
        text = json.dumps(response_dict)
        return LLMResponse(
            text=text,
            model_id="demo-1",
            model_snapshot="demo-1",
            provider_request_id="demo",
            input_tokens=len(request.prompt.split()),
            output_tokens=len(text.split()),
            latency_ms=0,
            finish_reason="stop",
        )
