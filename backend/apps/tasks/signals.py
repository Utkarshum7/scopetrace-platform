"""
Dead-letter queue signal handler (Phase 5e).

Connects to Celery's task_failure signal, which fires ONLY on a task's truly
FINAL failure. Confirmed via Celery's own retry mechanism: self.retry()
(whether called automatically by autoretry_for or manually) raises a Retry
exception internally, which Celery's task machinery treats as distinct
control flow, NOT a failure — task_failure does not fire for an attempt that
is about to be retried. This is what makes it safe to use this same signal
for both dead-letter logging AND the batch-status fixup below: both need to
happen exactly once, only when there is truly nothing left to retry.
"""
import logging

from celery.signals import task_failure
from django.utils import timezone

logger = logging.getLogger(__name__)

_INGEST_TASK_NAME = "apps.ingestion.tasks.ingest_task"
_CALCULATE_TASK_NAME = "apps.carbon.tasks.calculate_task"


def register_dead_letter_handler():
    task_failure.connect(
        _handle_permanently_failed_task, dispatch_uid="apps.tasks.dead_letter_handler"
    )


def _handle_permanently_failed_task(
    sender=None, task_id=None, exception=None, args=None, kwargs=None,
    traceback=None, einfo=None, **extra
):
    """
    task_name-agnostic: extracts batch_id/workflow_id from `kwargs`, never
    from positional `args` — apps.ingestion.views.BaseUploadView.post passes
    both as keyword arguments to ingest_task/calculate_task specifically so
    this handler never needs per-task-name positional-argument knowledge
    (the two tasks have different positional signatures — ingest_task also
    takes storage_key). Any FUTURE task that wants dead-letter + batch-status
    integration just needs to follow the same keyword convention.
    """
    from apps.tasks.models import FailedTaskLog

    kwargs = kwargs or {}
    args = list(args) if args else []
    task_name = sender.name if sender is not None else "unknown"
    batch_id = kwargs.get("batch_id")
    workflow_id = kwargs.get("workflow_id")
    retries = getattr(getattr(sender, "request", None), "retries", 0) or 0
    exception_type = type(exception).__name__ if exception is not None else "Unknown"
    exception_message = str(exception) if exception is not None else ""

    # This handler's own persistence can fail for the same underlying reason
    # the task itself just exhausted its retries over (most notably: the DB
    # was the thing that was down, and is still down). That must not raise
    # out of a signal receiver — Celery would log it as an unhandled error in
    # the signal dispatch and the worker would otherwise have no record at
    # all of the original permanent failure. Falling back to a CRITICAL log
    # line keeps the failure visible (worker stdout / any log aggregator)
    # even when neither the FailedTaskLog row nor the batch-status fixup
    # below can be written.
    try:
        FailedTaskLog.objects.create(
            task_name=task_name,
            task_id=task_id or "",
            batch_id=batch_id,
            workflow_id=workflow_id,
            args=args,
            kwargs=kwargs,
            exception_type=exception_type,
            exception_message=exception_message,
            traceback=str(einfo) if einfo else "",
            retries_attempted=retries,
        )
    except Exception:
        logger.critical(
            "DEAD LETTER LOGGING FAILED: could not persist FailedTaskLog for task %s "
            "(id=%s, workflow=%s, batch=%s) — the DB may be unavailable, which may "
            "also be why the original task's retries were exhausted. Original "
            "failure: %s retries, %s: %s",
            task_name, task_id, workflow_id, batch_id, retries, exception_type, exception_message,
            exc_info=True,
        )

    logger.error(
        "DEAD LETTER: task %s (id=%s, workflow=%s, batch=%s) permanently failed "
        "after %s retries: %s: %s",
        task_name, task_id, workflow_id, batch_id, retries, exception_type, exception_message,
    )

    if not batch_id:
        return

    # Ensure the batch reflects the permanent failure even when the
    # underlying exception was one of the retryable/transient types the
    # service layer deliberately did NOT mark terminal for (see
    # IngestionService.ingest_batch / CarbonCalculationService.
    # calculate_for_batch's transient_exceptions parameter). The
    # .exclude(...__in=TERMINAL) makes this an atomic, race-free, idempotent
    # no-op if the batch is already terminal (e.g. a non-retryable exception
    # already marked it, or — impossible in practice, since task_failure only
    # fires on final failure — a concurrent success already completed it) —
    # it only takes effect when retries were truly exhausted and nothing else
    # has marked the batch terminal yet.
    from apps.ingestion.models import UploadBatch

    # Same fallback reasoning as the FailedTaskLog write above: this update
    # can fail for the same reason the task's retries were just exhausted
    # (the DB itself unavailable). A failure here must not raise out of the
    # signal receiver, and must not be silent — a batch stuck non-terminal
    # with no error message and no log trace would be strictly worse than
    # one that's merely missing its DLQ audit row.
    try:
        if task_name == _INGEST_TASK_NAME:
            updated = (
                UploadBatch.objects.filter(pk=batch_id)
                .exclude(status__in=UploadBatch.TERMINAL_STATUSES)
                .update(
                    status=UploadBatch.BatchStatus.FAILED,
                    error_message=(
                        f"Ingestion failed permanently after {retries} retries: "
                        f"{exception_type}: {exception_message}"
                    ),
                    finished_at=timezone.now(),
                )
            )
            if updated:
                logger.info(
                    "DEAD LETTER: batch %s marked FAILED (was left non-terminal by a "
                    "retryable exception whose retries were exhausted)",
                    batch_id,
                )
        elif task_name == _CALCULATE_TASK_NAME:
            updated = (
                UploadBatch.objects.filter(pk=batch_id)
                .exclude(calculation_status__in=UploadBatch.CALCULATION_TERMINAL_STATUSES)
                .update(
                    calculation_status=UploadBatch.CalculationStatus.CALCULATION_FAILED,
                    error_message=(
                        f"Calculation failed permanently after {retries} retries: "
                        f"{exception_type}: {exception_message}"
                    ),
                    finished_at=timezone.now(),
                )
            )
            if updated:
                logger.info(
                    "DEAD LETTER: batch %s marked CALCULATION_FAILED (was left non-terminal "
                    "by a retryable exception whose retries were exhausted)",
                    batch_id,
                )
    except Exception:
        logger.critical(
            "DEAD LETTER BATCH FIXUP FAILED: could not mark batch %s terminal for task %s "
            "(workflow=%s) — the DB may still be unavailable. This batch will remain "
            "non-terminal until manually investigated and fixed up.",
            batch_id, task_name, workflow_id,
            exc_info=True,
        )
