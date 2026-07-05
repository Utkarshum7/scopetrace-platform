from unittest.mock import patch, MagicMock

from django.test import TestCase, override_settings

from apps.core.tasks import ping


class CeleryFoundationTests(TestCase):
    """Phase 5a — Celery app wiring, eager-mode-under-test, worker health probe."""

    def test_ping_task_executes_eagerly_under_test(self):
        # CELERY_TASK_ALWAYS_EAGER is forced True under the test runner (see
        # settings.py `_TESTING` gate) — no broker/worker required.
        result = ping.delay()
        self.assertTrue(result.successful())
        self.assertEqual(result.get(), "pong")

    @override_settings(CELERY_BROKER_URL="")
    def test_healthz_worker_unhealthy_when_broker_not_configured(self):
        response = self.client.get("/healthz/worker/")
        self.assertEqual(response.status_code, 503)
        body = response.json()
        self.assertEqual(body["status"], "unhealthy")
        self.assertIn("not configured", body["detail"])

    @override_settings(CELERY_BROKER_URL="redis://localhost:6379/0")
    def test_healthz_worker_unhealthy_when_broker_unreachable(self):
        with patch(
            "config.celery.app.control.inspect",
            side_effect=ConnectionError("connection refused"),
        ):
            response = self.client.get("/healthz/worker/")
        self.assertEqual(response.status_code, 503)
        self.assertIn("unreachable", response.json()["detail"])

    @override_settings(CELERY_BROKER_URL="redis://localhost:6379/0")
    def test_healthz_worker_unhealthy_when_no_workers_respond(self):
        mock_inspect = MagicMock()
        mock_inspect.ping.return_value = None
        with patch("config.celery.app.control.inspect", return_value=mock_inspect):
            response = self.client.get("/healthz/worker/")
        self.assertEqual(response.status_code, 503)
        self.assertIn("no workers responded", response.json()["detail"])

    @override_settings(CELERY_BROKER_URL="redis://localhost:6379/0")
    def test_healthz_worker_ok_when_a_worker_responds(self):
        mock_inspect = MagicMock()
        mock_inspect.ping.return_value = {"celery@worker1": {"ok": "pong"}}
        with patch("config.celery.app.control.inspect", return_value=mock_inspect):
            response = self.client.get("/healthz/worker/")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["workers"], ["celery@worker1"])
