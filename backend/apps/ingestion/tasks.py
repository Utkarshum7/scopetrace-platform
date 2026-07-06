"""
Async ingestion — first link in the ingest -> calculate chain (Phase 5d;
async ingestion itself dates to Phase 5b).

This task is a thin wrapper — it stages the durably-saved upload to a local
temp file (parsers are path-based; see the note below) and delegates all
business logic to IngestionService.ingest_batch(), the exact same code path
`IngestionService.ingest()` (the still-supported synchronous entry point)
runs. No ingestion logic lives here — keeping this file thin is what makes
IngestionService independently testable without Celery, and keeps the task
swappable without touching business logic.

Calculation is NOT triggered from here — it's a separate, independently-
retryable task (apps.carbon.tasks.calculate_task), chained after this one by
apps.ingestion.views.BaseUploadView.post. See docs/JOB_LIFECYCLE.md.
"""
import logging
import os
import shutil
import tempfile

from celery import shared_task

from apps.core.storage import get_storage_service
from apps.ingestion.models import UploadBatch
from apps.ingestion.services.ingestion_service import IngestionService

logger = logging.getLogger(__name__)


@shared_task(name="apps.ingestion.tasks.ingest_task", bind=True)
def ingest_task(self, batch_id: str, storage_key: str, workflow_id: str) -> str:
    """Run the ingestion pipeline (parse/validate/normalize/persist — NOT
    calculation) for an already-created, already-staged upload batch.

    `workflow_id` is a stable identifier for the whole chain's execution,
    threaded unchanged through every task (see UploadBatch.workflow_id) —
    logged alongside batch_id on every line here for correlation across this
    task and calculate_task, independent of each task's own (different)
    Celery task id.

    Idempotent under Celery's at-least-once delivery (acks_late — see
    config/celery.py): a batch already in a TERMINAL_STATUSES state is
    skipped rather than reprocessed. Without this guard, a task redelivered
    after a crash that happened AFTER the ingestion transaction committed
    (but before the broker received the ack) would re-parse the file and hit
    a unique_together (batch, row_index) IntegrityError on the second
    bulk_create.

    `bind=True` gives access to `self.request` — worker_id and retry_count
    are captured here (Celery-context observability) before delegating to
    IngestionService.ingest_batch(), which owns the actual outcome (COMPLETED
    vs PARTIALLY_COMPLETED vs FAILED) and doesn't know or need to know it's
    running inside Celery at all.

    Retry/backoff/dead-letter handling for genuinely failed tasks is Phase
    5e's concern, not this one — exceptions propagate unmodified here so
    Celery's own failure tracking sees them (IngestionService.ingest_batch
    already marks the batch FAILED with error_message before re-raising, and
    — because it raises — Celery's chain stops here: calculate_task never
    runs for a batch whose ingestion genuinely crashed).
    """
    try:
        batch = UploadBatch.objects.select_related("data_source").get(pk=batch_id)
    except UploadBatch.DoesNotExist:
        logger.error("ingest_task: workflow %s batch %s does not exist", workflow_id, batch_id)
        return "batch-not-found"

    if batch.status in UploadBatch.TERMINAL_STATUSES:
        logger.info(
            "ingest_task: workflow %s batch %s already %s — skipping (redelivered task)",
            workflow_id, batch_id, batch.status,
        )
        return f"skipped-{batch.status}"

    # Celery-context observability, captured before ingest_batch() writes its
    # first PROCESSING save() (so this rides along on that same write rather
    # than needing a separate one).
    batch.worker_id = self.request.hostname
    batch.retry_count = self.request.retries

    storage = get_storage_service()
    suffix = os.path.splitext(storage_key)[1]
    temp_path = None
    try:
        # Parsers (SAPFuelParser etc.) are path-based, not stream-based — they
        # weren't written with streaming in mind, and changing that would
        # ripple into all three parser classes for no benefit here. So this
        # task materializes the durable object to a local temp file and hands
        # the existing, unchanged parser code a path, exactly as before.
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            temp_path = tmp.name
            with storage.open(storage_key) as src:
                shutil.copyfileobj(src, tmp)

        result = IngestionService().ingest_batch(batch, temp_path)
        logger.info(
            "ingest_task: workflow %s batch %s completed — %s rows, %s failed",
            workflow_id, batch_id, result.total_rows, result.failed_rows,
        )
        return "completed"
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)
