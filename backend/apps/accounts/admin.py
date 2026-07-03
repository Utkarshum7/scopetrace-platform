from django.contrib import admin

from apps.accounts.models import Membership


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "organization", "role", "active", "created_at")
    list_filter = ("role", "active", "organization")
    search_fields = ("user__username", "user__email", "organization__name")
    readonly_fields = ("id", "created_at", "updated_at")
