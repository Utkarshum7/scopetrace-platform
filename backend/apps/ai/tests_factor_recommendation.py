"""
Phase 7c -- apps.ai.services.factor_recommendation tests.

Unlike anomaly_detection's tests (Phase 7b), which could embed a canned()
response inside an unbounded JSONField (validation_errors) that flows
straight into the prompt, every template var this capability's prompt
consumes is derived from a length-bounded DB column (ActivityType.name,
EmissionFactorDataset.version, Region.code, ...) -- none large enough to
hold a canned() marker without truncation risk on a real Postgres
varchar column. Tests that need an exact parsed AI response therefore
mock apps.ai.services.factor_recommendation.invoke_ai directly instead.
Tests that only need a REFUSAL outcome (AI disabled, schema invalid) still
exercise the real EchoProvider end to end, since those outcomes don't
depend on any specific response content.
"""
from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.ai.models import AIFactorRecommendation, AIInteraction, TenantAIPolicy
from apps.ai.services.factor_recommendation import (
    _candidate_factors,
    _format_candidates,
    recommend_emission_factor,
)
from apps.ai.services.gateway import AIGatewayResult
from apps.carbon.models import EmissionCalculation
from apps.carbon.tests.factories import activity_type, dataset, factor
from apps.core.models import DataSource, Organization
from apps.ingestion.models import EmissionRecord, UploadBatch


def _make_batch(org, ds):
    return UploadBatch.objects.create(organization=org, data_source=ds, file_name="factor_rec_svc_test.csv")


def _make_record(org, batch, **extra):
    defaults = dict(
        organization=org, batch=batch, row_index=1, raw_data_payload={"a": 1},
        status=EmissionRecord.RecordStatus.DRAFT, normalized_value=500,
        normalized_unit="L", scope_category="SCOPE_1",
    )
    defaults.update(extra)
    return EmissionRecord.objects.create(**defaults)


def _make_calculation(org, record, **extra):
    defaults = dict(
        organization=org, emission_record=record, is_current=True,
        resolution_status=EmissionCalculation.ResolutionStatus.UNRESOLVED_NO_FACTOR,
        scope="SCOPE_1", activity_quantity=500, activity_unit="L",
    )
    defaults.update(extra)
    return EmissionCalculation.objects.create(**defaults)


def _make_interaction(org):
    return AIInteraction.objects.create(
        organization=org, capability="factor_recommendation", provider="echo", model_id="echo-1",
        outcome=AIInteraction.Outcome.OK, egress_tier_applied=TenantAIPolicy.EgressTier.REDACTED,
        context_provenance=[],
    )


class CandidateFactorsTests(TestCase):
    def setUp(self):
        self.activity_type = activity_type()
        self.dataset = dataset()

    def test_returns_active_dataset_factors_for_the_activity_type(self):
        f = factor(self.dataset, self.activity_type)
        candidates = _candidate_factors(self.activity_type)
        self.assertIn(f, candidates)

    def test_excludes_factors_for_a_different_activity_type(self):
        other_type = activity_type(code="OTHER_TYPE")
        factor(self.dataset, other_type)
        candidates = _candidate_factors(self.activity_type)
        self.assertEqual(candidates, [])

    def test_respects_limit(self):
        for _ in range(3):
            factor(self.dataset, self.activity_type)
        candidates = _candidate_factors(self.activity_type, limit=2)
        self.assertEqual(len(candidates), 2)


class FormatCandidatesTests(TestCase):
    def test_empty_list_returns_placeholder(self):
        result = _format_candidates([])
        self.assertIn("no candidate factors", result)

    def test_labels_candidates_sequentially(self):
        activity = activity_type()
        ds = dataset()
        f1 = factor(ds, activity)
        f2 = factor(ds, activity)
        result = _format_candidates([f1, f2])
        self.assertIn("candidate_1:", result)
        self.assertIn("candidate_2:", result)


@override_settings(AI_ENABLED=True, AI_PROVIDER="echo", AI_DEFAULT_MODEL="echo-1")
class RecommendEmissionFactorHappyPathTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Factor Rec Service Org")
        TenantAIPolicy.objects.create(organization=self.org, ai_enabled=True, provider_override="echo")
        self.ds_source = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL,
        )
        self.batch = _make_batch(self.org, self.ds_source)
        self.activity_type = activity_type()
        self.dataset = dataset()
        self.factor1 = factor(self.dataset, self.activity_type)
        self.factor2 = factor(self.dataset, self.activity_type)

    def _invoke_ai_returning(self, parsed):
        interaction = _make_interaction(self.org)
        return AIGatewayResult(outcome=AIInteraction.Outcome.OK, interaction_id=str(interaction.id), parsed=parsed)

    def test_creates_a_recommendation_and_resolves_the_chosen_label(self):
        record = _make_record(self.org, self.batch)
        _make_calculation(self.org, record, activity_type=self.activity_type)
        parsed = {
            "recommended_candidate_label": "candidate_1",
            "confidence": "HIGH",
            "explanation": "Best regional and date match.",
            "reasoning": "candidate_1's window matches the reporting date.",
            "alternative_candidates": ["candidate_2"],
        }
        with patch(
            "apps.ai.services.factor_recommendation.invoke_ai",
            return_value=self._invoke_ai_returning(parsed),
        ) as mocked:
            rec = recommend_emission_factor(record)
        self.assertIsNotNone(rec)
        self.assertEqual(rec.recommended_factor, self.factor1)
        self.assertEqual(rec.record, record)
        self.assertEqual(rec.confidence, "HIGH")
        self.assertEqual(rec.alternative_candidates, ["candidate_2"])
        mocked.assert_called_once()

    def test_ai_recommending_none_creates_a_recommendation_with_no_factor(self):
        record = _make_record(self.org, self.batch)
        _make_calculation(self.org, record, activity_type=self.activity_type)
        parsed = {
            "recommended_candidate_label": "none",
            "confidence": "LOW",
            "explanation": "Neither candidate fits.",
            "reasoning": "Both candidates are regionally mismatched.",
            "alternative_candidates": [],
        }
        with patch(
            "apps.ai.services.factor_recommendation.invoke_ai",
            return_value=self._invoke_ai_returning(parsed),
        ):
            rec = recommend_emission_factor(record)
        self.assertIsNotNone(rec)
        self.assertIsNone(rec.recommended_factor)

    def test_unrecognized_label_resolves_to_no_factor(self):
        # Defensive: an AI response with a schema-valid but nonexistent
        # label (e.g. stale candidate set) never crashes -- resolves to
        # None rather than raising a KeyError.
        record = _make_record(self.org, self.batch)
        _make_calculation(self.org, record, activity_type=self.activity_type)
        parsed = {
            "recommended_candidate_label": "candidate_99",
            "confidence": "LOW",
            "explanation": "x",
            "reasoning": "x",
            "alternative_candidates": [],
        }
        with patch(
            "apps.ai.services.factor_recommendation.invoke_ai",
            return_value=self._invoke_ai_returning(parsed),
        ):
            rec = recommend_emission_factor(record)
        self.assertIsNotNone(rec)
        self.assertIsNone(rec.recommended_factor)

    def test_links_back_to_the_ai_interaction(self):
        record = _make_record(self.org, self.batch)
        _make_calculation(self.org, record, activity_type=self.activity_type)
        parsed = {
            "recommended_candidate_label": "candidate_1", "confidence": "HIGH",
            "explanation": "x", "reasoning": "x", "alternative_candidates": [],
        }
        with patch(
            "apps.ai.services.factor_recommendation.invoke_ai",
            return_value=self._invoke_ai_returning(parsed),
        ):
            rec = recommend_emission_factor(record)
        self.assertIsNotNone(rec.interaction)
        self.assertEqual(rec.interaction.capability, "factor_recommendation")
        self.assertEqual(rec.interaction.outcome, "OK")

    def test_never_mutates_the_calculation_or_factor(self):
        record = _make_record(self.org, self.batch)
        calc = _make_calculation(self.org, record, activity_type=self.activity_type)
        original_status = calc.resolution_status
        original_factor_id = calc.emission_factor_id
        original_co2e = self.factor1.co2e_per_unit
        parsed = {
            "recommended_candidate_label": "candidate_1", "confidence": "HIGH",
            "explanation": "x", "reasoning": "x", "alternative_candidates": [],
        }
        with patch(
            "apps.ai.services.factor_recommendation.invoke_ai",
            return_value=self._invoke_ai_returning(parsed),
        ):
            recommend_emission_factor(record)
        calc.refresh_from_db()
        self.factor1.refresh_from_db()
        self.assertEqual(calc.resolution_status, original_status)
        self.assertEqual(calc.emission_factor_id, original_factor_id)
        self.assertEqual(self.factor1.co2e_per_unit, original_co2e)


@override_settings(AI_ENABLED=True, AI_PROVIDER="echo", AI_DEFAULT_MODEL="echo-1")
class RecommendEmissionFactorScopingTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Factor Rec Scoping Org")
        TenantAIPolicy.objects.create(organization=self.org, ai_enabled=True, provider_override="echo")
        self.ds_source = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL,
        )
        self.batch = _make_batch(self.org, self.ds_source)
        self.activity_type = activity_type()
        self.dataset = dataset()
        self.factor1 = factor(self.dataset, self.activity_type)

    def test_no_current_calculation_returns_none(self):
        record = _make_record(self.org, self.batch)
        self.assertIsNone(recommend_emission_factor(record))
        self.assertEqual(AIFactorRecommendation.objects.count(), 0)

    def test_calculated_status_returns_none(self):
        record = _make_record(self.org, self.batch)
        _make_calculation(
            self.org, record, activity_type=self.activity_type,
            resolution_status=EmissionCalculation.ResolutionStatus.CALCULATED,
        )
        self.assertIsNone(recommend_emission_factor(record))
        self.assertEqual(AIFactorRecommendation.objects.count(), 0)

    def test_unresolved_no_activity_type_returns_none(self):
        # Out of scope by design -- a different problem (activity-type
        # mapping), not factor selection. See ADR 0010.
        record = _make_record(self.org, self.batch)
        _make_calculation(
            self.org, record, activity_type=None,
            resolution_status=EmissionCalculation.ResolutionStatus.UNRESOLVED_NO_ACTIVITY_TYPE,
        )
        self.assertIsNone(recommend_emission_factor(record))
        self.assertEqual(AIFactorRecommendation.objects.count(), 0)

    def test_non_current_calculation_is_ignored(self):
        record = _make_record(self.org, self.batch)
        _make_calculation(
            self.org, record, activity_type=self.activity_type, is_current=False,
        )
        self.assertIsNone(recommend_emission_factor(record))
        self.assertEqual(AIFactorRecommendation.objects.count(), 0)


@override_settings(AI_ENABLED=True, AI_PROVIDER="echo", AI_DEFAULT_MODEL="echo-1")
class RecommendEmissionFactorRefusalTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Factor Rec Refusal Org")
        self.ds_source = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL,
        )
        self.batch = _make_batch(self.org, self.ds_source)
        self.activity_type = activity_type()
        self.dataset = dataset()
        self.factor1 = factor(self.dataset, self.activity_type)

    def test_ai_disabled_returns_none_and_creates_no_recommendation(self):
        # No TenantAIPolicy row -- AI disabled for this org.
        record = _make_record(self.org, self.batch)
        _make_calculation(self.org, record, activity_type=self.activity_type)
        rec = recommend_emission_factor(record)
        self.assertIsNone(rec)
        self.assertEqual(AIFactorRecommendation.objects.count(), 0)

    def test_schema_invalid_response_returns_none_and_creates_no_recommendation(self):
        TenantAIPolicy.objects.create(organization=self.org, ai_enabled=True, provider_override="echo")
        # EchoProvider's default (non-canned) response never matches the
        # factor_recommendation schema.
        record = _make_record(self.org, self.batch)
        _make_calculation(self.org, record, activity_type=self.activity_type)
        rec = recommend_emission_factor(record)
        self.assertIsNone(rec)
        self.assertEqual(AIFactorRecommendation.objects.count(), 0)
        self.assertTrue(
            AIInteraction.objects.filter(capability="factor_recommendation", outcome="SCHEMA_INVALID").exists()
        )
