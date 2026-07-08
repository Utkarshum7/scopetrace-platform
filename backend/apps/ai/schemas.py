"""
Response schema registry -- one JSON Schema per (schema_id, version), used by
apps.ai.services.gateway (real calls) and apps.ai.evaluation.runner (golden-
dataset regression checks) to validate every response before it is usable
for anything (Phase 7's mandatory schema-enforced envelope -- see
docs/AI_ARCHITECTURE.md). Kept separate from apps.ai.prompts so a schema can
be versioned independently of any one prompt template referencing it (a
future capability might pair several prompt variants with the same response
shape).

Phase 7a shipped one schema: foundation.selftest, a non-business capability
that exercises the prompt-registry + gateway plumbing end to end. Phase
7a.5 added five more -- PLANNED schemas for anomaly_detection (7b),
factor_recommendation (7c), validation_assistance (7d), esg_assistant (7e),
and report_narration (7f), existing ONLY so the evaluation harness had
real, capability-shaped contracts to test its own plumbing against, wired
to no real business logic. Phase 7b is the first feature milestone to
actually implement one: anomaly_detection is now real (see
apps.ai.services.anomaly_detection), and its schema bumped v1 -> v2 --
v1 had the AI output `is_anomalous: bool`, which would let the AI
CLASSIFY a record; v2 drops that field entirely (the deterministic engine
already decided is_suspicious before AI is ever called -- AI only
explains). Phase 7c implements factor_recommendation next: v1 had the AI
output `recommended_activity_type` as free text; v2 instead asks the AI to
pick a LABEL (candidate_1, candidate_2, ..., or "none") from a small,
service-provided candidate set of real EmissionFactor rows -- never a raw
identifier, since LLMs are unreliable at reproducing UUIDs verbatim (see
apps.ai.services.factor_recommendation). Both v1 schemas are kept,
unreferenced, as a historical record of the original placeholder contract
-- never edited in place, matching every other versioned artifact in this
codebase (AIPromptVersion, golden
datasets).
"""

FOUNDATION_SELFTEST_V1 = {
    "type": "object",
    "required": ["acknowledged", "echo"],
    "properties": {
        "acknowledged": {"type": "boolean"},
        "echo": {"type": "string"},
    },
    "additionalProperties": False,
}

# --- Phase 7a.5: planned-capability schemas (eval-harness fixtures only) ---

# Superseded by ANOMALY_DETECTION_V2 (Phase 7b) -- kept, unreferenced, as a
# historical record of the original placeholder contract. Never edited in
# place.
ANOMALY_DETECTION_V1 = {
    "type": "object",
    "required": ["is_anomalous", "explanation", "confidence"],
    "properties": {
        "is_anomalous": {"type": "boolean"},
        "explanation": {"type": "string"},
        "confidence": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH"]},
    },
    "additionalProperties": False,
}

# Phase 7b: the real anomaly-EXPLANATION contract. Deliberately has NO
# is_anomalous (or any other classification) field -- the deterministic
# engine already decided a record is suspicious before this capability is
# ever invoked (apps.ingestion.services.validator.RowValidator); AI's only
# job is to explain that decision, never restate or override it. Every
# field maps directly to Milestone 7b's four required outputs: why the
# record is unusual (explanation), likely contributing factors, confidence,
# and a suggested investigation.
ANOMALY_DETECTION_V2 = {
    "type": "object",
    "required": ["explanation", "contributing_factors", "confidence", "suggested_investigation"],
    "properties": {
        "explanation": {"type": "string"},
        "contributing_factors": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH"]},
        "suggested_investigation": {"type": "string"},
    },
    "additionalProperties": False,
}

FACTOR_RECOMMENDATION_V1 = {
    "type": "object",
    "required": ["recommended_activity_type", "confidence", "rationale"],
    "properties": {
        "recommended_activity_type": {"type": "string"},
        "confidence": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH"]},
        "rationale": {"type": "string"},
    },
    "additionalProperties": False,
}

# Phase 7c: the real factor-RECOMMENDATION contract. Deliberately never
# asks the AI to reproduce an EmissionFactor's UUID -- LLMs are unreliable
# at reproducing identifiers verbatim, so the service instead shows the AI
# a small, labeled candidate set (candidate_1, candidate_2, ...) and asks
# it to pick a LABEL (or "none"), which the service then resolves back to
# a real object it already holds in memory. AI never chooses the factor
# used for calculation -- that stays apps.carbon.services.resolution's
# deterministic FactorIndex.resolve(), unchanged; this is advisory only.
FACTOR_RECOMMENDATION_V2 = {
    "type": "object",
    "required": ["recommended_candidate_label", "confidence", "explanation", "reasoning", "alternative_candidates"],
    "properties": {
        "recommended_candidate_label": {"type": "string"},
        "confidence": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH"]},
        "explanation": {"type": "string"},
        "reasoning": {"type": "string"},
        "alternative_candidates": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}

VALIDATION_ASSISTANCE_V1 = {
    "type": "object",
    "required": ["suggested_correction", "confidence", "rationale"],
    "properties": {
        "suggested_correction": {"type": "string"},
        "confidence": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH"]},
        "rationale": {"type": "string"},
    },
    "additionalProperties": False,
}

# Free text lives inside a typed field of a validated envelope -- never as
# the entire response (ADR 0005) -- `citations`/`unsupported_claim` make a
# fabricated or unsupported answer machine-checkable, not just readable.
ESG_ASSISTANT_V1 = {
    "type": "object",
    "required": ["answer", "citations", "confidence", "unsupported_claim"],
    "properties": {
        "answer": {"type": "string"},
        "citations": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH"]},
        "unsupported_claim": {"type": "boolean"},
    },
    "additionalProperties": False,
}

REPORT_NARRATION_V1 = {
    "type": "object",
    "required": ["narrative", "referenced_figures"],
    "properties": {
        "narrative": {"type": "string"},
        "referenced_figures": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}

# --- Phase 7a.5: LLM-as-Judge framework schemas (apps.ai.evaluation.judge) ---

JUDGE_SCORING_V1 = {
    "type": "object",
    "required": ["score", "rationale"],
    "properties": {
        "score": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "rationale": {"type": "string"},
    },
    "additionalProperties": False,
}

JUDGE_PAIRWISE_V1 = {
    "type": "object",
    "required": ["winner", "rationale"],
    "properties": {
        "winner": {"type": "string", "enum": ["A", "B", "TIE"]},
        "rationale": {"type": "string"},
    },
    "additionalProperties": False,
}

_SCHEMAS = {
    ("foundation.selftest", 1): FOUNDATION_SELFTEST_V1,
    ("anomaly_detection", 1): ANOMALY_DETECTION_V1,
    ("anomaly_detection", 2): ANOMALY_DETECTION_V2,
    ("factor_recommendation", 1): FACTOR_RECOMMENDATION_V1,
    ("factor_recommendation", 2): FACTOR_RECOMMENDATION_V2,
    ("validation_assistance", 1): VALIDATION_ASSISTANCE_V1,
    ("esg_assistant", 1): ESG_ASSISTANT_V1,
    ("judge_scoring", 1): JUDGE_SCORING_V1,
    ("judge_pairwise", 1): JUDGE_PAIRWISE_V1,
    ("report_narration", 1): REPORT_NARRATION_V1,
}


def get_schema(schema_id: str, version: int) -> dict:
    try:
        return _SCHEMAS[(schema_id, version)]
    except KeyError as exc:
        raise KeyError(f"No response schema registered for ({schema_id!r}, v{version})") from exc


def validate_response(response_text: str, schema: dict) -> tuple[dict | None, bool]:
    """Parse response_text as JSON and validate against schema. Returns
    (parsed, True) on success, (None, False) on any parse or validation
    failure.

    The single implementation apps.ai.services.gateway.invoke_ai() (real
    calls) and apps.ai.evaluation.runner.EvaluationRunner (golden-dataset
    checks) both use, so "no un-validated response is ever usable" (I1/I6)
    has exactly one implementation to prove correct, not two that could
    silently diverge.
    """
    import json

    import jsonschema

    try:
        candidate = json.loads(response_text)
        jsonschema.validate(candidate, schema)
        return candidate, True
    except (json.JSONDecodeError, jsonschema.ValidationError):
        return None, False
