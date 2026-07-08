"""
Phase 7c -- apps.ai.tasks.generate_factor_recommendations_task tests, plus
confirming calculate_task dispatches it on a successful run (mirrors
apps.ai.tests_anomaly_task's exact style: CELERY_TASK_ALWAYS_EAGER is
forced True under the test runner, so .delay(...) executes inline/
synchronously here -- no mocking of Celery dispatch needed).

Unlike tests_anomaly_task.py, this task's per-record work
(recommend_emission_factor) can't be driven to an exact response via
canned() -- see tests_factor_recommendation.py's module docstring for why
-- so the task-level tests here mock
apps.ai.services.factor_recommendation.recommend_emission_factor directly
to control task-level behavior (querying, exclusion, one-bad-record
handling), while the dispatch test exercises the real (AI-disabled-by-
default) path end to end.
"""
from datetime import date
from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.ai.models import AIFactorRecommendation, AIInteraction, TenantAIPolicy
from apps.ai.tasks import generate_factor_recommendations_task
from apps.carbon.models import EmissionCalculation
from apps.carbon.tasks import calculate_task
from apps.carbon.tests.factories import activity_type, dataset, factor
from apps.core.models import DataSource, Organization
from apps.core.storage import get_storage_service
from apps.ingestion.models import EmissionRecord, UploadBatch
from apps.ingestion.tasks import ingest_task


def _make_batch(org, ds):
    return UploadBatch.objects.create(organization=org, data_source=ds, file_name="factor_rec_task_test.csv")


def _make_interaction(org):
    return AIInteraction.objects.create(
        organization=org, capability="factor_recommendation", provider="echo", model_id="echo-1",
        outcome=AIInteraction.Outcome.OK, egress_tier_applied=TenantAIPolicy.EgressTier.REDACTED,
    )


@override_settings(AI_ENABLED=True, AI_PROVIDER="echo", AI_DEFAULT_MODEL="echo-1")
class GenerateFactorRecommendationsTaskTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Factor Rec Task Org")
        TenantAIPolicy.objects.create(organization=self.org, ai_enabled=True, provider_override="echo")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL,
        )
        self.batch = _make_batch(self.org, self.ds)
        self.activity_type = activity_type()
        self.dataset = dataset()
        self.factor1 = factor(self.dataset, self.activity_type)

    def _make_record(self, row_index=1, **extra):
        defaults = dict(
            organization=self.org, batch=self.batch, row_index=row_index, raw_data_payload={"a": row_index},
            status=EmissionRecord.RecordStatus.DRAFT, normalized_value=500,
            normalized_unit="L", scope_category="SCOPE_1",
        )
        defaults.update(extra)
        return EmissionRecord.objects.create(**defaults)

    def _make_calculation(self, record, **extra):
        defaults = dict(
            organization=self.org, emission_record=record, is_current=True,
            resolution_status=EmissionCalculation.ResolutionStatus.UNRESOLVED_NO_FACTOR,
            activity_type=self.activity_type, scope="SCOPE_1",
            activity_quantity=500, activity_unit="L",
        )
        defaults.update(extra)
        return EmissionCalculation.objects.create(**defaults)

    def test_generates_recommendations_only_for_unresolved_no_factor_records(self):
        unresolved = self._make_record(row_index=1)
        self._make_calculation(unresolved)
        resolved = self._make_record(row_index=2)
        self._make_calculation(resolved, resolution_status=EmissionCalculation.ResolutionStatus.CALCULATED)

        with patch(
            "apps.ai.services.factor_recommendation.recommend_emission_factor",
            return_value=object(),
        ) as mocked:
            generate_factor_recommendations_task(batch_id=str(self.batch.id))

        called_records = {call.args[0] for call in mocked.call_args_list}
        self.assertIn(unresolved, called_records)
        self.assertNotIn(resolved, called_records)

    def test_idempotent_skips_records_that_already_have_a_recommendation(self):
        record = self._make_record()
        self._make_calculation(record)
        AIFactorRecommendation.objects.create(
            organization=self.org, record=record, interaction=_make_interaction(self.org),
            confidence=AIFactorRecommendation.Confidence.MEDIUM, explanation="x", reasoning="x",
        )

        with patch(
            "apps.ai.services.factor_recommendation.recommend_emission_factor",
            return_value=object(),
        ) as mocked:
            result = generate_factor_recommendations_task(batch_id=str(self.batch.id))

        mocked.assert_not_called()
        self.assertEqual(result, "generated=0 errored=0")

    def test_one_bad_record_does_not_abort_the_rest_of_the_batch(self):
        bad = self._make_record(row_index=1)
        self._make_calculation(bad)
        good = self._make_record(row_index=2)
        self._make_calculation(good)

        def side_effect(record, *args, **kwargs):
            if record.id == bad.id:
                raise ValueError("boom")
            return object()

        with patch(
            "apps.ai.services.factor_recommendation.recommend_emission_factor",
            side_effect=side_effect,
        ):
            result = generate_factor_recommendations_task(batch_id=str(self.batch.id))

        self.assertIn("generated=1", result)
        self.assertIn("errored=1", result)

    def test_empty_batch_is_a_no_op(self):
        result = generate_factor_recommendations_task(batch_id=str(self.batch.id))
        self.assertEqual(result, "generated=0 errored=0")

    def test_routed_to_the_ai_queue(self):
        from django.conf import settings

        routes = settings.CELERY_TASK_ROUTES
        self.assertEqual(routes["apps.ai.tasks.generate_factor_recommendations_task"]["queue"], "ai")


def _sap_csv_bytes():
    today = date.today().strftime("%d.%m.%Y")
    return (
        "Werk;Buchungsdatum;Material;Materialkurztext;Menge;Einheit;Nettopreis\n"
        f"DE01;{today};DSL;Diesel;500,00;L;750.00\n"
    ).encode("utf-8")


class CalculateTaskDispatchesFactorRecommendationsTests(TestCase):
    """Mirrors apps.ai.tests_anomaly_task's IngestTaskDispatchesAnomalyExplanationsTests."""

    def setUp(self):
        self.org = Organization.objects.create(name="Calc Dispatch Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL,
        )

    def test_successful_calculation_dispatches_the_factor_recommendation_task_without_error(self):
        # AI stays disabled (no TenantAIPolicy, default settings) -- this
        # just proves the dispatch itself never breaks or delays a normal
        # calculate_task run, and calculate_task's own return value/
        # behavior is unaffected by the new dispatch.
        batch = UploadBatch.objects.create(
            organization=self.org, data_source=self.ds, file_name="sap.csv",
            status=UploadBatch.BatchStatus.PENDING,
        )
        key = f"uploads/{self.org.id}/{batch.id}/sap.csv"
        get_storage_service().save(key, _sap_csv_bytes(), content_type="text/csv")
        ingest_task(str(batch.id), key, workflow_id="wf-factor-rec-dispatch")

        result = calculate_task(batch_id=str(batch.id), workflow_id="wf-factor-rec-dispatch-calc")

        self.assertEqual(result, "completed")
        # No AIInteraction/AIFactorRecommendation at all -- AI is disabled
        # by default.
        self.assertEqual(AIFactorRecommendation.objects.count(), 0)
