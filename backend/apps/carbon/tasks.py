"""
Async carbon calculation — second link in the ingest -> calculate chain
(Phase 5d).

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

logger = logging.getLogger(__name__)


@shared_task(name="apps.carbon.tasks.calculate_task", bind=True)
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

    Idempotent under Celery's at-least-once delivery (acks_late): a batch
    whose calculation_status is already terminal (CALCULATED/
    CALCULATION_FAILED) is skipped, not reprocessed. Without this guard, a
    redelivered task would re-run bulk_create() and hit EmissionCalculation's
    unique_current_calc_per_record constraint.
    """
    from apps.ingestion.models import UploadBatch

    try:
        batch = UploadBatch.objects.select_related("data_source", "organization").get(pk=batch_id)
    except UploadBatch.DoesNotExist:
        logger.error("calculate_task: workflow %s batch %s does not exist", workflow_id, batch_id)
        return "batch-not-found"

    if batch.calculation_status in UploadBatch.CALCULATION_TERMINAL_STATUSES:
        logger.info(
            "calculate_task: workflow %s batch %s calculation already %s — "
            "skipping (redelivered task)",
            workflow_id, batch_id, batch.calculation_status,
        )
        return f"skipped-{batch.calculation_status}"

    # Celery-context observability. celery_task_id is overwritten here (the
    # view set it to ingest_task's id at enqueue) so it always points at
    # whichever task is currently active — see UploadBatch.celery_task_id.
    batch.worker_id = self.request.hostname
    batch.retry_count = self.request.retries
    batch.celery_task_id = self.request.id
    batch.save(update_fields=["worker_id", "retry_count", "celery_task_id"])

    from apps.carbon.services.carbon_service import CarbonCalculationService

    calculations = CarbonCalculationService().calculate_for_batch(batch)
    logger.info(
        "calculate_task: workflow %s batch %s calculated — %s calculations",
        workflow_id, batch_id, len(calculations),
    )
    return "completed"
