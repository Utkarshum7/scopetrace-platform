"""
D4 (Demo Mode) tests — the mode-derivation matrix and the demo-aware health
endpoint. The full upload -> ingest -> calculate pipeline running synchronously
under Demo Mode is proven in apps/ingestion/tests_demo_mode.py.
"""
from django.test import SimpleTestCase, TestCase, override_settings

from apps.core.execution import resolve_celery_execution


class ResolveCeleryExecutionTests(SimpleTestCase):
    """The pure (debug, testing, demo_mode) -> (always_eager, eager_propagates)
    matrix. Proves production (demo_mode=False) is byte-for-byte the pre-D4
    behavior, and Demo Mode forces eager execution WITHOUT propagating task
    exceptions to the caller (mirroring production's fire-and-forget .delay())."""

    def test_production_default_is_byte_for_byte_unchanged(self):
        # DEMO_MODE=False, not DEBUG, not testing -> the exact pre-D4 production
        # defaults: async (NOT eager) and propagate=True.
        self.assertEqual(
            resolve_celery_execution(debug=False, testing=False, demo_mode=False),
            (False, True),
        )

    def test_local_debug_unchanged(self):
        self.assertEqual(
            resolve_celery_execution(debug=True, testing=False, demo_mode=False),
            (True, True),
        )

    def test_test_runner_unchanged(self):
        self.assertEqual(
            resolve_celery_execution(debug=False, testing=True, demo_mode=False),
            (True, True),
        )

    def test_demo_mode_forces_eager_without_propagation(self):
        # The core D4 contract: demo runs eagerly (no worker/broker needed) and
        # does NOT re-raise task failures into the HTTP caller.
        self.assertEqual(
            resolve_celery_execution(debug=False, testing=False, demo_mode=True),
            (True, False),
        )

    def test_demo_mode_wins_regardless_of_debug_or_testing(self):
        for debug in (False, True):
            for testing in (False, True):
                self.assertEqual(
                    resolve_celery_execution(debug=debug, testing=testing, demo_mode=True),
                    (True, False),
                    msg=f"demo debug={debug} testing={testing}",
                )


class HealthzWorkerDemoModeTests(TestCase):
    """/healthz/worker/ must report Demo Mode as healthy (no worker is expected)
    rather than a false 503, while leaving the production path untouched."""

    @override_settings(DEMO_MODE=True)
    def test_demo_mode_reports_ok_without_a_worker(self):
        resp = self.client.get("/healthz/worker/")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["mode"], "demo")
        self.assertTrue(body["demo_mode"])
        # 200 without any worker running proves it did NOT run inspect().ping()
        # (which would have returned 503 "no workers responded").

    @override_settings(DEMO_MODE=False)
    def test_production_mode_is_not_short_circuited(self):
        # No broker/worker exists in the test environment, so the unchanged
        # production path reports unhealthy. The point is it does NOT return the
        # demo response -- the demo branch never leaks into production mode.
        resp = self.client.get("/healthz/worker/")
        self.assertNotEqual(resp.status_code, 200)
        self.assertNotIn("mode", resp.json())


class HealthzAiDemoModeTests(TestCase):
    """/healthz/ai/ does not false-fail in Demo Mode (AI is opt-in and off by
    default), so it needs no demo-specific code -- verified here."""

    @override_settings(DEMO_MODE=True)
    def test_ai_health_is_ok_in_demo_when_ai_disabled(self):
        resp = self.client.get("/healthz/ai/")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["ai_enabled"])
