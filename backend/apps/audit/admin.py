from django.contrib import admin, messages

from apps.audit.services import verify_chain

from .models import AuditChainState, AuditTrail


@admin.register(AuditTrail)
class AuditTrailAdmin(admin.ModelAdmin):
    list_display = ("sequence", "action", "organization", "record", "changed_by", "timestamp")
    list_filter = ("action", "organization", "timestamp")
    search_fields = ("action", "reason", "entry_hash")
    readonly_fields = (
        "id", "organization", "record", "record_uuid_backup", "action", "changed_by",
        "changes", "reason", "timestamp", "sequence", "prev_hash", "entry_hash",
    )
    ordering = ("organization", "sequence")
    actions = ["verify_selected_organizations_chain"]

    # Disable manual creation and deletion inside Django Admin for strict audit integrity
    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Verify hash chain for the selected entries' organization(s)")
    def verify_selected_organizations_chain(self, request, queryset):
        orgs = {entry.organization for entry in queryset.select_related("organization")}
        for org in orgs:
            result = verify_chain(org)
            level = messages.SUCCESS if result.valid else messages.ERROR
            self.message_user(
                request,
                f"{org.name}: {'VALID' if result.valid else 'BROKEN'} "
                f"({result.entries_checked} entries checked) — {result.detail}",
                level=level,
            )


@admin.register(AuditChainState)
class AuditChainStateAdmin(admin.ModelAdmin):
    """Read-only — this is the chain's mutable 'current tip' bookkeeping, not
    itself a governance record, but still not something to hand-edit; only
    apps.audit.services.append_entry() should ever change it."""
    list_display = ("organization", "last_sequence", "last_hash", "updated_at")
    readonly_fields = ("organization", "last_sequence", "last_hash", "updated_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
