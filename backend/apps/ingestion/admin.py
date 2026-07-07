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
    list_display = ("id", "organization", "batch", "row_index", "status", "normalized_value", "normalized_unit", "scope_category", "is_suspicious")
    list_filter = ("status", "scope_category", "is_suspicious", "organization")
    search_fields = ("batch__file_name", "id")
    readonly_fields = ("id", "created_at", "updated_at")


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
