import uuid

from django.db import models


class FailedTaskLog(models.Model):
    """
    Dead-letter record for a Celery task that failed PERMANENTLY (Phase 5e)
    — after autoretry_for's retries were exhausted, or immediately for an
    exception type not in the task's retry allowlist.

    Populated entirely by apps.tasks.signals's task_failure handler, never by
    the tasks themselves — ingest_task/calculate_task have no idea this
    model exists, which is deliberate: DLQ logging is a cross-cutting
    observability concern, not part of either task's own business logic.

    Chosen over a dedicated Celery DLQ queue (a design decision locked in
    during the original Phase 5 planning, before any of Phase 5 was built):
    simpler, zero extra broker infrastructure, and queryable/visible in
    Django admin immediately. A requeue action (re-enqueue the original task
    from its logged args/kwargs) is a reasonable future addition once there's
    an actual operational need for it — not built speculatively now.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task_name = models.CharField(
        max_length=255, db_index=True,
        help_text="Registered Celery task name, e.g. apps.ingestion.tasks.ingest_task",
    )
    task_id = models.CharField(max_length=255, help_text="The specific Celery task id that failed")
    batch_id = models.CharField(
        max_length=64, null=True, blank=True, db_index=True,
        help_text="UploadBatch id, extracted from kwargs — both ingest_task and "
                   "calculate_task pass batch_id as a keyword argument specifically so "
                   "this extraction never depends on positional-argument order.",
    )
    workflow_id = models.CharField(
        max_length=64, null=True, blank=True, db_index=True,
        help_text="Stable cross-task correlation id (UploadBatch.workflow_id), extracted "
                   "from kwargs the same way as batch_id.",
    )
    args = models.JSONField(default=list, blank=True)
    kwargs = models.JSONField(default=dict, blank=True)
    exception_type = models.CharField(max_length=255)
    exception_message = models.TextField(blank=True)
    traceback = models.TextField(blank=True)
    retries_attempted = models.IntegerField(
        default=0, help_text="self.request.retries at the moment of final failure",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Failed Task (Dead Letter)"
        verbose_name_plural = "Failed Tasks (Dead Letter)"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["task_name", "created_at"]),
        ]

    def __str__(self):
        return f"{self.task_name} ({self.task_id}) — {self.exception_type}"
