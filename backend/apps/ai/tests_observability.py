"""
Phase 7g -- apps.ai.services.observability / cache_metrics tests. Pure,
read-only aggregation -- no AI call, no gateway.
"""
from django.core.cache import cache
from django.test import TestCase

from apps.ai.evaluation.models import EvaluationResult, EvaluationRun
from apps.ai.models import AIInteraction, TenantAIPolicy
from apps.ai.services.cache_metrics import get_cache_hit_count, record_cache_hit
from apps.ai.services.observability import platform_ai_summary
from apps.core.models import Organization


def _make_interaction(org, **extra):
    defaults = dict(
        organization=org, capability="anomaly_detection", provider="echo", model_id="echo-1",
        outcome=AIInteraction.Outcome.OK, egress_tier_applied=TenantAIPolicy.EgressTier.REDACTED,
    )
    defaults.update(extra)
    return AIInteraction.objects.create(**defaults)


class CacheMetricsTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_starts_at_zero(self):
        self.assertEqual(get_cache_hit_count(), 0)

    def test_increments_on_each_call(self):
        record_cache_hit()
        record_cache_hit()
        record_cache_hit()
        self.assertEqual(get_cache_hit_count(), 3)


class PlatformAiSummaryTests(TestCase):
    def setUp(self):
        cache.clear()
        self.org = Organization.objects.create(name="Observability Org")

    def test_requests_total_and_by_outcome(self):
        _make_interaction(self.org, outcome=AIInteraction.Outcome.OK)
        _make_interaction(self.org, outcome=AIInteraction.Outcome.SCHEMA_INVALID)
        summary = platform_ai_summary()
        self.assertEqual(summary["requests"]["total"], 2)
        self.assertEqual(summary["requests"]["by_outcome"]["OK"], 1)
        self.assertEqual(summary["requests"]["by_outcome"]["SCHEMA_INVALID"], 1)
        self.assertEqual(summary["requests"]["failed"], 1)

    def test_provider_usage_and_replay_usage(self):
        _make_interaction(self.org, provider="echo")
        _make_interaction(self.org, provider="replay")
        _make_interaction(self.org, provider="replay")
        summary = platform_ai_summary()
        self.assertEqual(summary["provider_usage"]["echo"], 1)
        self.assertEqual(summary["provider_usage"]["replay"], 2)
        self.assertEqual(summary["replay_usage"], 2)

    def test_capability_usage(self):
        _make_interaction(self.org, capability="anomaly_detection")
        _make_interaction(self.org, capability="esg_assistant")
        summary = platform_ai_summary()
        self.assertEqual(summary["capability_usage"]["anomaly_detection"], 1)
        self.assertEqual(summary["capability_usage"]["esg_assistant"], 1)

    def test_tokens_and_cost_summed(self):
        _make_interaction(self.org, input_tokens=100, output_tokens=50, cost_usd="0.001500")
        _make_interaction(self.org, input_tokens=200, output_tokens=100, cost_usd="0.003000")
        summary = platform_ai_summary()
        self.assertEqual(summary["tokens_and_cost"]["input_tokens"], 300)
        self.assertEqual(summary["tokens_and_cost"]["output_tokens"], 150)
        self.assertEqual(summary["tokens_and_cost"]["estimated_cost_usd"], "0.004500")

    def test_latency_average(self):
        _make_interaction(self.org, latency_ms=100)
        _make_interaction(self.org, latency_ms=200)
        summary = platform_ai_summary()
        self.assertEqual(summary["latency"]["avg_ms"], 150.0)

    def test_latency_trend_buckets_by_day(self):
        import datetime

        first = _make_interaction(self.org, latency_ms=100)
        AIInteraction.objects.filter(pk=first.pk).update(
            created_at=datetime.datetime(2026, 1, 1, 10, tzinfo=datetime.timezone.utc)
        )
        second = _make_interaction(self.org, latency_ms=300)
        AIInteraction.objects.filter(pk=second.pk).update(
            created_at=datetime.datetime(2026, 1, 1, 14, tzinfo=datetime.timezone.utc)
        )
        third = _make_interaction(self.org, latency_ms=50)
        AIInteraction.objects.filter(pk=third.pk).update(
            created_at=datetime.datetime(2026, 1, 2, 9, tzinfo=datetime.timezone.utc)
        )

        summary = platform_ai_summary()
        trend = summary["latency"]["trend"]
        self.assertEqual(trend, [
            {"date": "2026-01-01", "avg_ms": 200.0},
            {"date": "2026-01-02", "avg_ms": 50.0},
        ])

    def test_latency_trend_excludes_interactions_without_latency(self):
        _make_interaction(self.org, latency_ms=None)
        summary = platform_ai_summary()
        self.assertEqual(summary["latency"]["trend"], [])

    def test_cache_hits_reflected(self):
        record_cache_hit()
        record_cache_hit()
        summary = platform_ai_summary()
        self.assertEqual(summary["cache_hits"], 2)

    def test_date_range_filters_requests(self):
        import datetime

        old = _make_interaction(self.org)
        AIInteraction.objects.filter(pk=old.pk).update(created_at=datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc))
        _make_interaction(self.org)

        summary = platform_ai_summary({"date_from": "2025-01-01"})
        self.assertEqual(summary["requests"]["total"], 1)

    def test_evaluation_summary_reports_latest_run_per_tier(self):
        run = EvaluationRun.objects.create(
            tier=EvaluationRun.Tier.TIER_1_DETERMINISTIC, status=EvaluationRun.Status.COMPLETED,
            total_cases=5, passed_cases=5, failed_cases=0,
        )
        EvaluationResult.objects.create(
            run=run, capability="anomaly_detection", case_id="c1", prompt_name="anomaly_detection",
            outcome=EvaluationResult.Outcome.OK,
        )
        summary = platform_ai_summary()
        latest = summary["evaluation"]["latest_by_tier"][EvaluationRun.Tier.TIER_1_DETERMINISTIC]
        self.assertIsNotNone(latest)
        self.assertEqual(latest["total_cases"], 5)
        self.assertIsNone(summary["evaluation"]["latest_by_tier"][EvaluationRun.Tier.TIER_2_ADVISORY])

    def test_evaluation_summary_counts_regressions_and_schema_failures(self):
        run = EvaluationRun.objects.create(tier=EvaluationRun.Tier.TIER_1_DETERMINISTIC, total_cases=2)
        EvaluationResult.objects.create(
            run=run, capability="anomaly_detection", case_id="c1", prompt_name="anomaly_detection",
            outcome=EvaluationResult.Outcome.REGRESSION,
        )
        EvaluationResult.objects.create(
            run=run, capability="anomaly_detection", case_id="c2", prompt_name="anomaly_detection",
            outcome=EvaluationResult.Outcome.SCHEMA_INVALID,
        )
        summary = platform_ai_summary()
        self.assertEqual(summary["evaluation"]["regressions"], 1)
        self.assertEqual(summary["evaluation"]["schema_failures"], 1)

    def test_evaluation_summary_counts_replay_failures(self):
        run = EvaluationRun.objects.create(tier=EvaluationRun.Tier.TIER_1_DETERMINISTIC, total_cases=1)
        EvaluationResult.objects.create(
            run=run, capability="anomaly_detection", case_id="c1", prompt_name="anomaly_detection",
            outcome=EvaluationResult.Outcome.PROVIDER_ERROR,
        )
        summary = platform_ai_summary()
        self.assertEqual(summary["evaluation"]["replay_failures"], 1)

    def test_evaluation_summary_reports_recent_runs_oldest_first(self):
        import time

        first = EvaluationRun.objects.create(
            tier=EvaluationRun.Tier.TIER_1_DETERMINISTIC, status=EvaluationRun.Status.COMPLETED,
            total_cases=3, passed_cases=3, failed_cases=0,
        )
        time.sleep(0.01)
        second = EvaluationRun.objects.create(
            tier=EvaluationRun.Tier.TIER_2_ADVISORY, status=EvaluationRun.Status.COMPLETED,
            total_cases=2, passed_cases=1, failed_cases=1,
        )
        summary = platform_ai_summary()
        recent_runs = summary["evaluation"]["recent_runs"]
        self.assertEqual(len(recent_runs), 2)
        self.assertEqual(recent_runs[0]["id"], str(first.id))
        self.assertEqual(recent_runs[1]["id"], str(second.id))
        self.assertEqual(recent_runs[1]["failed_cases"], 1)

    def test_evaluation_summary_recent_runs_capped_at_ten(self):
        for _ in range(12):
            EvaluationRun.objects.create(tier=EvaluationRun.Tier.TIER_1_DETERMINISTIC, total_cases=1)
        summary = platform_ai_summary()
        self.assertEqual(len(summary["evaluation"]["recent_runs"]), 10)

    def test_never_mutates_any_ai_interaction_or_evaluation_row(self):
        interaction = _make_interaction(self.org)
        run = EvaluationRun.objects.create(tier=EvaluationRun.Tier.TIER_1_DETERMINISTIC, total_cases=1)
        before_interaction = AIInteraction.objects.get(pk=interaction.pk).outcome
        before_run_status = EvaluationRun.objects.get(pk=run.pk).status

        platform_ai_summary()

        self.assertEqual(AIInteraction.objects.get(pk=interaction.pk).outcome, before_interaction)
        self.assertEqual(EvaluationRun.objects.get(pk=run.pk).status, before_run_status)
