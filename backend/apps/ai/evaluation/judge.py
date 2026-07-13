"""
LLM-as-Judge framework -- Tier 2 (advisory) evaluation. FRAMEWORK ONLY:
rubric definitions, pairwise comparison, a scoring interface. Real, tested
code (not a TODO stub -- same precedent as the carbon pipeline's
AIRecommendationStage, a real class that's simply inert until a feature
turns it on), but NOT enabled by default and NOT called by any Tier 1
(blocking) test or CI job -- see docs/AI_EVALUATION.md.

settings.AI_JUDGE_ENABLED (default False) gates every entry point: calling
run_judge_scoring()/run_pairwise_comparison() while disabled raises
JudgeDisabledError immediately, before rendering a prompt or touching a
provider. Even when explicitly enabled (this module's own tests, and the
milestone's own Tier 2 CI job), judge calls go through the SAME
get_llm_provider()/render_prompt()/validate_response() building blocks as
everything else in apps.ai -- defaulting to 'echo'/'replay' in
DEBUG/_TESTING, so "framework only" doesn't mean "untested," it means
"never wired to a real vendor call by default, and never a Tier 1
blocking gate."
"""
from dataclasses import dataclass

from django.conf import settings

from apps.ai.prompts.registry import render_prompt
from apps.ai.providers.base import LLMRequest
from apps.ai.providers.factory import get_llm_provider
from apps.ai.schemas import get_schema, validate_response


class JudgeDisabledError(Exception):
    """Raised by every judge entry point when settings.AI_JUDGE_ENABLED is False."""


class JudgeInvalidResponseError(Exception):
    """Raised when the judge provider's response fails schema validation."""


@dataclass(frozen=True)
class JudgeRubric:
    """A named set of criteria a judge scores a response against."""
    name: str
    criteria: list[str]
    scale_description: str = "0.0 (fails every criterion) to 1.0 (meets every criterion)"


@dataclass(frozen=True)
class JudgeScoringResult:
    score: float
    rationale: str


@dataclass(frozen=True)
class PairwiseComparisonResult:
    winner: str  # "A" | "B" | "TIE"
    rationale: str


def _require_enabled():
    if not settings.AI_JUDGE_ENABLED:
        raise JudgeDisabledError(
            "AI_JUDGE_ENABLED is False -- the LLM-as-Judge framework is not enabled."
        )


def run_judge_scoring(
    rubric: JudgeRubric, candidate_response: dict, *, context: dict | None = None, provider_name: str = "echo",
) -> JudgeScoringResult:
    """Scores one response against a rubric. Raises JudgeDisabledError if
    AI_JUDGE_ENABLED is False, JudgeInvalidResponseError if the provider's
    response fails schema validation, LLMProviderError if the provider
    call itself fails."""
    _require_enabled()

    rendered = render_prompt(
        "judge_scoring",
        {
            "rubric_name": rubric.name,
            "criteria": "; ".join(rubric.criteria),
            "scale_description": rubric.scale_description,
            "context": str(context or {}),
            "candidate_response": str(candidate_response),
        },
        response_schema_id="judge_scoring", response_schema_version=1,
    )
    provider = get_llm_provider(provider_name=provider_name)
    response = provider.complete(LLMRequest(prompt=rendered.text, model="judge-1"))

    schema = get_schema("judge_scoring", 1)
    parsed, valid = validate_response(response.text, schema)
    if not valid:
        raise JudgeInvalidResponseError("Judge response failed schema validation.")

    return JudgeScoringResult(score=parsed["score"], rationale=parsed["rationale"])


def run_pairwise_comparison(
    rubric: JudgeRubric, response_a: dict, response_b: dict, *,
    context: dict | None = None, provider_name: str = "echo",
) -> PairwiseComparisonResult:
    """Compares two responses against a rubric, returning which one better
    satisfies it (or a tie). Same error contract as run_judge_scoring()."""
    _require_enabled()

    rendered = render_prompt(
        "judge_pairwise",
        {
            "rubric_name": rubric.name,
            "criteria": "; ".join(rubric.criteria),
            "context": str(context or {}),
            "response_a": str(response_a),
            "response_b": str(response_b),
        },
        response_schema_id="judge_pairwise", response_schema_version=1,
    )
    provider = get_llm_provider(provider_name=provider_name)
    response = provider.complete(LLMRequest(prompt=rendered.text, model="judge-1"))

    schema = get_schema("judge_pairwise", 1)
    parsed, valid = validate_response(response.text, schema)
    if not valid:
        raise JudgeInvalidResponseError("Judge response failed schema validation.")

    return PairwiseComparisonResult(winner=parsed["winner"], rationale=parsed["rationale"])
