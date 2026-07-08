"""Phase 7a.5 -- EvaluationService (persistence layer) tests."""
from django.test import TestCase

from apps.ai.evaluation.models import EvaluationResult, EvaluationRun
from apps.ai.evaluation.service import run_tier1_evaluation


class RunTier1EvaluationTests(TestCase):
    def test_runs_all_capabilities_by_default(self):
        run = run_tier1_evaluation(trigger="test")
        self.assertEqual(run.tier, EvaluationRun.Tier.TIER_1_DETERMINISTIC)
        self.assertEqual(run.status, EvaluationRun.Status.COMPLETED)
        self.assertGreater(run.total_cases, 0)

    def test_all_real_golden_cases_pass(self):
        # The whole point of authoring the fixtures correctly -- if this
        # ever fails, either a prompt template or a schema drifted without
        # its golden fixture being updated to match.
        run = run_tier1_evaluation(trigger="test")
        self.assertEqual(run.failed_cases, 0)
        self.assertEqual(run.passed_cases, run.total_cases)

    def test_persists_one_result_row_per_case(self):
        run = run_tier1_evaluation(trigger="test")
        self.assertEqual(EvaluationResult.objects.filter(run=run).count(), run.total_cases)

    def test_single_capability_scoping(self):
        run = run_tier1_evaluation(trigger="test", capability="anomaly_detection")
        self.assertEqual(run.total_cases, 3)
        capabilities = set(EvaluationResult.objects.filter(run=run).values_list("capability", flat=True))
        self.assertEqual(capabilities, {"anomaly_detection"})

    def test_finished_at_is_set_after_completion(self):
        run = run_tier1_evaluation(trigger="test")
        self.assertIsNotNone(run.finished_at)
        self.assertGreaterEqual(run.finished_at, run.started_at)

    def test_trigger_is_recorded(self):
        run = run_tier1_evaluation(trigger="ci")
        self.assertEqual(run.trigger, "ci")
