"""
Phase 7.5 (H4-13) -- apps.tasks.management.commands.replay_failed_task.
Celery's own dispatch (app.send_task) is mocked throughout -- these tests
verify the command's OWN logic (selection, dry-run default, deletion
opt-in), not that Celery itself can deliver a message, which is already
covered by every other task-dispatch test in this codebase.
"""
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.tasks.models import FailedTaskLog


def _log(**overrides):
    defaults = dict(
        task_name="apps.ingestion.tasks.ingest_task",
        task_id="celery-task-id-1",
        args=[],
        kwargs={"batch_id": "batch-1", "storage_key": "uploads/x.csv", "workflow_id": "wf-1"},
        exception_type="OperationalError",
        exception_message="db unreachable",
        retries_attempted=3,
    )
    defaults.update(overrides)
    return FailedTaskLog.objects.create(**defaults)


class ReplayFailedTaskCommandTests(TestCase):
    def test_requires_id_or_task_name(self):
        with self.assertRaises(CommandError):
            call_command("replay_failed_task")

    def test_rejects_both_id_and_task_name(self):
        log = _log()
        with self.assertRaises(CommandError):
            call_command("replay_failed_task", id=str(log.id), **{"task_name": log.task_name})

    def test_dry_run_is_the_default_and_dispatches_nothing(self):
        log = _log()
        with patch("config.celery.app.send_task") as mock_send:
            out = StringIO()
            call_command("replay_failed_task", id=str(log.id), stdout=out)
        mock_send.assert_not_called()
        self.assertIn("Would replay 1 task", out.getvalue())
        # Dry run never deletes the log row either.
        self.assertTrue(FailedTaskLog.objects.filter(id=log.id).exists())

    def test_replay_by_id_dispatches_the_original_task_name_args_kwargs(self):
        log = _log(args=[1, 2], kwargs={"batch_id": "batch-1"})
        with patch("config.celery.app.send_task") as mock_send:
            call_command("replay_failed_task", id=str(log.id), replay=True)
        mock_send.assert_called_once_with(
            "apps.ingestion.tasks.ingest_task", args=[1, 2], kwargs={"batch_id": "batch-1"},
        )
        # Default: the log row survives a replay (observability record kept).
        self.assertTrue(FailedTaskLog.objects.filter(id=log.id).exists())

    def test_replay_by_task_name_dispatches_every_matching_row(self):
        _log(task_id="a")
        _log(task_id="b")
        _log(task_id="c", task_name="apps.carbon.tasks.calculate_task")
        with patch("config.celery.app.send_task") as mock_send:
            out = StringIO()
            call_command(
                "replay_failed_task", task_name="apps.ingestion.tasks.ingest_task",
                replay=True, stdout=out,
            )
        self.assertEqual(mock_send.call_count, 2)
        self.assertIn("Replayed 2 task", out.getvalue())

    def test_delete_on_replay_removes_the_log_row_only_on_success(self):
        log = _log()
        with patch("config.celery.app.send_task"):
            call_command("replay_failed_task", id=str(log.id), replay=True, delete_on_replay=True)
        self.assertFalse(FailedTaskLog.objects.filter(id=log.id).exists())

    def test_no_matching_rows_reports_and_dispatches_nothing(self):
        with patch("config.celery.app.send_task") as mock_send:
            out = StringIO()
            call_command("replay_failed_task", id="00000000-0000-0000-0000-000000000000", stdout=out)
        mock_send.assert_not_called()
        self.assertIn("No matching", out.getvalue())
