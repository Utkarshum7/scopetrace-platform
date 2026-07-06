from django.contrib import admin

from apps.tasks.models import FailedTaskLog


@admin.register(FailedTaskLog)
class FailedTaskLogAdmin(admin.ModelAdmin):
    """Read-only dead-letter view — these are audit records of what actually
    happened, not something to hand-edit. Deletion is allowed (an operator
    clearing out resolved/investigated entries), creation/editing is not."""

    list_display = (
        "task_name", "batch_id", "workflow_id", "exception_type",
        "retries_attempted", "created_at",
    )
    list_filter = ("task_name", "exception_type")
    search_fields = ("batch_id", "workflow_id", "task_id", "exception_message")
    readonly_fields = [f.name for f in FailedTaskLog._meta.fields]
    ordering = ("-created_at",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
