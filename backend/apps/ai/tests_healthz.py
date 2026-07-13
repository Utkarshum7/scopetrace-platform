"""
Phase 7a -- /healthz/ai/ tests. The view itself lives in apps.core.views
(apps.core owns cross-cutting health/infra concerns -- see that module's
existing healthz/healthz_worker precedent), but is entirely about apps.ai's
own feature, so its tests live here rather than in apps/core/tests.py --
keeps every Phase 7a test file self-contained under apps/ai/.
"""
from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.ai.tasks import AI_HEARTBEAT_CACHE_KEY


class HealthzAITests(TestCase):
    def setUp(self):
        cache.delete(AI_HEARTBEAT_CACHE_KEY)

    @override_settings(AI_ENABLED=False)
    def test_disabled_returns_200_not_503(self):
        # A deliberately-disabled feature is expected, healthy state -- not
        # a health-check failure that should page anyone.
        response = self.client.get("/healthz/ai/")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertFalse(body["ai_enabled"])

    @override_settings(AI_ENABLED=True, AI_PROVIDER="echo")
    def test_enabled_with_valid_provider_returns_200(self):
        response = self.client.get("/healthz/ai/")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["ai_enabled"])
        self.assertEqual(body["provider"], "echo")

    @override_settings(AI_ENABLED=True, AI_PROVIDER="anthropic", ANTHROPIC_API_KEY="")
    def test_enabled_with_misconfigured_provider_returns_503(self):
        response = self.client.get("/healthz/ai/")
        self.assertEqual(response.status_code, 503)
        body = response.json()
        self.assertEqual(body["status"], "unhealthy")
        self.assertIn("provider misconfigured", body["detail"])

    @override_settings(AI_ENABLED=True, AI_PROVIDER="echo")
    def test_never_makes_a_real_provider_call(self):
        # Structural proof: hitting this endpoint must never write an
        # AIInteraction row -- if it did, that would mean a real (billable)
        # gateway call happened on every health-check poll.
        from apps.ai.models import AIInteraction

        self.client.get("/healthz/ai/")
        self.assertEqual(AIInteraction.objects.count(), 0)

    @override_settings(AI_ENABLED=False)
    def test_disabled_check_does_not_require_authentication(self):
        # Matches /healthz and /healthz/worker's precedent -- an
        # orchestrator health probe must never need a JWT.
        response = self.client.get("/healthz/ai/")
        self.assertEqual(response.status_code, 200)

    @override_settings(AI_ENABLED=True, AI_PROVIDER="echo")
    def test_reports_stale_ai_heartbeat_when_task_never_ran(self):
        response = self.client.get("/healthz/ai/")
        self.assertEqual(response.json()["ai_heartbeat"], {"status": "stale"})

    @override_settings(AI_ENABLED=True, AI_PROVIDER="echo")
    def test_reports_fresh_ai_heartbeat_after_task_runs(self):
        from apps.ai.tasks import ai_heartbeat_task

        ai_heartbeat_task.delay()
        response = self.client.get("/healthz/ai/")
        heartbeat = response.json()["ai_heartbeat"]
        self.assertEqual(heartbeat["status"], "ok")
        self.assertLess(heartbeat["age_seconds"], 5)

    @override_settings(AI_ENABLED=True, AI_PROVIDER="echo")
    def test_ai_heartbeat_reported_even_on_provider_misconfigured_path(self):
        # Additive context, independent of the authoritative pass/fail
        # check -- mirrors _beat_heartbeat's own "still appears on the
        # earliest-return failure path" precedent.
        with override_settings(AI_PROVIDER="anthropic", ANTHROPIC_API_KEY=""):
            response = self.client.get("/healthz/ai/")
        self.assertEqual(response.status_code, 503)
        self.assertIn("ai_heartbeat", response.json())
