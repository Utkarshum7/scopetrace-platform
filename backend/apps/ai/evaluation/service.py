"""
EvaluationService -- the orchestration layer a CI job or management command
calls: loads golden fixtures (all capabilities, or one), runs them through
EvaluationRunner, and PERSISTS the result as one EvaluationRun +
one EvaluationResult per case. EvaluationRunner itself stays pure/
side-effect-free (no DB writes) so it's trivially unit-testable in
isolation; this module is the only place that writes evaluation history.
"""
from django.utils import timezone

from apps.ai.evaluation.fixtures.loader import load_all_golden_cases, load_golden_cases_for_capability
from apps.ai.evaluation.models import EvaluationResult, EvaluationRun
from apps.ai.evaluation.runner import OUTCOME_OK, EvaluationRunner


def run_tier1_evaluation(*, trigger: str = "manual", capability: str | None = None) -> EvaluationRun:
    """Runs the full deterministic (Tier 1) suite -- every golden case for
    every registered capability, or just one capability if given -- and
    persists the result. Returns the completed EvaluationRun (with its
    `results` reverse relation populated)."""
    cases = load_golden_cases_for_capability(capability) if capability else load_all_golden_cases()

    run = EvaluationRun.objects.create(
        tier=EvaluationRun.Tier.TIER_1_DETERMINISTIC, trigger=trigger, total_cases=len(cases),
    )

    runner = EvaluationRunner(provider_name="replay")
    outcomes = runner.run_cases(cases)

    passed = 0
    for outcome in outcomes:
        EvaluationResult.objects.create(
            run=run, capability=outcome.capability, case_id=outcome.case_id,
            prompt_name=outcome.prompt_name, outcome=outcome.outcome, score=outcome.score,
            detail=outcome.detail, prompt_template_hash=outcome.prompt_template_hash,
            rendered_input_hash=outcome.rendered_input_hash,
        )
        if outcome.outcome == OUTCOME_OK:
            passed += 1

    run.passed_cases = passed
    run.failed_cases = len(outcomes) - passed
    run.status = EvaluationRun.Status.COMPLETED
    run.finished_at = timezone.now()
    run.save(update_fields=["passed_cases", "failed_cases", "status", "finished_at"])
    return run
