"""
Phase 7f -- apps.ai.tasks.generate_report_narration_task tests. Unlike
the other three AI tasks, this processes exactly one narration request
(no per-batch-item loop), so these tests mock apps.ai.services.
report_narration.generate_report_narration directly to control task-level
behavior (organization resolution, actor resolution, logging) without
needing full AI mocking -- mirroring tests_factor_recommendation_task.py's
approach to the same problem.
"""
from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.ai.tasks import generate_report_narration_task
from apps.core.models import Organization

User = get_user_model()


class GenerateReportNarrationTaskTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Report Narration Task Org")

    def test_generates_narration_and_resolves_organization(self):
        with patch(
            "apps.ai.services.report_narration.generate_report_narration",
            return_value=object(),
        ) as mocked:
            result = generate_report_narration_task(
                organization_id=str(self.org.id), date_from="2026-01-01", date_to="2026-03-31",
            )
        self.assertEqual(result, "generated")
        mocked.assert_called_once()
        called_org = mocked.call_args.args[0]
        self.assertEqual(called_org, self.org)
        self.assertEqual(mocked.call_args.args[1], date(2026, 1, 1))
        self.assertEqual(mocked.call_args.args[2], date(2026, 3, 31))

    def test_passes_scope_through(self):
        with patch(
            "apps.ai.services.report_narration.generate_report_narration",
            return_value=object(),
        ) as mocked:
            generate_report_narration_task(
                organization_id=str(self.org.id), date_from="2026-01-01", date_to="2026-03-31", scope="SCOPE_1",
            )
        self.assertEqual(mocked.call_args.args[3], "SCOPE_1")

    def test_empty_scope_becomes_none(self):
        with patch(
            "apps.ai.services.report_narration.generate_report_narration",
            return_value=object(),
        ) as mocked:
            generate_report_narration_task(
                organization_id=str(self.org.id), date_from="2026-01-01", date_to="2026-03-31", scope="",
            )
        self.assertIsNone(mocked.call_args.args[3])

    def test_resolves_actor_from_id(self):
        user = User.objects.create_user(username="report_task_user", password="pw")
        with patch(
            "apps.ai.services.report_narration.generate_report_narration",
            return_value=object(),
        ) as mocked:
            generate_report_narration_task(
                organization_id=str(self.org.id), date_from="2026-01-01", date_to="2026-03-31",
                actor_id=str(user.id),
            )
        self.assertEqual(mocked.call_args.kwargs["actor"], user)

    def test_no_actor_id_means_no_actor(self):
        with patch(
            "apps.ai.services.report_narration.generate_report_narration",
            return_value=object(),
        ) as mocked:
            generate_report_narration_task(
                organization_id=str(self.org.id), date_from="2026-01-01", date_to="2026-03-31",
            )
        self.assertIsNone(mocked.call_args.kwargs["actor"])

    def test_returns_no_narration_when_generation_returns_none(self):
        with patch(
            "apps.ai.services.report_narration.generate_report_narration",
            return_value=None,
        ):
            result = generate_report_narration_task(
                organization_id=str(self.org.id), date_from="2026-01-01", date_to="2026-03-31",
            )
        self.assertEqual(result, "no_narration")

    def test_unknown_organization_returns_early_without_raising(self):
        result = generate_report_narration_task(
            organization_id="00000000-0000-0000-0000-000000000000",
            date_from="2026-01-01", date_to="2026-03-31",
        )
        self.assertEqual(result, "organization_not_found")

    def test_routed_to_the_ai_queue(self):
        from django.conf import settings

        routes = settings.CELERY_TASK_ROUTES
        self.assertEqual(routes["apps.ai.tasks.generate_report_narration_task"]["queue"], "ai")
