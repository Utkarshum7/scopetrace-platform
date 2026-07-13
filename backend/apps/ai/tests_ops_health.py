"""
Phase 7g -- apps.ai.services.ops_health tests. Broker-dependent checks
are mocked the same way apps.core.tests's healthz_worker tests mock
Celery's control-plane inspect() -- no real Redis needed.
"""
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from apps.ai.evaluation.models import EvaluationRun
from apps.ai.providers.replay import fixture_stats
from apps.ai.services.ops_health import (
    ai_heartbeat_status,
    ai_ops_health,
    ai_provider_status,
    ai_queue_depth,
    replay_provider_health,
)


class AiHeartbeatStatusTests(TestCase):
    def test_stale_when_task_never_ran(self):
        from django.core.cache import cache

        from apps.ai.tasks import AI_HEARTBEAT_CACHE_KEY

        cache.delete(AI_HEARTBEAT_CACHE_KEY)
        self.assertEqual(ai_heartbeat_status(), {"status": "stale"})

    @override_settings(AI_ENABLED=True, AI_PROVIDER="echo")
    def test_ok_after_heartbeat_task_runs(self):
        from apps.ai.tasks import ai_heartbeat_task

        ai_heartbeat_task.delay()
        status = ai_heartbeat_status()
        self.assertEqual(status["status"], "ok")
        self.assertLess(status["age_seconds"], 5)

    @override_settings(AI_ENABLED=True, AI_PROVIDER="echo")
    def test_matches_healthz_ai_response_shape(self):
        # Regression guard for the Phase 7g refactor -- apps.core.views'
        # /healthz/ai must still return byte-identical ai_heartbeat shape.
        response = self.client.get("/healthz/ai/")
        self.assertEqual(response.json()["ai_heartbeat"], ai_heartbeat_status())


class AiProviderStatusTests(TestCase):
    @override_settings(AI_ENABLED=False)
    def test_disabled(self):
        self.assertEqual(ai_provider_status(), {"status": "disabled", "ai_enabled": False})

    @override_settings(AI_ENABLED=True, AI_PROVIDER="echo")
    def test_ok_with_valid_provider(self):
        status = ai_provider_status()
        self.assertEqual(status["status"], "ok")
        self.assertEqual(status["provider"], "echo")

    @override_settings(AI_ENABLED=True, AI_PROVIDER="anthropic", ANTHROPIC_API_KEY="")
    def test_unhealthy_with_misconfigured_provider(self):
        status = ai_provider_status()
        self.assertEqual(status["status"], "unhealthy")
        self.assertIn("provider misconfigured", status["detail"])


class AiQueueDepthTests(TestCase):
    @override_settings(CELERY_BROKER_URL="")
    def test_unknown_when_broker_not_configured(self):
        self.assertEqual(ai_queue_depth()["status"], "unknown")

    @override_settings(CELERY_BROKER_URL="redis://localhost:6379/0")
    def test_ok_with_depth_when_broker_reachable(self):
        mock_client = MagicMock()
        mock_client.llen.return_value = 7
        with patch("redis.from_url", return_value=mock_client):
            result = ai_queue_depth("ai")
        self.assertEqual(result, {"status": "ok", "queue": "ai", "depth": 7})
        mock_client.llen.assert_called_once_with("ai")

    @override_settings(CELERY_BROKER_URL="redis://localhost:6379/0")
    def test_unknown_when_broker_unreachable(self):
        with patch("redis.from_url", side_effect=ConnectionError("nope")):
            result = ai_queue_depth()
        self.assertEqual(result["status"], "unknown")
        self.assertIn("broker unreachable", result["detail"])


class ReplayProviderHealthTests(TestCase):
    def test_ok_and_matches_fixture_stats(self):
        result = replay_provider_health()
        self.assertEqual(result["status"], "ok")
        stats = fixture_stats()
        self.assertEqual(result["fixtures_dir_exists"], stats["fixtures_dir_exists"])
        self.assertEqual(result["fixture_count"], stats["fixture_count"])
        self.assertTrue(result["fixtures_dir_exists"])
        self.assertGreater(result["fixture_count"], 0)


class AiOpsHealthTests(TestCase):
    @override_settings(AI_ENABLED=True, AI_PROVIDER="echo")
    def test_aggregates_all_signals(self):
        run = EvaluationRun.objects.create(tier=EvaluationRun.Tier.TIER_1_DETERMINISTIC, total_cases=1)
        result = ai_ops_health()
        self.assertEqual(result["ai_provider"]["status"], "ok")
        self.assertIn("status", result["ai_heartbeat"])
        self.assertIn("status", result["queue_depth"])
        self.assertIn(str(run.id), str(result["evaluation"]["latest_by_tier"]))
        self.assertEqual(result["replay_provider"]["status"], "ok")
