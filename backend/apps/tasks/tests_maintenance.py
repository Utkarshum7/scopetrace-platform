"""
Phase 5f — apps.tasks.tasks.cleanup_old_failed_task_logs_task, the DLQ's
retention-cleanup sweep.
"""
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.tasks.models import FailedTaskLog
from apps.tasks.tasks import cleanup_old_failed_task_logs_task


class CleanupOldFailedTaskLogsTaskTests(TestCase):
    def _make_log(self, task_id, days_ago):
        log = FailedTaskLog.objects.create(
            task_name="apps.ingestion.tasks.ingest_task",
            task_id=task_id,
            exception_type="OperationalError",
            exception_message="db unreachable",
        )
        past = timezone.now() - timezone.timedelta(days=days_ago)
        FailedTaskLog.objects.filter(pk=log.pk).update(created_at=past)
        return log

    @override_settings(FAILED_TASK_LOG_RETENTION_DAYS=90)
    def test_deletes_rows_older_than_retention_window(self):
        self._make_log("old-1", days_ago=100)
        self._make_log("old-2", days_ago=95)
        recent = self._make_log("recent-1", days_ago=10)

        result = cleanup_old_failed_task_logs_task()

        self.assertEqual(result, "deleted=2")
        self.assertEqual(FailedTaskLog.objects.count(), 1)
        self.assertTrue(FailedTaskLog.objects.filter(pk=recent.pk).exists())

    @override_settings(FAILED_TASK_LOG_RETENTION_DAYS=90)
    def test_no_op_when_nothing_is_old_enough(self):
        self._make_log("recent-1", days_ago=1)

        result = cleanup_old_failed_task_logs_task()

        self.assertEqual(result, "deleted=0")
        self.assertEqual(FailedTaskLog.objects.count(), 1)

    @override_settings(FAILED_TASK_LOG_RETENTION_DAYS=90)
    def test_is_idempotent_when_run_twice(self):
        self._make_log("old-1", days_ago=100)

        first = cleanup_old_failed_task_logs_task()
        second = cleanup_old_failed_task_logs_task()

        self.assertEqual(first, "deleted=1")
        self.assertEqual(second, "deleted=0")
