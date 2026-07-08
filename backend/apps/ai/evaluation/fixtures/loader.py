"""
Loads golden-dataset fixture files (apps/ai/evaluation/fixtures/golden/
<dataset>/<version>/cases.json) into EvaluationCase objects.

Versioned by directory, not by a field inside the file -- "golden dataset
v2" is a new directory, so v1's fixtures never silently change out from
under a regression run that still references them (the same reasoning
AIPromptVersion itself uses: each version is its own immutable row/file,
never edited in place).
"""
import json
from dataclasses import dataclass, field
from pathlib import Path

_GOLDEN_DIR = Path(__file__).resolve().parent / "golden"


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    capability: str
    prompt_name: str
    template_vars: dict
    expected_response: dict
    response_schema_id: str
    response_schema_version: int
    expected_prompt_template_hash: str
    expected_rendered_input_hash: str
    min_score: float = 1.0
    description: str = field(default="")


def load_golden_cases(*, capability: str, dataset: str, version: str) -> list[EvaluationCase]:
    path = _GOLDEN_DIR / dataset / version / "cases.json"
    if not path.exists():
        raise FileNotFoundError(f"No golden dataset found for {capability!r} at {path}")

    raw_cases = json.loads(path.read_text(encoding="utf-8"))
    return [
        EvaluationCase(
            case_id=raw["case_id"],
            capability=capability,
            prompt_name=raw["prompt_name"],
            template_vars=raw["template_vars"],
            expected_response=raw["expected_response"],
            response_schema_id=raw["response_schema_id"],
            response_schema_version=raw["response_schema_version"],
            expected_prompt_template_hash=raw["expected_prompt_template_hash"],
            expected_rendered_input_hash=raw["expected_rendered_input_hash"],
            min_score=raw.get("min_score", 1.0),
            description=raw.get("description", ""),
        )
        for raw in raw_cases
    ]


def load_golden_cases_for_capability(capability: str) -> list[EvaluationCase]:
    from apps.ai.evaluation.capabilities import get_capability_config

    config = get_capability_config(capability)
    return load_golden_cases(
        capability=capability, dataset=config.fixture_dataset, version=config.fixture_version,
    )


def load_all_golden_cases() -> list[EvaluationCase]:
    """Every case across every registered capability -- what a full Tier 1
    evaluation run executes."""
    from apps.ai.evaluation.capabilities import CAPABILITY_REGISTRY

    cases = []
    for capability in CAPABILITY_REGISTRY:
        cases.extend(load_golden_cases_for_capability(capability))
    return cases
