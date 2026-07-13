"""Phase 7a.5 -- EvaluationRunner tests. Deliberately constructs
EvaluationCase objects with EACH kind of expected failure (not just the
happy path) so every classification (SCHEMA_INVALID / REGRESSION /
PROVIDER_ERROR / EVALUATION_FAILURE / OK) is proven distinctly."""
from django.test import TestCase, override_settings

from apps.ai.evaluation.fixtures.loader import EvaluationCase, load_golden_cases_for_capability
from apps.ai.evaluation.runner import (
    OUTCOME_EVALUATION_FAILURE,
    OUTCOME_OK,
    OUTCOME_PROVIDER_ERROR,
    OUTCOME_REGRESSION,
    OUTCOME_SCHEMA_INVALID,
    EvaluationRunner,
)


def _foundation_case(**overrides):
    real_case = load_golden_cases_for_capability("foundation.selftest")[0]
    fields = {
        "case_id": real_case.case_id, "capability": real_case.capability,
        "prompt_name": real_case.prompt_name, "template_vars": real_case.template_vars,
        "expected_response": real_case.expected_response,
        "response_schema_id": real_case.response_schema_id,
        "response_schema_version": real_case.response_schema_version,
        "expected_prompt_template_hash": real_case.expected_prompt_template_hash,
        "expected_rendered_input_hash": real_case.expected_rendered_input_hash,
        "min_score": real_case.min_score,
    }
    fields.update(overrides)
    return EvaluationCase(**fields)


class EvaluationRunnerHappyPathTests(TestCase):
    def test_real_golden_case_scores_ok(self):
        runner = EvaluationRunner()
        outcome = runner.run_case(_foundation_case())
        self.assertEqual(outcome.outcome, OUTCOME_OK)
        self.assertEqual(outcome.score, 1.0)

    def test_ok_outcome_carries_live_hashes(self):
        runner = EvaluationRunner()
        outcome = runner.run_case(_foundation_case())
        self.assertTrue(outcome.prompt_template_hash)
        self.assertTrue(outcome.rendered_input_hash)

    def test_run_cases_processes_a_list(self):
        runner = EvaluationRunner()
        cases = load_golden_cases_for_capability("anomaly_detection")
        outcomes = runner.run_cases(cases)
        self.assertEqual(len(outcomes), len(cases))
        self.assertTrue(all(o.outcome == OUTCOME_OK for o in outcomes))


class EvaluationRunnerRegressionDetectionTests(TestCase):
    def test_stale_template_hash_is_a_regression(self):
        runner = EvaluationRunner()
        case = _foundation_case(expected_prompt_template_hash="0" * 64)
        outcome = runner.run_case(case)
        self.assertEqual(outcome.outcome, OUTCOME_REGRESSION)
        self.assertIn("hash drifted", outcome.detail)

    def test_stale_rendered_input_hash_is_a_regression(self):
        runner = EvaluationRunner()
        case = _foundation_case(expected_rendered_input_hash="0" * 64)
        outcome = runner.run_case(case)
        self.assertEqual(outcome.outcome, OUTCOME_REGRESSION)

    def test_score_below_threshold_is_a_regression(self):
        runner = EvaluationRunner()
        case = _foundation_case(min_score=1.1)  # unattainable -- exact match maxes at 1.0
        outcome = runner.run_case(case)
        self.assertEqual(outcome.outcome, OUTCOME_REGRESSION)
        self.assertIn("below required minimum", outcome.detail)


class EvaluationRunnerSchemaInvalidTests(TestCase):
    def test_unknown_schema_id_is_schema_invalid(self):
        runner = EvaluationRunner()
        case = _foundation_case(response_schema_id="no.such.schema")
        outcome = runner.run_case(case)
        self.assertEqual(outcome.outcome, OUTCOME_SCHEMA_INVALID)

    def test_expected_response_not_matching_schema_is_schema_invalid(self):
        runner = EvaluationRunner()
        case = _foundation_case(expected_response={"acknowledged": "not-a-bool", "echo": "x"})
        outcome = runner.run_case(case)
        self.assertEqual(outcome.outcome, OUTCOME_SCHEMA_INVALID)
        self.assertIn("no longer matches the live schema", outcome.detail)


class EvaluationRunnerProviderErrorTests(TestCase):
    @override_settings(ANTHROPIC_API_KEY="")
    def test_misconfigured_provider_is_provider_error(self):
        runner = EvaluationRunner(provider_name="anthropic")
        outcome = runner.run_case(_foundation_case())
        self.assertEqual(outcome.outcome, OUTCOME_PROVIDER_ERROR)


class EvaluationRunnerEvaluationFailureTests(TestCase):
    def test_missing_prompt_template_is_an_evaluation_failure(self):
        runner = EvaluationRunner()
        case = _foundation_case(prompt_name="no.such.template.on.disk")
        outcome = runner.run_case(case)
        self.assertEqual(outcome.outcome, OUTCOME_EVALUATION_FAILURE)

    def test_evaluation_failure_never_raises_out_of_run_case(self):
        # The whole point of the outer try/except -- a bad case must never
        # abort a batch of otherwise-good cases.
        runner = EvaluationRunner()
        try:
            outcome = runner.run_case(_foundation_case(prompt_name="totally.missing"))
        except Exception as exc:  # noqa: BLE001
            self.fail(f"run_case() must never raise, got {exc!r}")
        self.assertEqual(outcome.outcome, OUTCOME_EVALUATION_FAILURE)
