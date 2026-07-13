"""Phase 7a.5 -- EvaluationRun/EvaluationResult model tests."""
from django.test import TestCase

from apps.ai.evaluation.models import EvaluationResult, EvaluationRun


class EvaluationRunTests(TestCase):
    def test_create_minimal_run(self):
        run = EvaluationRun.objects.create(tier=EvaluationRun.Tier.TIER_1_DETERMINISTIC)
        self.assertIsNotNone(run.id)
        self.assertEqual(run.status, EvaluationRun.Status.RUNNING)
        self.assertEqual(run.trigger, "manual")

    def test_no_organization_field_platform_level_artifact(self):
        # Evaluation runs test capability CONTRACTS against golden fixtures,
        # never real tenant data -- confirmed structurally, not just by
        # convention.
        field_names = {f.name for f in EvaluationRun._meta.fields}
        self.assertNotIn("organization", field_names)


class EvaluationResultTests(TestCase):
    def setUp(self):
        self.run = EvaluationRun.objects.create(tier=EvaluationRun.Tier.TIER_1_DETERMINISTIC)

    def test_create_result_linked_to_run(self):
        result = EvaluationResult.objects.create(
            run=self.run, capability="foundation.selftest", case_id="case-1",
            prompt_name="foundation.selftest", outcome=EvaluationResult.Outcome.OK, score=1.0,
        )
        self.assertEqual(result.run, self.run)
        self.assertIn(result, self.run.results.all())

    def test_no_organization_field_platform_level_artifact(self):
        field_names = {f.name for f in EvaluationResult._meta.fields}
        self.assertNotIn("organization", field_names)

    def test_results_deleted_when_run_deleted(self):
        EvaluationResult.objects.create(
            run=self.run, capability="x", case_id="c1", prompt_name="x",
            outcome=EvaluationResult.Outcome.OK,
        )
        self.run.delete()
        self.assertEqual(EvaluationResult.objects.count(), 0)

    def test_all_four_failure_outcomes_plus_ok_are_distinct(self):
        outcomes = {
            EvaluationResult.Outcome.OK,
            EvaluationResult.Outcome.SCHEMA_INVALID,
            EvaluationResult.Outcome.REGRESSION,
            EvaluationResult.Outcome.PROVIDER_ERROR,
            EvaluationResult.Outcome.EVALUATION_FAILURE,
        }
        self.assertEqual(len(outcomes), 5)
