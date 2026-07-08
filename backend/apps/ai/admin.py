from django.contrib import admin

from .models import (
    AIAnnotation,
    AIConversation,
    AIConversationMessage,
    AIFactorRecommendation,
    AIInteraction,
    AIPromptVersion,
    TenantAIPolicy,
)


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


@admin.register(AIAnnotation)
class AIAnnotationAdmin(admin.ModelAdmin):
    list_display = ("capability", "record", "confidence", "organization", "created_at")
    list_filter = ("capability", "confidence")
    search_fields = ("record__id", "organization__name")
    readonly_fields = [f.name for f in AIAnnotation._meta.fields]

    def has_add_permission(self, request):
        # Rows are created only via the capability's own service (e.g.
        # apps.ai.services.anomaly_detection) -- immutable once created.
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(AIFactorRecommendation)
class AIFactorRecommendationAdmin(admin.ModelAdmin):
    list_display = ("record", "recommended_factor", "confidence", "organization", "created_at")
    list_filter = ("confidence",)
    search_fields = ("record__id", "organization__name")
    readonly_fields = [f.name for f in AIFactorRecommendation._meta.fields]

    def has_add_permission(self, request):
        # Rows are created only via apps.ai.services.factor_recommendation
        # -- immutable once created.
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(AIConversation)
class AIConversationAdmin(admin.ModelAdmin):
    list_display = ("id", "organization", "user", "created_at")
    search_fields = ("id", "organization__name", "user__username")
    readonly_fields = [f.name for f in AIConversation._meta.fields]

    def has_add_permission(self, request):
        # Rows are created only via the esg_assistant API/service.
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(AIConversationMessage)
class AIConversationMessageAdmin(admin.ModelAdmin):
    list_display = ("conversation", "role", "confidence", "organization", "created_at")
    list_filter = ("role", "confidence")
    search_fields = ("conversation__id", "organization__name")
    readonly_fields = [f.name for f in AIConversationMessage._meta.fields]

    def has_add_permission(self, request):
        # Rows are created only via apps.ai.services.esg_assistant --
        # immutable once created.
        return False

    def has_change_permission(self, request, obj=None):
        return False
