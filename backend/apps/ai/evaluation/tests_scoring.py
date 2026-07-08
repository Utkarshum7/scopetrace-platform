"""Phase 7a.5 -- deterministic scoring function tests. Direct, targeted
input/output cases -- deliberately independent of EvaluationRunner's own
tests, which only exercise the trivial always-matches replay path."""
from django.test import SimpleTestCase

from apps.ai.evaluation.scoring import (
    score_exact_match,
    score_field_overlap,
    score_required_fields_present,
)


class ScoreExactMatchTests(SimpleTestCase):
    def test_identical_dicts_score_1(self):
        self.assertEqual(score_exact_match({"a": 1, "b": 2}, {"a": 1, "b": 2}), 1.0)

    def test_different_dicts_score_0(self):
        self.assertEqual(score_exact_match({"a": 1}, {"a": 2}), 0.0)

    def test_extra_key_scores_0(self):
        self.assertEqual(score_exact_match({"a": 1, "b": 2}, {"a": 1}), 0.0)

    def test_missing_key_scores_0(self):
        self.assertEqual(score_exact_match({"a": 1}, {"a": 1, "b": 2}), 0.0)

    def test_key_order_does_not_matter(self):
        self.assertEqual(score_exact_match({"b": 2, "a": 1}, {"a": 1, "b": 2}), 1.0)


class ScoreFieldOverlapTests(SimpleTestCase):
    def test_all_fields_match_scores_1(self):
        self.assertEqual(score_field_overlap({"a": 1, "b": 2}, {"a": 1, "b": 2}), 1.0)

    def test_no_fields_match_scores_0(self):
        self.assertEqual(score_field_overlap({"a": 9, "b": 9}, {"a": 1, "b": 2}), 0.0)

    def test_half_fields_match_scores_half(self):
        self.assertEqual(score_field_overlap({"a": 1, "b": 9}, {"a": 1, "b": 2}), 0.5)

    def test_extra_actual_fields_not_in_expected_are_ignored(self):
        self.assertEqual(score_field_overlap({"a": 1, "extra": "whatever"}, {"a": 1}), 1.0)

    def test_missing_expected_key_in_actual_does_not_match(self):
        self.assertEqual(score_field_overlap({}, {"a": 1}), 0.0)

    def test_empty_expected_scores_1_not_zero_division(self):
        self.assertEqual(score_field_overlap({"a": 1}, {}), 1.0)


class ScoreRequiredFieldsPresentTests(SimpleTestCase):
    def test_all_present_scores_1(self):
        self.assertEqual(score_required_fields_present({"a": 1, "b": 2}, ["a", "b"]), 1.0)

    def test_none_present_scores_0(self):
        self.assertEqual(score_required_fields_present({}, ["a", "b"]), 0.0)

    def test_partial_presence_scores_fraction(self):
        self.assertEqual(score_required_fields_present({"a": 1}, ["a", "b"]), 0.5)

    def test_none_value_counts_as_not_present(self):
        self.assertEqual(score_required_fields_present({"a": None}, ["a"]), 0.0)

    def test_empty_required_fields_scores_1_not_zero_division(self):
        self.assertEqual(score_required_fields_present({"a": 1}, []), 1.0)
