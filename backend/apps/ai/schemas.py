"""
Response schema registry -- one JSON Schema per (schema_id, version), used by
apps.ai.services.gateway to validate every provider response before it is
usable for anything (Phase 7's mandatory schema-enforced envelope -- see
docs/AI_ARCHITECTURE.md). Kept separate from apps.ai.prompts so a schema can
be versioned independently of any one prompt template referencing it (a
future capability might pair several prompt variants with the same response
shape).

Only one schema exists in Phase 7a: the foundation self-test capability
used to prove the prompt-registry + gateway plumbing end to end. Real
capability schemas (anomaly suggestions, factor recommendations, ...) are
added by their own milestones (7b+), never here.
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

_SCHEMAS = {
    ("foundation.selftest", 1): FOUNDATION_SELFTEST_V1,
}


def get_schema(schema_id: str, version: int) -> dict:
    try:
        return _SCHEMAS[(schema_id, version)]
    except KeyError as exc:
        raise KeyError(f"No response schema registered for ({schema_id!r}, v{version})") from exc
