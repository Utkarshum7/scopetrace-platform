"""
Periodic maintenance for apps.tasks (Phase 5f).

Kept separate from apps.tasks.signals (the DLQ's write path) — this module
only ever reads/deletes what signals.py already wrote.
"""
import logging

from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(name="apps.tasks.tasks.cleanup_old_failed_task_logs_task")
def cleanup_old_failed_task_logs_task() -> str:
    """Purge FailedTaskLog rows older than FAILED_TASK_LOG_RETENTION_DAYS.

    Safe by construction: FailedTaskLog has no foreign keys (batch_id/
    workflow_id are plain CharFields, not relations — see models.py), so
    deleting old rows here can never cascade into or corrupt any other
    table's data. These are audit/observability records of past failures,
    not the failures' actual state (that lives on UploadBatch, already
    fixed up by the signal handler at the time of the original failure) —
    purging old ones is pure housekeeping, never a state change.
    """
    from apps.tasks.models import FailedTaskLog

    cutoff = timezone.now() - timezone.timedelta(days=settings.FAILED_TASK_LOG_RETENTION_DAYS)
    deleted_count, _ = FailedTaskLog.objects.filter(created_at__lt=cutoff).delete()

    if deleted_count:
        logger.info(
            "cleanup_old_failed_task_logs_task: deleted %s FailedTaskLog row(s) "
            "older than %s days",
            deleted_count, settings.FAILED_TASK_LOG_RETENTION_DAYS,
        )
    else:
        logger.info("cleanup_old_failed_task_logs_task: nothing to delete")

    return f"deleted={deleted_count}"
