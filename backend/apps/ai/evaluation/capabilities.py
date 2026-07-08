"""
Capability-aware evaluation registry -- maps each capability name to its
prompt/schema identity and golden-dataset location. This is what makes
EvaluationService/EvaluationRunner "capability-aware" per the Phase 7a.5
requirement: they never hardcode per-capability logic, they look it up
here. Adding a new capability's evaluation coverage (when 7b+ actually
implements one) is additive -- one new CapabilityConfig entry plus a
fixtures/golden/<name>/v1/cases.json file, nothing else changes.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class CapabilityConfig:
    name: str
    prompt_name: str
    response_schema_id: str
    response_schema_version: int
    fixture_dataset: str
    fixture_version: str


CAPABILITY_REGISTRY: dict[str, CapabilityConfig] = {
    "foundation.selftest": CapabilityConfig(
        name="foundation.selftest", prompt_name="foundation.selftest",
        response_schema_id="foundation.selftest", response_schema_version=1,
        fixture_dataset="foundation_selftest", fixture_version="v1",
    ),
    # Phase 7a.5 planned-capability entries -- eval-harness fixtures only,
    # no real feature implemented behind any of these yet (see
    # apps.ai.schemas's own docstring).
    "anomaly_detection": CapabilityConfig(
        name="anomaly_detection", prompt_name="anomaly_detection",
        response_schema_id="anomaly_detection", response_schema_version=1,
        fixture_dataset="anomaly_detection", fixture_version="v1",
    ),
    "factor_recommendation": CapabilityConfig(
        name="factor_recommendation", prompt_name="factor_recommendation",
        response_schema_id="factor_recommendation", response_schema_version=1,
        fixture_dataset="factor_recommendation", fixture_version="v1",
    ),
    "validation_assistance": CapabilityConfig(
        name="validation_assistance", prompt_name="validation_assistance",
        response_schema_id="validation_assistance", response_schema_version=1,
        fixture_dataset="validation_assistance", fixture_version="v1",
    ),
    "esg_assistant": CapabilityConfig(
        name="esg_assistant", prompt_name="esg_assistant",
        response_schema_id="esg_assistant", response_schema_version=1,
        fixture_dataset="esg_assistant", fixture_version="v1",
    ),
    "report_narration": CapabilityConfig(
        name="report_narration", prompt_name="report_narration",
        response_schema_id="report_narration", response_schema_version=1,
        fixture_dataset="report_narration", fixture_version="v1",
    ),
}


def get_capability_config(name: str) -> CapabilityConfig:
    try:
        return CAPABILITY_REGISTRY[name]
    except KeyError as exc:
        raise KeyError(f"No evaluation capability registered for {name!r}") from exc
