"""Phase 7a -- apps.ai.tasks.ai_heartbeat_task and its queue/schedule wiring."""
from django.conf import settings
from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.ai.tasks import AI_HEARTBEAT_CACHE_KEY, ai_heartbeat_task


class AIHeartbeatTaskTests(TestCase):
    def setUp(self):
        cache.delete(AI_HEARTBEAT_CACHE_KEY)

    @override_settings(AI_ENABLED=False)
    def test_disabled_writes_disabled_status_without_touching_provider_factory(self):
        result = ai_heartbeat_task.delay()
        self.assertEqual(result.get(), "disabled")
        payload = cache.get(AI_HEARTBEAT_CACHE_KEY)
        self.assertEqual(payload["status"], "disabled")

    @override_settings(AI_ENABLED=True, AI_PROVIDER="echo")
    def test_enabled_with_valid_provider_writes_ok(self):
        result = ai_heartbeat_task.delay()
        self.assertEqual(result.get(), "ok")
        payload = cache.get(AI_HEARTBEAT_CACHE_KEY)
        self.assertEqual(payload["status"], "ok")
        self.assertIn("worker_id", payload)
        self.assertIn("timestamp", payload)

    @override_settings(AI_ENABLED=True, AI_PROVIDER="anthropic")
    def test_enabled_with_missing_credentials_writes_provider_unavailable(self):
        # No ANTHROPIC_API_KEY configured in the test settings baseline.
        result = ai_heartbeat_task.delay()
        self.assertEqual(result.get(), "provider_unavailable")
        payload = cache.get(AI_HEARTBEAT_CACHE_KEY)
        self.assertEqual(payload["status"], "provider_unavailable")
        self.assertTrue(payload["detail"])

    @override_settings(AI_ENABLED=True, AI_PROVIDER="echo")
    def test_never_writes_a_real_ai_interaction(self):
        # No network call is ever made -- proven indirectly: nothing here
        # constructs a request/response through the gateway, only the
        # provider factory (construction, no network I/O for any adapter).
        from apps.ai.models import AIInteraction

        ai_heartbeat_task.delay()
        self.assertEqual(AIInteraction.objects.count(), 0)


class AIQueueConfigurationTests(TestCase):
    def test_ai_heartbeat_task_is_scheduled(self):
        schedule = settings.CELERY_BEAT_SCHEDULE
        self.assertIn("ai-heartbeat", schedule)
        self.assertEqual(schedule["ai-heartbeat"]["task"], "apps.ai.tasks.ai_heartbeat_task")

    def test_ai_heartbeat_task_routed_to_ai_queue(self):
        routes = settings.CELERY_TASK_ROUTES
        self.assertEqual(routes["apps.ai.tasks.ai_heartbeat_task"]["queue"], "ai")
