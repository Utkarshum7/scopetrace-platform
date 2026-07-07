from django.contrib import admin

from .models import AIInteraction, AIPromptVersion, TenantAIPolicy


@admin.register(AIPromptVersion)
class AIPromptVersionAdmin(admin.ModelAdmin):
    list_display = ("name", "version", "response_schema_id", "response_schema_version", "created_at")
    list_filter = ("name",)
    search_fields = ("name", "template_hash")
    readonly_fields = (
        "id", "name", "version", "template_hash", "template_text",
        "response_schema_id", "response_schema_version", "created_at",
    )

    def has_add_permission(self, request):
        # Rows are created only via AIPromptVersion.register(); admin is
        # read-only, mirroring AuditTrail's append-only-via-service pattern.
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(TenantAIPolicy)
class TenantAIPolicyAdmin(admin.ModelAdmin):
    list_display = ("organization", "ai_enabled", "provider_override", "egress_tier", "monthly_budget_usd", "updated_at")
    list_filter = ("ai_enabled", "egress_tier")
    search_fields = ("organization__name",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(AIInteraction)
class AIInteractionAdmin(admin.ModelAdmin):
    list_display = ("capability", "organization", "provider", "model_id", "outcome", "cost_usd", "created_at")
    list_filter = ("outcome", "provider", "egress_tier_applied")
    search_fields = ("capability", "organization__name", "idempotency_key")
    readonly_fields = [f.name for f in AIInteraction._meta.fields]

    def has_add_permission(self, request):
        # Rows are created only via apps.ai.services.gateway.invoke_ai().
        return False

    def has_change_permission(self, request, obj=None):
        return False
