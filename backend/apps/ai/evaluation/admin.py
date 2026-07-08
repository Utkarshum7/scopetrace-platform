from django.contrib import admin

from .models import EvaluationResult, EvaluationRun


class EvaluationResultInline(admin.TabularInline):
    model = EvaluationResult
    extra = 0
    readonly_fields = [f.name for f in EvaluationResult._meta.fields]
    can_delete = False


@admin.register(EvaluationRun)
class EvaluationRunAdmin(admin.ModelAdmin):
    list_display = ("id", "tier", "trigger", "status", "passed_cases", "total_cases", "started_at")
    list_filter = ("tier", "status")
    readonly_fields = [f.name for f in EvaluationRun._meta.fields]
    inlines = [EvaluationResultInline]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(EvaluationResult)
class EvaluationResultAdmin(admin.ModelAdmin):
    list_display = ("capability", "case_id", "outcome", "score", "run", "created_at")
    list_filter = ("outcome", "capability")
    search_fields = ("case_id", "capability")
    readonly_fields = [f.name for f in EvaluationResult._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
