"""
EvaluationRunner -- executes a list of EvaluationCase objects and returns a
list of EvaluationOutcome objects. Pure execution logic: no persistence, no
tier orchestration (that's EvaluationService, next). Deliberately bypasses
apps.ai.services.gateway.invoke_ai() -- the gateway's job is tenant policy/
budget/egress/audit enforcement for REAL calls; evaluation is a different
execution mode entirely (offline, zero-cost, no tenant/organization
context) that reuses the gateway's two SHARED building blocks instead of
duplicating them: apps.ai.prompts.registry.render_prompt() (identical
prompt-rendering logic) and apps.ai.schemas.validate_response() (identical
schema-validation logic). Two implementations of either would risk exactly
the kind of silent drift this codebase's own architecture reviews exist to
catch.

For each case, in order:
1. Render the prompt (apps.ai.prompts.registry.render_prompt()).
2. Compare the freshly-rendered template_hash/rendered_input_hash against
   the fixture's own recorded snapshot -- a mismatch means the prompt (or
   its rendering) drifted from what the golden fixture was authored
   against, without the fixture being updated to match: REGRESSION.
3. Resolve the schema and validate the fixture's own expected_response
   against it -- if the schema changed shape without the fixture being
   updated: SCHEMA_INVALID.
4. Call a provider (ReplayProvider by default) with the case's
   expected_response as its canned_response, proving the deterministic
   replay + schema-validation + scoring pipeline runs end to end with zero
   cost, zero network, fully offline.
5. Score actual vs. expected; below `case.min_score`: REGRESSION.

Any unexpected exception during steps 1-5 (not a specific classified
failure) is caught and recorded as EVALUATION_FAILURE -- the runner itself
never lets an unhandled exception propagate and abort an entire batch over
one bad case.
"""
import json
from dataclasses import dataclass

from django.core.exceptions import ImproperlyConfigured

from apps.ai.evaluation.fixtures.loader import EvaluationCase
from apps.ai.evaluation.scoring import score_exact_match
from apps.ai.prompts.registry import render_prompt
from apps.ai.providers.base import LLMProviderError, LLMRequest
from apps.ai.providers.factory import get_llm_provider
from apps.ai.schemas import get_schema, validate_response

OUTCOME_OK = "OK"
OUTCOME_SCHEMA_INVALID = "SCHEMA_INVALID"
OUTCOME_REGRESSION = "REGRESSION"
OUTCOME_PROVIDER_ERROR = "PROVIDER_ERROR"
OUTCOME_EVALUATION_FAILURE = "EVALUATION_FAILURE"


@dataclass
class EvaluationOutcome:
    case_id: str
    capability: str
    prompt_name: str
    outcome: str
    score: float | None = None
    detail: str = ""
    prompt_template_hash: str = ""
    rendered_input_hash: str = ""


class EvaluationRunner:
    def __init__(self, *, provider_name: str = "replay", score_fn=score_exact_match):
        self._provider_name = provider_name
        self._score_fn = score_fn

    def run_case(self, case: EvaluationCase) -> EvaluationOutcome:
        try:
            return self._run_case(case)
        except Exception as exc:  # noqa: BLE001 - any unclassified failure becomes EVALUATION_FAILURE
            return EvaluationOutcome(
                case_id=case.case_id, capability=case.capability, prompt_name=case.prompt_name,
                outcome=OUTCOME_EVALUATION_FAILURE, detail=f"unexpected evaluation error: {exc}",
            )

    def run_cases(self, cases: list[EvaluationCase]) -> list[EvaluationOutcome]:
        return [self.run_case(case) for case in cases]

    def _run_case(self, case: EvaluationCase) -> EvaluationOutcome:
        rendered = render_prompt(
            case.prompt_name, case.template_vars,
            response_schema_id=case.response_schema_id,
            response_schema_version=case.response_schema_version,
        )

        if (
            rendered.template_hash != case.expected_prompt_template_hash
            or rendered.rendered_input_hash != case.expected_rendered_input_hash
        ):
            return EvaluationOutcome(
                case_id=case.case_id, capability=case.capability, prompt_name=case.prompt_name,
                outcome=OUTCOME_REGRESSION,
                detail=(
                    "prompt/template hash drifted from the golden fixture's recorded snapshot -- "
                    "the template or template_vars changed without the fixture being updated to match"
                ),
                prompt_template_hash=rendered.template_hash,
                rendered_input_hash=rendered.rendered_input_hash,
            )

        try:
            schema = get_schema(case.response_schema_id, case.response_schema_version)
        except KeyError as exc:
            return EvaluationOutcome(
                case_id=case.case_id, capability=case.capability, prompt_name=case.prompt_name,
                outcome=OUTCOME_SCHEMA_INVALID, detail=str(exc),
                prompt_template_hash=rendered.template_hash, rendered_input_hash=rendered.rendered_input_hash,
            )

        _fixture_parsed, fixture_schema_valid = validate_response(json.dumps(case.expected_response), schema)
        if not fixture_schema_valid:
            return EvaluationOutcome(
                case_id=case.case_id, capability=case.capability, prompt_name=case.prompt_name,
                outcome=OUTCOME_SCHEMA_INVALID,
                detail="the golden fixture's own expected_response no longer matches the live schema",
                prompt_template_hash=rendered.template_hash, rendered_input_hash=rendered.rendered_input_hash,
            )

        request = LLMRequest(
            prompt=rendered.text, model="replay-1", extra={"canned_response": case.expected_response},
        )
        try:
            provider = get_llm_provider(provider_name=self._provider_name)
            response = provider.complete(request)
        except (LLMProviderError, ImproperlyConfigured) as exc:
            return EvaluationOutcome(
                case_id=case.case_id, capability=case.capability, prompt_name=case.prompt_name,
                outcome=OUTCOME_PROVIDER_ERROR, detail=str(exc),
                prompt_template_hash=rendered.template_hash, rendered_input_hash=rendered.rendered_input_hash,
            )

        actual_parsed, actual_valid = validate_response(response.text, schema)
        if not actual_valid:
            return EvaluationOutcome(
                case_id=case.case_id, capability=case.capability, prompt_name=case.prompt_name,
                outcome=OUTCOME_SCHEMA_INVALID, detail="replayed response failed schema validation",
                prompt_template_hash=rendered.template_hash, rendered_input_hash=rendered.rendered_input_hash,
            )

        score = self._score_fn(actual_parsed, case.expected_response)
        if score < case.min_score:
            return EvaluationOutcome(
                case_id=case.case_id, capability=case.capability, prompt_name=case.prompt_name,
                outcome=OUTCOME_REGRESSION, score=score,
                detail=f"score {score} below required minimum {case.min_score}",
                prompt_template_hash=rendered.template_hash, rendered_input_hash=rendered.rendered_input_hash,
            )

        return EvaluationOutcome(
            case_id=case.case_id, capability=case.capability, prompt_name=case.prompt_name,
            outcome=OUTCOME_OK, score=score,
            prompt_template_hash=rendered.template_hash, rendered_input_hash=rendered.rendered_input_hash,
        )
