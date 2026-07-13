from django.contrib import admin
from .models import UploadBatch, EmissionRecord, EmissionRecordVersion

@admin.register(UploadBatch)
class UploadBatchAdmin(admin.ModelAdmin):
    list_display = ("file_name", "organization", "data_source", "status", "total_rows", "created_at")
    list_filter = ("status", "organization", "data_source")
    search_fields = ("file_name",)
    readonly_fields = ("id", "created_at", "updated_at")

@admin.register(EmissionRecord)
class EmissionRecordAdmin(admin.ModelAdmin):
    """Phase 6d: has_delete_permission=False -- EmissionRecord.delete()
    raises unconditionally (hard deletion is never permitted; see that
    model), so leaving the delete action enabled here would just reach a
    confusing exception instead of a clean "no delete" admin UI. Use the
    soft-delete API instead. get_queryset() uses all_objects (not the
    filtered default) so admins retain full oversight of soft-deleted
    records, matching this project's "admin = oversight, not just the
    working view" precedent."""
    list_display = ("id", "organization", "batch", "row_index", "status", "normalized_value", "normalized_unit", "scope_category", "is_suspicious", "is_deleted")
    list_filter = ("status", "scope_category", "is_suspicious", "is_deleted", "organization")
    search_fields = ("batch__file_name", "id")
    readonly_fields = ("id", "created_at", "updated_at")

    def get_queryset(self, request):
        return EmissionRecord.all_objects.all()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(EmissionRecordVersion)
class EmissionRecordVersionAdmin(admin.ModelAdmin):
    """Phase 6b — read-only, mirroring AuditTrailAdmin/FailedTaskLogAdmin's
    established pattern: these are historical audit records, not something
    to hand-edit."""
    list_display = ("record", "version_number", "status", "organization", "created_at", "created_by")
    list_filter = ("status", "organization")
    search_fields = ("record_uuid_backup", "reason")
    readonly_fields = [f.name for f in EmissionRecordVersion._meta.fields]
    ordering = ("-created_at",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
