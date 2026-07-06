"""
Async carbon calculation — second link in the ingest -> calculate chain
(Phase 5d; retry/backoff Phase 5e).

Thin wrapper, same philosophy as apps.ingestion.tasks.ingest_task: no
business logic here, delegates to CarbonCalculationService.
calculate_for_batch() — the exact same method the synchronous
IngestionService.ingest() convenience path calls. Keeping this thin is what
makes the calculation logic testable without Celery, and swappable (e.g. a
future calculate_task_v2 routed to an alternate engine — see
docs/JOB_LIFECYCLE.md's future-compatibility notes) without touching
business logic.
"""
import logging

from celery import shared_task
from django.db import InterfaceError, OperationalError

logger = logging.getLogger(__name__)

# Retry policy for calculate_task (Phase 5e) — designed INDEPENDENTLY of
# ingest_task's, not shared, even though the exception tuple happens to
# coincide today (see docs/RETRY_DLQ.md for the full rationale). Same
# rationale for scoping to Django's transient-connectivity exceptions only
# (not a bare Exception catch-all): calculate_one() already degrades a bad
# individual record to UNRESOLVED rather than raising, so anything that
# reaches this task's try/except is either a genuine DB connectivity issue
# (worth retrying) or a deterministic bug (not worth retrying).
#
# max_retries=5 (more than ingest_task's 3), backoff 2s/4s/8s/16s/32s (capped
# 120s): calculate_task has a narrower failure surface than ingest_task (DB
# only — no storage/file I/O), so a false-positive retry loop is less of a
# concern; and giving up on calculation after a successful ingestion is more
# wasteful to abandon early than giving up on ingestion itself — the
# expensive parse/persist work is already done, retrying the remaining
# (cheap, single bulk_create) calculation step costs little, so a bit more
# patience before finally marking CALCULATION_FAILED is worth it.
CALCULATE_RETRYABLE_EXCEPTIONS = (OperationalError, InterfaceError)


@shared_task(
    name="apps.carbon.tasks.calculate_task",
    bind=True,
    autoretry_for=CALCULATE_RETRYABLE_EXCEPTIONS,
    retry_backoff=2,
    retry_backoff_max=120,
    retry_jitter=True,
    max_retries=5,
)
def calculate_task(self, batch_id: str, workflow_id: str) -> str:
    """Compute + persist CO2e for an already-ingested batch.

    See apps.ingestion.tasks.ingest_task (the chain's first link) and
    apps.ingestion.views.BaseUploadView.post, which constructs
    chain(ingest_task.si(...), calculate_task.si(...)).

    `workflow_id` is the same stable identifier threaded through from
    ingest_task — logged alongside batch_id here for correlation across both
    tasks despite each having its own (different) Celery task id.

    Never runs at all if ingest_task raised (a genuine pipeline crash) —
    Celery chains stop at the first raised exception, so there's nothing to
    guard against for that case here; this only ever runs when ingestion
    reached COMPLETED or PARTIALLY_COMPLETED.

    Idempotent under Celery's at-least-once delivery (acks_late) AND under
    retries (Phase 5e) — both redeliver the task as a brand-new message
    executed from the top: a batch whose calculation_status is already
    terminal (CALCULATED/CALCULATION_FAILED) is skipped, not reprocessed.
    Without this guard, a redelivered task would re-run bulk_create() and hit
    EmissionCalculation's unique_current_calc_per_record constraint.
    CALCULATE_RETRYABLE_EXCEPTIONS is passed as calculate_for_batch's
    transient_exceptions so a retryable exception does NOT get marked
    CALCULATION_FAILED prematurely — see that method's docstring and
    docs/RETRY_DLQ.md.

    A permanently-failed task (non-retryable exception, or retries
    exhausted) is caught by apps.tasks.signals's task_failure handler, which
    logs it to the dead-letter table (apps.tasks.models.FailedTaskLog) AND
    marks the batch CALCULATION_FAILED if a retryable exception left it
    non-terminal.
    """
    from apps.ingestion.models import UploadBatch

    attempt = self.request.retries + 1
    attempt_label = "initial attempt" if self.request.retries == 0 else (
        f"retry attempt {self.request.retries}/{self.max_retries}"
    )

    try:
        batch = UploadBatch.objects.select_related("data_source", "organization").get(pk=batch_id)
    except UploadBatch.DoesNotExist:
        logger.error("calculate_task: workflow %s batch %s does not exist", workflow_id, batch_id)
        return "batch-not-found"

    if batch.calculation_status in UploadBatch.CALCULATION_TERMINAL_STATUSES:
        logger.info(
            "calculate_task: workflow %s batch %s calculation already %s — "
            "skipping (redelivered task, %s)",
            workflow_id, batch_id, batch.calculation_status, attempt_label,
        )
        return f"skipped-{batch.calculation_status}"

    logger.info(
        "calculate_task: workflow %s batch %s starting (%s)",
        workflow_id, batch_id, attempt_label,
    )

    # Celery-context observability. celery_task_id is overwritten here (the
    # view set it to ingest_task's id at enqueue) so it always points at
    # whichever task is currently active — see UploadBatch.celery_task_id.
    batch.worker_id = self.request.hostname
    batch.retry_count = self.request.retries
    batch.celery_task_id = self.request.id
    batch.save(update_fields=["worker_id", "retry_count", "celery_task_id"])

    from apps.carbon.services.carbon_service import CarbonCalculationService

    from apps.core.tasks import send_notification_task

    try:
        calculations = CarbonCalculationService().calculate_for_batch(
            batch, transient_exceptions=CALCULATE_RETRYABLE_EXCEPTIONS
        )
        logger.info(
            "calculate_task: workflow %s batch %s calculated (%s, attempt %s) — %s calculations",
            workflow_id, batch_id, attempt_label, attempt, len(calculations),
        )
        # Phase 5g: this IS the whole chain's final resting state on
        # success — ingest_task already succeeded (or this wouldn't be
        # running at all) and calculation just finished too. Dispatched as
        # a separate, independently-retryable task so mail delivery can
        # never delay returning "completed" here or affect batch state.
        send_notification_task.delay(batch_id=batch_id)
        return "completed"
    except CALCULATE_RETRYABLE_EXCEPTIONS:
        logger.warning(
            "calculate_task: workflow %s batch %s transient failure on %s — "
            "will retry if attempts remain",
            workflow_id, batch_id, attempt_label,
            exc_info=True,
        )
        raise
    except Exception:
        # Non-retryable — CarbonCalculationService.calculate_for_batch()
        # already marked the batch CALCULATION_FAILED and re-raised. Also a
        # final resting state (ingestion already succeeded, so this is the
        # end of the line for this batch either way) — notify here too.
        send_notification_task.delay(batch_id=batch_id)
        raise


@shared_task(name="apps.carbon.tasks.recalculate_missing_calculations_task")
def recalculate_missing_calculations_task() -> str:
    """Phase 5f — daily safety net: compute CO2e for any EmissionRecord that
    still has no current EmissionCalculation.

    In steady state this should find nothing — every record gets calculated
    synchronously (calculate_task, chained right after ingestion) at upload
    time. It exists for the records that can legitimately fall through that
    path: a batch whose calculation permanently failed (calculate_task's
    retries exhausted, logged to the dead-letter table — see
    docs/RETRY_DLQ.md) and was never manually recalculated, or a record
    created/edited outside the normal upload flow.

    Deliberately reuses `backfill_calculations`'s existing, already-tested
    non-force mode (`manage.py backfill_calculations`) rather than
    duplicating its query/resolution logic — that command already does
    exactly this: "compute a calculation for every record that has no
    current calculation", per-organization with preloaded resources and
    chunked bulk writes. --force is never passed here: this task only fills
    in what's missing, it never supersedes an existing calculation (that
    remains an explicit, human-triggered action — recalculate/backfill
    --force), and APPROVED records without a calculation still get one
    (backfill_calculations' default mode doesn't exclude APPROVED, only
    --force does, since --force is about *recomputing existing* calcs,
    which would violate the audit lock).
    """
    import io

    from django.core.management import call_command

    output = io.StringIO()
    call_command("backfill_calculations", stdout=output)
    result = output.getvalue().strip()
    logger.info("recalculate_missing_calculations_task: %s", result)
    return result
