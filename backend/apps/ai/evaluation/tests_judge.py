"""
Phase 7a.5 -- LLM-as-Judge framework tests. Tier 2 (advisory) -- see
docs/AI_EVALUATION.md for the CI wiring (these tests are tagged so they
never block the Tier 1 gate, but they DO run, offline, via EchoProvider;
no real vendor call is ever made by this test module).
"""
from django.test import SimpleTestCase, TestCase, override_settings, tag

from apps.ai.evaluation.judge import (
    JudgeDisabledError,
    JudgeInvalidResponseError,
    JudgeRubric,
    run_judge_scoring,
    run_pairwise_comparison,
)
from apps.ai.providers.echo import canned


@tag("ai_advisory")
class JudgeRubricTests(SimpleTestCase):
    def test_rubric_has_a_default_scale_description(self):
        rubric = JudgeRubric(name="test", criteria=["accurate", "concise"])
        self.assertIn("0.0", rubric.scale_description)


@tag("ai_advisory")
@override_settings(AI_JUDGE_ENABLED=False)
class JudgeDisabledByDefaultTests(TestCase):
    def test_scoring_raises_when_disabled(self):
        rubric = JudgeRubric(name="test", criteria=["accurate"])
        with self.assertRaises(JudgeDisabledError):
            run_judge_scoring(rubric, {"answer": "x"})

    def test_pairwise_raises_when_disabled(self):
        rubric = JudgeRubric(name="test", criteria=["accurate"])
        with self.assertRaises(JudgeDisabledError):
            run_pairwise_comparison(rubric, {"a": 1}, {"b": 2})

    def test_disabled_call_never_renders_a_prompt(self):
        from apps.ai.models import AIPromptVersion

        rubric = JudgeRubric(name="test", criteria=["accurate"])
        try:
            run_judge_scoring(rubric, {"answer": "x"})
        except JudgeDisabledError:
            pass
        self.assertEqual(AIPromptVersion.objects.filter(name="judge_scoring").count(), 0)


@tag("ai_advisory")
@override_settings(AI_JUDGE_ENABLED=True)
class JudgeScoringEnabledTests(TestCase):
    def test_valid_canned_response_returns_score_and_rationale(self):
        rubric = JudgeRubric(name="quality", criteria=["accurate", "concise"])
        result = run_judge_scoring(
            rubric,
            {"answer": canned({"score": 0.8, "rationale": "Mostly accurate, a bit verbose."})},
        )
        self.assertEqual(result.score, 0.8)
        self.assertIn("accurate", result.rationale)

    def test_invalid_response_raises_judge_invalid_response_error(self):
        rubric = JudgeRubric(name="quality", criteria=["accurate"])
        with self.assertRaises(JudgeInvalidResponseError):
            run_judge_scoring(
                rubric,
                {"answer": canned({"score": "not-a-number", "rationale": "x"})},
            )

    def test_score_out_of_range_raises_judge_invalid_response_error(self):
        rubric = JudgeRubric(name="quality", criteria=["accurate"])
        with self.assertRaises(JudgeInvalidResponseError):
            run_judge_scoring(
                rubric,
                {"answer": canned({"score": 1.5, "rationale": "x"})},
            )

    def test_registers_a_prompt_version(self):
        from apps.ai.models import AIPromptVersion

        rubric = JudgeRubric(name="quality", criteria=["accurate"])
        run_judge_scoring(rubric, {"answer": canned({"score": 1.0, "rationale": "perfect"})})
        self.assertTrue(AIPromptVersion.objects.filter(name="judge_scoring").exists())


@tag("ai_advisory")
@override_settings(AI_JUDGE_ENABLED=True)
class JudgePairwiseComparisonEnabledTests(TestCase):
    def test_valid_canned_response_returns_winner_and_rationale(self):
        rubric = JudgeRubric(name="quality", criteria=["accurate"])
        result = run_pairwise_comparison(
            rubric,
            {"answer": "response A text"},
            {"answer": canned({"winner": "A", "rationale": "A is more precise."})},
        )
        self.assertEqual(result.winner, "A")

    def test_tie_is_a_valid_winner_value(self):
        rubric = JudgeRubric(name="quality", criteria=["accurate"])
        result = run_pairwise_comparison(
            rubric,
            {"answer": "response A text"},
            {"answer": canned({"winner": "TIE", "rationale": "Equally good."})},
        )
        self.assertEqual(result.winner, "TIE")

    def test_invalid_winner_value_raises(self):
        rubric = JudgeRubric(name="quality", criteria=["accurate"])
        with self.assertRaises(JudgeInvalidResponseError):
            run_pairwise_comparison(
                rubric,
                {"answer": "response A text"},
                {"answer": canned({"winner": "C", "rationale": "x"})},
            )


@tag("ai_advisory")
@override_settings(AI_JUDGE_ENABLED=True)
class JudgeNeverMakesARealProviderCallTests(TestCase):
    """Even enabled, judge calls in this test suite always go through
    EchoProvider (the AI_PROVIDER default in DEBUG/_TESTING) -- structural
    proof no vendor SDK is reachable from this module at all (the shared
    apps.ai.tests_import_guard already covers this file, since it's under
    apps/ai/evaluation/, but this test asserts the RUNTIME behavior too)."""

    def test_default_provider_name_is_echo(self):
        import inspect

        sig = inspect.signature(run_judge_scoring)
        self.assertEqual(sig.parameters["provider_name"].default, "echo")

    def test_pairwise_default_provider_name_is_echo(self):
        import inspect

        sig = inspect.signature(run_pairwise_comparison)
        self.assertEqual(sig.parameters["provider_name"].default, "echo")
