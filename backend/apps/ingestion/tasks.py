"""
Async ingestion — first link in the ingest -> calculate chain (Phase 5d;
async ingestion itself dates to Phase 5b; retry/backoff Phase 5e).

This task is a thin wrapper — it stages the durably-saved upload to a local
temp file (parsers are path-based; see the note below) and delegates all
business logic to IngestionService.ingest_batch(), the exact same code path
`IngestionService.ingest()` (the still-supported synchronous entry point)
runs. No ingestion logic lives here — keeping this file thin is what makes
IngestionService independently testable without Celery, and keeps the task
swappable without touching business logic.

Calculation is NOT triggered from here — it's a separate, independently-
retryable task (apps.carbon.tasks.calculate_task), chained after this one by
apps.ingestion.views.BaseUploadView.post. See docs/JOB_LIFECYCLE.md and
docs/RETRY_DLQ.md.
"""
import logging
import os
import shutil
import tempfile

from celery import shared_task
from django.db import InterfaceError, OperationalError

from apps.core.storage import get_storage_service
from apps.ingestion.models import UploadBatch
from apps.ingestion.services.ingestion_service import IngestionService

logger = logging.getLogger(__name__)

# Retry policy for ingest_task (Phase 5e) — see docs/RETRY_DLQ.md for the
# full rationale. Scoped to Django's own transient-connectivity exceptions
# (OperationalError: connection refused/reset/timeout; InterfaceError:
# "connection already closed" and similar) — deliberately NOT a bare
# `Exception` catch-all, which would retry deterministic failures (an
# unregistered parser, a genuinely malformed file) that will fail identically
# every time and gain nothing from a retry, only delaying the user-visible
# FAILED result. boto3/django-storages already retry transient network
# errors internally before raising, so storage-layer exceptions reaching this
# task have already exhausted that lower-level retry budget — not included
# here; if storage-transient failures are observed in practice, wrapping them
# into a StorageService-level transient exception type is a reasonable,
# well-scoped future addition (not built speculatively now).
#
# max_retries=3, backoff 2s/4s/8s (capped 60s), jitter: ingestion is fast
# (sub-second in every observed run), so 3 attempts spanning at most ~14s
# covers a brief DB blip or failover without tying up a worker slot for long
# — acks_late+prefetch=1 means this only holds ONE slot, not the whole pool,
# but there's still no reason to be generous with an operation this cheap to
# repeat. This tuple is used BOTH in the decorator below and passed to
# ingest_batch()'s transient_exceptions param — single source of truth, so
# they can never drift out of sync.
INGEST_RETRYABLE_EXCEPTIONS = (OperationalError, InterfaceError)


@shared_task(
    name="apps.ingestion.tasks.ingest_task",
    bind=True,
    autoretry_for=INGEST_RETRYABLE_EXCEPTIONS,
    retry_backoff=2,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=3,
)
def ingest_task(self, batch_id: str, storage_key: str, workflow_id: str) -> str:
    """Run the ingestion pipeline (parse/validate/normalize/persist — NOT
    calculation) for an already-created, already-staged upload batch.

    `workflow_id` is a stable identifier for the whole chain's execution,
    threaded unchanged through every task (see UploadBatch.workflow_id) —
    logged alongside batch_id on every line here for correlation across this
    task and calculate_task, independent of each task's own (different)
    Celery task id, and independent of self.request.retries incrementing on
    every attempt.

    Idempotent under Celery's at-least-once delivery (acks_late — see
    config/celery.py) AND under retries (Phase 5e) — both redeliver the task
    as a brand-new message executed from the top, so the same guard covers
    both: a batch already in a TERMINAL_STATUSES state is skipped rather than
    reprocessed. Without this guard, a task redelivered after a crash that
    happened AFTER the ingestion transaction committed (but before the
    broker received the ack) would re-parse the file and hit a
    unique_together (batch, row_index) IntegrityError on the second
    bulk_create.

    `bind=True` gives access to `self.request` — worker_id and retry_count
    are captured here (Celery-context observability) before delegating to
    IngestionService.ingest_batch(), which owns the actual outcome (COMPLETED
    vs PARTIALLY_COMPLETED vs FAILED) and doesn't know or need to know it's
    running inside Celery at all. INGEST_RETRYABLE_EXCEPTIONS is passed as
    ingest_batch's transient_exceptions so a retryable exception does NOT get
    marked FAILED prematurely — see that method's docstring and
    docs/RETRY_DLQ.md for why this matters (a genuine bug found and fixed
    while reviewing this exact interaction before writing this code).

    A permanently-failed task (non-retryable exception, or retries
    exhausted) is caught by apps.tasks.signals's task_failure handler, which
    logs it to the dead-letter table (apps.tasks.models.FailedTaskLog) AND
    marks the batch FAILED if a retryable exception left it non-terminal.
    """
    attempt = self.request.retries + 1
    attempt_label = "initial attempt" if self.request.retries == 0 else (
        f"retry attempt {self.request.retries}/{self.max_retries}"
    )

    try:
        batch = UploadBatch.objects.select_related("data_source").get(pk=batch_id)
    except UploadBatch.DoesNotExist:
        logger.error("ingest_task: workflow %s batch %s does not exist", workflow_id, batch_id)
        return "batch-not-found"

    if batch.status in UploadBatch.TERMINAL_STATUSES:
        logger.info(
            "ingest_task: workflow %s batch %s already %s — skipping (redelivered task, %s)",
            workflow_id, batch_id, batch.status, attempt_label,
        )
        return f"skipped-{batch.status}"

    logger.info(
        "ingest_task: workflow %s batch %s starting (%s)",
        workflow_id, batch_id, attempt_label,
    )

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

        result = IngestionService().ingest_batch(
            batch, temp_path, transient_exceptions=INGEST_RETRYABLE_EXCEPTIONS
        )
        logger.info(
            "ingest_task: workflow %s batch %s completed (%s, attempt %s) — %s rows, %s failed",
            workflow_id, batch_id, attempt_label, attempt, result.total_rows, result.failed_rows,
        )
        return "completed"
    except INGEST_RETRYABLE_EXCEPTIONS:
        logger.warning(
            "ingest_task: workflow %s batch %s transient failure on %s — "
            "will retry if attempts remain",
            workflow_id, batch_id, attempt_label,
            exc_info=True,
        )
        raise
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)
